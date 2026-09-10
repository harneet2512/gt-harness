import { describe, it, expect } from "vitest";
import { buildCity, dimensions } from "../city";
import { EMPTY_FIELD, type ParticleField } from "../graph";
import { CameraMotion, travel } from "../cityMotion";
function field(paths: string[]): ParticleField {
  const particles = paths.map((path) => ({
    id: path,
    path,
    label: path,
    kind: "file" as const,
    count: 1,
    size: 1200,
    lang: "ts",
    cluster: path.split("/")[0],
    hue: 0,
    r: 3,
  }));
  return {
    ...EMPTY_FIELD,
    particles,
    byId: new Map(particles.map((p) => [p.id, p])),
    signature: paths.join(","),
  };
}
describe("city presentation", () => {
  it("composes the first populated response identically after an empty loading state", () => {
    const files=field(["core/a.ts","core/b.ts","ui/a.ts","tests/a.ts"]);
    expect(buildCity(files,buildCity(EMPTY_FIELD)).nodes).toEqual(buildCity(files).nodes);
  });
  it("packs deterministically independent of input order", () => {
    expect(buildCity(field(["a/x", "b/z", "a/y"])).nodes).toEqual(
      buildCity(field(["a/y", "a/x", "b/z"])).nodes,
    );
  });
  it("updates file massing from new repository metadata without moving the neighborhood", () => {
    const first=buildCity(field(["core/auth.ts","core/cache.ts","ui/view.ts"]));
    const changed=field(["core/auth.ts","core/cache.ts","ui/view.ts"]);
    changed.byId.get("core/auth.ts")!.size=64000;
    const next=buildCity(changed,first);
    expect(next.byId.get("core/auth.ts")!.height).toBeGreaterThan(first.byId.get("core/auth.ts")!.height);
    for(const before of first.nodes)expect(next.byId.get(before.id)).toMatchObject({x:before.x,y:before.y,z:before.z});
  });
  it("retains plots across edits, deletion, insertion and new districts", () => {
    const a = buildCity(field(["b/x", "b/y"]));
    const b = buildCity(field(["a/z", "b/y", "b/new"]), a);
    expect(b.byId.get("b/y")).toMatchObject({
      x: a.byId.get("b/y")!.x,
      z: a.byId.get("b/y")!.z,
    });
    expect([b.byId.get("b/new")!.x,b.byId.get("b/new")!.z]).not.toEqual([a.byId.get("b/x")!.x,a.byId.get("b/x")!.z]);
  });
  it("keeps large districts separate and instance ids unambiguous", () => {
    const a = buildCity(
      field([...Array.from({ length: 1000 }, (_, i) => `a/${i}`), "b/file"]),
    );
    expect(new Set(a.nodes.map((p) => `${p.x},${p.z}`)).size).toBe(1001);
    a.nodes.forEach((p, i) => expect(a.nodes[i].id).toBe(p.id));
  });
  it("uses compact districts with five terraces and six archetypes", () => {
    const city = buildCity(field(Array.from({length: 180}, (_, i) => `${["core", "ui", "services", "infra", "tests", "agents"][i % 6]}/${i}.ts`)));
    expect(city.districts).toHaveLength(6);
    expect(city.districts.every(d => d.terrace === 5)).toBe(true);
    expect(new Set(city.nodes.map(n => n.archetype)).size).toBe(6);
    for (const a of city.districts) for (const b of city.districts) {
      if (a.id === b.id) continue;
      expect(Math.hypot(a.x + a.width/2 - b.x - b.width/2, a.z + a.depth/2 - b.z - b.depth/2)).toBeGreaterThan((a.width + b.width)/2);
    }
  });
  it("bounds byte scaling including invalid metadata", () => {
    expect(dimensions(Infinity)).toEqual(dimensions(0));
    expect(dimensions(1e99).height).toBe(35);
  });
  it("cancels and supersedes camera ownership", () => {
    const c = new CameraMotion();
    c.begin({ x: 0, y: 0, z: 0 }, { x: 10, y: 0, z: 0 }, 0, 480);
    c.cancel();
    expect(c.sample(200)).toBeNull();
    c.begin({ x: 0, y: 0, z: 0 }, { x: 20, y: 0, z: 0 }, 0, 480, true);
    expect(c.sample(100)?.x).toBe(20);
    expect(c.busy).toBe(false);
  });
  it("travels above roofs with exact endpoints", () => {
    const a = { x: 0, y: 20, z: 0 },
      b = { x: 80, y: 40, z: 100 };
    expect(travel(a, b, 0)).toEqual(a);
    expect(travel(a, b, 1)).toEqual(b);
    expect(travel(a, b, 0.3).y).toBeGreaterThan(40);
  });
});
