/* ------------------------------------------------------------------ *
 * The field, in three dimensions.
 *
 * Nothing here knows about three.js, and nothing here fetches anything.
 * It reads the same `ParticleField` the 2D canvas draws — the same
 * particles, the same filaments — and answers three questions the flat
 * view never has to:
 *
 *   · where does a directory sit in space (`lobeAnchors`), so the regions
 *     read as lobes rather than as one cloud;
 *   · what path does a relation take between two of them (`controlOf`),
 *     so the hundred edges between `src/` and `tests/` read as one tract
 *     instead of a hundred chords through the middle of everything;
 *   · which particle is under the pointer (`project3d`, `hitTest3d`),
 *     which is the same question `hitTest` answers in 2D and is answered
 *     the same way — by projecting, not by ray-casting geometry that is
 *     billboarded and would lie about where it is.
 *
 * Every position is derived deterministically: a golden angle and a hash
 * of the particle's own id, never `Math.random`. Two builds of the same
 * field are the same picture, which is what makes it testable and what
 * stops a refetch from reshuffling the map under the reader.
 * ------------------------------------------------------------------ */

import { idOf, type FilamentKind, type ParticleField } from "./graph";

export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

/**
 * One particle, in space. Deliberately *not* a `Particle`: d3-force writes
 * positions in place, and the flat layout is persisted and is what the 2D
 * view opens on. Sharing the objects would mean orbiting the 3D view
 * quietly rewrote the 2D one.
 */
export interface Node3D {
  id: string;
  /** The radius the 2D view already chose. Depth must not resize files. */
  r: number;
  cluster: string;
  hue: number;
  x: number;
  y: number;
  z: number;
  vx?: number;
  vy?: number;
  vz?: number;
  fx?: number | null;
  fy?: number | null;
  fz?: number | null;
  index?: number;
}

/** A filament, re-expressed for the 3D solver. Same ends, same kind. */
export interface Link3D {
  source: string | Node3D;
  target: string | Node3D;
  kind: FilamentKind;
  weight: number;
  /** True when the two ends live in different directories. */
  across: boolean;
  /** Points sampled along this link's arc. Fixed, so buffers are stable. */
  samples: number;
  /**
   * Every other segment is left out, which is how a co-touch relation
   * keeps the dashed line the flat view gives it. A line renderer has no
   * dash pattern worth having, but this arc is sampled by hand anyway, so
   * the gaps cost nothing but the vertices they save.
   */
  dashed: boolean;
}

export interface Scene3D {
  nodes: Node3D[];
  links: Link3D[];
  byId: Map<string, Node3D>;
  /** Directory to the centre of its lobe. */
  anchors: Map<string, Vec3>;
  /**
   * `a b` to the point every relation between those two lobes bends
   * through. This is what turns N chords into one tract.
   */
  waists: Map<string, Vec3>;
  /** The field this was built from; a different one is a different scene. */
  signature: string;
  /** Total line vertices the edge geometry needs. */
  edgeVertices: number;
}

export const EMPTY_SCENE: Scene3D = {
  nodes: [],
  links: [],
  byId: new Map(),
  anchors: new Map(),
  waists: new Map(),
  signature: "",
  edgeVertices: 0,
};

/* ------------------------------------------------------------------ *
 * Lobes
 * ------------------------------------------------------------------ */

/** Golden angle. The same constant `graphSim.seed` spreads with. */
const GOLDEN = 2.399963229728653;

/**
 * How far the lobes sit from the origin. Grows with the number of
 * directories rather than with the viewport, exactly as the 2D ring does,
 * so the layout is stable and framing is the camera's job.
 */
export function lobeRadius(count: number): number {
  return 80 + 22 * count;
}

/**
 * Directory to lobe centre, spread evenly over a sphere.
 *
 * A Fibonacci sphere rather than a ring: a ring in 3D is a 2D layout that
 * happens to be tilted, and the whole point of the depth is that a
 * repository with nine top-level directories has somewhere to put the
 * ninth. Evenly spread also means the gaps between lobes are the same
 * size, which is what makes a tract between two of them legible.
 */
export function lobeAnchors(clusters: readonly string[]): Map<string, Vec3> {
  const out = new Map<string, Vec3>();
  const n = clusters.length;
  if (n === 0) return out;
  if (n === 1) {
    out.set(clusters[0], { x: 0, y: 0, z: 0 });
    return out;
  }

  const radius = lobeRadius(n);
  for (let i = 0; i < n; i += 1) {
    /* Offset by a half step so neither the first nor the last lobe lands
       exactly on a pole, where the neighbours crowd. */
    const y = 1 - (2 * i + 1) / n;
    const ring = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = GOLDEN * i;
    out.set(clusters[i], {
      x: Math.cos(theta) * ring * radius,
      y: y * radius,
      z: Math.sin(theta) * ring * radius,
    });
  }
  return out;
}

/* ------------------------------------------------------------------ *
 * Seeding
 * ------------------------------------------------------------------ */

/** FNV-1a, folded to [0, 1). Stable for an id across builds and reloads. */
export function hash01(id: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < id.length; i += 1) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return ((h >>> 0) % 100000) / 100000;
}

/** How deep a lobe is, relative to how wide the 2D layout made it. */
const DEPTH = 0.7;

/**
 * Where a particle starts.
 *
 * The 2D view has usually already settled this field, and that settled
 * arrangement is the picture the reader knows. So the seed keeps it: a
 * particle's offset from its flat cluster anchor is carried over as the
 * offset from its lobe centre, and only the third axis is invented. The
 * result is that switching to 3D looks like the same map gaining depth
 * rather than like a different map, and the solver starts near-solved.
 *
 * A particle the flat view never placed falls back to a golden-angle
 * spread around its lobe, which is what `graphSim.seed` does.
 */
export function seedNode(
  node: Node3D,
  anchor: Vec3,
  flat: { x?: number; y?: number } | undefined,
  flatAnchor: { x: number; y: number } | undefined,
  index: number,
): void {
  const spin = hash01(node.id);
  const placed =
    flat !== undefined &&
    flat.x !== undefined &&
    flat.y !== undefined &&
    Number.isFinite(flat.x) &&
    Number.isFinite(flat.y);

  if (placed) {
    const dx = (flat.x as number) - (flatAnchor?.x ?? 0);
    const dy = (flat.y as number) - (flatAnchor?.y ?? 0);
    node.x = anchor.x + dx;
    node.y = anchor.y + dy;
    /* The one axis the flat layout has no opinion about. Scaled to how
       far the particle already sits from its own lobe centre, so a dense
       core stays a core and only the outskirts gain depth. */
    const reach = Math.hypot(dx, dy);
    node.z = anchor.z + (spin - 0.5) * 2 * DEPTH * (10 + reach);
  } else {
    const angle = index * GOLDEN;
    const spread = 8 + Math.sqrt(index) * 4;
    node.x = anchor.x + Math.cos(angle) * spread;
    node.y = anchor.y + Math.sin(angle) * spread;
    node.z = anchor.z + (spin - 0.5) * 2 * spread * DEPTH;
  }
  node.vx = 0;
  node.vy = 0;
  node.vz = 0;
}

/* ------------------------------------------------------------------ *
 * Tracts
 * ------------------------------------------------------------------ */

/** How hard an edge between two lobes is pulled onto the shared arc. */
export const BUNDLE = 0.72;
/** How far a tract stands off the straight line between two lobes. */
const LIFT = 0.34;
/** A relation inside one lobe only needs enough bow to be distinguishable. */
const BOW = 0.1;

export function pairOf(a: string, b: string): string {
  return a < b ? `${a} ${b}` : `${b} ${a}`;
}

function norm(v: Vec3): number {
  return Math.hypot(v.x, v.y, v.z);
}

/**
 * The point every relation between two lobes bends through.
 *
 * Halfway between the two lobe centres, then pushed off that line. Two
 * lobes on opposite sides of the field have a midpoint at the origin —
 * the straight run through the middle of everything, which is the failure
 * mode — so those are pushed out along the perpendicular instead, and the
 * tract arcs around the field rather than through it.
 *
 * The offset is a function of the two lobes only, so every relation
 * between them leans the same way and by the same amount: that is what
 * makes them read as one bundle rather than as N lines that happen to be
 * near each other.
 */
export function waistOf(a: Vec3, b: Vec3): Vec3 {
  const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, z: (a.z + b.z) / 2 };
  const span = Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
  const lift = span * LIFT;
  const out = norm(mid);

  if (out > span * 0.12) {
    /* Off-centre already: push further out, away from the origin. */
    const k = (out + lift) / out;
    return { x: mid.x * k, y: mid.y * k, z: mid.z * k };
  }

  /* Near-antipodal lobes. Bend along the cross product, which is
     perpendicular to both and is defined by the two lobes alone, so the
     tract is stable however the field is rebuilt. */
  const cx = a.y * b.z - a.z * b.y;
  const cy = a.z * b.x - a.x * b.z;
  const cz = a.x * b.y - a.y * b.x;
  const len = Math.hypot(cx, cy, cz);
  if (len < 1e-6) return { x: mid.x, y: mid.y + lift, z: mid.z };
  const k = lift / len;
  return { x: mid.x + cx * k, y: mid.y + cy * k, z: mid.z + cz * k };
}

/**
 * The control point of one relation's arc.
 *
 * Within a lobe: a light bow off the midpoint, so two files joined both
 * ways do not draw as one line. Between lobes: most of the way onto the
 * shared waist, which is what gathers them into a tract while each end
 * still lands on the file it actually belongs to.
 */
export function controlOf(
  scene: Scene3D,
  a: Node3D,
  b: Node3D,
  across: boolean,
  out: Vec3,
): Vec3 {
  const mx = (a.x + b.x) / 2;
  const my = (a.y + b.y) / 2;
  const mz = (a.z + b.z) / 2;

  if (!across) {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const dz = b.z - a.z;
    const len = Math.hypot(dx, dy, dz) || 1;
    /* Perpendicular to the chord, leaning the same way every time so a
       filament does not flip as the layout settles. */
    let px = -dy;
    let py = dx;
    let pz = dz * 0.35;
    const plen = Math.hypot(px, py, pz) || 1;
    const k = (len * BOW) / plen;
    px *= k;
    py *= k;
    pz *= k;
    out.x = mx + px;
    out.y = my + py;
    out.z = mz + pz;
    return out;
  }

  const waist = scene.waists.get(pairOf(a.cluster, b.cluster));
  if (!waist) {
    out.x = mx;
    out.y = my;
    out.z = mz;
    return out;
  }
  out.x = mx + (waist.x - mx) * BUNDLE;
  out.y = my + (waist.y - my) * BUNDLE;
  out.z = mz + (waist.z - mz) * BUNDLE;
  return out;
}

/** A point on the quadratic through `a`, `control`, `b`. */
export function pointOn3d(
  a: Vec3,
  c: Vec3,
  b: Vec3,
  t: number,
  out: Vec3,
): Vec3 {
  const u = 1 - t;
  const uu = u * u;
  const ut2 = 2 * u * t;
  const tt = t * t;
  out.x = uu * a.x + ut2 * c.x + tt * b.x;
  out.y = uu * a.y + ut2 * c.y + tt * b.y;
  out.z = uu * a.z + ut2 * c.z + tt * b.z;
  return out;
}

/** A relation inside a lobe needs few points; an arc between lobes more. */
export const SAMPLES_NEAR = 5;
export const SAMPLES_ACROSS = 13;

/** How many of a link's segments are actually drawn. Dashes skip the odd ones. */
export function drawnSegments(samples: number, dashed: boolean): number {
  const all = Math.max(0, samples - 1);
  return dashed ? Math.ceil(all / 2) : all;
}

/* ------------------------------------------------------------------ *
 * Building the scene
 * ------------------------------------------------------------------ */

/**
 * The 3D scene for a field.
 *
 * `flatAnchors` is where the 2D simulation puts each cluster — passing it
 * lets the seed carry the flat arrangement over (see `seedNode`). A
 * `previous` scene for the same particles hands its positions on, so a
 * refetch does not throw the depth away any more than it throws the flat
 * layout away.
 */
export function buildScene3d(
  field: ParticleField,
  flatAnchors: ReadonlyMap<string, { x: number; y: number }>,
  previous?: Scene3D | null,
): Scene3D {
  if (field.particles.length === 0) return EMPTY_SCENE;

  const anchors = lobeAnchors(field.clusters);
  const nodes: Node3D[] = [];
  const byId = new Map<string, Node3D>();

  field.particles.forEach((particle, i) => {
    const node: Node3D = {
      id: particle.id,
      r: particle.r,
      cluster: particle.cluster,
      hue: particle.hue,
      x: 0,
      y: 0,
      z: 0,
    };
    const before = previous?.byId.get(particle.id);
    if (before && Number.isFinite(before.x)) {
      node.x = before.x;
      node.y = before.y;
      node.z = before.z;
      node.vx = before.vx ?? 0;
      node.vy = before.vy ?? 0;
      node.vz = before.vz ?? 0;
    } else {
      seedNode(
        node,
        anchors.get(particle.cluster) ?? { x: 0, y: 0, z: 0 },
        particle,
        flatAnchors.get(particle.cluster),
        i,
      );
    }
    nodes.push(node);
    byId.set(node.id, node);
  });

  const waists = new Map<string, Vec3>();
  const links: Link3D[] = [];
  let edgeVertices = 0;

  for (const filament of field.filaments) {
    const a = byId.get(idOf(filament.source));
    const b = byId.get(idOf(filament.target));
    if (!a || !b) continue;
    const across = a.cluster !== b.cluster;
    if (across) {
      const key = pairOf(a.cluster, b.cluster);
      if (!waists.has(key)) {
        waists.set(
          key,
          waistOf(
            anchors.get(a.cluster) ?? { x: 0, y: 0, z: 0 },
            anchors.get(b.cluster) ?? { x: 0, y: 0, z: 0 },
          ),
        );
      }
    }
    const samples = across ? SAMPLES_ACROSS : SAMPLES_NEAR;
    const dashed = filament.kind === "cotouch";
    links.push({
      source: a.id,
      target: b.id,
      kind: filament.kind,
      weight: filament.weight,
      across,
      samples,
      dashed,
    });
    /* A polyline of `samples` points is `samples - 1` segments, and the
       line geometry wants both ends of each. A dashed one draws half. */
    edgeVertices += drawnSegments(samples, dashed) * 2;
  }

  return {
    nodes,
    links,
    byId,
    anchors,
    waists,
    signature: field.signature,
    edgeVertices,
  };
}

/* ------------------------------------------------------------------ *
 * A one-slot cache, so switching modes is not a re-settle.
 *
 * Leaving 3D unmounts the renderer and everything it owns. The *layout*
 * is not the renderer's, though — it is as expensive to earn as the flat
 * one, which is why that one is written to storage. One slot is enough:
 * the only scene anyone ever comes back to is the one they just left.
 * ------------------------------------------------------------------ */

let remembered: Scene3D | null = null;

export function rememberScene(scene: Scene3D): void {
  remembered = scene.nodes.length > 0 ? scene : null;
}

export function recallScene(signature: string): Scene3D | null {
  if (!remembered || remembered.signature !== signature) return null;
  return remembered;
}

export function forgetScene(): void {
  remembered = null;
}

/* ------------------------------------------------------------------ *
 * Framing, projection and hit testing
 * ------------------------------------------------------------------ */

export interface Sphere {
  x: number;
  y: number;
  z: number;
  r: number;
}

/** The ball that holds every particle, particle radii included. */
export function bounds3d(nodes: readonly Node3D[]): Sphere | null {
  if (nodes.length === 0) return null;
  let minX = Infinity;
  let minY = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  let maxZ = -Infinity;

  for (const node of nodes) {
    if (!Number.isFinite(node.x)) continue;
    minX = Math.min(minX, node.x - node.r);
    minY = Math.min(minY, node.y - node.r);
    minZ = Math.min(minZ, node.z - node.r);
    maxX = Math.max(maxX, node.x + node.r);
    maxY = Math.max(maxY, node.y + node.r);
    maxZ = Math.max(maxZ, node.z + node.r);
  }
  if (!Number.isFinite(minX)) return null;

  const x = (minX + maxX) / 2;
  const y = (minY + maxY) / 2;
  const z = (minZ + maxZ) / 2;
  let r = 0;
  for (const node of nodes) {
    if (!Number.isFinite(node.x)) continue;
    r = Math.max(r, Math.hypot(node.x - x, node.y - y, node.z - z) + node.r);
  }
  return { x, y, z, r: Math.max(1, r) };
}

/** Padding around the field when it is framed, as a fraction of radius. */
const FIT_PAD = 1.24;

/**
 * How far back the camera has to stand to hold a ball of `radius` in a
 * `fov`-degree frustum at `aspect`. The narrower of the two axes decides,
 * which is why a tall thin panel frames the field rather than cropping it.
 */
export function fitDistance(
  radius: number,
  fovDegrees: number,
  aspect: number,
): number {
  const fov = (Math.max(1, fovDegrees) * Math.PI) / 180;
  const vertical = radius / Math.sin(fov / 2);
  const hFov = 2 * Math.atan(Math.tan(fov / 2) * Math.max(0.05, aspect));
  const horizontal = radius / Math.sin(hFov / 2);
  return Math.max(vertical, horizontal) * FIT_PAD;
}

/**
 * The direction the camera is put in on a fit. Slightly above and to the
 * side of straight-on: enough that the field is visibly a volume on the
 * first frame, not so much that anyone has to reorient to read it.
 */
export const FIT_DIRECTION: Vec3 = { x: 0.34, y: 0.26, z: 1 };

/** Floats per projected particle: screen x, screen y, view depth, radius. */
export const PROJECT_STRIDE = 4;

/**
 * Project every node to screen space with one view-projection matrix.
 *
 * Column-major, the order three.js stores a `Matrix4` in. Nothing is
 * allocated: the caller owns `out` and it is `PROJECT_STRIDE` floats per
 * node. A node behind the camera gets a depth of `-1` and screen
 * coordinates far off the panel, so it never wins a hit test and is never
 * labelled.
 */
export function project3d(
  m: ArrayLike<number>,
  nodes: readonly Node3D[],
  width: number,
  height: number,
  out: Float32Array,
): void {
  const halfW = width / 2;
  const halfH = height / 2;

  for (let i = 0; i < nodes.length; i += 1) {
    const node = nodes[i];
    const at = i * PROJECT_STRIDE;
    const x = node.x;
    const y = node.y;
    const z = node.z;

    const w = m[3] * x + m[7] * y + m[11] * z + m[15];
    if (!(w > 1e-6)) {
      out[at] = -1e6;
      out[at + 1] = -1e6;
      out[at + 2] = -1;
      out[at + 3] = 0;
      continue;
    }
    const cx = m[0] * x + m[4] * y + m[8] * z + m[12];
    const cy = m[1] * x + m[5] * y + m[9] * z + m[13];

    out[at] = halfW + (cx / w) * halfW;
    out[at + 1] = halfH - (cy / w) * halfH;
    out[at + 2] = w;
    /* The radius on screen. `m[5]` carries the vertical focal length, so
       this is the same perspective divide the vertex shader does. */
    out[at + 3] = Math.abs((node.r * m[5] * halfH) / w);
  }
}

/** How much slack a click gets, in screen pixels. Matches the 2D view. */
const HIT_PAD = 4;

/**
 * The nearest particle under a point, or -1.
 *
 * "Nearest" is by camera depth, not by screen distance: two particles
 * overlapping on screen are two particles at different depths, and the
 * one in front is the one being pointed at. That is also the one drawn on
 * top, so the click matches the picture.
 */
export function hitTest3d(
  screen: Float32Array,
  count: number,
  px: number,
  py: number,
): number {
  let best = -1;
  let bestDepth = Infinity;

  for (let i = 0; i < count; i += 1) {
    const at = i * PROJECT_STRIDE;
    const depth = screen[at + 2];
    if (depth <= 0 || depth >= bestDepth) continue;
    const reach = screen[at + 3] + HIT_PAD;
    const dx = screen[at] - px;
    const dy = screen[at + 1] - py;
    if (dx * dx + dy * dy > reach * reach) continue;
    best = i;
    bestDepth = depth;
  }
  return best;
}

/**
 * Draw order for the particles: furthest first.
 *
 * There is no depth buffer in this renderer — everything is a
 * billboarded, blended disc, and a depth buffer would have them fighting
 * each other at the edges. The painter's algorithm is what the 2D view
 * uses too, so this is the same rule, sorted along the axis 2D does not
 * have. `order` belongs to the caller and is reused every frame.
 */
export function depthOrder(
  screen: Float32Array,
  count: number,
  order: Uint32Array,
): void {
  for (let i = 0; i < count; i += 1) order[i] = i;
  order
    .subarray(0, count)
    .sort((a, b) => screen[b * PROJECT_STRIDE + 2] - screen[a * PROJECT_STRIDE + 2]);
}
