import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Backend runs locally (uvicorn app.main:app --port 8000).
// The dev server proxies both REST and WebSocket traffic, so the
// browser only ever talks to http://localhost:5173.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
