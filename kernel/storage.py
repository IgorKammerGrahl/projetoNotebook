"""Single-writer notebooks and checked replacement on local Linux filesystems."""
import ctypes
import errno
import fcntl
import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .fmt import Document, serialize_document


class FileConflict(OSError):
    def __init__(self, message, preserved=None):
        super().__init__(message)
        self.preserved = preserved


@dataclass(frozen=True)
class Version:
    device: int
    inode: int
    modified: int
    mode: int
    digest: bytes


def read_version(path):
    """Read one regular inode without following a newly substituted symlink."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise FileConflict("O arquivo foi substituído por um link simbólico.") from exc
        raise
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise FileConflict("Use um arquivo regular sem hard links para o notebook.")
        data = stream.read()
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise FileConflict("O arquivo mudou durante a leitura. Aguarde o editor externo terminar.")
        return data, Version(after.st_dev, after.st_ino, after.st_mtime_ns,
                             stat.S_IMODE(after.st_mode), hashlib.sha256(data).digest())


def _rename(source, destination, flags):
    # No check-then-os.replace fallback: it would discard a racing external save.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise OSError(errno.ENOSYS, "renameat2 is required for protected notebook saves")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), flags):
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class NotebookFile:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")
        self.fd = None
        self.expected = None

    def open(self):
        self.fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            info = os.fstat(self.fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("Arquivo de bloqueio inválido.")
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise OSError(f"Este notebook já está aberto em outro servidor: {self.path}") from exc
            self.check_lock()
            data, self.expected = read_version(self.path)
            return data
        except BaseException:
            self.close()
            raise

    def check_lock(self):
        if self.fd is None:
            raise FileConflict("O bloqueio de escrita não está ativo.")
        held = os.fstat(self.fd)
        try:
            named = self.lock_path.lstat()
        except FileNotFoundError as exc:
            raise FileConflict("O arquivo de bloqueio foi removido. Reinicie o servidor.") from exc
        if (held.st_dev, held.st_ino, held.st_nlink) != (named.st_dev, named.st_ino, 1):
            raise FileConflict("O arquivo de bloqueio foi substituído. Reinicie o servidor.")

    def check(self, expected):
        self.check_lock()
        data, version = read_version(self.path)
        if version != expected:
            raise FileConflict("O arquivo mudou fora deste servidor. O salvamento automático foi suspenso.")
        return data

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        # Never unlink: another process may already hold this same lock inode.

    def preserve(self, document):
        """Exclusive, durable copy; client never chooses a filesystem path."""
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                         prefix=f"{self.path.stem}.session-", suffix=".nb.md", delete=False) as stream:
            copy = Path(stream.name)
            stream.write(serialize_document(document))
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(copy.parent)
        _, version = read_version(copy)
        return copy, version


def write_notebook(file: NotebookFile, document: Document):
    """Check before replacement, and keep the exact displaced inode on a race."""
    file.check(file.expected)
    candidate = None
    displaced = False
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=file.path.parent,
                                         prefix=f".{file.path.name}.", suffix=".previous.nb.md", delete=False) as stream:
            candidate = Path(stream.name)
            if file.expected:
                os.fchmod(stream.fileno(), file.expected.mode)
            stream.write(serialize_document(document))
            stream.flush()
            os.fsync(stream.fileno())
        _, written = read_version(candidate)
        file.check(file.expected)
        try:
            _rename(candidate, file.path, 2 if file.expected else 1)  # EXCHANGE / NOREPLACE
        except (FileExistsError, FileNotFoundError) as exc:
            raise FileConflict("O arquivo mudou durante o salvamento; nenhuma versão foi descartada.") from exc
        displaced = file.expected is not None
        # Rename can succeed even if the directory fsync below fails. Remember
        # our installed inode so a retry won't mistake it for an external edit.
        previous = file.expected
        file.expected = written
        def verify():
            try:
                if displaced:
                    _, actual = read_version(candidate)
                    if actual != previous:
                        raise FileConflict("Outra gravação coincidiu com o salvamento. A versão deslocada foi preservada.")
                file.check(written)
            except (OSError, ValueError) as exc:
                if not displaced:
                    raise
                detail = f"{exc} Confira também a cópia preservada: {candidate}"
                try:
                    sync_directory(file.path.parent)
                except OSError:
                    detail += " A durabilidade dessa cópia não pôde ser confirmada."
                raise FileConflict(detail, str(candidate)) from exc

        verify()
        try:
            sync_directory(file.path.parent)
        except OSError:
            verify()  # an I/O error must not hide an external write during fsync
            raise
        verify()
        # An uncooperative process can still write an old open descriptor after
        # this final check. This is detection, not universal writer coordination.
        displaced = False  # only a fully verified replacement may discard its preimage
    finally:
        if candidate is not None and not displaced:
            candidate.unlink(missing_ok=True)
