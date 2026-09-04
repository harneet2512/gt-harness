/* ------------------------------------------------------------------ *
 * The terrain: a squarified treemap of the repository tree.
 *
 * Pure geometry — no React, no DOM. Files are sized by sqrt(bytes) so a
 * 30-byte `__init__.py` is still a visible cell next to an 8 kB doc.
 * ------------------------------------------------------------------ */

export interface TreeFileInput {
  path: string;
  size: number;
}

export type NodeKind = "dir" | "file" | "overflow";

export interface MapNode {
  kind: NodeKind;
  /** Full repo-relative path. Overflow nodes end in `/…`. */
  path: string;
  name: string;
  bytes: number;
  /** Layout weight; a directory's is the sum of its children's. */
  weight: number;
  /** Files represented — 1 for a file, N for a folded or collapsed group. */
  count: number;
  children: MapNode[];
}

/** Children kept per directory before the tail folds into one "… N more". */
const MAX_CHILDREN = 22;
/** Hard cap on drawn cells; deeper directories stop expanding past it. */
const MAX_CELLS = 600;

const PAD = 1;
const LABEL_H = 13;
const LABEL_MIN_W = 52;
const LABEL_MIN_H = 30;
const RECURSE_MIN = 14;

/** A zero-byte file still deserves ink, so every file gets a floor. */
export function fileWeight(bytes: number): number {
  return Math.sqrt(Math.max(bytes, 0)) + 2;
}

export function buildTree(
  files: readonly TreeFileInput[],
  collapsed: ReadonlySet<string> = new Set(),
): MapNode {
  const root: MapNode = {
    kind: "dir",
    path: "",
    name: "",
    bytes: 0,
    weight: 0,
    count: 0,
    children: [],
  };
  const dirs = new Map<string, MapNode>([["", root]]);

  const dirFor = (path: string): MapNode => {
    const found = dirs.get(path);
    if (found) return found;
    const cut = path.lastIndexOf("/");
    const parent = dirFor(cut === -1 ? "" : path.slice(0, cut));
    const node: MapNode = {
      kind: "dir",
      path,
      name: cut === -1 ? path : path.slice(cut + 1),
      bytes: 0,
      weight: 0,
      count: 0,
      children: [],
    };
    parent.children.push(node);
    dirs.set(path, node);
    return node;
  };

  for (const file of files) {
    if (!file.path) continue;
    const cut = file.path.lastIndexOf("/");
    const parent = dirFor(cut === -1 ? "" : file.path.slice(0, cut));
    parent.children.push({
      kind: "file",
      path: file.path,
      name: file.path.slice(cut + 1),
      bytes: Math.max(0, file.size),
      weight: fileWeight(file.size),
      count: 1,
      children: [],
    });
  }

  rollup(root, collapsed);
  return root;
}

function rollup(node: MapNode, collapsed: ReadonlySet<string>): void {
  if (node.kind !== "dir") return;

  for (const child of node.children) rollup(child, collapsed);

  node.bytes = node.children.reduce((sum, c) => sum + c.bytes, 0);
  node.count = node.children.reduce((sum, c) => sum + c.count, 0);
  node.children.sort(
    (a, b) => b.weight - a.weight || a.name.localeCompare(b.name),
  );

  if (node.path && collapsed.has(node.path)) {
    node.children = [];
  } else if (node.children.length > MAX_CHILDREN) {
    const kept = node.children.slice(0, MAX_CHILDREN - 1);
    const folded = node.children.slice(MAX_CHILDREN - 1);
    kept.push({
      kind: "overflow",
      path: `${node.path ? `${node.path}/` : ""}…`,
      name: `… ${folded.reduce((s, c) => s + c.count, 0)} more`,
      bytes: folded.reduce((s, c) => s + c.bytes, 0),
      weight: folded.reduce((s, c) => s + c.weight, 0),
      count: folded.reduce((s, c) => s + c.count, 0),
      children: [],
    });
    node.children = kept;
  }

  node.weight = node.children.length
    ? node.children.reduce((sum, c) => sum + c.weight, 0)
    : fileWeight(node.bytes);
}

/* ------------------------------------------------------------------ *
 * Layout
 * ------------------------------------------------------------------ */

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface MapCell extends Rect {
  kind: NodeKind;
  path: string;
  name: string;
  bytes: number;
  count: number;
  depth: number;
  /** The directory drew a label strip, so its name is already on screen. */
  hasLabel: boolean;
  /** Nothing was drawn inside it — it reads as a single block. */
  leaf: boolean;
}

export interface MapLayout {
  cells: MapCell[];
  byPath: Map<string, MapCell>;
}

/**
 * Cells come out parent-first, which is also the correct painting order:
 * a directory is a frame its children are drawn on top of.
 */
export function layoutTree(
  root: MapNode,
  width: number,
  height: number,
): MapLayout {
  const cells: MapCell[] = [];
  if (width > 4 && height > 4) {
    place(root, { x: 0, y: 0, w: width, h: height }, 0, cells);
  }
  const byPath = new Map<string, MapCell>();
  for (const cell of cells) byPath.set(cell.path, cell);
  return { cells, byPath };
}

function place(
  node: MapNode,
  rect: Rect,
  depth: number,
  cells: MapCell[],
): void {
  const expandable =
    node.children.length > 0 &&
    cells.length < MAX_CELLS &&
    rect.w > RECURSE_MIN &&
    rect.h > RECURSE_MIN;

  const hasLabel =
    node.kind === "dir" &&
    depth > 0 &&
    expandable &&
    rect.w >= LABEL_MIN_W &&
    rect.h >= LABEL_MIN_H;

  if (depth > 0) {
    cells.push({
      ...rect,
      kind: node.kind,
      path: node.path,
      name: node.name,
      bytes: node.bytes,
      count: node.count,
      depth,
      hasLabel,
      leaf: !expandable,
    });
  }

  if (!expandable) return;

  const top = rect.y + PAD + (hasLabel ? LABEL_H : 0);
  const inner: Rect = {
    x: rect.x + PAD,
    y: top,
    w: Math.max(0, rect.w - PAD * 2),
    h: Math.max(0, rect.y + rect.h - PAD - top),
  };
  if (inner.w < 2 || inner.h < 2) return;

  const rects = squarify(node.children, inner);
  for (let i = 0; i < node.children.length; i += 1) {
    const child = rects[i];
    if (child.w < 1.5 || child.h < 1.5) continue;
    place(node.children[i], child, depth + 1, cells);
  }
}

/** Bruls/Huizing/van Wijk squarified treemap. */
function squarify(nodes: readonly MapNode[], rect: Rect): Rect[] {
  const out: Rect[] = new Array(nodes.length);
  const total = nodes.reduce((sum, n) => sum + n.weight, 0);
  const empty: Rect = { x: rect.x, y: rect.y, w: 0, h: 0 };
  if (total <= 0) return out.fill(empty);

  let free: Rect = { ...rect };
  let remaining = total;
  let i = 0;

  while (i < nodes.length && free.w > 0.5 && free.h > 0.5 && remaining > 0) {
    const scale = (free.w * free.h) / remaining;
    const shorter = Math.min(free.w, free.h);

    const areas = [nodes[i].weight * scale];
    let j = i + 1;
    while (j < nodes.length) {
      const next = nodes[j].weight * scale;
      if (worst([...areas, next], shorter) > worst(areas, shorter)) break;
      areas.push(next);
      j += 1;
    }

    const rowArea = areas.reduce((sum, a) => sum + a, 0);
    const thick = rowArea / shorter;

    if (free.w >= free.h) {
      let y = free.y;
      for (let k = 0; k < areas.length; k += 1) {
        const h = areas[k] / thick;
        out[i + k] = { x: free.x, y, w: thick, h };
        y += h;
      }
      free = { x: free.x + thick, y: free.y, w: free.w - thick, h: free.h };
    } else {
      let x = free.x;
      for (let k = 0; k < areas.length; k += 1) {
        const w = areas[k] / thick;
        out[i + k] = { x, y: free.y, w, h: thick };
        x += w;
      }
      free = { x: free.x, y: free.y + thick, w: free.w, h: free.h - thick };
    }

    remaining -= rowArea / scale;
    i += areas.length;
  }

  for (; i < nodes.length; i += 1) out[i] = { x: free.x, y: free.y, w: 0, h: 0 };
  return out;
}

function worst(areas: readonly number[], side: number): number {
  let total = 0;
  let max = -Infinity;
  let min = Infinity;
  for (const area of areas) {
    total += area;
    if (area > max) max = area;
    if (area < min) min = area;
  }
  if (total <= 0 || min <= 0) return Infinity;
  const s2 = side * side;
  const t2 = total * total;
  return Math.max((s2 * max) / t2, t2 / (s2 * min));
}

/** "8.6 kB" / "210 B" — sizes read next to the path in the tooltip. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} kB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
