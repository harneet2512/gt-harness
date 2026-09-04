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

const PAPER = "#FAFAF8";
const GRID = "#E9E7E1";
const INK = "#1A1B1E";
const INK_2 = "#5B5D63";
const ORANGE = "240, 102, 47";
const TEAL = "21, 154, 135";
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
const LABEL_ZOOM = 1.8;
const LABEL_MIN_R = 6;
const HALO_MS = 1400;

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
  now: number;
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

export function draw(input: DrawInput): void {
  const { ctx, width, height } = input;

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = PAPER;
  ctx.fillRect(0, 0, width, height);
  paintGrid(ctx, width, height);

  if (input.field.particles.length === 0) return;

  paintFilaments(input);
  paintSignals(input);
  paintParticles(input);
  paintLabels(input);
}

/* ---------------- background ---------------- */

let patternFor: CanvasRenderingContext2D | null = null;
let pattern: CanvasPattern | null = null;

function paintGrid(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
): void {
  if (patternFor !== ctx || pattern === null) {
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
  const value = Number.parseInt(hex.slice(1), 16);
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

    const head = pointOn(curve, signal.progress);
    const tailStart = Math.max(0, signal.progress - TAIL);
    const tail = pointOn(curve, tailStart);

    const gradient = ctx.createLinearGradient(tail[0], tail[1], head[0], head[1]);
    gradient.addColorStop(0, `rgba(${ORANGE}, 0)`);
    gradient.addColorStop(1, `rgba(${ORANGE}, 0.85)`);

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
    ctx.fillStyle = `rgba(${ORANGE}, 0.95)`;
    ctx.fill();
  }

  ctx.lineCap = "butt";
}

/* ---------------- particles ---------------- */

function paintParticles(input: DrawInput): void {
  const { ctx, hoverId, dim, matches } = input;
  const near = hoverId ? input.neighbours.get(hoverId) : undefined;
  const halo = input.running ? (input.now % HALO_MS) / HALO_MS : null;

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

/* ---------------- labels ---------------- */

function paintLabels(input: DrawInput): void {
  const { ctx, transform } = input;
  const all = input.labels || transform.k > LABEL_ZOOM;

  ctx.font = '500 10.5px "JetBrains Mono", ui-monospace, SFMono-Regular, monospace';
  ctx.textAlign = "center";
  ctx.textBaseline = "top";

  for (const particle of input.field.particles) {
    if (particle.x === undefined || particle.y === undefined) continue;
    const marked =
      particle.id === input.hoverId ||
      particle.id === input.selectedId ||
      particle.id === input.positionId;
    if (!all && !marked && particle.r < LABEL_MIN_R) continue;

    const sx = screenX(transform, particle.x);
    const sy = screenY(transform, particle.y);
    if (sx < -80 || sx > input.width + 80) continue;
    if (sy < -20 || sy > input.height + 20) continue;

    const r = particle.r * transform.k;
    ctx.fillStyle = marked ? INK : INK_2;
    ctx.globalAlpha = marked ? 1 : 0.85;
    ctx.fillText(particle.label, sx, sy + r + 4);
  }

  ctx.globalAlpha = 1;
}
