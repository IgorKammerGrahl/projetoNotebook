"""Stable recovery identity across real server lifetimes and external edits."""
import asyncio

import aiohttp

from kernel.fmt import Cell, Document, parse_document, serialize_document
from kernel.web import NotebookServer
from test_persistence import connect, edit, receive, save_status


def test_cell_identity_survives_save_restart_reorder_and_removal(tmp_path):
    async def main():
        path = tmp_path / "recovery.nb.md"
        path.write_text("```python\na = 1\n```\n\n```python\nb = 2\n```\n")
        first = NotebookServer(path)
        await first.start()
        async with aiohttp.ClientSession() as http:
            try:
                ws = await connect(http, first)
                before = await ws.receive_json()
                a, b = before["cells"]
                await edit(ws, first, b["id"], "b = 3")
                await save_status(ws, "saved")
                document = parse_document(path.read_text())
                assert document.cell_ids == [a["uid"], b["uid"]]
            finally:
                await first.close()
            # An editor outside the server reorders cells and edits their source.
            path.write_text(serialize_document(Document([Cell("python", "b = 4"), document.cells[0]],
                                                        list(reversed(document.cell_ids)))))
            second = NotebookServer(path)
            await second.start()
            try:
                ws = await connect(http, second)
                after = await ws.receive_json()
                assert before["document"]["id"] == after["document"]["id"]
                assert before["document"]["session"] != after["document"]["session"]
                assert [c["uid"] for c in after["cells"]] == [b["uid"], a["uid"]]
                assert after["cells"][0]["id"] != b["id"]  # positional IDs really changed
                # Old version checks remain strict despite the stable identity.
                await ws.send_json({"type": "edit", "cid": after["cells"][0]["id"],
                                    "code": "b = 99", "base_version": b["version"]})
                conflict = await receive(ws, lambda m: m["type"] == "conflict")
                assert conflict["cell"]["code"] == "b = 4"
                await ws.send_json({"type": "delete", "cid": after["cells"][1]["id"]})
                await save_status(ws, "saved")
                assert parse_document(path.read_text()).cell_ids == [b["uid"]]
            finally:
                await second.close()
            # Copies at another path cannot pick up drafts for this notebook.
            copied = tmp_path / "copy.nb.md"
            copied.write_text(path.read_text())
            third = NotebookServer(copied)
            assert third._document_id != first._document_id
    asyncio.run(main())


def test_unchanged_legacy_file_keeps_identity_without_needing_a_write(tmp_path):
    async def main():
        path = tmp_path / "legacy.nb.md"
        original = "Original\n"
        path.write_text(original)
        identities = []
        async with aiohttp.ClientSession() as http:
            for _ in range(2):
                server = NotebookServer(path)
                await server.start()
                try:
                    ws = await connect(http, server)
                    snapshot = await ws.receive_json()
                    identities.append((snapshot["document"]["id"], snapshot["cells"][0]["uid"]))
                finally:
                    await server.close()
        assert identities[0] == identities[1]
        assert path.read_text() == original
    asyncio.run(main())
