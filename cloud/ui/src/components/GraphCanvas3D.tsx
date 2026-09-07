import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphViewProps } from "../graphProps";
import type { WorkerLayer } from "../graphDraw";
import {
  buildScene3d,
  recallScene,
  rememberScene,
  type Node3D,
  type Scene3D,
} from "../graph3d";
import { clusterAnchors } from "../graphSim";
import { useReducedMotion } from "../motion";
import { onPalette } from "../palette";
import { useSignals } from "../useSignals";
import { useSize } from "../useSize";
import { Renderer3D, type Frame3DState } from "../three/renderer3d";
import GraphOverlay, { type HoverInfo } from "./GraphOverlay";

/** A drag is not a click. The same slop the flat canvas allows. */
const CLICK_SLOP = 3;

/**
 * The particle field with depth.
 *
 * This module is the whole of the 3D view's static import graph — three,
 * the octree solver, the shaders — and it is only ever reached through a
 * dynamic import, so a session that never asks for depth never downloads
 * any of it.
 *
 * It owns no drawing. React's job here is the three things React is good
 * at: put two canvases on the page, keep a renderer alive for as long as
 * they are there, and turn a pointer into a selection. Everything that
 * happens sixty times a second happens inside `Renderer3D`, reading the
 * props out of a ref — a frame must never cost a render.
 */
export default function GraphCanvas3D(props: GraphViewProps) {
  const [wrapRef, size] = useSize<HTMLDivElement>();
  /* Written by the mount effect, which owns the element. */
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const labelsRef = useRef<HTMLCanvasElement>(null);
  const [hover, setHover] = useState<HoverInfo | null>(null);

  const reduced = useReducedMotion();
  const hoverId = useRef<string | null>(null);
  const renderer = useRef<Renderer3D | null>(null);

  /* Everything the loop reads lives in a ref, and the ref is refreshed on
     every render. The loop always sees the newest props and never causes
     one. */
  const live = useRef(props);
  live.current = props;

  const layers = useMemo<WorkerLayer[]>(
    () =>
      props.workerTrails.map((worker) => ({
        id: worker.id,
        rgb: worker.rgb,
        attention: worker.attention,
        steps: worker.steps,
        positionId: worker.positionId,
        running: worker.status === "running",
      })),
    [props.workerTrails],
  );
  const layersRef = useRef(layers);
  layersRef.current = layers;

  const reducedRef = useRef(reduced);
  reducedRef.current = reduced;

  const kick = useRef(() => renderer.current?.kick()).current;

  /* The same director, the same rules, the same one-departure-a-frame
     clock the flat canvas runs on. */
  const { director, arrivals } = useSignals({
    trailIds: props.trailIds,
    trailToken: props.trailToken,
    animate: props.animate,
    workerTrails: props.workerTrails,
    animateWorkers: props.animateWorkers,
    reduced,
    kick,
  });

  /* ---------------- the layout ---------------- */

  const sceneRef = useRef<Scene3D | null>(null);
  const layout = useMemo(() => {
    const { field } = props;
    /* A scene left behind by the last time this view was open is the same
       layout, already settled: coming back to 3D should not re-derive a
       picture the reader was just looking at. */
    const previous = sceneRef.current ?? recallScene(field.signature);
    if (previous && previous.signature === field.signature) {
      sceneRef.current = previous;
      return { scene: previous, carried: true };
    }
    const built = buildScene3d(
      field,
      clusterAnchors(field.clusters),
      previous,
    );
    sceneRef.current = built;
    return { scene: built, carried: previous !== null };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.field]);

  /* ---------------- the renderer ---------------- */

  useEffect(() => {
    const host = wrapRef.current;
    const overlay = labelsRef.current;
    if (!host || !overlay) return;

    /* A fresh canvas element per mount, made here rather than in JSX.
       A WebGL context cannot be re-acquired from a canvas whose context
       was destroyed, and disposing this renderer destroys it — so a
       remount (a mode switch, a panel reopening, React's own double
       mount in development) handed three.js a canvas that answered
       `getContext` with null, and it threw reading `precision` off the
       capabilities it never got. The element is the context's lifetime,
       so the element is created and dropped with it. */
    const canvas = document.createElement("canvas");
    canvas.className = "graph-canvas";
    canvasRef.current = canvas;
    host.insertBefore(canvas, host.firstChild);

    const getState = (): Frame3DState => {
      const state = live.current;
      return {
        field: state.field,
        neighbours: state.neighbours,
        attention: state.attention,
        currentStep: state.currentStep,
        edited: state.edited,
        positionId: state.positionId,
        running: state.running,
        selectedId: state.selectedId,
        hoverId: hoverId.current,
        matches: state.matches,
        labels: state.labels,
        workers: layersRef.current,
        presence: state.presence,
        focusAgent: state.focusAgent,
        reduced: reducedRef.current,
        director: director.current,
        arrivals: arrivals.current,
      };
    };

    let made: Renderer3D;
    try {
      made = new Renderer3D({
        canvas,
        overlay,
        getState,
        onZoom: live.current.onZoom,
      });
    } catch (error) {
      /* A context that could not be created after the probe said it
         could. The stage above catches this and falls back to the flat
         view with a line saying so. */
      renderer.current = null;
      throw error;
    }
    renderer.current = made;
    made.setReduced(reducedRef.current);

    return () => {
      /* The layout is expensive and outlives the renderer; the GPU
         objects do not, and a context leaked here is a view that stops
         working after a few switches. */
      if (sceneRef.current) rememberScene(sceneRef.current);
      renderer.current = null;
      made.dispose();
      canvas.remove();
      if (canvasRef.current === canvas) canvasRef.current = null;
    };
  }, [director, arrivals]);

  useEffect(() => {
    renderer.current?.setLayout(layout.scene, layout.carried);
  }, [layout]);

  useEffect(() => {
    renderer.current?.setReduced(reduced);
  }, [reduced]);

  /* ---------------- sizing ---------------- */

  useEffect(() => {
    const overlay = labelsRef.current;
    if (!overlay || size.width < 2 || size.height < 2) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    overlay.width = Math.round(size.width * dpr);
    overlay.height = Math.round(size.height * dpr);
    overlay.style.width = `${size.width}px`;
    overlay.style.height = `${size.height}px`;
    overlay.getContext("2d")?.setTransform(dpr, 0, 0, dpr, 0, 0);
    renderer.current?.resize(size.width, size.height, dpr);
  }, [size.width, size.height]);

  /* A theme change is a repaint: the shaders read the same tokens the
     sheet does, and nothing else would ask them to. */
  useEffect(() => onPalette(kick), [kick]);

  /* Anything the renderer reads should land on screen. */
  useEffect(kick, [
    kick,
    props.attention,
    props.currentStep,
    props.edited,
    props.positionId,
    props.running,
    props.selectedId,
    props.matches,
    props.labels,
    props.presence,
    props.focusAgent,
    layers,
  ]);

  useEffect(() => {
    if (props.fitToken === 0) return;
    renderer.current?.fit();
  }, [props.fitToken]);

  /* ---------------- pointer ---------------- */

  const down = useRef<{ x: number; y: number } | null>(null);

  const at = (event: React.PointerEvent | React.MouseEvent): Node3D | null => {
    const native = event.nativeEvent as PointerEvent | MouseEvent;
    return renderer.current?.pick(native.offsetX, native.offsetY) ?? null;
  };

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const native = event.nativeEvent;
    const found = at(event);
    if ((found?.id ?? null) !== hoverId.current) {
      hoverId.current = found?.id ?? null;
      kick();
    }
    if (!found) {
      if (hover !== null) setHover(null);
      return;
    }
    if (
      hover &&
      hover.id === found.id &&
      Math.abs(hover.x - native.offsetX) < 2 &&
      Math.abs(hover.y - native.offsetY) < 2
    ) {
      return;
    }
    const particle = props.field.byId.get(found.id);
    if (!particle) return;
    setHover({
      id: found.id,
      path: particle.kind === "dir" ? particle.label : particle.path,
      size: particle.size,
      reads: props.attention.get(found.id)?.reads ?? 0,
      edit: props.edited.get(found.id),
      x: native.offsetX,
      y: native.offsetY,
    });
  };

  return (
    <div
      className="graph"
      ref={wrapRef}
      /* The pointer lives on the wrapper, not on the canvas: the canvas
         is created per mount by the effect above, so it has no JSX to
         hang handlers off. The two boxes are the same rectangle. */
      onPointerDown={(event) => {
          down.current = {
            x: event.nativeEvent.offsetX,
            y: event.nativeEvent.offsetY,
          };
        }}
        onPointerMove={onPointerMove}
        onPointerLeave={() => {
          hoverId.current = null;
          setHover(null);
          kick();
        }}
        onClick={(event) => {
          /* An orbit that happens to end where it began is a click; one
             that travelled is the camera being moved, and moving the
             camera must not change what is selected. */
          const start = down.current;
          down.current = null;
          if (
            start &&
            (Math.abs(start.x - event.nativeEvent.offsetX) > CLICK_SLOP ||
              Math.abs(start.y - event.nativeEvent.offsetY) > CLICK_SLOP)
          ) {
            return;
          }
          const found = at(event);
          props.onSelect(found ? found.id : null);
        }}
      onDoubleClick={(event) => {
        if (!at(event)) renderer.current?.fit();
      }}
    >
      {/* The names, on their own flat canvas over the field: text has to
          stay upright and stay crisp, and neither is something a mesh
          does well. */}
      <canvas ref={labelsRef} className="graph-labels" aria-hidden="true" />
      <GraphOverlay emptyText={props.emptyText} hover={hover} size={size} />
    </div>
  );
}
