import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";

const BACKEND = "http://localhost:8000";

/** The apex is plain localhost (or www.localhost), as luke-hirsch.de is in prod. */
const onApex = (host: string | undefined) =>
  ["localhost", "www.localhost"].includes((host ?? "").split(":")[0]);

export default defineConfig({
  plugins: [
    tanstackRouter({ target: "react", autoCodeSplitting: true }),
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  // dist/.vite/manifest.json: the Django-rendered pages look up the hashed stylesheet in it
  // (backend/spa/theme.py, settings.SPA_MANIFEST).
  build: { manifest: true },
  server: {
    host: true, // listen on 0.0.0.0, so localhost, jac.localhost and portfolio.localhost all resolve
    allowedHosts: [".localhost"], // the bare domain and every subdomain of it
    proxy: {
      // changeOrigin:false (the default) forwards the ORIGINAL Host to Django, so it sees
      // jac.localhost:5173 or portfolio.localhost:5173, as nginx passes the real host in prod.
      // ALLOWED_HOSTS and the CSRF origin check go by it.
      "/api": { target: BACKEND, changeOrigin: false },
      "/_allauth": { target: BACKEND, changeOrigin: false },
      "/admin": { target: BACKEND, changeOrigin: false },
      "/legal": { target: BACKEND, changeOrigin: false },
      "/media": { target: BACKEND, changeOrigin: false },
      "/static": { target: BACKEND, changeOrigin: false },
      "/ws": { target: BACKEND, ws: true, changeOrigin: false },
      // The apex's "/" is Django's landing, as nginx serves it in prod. Anywhere else, bypass hands
      // the request back to vite: "/" on jac. and portfolio. stays the SPA's.
      "^/(\\?.*)?$": {
        target: BACKEND,
        changeOrigin: false,
        bypass: (req) => (onApex(req.headers.host) ? undefined : req.url),
      },
    },
  },
});
