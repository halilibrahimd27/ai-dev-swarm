import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builds the control-plane SPA to ./dist. FastAPI serves that directory as
// static assets (CSP: script-src 'self' — the bundle is same-origin). During
// development, `vite` proxies /api + /sse to the running orchestrator on 8080
// so the SPA talks to the real backend without CORS.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "dist",
    assetsDir: "assets",
    sourcemap: false,
    // A single small bundle is fine for a loopback tool — no code-splitting
    // churn, fewer requests, simpler CSP surface.
    chunkSizeWarningLimit: 1500,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8080", changeOrigin: true },
      "/sse": { target: "http://127.0.0.1:8080", changeOrigin: true, ws: false },
    },
  },
  // `npm run preview` serves the PRODUCTION bundle (CSP-compatible — no HMR
  // inline scripts) and proxies the API/SSE to the running orchestrator, so
  // the real UI can be previewed against live data without a docker rebuild.
  preview: {
    port: 4173,
    host: "127.0.0.1",
    proxy: {
      "/api": { target: "http://127.0.0.1:8080", changeOrigin: true },
      "/sse": { target: "http://127.0.0.1:8080", changeOrigin: true, ws: false },
    },
  },
});
