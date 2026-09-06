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
    proxy: proxyTable(),
  },
});

/*
 * The dev server talks to a local API by default. Point it at a deployed one
 * — a codespace, say — with GT_API_TARGET, and hand it a token with
 * GT_API_TOKEN when that deployment wants one: the header is added by the
 * proxy, so the browser never holds a credential for another origin. Dev
 * only; the production bundle is served same-origin behind nginx.
 */
function proxyTable() {
  const target = process.env.GT_API_TARGET ?? "http://localhost:8000";
  const token = process.env.GT_API_TOKEN ?? "";
  const one = {
    target,
    changeOrigin: true,
    secure: true,
    ...(token ? { headers: { Authorization: `Bearer ${token}` } } : {}),
  };
  return { "/api": one, "/auth": one, "/health": one };
}
