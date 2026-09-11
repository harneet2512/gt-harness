import {
  BoxGeometry,
  BufferGeometry,
  CanvasTexture,
  ConeGeometry,
  CylinderGeometry,
  Float32BufferAttribute,
  Matrix4,
  Shape,
  SRGBColorSpace,
  ExtrudeGeometry,
} from "three";
import { mergeGeometries, mergeVertices } from "three/addons/utils/BufferGeometryUtils.js";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
export function roofFootprint(kind:number) {
  const roofs=[
    {x:0,z:0,width:1,depth:1},
    {x:0,z:0,width:.55,depth:.65},
    {x:-.2,z:0,width:.45,depth:.5},
    {x:-.27,z:0,width:.44,depth:.85},
    {x:0,z:0,width:.28,depth:.95},
    {x:-.14,z:-.1,width:.55,depth:.62},
  ];
  return roofs[kind]??roofs[0];
}
/** Unit footprints; all architectural components stay inside their plot bounds. */
export function buildingGeometry(kind: number): BufferGeometry {
  const parts: BufferGeometry[] = [];
  const box = (
    x: number,
    y: number,
    z: number,
    w: number,
    h: number,
    d: number,
  ) => {
    const g = new RoundedBoxGeometry(w, h, d, 2, 0.035);
    g.applyMatrix4(new Matrix4().makeTranslation(x, y + h / 2, z));
    parts.push(g);
    // A quiet recessed roof and ceramic coping make each volume legible.
    const rim=.025;
    for(const [rx,rz,rw,rd] of [[-w/2+rim/2,0,rim,d],[w/2-rim/2,0,rim,d],[0,-d/2+rim/2,w,rim],[0,d/2-rim/2,w,rim]]) {
      const coping=new RoundedBoxGeometry(rw,.018,rd,2,.006);
      coping.translate(x+rx,y+h+.005,z+rz);parts.push(coping);
    }
  };
  switch (kind) {
    case 0:
      box(0, 0, 0, 1, 0.48, 1);
      box(-0.2, 0.48, -0.15, 0.4, 0.1, 0.4);   // rooftop plant
      break;
    case 1:
      box(0, 0, 0, 0.55, 1, 0.65);
      box(0, 1, 0, 0.09, 0.22, 0.09);          // antenna spire
      break;
    case 2:
      box(0, 0, 0, 1, 0.38, 1);
      box(-0.12, 0.38, 0, 0.72, 0.34, 0.74);
      box(-0.2, 0.72, 0, 0.45, 0.28, 0.5);
      break;
    case 3:
      box(-0.27, 0, 0, 0.44, 0.85, 0.85);
      box(0.27, 0, 0.1, 0.44, 0.6, 0.85);
      box(0, 0.58, 0.05, 0.5, 0.05, 0.5);      // the skybridge
      break;
    case 4:
      box(0, 0, 0, 0.28, 1, 0.95);
      box(-0.3, 0, 0.1, 0.32, 0.6, 0.65);
      box(0.3, 0, -0.1, 0.32, 0.78, 0.65);
      break;
    case 5:
      box(0, 0, 0, 1, 0.2, 1);
      box(-0.14, 0.2, -0.1, 0.55, 0.8, 0.62);
      box(0.24, 0.2, 0.18, 0.3, 0.45, 0.4);    // podium annex
      break;
    default:
      box(0, 0, 0, 0.84, 0.24, 0.84);
      box(0, 0.24, 0, 1, 0.08, 1);
  }
  const out = mergeGeometries(parts);
  parts.forEach((g) => g.dispose());
  const geometry = out ?? new BoxGeometry();
  geometry.computeBoundingBox();
  geometry.scale(1, 1 / (geometry.boundingBox?.max.y || 1), 1);
  const normals = geometry.getAttribute("normal");
  const colors = new Float32Array(normals.count * 3);
  for (let i = 0; i < normals.count; i++) {
    const shade =
      normals.getY(i) > 0.85 ? 1 : normals.getY(i) > 0.1 ? 0.90 : 0.94;
    colors.set([shade, shade, shade], i * 3);
  }
  geometry.setAttribute("color", new Float32BufferAttribute(colors, 3));
  return geometry;
}

/** Crisp architectural platform: a softened rectangle with the barest
 *  per-district wobble — a plinth, not an amoeba. */
export function terraceGeometry(seed: number): BufferGeometry {
  const shape = new Shape();
  for (let i=0; i<96; i++) {
    const a=i/96*Math.PI*2;
    const radius=0.5*(1+0.006*Math.sin(3*a+seed)+0.004*Math.cos(5*a+seed*.7));
    const power=.72+(seed%3)*.08;
    const x=Math.sign(Math.cos(a))*Math.pow(Math.abs(Math.cos(a)),power)*radius;
    const y=Math.sign(Math.sin(a))*Math.pow(Math.abs(Math.sin(a)),power)*radius;
    if(i===0) shape.moveTo(x,y); else shape.lineTo(x,y);
  }
  shape.closePath();
  const raw=new ExtrudeGeometry(shape,{depth:1.4,bevelEnabled:true,bevelSegments:5,steps:1,bevelSize:0.018,bevelThickness:0.2,curveSegments:64});
  const g=mergeVertices(raw);raw.dispose();g.computeVertexNormals();
  g.rotateX(-Math.PI/2);
  const normals=g.getAttribute("normal");
  const colors=new Float32Array(normals.count*3);
  for(let i=0;i<normals.count;i++) {
    const shade=normals.getY(i)>.85?1:normals.getY(i)>.1?.97:.90;
    colors.set([shade,shade,shade],i*3);
  }
  g.setAttribute("color",new Float32BufferAttribute(colors,3));
  return g;
}

/* ---------------- facade detail ----------------
 * The windows are texture, not geometry: one small canvas per material,
 * so six archetypes share two draws' worth of texture memory. `facade`
 * keeps the face near-white so the pastel module tints still read;
 * `lit` is the sparse warm emissive layer on top. */
export function facadeTexture(variant = 0): CanvasTexture {
  const c = document.createElement("canvas");
  c.width = 64; c.height = 96;
  const t = new CanvasTexture(c);
  t.colorSpace = SRGBColorSpace;
  const g = c.getContext("2d");
  if (!g) return t;
  g.fillStyle = "#ffffff";
  g.fillRect(0, 0, 64, 96);
  if (variant === 1) {
    // Horizontal banding — slab blocks read as long courses of windows.
    for (let r = 0; r < 10; r++) {
      g.fillStyle = "rgba(84,100,128,0.38)";
      g.fillRect(4, r * 9.6 + 3, 56, 3.4);
    }
  } else if (variant === 2) {
    // Sparse large panes — the civic read for podium archetypes.
    for (let r = 0; r < 5; r++) {
      for (let col = 0; col < 3; col++) {
        g.fillStyle = "rgba(84,100,128,0.40)";
        g.fillRect(6 + col * 19, 6 + r * 18, 13, 12);
      }
    }
  } else {
    const cols = 4, rows = 8;
    const cw = 64 / cols, rh = 96 / rows;
    for (let r = 0; r < rows; r++) {
      for (let col = 0; col < cols; col++) {
        // A window reveal that reads from the overview camera.
        g.fillStyle = "rgba(84,100,128,0.42)";
        g.fillRect(col * cw + cw * 0.22, r * rh + rh * 0.24, cw * 0.56, rh * 0.5);
      }
    }
  }
  // The ground floor reads as entrances, slightly deeper.
  g.fillStyle = "rgba(80,95,120,0.22)";
  for (let col = 0; col < 4; col++) {
    g.fillRect(col * 16 + 4.5, 96 - 6, 10, 5);
  }
  t.needsUpdate = true;
  return t;
}

export function litFacadeTexture(seed = 7): CanvasTexture {
  const c = document.createElement("canvas");
  c.width = 64; c.height = 96;
  const t = new CanvasTexture(c);
  t.colorSpace = SRGBColorSpace;
  const g = c.getContext("2d");
  if (!g) return t;
  g.fillStyle = "#000000";
  g.fillRect(0, 0, 64, 96);
  const cols = 4, rows = 8;
  const cw = 64 / cols, rh = 96 / rows;
  let n = seed;
  const rand = () => (n = (n * 16807) % 2147483647) / 2147483647;
  for (let r = 0; r < rows; r++) {
    for (let col = 0; col < cols; col++) {
      if (rand() < 0.16) {
        g.fillStyle = `rgba(255,${196 + Math.floor(rand() * 40)},140,${0.7 + rand() * 0.3})`;
        g.fillRect(col * cw + cw * 0.22, r * rh + rh * 0.24, cw * 0.56, rh * 0.5);
      }
    }
  }
  t.needsUpdate = true;
  return t;
}

/** A small planted tree: cone canopy on a stub trunk, unit-scaled. */
export function treeGeometry(): BufferGeometry {
  const canopy = new ConeGeometry(0.16, 0.55, 6);
  canopy.translate(0, 0.42, 0);
  const trunk = new CylinderGeometry(0.035, 0.05, 0.22, 5);
  trunk.translate(0, 0.11, 0);
  const g = mergeGeometries([canopy, trunk]) ?? canopy;
  canopy.dispose(); trunk.dispose();
  return g;
}
