"""Persistence through the real server, including disk faults and concurrent edits."""
import asyncio
import errno
import os
import stat
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import aiohttp
import pytest

import kernel.web as web_module
from kernel.fmt import Cell, parse
from kernel.web import NotebookServer


@asynccontextmanager
async def notebook(tmp_path):
    path = tmp_path / "t.nb.md"
    path.write_text("Original\n")
    server = NotebookServer(path)
    await server.start()
    try:
        async with aiohttp.ClientSession() as http:
            ws = await connect(http, server)
            snapshot = await ws.receive_json()
            yield server, http, ws, snapshot, path
    finally:
        if not server._closing:
            await server.close()


async def connect(http, server):
    return await http.ws_connect(f"http://127.0.0.1:{server.port}/ws?token={server.token}",
                                 headers={"Origin": f"http://127.0.0.1:{server.port}"})


async def receive(ws, predicate):
    async def wait():
        while True:
            message = await ws.receive_json()
            if predicate(message):
                return message
    return await asyncio.wait_for(wait(), 5)


async def save_status(ws, status):
    return (await receive(ws, lambda m: m["type"] == "save_status" and m["save"]["status"] == status))["save"]


async def edit(ws, server, cid, code):
    await ws.send_json({"type": "edit", "cid": cid, "code": code,
                        "base_version": server._cell_json(cid)["version"]})
    return await receive(ws, lambda m: m["type"] == "update" and
                         any(c["id"] == cid and c["code"] == code for c in m["cells"]))


def test_save_serializes_snapshots_and_never_confirms_a_newer_edit(tmp_path, monkeypatch):
    async def main():
        async with notebook(tmp_path) as (server, http, ws, snapshot, path):
            assert snapshot["save"] == {"status": "saved", "revision": 0, "saved_revision": 0, "error": None}
            loop = asyncio.get_running_loop()
            started = [asyncio.Event(), asyncio.Event()]
            release = [threading.Event(), threading.Event()]
            write = web_module._write_notebook
            calls = []

            def slow_write(path, cells):
                index = len(calls)
                calls.append(cells)
                loop.call_soon_threadsafe(started[index].set)
                assert release[index].wait(5), "test did not release the write"
                write(path, cells)

            monkeypatch.setattr(web_module, "_write_notebook", slow_write)
            try:
                cid = snapshot["cells"][0]["id"]
                first = await edit(ws, server, cid, "First")
                assert first["save"]["status"] == "saving"
                await ws.send_json({"type": "retry_save"})  # start now, bypass the debounce
                await asyncio.wait_for(started[0].wait(), 3)
                # Same socket continues dispatching edits while the first writer is blocked.
                second = await edit(ws, server, cid, "Latest")
                assert second["save"]["revision"] == 2
                assert second["save"]["saved_revision"] == 0
                await ws.send_json({"type": "retry_save", "seq": 42})
                await receive(ws, lambda m: m == {"type": "ack", "seq": 42})
                assert len(calls) == 1  # retry cannot launch a competing writer
                assert path.read_text() == "Original\n"
                other = await connect(http, server)
                assert (await other.receive_json())["save"] == second["save"]
                await other.close()
                release[0].set()
                await asyncio.wait_for(started[1].wait(), 3)
                partial = await receive(ws, lambda m: m["type"] == "save_status" and m["save"]["saved_revision"] == 1)
                assert partial["save"]["status"] == "saving"
                assert partial["save"]["revision"] == 2
                assert parse(path.read_text()) == [Cell("markdown", "First")]
                release[1].set()
                saved = await save_status(ws, "saved")
                assert saved["revision"] == saved["saved_revision"] == 2
                assert parse(path.read_text()) == [Cell("markdown", "Latest")]
            finally:
                for event in release:
                    event.set()

    asyncio.run(main())


@pytest.mark.parametrize("stage,number", [("create", errno.EACCES), ("file_sync", errno.ENOSPC),
                                         ("replace", errno.EACCES), ("directory_sync", errno.EIO)])
def test_disk_failures_are_visible_and_retry_preserves_the_accepted_edit(tmp_path, monkeypatch, stage, number):
    async def main():
        async with notebook(tmp_path) as (server, http, ws, snapshot, path):
            cid = snapshot["cells"][0]["id"]
            with monkeypatch.context() as fault:
                def fail(*args, **kwargs):
                    raise OSError(number, os.strerror(number))

                if stage == "create":
                    fault.setattr(web_module.tempfile, "NamedTemporaryFile", fail)
                elif stage == "replace":
                    fault.setattr(Path, "replace", fail)
                else:
                    real_sync = os.fsync

                    def sync(fd):
                        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
                        if directory == (stage == "directory_sync"):
                            fail()
                        real_sync(fd)

                    fault.setattr(web_module.os, "fsync", sync)
                await edit(ws, server, cid, "Preserve me")
                failed = await save_status(ws, "error")  # actual debounce, without manual save
                assert failed["revision"] == 1 and failed["saved_revision"] == 0
                assert failed["error"]
                assert not list(tmp_path.glob(".t.nb.md.*.tmp"))
                assert path.read_text() == ("Preserve me\n" if stage == "directory_sync" else "Original\n")
                assert server.session.to_file_cells() == [Cell("markdown", "Preserve me")]
                other = await connect(http, server)
                assert (await other.receive_json())["save"] == failed
                await other.close()
                await ws.send_json({"type": "retry_save"})
                assert (await save_status(ws, "error"))["saved_revision"] == 0
            await ws.send_json({"type": "retry_save"})
            saved = await save_status(ws, "saved")
            assert saved["revision"] == saved["saved_revision"] == 1
            assert saved["error"] is None
            assert parse(path.read_text()) == [Cell("markdown", "Preserve me")]

    asyncio.run(main())


def test_new_edit_retries_a_failed_autosave(tmp_path, monkeypatch):
    async def main():
        async with notebook(tmp_path) as (server, _, ws, snapshot, path):
            cid = snapshot["cells"][0]["id"]
            with monkeypatch.context() as fault:
                def fail(*args):
                    raise PermissionError(errno.EACCES, "denied")
                fault.setattr(web_module, "_write_notebook", fail)
                await edit(ws, server, cid, "Failed")
                await save_status(ws, "error")
            await edit(ws, server, cid, "Recovered")
            saved = await save_status(ws, "saved")
            assert saved["revision"] == saved["saved_revision"] == 2
            assert parse(path.read_text()) == [Cell("markdown", "Recovered")]

    asyncio.run(main())


@pytest.mark.parametrize("permanent", [False, True])
def test_shutdown_retries_failed_saves_and_cleans_up_even_if_disk_still_fails(tmp_path, monkeypatch, permanent):
    async def main():
        async with notebook(tmp_path) as (server, _, ws, snapshot, path):
            real_write = web_module._write_notebook
            calls = []

            def write(path, cells):
                calls.append(cells)
                if permanent or len(calls) == 1:
                    raise OSError(errno.ENOSPC, "full")
                real_write(path, cells)

            monkeypatch.setattr(web_module, "_write_notebook", write)
            session_close = AsyncMock(wraps=server.session.close)
            cleanup = AsyncMock(wraps=server._runner.cleanup)
            monkeypatch.setattr(server.session, "close", session_close)
            monkeypatch.setattr(type(server._runner), "cleanup", cleanup)
            await edit(ws, server, snapshot["cells"][0]["id"], "Final")
            await save_status(ws, "error")
            if permanent:
                with pytest.raises(OSError, match="alterações pendentes não foram gravadas"):
                    await server.close()
                assert path.read_text() == "Original\n"
            else:
                await server.close()
                assert parse(path.read_text()) == [Cell("markdown", "Final")]
            assert len(calls) == 2
            session_close.assert_awaited_once()
            cleanup.assert_awaited_once()

    asyncio.run(main())


def test_shutdown_waits_for_current_write_and_rejects_new_edits(tmp_path, monkeypatch):
    async def main():
        async with notebook(tmp_path) as (server, _, ws, snapshot, path):
            loop = asyncio.get_running_loop()
            started, release = asyncio.Event(), threading.Event()
            real_write = web_module._write_notebook

            def write(path, cells):
                loop.call_soon_threadsafe(started.set)
                assert release.wait(5)
                real_write(path, cells)

            monkeypatch.setattr(web_module, "_write_notebook", write)
            closing = None
            try:
                cid = snapshot["cells"][0]["id"]
                await edit(ws, server, cid, "Final")
                await ws.send_json({"type": "retry_save"})
                await asyncio.wait_for(started.wait(), 3)
                closing = asyncio.create_task(server.close())
                await asyncio.sleep(0)  # enter close(), which waits for the held writer
                assert server._closing and not closing.done()
                await ws.send_json({"type": "edit", "cid": cid, "code": "Too late", "seq": 7,
                                    "base_version": server._cell_json(cid)["version"]})
                rejection = await receive(ws, lambda m: m["type"] == "error" and m.get("seq") == 7)
                assert "encerrando" in rejection["error"]
                assert server._revision == 1
            finally:
                release.set()
                if closing:
                    await closing
            assert parse(path.read_text()) == [Cell("markdown", "Final")]

    asyncio.run(main())


def test_new_notebook_is_not_reported_saved_before_it_exists(tmp_path):
    async def main():
        path = tmp_path / "new.nb.md"
        server = NotebookServer(path)
        await server.start()
        try:
            async with aiohttp.ClientSession() as http:
                ws = await connect(http, server)
                assert (await ws.receive_json())["save"]["status"] == "saving"
                assert not path.exists()
                await save_status(ws, "saved")
                assert path.exists() and parse(path.read_text()) == []
        finally:
            await server.close()

    asyncio.run(main())


def test_stop_remains_available_on_the_same_socket_during_disk_io(tmp_path, monkeypatch):
    async def main():
        async with notebook(tmp_path) as (server, _, ws, snapshot, path):
            loop = asyncio.get_running_loop()
            started, release = asyncio.Event(), threading.Event()
            real_write = web_module._write_notebook
            calls = []

            def write(path, cells):
                calls.append(cells)
                if len(calls) == 1:
                    loop.call_soon_threadsafe(started.set)
                    assert release.wait(8)
                real_write(path, cells)

            monkeypatch.setattr(web_module, "_write_notebook", write)
            try:
                await edit(ws, server, snapshot["cells"][0]["id"], "Writing")
                await ws.send_json({"type": "retry_save"})
                await asyncio.wait_for(started.wait(), 3)
                await ws.send_json({"type": "add", "kind": "python", "code": "while True: pass"})
                cid = (await receive(ws, lambda m: m["type"] == "added"))["cid"]
                await ws.send_json({"type": "run", "cid": cid})
                await receive(ws, lambda m: any(c["id"] == cid and c["status"] == "running" for c in m.get("cells", [])))
                await ws.send_json({"type": "stop"})
                await receive(ws, lambda m: any(c["id"] == cid and c["status"] == "interrupted" for c in m.get("cells", [])))
                assert not release.is_set()
                assert server._revision == 2  # executing / interrupting changes no source
            finally:
                release.set()
            saved = await save_status(ws, "saved")
            assert saved["revision"] == saved["saved_revision"] == 2
            assert parse(path.read_text())[0].code == "Writing"

    asyncio.run(main())


def test_deleting_the_last_cell_saves_an_empty_notebook(tmp_path):
    async def main():
        async with notebook(tmp_path) as (server, _, ws, snapshot, path):
            await ws.send_json({"type": "delete", "cid": snapshot["cells"][0]["id"]})
            update = await receive(ws, lambda m: m["type"] == "update" and not m["order"])
            assert update["save"]["status"] == "saving"
            saved = await save_status(ws, "saved")
            assert saved["revision"] == saved["saved_revision"] == 1
            assert parse(path.read_text()) == []

    asyncio.run(main())


def test_edit_during_a_failing_write_remains_pending_for_explicit_retry(tmp_path, monkeypatch):
    async def main():
        monkeypatch.setattr(web_module, "SAVE_DEBOUNCE", 0.02)
        async with notebook(tmp_path) as (server, _, ws, snapshot, path):
            loop = asyncio.get_running_loop()
            started, release = asyncio.Event(), threading.Event()
            real_write = web_module._write_notebook
            calls = []

            def write(path, cells):
                calls.append(cells)
                if len(calls) == 1:
                    loop.call_soon_threadsafe(started.set)
                    assert release.wait(5)
                    raise OSError(errno.ENOSPC, "full")
                real_write(path, cells)

            monkeypatch.setattr(web_module, "_write_notebook", write)
            try:
                cid = snapshot["cells"][0]["id"]
                await edit(ws, server, cid, "First")
                await asyncio.wait_for(started.wait(), 3)
                await edit(ws, server, cid, "Latest")
                release.set()
                failed = await save_status(ws, "error")
                assert failed["revision"] == 2 and failed["saved_revision"] == 0
                await asyncio.sleep(0.1)  # beyond the second edit's cancelled debounce
                assert len(calls) == 1
                assert server.session.to_file_cells() == [Cell("markdown", "Latest")]
                assert path.read_text() == "Original\n"
                await ws.send_json({"type": "retry_save"})
                saved = await save_status(ws, "saved")
                assert saved["revision"] == saved["saved_revision"] == 2
                assert parse(path.read_text()) == [Cell("markdown", "Latest")]
            finally:
                release.set()

    asyncio.run(main())
