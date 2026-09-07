/* ------------------------------------------------------------------ *
 * The particle field.
 *
 * Every tracked file is a particle; every known relation between two of
 * them is a filament. Files are grouped into clusters by their top-level
 * directory so the force layout settles into regions you can recognise.
 *
 * Two things are derived here rather than fetched: co-touch filaments (the
 * agent moved from A to B with no declared relation between them, which is
 * a relation of its own) and directory collapse, which keeps a very large
 * repository under the particle cap without dropping it on the floor.
 * ------------------------------------------------------------------ */

import type { GraphEdge, SessionGraph } from "./api";

export type FilamentKind =
  | "import"
  | "gt_call"
  | "gt_ref"
  | "gt_import"
  | "cotouch";

/** Above this many particles, the deepest directories fold into one each. */
export const MAX_PARTICLES = 1500;

export interface Particle {
  id: string;
  /** Full path for a file; `some/dir/` for a folded directory. */
  path: string;
  label: string;
  kind: "file" | "dir";
  /** Files folded into this particle; 1 for a file. */
  count: number;
  size: number;
  lang: string;
  /** Top-level directory; the clustering key. */
  cluster: string;
  hue: number;
  r: number;

  /* d3-force writes these in place. */
  index?: number;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number | null;
  fy?: number | null;
}

export interface Filament {
  source: string | Particle;
  target: string | Particle;
  kind: FilamentKind;
  weight: number;
  index?: number;
}

export interface ParticleField {
  particles: Particle[];
  filaments: Filament[];
  byId: Map<string, Particle>;
  /** File path -> the particle that stands for it once folding is done. */
  resolve: Map<string, string>;
  clusters: string[];
  /** Files hidden inside directory particles. */
  folded: number;
  /**
   * Identity of the *shape*: which particles exist and what joins them.
   * Two builds with the same signature are the same layout problem, so the
   * simulation must not be restarted between them.
   */
  signature: string;
}

export const EMPTY_FIELD: ParticleField = {
  particles: [],
  filaments: [],
  byId: new Map(),
  resolve: new Map(),
  clusters: [],
  folded: 0,
  signature: "",
};

/* ------------------------------------------------------------------ *
 * Colour and size
 * ------------------------------------------------------------------ */

/** Eight muted hues, far enough apart to tell regions apart at a glance. */
const HUES = [28, 152, 205, 262, 338, 96, 182, 48];

export function clusterHue(clusters: readonly string[], cluster: string): number {
  const at = clusters.indexOf(cluster);
  return HUES[(at < 0 ? 0 : at) % HUES.length];
}

export function hueFill(hue: number, alpha = 1): string {
  return alpha >= 1
    ? `hsl(${hue} 35% 78%)`
    : `hsl(${hue} 35% 78% / ${alpha})`;
}

/** Saturation and lightness of a cluster hue. `hueFill` writes the same two. */
const HUE_S = 0.35;
const HUE_L = 0.78;

/**
 * The same colour `hueFill` names, as 0..1 channels — what a shader takes.
 * One definition of the region colour, two ways of spelling it, so the
 * flat view and the 3D one cannot end up painting different maps.
 */
export function hueRgb(hue: number): { r: number; g: number; b: number } {
  const c = (1 - Math.abs(2 * HUE_L - 1)) * HUE_S;
  const h = (((hue % 360) + 360) % 360) / 60;
  const x = c * (1 - Math.abs((h % 2) - 1));
  const m = HUE_L - c / 2;
  let r = 0;
  let g = 0;
  let b = 0;
  if (h < 1) [r, g, b] = [c, x, 0];
  else if (h < 2) [r, g, b] = [x, c, 0];
  else if (h < 3) [r, g, b] = [0, c, x];
  else if (h < 4) [r, g, b] = [0, x, c];
  else if (h < 5) [r, g, b] = [x, 0, c];
  else [r, g, b] = [c, 0, x];
  return { r: r + m, g: g + m, b: b + m };
}

function clamp(value: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, value));
}

export function fileRadius(size: number): number {
  return clamp(2.5 + 1.6 * Math.sqrt(Math.sqrt(Math.max(0, size))), 2.5, 9);
}

/** A folded directory reads by how much it swallowed, not by byte count. */
export function dirRadius(count: number): number {
  return clamp(3.5 + 1.3 * Math.sqrt(count), 3.5, 15);
}

/* ------------------------------------------------------------------ *
 * Folding
 * ------------------------------------------------------------------ */

function dirname(path: string): string {
  const cut = path.lastIndexOf("/");
  return cut < 0 ? "" : path.slice(0, cut);
}

export function basename(path: string): string {
  const cut = path.lastIndexOf("/");
  return cut < 0 ? path : path.slice(cut + 1);
}

/**
 * Map every file path onto the particle that will represent it. Under the
 * cap that is the file itself; over it, the deepest directories are folded
 * one level at a time until the count fits.
 */
function foldPaths(paths: readonly string[], cap: number): Map<string, string> {
  const resolve = new Map<string, string>();
  for (const path of paths) resolve.set(path, path);
  if (paths.length <= cap) return resolve;

  // Group by directory, deepest first: folding a leaf directory costs the
  // least detail.
  const byDir = new Map<string, string[]>();
  for (const path of paths) {
    const dir = dirname(path);
    const bucket = byDir.get(dir);
    if (bucket) bucket.push(path);
    else byDir.set(dir, [path]);
  }

  const dirs = [...byDir.keys()]
    .filter((dir) => dir !== "")
    .sort((a, b) => depth(b) - depth(a) || (a < b ? 1 : -1));

  let count = paths.length;
  for (const dir of dirs) {
    if (count <= cap) break;
    const members = byDir.get(dir) ?? [];
    if (members.length < 2) continue;
    for (const path of members) resolve.set(path, `dir:${dir}`);
    count -= members.length - 1;
  }

  return resolve;
}

function depth(path: string): number {
  return path.split("/").length;
}

/* ------------------------------------------------------------------ *
 * Building the field
 * ------------------------------------------------------------------ */

const KINDS: ReadonlySet<string> = new Set([
  "import",
  "gt_call",
  "gt_ref",
  "gt_import",
]);

function kindOf(raw: string): FilamentKind {
  return KINDS.has(raw) ? (raw as FilamentKind) : "import";
}

/** `a\u0000b`, order-independent, so A–B and B–A are one filament. */
export function pairKey(a: string, b: string): string {
  return a < b ? `${a}\u0000${b}` : `${b}\u0000${a}`;
}

/**
 * Fold the server graph plus the session's co-touch pairs into everything
 * the canvas draws. Particles keep their simulated position across rebuilds
 * when `previous` is supplied, so a refetch does not throw the layout away.
 */
export function buildField(
  graph: SessionGraph,
  cotouch: ReadonlySet<string>,
  previous?: ParticleField,
): ParticleField {
  if (graph.nodes.length === 0) return EMPTY_FIELD;

  const clusters = [
    ...new Set(graph.nodes.map((node) => node.dir ?? "")),
  ].sort();

  const resolve = foldPaths(
    graph.nodes.map((node) => node.path),
    MAX_PARTICLES,
  );

  const byId = new Map<string, Particle>();
  let folded = 0;

  for (const node of graph.nodes) {
    const id = resolve.get(node.path) ?? node.path;
    const cluster = node.dir ?? "";
    const existing = byId.get(id);

    if (id.startsWith("dir:")) {
      folded += 1;
      if (existing) {
        existing.count += 1;
        existing.size += node.size;
        existing.r = dirRadius(existing.count);
        existing.label = `${existing.path} (${existing.count} files)`;
        continue;
      }
      const dir = id.slice(4);
      byId.set(id, {
        id,
        path: `${dir}/`,
        label: `${dir}/ (1 file)`,
        kind: "dir",
        count: 1,
        size: node.size,
        lang: "",
        cluster,
        hue: clusterHue(clusters, cluster),
        r: dirRadius(1),
      });
      continue;
    }

    byId.set(id, {
      id,
      path: node.path,
      label: basename(node.path),
      kind: "file",
      count: 1,
      size: node.size,
      lang: node.lang ?? "",
      cluster,
      hue: clusterHue(clusters, cluster),
      r: fileRadius(node.size),
    });
  }

  // A folded directory swallows its files, so `folded` counted them all.
  folded = Math.max(0, folded - countDirs(byId));

  // Carry positions over: a refetch after `turn_finished` must not reshuffle
  // the field the reader is looking at. Velocity and any pin travel with the
  // particle too, so a node held under the pointer stays where it was put.
  if (previous) {
    for (const particle of byId.values()) {
      const before = previous.byId.get(particle.id);
      if (before && before.x !== undefined) {
        particle.x = before.x;
        particle.y = before.y;
        particle.vx = before.vx ?? 0;
        particle.vy = before.vy ?? 0;
        if (before.fx != null) particle.fx = before.fx;
        if (before.fy != null) particle.fy = before.fy;
      }
    }
  }

  const seen = new Set<string>();
  const filaments: Filament[] = [];

  const add = (a: string, b: string, kind: FilamentKind, weight: number) => {
    if (a === b || !byId.has(a) || !byId.has(b)) return;
    const key = `${pairKey(a, b)}\u0000${kind}`;
    if (seen.has(key)) return;
    seen.add(key);
    filaments.push({ source: a, target: b, kind, weight });
  };

  for (const edge of graph.edges) {
    const source = resolve.get(edge.source) ?? edge.source;
    const target = resolve.get(edge.target) ?? edge.target;
    add(source, target, kindOf(String(edge.kind)), edge.weight || 1);
  }

  for (const key of cotouch) {
    const [a, b] = key.split("\u0000");
    const source = resolve.get(a) ?? a;
    const target = resolve.get(b) ?? b;
    // A declared relation already says it better than co-touch does.
    if (
      seen.has(`${pairKey(source, target)}\u0000import`) ||
      seen.has(`${pairKey(source, target)}\u0000gt_call`) ||
      seen.has(`${pairKey(source, target)}\u0000gt_ref`) ||
      seen.has(`${pairKey(source, target)}\u0000gt_import`)
    ) {
      continue;
    }
    add(source, target, "cotouch", 1);
  }

  const signature = signatureOf(byId, filaments);

  if (previous && previous.signature === signature) {
    /* The same particles joined the same way: hand back the very field the
       canvas is already simulating, so React sees no change and the layout
       is not restarted at all. Facts that can move without moving anything
       — byte size and the radius it drives — are refreshed in place, which
       is how d3-force treats these objects anyway. */
    for (const particle of byId.values()) {
      const before = previous.byId.get(particle.id);
      if (!before) continue;
      before.size = particle.size;
      before.count = particle.count;
      before.label = particle.label;
      before.lang = particle.lang;
      before.cluster = particle.cluster;
      before.hue = particle.hue;
      before.r = particle.r;
    }
    return previous;
  }

  return {
    particles: [...byId.values()],
    filaments,
    byId,
    resolve,
    clusters,
    folded,
    signature,
  };
}

/** Particle ids and filament ends, order-independent. */
function signatureOf(
  byId: ReadonlyMap<string, Particle>,
  filaments: readonly Filament[],
): string {
  const ids = [...byId.keys()].sort();
  const links = filaments
    .map((f) => `${idOf(f.source)}>${idOf(f.target)}:${f.kind}`)
    .sort();
  return `${ids.length}:${links.length}|${ids.join(",")}|${links.join(",")}`;
}

function countDirs(byId: ReadonlyMap<string, Particle>): number {
  let n = 0;
  for (const particle of byId.values()) if (particle.kind === "dir") n += 1;
  return n;
}

/* ------------------------------------------------------------------ *
 * Relations, read at file level (never folded — the inspector is about
 * one real file).
 * ------------------------------------------------------------------ */

export interface Relations {
  imports: GraphEdge[];
  importedBy: GraphEdge[];
  gtOut: GraphEdge[];
  gtIn: GraphEdge[];
}

const EMPTY_RELATIONS: Relations = {
  imports: [],
  importedBy: [],
  gtOut: [],
  gtIn: [],
};

export function buildRelations(
  graph: SessionGraph,
): ReadonlyMap<string, Relations> {
  const out = new Map<string, Relations>();
  const slot = (path: string): Relations => {
    const existing = out.get(path);
    if (existing) return existing;
    const created: Relations = {
      imports: [],
      importedBy: [],
      gtOut: [],
      gtIn: [],
    };
    out.set(path, created);
    return created;
  };

  for (const edge of graph.edges) {
    const gt = String(edge.kind).startsWith("gt");
    if (gt) {
      slot(edge.source).gtOut.push(edge);
      slot(edge.target).gtIn.push(edge);
    } else {
      slot(edge.source).imports.push(edge);
      slot(edge.target).importedBy.push(edge);
    }
  }

  return out;
}

export function relationsFor(
  table: ReadonlyMap<string, Relations>,
  path: string,
): Relations {
  return table.get(path) ?? EMPTY_RELATIONS;
}

/** Every particle one filament away, for the hover highlight. */
export function neighboursOf(field: ParticleField): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>();
  const link = (a: string, b: string) => {
    const bucket = out.get(a);
    if (bucket) bucket.add(b);
    else out.set(a, new Set([b]));
  };
  for (const filament of field.filaments) {
    const a = idOf(filament.source);
    const b = idOf(filament.target);
    link(a, b);
    link(b, a);
  }
  return out;
}

export function idOf(end: string | Particle): string {
  return typeof end === "string" ? end : end.id;
}
