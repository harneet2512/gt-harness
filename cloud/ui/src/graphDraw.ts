/* ------------------------------------------------------------------ *
 * The painter. One pure pass over the field per frame: filaments, then
 * signals, then particles, then labels. Everything is computed in screen
 * space so hairlines stay hairlines at any zoom.
 * ------------------------------------------------------------------ */

import type { ZoomTransform } from "d3-zoom";
import type { DiffFile } from "./api";
import { hueFill, idOf, type Filament, type ParticleField } from "./graph";
import { endsOf } from "./graphSim";
import { curveOf, pointOn, TAIL, type Curve, type LiveSignal } from "./signals";
import { attentionAlpha, type Attention } from "./trail";

/* The canvas cannot read a CSS variable, so it reads them through
   `palette.ts` — once per theme change, never per frame. */
import { palette } from "./palette";

const GRID_PITCH = 24;

const FILAMENT_ALPHA: Record<string, number> = {
  import: 0.14,
  gt_call: 0.26,
  gt_ref: 0.26,
  gt_import: 0.26,
  cotouch: 0.4,
};

const FILAMENT_WIDTH: Record<string, number> = {
  import: 0.7,
  gt_call: 0.9,
  gt_ref: 0.9,
  gt_import: 0.9,
  cotouch: 0.8,
};

const DIMMED = 0.18;
/** What an agent fades to while another one is focused. */
const FOCUS_DIM = 0.14;
const LABEL_ZOOM = 1.8;
/** A file particle never exceeds r 9 (see `fileRadius`), so with labels off
    only the folded directory particles carry a name of their own. */
const LABEL_MIN_R = 10;
/** Label collision grid, in screen pixels: one row per line of type. */
const LABEL_CELL_W = 26;
const LABEL_CELL_H = 13;
const HALO_MS = 1400;
/**
 * A worker breathes slower than the primary agent and at half the depth.
 * Four of these on screen at once is the case that has to stay calm, and
 * calm is mostly a matter of how slow you are willing to be.
 */
const WORKER_HALO_MS = 2600;
/** How far outside a particle an agent's ring sits, in screen pixels. */
const RING_GAP = 3;
/** The wedge cut out between two agents' arcs, in radians. */
const SLOT_GAP = 0.2;
/** The soft under-glow: radius past the particle, and its strongest alpha. */
const GLOW_SPREAD = 7;
const GLOW_ALPHA = 0.13;
/**
 * Where an agent is, and where it just was — and nowhere else.
 *
 * The rings already carry the whole decaying trail. If the glow decayed on
 * the same curve it would only be a second, blurrier copy of them, and
 * four agents' worth of that is the christmas tree. `attentionAlpha` falls
 * one sixth per step, so two thirds is the last two.
 */
const GLOW_MIN_HEAT = 0.66;

/**
 * One worker's walk through the same field, drawn in its own colour so two
 * agents on one map never read as one. Everything here is keyed by particle
 * id, exactly as the primary trail is.
 */
export interface WorkerLayer {
  id: string;
  /** `r, g, b`. */
  rgb: string;
  attention: ReadonlyMap<string, Attention>;
  /** Steps walked, so the attention decay has a clock of its own. */
  steps: number;
  positionId: string | null;
  running: boolean;
}

export interface DrawInput {
  ctx: CanvasRenderingContext2D;
  width: number;
  height: number;
  transform: ZoomTransform;
  field: ParticleField;
  neighbours: ReadonlyMap<string, ReadonlySet<string>>;
  /** Keyed by particle id, already folded. */
  attention: ReadonlyMap<string, Attention>;
  currentStep: number;
  edited: ReadonlyMap<string, DiffFile>;
  positionId: string | null;
  running: boolean;
  hoverId: string | null;
  selectedId: string | null;
  /** Search result ids; null when the box is empty. */
  matches: ReadonlySet<string> | null;
  /** Hover dim tween, 0 (nothing dimmed) to 1 (fully dimmed). */
  dim: number;
  labels: boolean;
  signals: readonly LiveSignal[];
  /** The worker agents' trails. Empty on a session that spawned none. */
  workers: readonly WorkerLayer[];
  /**
   * Particle id → the agents on it, in trail order. Folded once per
   * render in `useGraphView`: the painter walks it, it never builds it.
   */
  presence: ReadonlyMap<string, readonly string[]>;
  /** The one agent drawn at full strength; every other one fades back. */
  focusAgent: string | null;
  /** `prefers-reduced-motion`: nothing pulses and nothing travels. */
  reduced: boolean;
  now: number;
}

/* Set from the palette at the top of every frame. They are the theme, and
   the painter below reads them exactly as it read the constants they
   replaced. */
let PAPER = "#0f1113";
let GRID = "#1a1d21";
let INK = "#e6e6e6";
let INK_2 = "#8b8f97";
let ORANGE = "217, 119, 87";
let TEAL = "86, 182, 194";

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

export function draw(input: DrawInput): void {
  const { ctx, width, height } = input;
  const skin = palette();
  PAPER = skin.paper;
  GRID = skin.grid;
  INK = skin.ink;
  INK_2 = skin.ink2;
  ORANGE = skin.accent;
  TEAL = skin.change;

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = PAPER;
  ctx.fillRect(0, 0, width, height);
  paintGrid(ctx, width, height);

  if (input.field.particles.length === 0) return;

  paintFilaments(input);
  /* Under the particles, so a particle someone is working in glows from
     beneath rather than being covered up by the news that it does. */
  paintAgentGlow(input);
  paintSignals(input);
  paintParticles(input);
  paintWorkers(input);
  paintLabels(input);
}

/* ---------------- background ---------------- */

let patternFor: CanvasRenderingContext2D | null = null;
let patternInk = "";
let pattern: CanvasPattern | null = null;

function paintGrid(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
): void {
  if (patternFor !== ctx || pattern === null || patternInk !== GRID) {
    patternInk = GRID;
    const tile = document.createElement("canvas");
    tile.width = GRID_PITCH;
    tile.height = GRID_PITCH;
    const tileCtx = tile.getContext("2d");
    if (tileCtx) {
      tileCtx.fillStyle = GRID;
      tileCtx.fillRect(0, 0, 1, 1);
    }
    pattern = ctx.createPattern(tile, "repeat");
    patternFor = ctx;
  }
  if (!pattern) return;
  ctx.fillStyle = pattern;
  ctx.fillRect(0, 0, width, height);
}

/* ---------------- geometry ---------------- */

function screenX(transform: ZoomTransform, x: number): number {
  return transform.x + transform.k * x;
}

function screenY(transform: ZoomTransform, y: number): number {
  return transform.y + transform.k * y;
}

function curveFor(input: DrawInput, filament: Filament): Curve | null {
  const ends = endsOf(filament, input.field);
  if (!ends) return null;
  const [a, b] = ends;
  return curveOf(
    screenX(input.transform, a.x ?? 0),
    screenY(input.transform, a.y ?? 0),
    screenX(input.transform, b.x ?? 0),
    screenY(input.transform, b.y ?? 0),
  );
}

/* ---------------- filaments ---------------- */

function paintFilaments(input: DrawInput): void {
  const { ctx, hoverId, dim, matches } = input;

  for (const filament of input.field.filaments) {
    const a = idOf(filament.source);
    const b = idOf(filament.target);
    const curve = curveFor(input, filament);
    if (!curve) continue;
    if (offscreen(curve, input.width, input.height)) continue;

    const base = FILAMENT_ALPHA[filament.kind] ?? 0.14;
    const touched = hoverId !== null && (a === hoverId || b === hoverId);

    let alpha = touched ? lerp(base, 0.6, dim) : base * lerp(1, DIMMED, dim);
    if (matches && !matches.has(a) && !matches.has(b)) alpha *= DIMMED;
    if (alpha < 0.012) continue;

    const width =
      (FILAMENT_WIDTH[filament.kind] ?? 0.7) +
      Math.min(0.6, Math.max(0, filament.weight - 1) * 0.15);

    ctx.beginPath();
    ctx.moveTo(curve.ax, curve.ay);
    ctx.quadraticCurveTo(curve.cx, curve.cy, curve.bx, curve.by);
    ctx.lineWidth = width;
    ctx.setLineDash(filament.kind === "cotouch" ? [3, 3] : []);
    ctx.strokeStyle =
      filament.kind === "cotouch"
        ? `rgba(${TEAL}, ${alpha.toFixed(3)})`
        : hexAlpha(INK, alpha);
    ctx.stroke();
  }

  ctx.setLineDash([]);
}

function offscreen(curve: Curve, width: number, height: number): boolean {
  const pad = 60;
  const minX = Math.min(curve.ax, curve.bx, curve.cx);
  const maxX = Math.max(curve.ax, curve.bx, curve.cx);
  const minY = Math.min(curve.ay, curve.by, curve.cy);
  const maxY = Math.max(curve.ay, curve.by, curve.cy);
  return maxX < -pad || minX > width + pad || maxY < -pad || minY > height + pad;
}

function hexAlpha(hex: string, alpha: number): string {
  const value = Number.parseInt(hex.replace("#", ""), 16);
  const r = (value >> 16) & 255;
  const g = (value >> 8) & 255;
  const b = value & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`;
}

/* ---------------- signals ---------------- */

function paintSignals(input: DrawInput): void {
  const { ctx } = input;

  for (const signal of input.signals) {
    const from = input.field.byId.get(signal.from);
    const to = input.field.byId.get(signal.to);
    if (!from || !to || from.x === undefined || to.x === undefined) continue;

    const curve = curveOf(
      screenX(input.transform, from.x),
      screenY(input.transform, from.y ?? 0),
      screenX(input.transform, to.x),
      screenY(input.transform, to.y ?? 0),
    );
    if (offscreen(curve, input.width, input.height)) continue;

    const rgb = signal.rgb || ORANGE;
    const focus = agentAlpha(input, signal.agentId);
    if (focus <= 0.02) continue;

    if (signal.still) {
      paintStillSignal(ctx, curve, rgb, signal.fade * focus);
      continue;
    }

    const head = pointOn(curve, signal.progress);
    const tailStart = Math.max(0, signal.progress - TAIL);
    const tail = pointOn(curve, tailStart);

    const gradient = ctx.createLinearGradient(tail[0], tail[1], head[0], head[1]);
    gradient.addColorStop(0, `rgba(${rgb}, 0)`);
    gradient.addColorStop(1, `rgba(${rgb}, ${(0.85 * focus).toFixed(3)})`);

    ctx.beginPath();
    ctx.moveTo(tail[0], tail[1]);
    const steps = 6;
    for (let i = 1; i <= steps; i += 1) {
      const t = tailStart + ((signal.progress - tailStart) * i) / steps;
      const point = pointOn(curve, t);
      ctx.lineTo(point[0], point[1]);
    }
    ctx.strokeStyle = gradient;
    ctx.lineWidth = 1.6;
    ctx.lineCap = "round";
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(head[0], head[1], 2.4, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(${rgb}, ${(0.95 * focus).toFixed(3)})`;
    ctx.fill();
  }

  ctx.lineCap = "butt";
}

/**
 * The same hop, at rest.
 *
 * With `prefers-reduced-motion` a signal says *this agent went from here
 * to there* by lighting the filament and marking the far end, and then
 * leaving. Nothing moves along it, nothing pulses, and the only change
 * over its life is the alpha it goes out on.
 */
function paintStillSignal(
  ctx: CanvasRenderingContext2D,
  curve: Curve,
  rgb: string,
  alpha: number,
): void {
  if (alpha <= 0.02) return;
  ctx.beginPath();
  ctx.moveTo(curve.ax, curve.ay);
  ctx.quadraticCurveTo(curve.cx, curve.cy, curve.bx, curve.by);
  ctx.lineWidth = 1.4;
  ctx.strokeStyle = `rgba(${rgb}, ${(0.5 * alpha).toFixed(3)})`;
  ctx.stroke();

  ctx.beginPath();
  ctx.arc(curve.bx, curve.by, 2.4, 0, Math.PI * 2);
  ctx.fillStyle = `rgba(${rgb}, ${(0.85 * alpha).toFixed(3)})`;
  ctx.fill();
}

/* ---------------- worker trails ---------------- *
 *
 * Two passes, both driven by `presence` — particle id to the agents on it,
 * folded once per render. Walking the particles rather than the agents is
 * what makes a shared particle answerable: by the time anything is drawn
 * we already know how many agents are standing there, so the ring can be
 * split between them instead of one hue silently overdrawing another.
 * --------------------------------------------------------------------- */

/** Agent id → layer, rebuilt only when the layer list itself changes. */
let indexedLayers: readonly WorkerLayer[] | null = null;
const layerIndex = new Map<string, WorkerLayer>();

function layersById(
  workers: readonly WorkerLayer[],
): ReadonlyMap<string, WorkerLayer> {
  if (indexedLayers === workers) return layerIndex;
  layerIndex.clear();
  for (const worker of workers) layerIndex.set(worker.id, worker);
  indexedLayers = workers;
  return layerIndex;
}

/* Scratch for one particle's occupants. Module level and grown in place:
   the hot loop must not allocate, and no more than `MAX_SLOTS` agents ever
   land in here at once. */
const slotLayer: WorkerLayer[] = [];
const slotHeat: number[] = [];
const slotHere: boolean[] = [];
/** Which wedge of the shared ring this occupant owns. See `occupants`. */
const slotAt: number[] = [];

/** How strongly this agent draws while another one has the focus. */
function agentAlpha(input: DrawInput, agentId: string): number {
  if (input.focusAgent === null || input.focusAgent === agentId) return 1;
  return FOCUS_DIM;
}

/**
 * The occupants of one particle worth drawing this frame, into the scratch
 * arrays. Returns how many. An agent whose attention here has decayed to
 * nothing and which has moved on is not an occupant.
 */
function occupants(
  id: string,
  agents: readonly string[],
  index: ReadonlyMap<string, WorkerLayer>,
): number {
  let n = 0;
  for (let slot = 0; slot < agents.length; slot += 1) {
    const layer = index.get(agents[slot]);
    if (!layer) continue;
    const seen = layer.attention.get(id);
    const here = id === layer.positionId;
    const heat = seen ? attentionAlpha(seen.last, layer.steps) : 0;
    if (heat <= 0 && !here) continue;
    slotLayer[n] = layer;
    slotHeat[n] = heat;
    slotHere[n] = here;
    /* The wedge belongs to the agent, not to whoever happens to be lit
       this frame: an agent decaying off a particle must not rotate the
       one beside it into a different quarter of the ring. */
    slotAt[n] = slot;
    n += 1;
  }
  return n;
}

/**
 * A particle under active work, glowing from underneath in the hue of
 * whoever is working in it.
 *
 * Deliberately faint, and deliberately short — this is the line between
 * tasteful and a christmas tree. It is drawn at a tenth of the opacity of
 * everything else on the map, and only where an agent is standing or has
 * just been. It says *someone is in here now*, it goes out as that agent
 * moves on, and it is a different statement from the orange read-flare,
 * which says the primary agent has been here.
 */
function paintAgentGlow(input: DrawInput): void {
  if (input.workers.length === 0 || input.presence.size === 0) return;
  const { ctx } = input;
  const index = layersById(input.workers);

  for (const [id, agents] of input.presence) {
    const particle = input.field.byId.get(id);
    if (!particle || particle.x === undefined || particle.y === undefined) {
      continue;
    }
    const sx = screenX(input.transform, particle.x);
    const sy = screenY(input.transform, particle.y);
    if (sx < -60 || sx > input.width + 60) continue;
    if (sy < -60 || sy > input.height + 60) continue;

    const n = occupants(id, agents, index);
    if (n === 0) continue;

    const r = particle.r * input.transform.k;
    for (let i = 0; i < n; i += 1) {
      const layer = slotLayer[i];
      /* Full strength where the agent is standing, once behind it, then
         nothing: the rings are what carry the rest of the trail. */
      if (!slotHere[i] && slotHeat[i] < GLOW_MIN_HEAT) continue;
      const strength = slotHere[i] ? 1 : slotHeat[i];
      const alpha =
        (GLOW_ALPHA * strength * agentAlpha(input, layer.id)) / Math.max(1, n);
      if (alpha < 0.008) continue;
      /* Two flat discs rather than a gradient: no per-frame gradient
         object, and at these opacities the step is invisible anyway. */
      disc(ctx, sx, sy, r + GLOW_SPREAD, `rgba(${layer.rgb}, ${alpha.toFixed(3)})`);
      disc(
        ctx,
        sx,
        sy,
        r + GLOW_SPREAD * 0.5,
        `rgba(${layer.rgb}, ${alpha.toFixed(3)})`,
      );
    }
  }
}

/**
 * The rings. One agent gets a whole ring; two or more share one, a wedge
 * each, at the same radius — so four agents on a file read as a quartered
 * circle you can count, rather than as four rings creeping outwards or as
 * whichever hue happened to be painted last.
 */
function paintWorkers(input: DrawInput): void {
  if (input.workers.length === 0 || input.presence.size === 0) return;
  const { ctx } = input;
  const index = layersById(input.workers);
  const pulse = input.reduced ? 0 : (input.now % WORKER_HALO_MS) / WORKER_HALO_MS;

  for (const [id, agents] of input.presence) {
    const particle = input.field.byId.get(id);
    if (!particle || particle.x === undefined || particle.y === undefined) {
      continue;
    }
    const sx = screenX(input.transform, particle.x);
    const sy = screenY(input.transform, particle.y);
    if (sx < -40 || sx > input.width + 40) continue;
    if (sy < -40 || sy > input.height + 40) continue;

    const n = occupants(id, agents, index);
    if (n === 0) continue;

    const r = particle.r * input.transform.k + RING_GAP;
    const slots = Math.max(1, agents.length);
    const segment = (Math.PI * 2) / slots;
    const gap = slots === 1 ? 0 : Math.min(SLOT_GAP, segment * 0.18);

    for (let i = 0; i < n; i += 1) {
      const layer = slotLayer[i];
      const here = slotHere[i];
      const alpha =
        (here ? 0.9 : 0.28 + slotHeat[i] * 0.45) * agentAlpha(input, layer.id);
      if (alpha < 0.02) continue;

      /* Slot 0 starts at the top and they run clockwise, so the order on
         screen is the order in the legend. A wedge left empty is an agent
         that has been here and is not here now, which is the truth. */
      const from = -Math.PI / 2 + slotAt[i] * segment + gap / 2;
      const to = from + segment - gap;
      arc(
        ctx,
        sx,
        sy,
        r,
        from,
        to,
        `rgba(${layer.rgb}, ${alpha.toFixed(3)})`,
        here ? 1.9 : 1.25,
      );

      /* Breathing, once, slowly, and only where an agent actually is. */
      if (here && layer.running && !input.reduced) {
        arc(
          ctx,
          sx,
          sy,
          r + pulse * 5,
          from,
          to,
          `rgba(${layer.rgb}, ${(0.34 * (1 - pulse) * agentAlpha(input, layer.id)).toFixed(3)})`,
          1.1,
        );
      }
    }
  }
}

/* ---------------- particles ---------------- */

function paintParticles(input: DrawInput): void {
  const { ctx, hoverId, dim, matches } = input;
  const near = hoverId ? input.neighbours.get(hoverId) : undefined;
  const halo =
    input.running && !input.reduced ? (input.now % HALO_MS) / HALO_MS : null;

  for (const particle of input.field.particles) {
    if (particle.x === undefined || particle.y === undefined) continue;
    const sx = screenX(input.transform, particle.x);
    const sy = screenY(input.transform, particle.y);
    const edit = input.edited.get(particle.id);
    const isPosition = particle.id === input.positionId;

    let r = particle.r * input.transform.k;
    if (edit) r *= 1.2;
    if (isPosition) r += 2;

    if (sx < -r - 20 || sx > input.width + r + 20) continue;
    if (sy < -r - 20 || sy > input.height + r + 20) continue;

    const related =
      hoverId === null ||
      particle.id === hoverId ||
      near?.has(particle.id) === true;
    let alpha = related ? 1 : lerp(1, DIMMED, dim);
    if (matches && !matches.has(particle.id)) alpha *= DIMMED;

    ctx.globalAlpha = alpha;

    /* fill */
    ctx.beginPath();
    ctx.arc(sx, sy, r, 0, Math.PI * 2);
    ctx.fillStyle = edit
      ? `rgba(${TEAL}, 0.55)`
      : isPosition
        ? `rgba(${ORANGE}, 0.9)`
        : hueFill(particle.hue);
    ctx.fill();

    /* read flare, decaying over the last few steps */
    const seen = input.attention.get(particle.id);
    const heat = seen ? attentionAlpha(seen.last, input.currentStep) : 0;
    if (heat > 0 && !edit && !isPosition) {
      ctx.fillStyle = `rgba(${ORANGE}, ${(heat * 0.75).toFixed(3)})`;
      ctx.fill();
    }

    /* outline */
    ctx.lineWidth = 0.75;
    ctx.strokeStyle = hexAlpha(INK, 0.35);
    ctx.stroke();

    /* rings */
    if (isPosition) {
      ring(ctx, sx, sy, r + 2.5, `rgba(${ORANGE}, 0.95)`, 2);
      if (halo !== null) {
        ring(
          ctx,
          sx,
          sy,
          r * (1 + 1.2 * halo),
          `rgba(${ORANGE}, ${(0.5 * (1 - halo)).toFixed(3)})`,
          1.25,
        );
      }
    } else if (edit) {
      ring(ctx, sx, sy, r + 2.5, `rgba(${TEAL}, 0.75)`, 1.25);
      if (edit.status === "added") {
        ctx.beginPath();
        ctx.arc(sx, sy - r - 5, 1.9, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${TEAL}, 0.9)`;
        ctx.fill();
      }
    } else if (heat > 0) {
      ring(
        ctx,
        sx,
        sy,
        r + 3,
        `rgba(${ORANGE}, ${(0.35 + heat * 0.5).toFixed(3)})`,
        1.25,
      );
    }

    if (particle.id === input.selectedId) {
      ring(ctx, sx, sy, r + 5, hexAlpha(INK, 0.55), 1);
    }
  }

  ctx.globalAlpha = 1;
}

function ring(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  stroke: string,
  width: number,
): void {
  ctx.beginPath();
  ctx.arc(x, y, Math.max(0.5, r), 0, Math.PI * 2);
  ctx.lineWidth = width;
  ctx.strokeStyle = stroke;
  ctx.stroke();
}

/** One agent's wedge of a shared ring. */
function arc(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  from: number,
  to: number,
  stroke: string,
  width: number,
): void {
  ctx.beginPath();
  ctx.arc(x, y, Math.max(0.5, r), from, to);
  ctx.lineWidth = width;
  ctx.strokeStyle = stroke;
  ctx.stroke();
}

function disc(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  fill: string,
): void {
  ctx.beginPath();
  ctx.arc(x, y, Math.max(0.5, r), 0, Math.PI * 2);
  ctx.fillStyle = fill;
  ctx.fill();
}

/* ---------------- labels ---------------- */

function paintLabels(input: DrawInput): void {
  const { ctx, transform } = input;
  const all = input.labels || transform.k > LABEL_ZOOM;

  ctx.font = '500 10.5px "JetBrains Mono", ui-monospace, SFMono-Regular, monospace';
  ctx.textAlign = "center";
  ctx.textBaseline = "top";

  /* Overlapping names read as a smear, so a label claims the cells it covers
     and a later one that would land on top of it is dropped. The particles the
     reader is pointing at are laid down first, so they always win. */
  const taken = new Set<string>();
  const claim = (sx: number, sy: number, width: number): boolean => {
    const from = Math.floor((sx - width / 2) / LABEL_CELL_W);
    const to = Math.floor((sx + width / 2) / LABEL_CELL_W);
    const row = Math.floor(sy / LABEL_CELL_H);
    for (let col = from; col <= to; col += 1) {
      if (taken.has(`${col}:${row}`)) return false;
    }
    for (let col = from; col <= to; col += 1) taken.add(`${col}:${row}`);
    return true;
  };

  const isMarked = (id: string): boolean =>
    id === input.hoverId || id === input.selectedId || id === input.positionId;

  const ordered = [...input.field.particles].sort(
    (a, b) => Number(isMarked(b.id)) - Number(isMarked(a.id)),
  );

  for (const particle of ordered) {
    if (particle.x === undefined || particle.y === undefined) continue;
    const marked = isMarked(particle.id);
    if (!all && !marked && particle.r < LABEL_MIN_R) continue;

    const sx = screenX(transform, particle.x);
    const sy = screenY(transform, particle.y);
    if (sx < -80 || sx > input.width + 80) continue;
    if (sy < -20 || sy > input.height + 20) continue;

    const r = particle.r * transform.k;
    const top = sy + r + 4;
    if (!claim(sx, top, ctx.measureText(particle.label).width)) continue;

    ctx.fillStyle = marked ? INK : INK_2;
    ctx.globalAlpha = marked ? 1 : 0.85;
    ctx.fillText(particle.label, sx, top);
  }

  ctx.globalAlpha = 1;
}
