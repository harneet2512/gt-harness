/* The 3D layout, tested where it is pure.
 *
 * Everything below runs without a GPU, a canvas or a DOM, because
 * everything below is arithmetic: where a directory sits, where a file
 * starts, what path a relation takes between two lobes, and which
 * particle is under a click. What is *not* covered here is anything that
 * needs a WebGL context — the shaders, the buffer uploads, the frame
 * loop. That is stated plainly in the report rather than papered over
 * with a mock that would only be testing the mock.
 */

import { describe, expect, it } from "vitest";
import type { GraphNode, SessionGraph } from "../api";
import { buildField, type ParticleField } from "../graph";
import { clusterAnchors } from "../graphSim";
import {
  bounds3d,
  buildScene3d,
  controlOf,
  depthOrder,
  drawnSegments,
  fitDistance,
  forgetScene,
  hash01,
  hitTest3d,
  lobeAnchors,
  lobeRadius,
  pairOf,
  pointOn3d,
  project3d,
  PROJECT_STRIDE,
  recallScene,
  rememberScene,
  SAMPLES_ACROSS,
  SAMPLES_NEAR,
  seedNode,
  waistOf,
  type Node3D,
  type Vec3,
} from "../graph3d";

const NO_COTOUCH: ReadonlySet<string> = new Set();

function node(path: string, size = 100): GraphNode {
  const slash = path.indexOf("/");
  const dot = path.lastIndexOf(".");
  return {
    id: path,
    path,
    size,
    lang: dot > 0 ? path.slice(dot + 1) : "",
    dir: slash > 0 ? path.slice(0, slash) : "",
  };
}

function graph(
  paths: readonly string[],
  edges: readonly [string, string, string][] = [],
): SessionGraph {
  return {
    base_sha: "abc",
    gt: true,
    nodes: paths.map((p) => node(p)),
    edges: edges.map(([source, target, kind]) => ({
      source,
      target,
      kind,
      weight: 1,
    })),
  };
}

function field(
  paths: readonly string[],
  edges: readonly [string, string, string][] = [],
): ParticleField {
  return buildField(graph(paths, edges), NO_COTOUCH);
}

/** Give every particle a flat position, the way the 2D sim would. */
function flatten(one: ParticleField): ParticleField {
  one.particles.forEach((particle, i) => {
    particle.x = 20 * Math.cos(i);
    particle.y = 20 * Math.sin(i);
  });
  return one;
}

function scene(one: ParticleField) {
  return buildScene3d(one, clusterAnchors(one.clusters));
}

function at(node: Node3D): [number, number, number] {
  return [node.x, node.y, node.z];
}

function apart(a: Vec3, b: Vec3): number {
  return Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
}

/* ------------------------------------------------------------------ *
 * Lobes
 * ------------------------------------------------------------------ */

describe("graph3d — lobes", () => {
  it("puts one directory at the origin and says nothing about none", () => {
    expect(lobeAnchors([]).size).toBe(0);
    expect(lobeAnchors(["src"]).get("src")).toEqual({ x: 0, y: 0, z: 0 });
  });

  it("spreads directories over a sphere, all at the same distance", () => {
    const clusters = ["a", "b", "c", "d", "e", "f"];
    const anchors = lobeAnchors(clusters);
    const radius = lobeRadius(clusters.length);
    for (const cluster of clusters) {
      const anchor = anchors.get(cluster);
      expect(anchor).toBeDefined();
      const out = Math.hypot(anchor!.x, anchor!.y, anchor!.z);
      expect(out).toBeCloseTo(radius, 4);
    }
  });

  it("keeps every lobe clear of every other one", () => {
    const clusters = ["a", "b", "c", "d", "e", "f", "g", "h"];
    const anchors = [...lobeAnchors(clusters).values()];
    let closest = Infinity;
    for (let i = 0; i < anchors.length; i += 1) {
      for (let j = i + 1; j < anchors.length; j += 1) {
        closest = Math.min(closest, apart(anchors[i], anchors[j]));
      }
    }
    /* Two lobes closer together than a file's own reach would read as
       one lobe, which is the failure this layout exists to avoid. */
    expect(closest).toBeGreaterThan(60);
  });

  it("gives more room as more directories arrive", () => {
    expect(lobeRadius(8)).toBeGreaterThan(lobeRadius(3));
  });
});

/* ------------------------------------------------------------------ *
 * Seeding
 * ------------------------------------------------------------------ */

describe("graph3d — seeding", () => {
  it("hashes an id to a stable number in range", () => {
    expect(hash01("src/a.py")).toBe(hash01("src/a.py"));
    expect(hash01("src/a.py")).not.toBe(hash01("src/b.py"));
    for (const id of ["", "a", "src/very/deep/name.ts"]) {
      expect(hash01(id)).toBeGreaterThanOrEqual(0);
      expect(hash01(id)).toBeLessThan(1);
    }
  });

  it("carries the flat arrangement over, and only invents depth", () => {
    const one: Node3D = {
      id: "src/a.py",
      r: 3,
      cluster: "src",
      hue: 0,
      x: 0,
      y: 0,
      z: 0,
    };
    seedNode(one, { x: 100, y: 0, z: -50 }, { x: 12, y: -7 }, { x: 4, y: 3 }, 0);
    /* The offset from the flat cluster anchor is the offset from the
       lobe centre: the same picture, moved. */
    expect(one.x).toBeCloseTo(100 + (12 - 4), 6);
    expect(one.y).toBeCloseTo(0 + (-7 - 3), 6);
    expect(Number.isFinite(one.z)).toBe(true);
    expect(one.z).not.toBe(-50);
  });

  it("still places a particle the flat view never placed", () => {
    const one: Node3D = {
      id: "src/b.py",
      r: 3,
      cluster: "src",
      hue: 0,
      x: Number.NaN,
      y: Number.NaN,
      z: Number.NaN,
    };
    seedNode(one, { x: 0, y: 0, z: 0 }, undefined, undefined, 7);
    expect(at(one).every(Number.isFinite)).toBe(true);
    expect(one.vx).toBe(0);
  });

  it("never uses randomness: two builds are the same picture", () => {
    const paths = ["src/a.py", "src/b.py", "tests/t.py", "docs/x.md"];
    const first = scene(flatten(field(paths)));
    const second = scene(flatten(field(paths)));
    expect(first.nodes.map(at)).toEqual(second.nodes.map(at));
  });

  it("hands positions on across a rebuild, and seeds only the newcomer", () => {
    const before = scene(flatten(field(["src/a.py", "src/b.py"])));
    const after = buildScene3d(
      flatten(field(["src/a.py", "src/b.py", "src/c.py"])),
      clusterAnchors(["src"]),
      before,
    );
    expect(at(after.byId.get("src/a.py")!)).toEqual(
      at(before.byId.get("src/a.py")!),
    );
    expect(after.byId.get("src/c.py")).toBeDefined();
    expect(at(after.byId.get("src/c.py")!).every(Number.isFinite)).toBe(true);
  });

  it("is empty for an empty field", () => {
    expect(scene(field([])).nodes).toEqual([]);
  });
});

/* ------------------------------------------------------------------ *
 * Tracts
 * ------------------------------------------------------------------ */

describe("graph3d — tracts", () => {
  it("names a lobe pair the same way round either way", () => {
    expect(pairOf("src", "tests")).toBe(pairOf("tests", "src"));
  });

  it("pushes a waist away from the origin rather than through it", () => {
    const a = { x: 100, y: 20, z: 0 };
    const b = { x: 80, y: -30, z: 40 };
    const mid = { x: 90, y: -5, z: 20 };
    const waist = waistOf(a, b);
    expect(Math.hypot(waist.x, waist.y, waist.z)).toBeGreaterThan(
      Math.hypot(mid.x, mid.y, mid.z),
    );
  });

  it("bends around the middle when two lobes face each other", () => {
    const waist = waistOf({ x: 120, y: 0, z: 0 }, { x: -120, y: 0, z: 0 });
    /* The midpoint is the origin — the straight run through everything.
       The waist must not be there. */
    expect(Math.hypot(waist.x, waist.y, waist.z)).toBeGreaterThan(20);
  });

  it("bends every relation between two lobes through the same point", () => {
    const one = flatten(
      field(
        [
          "src/a.py",
          "src/b.py",
          "src/c.py",
          "tests/x.py",
          "tests/y.py",
          "tests/z.py",
        ],
        [
          ["src/a.py", "tests/x.py", "import"],
          ["src/b.py", "tests/y.py", "import"],
          ["src/c.py", "tests/z.py", "import"],
        ],
      ),
    );
    const built = scene(one);
    const across = built.links.filter((link) => link.across);
    expect(across).toHaveLength(3);

    const controls: Vec3[] = [];
    const middles: Vec3[] = [];
    for (const link of across) {
      const a = built.byId.get(String(link.source))!;
      const b = built.byId.get(String(link.target))!;
      const out: Vec3 = { x: 0, y: 0, z: 0 };
      controls.push({ ...controlOf(built, a, b, true, out) });
      middles.push({
        x: (a.x + b.x) / 2,
        y: (a.y + b.y) / 2,
        z: (a.z + b.z) / 2,
      });
    }

    const spreadOf = (points: readonly Vec3[]): number => {
      let most = 0;
      for (let i = 0; i < points.length; i += 1) {
        for (let j = i + 1; j < points.length; j += 1) {
          most = Math.max(most, apart(points[i], points[j]));
        }
      }
      return most;
    };

    /* Three separate chords would keep their midpoints apart. Gathered
       onto the shared waist they read as one bundle, so the controls are
       markedly closer together than the chords they came from. */
    expect(spreadOf(controls)).toBeLessThan(spreadOf(middles) * 0.5);
  });

  it("bows a relation inside one lobe without gathering it anywhere", () => {
    const built = scene(
      flatten(
        field(["src/a.py", "src/b.py"], [["src/a.py", "src/b.py", "import"]]),
      ),
    );
    const link = built.links[0];
    expect(link.across).toBe(false);
    const a = built.byId.get(String(link.source))!;
    const b = built.byId.get(String(link.target))!;
    const out: Vec3 = { x: 0, y: 0, z: 0 };
    controlOf(built, a, b, false, out);
    const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, z: (a.z + b.z) / 2 };
    const span = apart(a, b);
    const bow = apart(out, mid);
    /* Enough to tell two relations apart, not enough to be a detour. */
    expect(bow).toBeGreaterThan(0);
    expect(bow).toBeLessThan(span * 0.3);
  });

  it("samples an arc between lobes more finely than one inside one", () => {
    const built = scene(
      flatten(
        field(
          ["src/a.py", "src/b.py", "tests/x.py"],
          [
            ["src/a.py", "src/b.py", "import"],
            ["src/a.py", "tests/x.py", "import"],
          ],
        ),
      ),
    );
    const near = built.links.find((link) => !link.across)!;
    const across = built.links.find((link) => link.across)!;
    expect(near.samples).toBe(SAMPLES_NEAR);
    expect(across.samples).toBe(SAMPLES_ACROSS);
  });

  it("walks the quadratic from one end to the other", () => {
    const a = { x: 0, y: 0, z: 0 };
    const c = { x: 5, y: 10, z: 0 };
    const b = { x: 10, y: 0, z: 0 };
    const out: Vec3 = { x: 0, y: 0, z: 0 };
    expect(pointOn3d(a, c, b, 0, out)).toEqual(a);
    expect({ ...pointOn3d(a, c, b, 1, out) }).toEqual(b);
    pointOn3d(a, c, b, 0.5, out);
    expect(out.x).toBeCloseTo(5, 6);
    expect(out.y).toBeCloseTo(5, 6);
  });
});

/* ------------------------------------------------------------------ *
 * Buffers
 * ------------------------------------------------------------------ */

describe("graph3d — how much room the geometry needs", () => {
  it("draws half a dashed relation's segments", () => {
    expect(drawnSegments(SAMPLES_NEAR, false)).toBe(4);
    expect(drawnSegments(SAMPLES_NEAR, true)).toBe(2);
    expect(drawnSegments(SAMPLES_ACROSS, true)).toBe(6);
    expect(drawnSegments(1, false)).toBe(0);
  });

  it("asks for exactly the vertices its links will write", () => {
    const built = scene(
      flatten(
        field(
          ["src/a.py", "src/b.py", "tests/x.py"],
          [
            ["src/a.py", "src/b.py", "import"],
            ["src/a.py", "tests/x.py", "gt_call"],
          ],
        ),
      ),
    );
    const wanted = built.links.reduce(
      (sum, link) => sum + drawnSegments(link.samples, link.dashed) * 2,
      0,
    );
    expect(built.edgeVertices).toBe(wanted);
  });

  it("marks a co-touch relation as the dashed one", () => {
    const one = buildField(
      graph(["src/a.py", "src/b.py"]),
      new Set(["src/a.py\u0000src/b.py"]),
    );
    flatten(one);
    const built = scene(one);
    expect(built.links).toHaveLength(1);
    expect(built.links[0].kind).toBe("cotouch");
    expect(built.links[0].dashed).toBe(true);
  });
});

/* ------------------------------------------------------------------ *
 * Framing, projection and picking
 * ------------------------------------------------------------------ */

describe("graph3d — framing", () => {
  it("has no bounds for nothing, and holds every particle otherwise", () => {
    expect(bounds3d([])).toBeNull();
    const built = scene(flatten(field(["src/a.py", "tests/x.py"])));
    const ball = bounds3d(built.nodes)!;
    for (const one of built.nodes) {
      expect(
        Math.hypot(one.x - ball.x, one.y - ball.y, one.z - ball.z) + one.r,
      ).toBeLessThanOrEqual(ball.r + 1e-6);
    }
  });

  it("stands further back for a bigger field and a narrower panel", () => {
    expect(fitDistance(200, 45, 1.6)).toBeGreaterThan(fitDistance(100, 45, 1.6));
    /* A tall thin panel is the constrained axis, so it needs more room
       than a square one — the field is framed, not cropped. */
    expect(fitDistance(100, 45, 0.4)).toBeGreaterThan(fitDistance(100, 45, 1));
  });
});

/** A textbook perspective matrix, column-major, as three.js stores one. */
function perspective(fov: number, aspect: number, near: number, far: number) {
  const f = 1 / Math.tan((fov * Math.PI) / 360);
  const m = new Float32Array(16);
  m[0] = f / aspect;
  m[5] = f;
  m[10] = (far + near) / (near - far);
  m[11] = -1;
  m[14] = (2 * far * near) / (near - far);
  return m;
}

function makeNode(id: string, x: number, y: number, z: number, r = 6): Node3D {
  return { id, r, cluster: "src", hue: 0, x, y, z };
}

describe("graph3d — projection and picking", () => {
  const m = perspective(90, 1, 1, 1000);
  const width = 600;
  const height = 400;

  it("puts a particle straight ahead in the middle of the panel", () => {
    const nodes = [makeNode("a", 0, 0, -100)];
    const out = new Float32Array(PROJECT_STRIDE);
    project3d(m, nodes, width, height, out);
    expect(out[0]).toBeCloseTo(width / 2, 4);
    expect(out[1]).toBeCloseTo(height / 2, 4);
    expect(out[2]).toBeCloseTo(100, 4);
    /* r 6, focal length 1, half a 400-pixel panel, 100 away. */
    expect(out[3]).toBeCloseTo((6 * 200) / 100, 4);
  });

  it("shrinks a particle as it goes back", () => {
    const nodes = [makeNode("near", 0, 0, -50), makeNode("far", 0, 0, -400)];
    const out = new Float32Array(2 * PROJECT_STRIDE);
    project3d(m, nodes, width, height, out);
    expect(out[3]).toBeGreaterThan(out[PROJECT_STRIDE + 3]);
  });

  it("refuses to place anything behind the camera", () => {
    const nodes = [makeNode("behind", 0, 0, 100)];
    const out = new Float32Array(PROJECT_STRIDE);
    project3d(m, nodes, width, height, out);
    expect(out[2]).toBe(-1);
    expect(hitTest3d(out, 1, width / 2, height / 2)).toBe(-1);
  });

  it("picks what is under the pointer, and nothing where there is none", () => {
    const nodes = [makeNode("a", 0, 0, -100)];
    const out = new Float32Array(PROJECT_STRIDE);
    project3d(m, nodes, width, height, out);
    expect(hitTest3d(out, 1, width / 2, height / 2)).toBe(0);
    expect(hitTest3d(out, 1, 10, 10)).toBe(-1);
  });

  it("picks the nearer of two particles that overlap on screen", () => {
    const nodes = [makeNode("far", 0, 0, -300), makeNode("near", 0, 0, -80)];
    const out = new Float32Array(2 * PROJECT_STRIDE);
    project3d(m, nodes, width, height, out);
    /* Both land on the centre; the one in front is the one being
       pointed at, and it is also the one drawn on top. */
    expect(hitTest3d(out, 2, width / 2, height / 2)).toBe(1);
  });

  it("orders particles furthest first, which is how they are drawn", () => {
    const nodes = [
      makeNode("near", 0, 0, -50),
      makeNode("far", 0, 0, -500),
      makeNode("middle", 0, 0, -200),
    ];
    const out = new Float32Array(3 * PROJECT_STRIDE);
    project3d(m, nodes, width, height, out);
    const order = new Uint32Array(3);
    depthOrder(out, 3, order);
    expect([...order].map((i) => nodes[i].id)).toEqual([
      "far",
      "middle",
      "near",
    ]);
  });
});

/* ------------------------------------------------------------------ *
 * Coming back to a layout
 * ------------------------------------------------------------------ */

describe("graph3d — the one-slot scene cache", () => {
  it("hands the same layout back for the same field, and no other", () => {
    forgetScene();
    const built = scene(flatten(field(["src/a.py", "src/b.py"])));
    expect(recallScene(built.signature)).toBeNull();
    rememberScene(built);
    expect(recallScene(built.signature)).toBe(built);
    expect(recallScene("some other field")).toBeNull();
    forgetScene();
    expect(recallScene(built.signature)).toBeNull();
  });

  it("never remembers an empty layout", () => {
    forgetScene();
    const empty = scene(field([]));
    rememberScene(empty);
    expect(recallScene(empty.signature)).toBeNull();
  });
});
