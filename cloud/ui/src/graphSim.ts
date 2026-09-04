/* ------------------------------------------------------------------ *
 * The force layout.
 *
 * The simulation lives in its own unbounded space centred on the origin;
 * the canvas transform decides what you see. That way resizing a panel
 * only moves the camera and never disturbs a settled layout.
 * ------------------------------------------------------------------ */

import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type Simulation,
} from "d3-force";
import { zoomIdentity, type ZoomTransform } from "d3-zoom";
import type { Filament, FilamentKind, Particle, ParticleField } from "./graph";

const DISTANCE: Record<FilamentKind, number> = {
  import: 28,
  gt_call: 22,
  gt_ref: 22,
  gt_import: 22,
  cotouch: 40,
};

const STRENGTH: Record<FilamentKind, number> = {
  import: 0.4,
  gt_call: 0.6,
  gt_ref: 0.6,
  gt_import: 0.6,
  cotouch: 0.25,
};

export const RESTART_ALPHA = 0.35;

/** Where each top-level directory pulls its files. */
export type Anchors = Map<string, { x: number; y: number }>;

export function clusterAnchors(clusters: readonly string[]): Anchors {
  const anchors: Anchors = new Map();
  if (clusters.length === 0) return anchors;
  if (clusters.length === 1) {
    anchors.set(clusters[0], { x: 0, y: 0 });
    return anchors;
  }
  // Radius grows with the number of regions, not with the viewport, so the
  // layout is stable and "fit" does the framing.
  const radius = 70 + 26 * clusters.length;
  clusters.forEach((cluster, i) => {
    const angle = (2 * Math.PI * i) / clusters.length - Math.PI / 2;
    anchors.set(cluster, {
      x: Math.cos(angle) * radius,
      y: Math.sin(angle) * radius,
    });
  });
  return anchors;
}

function anchorOf(anchors: Anchors, particle: Particle): { x: number; y: number } {
  return anchors.get(particle.cluster) ?? { x: 0, y: 0 };
}

/** Seed unplaced particles inside their own region so it forms at once. */
function seed(field: ParticleField, anchors: Anchors): void {
  field.particles.forEach((particle, i) => {
    if (particle.x !== undefined && Number.isFinite(particle.x)) return;
    const anchor = anchorOf(anchors, particle);
    const angle = i * 2.399963; // golden angle: no two seeds land together
    const spread = 8 + Math.sqrt(i) * 4;
    particle.x = anchor.x + Math.cos(angle) * spread;
    particle.y = anchor.y + Math.sin(angle) * spread;
    particle.vx = 0;
    particle.vy = 0;
  });
}

export function createSim(
  field: ParticleField,
  anchors: Anchors,
): Simulation<Particle, Filament> {
  seed(field, anchors);

  const sim = forceSimulation<Particle, Filament>(field.particles)
    .force(
      "link",
      forceLink<Particle, Filament>(field.filaments)
        .id((d) => d.id)
        .distance((d) => DISTANCE[d.kind] ?? 28)
        .strength((d) => STRENGTH[d.kind] ?? 0.4),
    )
    .force(
      "charge",
      forceManyBody<Particle>().strength((d) => -(12 + d.r * 2)),
    )
    .force("collide", forceCollide<Particle>().radius((d) => d.r + 2))
    .force("x", forceX<Particle>((d) => anchorOf(anchors, d).x).strength(0.05))
    .force("y", forceY<Particle>((d) => anchorOf(anchors, d).y).strength(0.05))
    .alphaDecay(0.03)
    .alphaMin(0.001);

  // Ticked by the canvas render loop, not by d3's own timer.
  sim.stop();
  return sim;
}

/* ------------------------------------------------------------------ *
 * Framing
 * ------------------------------------------------------------------ */

export interface Box {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export function bounds(particles: readonly Particle[]): Box | null {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;

  for (const particle of particles) {
    if (particle.x === undefined || particle.y === undefined) continue;
    minX = Math.min(minX, particle.x - particle.r);
    minY = Math.min(minY, particle.y - particle.r);
    maxX = Math.max(maxX, particle.x + particle.r);
    maxY = Math.max(maxY, particle.y + particle.r);
  }

  if (!Number.isFinite(minX)) return null;
  return { minX, minY, maxX, maxY };
}

const FIT_PAD = 48;
const MIN_K = 0.15;
const MAX_K = 6;

export function fitTransform(
  particles: readonly Particle[],
  width: number,
  height: number,
): ZoomTransform {
  const box = bounds(particles);
  if (!box || width < 2 || height < 2) {
    return zoomIdentity.translate(width / 2, height / 2);
  }

  const w = Math.max(1, box.maxX - box.minX);
  const h = Math.max(1, box.maxY - box.minY);
  const k = Math.min(
    MAX_K,
    Math.max(
      MIN_K,
      Math.min((width - FIT_PAD * 2) / w, (height - FIT_PAD * 2) / h),
    ),
  );

  const cx = (box.minX + box.maxX) / 2;
  const cy = (box.minY + box.maxY) / 2;
  return zoomIdentity.translate(width / 2, height / 2).scale(k).translate(-cx, -cy);
}

export const ZOOM_EXTENT: [number, number] = [MIN_K, MAX_K];

/** Keep the world point under the canvas centre when the canvas resizes. */
export function recenter(
  transform: ZoomTransform,
  dw: number,
  dh: number,
): ZoomTransform {
  if (dw === 0 && dh === 0) return transform;
  return zoomIdentity
    .translate(transform.x + dw / 2, transform.y + dh / 2)
    .scale(transform.k);
}

/** The particle under a canvas point, or null. */
export function hitTest(
  particles: readonly Particle[],
  transform: ZoomTransform,
  px: number,
  py: number,
): Particle | null {
  let best: Particle | null = null;
  let bestDistance = Infinity;

  for (const particle of particles) {
    if (particle.x === undefined || particle.y === undefined) continue;
    const sx = transform.x + transform.k * particle.x;
    const sy = transform.y + transform.k * particle.y;
    const reach = particle.r * transform.k + 4;
    const distance = Math.hypot(sx - px, sy - py);
    if (distance <= reach && distance < bestDistance) {
      best = particle;
      bestDistance = distance;
    }
  }

  return best;
}

/** Both ends of a filament, resolved to particles. */
export function endsOf(
  filament: Filament,
  field: ParticleField,
): [Particle, Particle] | null {
  const source =
    typeof filament.source === "string"
      ? field.byId.get(filament.source)
      : filament.source;
  const target =
    typeof filament.target === "string"
      ? field.byId.get(filament.target)
      : filament.target;
  if (!source || !target) return null;
  if (source.x === undefined || target.x === undefined) return null;
  return [source, target];
}
