import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphViewProps } from "../graphProps";
import { buildCity, recallCity, rememberCity, type CityLayout } from "../city";
import { useReducedMotion } from "../motion";
import { onPalette } from "../palette";
import { useSize } from "../useSize";
import { Renderer3D } from "../three/renderer3d";
import GraphOverlay, { type HoverInfo } from "./GraphOverlay";

export default function GraphCanvas3D(props: GraphViewProps) {
  const [wrapRef, size] = useSize<HTMLDivElement>();
  const labels = useRef<HTMLCanvasElement>(null);
  const renderer = useRef<Renderer3D | null>(null);
  const current = useRef(props);
  current.current = props;
  const reduced = useReducedMotion();
  const reducedRef = useRef(reduced);
  reducedRef.current = reduced;
  const hoverId = useRef<string | null>(null);
  const [hover, setHover] = useState<HoverInfo | null>(null);
  const [lost, setLost] = useState(false);
  const [zoom, setZoom] = useState(100);
  const previous = useRef<CityLayout | null>(null);
  const previousSession = useRef(props.sessionId);
  const layout = useMemo(() => {
    if(previousSession.current !== props.sessionId){previous.current=null;previousSession.current=props.sessionId;}
    const city = buildCity(
      props.field,
      previous.current ?? recallCity(props.sessionId),
    );
    previous.current = city;
    rememberCity(props.sessionId, city);
    return city;
  }, [props.field, props.sessionId]);
  useEffect(() => {
    const host = wrapRef.current,
      overlay = labels.current;
    if (!host || !overlay) return;
    const canvas = document.createElement("canvas");
    canvas.className = "graph-canvas";
    host.insertBefore(canvas, host.firstChild);
    const onLost = (e: Event) => {
      e.preventDefault();
      setLost(true);
    };
    canvas.addEventListener("webglcontextlost", onLost);
    let made: Renderer3D;
    try {
      made = new Renderer3D({
        canvas,
        overlay,
        getState: () => ({
          ...current.current,
          hoverId: hoverId.current,
          reduced: reducedRef.current,
        }),
        onZoom: (k) => {
          setZoom(Math.round(k * 100));
          current.current.onZoom(k);
        },
      });
    } catch (e) {
      canvas.remove();
      throw e;
    }
    renderer.current = made;
    return () => {
      canvas.removeEventListener("webglcontextlost", onLost);
      made.dispose();
      renderer.current = null;
      canvas.remove();
    };
  }, []);
  useEffect(() => {
    renderer.current?.setLayout(layout, true);
  }, [layout]);
  useEffect(() => {
    renderer.current?.setReduced(reduced);
  }, [reduced]);
  useEffect(() => {
    const c = labels.current;
    if (!c || size.width < 2 || size.height < 2) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    c.width = Math.round(size.width * dpr);
    c.height = Math.round(size.height * dpr);
    c.style.width = `${size.width}px`;
    c.style.height = `${size.height}px`;
    c.getContext("2d")?.setTransform(dpr, 0, 0, dpr, 0, 0);
    renderer.current?.resize(size.width, size.height, dpr);
  }, [size.width, size.height]);
  useEffect(() => onPalette(() => renderer.current?.kick()), []);
  useEffect(() => {
    renderer.current?.kick();
  }, [props]);
  useEffect(() => {
    if (props.fitToken) renderer.current?.fit();
  }, [props.fitToken]);
  const down = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  const point = (e: React.PointerEvent | React.MouseEvent) => {
    const r = wrapRef.current!.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };
  if (lost) throw new Error("City WebGL context lost");
  return (
    <div
      className="graph city-canvas"
      tabIndex={0}
      aria-label="Repository city. Use plus and minus to zoom, zero to fit. Drag to orbit."
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (["+", "=", "-", "0"].includes(e.key)) {
          e.preventDefault();
          if (e.key === "0") renderer.current?.fit();
          else renderer.current?.zoomBy(e.key === "-" ? 1 / 1.3 : 1.3);
        }
      }}
      ref={wrapRef}
      onPointerDown={(e) => {
        if (timer.current) clearTimeout(timer.current);
        setHover(null);
        const p = point(e);
        down.current = { ...p, moved: false };
      }}
      onPointerMove={(e) => {
        const p = point(e);
        if (
          down.current &&
          Math.hypot(p.x - down.current.x, p.y - down.current.y) > 3
        )
          down.current.moved = true;
        if (down.current?.moved) {
          if (timer.current) clearTimeout(timer.current);
          hoverId.current = null;
          setHover(null);
          return;
        }
        const found = renderer.current?.pick(p.x, p.y);
        if ((found?.id ?? null) === hoverId.current) return;
        hoverId.current = found?.id ?? null;
        renderer.current?.kick();
        if (timer.current) clearTimeout(timer.current);
        setHover(null);
        const particle = found ? props.field.byId.get(found.id) : null;
        if (particle)
          timer.current = setTimeout(
            () =>
              setHover({
                id: particle.id,
                path: particle.path,
                size: particle.size,
                reads: props.attention.get(particle.id)?.reads ?? 0,
                edit: props.edited.get(particle.id),
                x: p.x,
                y: p.y,
              }),
            100,
          );
      }}
      onPointerLeave={() => {
        hoverId.current = null;
        setHover(null);
        if (timer.current) clearTimeout(timer.current);
        renderer.current?.kick();
      }}
      onClick={(e) => {
        if (down.current?.moved) {
          down.current = null;
          return;
        }
        down.current = null;
        const p = point(e);
        const hit = renderer.current?.pickSelection(p.x, p.y);
        if (hit?.kind === "agent") props.onSelectAgent?.(hit.id);
        else props.onSelect(hit?.id ?? null);
      }}
      onDoubleClick={(e) => {
        const p = point(e);
        if (!renderer.current?.pickSelection(p.x, p.y)) renderer.current?.fit();
      }}
    >
      <canvas ref={labels} className="graph-labels" aria-hidden="true" />
      <GraphOverlay emptyText={props.emptyText} hover={hover} size={size} />
      <div className="city-zoom" role="group" aria-label="City camera controls"
        onPointerDown={(e) => e.stopPropagation()}
        onPointerMove={(e) => e.stopPropagation()}
        onClick={(e) => e.stopPropagation()}
        onDoubleClick={(e) => e.stopPropagation()}>
        <button type="button" aria-label="Zoom in" title="Zoom in (+)" onClick={() => renderer.current?.zoomBy(1.3)}>+</button>
        <output aria-label="City zoom">{zoom}%</output>
        <button type="button" aria-label="Zoom out" title="Zoom out (-)" onClick={() => renderer.current?.zoomBy(1 / 1.3)}>−</button>
        <button type="button" aria-label="Fit repository" title="Fit repository (0)" onClick={() => renderer.current?.fit()}>⌖</button>
      </div>
    </div>
  );
}
