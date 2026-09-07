/* ------------------------------------------------------------------ *
 * Somewhere to put a frame.
 *
 * Three buffers, filled from scratch every frame and never reallocated
 * while one is being drawn: a set of billboarded discs, a set of rings,
 * and a run of line segments. Between them they carry everything the flat
 * painter draws, and each is one draw call however much is in it.
 *
 * The rule this file exists to enforce is that a frame allocates nothing.
 * Capacity is decided once, when the scene changes; a frame writes into
 * typed arrays it already owns, says how much of them it used, and stops.
 * If a frame ever wants more room than it was given it draws what fits
 * rather than growing mid-flight — the alternative is a garbage collection
 * pause in the middle of an animation, which is the thing being avoided.
 * ------------------------------------------------------------------ */

import {
  BufferAttribute,
  BufferGeometry,
  DynamicDrawUsage,
  InstancedBufferAttribute,
  InstancedBufferGeometry,
  LineSegments,
  Mesh,
  type Material,
  type Object3D,
} from "three";

/* A unit quad, in its own attributes each time: two geometries that share
   one attribute share one GPU buffer, and then disposing either of them
   is a question about the other. */
const QUAD_POSITION = [-0.5, -0.5, 0, 0.5, -0.5, 0, 0.5, 0.5, 0, -0.5, 0.5, 0];
const QUAD_INDEX = [0, 1, 2, 0, 2, 3];

function quadGeometry(): InstancedBufferGeometry {
  const geometry = new InstancedBufferGeometry();
  geometry.setAttribute(
    "position",
    new BufferAttribute(new Float32Array(QUAD_POSITION), 3),
  );
  geometry.setIndex(new BufferAttribute(new Uint16Array(QUAD_INDEX), 1));
  return geometry;
}

function instanced(
  geometry: InstancedBufferGeometry,
  name: string,
  capacity: number,
  size: number,
): Float32Array {
  const data = new Float32Array(capacity * size);
  const attribute = new InstancedBufferAttribute(data, size);
  attribute.setUsage(DynamicDrawUsage);
  geometry.setAttribute(name, attribute);
  return data;
}

function flush(geometry: InstancedBufferGeometry, names: readonly string[]): void {
  for (const name of names) {
    const attribute = geometry.getAttribute(name);
    if (attribute) attribute.needsUpdate = true;
  }
}

/* ------------------------------------------------------------------ *
 * Discs
 * ------------------------------------------------------------------ */

const DISC_ATTRIBUTES = ["iOffset", "iRadius", "iColor", "iAlpha"] as const;

/** Every particle, or every signal head, in one instanced quad. */
export class DiscSet {
  readonly geometry: InstancedBufferGeometry;
  readonly mesh: Mesh;
  readonly capacity: number;

  private readonly offset: Float32Array;
  private readonly radius: Float32Array;
  private readonly color: Float32Array;
  private readonly alpha: Float32Array;
  private n = 0;

  constructor(capacity: number, material: Material, renderOrder: number) {
    this.capacity = Math.max(1, capacity);
    this.geometry = quadGeometry();
    this.offset = instanced(this.geometry, "iOffset", this.capacity, 3);
    this.radius = instanced(this.geometry, "iRadius", this.capacity, 1);
    this.color = instanced(this.geometry, "iColor", this.capacity, 3);
    this.alpha = instanced(this.geometry, "iAlpha", this.capacity, 1);
    this.mesh = new Mesh(this.geometry, material);
    /* The instances live in attributes, not in the object's transform, so
       the mesh's own bounding sphere says nothing true about where they
       are and culling against it would drop the whole field. */
    this.mesh.frustumCulled = false;
    this.mesh.renderOrder = renderOrder;
  }

  begin(): void {
    this.n = 0;
  }

  add(
    x: number,
    y: number,
    z: number,
    r: number,
    cr: number,
    cg: number,
    cb: number,
    alpha: number,
  ): void {
    const i = this.n;
    if (i >= this.capacity) return;
    this.offset[i * 3] = x;
    this.offset[i * 3 + 1] = y;
    this.offset[i * 3 + 2] = z;
    this.radius[i] = r;
    this.color[i * 3] = cr;
    this.color[i * 3 + 1] = cg;
    this.color[i * 3 + 2] = cb;
    this.alpha[i] = alpha;
    this.n = i + 1;
  }

  end(): void {
    this.geometry.instanceCount = this.n;
    flush(this.geometry, DISC_ATTRIBUTES);
  }

  get count(): number {
    return this.n;
  }

  dispose(): void {
    this.geometry.dispose();
  }
}

/* ------------------------------------------------------------------ *
 * Rings
 * ------------------------------------------------------------------ */

const RING_ATTRIBUTES = [
  "iOffset",
  "iRadius",
  "iWidth",
  "iColor",
  "iAlpha",
  "iArc",
] as const;

/** The full turn, for a ring that is not shared with anybody. */
export const FULL_TURN = Math.PI * 2;

/** Rings, halos and the wedges several agents split one ring into. */
export class RingSet {
  readonly geometry: InstancedBufferGeometry;
  readonly mesh: Mesh;
  readonly capacity: number;

  private readonly offset: Float32Array;
  private readonly radius: Float32Array;
  private readonly width: Float32Array;
  private readonly color: Float32Array;
  private readonly alpha: Float32Array;
  private readonly arc: Float32Array;
  private n = 0;

  constructor(capacity: number, material: Material, renderOrder: number) {
    this.capacity = Math.max(1, capacity);
    this.geometry = quadGeometry();
    this.offset = instanced(this.geometry, "iOffset", this.capacity, 3);
    this.radius = instanced(this.geometry, "iRadius", this.capacity, 1);
    this.width = instanced(this.geometry, "iWidth", this.capacity, 1);
    this.color = instanced(this.geometry, "iColor", this.capacity, 3);
    this.alpha = instanced(this.geometry, "iAlpha", this.capacity, 1);
    this.arc = instanced(this.geometry, "iArc", this.capacity, 2);
    this.mesh = new Mesh(this.geometry, material);
    this.mesh.frustumCulled = false;
    this.mesh.renderOrder = renderOrder;
  }

  begin(): void {
    this.n = 0;
  }

  add(
    x: number,
    y: number,
    z: number,
    r: number,
    width: number,
    cr: number,
    cg: number,
    cb: number,
    alpha: number,
    from = 0,
    span = FULL_TURN,
  ): void {
    const i = this.n;
    if (i >= this.capacity || alpha <= 0.004) return;
    this.offset[i * 3] = x;
    this.offset[i * 3 + 1] = y;
    this.offset[i * 3 + 2] = z;
    this.radius[i] = r;
    this.width[i] = width;
    this.color[i * 3] = cr;
    this.color[i * 3 + 1] = cg;
    this.color[i * 3 + 2] = cb;
    this.alpha[i] = alpha;
    this.arc[i * 2] = from;
    this.arc[i * 2 + 1] = span;
    this.n = i + 1;
  }

  end(): void {
    this.geometry.instanceCount = this.n;
    flush(this.geometry, RING_ATTRIBUTES);
  }

  get count(): number {
    return this.n;
  }

  dispose(): void {
    this.geometry.dispose();
  }
}

/* ------------------------------------------------------------------ *
 * Lines
 * ------------------------------------------------------------------ */

/**
 * Every relation, and every signal's tail, as one run of segments.
 *
 * The colour of each end is written per vertex with the alpha already
 * mixed into it against the paper. Lines cannot carry alpha per vertex,
 * but more to the point they should not: a hundred faint relations
 * crossing each other would each add their own opacity and the middle of
 * the field would turn into a bright smear. Pre-mixing means a faint
 * relation stays faint however many others lie behind it, which is what
 * keeps a dense graph readable rather than glowing.
 */
export class LineSet {
  readonly geometry: BufferGeometry;
  readonly mesh: LineSegments;
  readonly capacity: number;

  private readonly position: Float32Array;
  private readonly color: Float32Array;
  private readonly positionAttribute: BufferAttribute;
  private readonly colorAttribute: BufferAttribute;
  private n = 0;

  /** `capacity` is in segments; each takes two vertices. */
  constructor(capacity: number, material: Material, renderOrder: number) {
    this.capacity = Math.max(1, capacity);
    this.geometry = new BufferGeometry();
    this.position = new Float32Array(this.capacity * 6);
    this.color = new Float32Array(this.capacity * 6);
    this.positionAttribute = new BufferAttribute(this.position, 3);
    this.positionAttribute.setUsage(DynamicDrawUsage);
    this.colorAttribute = new BufferAttribute(this.color, 3);
    this.colorAttribute.setUsage(DynamicDrawUsage);
    this.geometry.setAttribute("position", this.positionAttribute);
    this.geometry.setAttribute("color", this.colorAttribute);
    this.mesh = new LineSegments(this.geometry, material);
    this.mesh.frustumCulled = false;
    this.mesh.renderOrder = renderOrder;
  }

  begin(): void {
    this.n = 0;
  }

  /** One segment, with a colour at each end so a tail can fade along it. */
  add(
    ax: number,
    ay: number,
    az: number,
    bx: number,
    by: number,
    bz: number,
    ar: number,
    ag: number,
    ab: number,
    br: number,
    bg: number,
    bb: number,
  ): void {
    const i = this.n;
    if (i >= this.capacity) return;
    const at = i * 6;
    this.position[at] = ax;
    this.position[at + 1] = ay;
    this.position[at + 2] = az;
    this.position[at + 3] = bx;
    this.position[at + 4] = by;
    this.position[at + 5] = bz;
    this.color[at] = ar;
    this.color[at + 1] = ag;
    this.color[at + 2] = ab;
    this.color[at + 3] = br;
    this.color[at + 4] = bg;
    this.color[at + 5] = bb;
    this.n = i + 1;
  }

  /** Only the positions changed — the layout moved, the theme did not. */
  touchPositions(): void {
    this.positionAttribute.needsUpdate = true;
  }

  touchColors(): void {
    this.colorAttribute.needsUpdate = true;
  }

  end(both = true): void {
    this.geometry.setDrawRange(0, this.n * 2);
    this.positionAttribute.needsUpdate = true;
    if (both) this.colorAttribute.needsUpdate = true;
  }

  get count(): number {
    return this.n;
  }

  /** Write into segment `i` without disturbing the fill cursor. */
  get segments(): Float32Array {
    return this.position;
  }

  get colors(): Float32Array {
    return this.color;
  }

  set count(n: number) {
    this.n = Math.min(this.capacity, Math.max(0, n));
  }

  dispose(): void {
    this.geometry.dispose();
  }
}

/** Everything a set puts in the scene, for `Object3D.add`. */
export type Drawable = { mesh: Object3D };
