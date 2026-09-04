import { useCallback, useEffect, useRef, type MutableRefObject } from "react";
import { select } from "d3-selection";
import { zoom, zoomIdentity, type ZoomBehavior, type ZoomTransform } from "d3-zoom";
import type { Particle } from "./graph";
import { fitTransform, hitTest, recenter, ZOOM_EXTENT } from "./graphSim";
import { useSize, type Size } from "./useSize";

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
): Camera {
  const [wrapRef, size] = useSize<HTMLDivElement>();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const transform = useRef<ZoomTransform>(zoomIdentity);
  const zoomRef = useRef<ZoomBehavior<HTMLCanvasElement, unknown> | null>(null);
  const framed = useRef(false);
  const lastSize = useRef({ width: 0, height: 0 });

  const applyTransform = useCallback(
    (next: ZoomTransform) => {
      transform.current = next;
      const canvas = canvasRef.current;
      if (canvas && zoomRef.current) {
        select(canvas).call(zoomRef.current.transform, next);
      }
      onZoom(next.k);
      kick();
    },
    [onZoom, kick],
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
  }, [getParticles, kick, onZoom]);

  return { wrapRef, canvasRef, size, transform, framed, fit };
}
