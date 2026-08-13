import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/// <reference types="vitest" />

export default defineConfig({
  // Relative base: the same build works at "/" (standalone port) and under a
  // reverse-proxied prefix like "/retinue/" on the portal. Requires the SPA to
  // avoid client-side path routing and to call APIs with relative URLs.
  base: "./",
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: "../server/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:9219",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test-setup.ts",
    css: false,
  },
});
