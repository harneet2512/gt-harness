/* ------------------------------------------------------------------ *
 * The force layout, in three dimensions.
 *
 * The same four forces the flat layout uses — link, charge, collide, and
 * a pull toward the region a file belongs to — solved on three axes
 * instead of two. `d3-force-3d` is the same velocity-Verlet integrator as
 * `d3-force` with an octree instead of a quadtree, so the constants below
 * mean what they mean in `graphSim.ts` and are kept next to them
 * deliberately.
 *
 * The one thing that is genuinely different is the anchor. In 2D the
 * directories sit on a ring and the pull is weak, because a ring has only
 * so much room and a stronger pull would flatten every region into the
 * same disc. A sphere has room, so the pull is a little firmer: the
 * regions are supposed to read as separate lobes, and a lobe you cannot
 * see the edge of is a cloud.
 *
 * This module is only ever reached through the lazily-loaded 3D view.
 * ------------------------------------------------------------------ */

import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  forceZ,
  type Simulation3D,
} from "d3-force-3d";
import type { FilamentKind } from "./graph";
import type { Link3D, Node3D, Scene3D, Vec3 } from "./graph3d";

/** The flat view's distances. A relation means the same thing in depth. */
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

/**
 * How hard a file is held in its own lobe.
 *
 * Above the flat view's 0.05, and this is the whole difference between a
 * brain and a hairball: it is what keeps `src/` and `tests/` as two
 * things you can point at rather than one ball with two colours in it.
 * Much above this and the link force stops mattering, which would be
 * worse — the relations are the view, and a lobe with no visible
 * relations inside it is a decoration.
 */
export const LOBE_PULL = 0.085;

/** A new field: settle it hard, once. Matches `RESTART_ALPHA`. */
export const RESTART_ALPHA_3D = 0.35;
/** A field that already has positions. Matches `GENTLE_ALPHA`. */
export const GENTLE_ALPHA_3D = 0.08;

/**
 * Ticks run before the first frame. Fewer than the flat view's 90
 * because the seed carries the settled flat arrangement over (see
 * `seedNode`), so this starts most of the way to an answer and the rest
 * is finished in the render loop where it costs nobody a blocked frame.
 */
export const PRESETTLE_3D = 45;

const ORIGIN: Vec3 = { x: 0, y: 0, z: 0 };

function anchorOf(scene: Scene3D, node: Node3D): Vec3 {
  return scene.anchors.get(node.cluster) ?? ORIGIN;
}

export function createSim3d(scene: Scene3D): Simulation3D<Node3D, Link3D> {
  const sim = forceSimulation<Node3D, Link3D>(scene.nodes, 3)
    .force(
      "link",
      forceLink<Node3D, Link3D>(scene.links)
        .id((node) => node.id)
        .distance((link) => DISTANCE[link.kind] ?? 28)
        .strength((link) => STRENGTH[link.kind] ?? 0.4),
    )
    .force(
      "charge",
      forceManyBody<Node3D, Link3D>().strength((node) => -(16 + node.r * 2.2)),
    )
    .force(
      "collide",
      forceCollide<Node3D, Link3D>().radius((node) => node.r + 2),
    )
    .force(
      "x",
      forceX<Node3D, Link3D>((node) => anchorOf(scene, node).x).strength(LOBE_PULL),
    )
    .force(
      "y",
      forceY<Node3D, Link3D>((node) => anchorOf(scene, node).y).strength(LOBE_PULL),
    )
    .force(
      "z",
      forceZ<Node3D, Link3D>((node) => anchorOf(scene, node).z).strength(LOBE_PULL),
    )
    .alphaDecay(0.03)
    .alphaMin(0.001);

  /* Ticked by the render loop, not by d3's own timer — which the
     constructor has already started, so this is not optional. */
  sim.stop();
  return sim;
}

/**
 * Bring a fresh scene most of the way to rest before anything is drawn,
 * and leave the rest to the loop. A scene handed over from a previous
 * mount is already settled and only needs enough energy to absorb
 * whatever the refetch added.
 */
export function settle3d(
  sim: Simulation3D<Node3D, Link3D>,
  carriedOver: boolean,
): void {
  if (carriedOver) {
    sim.alpha(GENTLE_ALPHA_3D);
    return;
  }
  for (let i = 0; i < PRESETTLE_3D; i += 1) sim.tick();
  sim.alpha(RESTART_ALPHA_3D);
}
