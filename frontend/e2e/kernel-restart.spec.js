import { test, expect } from "@playwright/test";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { startServer, regions, observeAnnouncements } from "./helpers.js";

test("the restart button brings a fresh kernel up and reruns what had run (D-026)", async ({ page }) => {
  const directory = await mkdtemp(join(tmpdir(), "notebook-e2e-"));
  const path = join(directory, "restart.nb.md");
  // `pid` proves the second run happens in another process, not that the old values survived
  await writeFile(path, "```python\nimport os\npid = os.getpid()\n```\n\n```python\ndoubled = pid * 2\n```\n");
  const server = startServer(path);
  try {
    await page.goto(await server.url);
    const first = regions(page).first();
    const pidRow = first.locator("dd").first();
    await expect(pidRow).toContainText("int");
    const before = await pidRow.textContent();
    const announcements = await observeAnnouncements(page);

    await page.getByRole("button", { name: "reiniciar kernel" }).click();

    await expect(page.getByText("reinícios do kernel: 1")).toBeVisible();
    await expect(page.getByText("reiniciando kernel…")).toHaveCount(0);
    await expect(pidRow).not.toHaveText(before);
    await expect(regions(page).nth(1).locator("dd").first()).toContainText("int");
    expect((await announcements()).filter((m) => m.includes("O kernel foi reiniciado"))).toHaveLength(1);
  } finally {
    await server.stop();
    await rm(directory, { recursive: true, force: true });
  }
});
