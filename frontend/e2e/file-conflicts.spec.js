import { test as base, expect } from "@playwright/test";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { editorLines, editorOf, observeSockets, readSavedCells, regions, replaceCode, saveStatus, startServer } from "./helpers.js";

const panel = (page) => page.getByRole("region", { name: "conflito no arquivo", exact: true });
const test = base.extend({
  notebook: async ({ page }, use, testInfo) => {
    const directory = await mkdtemp(join(tmpdir(), "notebook-file-conflict-"));
    const path = join(directory, "work.nb.md"), marker = join(directory, "executed");
    await writeFile(path, "```python\nvalue = 1\n```\n");
    const server = startServer(path), errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const connections = observeSockets(page);
    try {
      const url = await server.url;
      await page.goto(url);
      await expect(saveStatus(page)).toHaveText("salvo");
      await use({ path, directory, marker, server, url, connections,
        external: () => writeFile(path, `\`\`\`python\nfrom pathlib import Path\nPath(${JSON.stringify(marker)}).touch()\nvalue = 3\n\`\`\`\n`),
      });
      expect(errors).toEqual([]);
    } finally {
      await chmod(directory, 0o700);
      await server.stop();
      if (testInfo.status !== testInfo.expectedStatus)
        await testInfo.attach("server-log", { body: server.log(), contentType: "text/plain" });
      await rm(directory, { recursive: true, force: true });
    }
  },
});

test("external conflict preserves both versions and explicitly reloads without executing", async ({ page, notebook: n }) => {
  await n.external();
  await replaceCode(editorOf(regions(page).first()), "value = 2");
  await expect(saveStatus(page)).toHaveText("conflito no arquivo");
  await expect(panel(page).getByRole("button", { name: "carregar versão externa" })).toBeDisabled();
  const external = await readFile(n.path, "utf8");
  await page.reload();
  await expect(saveStatus(page)).toHaveText("conflito no arquivo");
  await expect.poll(() => editorLines(editorOf(regions(page).first()))).toEqual(["value = 2"]);
  await panel(page).getByRole("button", { name: "preservar cópia da sessão" }).click();
  await expect(panel(page).getByRole("button", { name: "carregar versão externa" })).toBeEnabled();
  const copy = await panel(page).locator("code").last().textContent();
  expect(readSavedCells(copy)).toEqual([{ kind: "python", code: "value = 2" }]);
  expect(await readFile(n.path, "utf8")).toBe(external);
  await page.screenshot({ path: test.info().outputPath("file-conflict.png"), fullPage: true });
  await panel(page).getByRole("button", { name: "carregar versão externa" }).click();
  await expect(panel(page)).toHaveCount(0);
  await expect(saveStatus(page)).toHaveText("salvo");
  await expect.poll(() => editorLines(editorOf(regions(page).first()))).toEqual(readSavedCells(n.path)[0].code.split("\n"));
  expect(await readFile(n.marker, "utf8").catch(() => null)).toBeNull();
  expect(n.connections.flatMap((c) => c.sent).filter((m) => ["run", "run_all"].includes(m.type))).toEqual([]);
  // The reloaded session can subsequently save and run an explicit request.
  await replaceCode(editorOf(regions(page).first()), "value = 4\nprint(value)");
  await expect(saveStatus(page)).toHaveText("salvo");
  await editorOf(regions(page).first()).press("Shift+Enter");
  await expect(regions(page).first().locator(".stdout")).toHaveText("4\n");
  expect(readSavedCells(copy)[0].code).toBe("value = 2");
});

test("another tab's unresolved editor blocks reload and retains its text", async ({ page, context, notebook: n }) => {
  const other = await context.newPage();
  await other.goto(n.url);
  await expect(saveStatus(other)).toHaveText("salvo");
  await editorOf(regions(other).first()).click(); // focused old source must become an editor conflict
  await n.external();
  await replaceCode(editorOf(regions(page).first()), "value = 2");
  await expect(saveStatus(page)).toHaveText("conflito no arquivo");
  await expect(other.getByText("O código mudou no servidor. Sua versão foi mantida no editor.")).toBeVisible();
  await panel(page).getByRole("button", { name: "preservar cópia da sessão" }).click();
  await expect(panel(page).getByRole("button", { name: "carregar versão externa" })).toBeEnabled();
  await panel(page).getByRole("button", { name: "carregar versão externa" }).click();
  await expect(page.getByText(/Carregamento cancelado:/)).toBeVisible();
  await expect.poll(() => editorLines(editorOf(regions(other).first()))).toEqual(["value = 1"]);
  await expect(saveStatus(page)).toHaveText("conflito no arquivo");
  await other.getByRole("button", { name: "carregar a do servidor", exact: true }).click();
  await expect.poll(() => editorLines(editorOf(regions(other).first()))).toEqual(["value = 2"]);
  await panel(page).getByRole("button", { name: "carregar versão externa" }).click();
  await expect(panel(page)).toHaveCount(0);
  await expect(panel(other)).toHaveCount(0);
  expect(await readFile(n.marker, "utf8").catch(() => null)).toBeNull();
});

test("a failed preservation keeps reload blocked and a later edit invalidates an older copy", async ({ page, notebook: n }) => {
  await n.external();
  await replaceCode(editorOf(regions(page).first()), "value = 2");
  await expect(saveStatus(page)).toHaveText("conflito no arquivo");
  await chmod(n.directory, 0o500);
  await panel(page).getByRole("button", { name: "preservar cópia da sessão" }).click();
  await expect(page.getByText(/Permission denied/)).toBeVisible();
  await expect(panel(page).getByRole("button", { name: "carregar versão externa" })).toBeDisabled();
  await chmod(n.directory, 0o700);
  await panel(page).getByRole("button", { name: "preservar cópia da sessão" }).click();
  await expect(panel(page).getByRole("button", { name: "carregar versão externa" })).toBeEnabled();
  const copy = await panel(page).locator("code").last().textContent();
  await replaceCode(editorOf(regions(page).first()), "value = 4");
  await expect(panel(page)).toContainText("há edições mais recentes");
  await expect(panel(page).getByRole("button", { name: "carregar versão externa" })).toBeDisabled();
  expect(readSavedCells(copy)[0].code).toBe("value = 2");
});
