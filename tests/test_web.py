"""HTTP + WebSocket server: local security (D-016) and the protocol (D-015)."""
import asyncio

import aiohttp
import pytest

from kernel.fmt import Cell, parse
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
                if not srv._closing:
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
    await ws.send_json({"type": "edit", "cid": a, "base_version": srv._cell_json(a)["version"], "code": "a = 100"})
    m = await recv_until(ws, lambda m: any(c["id"] == a and c["status"] == "modified" for c in m.get("cells", [])))
    child = next(c for c in m["cells"] if c["id"] == b)          # review item 6: flag reaches the frontend
    assert "queue_position" in child
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


def test_protocol_exposes_compiling_flag_and_diagnostics(tmp_path):  # review item 7
    import shutil, uuid
    if not shutil.which("mojo"):
        pytest.skip("mojo not on PATH")

    @with_server
    async def body(srv, http, path):
        srv.session.speculate_debounce = 0.05
        ws = await http.ws_connect(ws_url(srv), headers=good(srv))
        await ws.receive_json()
        await ws.send_json({"type": "add", "kind": "mojo", "code": "def run(mut t: Int) raises:\n    t = 1"})
        cid = (await recv_until(ws, lambda m: m["type"] == "added"))["cid"]
        await ws.send_json({"type": "edit", "cid": cid, "base_version": srv._cell_json(cid)["version"],
                            "code": f"# nonce {uuid.uuid4().hex}\ndef run(mut t: Int) raises:\n    t = nope"})
        await recv_until(ws, lambda m: any(c["id"] == cid and c["compiling"] for c in m.get("cells", [])))
        m = await recv_until(ws, lambda m: any(c["id"] == cid and c["diagnostics"] for c in m.get("cells", [])))
        c = next(c for c in m["cells"] if c["id"] == cid)
        assert not c["compiling"] and c["status"] == "idle"
        assert c["diagnostics"][0]["line"] == 3 and "nope" in c["diagnostics"][0]["message"]
        await ws.close()

    body(tmp_path)



@with_server
async def test_delete_always_broadcasts_the_new_order(srv, http, path):
    ws = await http.ws_connect(ws_url(srv), headers=good(srv))
    snap = await ws.receive_json()
    md = snap["cells"][0]["id"]                     # a markdown cell: deleting it changes nothing else
    await ws.send_json({"type": "delete", "cid": md})
    m = await recv_until(ws, lambda m: m["type"] == "update" and md not in m["order"])
    assert len(m["order"]) == 2
    await ws.close()


# ---------------- review item 1(a): one connection must never block on long work ----------------

async def cell_status(ws, cid, want, timeout=30):
    return await recv_until(ws, lambda m: any(c["id"] == cid and c["status"] == want for c in m.get("cells", [])),
                            timeout=timeout)


@with_server
async def test_stop_on_the_same_connection_that_started_the_loop(srv, http, path):
    ws = await http.ws_connect(ws_url(srv), headers=good(srv))
    await ws.receive_json()
    await ws.send_json({"type": "add", "code": "import time as _t\nwhile True:\n    _t.sleep(0.01)"})
    cid = (await recv_until(ws, lambda m: m["type"] == "added"))["cid"]
    await ws.send_json({"type": "run", "cid": cid})
    await cell_status(ws, cid, "running")
    await asyncio.sleep(0.3)
    await ws.send_json({"type": "stop"})          # same connection, while the loop runs
    await cell_status(ws, cid, "interrupted", timeout=10)
    await ws.close()


@with_server
async def test_edit_and_run_on_the_same_connection_during_a_long_build(srv, http, path):
    import shutil, uuid
    if not shutil.which("mojo"):
        pytest.skip("mojo not on PATH")
    ws = await http.ws_connect(ws_url(srv), headers=good(srv))
    snap = await ws.receive_json()
    a = snap["cells"][1]["id"]                    # "a = 20"
    await ws.send_json({"type": "add", "kind": "mojo",
                        "code": f"# nonce {uuid.uuid4().hex}\ndef run(a: Int, mut m: Int) raises:\n    m = a"})
    m = (await recv_until(ws, lambda x: x["type"] == "added"))["cid"]
    await ws.send_json({"type": "run", "cid": m})
    await cell_status(ws, m, "compiling")
    t0 = asyncio.get_running_loop().time()
    await ws.send_json({"type": "edit", "cid": a, "base_version": srv._cell_json(a)["version"], "code": "a = 7"})   # same connection, mid-build
    await ws.send_json({"type": "run", "cid": a})
    msg = await cell_status(ws, a, "ok", timeout=10)
    python_done = asyncio.get_running_loop().time() - t0
    assert next(c for c in msg["cells"] if c["id"] == a)["previews"]["a"]["repr"] == "7"
    assert srv.session.sched.cells[m].status in ("compiling", "stale", "queued"), "the build should still be going"
    assert python_done < 1.0, python_done
    await cell_status(ws, m, "ok", timeout=60)
    await ws.close()


@with_server
async def test_dispatch_ack_does_not_wait_for_execution_and_bad_messages_do_not_kill_reader(srv, http, path):
    ws = await http.ws_connect(ws_url(srv), headers=good(srv))
    await ws.receive_json()
    await ws.send_str("{broken")
    assert (await ws.receive_json())["type"] == "error"
    await ws.send_json([])
    assert (await ws.receive_json())["type"] == "error"
    await ws.send_json({"type": "add", "code": "while True: pass", "seq": 1})
    cid = (await recv_until(ws, lambda m: m["type"] == "added"))["cid"]
    await recv_until(ws, lambda m: m == {"type": "ack", "seq": 1})
    await ws.send_json({"type": "run", "cid": cid, "seq": 2})
    await recv_until(ws, lambda m: m == {"type": "ack", "seq": 2}, timeout=3)
    assert srv.session.sched.cells[cid].status == "running"
    await ws.send_json({"type": "stop", "seq": 3})
    await cell_status(ws, cid, "interrupted", timeout=10)
    await ws.close()


@with_server
async def test_edit_on_same_connection_while_promoted_build_is_held(srv, http, path):
    from unittest.mock import patch

    started, release = asyncio.Event(), asyncio.Event()
    original = srv.session._build

    async def held_build(*args):
        started.set()
        await release.wait()
        await original(*args)

    ws = await http.ws_connect(ws_url(srv), headers=good(srv))
    snap = await ws.receive_json()
    a = snap["cells"][1]["id"]
    srv.session.speculate_debounce = 0.01
    with patch.object(srv.session, "_build", held_build):
        await ws.send_json({"type": "add", "kind": "mojo", "code": ""})
        cid = (await recv_until(ws, lambda m: m["type"] == "added"))["cid"]
        await ws.send_json({"type": "edit", "cid": cid, "base_version": srv._cell_json(cid)["version"], "code": "def run(mut out: Int):\n    out = 42"})
        await asyncio.wait_for(started.wait(), 3)
        await ws.send_json({"type": "run", "cid": cid})
        await cell_status(ws, cid, "compiling")
        await ws.send_json({"type": "edit", "cid": a, "base_version": srv._cell_json(a)["version"], "code": "a = 9"})
        await ws.send_json({"type": "run", "cid": a})
        result = await cell_status(ws, a, "ok", timeout=3)
        assert next(c for c in result["cells"] if c["id"] == a)["previews"]["a"]["repr"] == "9"
        assert not release.is_set()
        assert any(event == "build-promoted" for _, event, _ in srv.session.log)
        release.set()
        await cell_status(ws, cid, "ok")
    await ws.close()


def test_writer_failure_closes_the_connection():
    from unittest.mock import AsyncMock

    async def main():
        ws = AsyncMock()
        ws.send_json.side_effect = ConnectionError("peer gone")
        outbox = asyncio.Queue()
        outbox.put_nowait({"type": "ack", "seq": 1})
        await NotebookServer._writer(ws, outbox)
        ws.close.assert_awaited_once()
    asyncio.run(main())


@with_server
async def test_stale_offline_edit_cannot_overwrite_another_client_even_after_code_returns_to_original(srv, http, path):
    ws1 = await http.ws_connect(ws_url(srv), headers=good(srv))
    old = (await ws1.receive_json())["cells"][1]
    await ws1.close()  # client keeps an offline edit based on this version
    ws2 = await http.ws_connect(ws_url(srv), headers=good(srv))
    await ws2.receive_json()
    cid = old["id"]
    for code in ("a = 99", old["code"]):
        await ws2.send_json({"type": "edit", "cid": cid, "code": code,
                            "base_version": srv._cell_json(cid)["version"]})
        await recv_until(ws2, lambda m: any(c["id"] == cid and c["code"] == code for c in m.get("cells", [])))
    ws1 = await http.ws_connect(ws_url(srv), headers=good(srv))
    await ws1.receive_json()
    await ws1.send_json({"type": "edit", "cid": cid, "code": "a = -1", "request": "offline",
                         "base_version": old["version"], "seq": 1})
    conflict = await recv_until(ws1, lambda m: m["type"] == "conflict")
    assert conflict["request"] == "offline"
    assert conflict["cell"]["code"] == old["code"]
    assert conflict["cell"]["version"] != old["version"]
    assert srv.session.sched.cells[cid].code == old["code"]
    # Explicitly keeping the local draft still uses CAS against the latest version.
    await ws1.send_json({"type": "edit", "cid": cid, "code": "a = -1", "request": "resolved",
                         "base_version": conflict["cell"]["version"]})
    accepted = await recv_until(ws1, lambda m: any(c.get("edit_id") == "resolved" for c in m.get("cells", [])))
    assert next(c for c in accepted["cells"] if c["id"] == cid)["code"] == "a = -1"
    await ws1.close()
    await ws2.close()


@with_server
async def test_edit_requires_a_version_and_retry_does_not_create_another_revision(srv, http, path):
    ws = await http.ws_connect(ws_url(srv), headers=good(srv))
    old = (await ws.receive_json())["cells"][1]
    edit = {"type": "edit", "cid": old["id"], "code": "a = 7", "request": "once"}
    await ws.send_json(edit)
    assert (await recv_until(ws, lambda m: m["type"] == "conflict"))["cell"]["version"] == old["version"]
    edit["base_version"] = old["version"]
    await ws.send_json(edit)
    await recv_until(ws, lambda m: any(c.get("edit_id") == "once" for c in m.get("cells", [])))
    accepted_version = srv._cell_json(old["id"])["version"]
    await ws.close()
    ws = await http.ws_connect(ws_url(srv), headers=good(srv))
    await ws.receive_json()
    await ws.send_json(edit)  # ACK lost before reconnect: same edit is still current
    reply = await recv_until(ws, lambda m: any(c.get("edit_id") == "once" for c in m.get("cells", [])))
    assert next(c for c in reply["cells"] if c["id"] == old["id"])["version"] == accepted_version
    await ws.close()


@with_server
async def test_version_from_another_server_lifetime_is_rejected(srv, http, path):
    old = srv._cell_json(2)
    await srv.close()  # genuinely another lifetime: concurrent writers are now refused
    other = NotebookServer(path)
    await other.start()
    try:
        await other.session.idle()
        ws = await http.ws_connect(ws_url(other), headers=good(other))
        await ws.receive_json()
        await ws.send_json({"type": "edit", "cid": 2, "code": "a = 9", "base_version": old["version"]})
        conflict = await recv_until(ws, lambda m: m["type"] == "conflict")
        assert conflict["cell"]["code"] == old["code"]
        assert conflict["cell"]["version"] != old["version"]
        await ws.close()
    finally:
        await other.close()


@with_server
async def test_kernel_incident_id_is_stable_through_recovery_and_reconnect(srv, http, path):
    ws = await http.ws_connect(ws_url(srv), headers=good(srv))
    await ws.receive_json()
    await ws.send_json({"type": "add", "code": "import os as _os\n_os._exit(23)"})
    cid = (await recv_until(ws, lambda m: m["type"] == "added"))["cid"]
    await ws.send_json({"type": "run_all"})
    crash = await cell_status(ws, cid, "crashed")
    event = crash["kernel"]["event"]
    assert event["kind"] == "crashed" and event["cid"] == cid
    await srv.session.idle()
    assert srv._kernel_state()["event"] == event
    await ws.close()
    ws = await http.ws_connect(ws_url(srv), headers=good(srv))
    assert (await ws.receive_json())["kernel"]["event"] == event
    await ws.send_json({"type": "edit", "cid": cid, "code": "while True: pass",
                        "base_version": srv._cell_json(cid)["version"]})
    await ws.send_json({"type": "run_all"})
    await cell_status(ws, cid, "running")
    await ws.send_json({"type": "stop"})
    stop = await cell_status(ws, cid, "interrupted")
    assert stop["kernel"]["event"]["kind"] == "interrupted"
    assert stop["kernel"]["event"]["id"] != event["id"]
    await srv.session.idle()
    assert srv._kernel_state()["event"] == stop["kernel"]["event"]
    await ws.close()


@with_server
async def test_edit_of_running_cell_publishes_accepted_source_before_completion(srv, http, path):
    ws = await http.ws_connect(ws_url(srv), headers=good(srv))
    await ws.receive_json()
    await ws.send_json({"type": "add", "code": "while True: pass"})
    cid = (await recv_until(ws, lambda m: m["type"] == "added"))["cid"]
    old = srv._cell_json(cid)
    await ws.send_json({"type": "run", "cid": cid})
    await cell_status(ws, cid, "running")
    await ws.send_json({"type": "edit", "cid": cid, "code": "answer = 42", "request": "during-run",
                        "base_version": old["version"]})
    result = await recv_until(ws, lambda m: any(c.get("edit_id") == "during-run" for c in m.get("cells", [])), timeout=3)
    edited = next(c for c in result["cells"] if c["id"] == cid)
    assert edited["status"] == "running" and edited["code"] == "answer = 42"
    assert edited["version"] != old["version"]
    await ws.send_json({"type": "stop"})
    await cell_status(ws, cid, "interrupted")
    await ws.close()


def test_markdown_boundaries_survive_autosave_and_a_fresh_server(tmp_path):
    async def main():
        path = tmp_path / "roundtrip.nb.md"
        path.write_text("```python\nsafe = 1\n```\n")
        expected = [Cell("python", "safe = 1"),
                    Cell("markdown", "# Primeira"),
                    Cell("markdown", "\n```python\nraise RuntimeError('only an example')\n```\n<!-- === -->\n"),
                    Cell("markdown", ""), Cell("markdown", ""),
                    Cell("html", "<b>fim</b>")]
        server = NotebookServer(path)
        await server.start()
        try:
            await server.session.idle()
            async with aiohttp.ClientSession() as http:
                ws = await http.ws_connect(ws_url(server), headers=good(server))
                await ws.receive_json()
                for cell in expected[1:]:
                    await ws.send_json({"type": "add", "kind": cell.kind, "code": cell.code})
                    await recv_until(ws, lambda m: m["type"] == "added")

                async def autosaved():
                    while parse(path.read_text()) != expected:
                        await asyncio.sleep(0.025)

                # close() also saves: check disk while the original server is alive.
                await asyncio.wait_for(autosaved(), timeout=5)
                await ws.close()
        finally:
            await server.close()

        reopened = NotebookServer(path)
        await reopened.start()
        try:
            await reopened.session.idle()
            async with aiohttp.ClientSession() as http:
                ws = await http.ws_connect(ws_url(reopened), headers=good(reopened))
                snapshot = await ws.receive_json()
                assert [Cell(c["kind"], c["code"]) for c in snapshot["cells"]] == expected
                assert snapshot["cells"][0]["status"] == "ok"
                assert snapshot["kernel"]["event"] is None
                await ws.close()
        finally:
            await reopened.close()

    asyncio.run(main())
