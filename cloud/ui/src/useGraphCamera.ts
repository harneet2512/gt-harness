import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  type MutableRefObject,
} from "react";
import { select } from "d3-selection";
import { zoom, zoomIdentity, type ZoomBehavior, type ZoomTransform } from "d3-zoom";
import type { Particle } from "./graph";
import { fitTransform, hitTest, recenter, ZOOM_EXTENT } from "./graphSim";
import { loadCamera, saveCamera } from "./layoutStore";
import { useSize, type Size } from "./useSize";

/** Long enough that a pinch or a wheel gesture writes once, not per frame. */
const CAMERA_SAVE_MS = 500;

export interface Camera {
  wrapRef: React.RefObject<HTMLDivElement>;
  canvasRef: React.RefObject<HTMLCanvasElement>;
  size: Size;
  /** Read by the painter and by hit testing; never state, never a render. */
  transform: MutableRefObject<ZoomTransform>;
  /** True once the field has been framed at least once. */
  framed: MutableRefObject<boolean>;
  fit: () => void;
}

/**
 * The camera: canvas sizing at device pixel ratio, d3-zoom for pan and
 * wheel, framing, and re-centring when a panel resizes. Dragging a
 * particle is not a pan, so the zoom filter defers to a hit test.
 */
export function useGraphCamera(
  getParticles: () => readonly Particle[],
  onZoom: (k: number) => void,
  kick: () => void,
  /** Persistence key: the camera is remembered per session. */
  sessionId: string | null,
): Camera {
  const [wrapRef, size] = useSize<HTMLDivElement>();
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Restored before the first paint, so a reload opens on the same view
  // rather than re-framing a layout the reader had already positioned.
  const restored = useMemo(() => loadCamera(sessionId), [sessionId]);
  const transform = useRef<ZoomTransform>(
    restored
      ? zoomIdentity.translate(restored.x, restored.y).scale(restored.k)
      : zoomIdentity,
  );
  const zoomRef = useRef<ZoomBehavior<HTMLCanvasElement, unknown> | null>(null);
  const framed = useRef(restored !== null);
  const lastSize = useRef({ width: 0, height: 0 });

  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const remember = useCallback(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      saveTimer.current = null;
      const { k, x, y } = transform.current;
      saveCamera(sessionId, { k, x, y });
    }, CAMERA_SAVE_MS);
  }, [sessionId]);

  useEffect(
    () => () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    },
    [],
  );

  const applyTransform = useCallback(
    (next: ZoomTransform) => {
      transform.current = next;
      const canvas = canvasRef.current;
      if (canvas && zoomRef.current) {
        select(canvas).call(zoomRef.current.transform, next);
      }
      onZoom(next.k);
      remember();
      kick();
    },
    [onZoom, kick, remember],
  );

  const fit = useCallback(() => {
    const particles = getParticles();
    if (size.width < 2 || particles.length === 0) return;
    framed.current = true;
    applyTransform(fitTransform(particles, size.width, size.height));
  }, [applyTransform, getParticles, size.width, size.height]);

  /* ---- canvas sizing ---- */
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || size.width < 2 || size.height < 2) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(size.width * dpr);
    canvas.height = Math.round(size.height * dpr);
    canvas.style.width = `${size.width}px`;
    canvas.style.height = `${size.height}px`;
    canvas.getContext("2d")?.setTransform(dpr, 0, 0, dpr, 0, 0);

    const before = lastSize.current;
    lastSize.current = { width: size.width, height: size.height };

    if (!framed.current) fit();
    else if (before.width > 0) {
      applyTransform(
        recenter(
          transform.current,
          size.width - before.width,
          size.height - before.height,
        ),
      );
    }
    kick();
  }, [size.width, size.height, applyTransform, fit, kick]);

  /* ---- zoom and pan ---- */
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const behaviour = zoom<HTMLCanvasElement, unknown>()
      .scaleExtent(ZOOM_EXTENT)
      .filter((event: Event) => {
        if (event.type === "wheel") return true;
        if (event.type === "dblclick") return false;
        const pointer = event as MouseEvent;
        if (pointer.button !== undefined && pointer.button !== 0) return false;
        return (
          hitTest(
            getParticles(),
            transform.current,
            pointer.offsetX,
            pointer.offsetY,
          ) === null
        );
      })
      .on("zoom", (event: { transform: ZoomTransform }) => {
        transform.current = event.transform;
        onZoom(event.transform.k);
        remember();
        kick();
      });

    zoomRef.current = behaviour;
    const selection = select(canvas);
    selection.call(behaviour).on("dblclick.zoom", null);
    // Keep d3's own record in step with whatever framing already happened.
    selection.call(behaviour.transform, transform.current);

    return () => {
      selection.on(".zoom", null);
      zoomRef.current = null;
    };
  }, [getParticles, kick, onZoom, remember]);

  return { wrapRef, canvasRef, size, transform, framed, fit };
}
