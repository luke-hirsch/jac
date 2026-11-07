import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";

const BACKEND = "http://localhost:8000";

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
    },
  },
});
