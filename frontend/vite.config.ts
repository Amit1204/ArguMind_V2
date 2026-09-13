import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In Docker the nginx image proxies /api to the backend. This dev-server proxy
// exists only for optional host-side development (`npm run dev`).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3100,
    proxy: {
      "/api": "http://localhost:8100",
      "/health": "http://localhost:8100",
      "/ready": "http://localhost:8100",
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
