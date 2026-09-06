import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Simulation } from "d3-force";
import type { DiffFile } from "../api";
import type { Filament, Particle, ParticleField } from "../graph";
import { draw, type WorkerLayer } from "../graphDraw";
import { onPalette } from "../palette";
import {
  clusterAnchors,
  createSim,
  GENTLE_ALPHA,
  hitTest,
  RESTART_ALPHA,
} from "../graphSim";
import { useReducedMotion } from "../motion";
import { PRIMARY_AGENT, PRIMARY_RGB, SignalDirector } from "../signals";
import type { Attention } from "../trail";
import type { WorkerTrail } from "../useGraphView";
import { useGraphCamera } from "../useGraphCamera";
import GraphOverlay, { type HoverInfo } from "./GraphOverlay";

const DIM_MS = 120;
const CLICK_SLOP = 3;
/** Ticks run before the first paint so the field arrives already legible. */
const PRESETTLE = 90;

interface Props {
  /** Persistence key for the camera; null outside a session. */
  sessionId: string | null;
  field: ParticleField;
  neighbours: ReadonlyMap<string, ReadonlySet<string>>;
  /** Keyed by particle id. */
  attention: ReadonlyMap<string, Attention>;
  currentStep: number;
  edited: ReadonlyMap<string, DiffFile>;
  positionId: string | null;
  running: boolean;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  matches: ReadonlySet<string> | null;
  labels: boolean;
  /** Particle ids the agent walked, in order, up to the scrub cutoff. */
  trailIds: readonly string[];
  /** Identity of that walk: a change means replay, which never animates. */
  trailToken: string;
  animate: boolean;
  /** Every worker agent's walk across the same field, in its own colour. */
  workerTrails: readonly WorkerTrail[];
  /** Particle id → the agents on it, so a shared one shows all of them. */
  presence: ReadonlyMap<string, readonly string[]>;
  /** The agent drawn at full strength — hover or isolate. Null for all. */
  focusAgent: string | null;
  /**
   * Whether a worker's new waypoints animate. Separate from `animate`: a
   * worker runs on its own clock, so its trail moves while the primary
   * session sits idle.
   */
  animateWorkers: boolean;
  /** Incremented by the toolbar to ask for a fit. */
  fitToken: number;
  onZoom: (k: number) => void;
  emptyText: string | null;
}

/** The living particle graph: files as particles, relations as filaments. */
/**
 * Pointer capture, for a pointer that may already be gone.
 *
 * `setPointerCapture` throws `NotFoundError`/`InvalidPointerId` the moment
 * the pointer it names is no longer active — a pen lifted, a cancelled
 * touch, a very fast release — and one uncaught throw inside a React
 * handler is a broken drag (HAR-84 P2-12). Neither call is worth a page
 * error: capture is an optimisation, and losing it costs a drag, not the
 * app.
 */
function capture(el: Element, pointerId: number): void {
  if (pointerId === undefined || typeof el.setPointerCapture !== "function") {
    return;
  }
  try {
    el.setPointerCapture(pointerId);
  } catch {
    /* the pointer is already gone */
  }
}

function release(el: Element, pointerId: number): void {
  if (pointerId === undefined || typeof el.hasPointerCapture !== "function") {
    return;
  }
  try {
    if (el.hasPointerCapture(pointerId)) el.releasePointerCapture(pointerId);
  } catch {
    /* the pointer is already gone */
  }
}

export default function GraphCanvas(props: Props) {
  const { field, attention, edited, onSelect, onZoom, emptyText } = props;
  const { trailIds, trailToken, animate } = props;
  const [hover, setHover] = useState<HoverInfo | null>(null);

  /* Everything the render loop reads lives in a ref: a pan or a signal must
     never cost a React render, and the loop must see the newest props. */
  const live = useRef(props);
  live.current = props;

  const reduced = useReducedMotion();
  const reducedRef = useRef(reduced);
  reducedRef.current = reduced;

  /* The painter's view of the agents, folded once per render. Rebuilding
     this inside the loop was an allocation per frame per agent, which is
     exactly the thing a 60fps budget cannot afford (HAR-84). */
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

  const simRef = useRef<Simulation<Particle, Filament> | null>(null);
  /* Every agent travels through one director: a queue each so the hues
     never mix, one release clock so no two of them pulse on the same
     frame, and one ceiling on what may be in the air at once. */
  const director = useRef(new SignalDirector());
  const workerWalked = useRef(new Map<string, number>());
  const hoverId = useRef<string | null>(null);
  const dim = useRef(0);
  const raf = useRef<number | null>(null);
  const lastFrame = useRef(0);

  /* ---------------- the render loop ---------------- */

  const tick = useRef<(now: number) => void>(() => {});

  const kick = useCallback(() => {
    if (raf.current === null) {
      raf.current = requestAnimationFrame((now) => tick.current(now));
    }
  }, []);

  const getParticles = useCallback(() => live.current.field.particles, []);
  const camera = useGraphCamera(getParticles, onZoom, kick, props.sessionId);
  const { canvasRef, transform } = camera;

  tick.current = (now: number) => {
    raf.current = null;
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    const sim = simRef.current;
    const dt =
      lastFrame.current === 0 ? 16 : Math.min(64, now - lastFrame.current);
    lastFrame.current = now;

    const settling = sim !== null && sim.alpha() > sim.alphaMin();
    if (settling) sim.tick();

    const state = live.current;
    const target = hoverId.current ? 1 : 0;
    if (dim.current !== target) {
      const step = dt / DIM_MS;
      dim.current =
        target > dim.current
          ? Math.min(1, dim.current + step)
          : Math.max(0, dim.current - step);
    }
    const tweening = dim.current !== target;
    /* One array, reused: the director hands back the same buffer every
       frame rather than a fresh concatenation per agent. */
    const travelling = director.current.update(now);
    const queuesBusy = director.current.busy;
    const layers = layersRef.current;
    const still = reducedRef.current;
    const halo =
      !still &&
      ((state.running && state.positionId !== null) ||
        layers.some((layer) => layer.running && layer.positionId !== null));

    draw({
      ctx,
      width: canvas.clientWidth,
      height: canvas.clientHeight,
      transform: transform.current,
      field: state.field,
      neighbours: state.neighbours,
      attention: state.attention,
      currentStep: state.currentStep,
      edited: state.edited,
      positionId: state.positionId,
      running: state.running,
      hoverId: hoverId.current,
      selectedId: state.selectedId,
      matches: state.matches,
      dim: dim.current,
      labels: state.labels,
      signals: travelling,
      workers: layers,
      presence: state.presence,
      focusAgent: state.focusAgent,
      reduced: still,
      now,
    });

    if (settling || tweening || halo || queuesBusy) kick();
    else lastFrame.current = 0;
  };

  useEffect(
    () => () => {
      if (raf.current !== null) cancelAnimationFrame(raf.current);
      raf.current = null;
    },
    [],
  );

  /* A theme change is a repaint: the canvas reads its colours from the same
     custom properties the sheet does, but nothing else would ask it to. */
  useEffect(() => onPalette(kick), [kick]);

  /* Anything the painter reads should land on screen. */
  useEffect(kick, [
    kick,
    props.field,
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

  /* Turning the preference on mid-flight drops whatever was travelling
     rather than letting it finish its arc. */
  useEffect(() => {
    director.current.setReduced(reduced);
    kick();
  }, [reduced, kick]);

  /* ---------------- the simulation ---------------- */

  const fitRef = useRef(camera.fit);
  fitRef.current = camera.fit;

  useEffect(() => {
    if (field.particles.length === 0) {
      simRef.current?.stop();
      simRef.current = null;
      camera.framed.current = false;
      kick();
      return;
    }

    /* A field arrives with positions when it was carried across a refetch or
       restored from the last visit; only a genuinely new one is worth
       settling hard. Continuing one gets just enough alpha to open a gap for
       whatever was added — anything more is the reshuffle we are fixing.
       When *every* particle already has a place there is no gap to open, so
       the picture is left exactly as it was found: that is the case a reload
       hits, and relaxing it again is the drift this is meant to prevent. */
    let carried = 0;
    for (const particle of field.particles) {
      if (particle.x !== undefined && Number.isFinite(particle.x)) carried += 1;
    }
    const complete = carried > 0 && carried === field.particles.length;

    const sim = createSim(field, clusterAnchors(field.clusters));
    simRef.current = sim;
    if (complete) {
      // Below alphaMin, so the render loop never ticks it.
      sim.alpha(0);
    } else if (carried > 0) {
      sim.alpha(GENTLE_ALPHA);
    } else {
      for (let i = 0; i < PRESETTLE; i += 1) sim.tick();
      sim.alpha(RESTART_ALPHA);
    }
    if (!camera.framed.current) fitRef.current();
    kick();

    return () => {
      sim.stop();
    };
  }, [field, kick, camera.framed]);

  /* ---------------- signals ---------------- */

  const walked = useRef({ token: "", length: 0 });

  useEffect(() => {
    const state = walked.current;
    const queue = director.current.queue(PRIMARY_AGENT, PRIMARY_RGB);
    if (state.token !== trailToken || trailIds.length < state.length) {
      queue.clear();
    } else if (trailIds.length > state.length && animate) {
      for (let i = Math.max(1, state.length); i < trailIds.length; i += 1) {
        queue.push(trailIds[i - 1], trailIds[i]);
      }
    }
    walked.current = { token: trailToken, length: trailIds.length };
    kick();
  }, [trailIds, trailToken, animate, kick]);

  /* Every other agent's signals, on the same rule as the primary trail's:
     only new waypoints fire, and a trail that shrank (a card rebuilt from
     a reload) replays without animating. The director does the rest —
     whose turn it is, and how much may be in the air at once. */
  const { workerTrails, animateWorkers } = props;
  useEffect(() => {
    for (const worker of workerTrails) {
      const queue = director.current.queue(worker.id, worker.rgb);
      const seen = workerWalked.current.get(worker.id) ?? 0;
      if (worker.trailIds.length < seen) {
        queue.clear();
      } else if (worker.trailIds.length > seen && animateWorkers) {
        for (let i = Math.max(1, seen); i < worker.trailIds.length; i += 1) {
          queue.push(worker.trailIds[i - 1], worker.trailIds[i]);
        }
      }
      workerWalked.current.set(worker.id, worker.trailIds.length);
    }

    const alive = new Set(workerTrails.map((worker) => worker.id));
    director.current.retain(alive);
    for (const id of [...workerWalked.current.keys()]) {
      if (!alive.has(id)) workerWalked.current.delete(id);
    }
    kick();
  }, [workerTrails, animateWorkers, kick]);

  useEffect(() => {
    if (props.fitToken === 0) return;
    fitRef.current();
  }, [props.fitToken]);

  /* ---------------- particle drag, hover, selection ---------------- */

  const drag = useRef<{ particle: Particle; moved: boolean } | null>(null);
  const emptyDown = useRef<{ x: number; y: number } | null>(null);

  const at = (event: React.PointerEvent | React.MouseEvent) => {
    const native = event.nativeEvent as PointerEvent | MouseEvent;
    return hitTest(
      field.particles,
      transform.current,
      native.offsetX,
      native.offsetY,
    );
  };

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const found = at(event);
    if (!found) {
      emptyDown.current = {
        x: event.nativeEvent.offsetX,
        y: event.nativeEvent.offsetY,
      };
      return;
    }
    emptyDown.current = null;
    capture(event.currentTarget, event.pointerId);
    drag.current = { particle: found, moved: false };
    simRef.current?.alphaTarget(0.25);
    kick();
  };

  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const native = event.nativeEvent;
    const held = drag.current;

    if (held) {
      const [x, y] = transform.current.invert([native.offsetX, native.offsetY]);
      if (
        Math.abs((held.particle.x ?? 0) - x) > CLICK_SLOP ||
        Math.abs((held.particle.y ?? 0) - y) > CLICK_SLOP
      ) {
        held.moved = true;
      }
      held.particle.fx = x;
      held.particle.fy = y;
      kick();
      return;
    }

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
    setHover({
      id: found.id,
      path: found.kind === "dir" ? found.label : found.path,
      size: found.size,
      reads: attention.get(found.id)?.reads ?? 0,
      edit: edited.get(found.id),
      x: native.offsetX,
      y: native.offsetY,
    });
  };

  const onPointerUp = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const held = drag.current;
    drag.current = null;
    simRef.current?.alphaTarget(0);
    if (!held) return;
    release(event.currentTarget, event.pointerId);
    held.particle.fx = null;
    held.particle.fy = null;
    if (!held.moved) onSelect(held.particle.id);
    kick();
  };

  const onClick = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const start = emptyDown.current;
    emptyDown.current = null;
    if (at(event)) return;
    // A pan that ends on empty space is not a click.
    if (
      start &&
      (Math.abs(start.x - event.nativeEvent.offsetX) > CLICK_SLOP ||
        Math.abs(start.y - event.nativeEvent.offsetY) > CLICK_SLOP)
    ) {
      return;
    }
    onSelect(null);
  };

  return (
    <div className="graph" ref={camera.wrapRef}>
      <canvas
        ref={canvasRef}
        className="graph-canvas"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => {
          hoverId.current = null;
          setHover(null);
          kick();
        }}
        onClick={onClick}
        onDoubleClick={(event) => {
          if (!at(event)) camera.fit();
        }}
      />
      <GraphOverlay emptyText={emptyText} hover={hover} size={camera.size} />
    </div>
  );
}
