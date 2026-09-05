import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Stamp the commit into the bundle. `cloud/deploy.sh` exports BUILD_SHA and
  // cloud/ui/Dockerfile passes it through, so a deployed SPA can always be
  // matched against `GET /health`'s `commit`.
  define: {
    __BUILD_SHA__: JSON.stringify(process.env.BUILD_SHA ?? "dev"),
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/auth": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/health": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
