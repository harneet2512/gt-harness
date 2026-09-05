import { describe, expect, it } from "vitest";
import type { GraphNode, SessionGraph } from "../api";
import {
  buildField,
  buildRelations,
  clusterHue,
  fileRadius,
  MAX_PARTICLES,
  neighboursOf,
  pairKey,
  relationsFor,
  type ParticleField,
} from "../graph";
import {
  bounds,
  clusterAnchors,
  createSim,
  fitTransform,
  hitTest,
  recenter,
} from "../graphSim";

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

describe("graph — building the field", () => {
  it("turns nodes into particles and edges into filaments", () => {
    const field = buildField(
      graph(
        ["src/a.py", "src/b.py", "README"],
        [["src/a.py", "src/b.py", "import"]],
      ),
      NO_COTOUCH,
    );
    expect(field.particles.map((p) => p.id).sort()).toEqual([
      "README",
      "src/a.py",
      "src/b.py",
    ]);
    expect(field.filaments).toHaveLength(1);
    expect(field.filaments[0]).toMatchObject({ kind: "import", weight: 1 });
    expect(field.clusters).toEqual(["", "src"]);
    expect(field.folded).toBe(0);
  });

  it("gives an empty graph the shared empty field", () => {
    const field = buildField(graph([]), NO_COTOUCH);
    expect(field.particles).toEqual([]);
    expect(field.signature).toBe("");
  });

  it("adds a co-touch filament only where no relation was declared", () => {
    const g = graph(
      ["a.py", "b.py", "c.py"],
      [["a.py", "b.py", "import"]],
    );
    const cotouch = new Set([pairKey("a.py", "b.py"), pairKey("a.py", "c.py")]);
    const field = buildField(g, cotouch);
    const kinds = field.filaments.map((f) => f.kind).sort();
    // a–b is already an import; only a–c earns a co-touch filament.
    expect(kinds).toEqual(["cotouch", "import"]);
  });

  it("drops an unknown edge kind onto `import` rather than losing it", () => {
    const field = buildField(
      graph(["a.py", "b.py"], [["a.py", "b.py", "who_knows"]]),
      NO_COTOUCH,
    );
    expect(field.filaments[0].kind).toBe("import");
  });
});

describe("graph — reconciliation", () => {
  it("hands back the very same object when the signature is unchanged", () => {
    const g = graph(["a.py", "b.py"], [["a.py", "b.py", "import"]]);
    const first = buildField(g, NO_COTOUCH);
    const second = buildField(g, NO_COTOUCH, first);
    // Identical layout problem: React must see no change and d3 must not
    // be restarted.
    expect(second).toBe(first);
    expect(second.signature).toBe(first.signature);
  });

  it("refreshes size and radius in place on an unchanged signature", () => {
    const first = buildField(graph(["a.py"]), NO_COTOUCH);
    const grown: SessionGraph = { ...graph(["a.py"]), nodes: [node("a.py", 40_000)] };
    const second = buildField(grown, NO_COTOUCH, first);
    expect(second).toBe(first);
    expect(second.byId.get("a.py")!.size).toBe(40_000);
    expect(second.byId.get("a.py")!.r).toBe(fileRadius(40_000));
  });

  it("keeps the position, velocity and pin of a particle that carried over", () => {
    const before = buildField(graph(["a.py", "b.py"]), NO_COTOUCH);
    const a = before.byId.get("a.py")!;
    a.x = 12;
    a.y = -8;
    a.vx = 0.5;
    a.vy = -0.5;
    a.fx = 12;
    a.fy = -8;

    // A new file arrives: a different signature, so a genuinely new field.
    const after = buildField(graph(["a.py", "b.py", "c.py"]), NO_COTOUCH, before);
    expect(after).not.toBe(before);
    expect(after.byId.get("a.py")).toMatchObject({
      x: 12,
      y: -8,
      vx: 0.5,
      vy: -0.5,
      fx: 12,
      fy: -8,
    });
    expect(after.byId.get("c.py")!.x).toBeUndefined();
  });
});

describe("graph — folding past the cap", () => {
  it("folds the deepest directories until the field fits", () => {
    const paths = ["top.py"];
    for (let i = 0; i < MAX_PARTICLES + 100; i += 1) {
      paths.push(`pkg/deep/f${i}.py`);
    }
    const field = buildField(graph(paths), NO_COTOUCH);

    expect(field.particles.length).toBeLessThan(paths.length);
    const dirs = field.particles.filter((p) => p.kind === "dir");
    expect(dirs).toHaveLength(1);
    expect(dirs[0].path).toBe("pkg/deep/");
    expect(dirs[0].count).toBe(MAX_PARTICLES + 100);
    // Every file it swallowed is accounted for, none double-counted.
    expect(field.folded).toBe(MAX_PARTICLES + 100 - 1);
    expect(field.resolve.get("pkg/deep/f0.py")).toBe("dir:pkg/deep");
  });

  it("leaves a field under the cap entirely alone", () => {
    const field = buildField(graph(["a/b/c.py", "a/b/d.py"]), NO_COTOUCH);
    expect(field.particles.every((p) => p.kind === "file")).toBe(true);
    expect(field.folded).toBe(0);
  });
});

describe("graph — relations and neighbours", () => {
  const g = graph(
    ["a.py", "b.py", "c.py"],
    [
      ["a.py", "b.py", "import"],
      ["a.py", "c.py", "gt_call"],
    ],
  );

  it("splits imports from GT relations, in both directions", () => {
    const table = buildRelations(g);
    expect(relationsFor(table, "a.py").imports.map((e) => e.target)).toEqual([
      "b.py",
    ]);
    expect(relationsFor(table, "b.py").importedBy.map((e) => e.source)).toEqual([
      "a.py",
    ]);
    expect(relationsFor(table, "a.py").gtOut).toHaveLength(1);
    expect(relationsFor(table, "c.py").gtIn).toHaveLength(1);
    // A path nobody relates to answers with the shared empty record.
    expect(relationsFor(table, "nope.py").imports).toEqual([]);
  });

  it("lists every particle one filament away", () => {
    const near = neighboursOf(buildField(g, NO_COTOUCH));
    expect([...near.get("a.py")!].sort()).toEqual(["b.py", "c.py"]);
    expect([...near.get("b.py")!]).toEqual(["a.py"]);
  });

  it("keys a pair the same whichever end it is given", () => {
    expect(pairKey("a", "b")).toBe(pairKey("b", "a"));
  });

  it("gives every cluster its own hue and falls back for an unknown one", () => {
    const clusters = ["", "src", "tests"];
    expect(clusterHue(clusters, "src")).not.toBe(clusterHue(clusters, "tests"));
    expect(clusterHue(clusters, "nope")).toBe(clusterHue(clusters, ""));
  });
});

describe("graphSim — seeding and framing", () => {
  it("anchors one cluster at the origin and several on a ring", () => {
    expect(clusterAnchors([])).toEqual(new Map());
    expect(clusterAnchors(["src"]).get("src")).toEqual({ x: 0, y: 0 });
    const many = clusterAnchors(["a", "b", "c"]);
    expect(many.size).toBe(3);
    expect(many.get("a")).not.toEqual(many.get("b"));
  });

  it("seeds a new particle beside the neighbour it is joined to", () => {
    const field: ParticleField = buildField(
      graph(["src/a.py", "src/b.py"], [["src/a.py", "src/b.py", "import"]]),
      NO_COTOUCH,
    );
    const a = field.byId.get("src/a.py")!;
    a.x = 500;
    a.y = -300;

    createSim(field, clusterAnchors(field.clusters));

    const b = field.byId.get("src/b.py")!;
    const reach = a.r + b.r + 6;
    // Not at the origin, and within one touching distance of its neighbour.
    expect(Math.hypot(b.x! - a.x!, b.y! - a.y!)).toBeCloseTo(reach, 5);
  });

  it("seeds an unconnected particle in its own cluster region instead", () => {
    const field = buildField(graph(["lonely.py"]), NO_COTOUCH);
    createSim(field, clusterAnchors(field.clusters));
    const p = field.byId.get("lonely.py")!;
    expect(Number.isFinite(p.x!)).toBe(true);
    expect(Number.isFinite(p.y!)).toBe(true);
  });

  it("frames a field, and centres an empty one", () => {
    const field = buildField(graph(["a.py", "b.py"]), NO_COTOUCH);
    const [a, b] = field.particles;
    a.x = -100;
    a.y = -50;
    b.x = 100;
    b.y = 50;

    const box = bounds(field.particles)!;
    expect(box.minX).toBeLessThan(-100);
    expect(box.maxY).toBeGreaterThan(50);

    const t = fitTransform(field.particles, 800, 600);
    expect(t.k).toBeGreaterThan(0);
    expect(bounds([])).toBeNull();

    const empty = fitTransform([], 800, 600);
    expect([empty.x, empty.y, empty.k]).toEqual([400, 300, 1]);
  });

  it("hits the particle under the pointer and nothing else", () => {
    const field = buildField(graph(["a.py", "b.py"]), NO_COTOUCH);
    const [a, b] = field.particles;
    a.x = 0;
    a.y = 0;
    b.x = 300;
    b.y = 300;
    const t = fitTransform([], 200, 200); // identity-ish: translate(100,100) k=1

    expect(hitTest(field.particles, t, 100, 100)).toBe(a);
    expect(hitTest(field.particles, t, 400, 400)).toBe(b);
    expect(hitTest(field.particles, t, 220, 40)).toBeNull();
  });

  it("keeps the centred world point when the canvas resizes", () => {
    const t = fitTransform([], 800, 600);
    expect(recenter(t, 0, 0)).toBe(t);
    const moved = recenter(t, 100, 40);
    expect([moved.x, moved.y, moved.k]).toEqual([450, 320, 1]);
  });
});
