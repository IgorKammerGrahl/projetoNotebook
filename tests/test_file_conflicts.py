"""File ownership and external edits through real servers and kernel processes."""
import asyncio
import errno
import os
import signal
import sys
from pathlib import Path

import aiohttp
import pytest

import kernel.storage as storage
from kernel.fmt import Cell, parse
from kernel.web import NotebookServer
from kernel.session import Session
from test_persistence import connect, edit, receive, save_status


async def close_conflicted(server):
    with pytest.raises(OSError, match="alterações pendentes"):
        await server.close()


async def process(path):
    child = await asyncio.create_subprocess_exec(sys.executable, "-m", "kernel", "serve", str(path), "--port", "0",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    return child


async def stop_process(child):
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await child.wait()


def test_two_server_processes_lock_aliases_and_sigkill_releases_ownership(tmp_path):
    async def main():
        path = tmp_path / "work.nb.md"
        path.write_text("Original\n")
        alias = tmp_path / "alias.nb.md"
        alias.symlink_to(path)
        first = await process(path)
        second = third = None
        try:
            assert b"notebook: http" in await asyncio.wait_for(first.stdout.readline(), 10)
            second = await process(alias)
            output, _ = await asyncio.wait_for(second.communicate(), 10)
            assert second.returncode == 1
            assert "já está aberto em outro servidor" in output.decode()
            assert b"notebook: http" not in output
            # Kill only the owner, not its process group: children must not inherit the lock.
            first.kill()
            await first.wait()
            third = await process(path)
            assert b"notebook: http" in await asyncio.wait_for(third.stdout.readline(), 10)
            assert path.read_text() == "Original\n"
        finally:
            for child in (first, second, third):
                if child:
                    await stop_process(child)
    asyncio.run(main())


def test_startup_failure_releases_lock_and_does_not_launch_a_kernel(tmp_path):
    async def main():
        path = tmp_path / "bad.nb.md"
        path.write_text("<!-- notebook-format: 2 -->\ninvalid")
        server = NotebookServer(path)
        with pytest.raises(ValueError):
            await server.start()
        assert server.file.fd is None and server.session._kernel is None
        path.write_text("Valid\n")
        other = NotebookServer(path)
        await other.start()
        await other.close()
    asyncio.run(main())


@pytest.mark.parametrize("change", ["in_place", "replace", "delete", "symlink", "hardlink"])
def test_external_changes_suspend_saves_including_retry_new_edits_and_shutdown(tmp_path, change):
    async def main():
        path = tmp_path / "work.nb.md"
        path.write_text("Original\n")
        server = NotebookServer(path)
        await server.start()
        try:
            async with aiohttp.ClientSession() as http:
                ws = await connect(http, server)
                snapshot = await ws.receive_json()
                target = tmp_path / "outside.nb.md"
                target.write_text("External\n")
                if change == "in_place":
                    path.write_text("External\n")
                elif change == "replace":
                    target.replace(path)
                elif change == "hardlink":
                    os.link(path, tmp_path / "hard.nb.md")
                else:
                    path.unlink()
                    if change == "symlink":
                        path.symlink_to(target)
                expected = path.read_bytes() if path.exists() else None
                await edit(ws, server, snapshot["cells"][0]["id"], "Local")
                await ws.send_json({"type": "retry_save"})
                conflict = await save_status(ws, "conflict")
                assert conflict["saved_revision"] == 0
                other = await connect(http, server)
                assert (await other.receive_json())["save"] == conflict
                await ws.send_json({"type": "retry_save", "seq": 7})
                await receive(ws, lambda m: m == {"type": "ack", "seq": 7})
                await edit(ws, server, snapshot["cells"][0]["id"], "New local")
                assert server._save_state()["status"] == "conflict"
                assert server._save_handle is None or server._save_handle.cancelled()
                assert (path.read_bytes() if path.exists() else None) == expected
        finally:
            await close_conflicted(server)
        assert server.file.fd is None
        assert (path.read_bytes() if path.exists() else None) == expected
    asyncio.run(main())


def test_external_save_in_the_last_rename_window_is_preserved_not_silently_discarded(tmp_path, monkeypatch):
    async def main():
        path = tmp_path / "work.nb.md"
        path.write_text("Original\n")
        server = NotebookServer(path)
        await server.start()
        original = storage._rename

        def racing_rename(source, destination, flags):
            external = tmp_path / "editor-save"
            external.write_text("External racing save\n")
            external.replace(destination)
            original(source, destination, flags)

        monkeypatch.setattr(storage, "_rename", racing_rename)
        try:
            async with aiohttp.ClientSession() as http:
                ws = await connect(http, server)
                snapshot = await ws.receive_json()
                await edit(ws, server, snapshot["cells"][0]["id"], "Local")
                await ws.send_json({"type": "retry_save"})
                conflict = await save_status(ws, "conflict")
                preserved = Path(conflict["conflict"]["preserved"])
                assert preserved.read_text() == "External racing save\n"
                assert parse(path.read_text()) == [Cell("markdown", "Local")]
                assert conflict["saved_revision"] == 0
        finally:
            await close_conflicted(server)
        assert preserved.read_text() == "External racing save\n"
    asyncio.run(main())


def test_creation_race_never_replaces_an_external_file(tmp_path, monkeypatch):
    async def main():
        path = tmp_path / "new.nb.md"
        server = NotebookServer(path)
        await server.start()
        original = storage._rename

        def racing_rename(source, destination, flags):
            path.write_text("Created externally\n")
            original(source, destination, flags)

        monkeypatch.setattr(storage, "_rename", racing_rename)
        try:
            async with aiohttp.ClientSession() as http:
                ws = await connect(http, server)
                await ws.receive_json()
                await ws.send_json({"type": "retry_save"})
                await save_status(ws, "conflict")
                assert path.read_text() == "Created externally\n"
        finally:
            await close_conflicted(server)
    asyncio.run(main())


def test_unsupported_atomic_exchange_fails_without_unsafe_fallback(tmp_path, monkeypatch):
    async def main():
        path = tmp_path / "work.nb.md"
        path.write_text("Original\n")
        server = NotebookServer(path)
        await server.start()

        def unsupported(*args):
            raise OSError(errno.EOPNOTSUPP, "exchange unsupported")

        monkeypatch.setattr(storage, "_rename", unsupported)
        try:
            async with aiohttp.ClientSession() as http:
                ws = await connect(http, server)
                snapshot = await ws.receive_json()
                await edit(ws, server, snapshot["cells"][0]["id"], "Local")
                await ws.send_json({"type": "retry_save"})
                await save_status(ws, "error")
                assert path.read_text() == "Original\n"
        finally:
            await close_conflicted(server)
    asyncio.run(main())


def test_in_place_write_on_the_displaced_inode_during_fsync_is_preserved(tmp_path, monkeypatch):
    async def main():
        path = tmp_path / "work.nb.md"
        path.write_text("Original\n")
        server = NotebookServer(path)
        await server.start()
        external_fd = os.open(path, os.O_WRONLY)
        real_sync = storage.sync_directory
        raced = False

        def sync(directory):
            nonlocal raced
            if not raced:
                raced = True
                os.ftruncate(external_fd, 0)
                os.write(external_fd, b"Late external write\n")
                os.fsync(external_fd)
            real_sync(directory)

        monkeypatch.setattr(storage, "sync_directory", sync)
        try:
            async with aiohttp.ClientSession() as http:
                ws = await connect(http, server)
                snapshot = await ws.receive_json()
                await edit(ws, server, snapshot["cells"][0]["id"], "Local")
                await ws.send_json({"type": "retry_save"})
                state = await save_status(ws, "conflict")
                assert Path(state["conflict"]["preserved"]).read_text() == "Late external write\n"
                assert parse(path.read_text()) == [Cell("markdown", "Local")]
        finally:
            os.close(external_fd)
            await close_conflicted(server)
    asyncio.run(main())


def test_removed_lock_is_not_silently_reacquired_and_overwritten(tmp_path):
    async def main():
        path = tmp_path / "work.nb.md"
        path.write_text("Original\n")
        server = NotebookServer(path)
        await server.start()
        try:
            server.file.lock_path.unlink()
            async with aiohttp.ClientSession() as http:
                ws = await connect(http, server)
                snapshot = await ws.receive_json()
                await edit(ws, server, snapshot["cells"][0]["id"], "Local")
                await ws.send_json({"type": "retry_save"})
                assert "bloqueio" in (await save_status(ws, "conflict"))["conflict"]["message"]
                assert path.read_text() == "Original\n"
        finally:
            await close_conflicted(server)
    asyncio.run(main())


async def conflict_session(tmp_path, http):
    path = tmp_path / "work.nb.md"
    path.write_text("```python\nvalue = 1\n```\n")
    server = NotebookServer(path)
    await server.start()
    ws = await connect(http, server)
    snapshot = await ws.receive_json()
    marker = tmp_path / "must-not-run"
    path.write_text(f"```python\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\nvalue = 3\n```\n")
    await edit(ws, server, snapshot["cells"][0]["id"], "value = 2")
    await ws.send_json({"type": "retry_save"})
    await save_status(ws, "conflict")
    return server, ws, snapshot, path, marker


async def preserve(ws, server):
    await ws.send_json({"type": "preserve_copy", "revision": server._revision, "session": server._epoch})
    return (await receive(ws, lambda m: m["type"] == "save_status" and m["save"].get("copy")))["save"]["copy"]


async def request_reload(ws, server):
    await ws.send_json({"type": "reload_external", "revision": server._revision, "session": server._epoch})
    return (await receive(ws, lambda m: m["type"] == "save_status" and m["save"].get("reload")))["save"]["reload"]


def test_preserve_then_reload_requires_all_tabs_and_never_runs_external_code(tmp_path):
    async def main():
        async with aiohttp.ClientSession() as http:
            server, ws, before, path, marker = await conflict_session(tmp_path, http)
            try:
                saved = await preserve(ws, server)
                assert parse(Path(saved["path"]).read_text()) == [Cell("python", "value = 2")]
                other = await connect(http, server)
                await other.receive_json()
                request = await request_reload(ws, server)
                await ws.send_json({"type": "reload_ready", "request": request, "ready": True})
                await other.send_json({"type": "reload_ready", "request": request, "ready": False})
                assert "cancelado" in (await receive(ws, lambda m: m["type"] == "notice"))["text"]
                await receive(ws, lambda m: m["type"] == "save_status" and not m["save"].get("reload"))
                assert server.session.to_file_cells() == [Cell("python", "value = 2")]
                request = await request_reload(ws, server)
                for client in (ws, other):
                    await client.send_json({"type": "reload_ready", "request": request, "ready": True})
                after = await receive(ws, lambda m: m["type"] == "snapshot")
                assert after["document"]["session"] != before["document"]["session"]
                assert after["save"]["status"] == "saved"
                await server.session.idle()
                assert not marker.exists()
                assert after["cells"][0]["output"] == ""
                # A command queued by an old/offline tab cannot target a reused numeric ID.
                await ws.send_json({"type": "run", "cid": after["cells"][0]["id"],
                                    "session": before["document"]["session"]})
                assert "ação antiga" in (await receive(ws, lambda m: m["type"] == "error"))["error"]
                assert not marker.exists()
                assert parse(Path(saved["path"]).read_text()) == [Cell("python", "value = 2")]
            finally:
                await server.close()
    asyncio.run(main())


@pytest.mark.parametrize("problem", ["new_edit", "copy_removed", "invalid_external", "disconnected_tab"])
def test_resolution_refuses_stale_or_missing_copies_and_keeps_the_current_session(tmp_path, problem):
    async def main():
        async with aiohttp.ClientSession() as http:
            server, ws, before, path, marker = await conflict_session(tmp_path, http)
            try:
                saved = await preserve(ws, server)
                if problem == "new_edit":
                    await edit(ws, server, before["cells"][0]["id"], "value = 4")
                    await ws.send_json({"type": "reload_external", "revision": server._revision, "session": server._epoch})
                    assert "cópia atual" in (await receive(ws, lambda m: m["type"] == "error"))["error"]
                else:
                    if problem == "copy_removed":
                        Path(saved["path"]).unlink()
                    if problem == "invalid_external":
                        path.write_text("<!-- notebook-format: 2 -->\nbad")
                    other = await connect(http, server) if problem == "disconnected_tab" else None
                    if other:
                        await other.receive_json()
                    request = await request_reload(ws, server)
                    await ws.send_json({"type": "reload_ready", "request": request, "ready": True})
                    if other:
                        await other.close()
                    await receive(ws, lambda m: m["type"] == "notice")
                assert server._epoch == before["document"]["session"]
                assert server._save_state()["status"] == "conflict"
                assert not marker.exists()
            finally:
                await close_conflicted(server)
    asyncio.run(main())


def test_copy_changed_while_replacement_kernel_starts_keeps_original_session(tmp_path, monkeypatch):
    async def main():
        async with aiohttp.ClientSession() as http:
            server, ws, before, path, marker = await conflict_session(tmp_path, http)
            try:
                saved = await preserve(ws, server)
                old_session = server.session
                real_start = Session.start

                async def start(replacement):
                    await real_start(replacement)
                    Path(saved["path"]).write_text("Changed externally\n")

                monkeypatch.setattr(Session, "start", start)
                request = await request_reload(ws, server)
                await ws.send_json({"type": "reload_ready", "request": request, "ready": True})
                assert "mudou durante" in (await receive(ws, lambda m: m["type"] == "notice"))["text"]
                assert server.session is old_session
                assert server.session._kernel.proc.returncode is None
                assert server._epoch == before["document"]["session"]
                assert server._copy is None
                assert not marker.exists()
            finally:
                await close_conflicted(server)
    asyncio.run(main())


def test_buffered_edit_between_reload_dispatch_and_task_start_invalidates_old_copy(tmp_path):
    async def main():
        async with aiohttp.ClientSession() as http:
            server, ws, before, path, marker = await conflict_session(tmp_path, http)
            try:
                saved = await preserve(ws, server)
                server_ws = next(iter(server.clients))
                cid = before["cells"][0]["id"]
                # Deterministically model two already-buffered messages dispatched
                # before the reader yields to the newly scheduled resolution task.
                server._handle(server_ws, {"type": "reload_external", "revision": server._revision, "session": server._epoch})
                server._handle(server_ws, {"type": "edit", "cid": cid, "code": "value = 4",
                                           "base_version": server._cell_json(cid)["version"]})
                assert "mudou antes" in (await receive(ws, lambda m: m["type"] == "notice"))["text"]
                assert server.session.to_file_cells() == [Cell("python", "value = 4")]
                assert parse(Path(saved["path"]).read_text()) == [Cell("python", "value = 2")]
                assert server._epoch == before["document"]["session"]
                assert not marker.exists()
            finally:
                await close_conflicted(server)
    asyncio.run(main())
