import { useRef, useState } from "react";

export interface DragSize {
  size: number;
  handlers: {
    onPointerDown: (e: React.PointerEvent<HTMLElement>) => void;
    onPointerMove: (e: React.PointerEvent<HTMLElement>) => void;
    onPointerUp: (e: React.PointerEvent<HTMLElement>) => void;
    onDoubleClick: () => void;
  };
}

/**
 * A draggable panel edge. `x` grows to the right of the grip, `y` grows
 * above it — which is what a bottom panel with a grip on its top edge
 * wants — and `x-left` grows to the left of it, which is what a right-hand
 * panel wants. Double-click restores the default.
 */
export function useDragSize(
  initial: number,
  min: number,
  max: number,
  axis: "x" | "y" | "x-left",
): DragSize {
  const [size, setSize] = useState(initial);
  const start = useRef<{ at: number; size: number } | null>(null);

  return {
    size,
    handlers: {
      onPointerDown: (e) => {
        start.current = {
          at: axis === "y" ? e.clientY : e.clientX,
          size,
        };
        e.currentTarget.setPointerCapture(e.pointerId);
      },
      onPointerMove: (e) => {
        const from = start.current;
        if (!from) return;
        const now = axis === "y" ? e.clientY : e.clientX;
        const delta = now - from.at;
        const next = axis === "x" ? from.size + delta : from.size - delta;
        setSize(Math.min(max, Math.max(min, next)));
      },
      onPointerUp: (e) => {
        start.current = null;
        if (e.currentTarget.hasPointerCapture(e.pointerId)) {
          e.currentTarget.releasePointerCapture(e.pointerId);
        }
      },
      onDoubleClick: () => setSize(initial),
    },
  };
}
