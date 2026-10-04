"""HTTP + WebSocket server (D-015, D-016).

Security (D-016): bound to 127.0.0.1 only; every HTTP request (including the
WebSocket handshake) must carry an allowed Host header (DNS rebinding); the
WebSocket additionally requires an allowed Origin (cross-site WebSocket
hijacking) and the random token printed at startup.
"""
import asyncio
import errno
import hashlib
import json
import logging
import math
import secrets
from pathlib import Path

from aiohttp import WSMsgType, web

from .fmt import Document, parse_document
from .session import Session
from .storage import FileConflict, NotebookFile, read_version, write_notebook as _write_notebook

SAVE_DEBOUNCE = 1.0  # seconds after the last change (D-015)
log = logging.getLogger("notebook.web")


def _clean(obj):
    """JSON the browser can parse: NaN/Infinity become strings."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def cell_json(cid, c, sched) -> dict:
    return _clean({"id": cid, "kind": c.kind, "code": c.code, "status": c.status, "error": c.error,
                   "output": c.output, "previews": c.previews, "images": c.images,
                   "duration_ms": c.duration_ms, "defs": sorted(c.defs), "refs": sorted(c.refs),
                   # review item 6: values kept, but computed from outdated upstream code
                   "upstream_modified": sched.upstream_modified(cid),
                   # review item 7: speculative build in flight / its errors as editor diagnostics
                   "compiling": c.compiling is not None, "diagnostics": c.diagnostics,
                   "queue_position": sched.queue_positions.get(cid)})


class NotebookServer:
    def __init__(self, path: Path, port: int = 0, token: str | None = None,
                 static_dir: Path | None = None, extra_origins: tuple[str, ...] = (),
                 core_dumps: bool = False, speculate_debounce: float = 0.3, cache_limit: int | None = None):
        self.file = NotebookFile(path)
        self.path = self.file.path
        self.token = token or secrets.token_urlsafe(32)
        self.static_dir = static_dir
        self.extra_origins = set(extra_origins)
        self.port = port
        self._session_options = dict(core_dumps=core_dumps, speculate_debounce=speculate_debounce)
        if cache_limit is not None:
            self._session_options["cache_limit"] = cache_limit
        self.session = Session(self.path.parent / ".nbcache", **self._session_options)
        self.clients: dict[web.WebSocketResponse, asyncio.Queue] = {}  # one ordered outbox per client
        self._epoch = secrets.token_hex(16)
        self._document_id = hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()
        self._cell_ids: dict[int, str] = {}
        self._edit_sources: dict[int, tuple[int, str | None]] = {}
        self._save_handle = None
        self._save_task = None
        self._revision = 0
        self._saved_revision = 0
        self._save_error = None
        self._file_conflict = None
        self._copy = None
        self._operation_task = None
        self._reload_id = None
        self._reload_waiters = {}
        self._reload_committing = False
        self._reload_cancelled = False
        self._source_versions = ()
        self._loading = True
        self._closing = False
        self._runner = None

    # ---------------- security (D-016) ----------------

    def allowed_hosts(self):
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def allowed_origins(self):
        return {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"} | self.extra_origins

    @web.middleware
    async def _check_host(self, request, handler):
        if request.headers.get("Host") not in self.allowed_hosts():
            raise web.HTTPForbidden(text="forbidden: unexpected Host header")
        return await handler(request)

    # ---------------- lifecycle ----------------

    async def start(self):
        try:
            data = self.file.open()  # lock before reading or starting any kernel
            document = parse_document(data.decode("utf-8") if data is not None else "")
            await self._start(document, data is not None)
        except BaseException:
            try:
                await self.session.close()
                if self._runner:
                    await self._runner.cleanup()
            finally:
                self.file.close()
            raise

    async def _start(self, document, exists):
        app = web.Application(middlewares=[self._check_host])
        app.router.add_get("/ws", self._ws)
        app.router.add_get("/", self._index)
        if self.static_dir and (self.static_dir / "assets").is_dir():
            app.router.add_static("/assets", self.static_dir / "assets")
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", self.port)  # D-016: loopback only
        await site.start()
        self.bound = [sock.getsockname() for sock in site._server.sockets]
        self.port = self.bound[0][1]
        await self.session.start()
        self.session.listeners.append(self._on_change)
        self.session.load(document.cells)
        self._cell_ids = dict(zip(self.session.sched.cells, document.cell_ids))
        self._source_versions = self._document_version()
        self._loading = False
        if not exists:
            self._saved_revision = -1
            self._schedule_save()

    async def close(self):
        self._closing = True  # no new mutation may race the final snapshot
        if self._save_handle:
            self._save_handle.cancel()
            self._save_handle = None
        try:
            self._cancel_reload()
            if self._operation_task:
                await asyncio.shield(self._operation_task)
            if self._save_task:
                await asyncio.shield(self._save_task)
            if self._saved_revision != self._revision and not self._file_conflict:
                self._start_save()  # also retries a previous failed autosave once
                await asyncio.shield(self._save_task)
            if self._saved_revision != self._revision:
                raise OSError(f"Não foi possível salvar {self.path}: {self._save_error} "
                              "As alterações pendentes não foram gravadas.")
        finally:
            try:
                for ws in list(self.clients):
                    await ws.close()
            finally:
                try:
                    await self.session.close()
                finally:
                    try:
                        if self._runner:
                            await self._runner.cleanup()
                    finally:
                        self.file.close()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/?token={self.token}"

    # ---------------- HTTP ----------------

    async def _index(self, request):
        index = self.static_dir / "index.html" if self.static_dir else None
        if index and index.exists():
            return web.FileResponse(index)
        return web.Response(text="frontend not built; the WebSocket API is at /ws?token=...",
                            content_type="text/plain")

    # ---------------- WebSocket ----------------

    async def _ws(self, request):
        if self._closing or self._reload_committing:
            raise web.HTTPServiceUnavailable(text="notebook is closing")
        if request.headers.get("Origin") not in self.allowed_origins():
            raise web.HTTPForbidden(text="forbidden: unexpected Origin")
        if not secrets.compare_digest(request.query.get("token", ""), self.token):
            raise web.HTTPForbidden(text="forbidden: bad token")
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        # Every message to this client goes through one queue and one writer task, so
        # updates arrive in the order they were produced. Separate send tasks could be
        # reordered (large frames are compressed in an executor, small ones are not).
        outbox: asyncio.Queue = asyncio.Queue()
        self.clients[ws] = outbox
        self._cancel_reload()  # a newly connected tab must not miss the readiness barrier
        writer = asyncio.create_task(self._writer(ws, outbox))
        peer = f"{request.remote}/{id(ws):x}"
        log.info("client connected: %s", peer)
        try:
            outbox.put_nowait(self._snapshot())
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    seq = None
                    try:
                        m = json.loads(msg.data)
                        if not isinstance(m, dict):
                            raise ValueError("expected a message object")
                        seq = m.get("seq")
                        if seq is not None and (type(seq) is not int or seq < 1):
                            seq = None
                            raise ValueError("invalid sequence number")
                        log.debug("recv %s: type=%s cid=%s seq=%s", peer, m.get("type"), m.get("cid"), seq)
                        # Dispatch only. Session owns execution/build tasks; this reader
                        # must stay available for stop and subsequent edits.
                        self._handle(ws, m)
                    except (KeyError, TypeError, ValueError) as e:
                        outbox.put_nowait({"type": "error", "error": f"bad message: {e}", "seq": seq})
                    except Exception:
                        log.exception("dispatch failed: %s seq=%s", peer, seq)
                        outbox.put_nowait({"type": "error", "error": "Falha ao processar a mensagem.", "seq": seq})
                    if seq is not None:
                        outbox.put_nowait({"type": "ack", "seq": seq})
        finally:
            self._cancel_reload()
            self.clients.pop(ws, None)
            writer.cancel()
            log.info("client disconnected: %s", peer)
        return ws

    @staticmethod
    async def _writer(ws, outbox: asyncio.Queue):
        while True:
            msg = await outbox.get()
            try:
                await ws.send_json(msg)
            except (ConnectionError, RuntimeError):
                await ws.close()  # a dead writer must not leave a reader with an orphaned outbox
                return

    def _snapshot(self):
        s = self.session.sched
        return {"type": "snapshot", "cells": [self._cell_json(cid) for cid in s.cells],
                "edges": self._edges(), "kernel": self._kernel_state(), "save": self._save_state(),
                "document": {"id": self._document_id, "name": self.path.name, "session": self._epoch}}

    def _cell_json(self, cid):
        s = self.session.sched
        c = s.cells[cid]
        revision, request = self._edit_sources.get(cid, (-1, None))
        return {**cell_json(cid, c, s), "uid": self._cell_ids.setdefault(cid, secrets.token_hex(16)),
                "version": f"{self._epoch}:{cid}:{c.revision}",
                "edit_id": request if revision == c.revision else None}

    def _edges(self):
        parents, _, _ = self.session.sched._graph()
        return sorted([p, c] for c, ps in parents.items() for p in ps)

    def _kernel_state(self):
        event = self.session.sched.kernel_event
        return {"restarts": self.session.restarts, "dead": self.session.sched.kernel_dead,
                "restarting": self.session.restarting,
                "event": {**event, "id": f"{self._epoch}:{event['id']}"} if event else None}

    def _handle(self, ws, m):
        if self._closing:
            raise ValueError("O servidor está encerrando; a ação não foi aceita.")
        s = self.session
        kind = m.get("type")
        if kind == "reload_ready":
            future = self._reload_waiters.get(ws)
            if future and not future.done() and m.get("request") == self._reload_id:
                future.set_result(m.get("ready") is True)
            return
        if kind != "edit" and m.get("session", self._epoch) != self._epoch:
            raise ValueError("O notebook foi recarregado; a ação antiga foi descartada.")
        if self._reload_committing:
            raise ValueError("Aguarde o carregamento da versão externa.")
        if kind in {"edit", "add", "delete", "run", "run_all", "stop", "restart"}:
            self._cancel_reload()
        try:
            if kind == "edit":
                cid = m["cid"]
                current = self._cell_json(cid) if cid in s.sched.cells else None
                if not isinstance(m["code"], str):
                    raise ValueError("code must be text")
                request = m.get("request")
                if request is not None and (not isinstance(request, str) or len(request) > 100):
                    raise ValueError("invalid edit request")
                # A retried edit with a lost ACK is safe only if this exact edit is
                # still current. Otherwise compare the original base, never rebase.
                if (current and request and current["edit_id"] == request and current["code"] == m["code"]):
                    self.clients[ws].put_nowait({"type": "update", "cells": [current],
                        "order": list(s.sched.cells), "edges": self._edges(), "kernel": self._kernel_state(),
                        "save": self._save_state()})
                    return
                if current is None or m.get("base_version") != current["version"]:
                    self.clients[ws].put_nowait({"type": "conflict", "cid": cid, "cell": current,
                                                "request": request, "seq": m.get("seq")})
                    return
                self._edit_sources[cid] = (s.sched.cells[cid].revision + 1, request)
                s.edit(cid, m["code"])
            elif kind == "run":
                s.run(m["cid"])
            elif kind == "run_all":
                s.run_all()
            elif kind == "add":
                cid = s.add(m.get("code", ""), m.get("kind", "python"), m.get("after"))
                self.clients[ws].put_nowait({"type": "added", "cid": cid, "request": m.get("request")})
            elif kind == "delete":
                s.delete(m["cid"])
                self._edit_sources.pop(m["cid"], None)
                self._cell_ids.pop(m["cid"], None)
            elif kind == "stop":
                s.stop()
            elif kind == "restart":
                s.restart()
            elif kind == "retry_save":
                self._start_save()
            elif kind in {"preserve_copy", "reload_external"}:
                if not self._file_conflict or self._operation_task or self._save_task:
                    raise ValueError("A resolução do arquivo não está disponível agora.")
                if m.get("revision") != self._revision or m.get("session") != self._epoch:
                    raise ValueError("A sessão mudou. Confira as alterações antes de continuar.")
                if kind == "reload_external" and (not self._copy or self._copy[0] != self._revision):
                    raise ValueError("Preserve uma cópia atual da sessão antes de carregar o arquivo.")
                self._operation_task = asyncio.create_task(self._resolve_file(kind, self._revision))
            else:
                self.clients[ws].put_nowait({"type": "error", "error": f"unknown message type {kind!r}"})
                return
        except KeyError as e:
            self.clients[ws].put_nowait({"type": "error", "error": f"bad message: missing or unknown {e}"})
            return
        if kind == "delete":
            self._on_change({})  # nothing else may change: still tell clients the new `order`

    def _on_change(self, changed):
        version = self._document_version()
        if not self._loading and version != self._source_versions:
            self._source_versions = version
            self._schedule_save()  # publish dirty before publishing the new source
        msg = {"type": "update", "cells": [self._cell_json(cid) for cid in changed],
               "order": list(self.session.sched.cells), "edges": self._edges(),
               "kernel": self._kernel_state(), "save": self._save_state()}
        for outbox in self.clients.values():
            outbox.put_nowait(msg)

    # ---------------- file ----------------

    def _document_version(self):
        return tuple((cid, c.revision) for cid, c in self.session.sched.cells.items())

    def _save_state(self):
        state = {"status": "conflict" if self._file_conflict else "error" if self._save_error else
                          "saved" if self._saved_revision == self._revision else "saving",
                "revision": self._revision, "saved_revision": self._saved_revision,
                "error": self._save_error}
        if self._file_conflict:
            state["conflict"] = self._file_conflict
            state["copy"] = {"revision": self._copy[0], "path": str(self._copy[1])} if self._copy else None
            state["reload"] = self._reload_id
        return state

    def _document(self):
        return Document(self.session.to_file_cells(),
                        [self._cell_json(cid)["uid"] for cid in self.session.sched.cells])

    def _publish_save(self):
        msg = {"type": "save_status", "save": self._save_state()}
        for outbox in self.clients.values():
            outbox.put_nowait(msg)

    def _schedule_save(self):
        self._revision += 1
        if self._save_handle:
            self._save_handle.cancel()
        if not self._file_conflict:
            self._save_handle = asyncio.get_running_loop().call_later(SAVE_DEBOUNCE, self._start_save)
        self._publish_save()

    def _start_save(self):
        if self._save_handle:
            self._save_handle.cancel()
        self._save_handle = None
        if self._save_task or self._file_conflict or self._saved_revision == self._revision:
            return
        self._save_error = None
        self._save_task = asyncio.create_task(self._save())
        self._publish_save()

    async def _save(self):
        try:
            while self._saved_revision != self._revision:
                revision = self._revision
                document = self._document()  # copy before crossing threads
                try:
                    await asyncio.to_thread(_write_notebook, self.file, document)
                except Exception as exc:
                    reason = {errno.EACCES: "Sem permissão para gravar o arquivo.",
                              errno.EPERM: "Sem permissão para gravar o arquivo.",
                              errno.ENOSPC: "Sem espaço em disco."}.get(getattr(exc, "errno", None))
                    self._save_error = reason or "Não foi possível concluir a gravação do arquivo."
                    if isinstance(exc, FileConflict):
                        self._file_conflict = {"message": str(exc), "preserved": exc.preserved}
                        self._save_error = str(exc)
                    log.exception("notebook save failed: %s (revision %s)", self.path, revision)
                    # Keep all accepted edits in Session; retry is explicit or triggered by a new edit.
                    if self._save_handle:
                        self._save_handle.cancel()
                        self._save_handle = None
                    self._publish_save()
                    return
                self._saved_revision = revision
                self._publish_save()  # still 'saving' if an edit arrived during disk I/O
                # Only this task writes. Coalesce edits into the next snapshot, never overlap saves.
        finally:
            self._save_task = None

    def _cancel_reload(self):
        if self._reload_id:
            self._reload_cancelled = True
        for future in self._reload_waiters.values():
            if not future.done():
                future.set_result(False)

    async def _resolve_file(self, kind, revision):
        replacement = None
        try:
            if kind == "preserve_copy":
                revision = self._revision  # snapshot and revision captured together on the loop
                path, version = await asyncio.to_thread(self.file.preserve, self._document())
                self._copy = (revision, path, version)
                return
            if revision != self._revision or not self._copy or self._copy[0] != revision:
                raise ValueError("A sessão mudou antes do carregamento. Preserve uma cópia atual e tente novamente.")
            self._reload_id = secrets.token_hex(16)
            self._reload_cancelled = False
            self._reload_waiters = {ws: asyncio.get_running_loop().create_future() for ws in self.clients}
            self._publish_save()  # clients freeze editing before acknowledging readiness
            ready = await asyncio.wait_for(asyncio.gather(*self._reload_waiters.values()), 8)
            if not all(ready) or self._reload_cancelled or self._closing or revision != self._revision:
                raise ValueError("Carregamento cancelado: há uma aba desconectada ou com edições pendentes. Resolva-as e tente novamente.")
            self._reload_committing = True
            data, version = await asyncio.to_thread(read_version, self.path)
            if data is None:
                raise ValueError("O arquivo externo foi removido. Restaure-o antes de carregar.")
            document = parse_document(data.decode("utf-8"))
            _, copy_version = await asyncio.to_thread(read_version, self._copy[1])
            if copy_version != self._copy[2]:
                self._copy = None
                raise ValueError("A cópia da sessão mudou ou foi removida. Preserve outra cópia.")
            # Start a clean kernel first. Failure leaves the old session usable.
            replacement = Session(self.path.parent / ".nbcache", **self._session_options)
            await replacement.start()
            for cell in document.cells:
                replacement.add(cell.code, cell.kind)  # explicitly no run_all / recovery execution
            await asyncio.to_thread(self.file.check, version)
            _, copy_version = await asyncio.to_thread(read_version, self._copy[1])
            if copy_version != self._copy[2]:
                self._copy = None
                raise ValueError("A cópia da sessão mudou durante o carregamento. Preserve outra cópia.")
            if self._closing:
                raise ValueError("O servidor está encerrando.")
            previous_session = self.session
            previous_session.listeners.remove(self._on_change)
            # No await between final copy validation and publishing the swap.
            self.session = replacement
            replacement = None
            self.session.listeners.append(self._on_change)
            self.file.expected = version
            self._epoch = secrets.token_hex(16)
            self._cell_ids = dict(zip(self.session.sched.cells, document.cell_ids))
            self._edit_sources.clear()
            self._source_versions = self._document_version()
            self._revision = self._saved_revision = 0
            self._file_conflict = self._save_error = None
            self._reload_id = None
            self._reload_committing = False
            for outbox in self.clients.values():
                outbox.put_nowait(self._snapshot())
                outbox.put_nowait({"type": "notice", "text": f"Versão externa carregada sem executar. Cópia da sessão: {self._copy[1]}"})
            self._copy = None
            await previous_session.close()
        except Exception as exc:
            log.warning("file resolution failed: %s", exc, exc_info=True)
            for outbox in self.clients.values():
                outbox.put_nowait({"type": "notice", "text": str(exc) or "Uma aba não confirmou o carregamento. Tente novamente."})
        finally:
            if replacement:
                await replacement.close()
            self._reload_id = None
            self._reload_committing = False
            self._reload_waiters = {}
            self._operation_task = None
            self._publish_save()
