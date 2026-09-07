/* ------------------------------------------------------------------ *
 * Is there a GPU to draw on.
 *
 * The 3D view is a mode, not a requirement. A browser with WebGL turned
 * off, a machine whose driver is blocklisted, a headless run without a
 * software rasteriser — all of them are ordinary, and none of them should
 * produce a blank pane. The answer is taken *before* the three.js chunk
 * is fetched, so an unsupported browser never downloads a renderer it
 * cannot use.
 *
 * Asked once and remembered: creating a probe context is not free, and
 * the answer does not change within a page's life.
 * ------------------------------------------------------------------ */

/** What one attempt at a context returns. Anything truthy counts. */
export type ContextProbe = (kind: string) => unknown;

/**
 * WebGL 2, and only WebGL 2.
 *
 * Not a preference: three.js dropped WebGL 1 in r163 and its renderer
 * throws outright without a `webgl2` context, so accepting a WebGL 1
 * browser here would only move the failure later and make it louder.
 * WebGL 2 is also what SwiftShader provides in headless Chromium, which
 * is the configuration this view is recorded in.
 */
export const CONTEXT = "webgl2";

export function probeWebgl(probe: ContextProbe): boolean {
  try {
    return Boolean(probe(CONTEXT));
  } catch {
    /* Some browsers throw rather than returning null. Same answer. */
    return false;
  }
}

let cached: boolean | null = null;

/** Whether this browser can draw the 3D view at all. */
export function hasWebgl(): boolean {
  if (cached !== null) return cached;
  if (typeof document === "undefined") {
    cached = false;
    return cached;
  }
  try {
    const canvas = document.createElement("canvas");
    cached = probeWebgl((kind) => canvas.getContext(kind));
  } catch {
    cached = false;
  }
  return cached;
}

/** The one line the panel shows instead of the 3D view. */
export const NO_WEBGL =
  "3D needs WebGL, which this browser did not provide — showing the flat graph.";

/** The one line shown when the 3D chunk itself could not be loaded. */
export const NO_CHUNK =
  "the 3D view could not be loaded — showing the flat graph.";
