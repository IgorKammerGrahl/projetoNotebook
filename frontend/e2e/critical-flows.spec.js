import { test as base, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { controllableNetwork, editorLines, editorOf, observeAnnouncements, observeSockets,
  readSavedCells, regions, replaceCode, saveStatus, startServer } from "./helpers.js";

const python = (code) => ({ kind: "python", code });
const mojo = (code) => ({ kind: "mojo", code });
const execute = (cell) => cell.getByRole("button", { name: "executar célula", exact: true }).click();
const runAll = (page) => page.getByRole("button", { name: "rodar tudo", exact: true });
const preview = (cell, value) => expect(cell.locator(".previews dd")).toHaveText(new RegExp(`\\b${value}$`));

const test = base.extend({
  notebook: async ({ context }, use, testInfo) => {
    const directory = await mkdtemp(join(tmpdir(), "notebook-flows-"));
    const servers = [], errors = [];
    const watch = (page) => page.on("pageerror", (error) => errors.push(error.message));
    context.pages().forEach(watch);
    context.on("page", watch);
    try {
      await use({
        directory,
        async start(cells, options) {
          const path = join(directory, `notebook-${servers.length}.nb.md`);
          await writeFile(path, cells.map((c) => `\`\`\`${c.kind}\n${c.code}\n\`\`\``).join("\n\n") + "\n");
          const server = startServer(path, options);
          servers.push(server);
          return { path, url: await server.url };
        },
      });
      expect(errors).toEqual([]);
    } finally {
      for (const page of context.pages()) await page.goto("about:blank").catch(() => {});
      for (const server of servers) await server.stop();
      if (testInfo.status !== testInfo.expectedStatus) {
        await testInfo.attach("server-log", { body: servers.map((s) => s.log()).join("\n"), contentType: "text/plain" });
      }
      await rm(directory, { recursive: true, force: true });
    }
  },
});

test("stop interrupts an infinite loop on the same connection and the notebook remains usable", async ({ page, notebook }) => {
  const connections = observeSockets(page);
  const { path, url } = await notebook.start([python("value = 1"), python("answer = 10")]);
  await page.goto(url);
  const loop = regions(page).nth(0), independent = regions(page).nth(1);
  await preview(independent, 10);
  const announcements = await observeAnnouncements(page);
  const code = "import time as _time\nwhile True:\n    _time.sleep(0.01)";
  await replaceCode(editorOf(loop), code);
  await editorOf(loop).press("Shift+Enter");
  await expect(loop.locator(".chip").filter({ hasText: /^\s*executando\b/ })).toBeVisible();
  await page.getByRole("button", { name: "parar", exact: true }).click();
  await expect(loop.getByText("interrompida por você", { exact: true })).toBeVisible();
  await expect(page.getByText("reinícios do kernel: 1", { exact: true })).toBeVisible();
  await expect(independent.locator(".cell-head .chip")).toHaveCount(0);
  await preview(independent, 10);
  await expect.poll(() => editorLines(editorOf(loop))).toEqual(code.split("\n"));
  expect((await announcements()).filter((m) => m.includes("Execução interrompida"))).toHaveLength(1);

  await replaceCode(editorOf(independent), "answer = 42");
  await execute(independent);
  await preview(independent, 42);
  await replaceCode(editorOf(loop), "value = 2");
  await editorOf(loop).press("Shift+Enter");
  await preview(loop, 2);
  await expect(saveStatus(page)).toHaveText("salvo");
  expect(readSavedCells(path)).toEqual([python("value = 2"), python("answer = 42")]);
  expect(connections).toHaveLength(1);
  const sent = connections[0].sent;
  const start = sent.findIndex((m) => m.type === "run" && m.cid === 1);
  expect(start).toBeGreaterThanOrEqual(0);
  expect(sent.findIndex((m) => m.type === "stop")).toBeGreaterThan(start);
});

test("another cell can be edited and run during a promoted Mojo build on the same connection", async ({ page, notebook }) => {
  const realMojo = execFileSync("python", ["-c", "import shutil; print(shutil.which('mojo') or '')"], { encoding: "utf8" }).trim();
  expect(realMojo).not.toBe("");
  const bin = join(notebook.directory, "bin");
  const gate = join(notebook.directory, "hold-build");
  const entered = join(notebook.directory, "build-entered");
  const marker = `browser-build-${randomUUID()}`;
  await mkdir(bin);
  const wrapper = join(bin, "mojo");
  await writeFile(wrapper, `#!/usr/bin/env python
import os, sys, time
from pathlib import Path
if len(sys.argv) > 1 and sys.argv[1] == "build":
    src = next(Path(a) for a in sys.argv[2:] if a.endswith(".mojo"))
    if os.environ["NOTEBOOK_E2E_BUILD_MARKER"] in src.read_text():
        with Path(os.environ["NOTEBOOK_E2E_BUILD_ENTERED"]).open("a") as f:
            f.write(str(os.getpid()) + "\\n")
        while Path(os.environ["NOTEBOOK_E2E_BUILD_GATE"]).exists():
            time.sleep(0.01)
real = os.environ["NOTEBOOK_E2E_REAL_MOJO"]
os.execv(real, [real, *sys.argv[1:]])
`);
  await chmod(wrapper, 0o700);
  const initial = "def run(mut result: Int):\n    result = 7";
  const connections = observeSockets(page);
  const { path, url } = await notebook.start([python("answer = 1"), mojo(initial)], { env: {
    PATH: `${bin}:${process.env.PATH}`, NOTEBOOK_E2E_REAL_MOJO: realMojo,
    NOTEBOOK_E2E_BUILD_MARKER: marker, NOTEBOOK_E2E_BUILD_GATE: gate, NOTEBOOK_E2E_BUILD_ENTERED: entered,
  } });
  try {
    await page.goto(url);
    const independent = regions(page).nth(0), compiled = regions(page).nth(1);
    await preview(compiled, 7);
    await writeFile(gate, "hold");
    const code = `# ${marker}\ndef run(mut result: Int):\n    result = 21`;
    await replaceCode(editorOf(compiled), code);
    await expect(compiled.getByText("build em 2º plano", { exact: true })).toBeVisible();
    await expect.poll(() => readFile(entered, "utf8").catch(() => "")).not.toBe("");
    const processId = await readFile(entered, "utf8");
    await execute(compiled); // promotion must keep the in-flight build
    await expect(compiled.locator(".chip").filter({ hasText: /^\s*compilando\b/ })).toBeVisible();
    await replaceCode(editorOf(independent), "answer = 42");
    await editorOf(independent).press("Shift+Enter");
    await preview(independent, 42);
    expect(await readFile(gate, "utf8")).toBe("hold");
    expect(await readFile(entered, "utf8")).toBe(processId);
    expect(processId.trim().split("\n")).toHaveLength(1);
    await expect(compiled.locator(".chip").filter({ hasText: /^\s*compilando\b/ })).toBeVisible();
    await rm(gate);
    await preview(compiled, 21); // release actually invokes the real compiler
    await expect(saveStatus(page)).toHaveText("salvo");
    expect(readSavedCells(path)).toEqual([python("answer = 42"), mojo(code)]);
    expect(connections).toHaveLength(1);
    expect(connections[0].sent.filter((m) => m.type === "run").map((m) => m.cid)).toEqual([2, 1]);
  } finally {
    await rm(gate, { force: true });
  }
});

test("reconnect preserves offline edits but discards queued executions older than five seconds", async ({ page, notebook }) => {
  await page.clock.install();
  const network = await controllableNetwork(page);
  const executions = join(notebook.directory, "executions.txt");
  const counter = `from pathlib import Path as _Path\n_log = _Path(${JSON.stringify(executions)})\n_log.write_text((_log.read_text() if _log.exists() else '') + 'run\\n')\ncount = len(_log.read_text().splitlines())`;
  const { path, url } = await notebook.start([python(counter), python("value = 1")]);
  try {
    await page.goto(url);
    const action = regions(page).nth(0), edited = regions(page).nth(1);
    await preview(action, 1);
    await preview(edited, 1);
    await network.disconnect();
    await expect(saveStatus(page)).toHaveText("salvamento não confirmado");
    await execute(action);
    await runAll(page).click();
    await replaceCode(editorOf(edited), "value = 2");
    await editorOf(edited).press("Shift+Enter"); // also expires while waiting for the edit ACK
    await expect(runAll(page)).toBeDisabled();
    await page.clock.fastForward(6000);
    expect(await readFile(executions, "utf8")).toBe("run\n");
    expect(readSavedCells(path)[1]).toEqual(python("value = 1"));
    network.reconnect();
    await expect(saveStatus(page)).toHaveText("salvo");
    await expect(page.locator(".connection-notice")).toContainText(/descartada.*mais de 5 s/);
    await expect.poll(() => editorLines(editorOf(edited))).toEqual(["value = 2"]);
    expect(readSavedCells(path)[1]).toEqual(python("value = 2"));
    expect(network.connections.flatMap((c) => c.sent).filter((m) => m.type === "run" || m.type === "run_all")).toEqual([]);
    expect(await readFile(executions, "utf8")).toBe("run\n");
    await preview(edited, 1); // an edit does not implicitly execute the cell
    await execute(action);
    await preview(action, 2);
    expect(await readFile(executions, "utf8")).toBe("run\nrun\n");
  } finally {
    network.reconnect();
  }
});

for (const choice of ["manter a minha versão", "carregar a do servidor"]) {
  test(`two tabs show focused-editor conflicts and honor '${choice}'`, async ({ page, context, notebook }) => {
    const connections = observeSockets(page);
    const { path, url } = await notebook.start([python("value = 1")]);
    await page.goto(url);
    const cell = regions(page).first(), editor = editorOf(cell);
    await preview(cell, 1);
    await editor.click();
    await expect(editor).toBeFocused();
    const other = await context.newPage();
    await other.goto(url);
    await replaceCode(editorOf(regions(other).first()), "value = 2");
    await other.getByText("Notebook", { exact: true }).click();
    await expect(saveStatus(other)).toHaveText("salvo");
    await expect(cell.getByText("O código mudou no servidor. Sua versão foi mantida no editor.")).toBeVisible();
    await expect.poll(() => editorLines(editor)).toEqual(["value = 1"]);
    await expect(saveStatus(page)).toHaveText("alterações pendentes");
    await expect(runAll(page)).toBeDisabled();
    expect(readSavedCells(path)).toEqual([python("value = 2")]);
    await editor.press("Shift+Enter");
    await expect(page.locator(".connection-notice")).toContainText("Resolva o conflito");
    expect(connections.flatMap((c) => c.sent).filter((m) => m.type === "run")).toEqual([]);
    await cell.getByRole("button", { name: choice, exact: true }).click();
    const expected = choice === "manter a minha versão" ? "value = 1" : "value = 2";
    await expect(saveStatus(page)).toHaveText("salvo");
    await expect(cell.locator(".edit-conflict")).toHaveCount(0);
    await expect.poll(() => editorLines(editor)).toEqual([expected]);
    await expect.poll(() => editorLines(editorOf(regions(other).first()))).toEqual([expected]);
    expect(readSavedCells(path)).toEqual([python(expected)]);
    expect(connections.flatMap((c) => c.sent).filter((m) => m.type === "edit"))
      .toHaveLength(choice === "manter a minha versão" ? 1 : 0);
  });
}

test("an offline edit with an obsolete base becomes a conflict without running on reconnect", async ({ page, context, notebook }) => {
  const network = await controllableNetwork(page);
  const { path, url } = await notebook.start([python("value = 1")]);
  try {
    await page.goto(url);
    const cell = regions(page).first(), editor = editorOf(cell);
    await preview(cell, 1);
    const other = await context.newPage();
    await other.goto(url);
    await network.disconnect();
    await expect(saveStatus(page)).toHaveText("salvamento não confirmado");
    await replaceCode(editor, "value = 3");
    await editor.press("Shift+Enter");
    const remote = regions(other).first();
    await replaceCode(editorOf(remote), "value = 2");
    await editorOf(remote).press("Shift+Enter");
    await preview(remote, 2);
    await other.getByText("Notebook", { exact: true }).click();
    await expect(saveStatus(other)).toHaveText("salvo");
    network.reconnect();
    await expect(cell.locator(".edit-conflict")).toBeVisible();
    await expect.poll(() => editorLines(editor)).toEqual(["value = 3"]);
    await expect(saveStatus(page)).toHaveText("alterações pendentes");
    await expect.poll(() => network.connections.flatMap((c) => c.received).some((m) => m.type === "conflict")).toBe(true);
    expect(readSavedCells(path)).toEqual([python("value = 2")]);
    expect(network.connections.flatMap((c) => c.sent).filter((m) => m.type === "run")).toEqual([]);
    await cell.getByRole("button", { name: "manter a minha versão", exact: true }).click();
    await expect(saveStatus(page)).toHaveText("salvo");
    expect(readSavedCells(path)).toEqual([python("value = 3")]);
    await preview(cell, 2); // resolving the conflict must not replay the suspended run
    expect(network.connections.flatMap((c) => c.sent).filter((m) => m.type === "run")).toEqual([]);
    await execute(cell);
    await preview(cell, 3);
  } finally {
    network.reconnect();
  }
});

test("a real Mojo crash during run-all recovers survivors and announces the incident once", async ({ page, notebook }) => {
  const network = await controllableNetwork(page);
  const good = "def run(a: Int, mut c: Int) raises:\n    c = a * 10";
  const crash = "def run(a: Int, mut c: Int) raises:\n    Pointer[Int, MutAnyOrigin](unsafe_from_address=8).unsafe_store(a)";
  const initial = [python("a = 21"), mojo(good), python("d = c + 1"), python("e = 99")];
  const { path, url } = await notebook.start(initial);
  try {
    await page.goto(url);
    const root = regions(page).nth(0), culprit = regions(page).nth(1);
    const dependent = regions(page).nth(2), independent = regions(page).nth(3);
    await preview(dependent, 211);
    await preview(independent, 99);
    const announcements = await observeAnnouncements(page);
    await replaceCode(editorOf(culprit), crash);
    await expect(saveStatus(page)).toHaveText("salvo");
    await runAll(page).click();
    await expect(culprit.getByText("derrubou o kernel · quarentena", { exact: true })).toBeVisible();
    await expect(culprit.locator(".error-panel")).toContainText("SIGSEGV");
    await expect(dependent.locator(".chip").filter({ hasText: /bloqueada por/ })).toBeVisible();
    await expect(dependent.locator(".previews")).toHaveCount(0);
    await expect(root.locator(".cell-head .chip")).toHaveCount(0);
    await expect(independent.locator(".cell-head .chip")).toHaveCount(0);
    await preview(root, 21);
    await preview(independent, 99);
    await expect(page.getByText("reinícios do kernel: 1", { exact: true })).toBeVisible();
    await expect.poll(() => editorLines(editorOf(culprit))).toEqual(crash.split("\n"));
    expect(readSavedCells(path)).toEqual([initial[0], mojo(crash), ...initial.slice(2)]);
    expect((await announcements()).filter((m) => m.includes("sofreu um crash"))).toHaveLength(1);
    await network.disconnect();
    await expect(saveStatus(page)).toHaveText("salvamento não confirmado");
    network.reconnect();
    await expect(saveStatus(page)).toHaveText("salvo");
    expect((await announcements()).filter((m) => m.includes("sofreu um crash"))).toHaveLength(1);
    await page.screenshot({ path: test.info().outputPath("crash-recovered.png"), fullPage: true });

    await replaceCode(editorOf(culprit), good);
    await editorOf(culprit).press("Shift+Enter");
    await preview(culprit, 210);
    await preview(dependent, 211);
    await expect(culprit.getByText("derrubou o kernel · quarentena", { exact: true })).toHaveCount(0);
    await expect(page.getByText("reinícios do kernel: 1", { exact: true })).toBeVisible();
    await expect(saveStatus(page)).toHaveText("salvo");
    expect(readSavedCells(path)).toEqual(initial);
  } finally {
    network.reconnect();
  }
});
