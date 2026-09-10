import fs from "node:fs";
import { Mesh, OrthographicCamera, Vector3, Color } from "three";
import { SurveyorFactory } from "../src/three/surveyor.ts";
const factory = new SurveyorFactory();
function draw(cx, cy, width, height, view, dark = false, scale = 1) {
  const group = factory.create("#d97757");
  factory.body.color.set(dark ? "#69717b" : "#abb2bb");
  group.updateMatrixWorld(true);
  const camera = new OrthographicCamera(
    -13 / scale,
    13 / scale,
    13 / scale,
    -13 / scale,
    0.1,
    100,
  );
  camera.position.set(...view);
  camera.lookAt(0, 2.75, 0);
  camera.updateMatrixWorld();
  const tris = [];
  group.traverse((obj) => {
    if (!(obj instanceof Mesh)) return;
    const p = obj.geometry.attributes.position,
      idx = obj.geometry.index;
    for (let i = 0; i < (idx?.count ?? p.count); i += 3) {
      const pts = [0, 1, 2].map((j) =>
        new Vector3()
          .fromBufferAttribute(p, idx ? idx.getX(i + j) : i + j)
          .applyMatrix4(obj.matrixWorld),
      );
      const normal = pts[1]
        .clone()
        .sub(pts[0])
        .cross(pts[2].clone().sub(pts[0]))
        .normalize();
      const light =
        0.58 +
        Math.max(0, normal.dot(new Vector3(-0.4, 0.8, 0.5).normalize())) * 0.42;
      const c = new Color(obj.material.color).multiplyScalar(light);
      const projected = pts.map((v) => v.project(camera));
      tris.push({
        z: projected.reduce((s, p) => s + p.z, 0) / 3,
        svg: `<polygon points="${projected.map((p) => `${(cx + (p.x * width) / 2).toFixed(2)},${(cy - (p.y * height) / 2).toFixed(2)}`).join(" ")}" fill="#${c.getHexString()}"/>`,
      });
    }
  });
  factory.release(group);
  return tris
    .sort((a, b) => b.z - a.z)
    .map((t) => t.svg)
    .join("");
}
let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="1100" viewBox="0 0 1440 1100"><rect width="1440" height="1100" fill="#eeece5"/><style>text{font-family:Arial,sans-serif;fill:#303841}.title{font-size:28px;font-weight:bold}.label{font-size:15px}.note{font-size:13px}</style><text x="40" y="48" class="title">GT Surveyor · Canonical procedural sheet v2</text><text x="40" y="78" class="label">W18 x D12.5 x H5.5 - width:length:height 1.8:1.25:0.55 - central body 8.1 (45% of width)</text>`;
const views = [
  ["Front", [0, 5, 40]],
  ["Side", [40, 5, 0]],
  ["Top", [0, 45, 0.001]],
  ["Three-quarter", [28, 25, 30]],
];
for (let row = 0; row < 2; row++)
  for (let i = 0; i < 4; i++) {
    const x = 40 + i * 350,
      y = 105 + row * 290;
    svg += `<rect x="${x}" y="${y}" width="330" height="270" rx="5" fill="${row ? "#24282d" : "#f8f7f2"}"/>`;
    svg += draw(x + 165, y + 133, 280, 240, views[i][1], !!row);
    svg += `<text x="${x + 14}" y="${y + 248}" class="label" style="fill:${row ? "#d5dbe2" : "#303841"}">${views[i][0]} · ${row ? "graphite" : "cool gray"}</text>`;
  }
svg += `<text x="40" y="718" class="label">Shared geometry in every state · one small status emitter carries identity</text>`;
for (let i = 0; i < 4; i++) {
  const x = 40 + i * 350;
  svg += draw(x + 130, 815, 240, 170, [28, 25, 30]);
  if (i === 1)
    svg += `<path d="M ${x + 127} 831 l -28 51 h 56 Z" fill="#56b6c2" opacity=".13"/>`;
  if (i === 2)
    svg += `<path d="M ${x + 70} 878 h 130" stroke="#d97757" stroke-width="3"/>`;
  if (i === 3)
    svg += `<path d="M ${x + 15} 848 Q ${x + 40} 820 ${x + 75} 835" stroke="#d97757" stroke-width="2" fill="none"/>`;
  svg += `<text x="${x + 35}" y="910" class="label">${["Idle · settled", "Scanning · narrow cone", "Editing · roof accent", "Moving · elevated path"][i]}</text>`;
}
svg +=
  `<text x="40" y="953" class="label">Silhouette at native working pixels</text>` +
  draw(400, 950, 24, 24, [28, 25, 30], false, 1.35) +
  draw(490, 950, 32, 32, [28, 25, 30], false, 1.35) +
  `<text x="420" y="955" class="note">24px</text><text x="515" y="955" class="note">32px</text><text x="40" y="992" class="note">Four enclosed micro-rotors, short integrated arms, tapered underside, one downward aperture, two rear stabilizers.</text><text x="40" y="1018" class="note">Emitter 0.55 scene units; physical vehicle material roughness 0.42, metalness 0.45.</text><text x="40" y="1044" class="note">All views are projected from the same procedural mesh. No independently generated geometry or invented verification status.</text></svg>`;
fs.writeFileSync(new URL("../public/gt-surveyor-v2.svg", import.meta.url), svg);
factory.dispose();
console.log("Canonical sheet written from shared geometry.");
