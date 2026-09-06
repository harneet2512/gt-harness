/* ------------------------------------------------------------------ *
 * Which layout the shell is in.
 *
 * Three modes, one source of truth. The pixel values are repeated in
 * `styles/shell.css` and `styles/inspector.css` as media queries — they
 * have to be, CSS cannot read a module — so they live here as named
 * constants and the media queries quote them in a comment.
 * ------------------------------------------------------------------ */

import { useMedia } from "./useSize";

/** Below this the graph cannot share the row with two side panels. */
export const NARROW_MAX_PX = 1100;

/**
 * Below this the graph stops sharing the row with the transcript at all and
 * stacks under it. Quoted by the `max-width: 1199.98px` block in
 * `styles/term.css`; nothing in JS branches on it, because the split is
 * laid out entirely in CSS (HAR-84 P1-5).
 */
export const SPLIT_STACK_MAX_PX = 1200;

/** Below this there is no row left to share: the panes stack. */
export const STACK_MAX_PX = 760;

export type LayoutMode =
  /** Three columns side by side, as an editor has them. */
  | "wide"
  /** One column plus overlays: the conversation and the inspector float. */
  | "narrow"
  /** Stacked: conversation over graph, with full-screen sheets. */
  | "stacked";

/** The mode a viewport of `width` CSS pixels is in. */
export function layoutModeFor(width: number): LayoutMode {
  if (!Number.isFinite(width) || width <= 0) return "wide";
  if (width < STACK_MAX_PX) return "stacked";
  if (width < NARROW_MAX_PX) return "narrow";
  return "wide";
}

/** True when the conversation and the inspector are overlays, not columns. */
export function isOverlayMode(mode: LayoutMode): boolean {
  return mode !== "wide";
}

const NARROW_QUERY = `(max-width: ${NARROW_MAX_PX - 0.02}px)`;
const STACK_QUERY = `(max-width: ${STACK_MAX_PX - 0.02}px)`;

/** The current mode, kept in step with the CSS by the same two numbers. */
export function useLayoutMode(): LayoutMode {
  const narrow = useMedia(NARROW_QUERY);
  const stacked = useMedia(STACK_QUERY);
  if (stacked) return "stacked";
  if (narrow) return "narrow";
  return "wide";
}
