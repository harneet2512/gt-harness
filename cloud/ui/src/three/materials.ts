/* ------------------------------------------------------------------ *
 * Two shaders, and no more than two.
 *
 * The flat view draws exactly four things: filled discs, hairline rings
 * and arcs around them, hairlines between them, and text. The 3D view
 * draws the same four. Lines and text have renderers already, so what is
 * left is a disc and a ring — one instanced billboard each, one draw call
 * each, however many particles there are.
 *
 * Both are unlit. There is no light rig, because a shaded ball is a
 * *sphere* and a file is not a sphere — it is the same disc the flat view
 * draws, at a distance. Depth is carried by two things only: the
 * perspective divide, which makes near particles larger, and a linear
 * fade into the paper colour, which makes far ones recede. That is the
 * whole depth treatment.
 *
 * Neither shader uses a derivative instruction. The antialias width is
 * computed in the vertex shader from the particle's own radius in
 * pixels, which is exact rather than estimated, cheaper, and does not
 * depend on a software rasteriser implementing `fwidth` well.
 * ------------------------------------------------------------------ */

import {
  Color,
  DoubleSide,
  NormalBlending,
  ShaderMaterial,
  type IUniform,
} from "three";

/** Everything both shaders need to know about the theme and the camera. */
export interface FieldUniforms {
  [name: string]: IUniform<unknown>;
  /** The ink colour, for the hairline around a particle. */
  uInk: IUniform<Color>;
  /** The paper colour: what distance fades into. */
  uFogColor: IUniform<Color>;
  /** Where the fade starts and ends, in view-space units. */
  uFogNear: IUniform<number>;
  uFogFar: IUniform<number>;
  /** How far the fade is allowed to go. Never 1: nothing disappears. */
  uFogDepth: IUniform<number>;
  /** Half the drawing buffer height, for the pixel-exact antialias. */
  uHalfHeight: IUniform<number>;
}

function fieldUniforms(): FieldUniforms {
  return {
    uInk: { value: new Color(0.9, 0.9, 0.9) },
    uFogColor: { value: new Color(0.06, 0.07, 0.08) },
    uFogNear: { value: 1 },
    uFogFar: { value: 1000 },
    /* Two thirds of the way to the paper at the back of the field.
       Never all of it: a file that has receded is still a file. */
    uFogDepth: { value: 0.66 },
    uHalfHeight: { value: 300 },
  };
}

/* ------------------------------------------------------------------ *
 * The disc
 * ------------------------------------------------------------------ */

const DISC_VERTEX = /* glsl */ `
attribute vec3 iOffset;
attribute float iRadius;
attribute vec3 iColor;
attribute float iAlpha;

uniform float uHalfHeight;
uniform float uFogNear;
uniform float uFogFar;

varying vec2 vQuad;
varying vec3 vColor;
varying float vAlpha;
varying float vAA;
varying float vFog;

void main() {
  vec4 mv = modelViewMatrix * vec4(iOffset, 1.0);

  /* The quad's own coordinates, mapped to a unit disc. */
  vQuad = position.xy * 2.0;
  /* Billboard: the offset is added in view space, so the quad always
     faces the camera without a per-instance rotation. */
  mv.xy += position.xy * (2.0 * iRadius);

  float depth = max(-mv.z, 0.001);
  /* How many pixels across this disc is, from the vertical focal length.
     One pixel of that is the antialias width, in disc units. */
  float px = iRadius * projectionMatrix[1][1] * uHalfHeight / depth;
  vAA = clamp(1.0 / max(px, 0.5), 0.008, 0.9);

  vColor = iColor;
  vAlpha = iAlpha;
  vFog = clamp((depth - uFogNear) / max(1.0, uFogFar - uFogNear), 0.0, 1.0);

  gl_Position = projectionMatrix * mv;
}
`;

const DISC_FRAGMENT = /* glsl */ `
precision mediump float;

uniform vec3 uInk;
uniform vec3 uFogColor;
uniform float uFogDepth;
uniform float uRim;

varying vec2 vQuad;
varying vec3 vColor;
varying float vAlpha;
varying float vAA;
varying float vFog;

void main() {
  float d = length(vQuad);
  float fill = 1.0 - smoothstep(1.0 - vAA, 1.0 + vAA, d);
  if (fill <= 0.003 || vAlpha <= 0.003) discard;

  /* The hairline the flat view strokes around every particle, held at a
     little over a pixel whatever the particle's size or distance. */
  float rim = uRim * smoothstep(1.0 - 2.2 * vAA, 1.0 - 0.4 * vAA, d);
  vec3 col = mix(vColor, uInk, rim);
  col = mix(col, uFogColor, vFog * uFogDepth);

  gl_FragColor = vec4(col, fill * vAlpha);
}
`;

/**
 * A filled disc. `rim` is how strongly the outline shows: the particles
 * carry the flat view's hairline, a travelling signal's head does not —
 * it is a light, not an object.
 */
export function discMaterial(rim: number): ShaderMaterial {
  return new ShaderMaterial({
    uniforms: { ...fieldUniforms(), uRim: { value: rim } },
    vertexShader: DISC_VERTEX,
    fragmentShader: DISC_FRAGMENT,
    transparent: true,
    /* Painter's algorithm, as in the flat view: everything here is a
       blended billboard, and a depth buffer would only have their edges
       fighting each other. Order is set explicitly instead. */
    depthTest: false,
    depthWrite: false,
    blending: NormalBlending,
    side: DoubleSide,
  });
}

/* ------------------------------------------------------------------ *
 * The ring
 * ------------------------------------------------------------------ */

const RING_VERTEX = /* glsl */ `
attribute vec3 iOffset;
attribute float iRadius;
attribute float iWidth;
attribute vec3 iColor;
attribute float iAlpha;
/** Start angle and span, in radians: 0 is straight up, and it runs
    clockwise on screen — the same wedge the flat view cuts. */
attribute vec2 iArc;

uniform float uHalfHeight;
uniform float uFogNear;
uniform float uFogFar;

varying vec2 vQuad;
varying vec3 vColor;
varying float vAlpha;
varying float vAA;
varying float vFog;
varying float vRing;
varying float vBand;
varying vec2 vArc;

void main() {
  float reach = iRadius + iWidth;
  vec4 mv = modelViewMatrix * vec4(iOffset, 1.0);

  vQuad = position.xy * 2.0;
  mv.xy += position.xy * (2.0 * reach);

  float depth = max(-mv.z, 0.001);
  float px = reach * projectionMatrix[1][1] * uHalfHeight / depth;
  vAA = clamp(1.0 / max(px, 0.5), 0.008, 0.9);

  vRing = iRadius / max(reach, 0.0001);
  vBand = (iWidth * 0.5) / max(reach, 0.0001);
  vArc = iArc;
  vColor = iColor;
  vAlpha = iAlpha;
  vFog = clamp((depth - uFogNear) / max(1.0, uFogFar - uFogNear), 0.0, 1.0);

  gl_Position = projectionMatrix * mv;
}
`;

const RING_FRAGMENT = /* glsl */ `
precision mediump float;

#define TWO_PI 6.283185307179586

uniform vec3 uFogColor;
uniform float uFogDepth;

varying vec2 vQuad;
varying vec3 vColor;
varying float vAlpha;
varying float vAA;
varying float vFog;
varying float vRing;
varying float vBand;
varying vec2 vArc;

void main() {
  float d = length(vQuad);
  float band = 1.0 - smoothstep(vBand - vAA, vBand + vAA, abs(d - vRing));
  if (band <= 0.003 || vAlpha <= 0.003) discard;

  float wedge = 1.0;
  if (vArc.y < TWO_PI - 0.001) {
    /* atan(x, y) rather than atan(y, x): zero at the top, growing
       clockwise on screen, which is the convention the flat view's arcs
       already use — so an agent keeps the same quarter of the ring in
       both views. */
    float ang = atan(vQuad.x, vQuad.y);
    if (ang < 0.0) ang += TWO_PI;
    float rel = mod(ang - vArc.x + TWO_PI, TWO_PI);
    wedge = step(rel, vArc.y);
  }
  if (wedge <= 0.0) discard;

  vec3 col = mix(vColor, uFogColor, vFog * uFogDepth);
  gl_FragColor = vec4(col, band * vAlpha);
}
`;

/** A hairline ring, or one wedge of one where agents are sharing it. */
export function ringMaterial(): ShaderMaterial {
  return new ShaderMaterial({
    uniforms: fieldUniforms(),
    vertexShader: RING_VERTEX,
    fragmentShader: RING_FRAGMENT,
    transparent: true,
    depthTest: false,
    depthWrite: false,
    blending: NormalBlending,
    side: DoubleSide,
  });
}

/** Push the theme and the camera into whichever materials are on screen. */
export function applyFieldUniforms(
  material: ShaderMaterial,
  ink: { r: number; g: number; b: number },
  paper: { r: number; g: number; b: number },
  near: number,
  far: number,
  halfHeight: number,
): void {
  const u = material.uniforms;
  (u.uInk.value as Color).setRGB(ink.r, ink.g, ink.b);
  (u.uFogColor.value as Color).setRGB(paper.r, paper.g, paper.b);
  u.uFogNear.value = near;
  u.uFogFar.value = far;
  u.uHalfHeight.value = halfHeight;
}
