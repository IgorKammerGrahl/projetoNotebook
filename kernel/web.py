"""HTTP + WebSocket server (D-015, D-016).

Security (D-016): bound to 127.0.0.1 only; every HTTP request (including the
WebSocket handshake) must carry an allowed Host header (DNS rebinding); the
WebSocket additionally requires an allowed Origin (cross-site WebSocket
hijacking) and the random token printed at startup.
"""
import asyncio
import json
import math
import secrets
from pathlib import Path

from aiohttp import WSMsgType, web

from .fmt import parse, serialize
from .session import Session

SAVE_DEBOUNCE = 1.0  # seconds after the last change (D-015)


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
        self.clients: set[web.WebSocketResponse] = set()
        self._save_handle = None
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
        self.session.load(parse(self.path.read_text()) if self.path.exists() else [])

    async def close(self):
        if self._save_handle:
            self._save_handle.cancel()
            self._save()
        for ws in list(self.clients):
            await ws.close()
        await self.session.close()
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
        if request.headers.get("Origin") not in self.allowed_origins():
            raise web.HTTPForbidden(text="forbidden: unexpected Origin")
        if not secrets.compare_digest(request.query.get("token", ""), self.token):
            raise web.HTTPForbidden(text="forbidden: bad token")
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self.clients.add(ws)
        try:
            await ws.send_json(self._snapshot())
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle(ws, json.loads(msg.data))
        finally:
            self.clients.discard(ws)
        return ws

    def _snapshot(self):
        s = self.session.sched
        return {"type": "snapshot", "cells": [cell_json(cid, c, s) for cid, c in s.cells.items()],
                "edges": self._edges(), "kernel": self._kernel_state()}

    def _edges(self):
        parents, _, _ = self.session.sched._graph()
        return sorted([p, c] for c, ps in parents.items() for p in ps)

    def _kernel_state(self):
        return {"restarts": self.session.restarts, "dead": self.session.sched.kernel_dead}

    async def _handle(self, ws, m):
        s = self.session
        kind = m.get("type")
        try:
            if kind == "edit":
                s.edit(m["cid"], m["code"])
            elif kind == "run":
                s.run(m["cid"])
            elif kind == "run_all":
                s.run_all()
            elif kind == "add":
                cid = s.add(m.get("code", ""), m.get("kind", "python"), m.get("after"))
                await ws.send_json({"type": "added", "cid": cid, "request": m.get("request")})
            elif kind == "delete":
                s.delete(m["cid"])
            elif kind == "stop":
                s.stop()
            else:
                await ws.send_json({"type": "error", "error": f"unknown message type {kind!r}"})
                return
        except KeyError as e:
            await ws.send_json({"type": "error", "error": f"bad message: missing or unknown {e}"})
            return
        if kind == "delete":
            self._on_change({})  # nothing else may change: still tell clients the new `order`
        if kind in ("edit", "add", "delete"):
            self._schedule_save()

    def _on_change(self, changed):
        msg = {"type": "update", "cells": [cell_json(cid, c, self.session.sched) for cid, c in changed.items()],
               "order": list(self.session.sched.cells), "edges": self._edges(),
               "kernel": self._kernel_state()}
        for ws in list(self.clients):
            asyncio.ensure_future(ws.send_json(msg))

    # ---------------- file ----------------

    def _schedule_save(self):
        if self._save_handle:
            self._save_handle.cancel()
        self._save_handle = asyncio.get_running_loop().call_later(SAVE_DEBOUNCE, self._save)

    def _save(self):
        self._save_handle = None
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(serialize(self.session.to_file_cells()))
        tmp.replace(self.path)  # atomic: a crash mid-save never truncates the notebook
