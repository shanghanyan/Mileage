import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { serveStitchHtml } from "./vite.stitch";

export default defineConfig({
  plugins: [react(), serveStitchHtml()],
  server: {
    port: 5173,
    // Listen on LAN so phones on the same Wi‑Fi can open the UI.
    host: true,
    hmr: {
      overlay: false,
    },
    fs: {
      allow: [".."],
    },
    proxy: {
      "/redemptions": "http://127.0.0.1:8000",
      "/status": "http://127.0.0.1:8000",
      "/freshness": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
      "/scrape": "http://127.0.0.1:8000",
      "/me": "http://127.0.0.1:8000",
      "/users": "http://127.0.0.1:8000",
    },
  },
  preview: {
    host: true,
    port: 5173,
  },
});
