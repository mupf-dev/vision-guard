import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-Proxy analog zu nginx.conf. Ziel: laufender Stack (Frontend-nginx auf :8080).
const target = process.env.VG_PROXY_TARGET ?? "http://localhost:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: {
      "/api": { target, changeOrigin: true, ws: true },
      "/media": { target, changeOrigin: true },
      "/go2rtc": { target, changeOrigin: true, ws: true },
    },
  },
  build: {
    chunkSizeWarningLimit: 1200,
  },
});
