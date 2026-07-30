/**
 * Serve editable Stitch HTML from ../stitch_mileage at /stitch/*
 * Source of truth stays in stitch_mileage/ — edit those files and refresh.
 */
import type { Plugin } from "vite";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const STITCH_ROOT = path.resolve(__dirname, "..", "stitch_mileage");

const MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".webp": "image/webp",
  ".md": "text/markdown; charset=utf-8",
};

function safeJoin(root: string, urlPath: string): string | null {
  const decoded = decodeURIComponent(urlPath.split("?")[0] || "");
  const rel = decoded.replace(/^\/+/, "");
  const full = path.resolve(root, rel);
  if (!full.startsWith(root)) return null;
  return full;
}

export function serveStitchHtml(): Plugin {
  return {
    name: "serve-stitch-html",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = req.url || "";
        if (!url.startsWith("/stitch")) return next();

        let rel = url.slice("/stitch".length) || "/";
        if (rel === "/" || rel === "") rel = "/index.html";

        const filePath = safeJoin(STITCH_ROOT, rel);
        if (!filePath) {
          res.statusCode = 403;
          res.end("Forbidden");
          return;
        }

        let target = filePath;
        if (fs.existsSync(target) && fs.statSync(target).isDirectory()) {
          target = path.join(target, "index.html");
        }
        // Stitch screens live as code.html inside named folders
        if (!fs.existsSync(target) && !target.endsWith(".html")) {
          const asCode = path.join(filePath, "code.html");
          if (fs.existsSync(asCode)) target = asCode;
        }

        if (!fs.existsSync(target) || !fs.statSync(target).isFile()) {
          res.statusCode = 404;
          res.end(`Not found: ${rel}`);
          return;
        }

        const ext = path.extname(target).toLowerCase();
        res.setHeader("Content-Type", MIME[ext] || "application/octet-stream");
        res.setHeader("Cache-Control", "no-store");
        fs.createReadStream(target).pipe(res);
      });
    },
  };
}
