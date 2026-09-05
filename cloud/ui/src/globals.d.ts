/** Replaced at build time by vite (`define` in vite.config.ts).
 *
 * The short commit the bundle was built from, or `"dev"` under the dev server.
 * It exists so a served SPA can be identified from the outside: round-2 QA ran
 * against a `cloud-ui` image two commits behind the server with nothing on the
 * page to say so.
 *
 * `declare global` rather than a bare `declare const`: `moduleDetection:
 * "force"` in tsconfig.json makes every file a module, so a top-level
 * declaration would be scoped to this file alone.
 */
export {};

declare global {
  const __BUILD_SHA__: string;
}
