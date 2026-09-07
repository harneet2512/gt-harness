/* ------------------------------------------------------------------ *
 * `d3-force-3d` ships no types.
 *
 * This declares the corner of it the 3D layout uses, and no more —
 * writing out the whole surface would be inventing a contract we do not
 * exercise and cannot check. Every signature here mirrors the upstream
 * source (v3.0.6); the shapes are the same as `@types/d3-force`, with a
 * third dimension and a `numDimensions` argument to `forceSimulation`.
 * ------------------------------------------------------------------ */

declare module "d3-force-3d" {
  export interface SimulationNode3D {
    index?: number;
    x?: number;
    y?: number;
    z?: number;
    vx?: number;
    vy?: number;
    vz?: number;
    fx?: number | null;
    fy?: number | null;
    fz?: number | null;
  }

  export interface SimulationLink3D<N> {
    source: string | number | N;
    target: string | number | N;
    index?: number;
  }

  export interface Force3D<N, L> {
    (alpha: number): void;
    initialize?: (nodes: N[], random: () => number, nDim: number) => void;
    /** Present on the link force; the simulation never calls it. */
    links?: (links: L[]) => unknown;
  }

  export interface Simulation3D<N, L> {
    tick(iterations?: number): this;
    restart(): this;
    stop(): this;
    nodes(): N[];
    nodes(nodes: N[]): this;
    alpha(): number;
    alpha(alpha: number): this;
    alphaMin(): number;
    alphaMin(min: number): this;
    alphaDecay(decay: number): this;
    alphaTarget(target: number): this;
    velocityDecay(decay: number): this;
    force(name: string): Force3D<N, L> | undefined;
    force(name: string, force: Force3D<N, L> | null): this;
    find(x: number, y: number, z?: number, radius?: number): N | undefined;
    on(name: string, listener: ((this: this) => void) | null): this;
  }

  export function forceSimulation<N extends SimulationNode3D, L = unknown>(
    nodes?: N[],
    numDimensions?: number,
  ): Simulation3D<N, L>;

  export interface LinkForce3D<N, L> extends Force3D<N, L> {
    links(): L[];
    links(links: L[]): this;
    id(accessor: (node: N, i: number, nodes: N[]) => string): this;
    distance(distance: number | ((link: L, i: number, links: L[]) => number)): this;
    strength(strength: number | ((link: L, i: number, links: L[]) => number)): this;
    iterations(count: number): this;
  }

  export function forceLink<N extends SimulationNode3D, L>(
    links?: L[],
  ): LinkForce3D<N, L>;

  export interface ManyBodyForce3D<N, L> extends Force3D<N, L> {
    strength(strength: number | ((node: N, i: number, nodes: N[]) => number)): this;
    theta(theta: number): this;
    distanceMin(distance: number): this;
    distanceMax(distance: number): this;
  }

  export function forceManyBody<N extends SimulationNode3D, L = unknown>(): ManyBodyForce3D<
    N,
    L
  >;

  export interface CollideForce3D<N, L> extends Force3D<N, L> {
    radius(radius: number | ((node: N, i: number, nodes: N[]) => number)): this;
    strength(strength: number): this;
    iterations(count: number): this;
  }

  export function forceCollide<N extends SimulationNode3D, L = unknown>(): CollideForce3D<
    N,
    L
  >;

  export interface AxisForce3D<N, L> extends Force3D<N, L> {
    strength(strength: number | ((node: N, i: number, nodes: N[]) => number)): this;
    x?(x: number | ((node: N, i: number, nodes: N[]) => number)): this;
    y?(y: number | ((node: N, i: number, nodes: N[]) => number)): this;
    z?(z: number | ((node: N, i: number, nodes: N[]) => number)): this;
  }

  export function forceX<N extends SimulationNode3D, L = unknown>(
    x?: number | ((node: N, i: number, nodes: N[]) => number),
  ): AxisForce3D<N, L>;

  export function forceY<N extends SimulationNode3D, L = unknown>(
    y?: number | ((node: N, i: number, nodes: N[]) => number),
  ): AxisForce3D<N, L>;

  export function forceZ<N extends SimulationNode3D, L = unknown>(
    z?: number | ((node: N, i: number, nodes: N[]) => number),
  ): AxisForce3D<N, L>;
}
