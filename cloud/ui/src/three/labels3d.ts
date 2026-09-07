/* ------------------------------------------------------------------ *
 * The names, on a flat canvas over the field.
 *
 * Text in a 3D scene is either a texture atlas or a mesh per glyph, and
 * both are the wrong trade here: the labels have to stay upright, stay
 * crisp, and stay the same size as the ones the flat view draws — which
 * is to say they have to be exactly what a 2D canvas already gives, laid
 * over the picture and driven by the same projection the hit test uses.
 *
 * The collision rule is the flat painter's: a name claims the cells it
 * covers and a later one that would land on top of it is dropped. What is
 * new is the order — near to far, so when two files overlap it is the one
 * in front that keeps its name, which is also the one drawn on top.
 * ------------------------------------------------------------------ */

import { PROJECT_STRIDE, type Node3D } from "../graph3d";
import type { ParticleField } from "../graph";

/** Matches the flat painter's grid: one row per line of type. */
const CELL_W = 26;
const CELL_H = 13;
/** With labels off, only the folded directory particles carry a name. */
const LABEL_MIN_R = 10;
/** Below this many pixels across, a name is bigger than the thing it names. */
const LABEL_MIN_PX = 2.2;
/** A name in the far half of the field is not a name anyone can use. */
const LABEL_FADE = 0.45;

export interface LabelInput {
  ctx: CanvasRenderingContext2D;
  width: number;
  height: number;
  field: ParticleField;
  nodes: readonly Node3D[];
  /** `PROJECT_STRIDE` floats per node: x, y, depth, screen radius. */
  screen: Float32Array;
  /** Node indices, far to near — the order the particles were drawn in. */
  order: Uint32Array;
  count: number;
  labels: boolean;
  hoverId: string | null;
  selectedId: string | null;
  positionId: string | null;
  /** Where the fade into the paper begins and ends, in view units. */
  fogNear: number;
  fogFar: number;
  ink: string;
  ink2: string;
}

export function paintLabels3d(input: LabelInput): void {
  const { ctx, width, height, screen, order, count } = input;
  ctx.clearRect(0, 0, width, height);
  if (count === 0) return;

  ctx.font =
    '500 10.5px "JetBrains Mono", ui-monospace, SFMono-Regular, monospace';
  ctx.textAlign = "center";
  ctx.textBaseline = "top";

  const taken = new Set<string>();
  const claim = (sx: number, sy: number, w: number): boolean => {
    const from = Math.floor((sx - w / 2) / CELL_W);
    const to = Math.floor((sx + w / 2) / CELL_W);
    const row = Math.floor(sy / CELL_H);
    for (let col = from; col <= to; col += 1) {
      if (taken.has(`${col}:${row}`)) return false;
    }
    for (let col = from; col <= to; col += 1) taken.add(`${col}:${row}`);
    return true;
  };

  const marked = (id: string): boolean =>
    id === input.hoverId || id === input.selectedId || id === input.positionId;

  const span = Math.max(1, input.fogFar - input.fogNear);

  /* Two passes rather than a sort: the three particles the reader is
     pointing at always get their names, and then everything else takes
     what is left, nearest first. `order` is far to near, so it is walked
     backwards. */
  for (let pass = 0; pass < 2; pass += 1) {
    for (let k = count - 1; k >= 0; k -= 1) {
      const i = order[k];
      const node = input.nodes[i];
      if (!node) continue;
      const isMarked = marked(node.id);
      if (pass === 0 ? !isMarked : isMarked) continue;

      const at = i * PROJECT_STRIDE;
      const depth = screen[at + 2];
      if (depth <= 0) continue;
      const sx = screen[at];
      const sy = screen[at + 1];
      if (sx < -80 || sx > width + 80) continue;
      if (sy < -20 || sy > height + 20) continue;

      const r = screen[at + 3];
      if (!isMarked) {
        if (!input.labels && node.r < LABEL_MIN_R) continue;
        if (r < LABEL_MIN_PX) continue;
      }

      const particle = input.field.byId.get(node.id);
      if (!particle) continue;

      const top = sy + r + 4;
      const text = particle.label;
      if (!claim(sx, top, ctx.measureText(text).width)) continue;

      /* The same recession the shaders give the particles: a name in the
         back of the field is dimmer, not absent. */
      const away = Math.min(1, Math.max(0, (depth - input.fogNear) / span));
      ctx.fillStyle = isMarked ? input.ink : input.ink2;
      ctx.globalAlpha = isMarked ? 1 : 0.85 * (1 - away * LABEL_FADE);
      ctx.fillText(text, sx, top);
    }
  }

  ctx.globalAlpha = 1;
}
