"""HTTP + WebSocket server: local security (D-016) and the protocol (D-015)."""
import asyncio

import aiohttp
import pytest

from kernel.fmt import parse
from kernel.web import NotebookServer

NB = "# t\n\n```python\na = 20\n```\n\n```python\nb = a + 1\n```\n"


def with_server(fn):
    def wrapper(tmp_path):
        async def main():
            path = tmp_path / "t.nb.md"
            path.write_text(NB)
            srv = NotebookServer(path)
            await srv.start()
            await srv.session.idle()
            try:
                async with aiohttp.ClientSession() as http:
                    await fn(srv, http, path)
            finally:
                await srv.close()
        asyncio.run(main())
    wrapper.__name__ = fn.__name__
    return wrapper


def ws_url(srv, token=None):
    return f"http://127.0.0.1:{srv.port}/ws?token={srv.token if token is None else token}"


def good(srv, **over):
    h = {"Host": f"127.0.0.1:{srv.port}", "Origin": f"http://127.0.0.1:{srv.port}"}
    h.update(over)
    return {k: v for k, v in h.items() if v is not None}


async def handshake_status(http, url, headers):
    try:
        ws = await http.ws_connect(url, headers=headers)
    except aiohttp.WSServerHandshakeError as e:
        return e.status
    await ws.close()
    return 101


# ---------------- D-016: each rejection ----------------

@with_server
async def test_binds_to_loopback_only(srv, http, path):
    assert [addr[0] for addr in srv.bound] == ["127.0.0.1"]


@with_server
async def test_http_rejects_foreign_host_dns_rebinding(srv, http, path):
    r = await http.get(f"http://127.0.0.1:{srv.port}/", headers={"Host": f"evil.example:{srv.port}"})
    assert r.status == 403 and "Host" in await r.text()
    r = await http.get(f"http://127.0.0.1:{srv.port}/", headers={"Host": "127.0.0.1:1"})  # wrong port
    assert r.status == 403
    r = await http.get(f"http://127.0.0.1:{srv.port}/")
    assert r.status == 200
    r = await http.get(f"http://127.0.0.1:{srv.port}/", headers={"Host": f"localhost:{srv.port}"})
    assert r.status == 200


@with_server
async def test_ws_rejects_foreign_host(srv, http, path):
    assert await handshake_status(http, ws_url(srv), good(srv, Host=f"evil.example:{srv.port}")) == 403


@with_server
async def test_ws_rejects_foreign_origin(srv, http, path):
    assert await handshake_status(http, ws_url(srv), good(srv, Origin="https://evil.example")) == 403
    assert await handshake_status(http, ws_url(srv), good(srv, Origin=f"http://127.0.0.1:{srv.port + 1}")) == 403


@with_server
async def test_ws_rejects_missing_origin(srv, http, path):
    assert await handshake_status(http, ws_url(srv), good(srv, Origin=None)) == 403


@with_server
async def test_ws_rejects_missing_or_wrong_token(srv, http, path):
    assert await handshake_status(http, f"http://127.0.0.1:{srv.port}/ws", good(srv)) == 403
    assert await handshake_status(http, ws_url(srv, token="x" + srv.token), good(srv)) == 403
    assert await handshake_status(http, ws_url(srv), good(srv)) == 101


@with_server
async def test_ws_accepts_localhost_origin_and_extra_dev_origin(srv, http, path):
    assert await handshake_status(http, ws_url(srv), good(srv, Origin=f"http://localhost:{srv.port}")) == 101
    assert await handshake_status(http, ws_url(srv), good(srv, Origin="http://localhost:5173")) == 403
    srv.extra_origins.add("http://localhost:5173")
    assert await handshake_status(http, ws_url(srv), good(srv, Origin="http://localhost:5173")) == 101


# ---------------- protocol ----------------

async def recv_until(ws, pred, timeout=30):
    async def loop():
        while True:
            m = await ws.receive_json()
            if pred(m):
                return m
    return await asyncio.wait_for(loop(), timeout)


@with_server
async def test_snapshot_edit_run_and_save(srv, http, path):
    ws = await http.ws_connect(ws_url(srv), headers=good(srv))
    snap = await ws.receive_json()
    assert snap["type"] == "snapshot" and [c["status"] for c in snap["cells"]] == ["idle", "ok", "ok"]
    md, a, b = (c["id"] for c in snap["cells"])
    assert snap["edges"] == [[a, b]]
    await ws.send_json({"type": "edit", "cid": a, "code": "a = 100"})
    m = await recv_until(ws, lambda m: any(c["id"] == a and c["status"] == "modified" for c in m.get("cells", [])))
    child = next(c for c in m["cells"] if c["id"] == b)          # review item 6: flag reaches the frontend
    assert child["status"] == "ok" and child["upstream_modified"] == [a] and child["previews"]["b"]["repr"] == "21"
    await ws.send_json({"type": "run", "cid": a})
    m = await recv_until(ws, lambda m: any(c["id"] == b and c["status"] == "ok" for c in m.get("cells", [])))
    assert next(c for c in m["cells"] if c["id"] == b)["previews"]["b"]["repr"] == "101"
    await asyncio.sleep(1.3)  # save debounce
    assert [c.code for c in parse(path.read_text())][1] == "a = 100"
    assert not path.with_suffix(".md.tmp").exists()
    await ws.send_json({"type": "nope"})
    assert (await recv_until(ws, lambda m: m["type"] == "error"))["error"].startswith("unknown message type")
    await ws.close()


@with_server
async def test_nan_previews_are_valid_browser_json(srv, http, path):
    ws = await http.ws_connect(ws_url(srv), headers=good(srv))
    await ws.receive_json()
    await ws.send_json({"type": "add", "code": "import numpy as _np\nz = _np.array([1.0, _np.nan, _np.inf])"})
    added = await recv_until(ws, lambda m: m["type"] == "added")
    await ws.send_json({"type": "run", "cid": added["cid"]})
    m = await recv_until(ws, lambda m: any(c["id"] == added["cid"] and c["status"] == "ok" for c in m.get("cells", [])))
    raw = next(c for c in m["cells"] if c["id"] == added["cid"])["previews"]["z"]["head"]
    assert raw == [1.0, "nan", "inf"]
    await ws.close()


def test_tokenless_pages_carry_no_notebook_content_and_keep_host_check(tmp_path):  # review item 3
    secret = "SEGREDO_DO_NOTEBOOK_7f3a"
    path = tmp_path / "s.nb.md"
    path.write_text(f"# {secret}\n\n```python\nx = '{secret}'\n```\n")
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><div id=root></div><script src=/assets/app.js></script>")
    (static / "assets" / "app.js").write_text("console.log('app')")

    async def main():
        for static_dir in (None, static):  # placeholder page and a real frontend build
            srv = NotebookServer(path, static_dir=static_dir)
            await srv.start()
            await srv.session.idle()
            base = f"http://127.0.0.1:{srv.port}"
            try:
                async with aiohttp.ClientSession() as http:
                    routes = ["/"] + (["/assets/app.js"] if static_dir else [])
                    for route in routes:
                        r = await http.get(base + route)                 # no token at all
                        body = await r.text()
                        assert r.status == 200 and secret not in body and "x =" not in body
                        r = await http.get(base + route, headers={"Host": f"evil.example:{srv.port}"})
                        assert r.status == 403                           # Host check still applies
            finally:
                await srv.close()

    asyncio.run(main())
