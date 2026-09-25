import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `vite` on :5173 proxies /ws to the notebook server; start the server with
// `--dev-origin http://localhost:5173` (D-016: the Origin is checked on the WebSocket).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/ws": { target: "http://127.0.0.1:8765", ws: true, changeOrigin: true } },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
