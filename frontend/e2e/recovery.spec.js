import { test as base, expect, chromium } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { editorLines, editorOf, observeSockets, readSavedCells, regions, replaceCode, saveStatus, startServer } from "./helpers.js";

const recovered = (marker) => `from pathlib import Path as _Path\n_Path(${JSON.stringify(marker)}).write_text('executed')\nvalue = 2`;
const offer = (page) => page.getByRole("region", { name: "rascunhos recuperáveis" });
const noExecution = async (marker) => expect(await readFile(marker, "utf8").catch(() => null)).toBeNull();
const identified = (cells) => "<!-- notebook-format: 2 -->\n\n" + cells.map((c) =>
  `<!-- notebook-cell: ${c.uid} -->\n\`\`\`python\n${c.code}\n\`\`\``).join("\n\n") + "\n";
const test = base.extend({
  recoveryNotebook: async ({}, use, testInfo) => {
    const directory = await mkdtemp(join(tmpdir(), "notebook-recovery-"));
    const path = join(directory, "work.nb.md"), profile = join(directory, "browser");
    const marker = join(directory, "executed.txt"), servers = [], contexts = [], errors = [];
    let port = 0;
    try {
      await use({ path, marker,
        async server() {
          const server = startServer(path, { port });
          servers.push(server);
          const url = await server.url;
          port = Number(new URL(url).port); // recovery storage stays on the same origin; token changes
          return { ...server, url };
        },
        async browser(url, { storageUnavailable = false } = {}) {
          const context = await chromium.launchPersistentContext(profile, { headless: true, viewport: { width: 1280, height: 900 } });
          contexts.push(context);
          if (storageUnavailable) await context.addInitScript(() => {
            Object.defineProperty(window, "indexedDB", { value: {
              open() { throw new DOMException("Storage blocked by browser policy", "SecurityError"); },
            } });
          });
          const page = context.pages()[0];
          page.on("pageerror", (error) => errors.push(error.message));
          const connections = observeSockets(page);
          await page.goto(url);
          await expect(saveStatus(page)).toHaveText("salvo");
          return { context, page, connections };
        },
      });
      expect(errors).toEqual([]);
    } finally {
      await chmod(directory, 0o700);
      for (const context of contexts) await context.close().catch(() => {});
      for (const server of servers) await server.stop();
      if (testInfo.status !== testInfo.expectedStatus) {
        await testInfo.attach("server-log", { body: servers.map((s) => s.log()).join("\n"), contentType: "text/plain" });
      }
      await rm(directory, { recursive: true, force: true });
    }
  },
});

test("offline typing survives server SIGKILL and a whole browser restart without replaying Shift+Enter", async ({ recoveryNotebook: n }) => {
  await writeFile(n.path, "```python\nvalue = 1\n```\n");
  const first = await n.server();
  const { context, page } = await n.browser(first.url);
  await first.kill();
  await expect(saveStatus(page)).toHaveText("salvamento não confirmado");
  const code = recovered(n.marker);
  await replaceCode(editorOf(regions(page).first()), code);
  await editorOf(regions(page).first()).press("Shift+Enter");
  await expect(page.getByRole("status", { name: "recuperação local", exact: true })).toHaveText("rascunho protegido neste navegador");
  await context.close(); // close the actual Chromium process, preserving only its disk profile
  expect(readSavedCells(n.path)[0].code).toBe("value = 1");
  const second = await n.server();
  expect(second.url).not.toBe(first.url); // new authentication token
  const reopened = await n.browser(second.url);
  await expect(offer(reopened.page)).toBeVisible();
  await expect.poll(() => editorLines(editorOf(regions(reopened.page).first()))).toEqual(["value = 1"]);
  await noExecution(n.marker);
  await offer(reopened.page).getByRole("button", { name: "recuperar rascunho", exact: true }).click();
  await expect(offer(reopened.page)).toHaveCount(0);
  await expect(saveStatus(reopened.page)).toHaveText("salvo");
  expect(readSavedCells(n.path)[0].code).toBe(code);
  await noExecution(n.marker);
  expect(reopened.connections.flatMap((c) => c.sent).filter((m) => m.type === "run" || m.type === "run_all")).toEqual([]);
  await reopened.page.reload();
  await expect(saveStatus(reopened.page)).toHaveText("salvo");
  await expect(offer(reopened.page)).toHaveCount(0);
});

for (const choice of ["usar rascunho", "manter versão do servidor"]) {
  test(`an accepted but unsaved edit survives a browser crash and external reordering: ${choice}`, async ({ recoveryNotebook: n }) => {
    const cells = [{ uid: randomUUID().replaceAll("-", ""), code: "value = 1" },
      { uid: randomUUID().replaceAll("-", ""), code: "other = 99" }];
    await writeFile(n.path, identified(cells));
    const first = await n.server();
    const { context, page } = await n.browser(first.url);
    // Make the real atomic rename impossible; acceptance must not clear the draft.
    const directory = join(n.path, "..");
    await chmod(directory, 0o500);
    await replaceCode(editorOf(regions(page).first()), recovered(n.marker));
    await expect(saveStatus(page)).toHaveText("falha ao salvar");
    await expect(page.getByRole("status", { name: "recuperação local", exact: true })).toHaveText("rascunho protegido neste navegador");
    const browser = context.browser();
    const disconnected = new Promise((resolve) => browser.once("disconnected", resolve));
    const cdp = await browser.newBrowserCDPSession();
    const { processInfo } = await cdp.send("SystemInfo.getProcessInfo");
    const ownedBrowser = processInfo.find((p) => p.type === "browser");
    expect(ownedBrowser.id).toBeGreaterThan(0);
    process.kill(ownedBrowser.id, "SIGKILL"); // only the dedicated Chromium launched by this fixture
    await disconnected;
    await first.kill();
    await chmod(directory, 0o700);
    cells[0].code = "value = 3";
    await writeFile(n.path, identified([cells[1], cells[0]]));
    const second = await n.server();
    const reopened = await n.browser(second.url);
    await expect(offer(reopened.page)).toContainText("Conflito:");
    await expect(offer(reopened.page).getByRole("textbox", { name: "código atual no servidor" })).toHaveValue("value = 3");
    await noExecution(n.marker);
    await reopened.page.screenshot({ path: test.info().outputPath("recovery-conflict.png"), fullPage: true });
    await offer(reopened.page).getByRole("button", { name: choice, exact: true }).click();
    await expect(offer(reopened.page)).toHaveCount(0);
    await expect(saveStatus(reopened.page)).toHaveText("salvo");
    const expected = choice === "usar rascunho" ? recovered(n.marker) : "value = 3";
    expect(readSavedCells(n.path).map((c) => c.code)).toEqual(["other = 99", expected]);
    await expect.poll(() => editorLines(editorOf(regions(reopened.page).nth(1)))).toEqual(expected.split("\n"));
    await noExecution(n.marker);
    expect(reopened.connections.flatMap((c) => c.sent).filter((m) => m.type === "run" || m.type === "run_all")).toEqual([]);
  });
}

test("a removed cell's draft can be recovered as a new cell without running it", async ({ recoveryNotebook: n }) => {
  await writeFile(n.path, "```python\nvalue = 1\n```\n");
  const first = await n.server();
  const { context, page } = await n.browser(first.url);
  await first.kill();
  await expect(saveStatus(page)).toHaveText("salvamento não confirmado");
  await replaceCode(editorOf(regions(page).first()), recovered(n.marker));
  await expect(page.getByRole("status", { name: "recuperação local", exact: true })).toHaveText("rascunho protegido neste navegador");
  await context.close();
  await writeFile(n.path, identified([{ uid: randomUUID().replaceAll("-", ""), code: "other = 99" }]));
  const second = await n.server();
  const reopened = await n.browser(second.url);
  await expect(offer(reopened.page)).toContainText("Célula não encontrada");
  await offer(reopened.page).getByRole("button", { name: "recuperar em nova célula", exact: true }).click();
  await expect(offer(reopened.page)).toHaveCount(0);
  await expect(regions(reopened.page)).toHaveCount(2);
  await expect(saveStatus(reopened.page)).toHaveText("salvo");
  expect(readSavedCells(n.path).map((c) => c.code)).toEqual(["other = 99", recovered(n.marker)]);
  await noExecution(n.marker);
});

test("unavailable browser storage warns without preventing edits from reaching the notebook file", async ({ recoveryNotebook: n }) => {
  await writeFile(n.path, "```python\nvalue = 1\n```\n");
  const server = await n.server();
  const { page } = await n.browser(server.url, { storageUnavailable: true });
  await expect(page.getByRole("alert")).toContainText("Não foi possível atualizar a cópia de recuperação");
  await replaceCode(editorOf(regions(page).first()), "value = 2");
  await expect(saveStatus(page)).toHaveText("salvo");
  expect(readSavedCells(n.path)[0].code).toBe("value = 2");
  await expect(page.getByRole("status", { name: "recuperação local", exact: true })).toHaveText("cópia local incompleta");
});
