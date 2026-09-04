import { useCallback, useEffect, useRef, useState } from "react";
import type { Simulation } from "d3-force";
import type { DiffFile } from "../api";
import type { Filament, Particle, ParticleField } from "../graph";
import { draw } from "../graphDraw";
import { clusterAnchors, createSim, hitTest, RESTART_ALPHA } from "../graphSim";
import { SignalQueue } from "../signals";
import type { Attention } from "../trail";
import { useGraphCamera } from "../useGraphCamera";
import GraphOverlay, { type HoverInfo } from "./GraphOverlay";

const DIM_MS = 120;
const CLICK_SLOP = 3;
/** Ticks run before the first paint so the field arrives already legible. */
const PRESETTLE = 90;

interface Props {
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
  /** Incremented by the toolbar to ask for a fit. */
  fitToken: number;
  onZoom: (k: number) => void;
  emptyText: string | null;
}

/** The living particle graph: files as particles, relations as filaments. */
export default function GraphCanvas(props: Props) {
  const { field, attention, edited, onSelect, onZoom, emptyText } = props;
  const { trailIds, trailToken, animate } = props;
  const [hover, setHover] = useState<HoverInfo | null>(null);

  /* Everything the render loop reads lives in a ref: a pan or a signal must
     never cost a React render, and the loop must see the newest props. */
  const live = useRef(props);
  live.current = props;

  const simRef = useRef<Simulation<Particle, Filament> | null>(null);
  const signals = useRef(new SignalQueue());
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
  const camera = useGraphCamera(getParticles, onZoom, kick);
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
    const travelling = signals.current.update(now);
    const halo = state.running && state.positionId !== null;

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
      now,
    });

    if (settling || tweening || halo || signals.current.busy) kick();
    else lastFrame.current = 0;
  };

  useEffect(
    () => () => {
      if (raf.current !== null) cancelAnimationFrame(raf.current);
      raf.current = null;
    },
    [],
  );

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
  ]);

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

    const sim = createSim(field, clusterAnchors(field.clusters));
    simRef.current = sim;
    for (let i = 0; i < PRESETTLE; i += 1) sim.tick();
    sim.alpha(RESTART_ALPHA);
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
    if (state.token !== trailToken || trailIds.length < state.length) {
      signals.current.clear();
    } else if (trailIds.length > state.length && animate) {
      for (let i = Math.max(1, state.length); i < trailIds.length; i += 1) {
        signals.current.push(trailIds[i - 1], trailIds[i]);
      }
    }
    walked.current = { token: trailToken, length: trailIds.length };
    kick();
  }, [trailIds, trailToken, animate, kick]);

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
    event.currentTarget.setPointerCapture(event.pointerId);
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
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
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
