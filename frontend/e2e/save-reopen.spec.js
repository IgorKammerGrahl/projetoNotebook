import { test, expect } from "@playwright/test";
import { chmod, mkdtemp, writeFile, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { startServer, readSavedCells, regions, replaceMarkdown } from "./helpers.js";
const original = [
  { kind: "python", code: "number = 7" },
  { kind: "mojo", code: "def run(number: Int, mut doubled: Int):\n    doubled = number * 2" },
  { kind: "html", code: "<p>HTML preservado</p>" },
];

async function sourcesInUI(page) {
  const result = [];
  for (const region of await regions(page).all()) {
    const label = await region.getAttribute("aria-label");
    const [, kind] = label.split(", ");
    if (kind === "html") {
      const frame = region.getByTitle(/^html da célula /);
      await expect(frame).toBeVisible();
      result.push({ kind, code: await frame.getAttribute("srcdoc") });
      continue;
    }
    const editor = region.getByLabel(/^código da célula /);
    if (!await editor.count()) await region.locator(".rendered").dblclick();
    await expect(editor).toBeVisible();
    const code = (await editor.locator(".cm-line").allTextContents()).join("\n");
    result.push({ kind, code });
    if (kind === "markdown") await editor.press("Shift+Enter");
  }
  return result;
}

test("autosave preserves adjacent and empty Markdown across a server restart", async ({ page }, testInfo) => {
  const directory = await mkdtemp(join(tmpdir(), "notebook-e2e-"));
  const path = join(directory, "roundtrip.nb.md");
  const servers = [];
  const browserErrors = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  try {
    // Begin with an unversioned notebook to exercise backwards compatibility.
    await writeFile(path, original.map((c) => `\`\`\`${c.kind}\n${c.code}\n\`\`\``).join("\n\n") + "\n");
    const first = startServer(path);
    servers.push(first);
    await page.goto(await first.url);
    await expect(regions(page)).toHaveCount(original.length);

    const markdown = [
      "# Primeira\n\nTexto com **ênfase**.",
      "\nExemplo de código:\n```python\nraise RuntimeError('este exemplo não pode executar')\n```\n\n<!-- notebook:markdown === -->\n<!-- === -->\n",
      "", "",
    ];
    for (let i = 0; i < markdown.length; i++) {
      await regions(page).nth(i).getByRole("button", { name: "+ markdown", exact: true }).click();
      await expect(regions(page)).toHaveCount(original.length + i + 1);
      const editor = regions(page).nth(i + 1).getByLabel(/^código da célula /);
      await expect(editor).toBeVisible();
      if (markdown[i]) await replaceMarkdown(page, editor, markdown[i]);
      await editor.press("Shift+Enter");
    }

    // Reopen and change an existing Markdown cell through the actual UI.
    await regions(page).nth(1).locator(".rendered").dblclick();
    markdown[0] = "# Primeira editada\n\nTexto final salvo.";
    const editor = regions(page).nth(1).getByLabel(/^código da célula /);
    await replaceMarkdown(page, editor, markdown[0]);
    await editor.press("Shift+Enter");
    const expected = [original[0], ...markdown.map((code) => ({ kind: "markdown", code })), ...original.slice(1)];
    await expect(page.getByRole("button", { name: "rodar tudo", exact: true })).toBeEnabled();
    expect(await sourcesInUI(page)).toEqual(expected);

    // This check must precede stop(): shutdown flushes pending saves itself.
    await expect(page.getByRole("status", { name: "salvamento", exact: true })).toHaveText("salvo");
    expect(readSavedCells(path)).toEqual(expected);
    await expect.poll(() => readSavedCells(path), { timeout: 10_000 }).toEqual(expected);
    const autosaved = await readFile(path, "utf8");
    await page.goto("about:blank"); // prevent the old token from reconnecting during restart
    await first.stop();
    expect(await readFile(path, "utf8")).toBe(autosaved);

    const second = startServer(path);
    servers.push(second);
    await page.goto(await second.url);
    await expect(regions(page)).toHaveCount(expected.length);
    expect(await sourcesInUI(page)).toEqual(expected);
    await expect(regions(page).last().frameLocator("iframe").getByText("HTML preservado", { exact: true })).toBeVisible();
    await expect(regions(page).filter({ has: page.getByText("derrubou o kernel", { exact: false }) })).toHaveCount(0);
    expect(browserErrors).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath("reopened.png"), fullPage: true });
  } finally {
    await page.goto("about:blank").catch(() => {});
    for (const server of servers) await server.stop();
    if (testInfo.status !== testInfo.expectedStatus) {
      await testInfo.attach("server-log", { body: servers.map((s) => s.log()).join("\n--- restart ---\n"), contentType: "text/plain" });
    }
    await rm(directory, { recursive: true, force: true });
  }
});

test("a disk permission failure survives reconnect and can be retried without losing edits", async ({ page }, testInfo) => {
  const directory = await mkdtemp(join(tmpdir(), "notebook-save-error-"));
  const path = join(directory, "failure.nb.md");
  let server;
  try {
    await writeFile(path, "Original\n");
    server = startServer(path);
    await page.goto(await server.url);
    const status = page.getByRole("status", { name: "salvamento", exact: true });
    await expect(status).toHaveText("salvo");
    // The notebook can still be read, but atomic replacement needs directory write permission.
    await chmod(directory, 0o500);
    await regions(page).first().locator(".rendered").dblclick();
    const editor = regions(page).first().getByLabel(/^código da célula /);
    await replaceMarkdown(page, editor, "Alteração preservada");
    await editor.press("Shift+Enter");
    await expect(status).toHaveText("falha ao salvar");
    await expect(page.getByText(/Sem permissão para gravar o arquivo/)).toBeVisible();
    expect(await readFile(path, "utf8")).toBe("Original\n");

    // A fresh connection gets the failure and the unsaved source from the server snapshot.
    await page.reload();
    await expect(status).toHaveText("falha ao salvar");
    await expect(regions(page).first().locator(".rendered")).toHaveText("Alteração preservada");
    await page.screenshot({ path: testInfo.outputPath("save-error.png"), fullPage: true });
    await chmod(directory, 0o700);
    await page.getByRole("button", { name: "tentar salvar novamente", exact: true }).click();
    await expect(status).toHaveText("salvo");
    expect(readSavedCells(path)).toEqual([{ kind: "markdown", code: "Alteração preservada" }]);
    await expect(page.getByRole("button", { name: "tentar salvar novamente", exact: true })).toHaveCount(0);
  } finally {
    await chmod(directory, 0o700);
    await page.goto("about:blank").catch(() => {});
    if (server) {
      await server.stop();
      if (testInfo.status !== testInfo.expectedStatus) {
        await testInfo.attach("server-log", { body: server.log(), contentType: "text/plain" });
      }
    }
    await rm(directory, { recursive: true, force: true });
  }
});
