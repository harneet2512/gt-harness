/* ------------------------------------------------------------------ *
 * Reduced motion.
 *
 * The stylesheet already answers this for everything it owns (see the
 * `prefers-reduced-motion` block in `base.css`). The canvas cannot: it
 * paints itself, so it has to ask.
 *
 * Asking is guarded on both sides — `matchMedia` is absent under the
 * node test environment and its `addEventListener` is absent on older
 * WebKit — because a preference we cannot read is not a reason to fail to
 * draw. The fallback is motion, which is the status quo.
 * ------------------------------------------------------------------ */

import { useEffect, useState } from "react";

export const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  try {
    return window.matchMedia(REDUCED_MOTION).matches;
  } catch {
    return false;
  }
}

/** The preference, live: changing it mid-session takes effect at once. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotion);

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    let query: MediaQueryList;
    try {
      query = window.matchMedia(REDUCED_MOTION);
    } catch {
      return;
    }
    const onChange = () => setReduced(query.matches);
    onChange();
    if (typeof query.addEventListener === "function") {
      query.addEventListener("change", onChange);
      return () => query.removeEventListener("change", onChange);
    }
    /* Safari before 14. Deprecated, and the only thing there is. */
    query.addListener(onChange);
    return () => query.removeListener(onChange);
  }, []);

  return reduced;
}
