"""HTTP + WebSocket server (D-015, D-016).

Security (D-016): bound to 127.0.0.1 only; every HTTP request (including the
WebSocket handshake) must carry an allowed Host header (DNS rebinding); the
WebSocket additionally requires an allowed Origin (cross-site WebSocket
hijacking) and the random token printed at startup.
"""
import asyncio
import errno
import json
import logging
import math
import os
import secrets
import tempfile
from pathlib import Path

from aiohttp import WSMsgType, web

from .fmt import parse, serialize
from .session import Session

SAVE_DEBOUNCE = 1.0  # seconds after the last change (D-015)
log = logging.getLogger("notebook.web")


def _write_notebook(path, cells):
    """One immutable snapshot, replaced atomically; all disk I/O runs off the loop."""
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as f:
            tmp = Path(f.name)
            if path.exists():
                os.fchmod(f.fileno(), path.stat().st_mode & 0o777)
            f.write(serialize(cells))
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
        # A successful rename alone does not confirm directory durability.
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                log.warning("could not remove temporary notebook %s", tmp, exc_info=True)


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
                   "output": c.output, "previews": c.previews, "defs": sorted(c.defs), "refs": sorted(c.refs),
                   # review item 6: values kept, but computed from outdated upstream code
                   "upstream_modified": sched.upstream_modified(cid),
                   # review item 7: speculative build in flight / its errors as editor diagnostics
                   "compiling": c.compiling is not None, "diagnostics": c.diagnostics,
                   "queue_position": sched.queue_positions.get(cid)})


class NotebookServer:
    def __init__(self, path: Path, port: int = 0, token: str | None = None,
                 static_dir: Path | None = None, extra_origins: tuple[str, ...] = (),
                 core_dumps: bool = False, speculate_debounce: float = 0.3):
        self.path = Path(path)
        self.token = token or secrets.token_urlsafe(32)
        self.static_dir = static_dir
        self.extra_origins = set(extra_origins)
        self.port = port
        self.session = Session(self.path.parent / ".nbcache", core_dumps=core_dumps,
                               speculate_debounce=speculate_debounce)
        self.clients: dict[web.WebSocketResponse, asyncio.Queue] = {}  # one ordered outbox per client
        self._epoch = secrets.token_hex(16)
        self._edit_sources: dict[int, tuple[int, str | None]] = {}
        self._save_handle = None
        self._save_task = None
        self._revision = 0
        self._saved_revision = 0
        self._save_error = None
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
        exists = self.path.exists()
        self.session.load(parse(self.path.read_text()) if exists else [])
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
            if self._save_task:
                await asyncio.shield(self._save_task)
            if self._saved_revision != self._revision:
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
                    await self._runner.cleanup()

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
        if self._closing:
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
                "edges": self._edges(), "kernel": self._kernel_state(), "save": self._save_state()}

    def _cell_json(self, cid):
        s = self.session.sched
        c = s.cells[cid]
        revision, request = self._edit_sources.get(cid, (-1, None))
        return {**cell_json(cid, c, s), "version": f"{self._epoch}:{cid}:{c.revision}",
                "edit_id": request if revision == c.revision else None}

    def _edges(self):
        parents, _, _ = self.session.sched._graph()
        return sorted([p, c] for c, ps in parents.items() for p in ps)

    def _kernel_state(self):
        event = self.session.sched.kernel_event
        return {"restarts": self.session.restarts, "dead": self.session.sched.kernel_dead,
                "event": {**event, "id": f"{self._epoch}:{event['id']}"} if event else None}

    def _handle(self, ws, m):
        if self._closing:
            raise ValueError("O servidor está encerrando; a ação não foi aceita.")
        s = self.session
        kind = m.get("type")
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
            elif kind == "stop":
                s.stop()
            elif kind == "retry_save":
                self._start_save()
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
        return {"status": "error" if self._save_error else
                          "saved" if self._saved_revision == self._revision else "saving",
                "revision": self._revision, "saved_revision": self._saved_revision,
                "error": self._save_error}

    def _publish_save(self):
        msg = {"type": "save_status", "save": self._save_state()}
        for outbox in self.clients.values():
            outbox.put_nowait(msg)

    def _schedule_save(self):
        self._revision += 1
        if self._save_handle:
            self._save_handle.cancel()
        self._save_handle = asyncio.get_running_loop().call_later(SAVE_DEBOUNCE, self._start_save)
        self._publish_save()

    def _start_save(self):
        if self._save_handle:
            self._save_handle.cancel()
        self._save_handle = None
        if self._save_task or self._saved_revision == self._revision:
            return
        self._save_error = None
        self._save_task = asyncio.create_task(self._save())
        self._publish_save()

    async def _save(self):
        try:
            while self._saved_revision != self._revision:
                revision = self._revision
                cells = self.session.to_file_cells()  # copy on the loop, before crossing threads
                try:
                    await asyncio.to_thread(_write_notebook, self.path, cells)
                except Exception as exc:
                    reason = {errno.EACCES: "Sem permissão para gravar o arquivo.",
                              errno.EPERM: "Sem permissão para gravar o arquivo.",
                              errno.ENOSPC: "Sem espaço em disco."}.get(getattr(exc, "errno", None))
                    self._save_error = reason or "Não foi possível concluir a gravação do arquivo."
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
