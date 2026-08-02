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
    host: true, // listen on 0.0.0.0 so jane.localhost:5173 resolves
    allowedHosts: [".localhost"],
    proxy: {
      // changeOrigin:false (the default) forwards the ORIGINAL Host to Django, so
      // jane.localhost:5173 -> Django sees Host: jane.localhost:5173 -> owner = jane.
      "/api": { target: BACKEND, changeOrigin: false },
      "/_allauth": { target: BACKEND, changeOrigin: false },
      "/admin": { target: BACKEND, changeOrigin: false },
      "/media": { target: BACKEND, changeOrigin: false },
      "/static": { target: BACKEND, changeOrigin: false },
      "/ws": { target: BACKEND, ws: true, changeOrigin: false },
    },
  },
});
