/**
 * The connector's PDF preview (src/connector): one self-contained HTML file, scripts and styles
 * inlined. The connector serves it as the MCP App `ui://jac/preview` (backend/jac/mcp.py), and
 * Claude runs it in a sandbox that loads nothing from elsewhere, so nothing may be a separate
 * chunk. Written into the backend, untracked; the image's spa stage builds its own copy.
 */
import path from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

/** png-js gets fflate's `unzlib` without the Web Worker the sandbox refuses
 *  (src/connector/inflate-sync.ts). Only png-js: the shim itself imports the real fflate. */
const inflateOnThisThread: Plugin = {
  name: "jac-inflate-sync",
  enforce: "pre",
  resolveId(source, importer) {
    if (source === "fflate" && importer?.includes("/node_modules/png-js/")) {
      return path.resolve(import.meta.dirname, "src/connector/inflate-sync.ts");
    }
    return null;
  },
};

export default defineConfig({
  plugins: [inflateOnThisThread, react(), viteSingleFile()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  build: {
    outDir: path.resolve(
      import.meta.dirname,
      "../backend/jac/resources/connector",
    ),
    emptyOutDir: true,
    rollupOptions: {
      input: path.resolve(import.meta.dirname, "preview.html"),
    },
  },
});
