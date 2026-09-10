import { describe, it, expect } from "vitest";
import {
  Box3,
  InstancedMesh,
  Mesh,
  MeshStandardMaterial,
  Object3D,
  Raycaster,
  Vector3,
} from "three";
import { buildingGeometry, roofFootprint } from "../three/cityGeometry";
import { SurveyorFactory, SURVEYOR } from "../three/surveyor";
describe("city geometry contracts", () => {
  it("anchors scanners on the highest physical roof, including the split tower", () => {
    const material=new MeshStandardMaterial();
    for(let kind=0;kind<6;kind++) {
      const geometry=buildingGeometry(kind),roof=roofFootprint(kind),mesh=new Mesh(geometry,material);
      mesh.updateMatrixWorld();
      const hits=new Raycaster(new Vector3(roof.x,2,roof.z),new Vector3(0,-1,0)).intersectObject(mesh);
      expect(hits.length).toBeGreaterThan(0);
      expect(hits[0].point.y).toBeGreaterThan(.94);
      geometry.dispose();
    }
    material.dispose();
  });
  it("keeps six distinct architectures within their unit plots", () => {
    const counts = [];
    for (let i = 0; i < 6; i++) {
      const g = buildingGeometry(i);
      g.computeBoundingBox();
      const b = g.boundingBox!;
      expect(b.min.x).toBeGreaterThanOrEqual(-0.501);
      expect(b.max.x).toBeLessThanOrEqual(0.501);
      expect(b.min.y).toBeGreaterThanOrEqual(-0.001);
      expect(b.max.y).toBeLessThanOrEqual(1.001);
      counts.push(g.attributes.position.count);
      g.dispose();
    }
    expect(new Set(counts).size).toBeGreaterThan(2);
  });
  it("picks the correct instance from solid roof geometry", () => {
    const g = buildingGeometry(0),
      m = new MeshStandardMaterial(),
      mesh = new InstancedMesh(g, m, 2),
      o = new Object3D();
    for (let i = 0; i < 2; i++) {
      o.position.set(i * 20, 0, 0);
      o.scale.set(10, 20, 10);
      o.updateMatrix();
      mesh.setMatrixAt(i, o.matrix);
    }
    mesh.updateMatrixWorld();
    const ray = new Raycaster(new Vector3(20, 100, 0), new Vector3(0, -1, 0));
    expect(ray.intersectObject(mesh)[0].instanceId).toBe(1);
    mesh.dispose();
    g.dispose();
    m.dispose();
  });
  it("enforces the dimensioned Surveyor silhouette for every identity", () => {
    const f = new SurveyorFactory(),
      a = f.create("#d97757"),
      b = f.create("#56b6c2");
    const size = new Box3().setFromObject(a).getSize(new Vector3());
    expect(size.x).toBeCloseTo(SURVEYOR.width, 4);
    expect(size.y).toBeCloseTo(SURVEYOR.height, 4);
    expect(size.z).toBeCloseTo(SURVEYOR.depth, 4);
    expect(size.x / size.y).toBeCloseTo(18 / 5.5, 4);
    expect(new Box3().setFromObject(b).getSize(new Vector3())).toEqual(size);
    expect(a.children[0]).toHaveProperty(
      "geometry",
      (b.children[0] as (typeof a.children)[0] & { geometry: unknown })
        .geometry,
    );
    f.release(a);
    f.release(b);
    f.dispose();
  });
});
