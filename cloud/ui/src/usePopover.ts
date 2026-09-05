import { useEffect, useRef, useState } from "react";

export interface Popover {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
  /** Put this on the element that contains both the trigger and the panel. */
  ref: React.RefObject<HTMLDivElement>;
}

/**
 * A popover that closes the two ways every popover must: a click outside it,
 * and Escape. Nothing else — the trigger stays the caller's business.
 */
export function usePopover(): Popover {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return { open, setOpen, toggle: () => setOpen(!open), ref };
}
