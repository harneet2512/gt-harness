import { useEffect, useRef, useState } from "react";

const PIN_THRESHOLD_PX = 60;

export interface AutoScroll {
  ref: React.RefObject<HTMLDivElement>;
  /** True when the reader has scrolled away from the bottom. */
  detached: boolean;
  jumpToLatest: () => void;
}

/**
 * Keeps a scroll container glued to the bottom for streaming content, unless
 * the reader has scrolled up — in which case it stays put and `detached`
 * turns on so a "jump to latest" affordance can be shown.
 */
export function useAutoScroll(): AutoScroll {
  const ref = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const [detached, setDetached] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = () => {
      const atBottom =
        el.scrollHeight - el.scrollTop - el.clientHeight < PIN_THRESHOLD_PX;
      pinned.current = atBottom;
      setDetached(!atBottom);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // Runs after every render: cheap, and the pin check keeps it from fighting
  // a reader who has scrolled up.
  useEffect(() => {
    if (!pinned.current) return;
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  });

  const jumpToLatest = () => {
    const el = ref.current;
    if (!el) return;
    pinned.current = true;
    setDetached(false);
    el.scrollTop = el.scrollHeight;
  };

  return { ref, detached, jumpToLatest };
}
