import { test, expect } from "@playwright/test";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { startServer, regions } from "./helpers.js";

test("figures, 2-D previews and run time are shown under the cell (D-025)", async ({ page }) => {
  const directory = await mkdtemp(join(tmpdir(), "notebook-e2e-"));
  const path = join(directory, "outputs.nb.md");
  await writeFile(path, "```python\nimport numpy as np\nimport matplotlib.pyplot as plt\n"
    + "grid = np.arange(12.0).reshape(3, 4)\nplt.imshow(grid)\n```\n");
  const server = startServer(path);
  try {
    await page.goto(await server.url);
    const cell = regions(page).first();
    const figure = cell.getByRole("img", { name: "figura 1 da célula 1" });
    await expect(figure).toBeVisible();
    expect(await figure.evaluate((img) => img.naturalWidth)).toBeGreaterThan(0);  // a PNG the browser decoded
    await expect(cell.getByText("mín 0 · máx 11 · média 5.5")).toBeVisible();
    await expect(cell.getByRole("table", { name: "canto superior esquerdo de grid" })).toContainText("11");
    await expect(cell.locator(".duration")).toHaveText(/^\d+ ms$|^\d+,\d s$/);
  } finally {
    await server.stop();
    await rm(directory, { recursive: true, force: true });
  }
});
