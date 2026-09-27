import { expect } from "@playwright/test";
import { spawn, execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));

export function startServer(path, { env = {} } = {}) {
  const child = spawn("python", ["-m", "kernel", "serve", path, "--port", "0"], {
    cwd: root, env: { ...process.env, ...env }, detached: true, stdio: ["ignore", "pipe", "pipe"],
  });
  let log = "";
  const exited = new Promise((resolve) => child.once("close", resolve));
  const url = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Server did not start:\n${log}`)), 15_000);
    child.once("error", (error) => { clearTimeout(timer); reject(error); });
    child.once("exit", (code) => { clearTimeout(timer); reject(new Error(`Server exited (${code}):\n${log}`)); });
    child.stderr.on("data", (chunk) => { log += chunk; });
    child.stdout.on("data", (chunk) => {
      log += chunk;
      const match = log.match(/notebook: (http:\/\/127\.0\.0\.1:\d+\/\?token=\S+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    });
  });
  return {
    url,
    log: () => log,
    async stop() {
      if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
      child.kill("SIGINT");
      const timer = setTimeout(() => {
        try { process.kill(-child.pid, "SIGKILL"); } catch (error) { if (error.code !== "ESRCH") throw error; }
      }, 5000);
      try { await exited; } finally { clearTimeout(timer); }
    },
  };
}

export function readSavedCells(path) {
  const json = execFileSync("python", ["-c",
    "import json, sys; from pathlib import Path; from dataclasses import asdict; from kernel.fmt import parse; print(json.dumps([asdict(c) for c in parse(Path(sys.argv[1]).read_text())]))",
    path], { cwd: root, encoding: "utf8" });
  return JSON.parse(json);
}

export const regions = (page) => page.getByRole("region", { name: /^célula \d+, / });
export const editorOf = (region) => region.getByLabel(/^código da célula /);
export const saveStatus = (page) => page.getByRole("status", { name: "salvamento", exact: true });
export const editorLines = (editor) => editor.locator(".cm-line").allTextContents();

export async function replaceCode(editor, code) {
  await editor.fill(code);
  await expect.poll(() => editorLines(editor)).toEqual(code.split("\n"));
}

export async function replaceMarkdown(page, editor, code) {
  await editor.click();
  await editor.press("ControlOrMeta+A");
  await editor.press("Backspace");
  const lines = code.split("\n");
  for (let i = 0; i < lines.length; i++) {
    if (i) await editor.press("Enter");
    if (lines[i]) await page.keyboard.insertText(lines[i]);
  }
  await expect.poll(() => editorLines(editor)).toEqual(lines);
}

// Passive observation only: run/edit/stop are always triggered through the UI.
export function observeSockets(page) {
  const connections = [];
  page.on("websocket", (socket) => {
    const connection = { sent: [], received: [] };
    connections.push(connection);
    socket.on("framesent", ({ payload }) => connection.sent.push(JSON.parse(String(payload))));
    socket.on("framereceived", ({ payload }) => connection.received.push(JSON.parse(String(payload))));
  });
  return connections;
}

// Hold reconnect handshakes while offline; never invent application responses.
export async function controllableNetwork(page) {
  let offline = false;
  const waiting = new Set();
  const connections = [];
  await page.routeWebSocket(/\/ws\?/, async (client) => {
    if (offline) await new Promise((resolve) => waiting.add(resolve));
    const server = client.connectToServer();
    const connection = { client, server, sent: [], received: [] };
    connections.push(connection);
    client.onMessage((raw) => { connection.sent.push(JSON.parse(String(raw))); server.send(raw); });
    server.onMessage((raw) => { connection.received.push(JSON.parse(String(raw))); client.send(raw); });
  });
  return {
    connections,
    async disconnect() {
      offline = true;
      await Promise.all(connections.flatMap(({ client, server }) => [client.close(), server.close()]));
    },
    reconnect() {
      offline = false;
      for (const resolve of waiting) resolve();
      waiting.clear();
    },
  };
}

export async function observeAnnouncements(page) {
  const region = page.locator(".sr-only[aria-live]");
  await region.evaluate((element) => {
    element.testAnnouncements = [];
    new MutationObserver(() => element.testAnnouncements.push(element.textContent))
      .observe(element, { childList: true, subtree: true, characterData: true });
  });
  return () => region.evaluate((element) => element.testAnnouncements);
}
