import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Development keeps a same-origin browser surface while proxying to the local
// Hybrid Hub. Production cloud builds connect directly through hub/runtime.ts.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:17841",
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
