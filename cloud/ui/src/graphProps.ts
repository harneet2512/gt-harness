/* ------------------------------------------------------------------ *
 * What a view of the field needs to draw it.
 *
 * Types only — nothing here survives compilation. It exists so the flat
 * canvas and the 3D one take *the same* props: they are two drawings of
 * one session, and a prop that only one of them has is a fact only one of
 * them can tell you, which is how two views of the same thing start
 * disagreeing.
 * ------------------------------------------------------------------ */

import type { DiffFile } from "./api";
import type { ParticleField } from "./graph";
import type { Attention } from "./trail";
import type { WorkerTrail } from "./useGraphView";

export interface GraphViewProps {
  /** Persistence key for the camera; null outside a session. */
  sessionId: string | null;
  field: ParticleField;
  neighbours: ReadonlyMap<string, ReadonlySet<string>>;
  /** Keyed by particle id. */
  attention: ReadonlyMap<string, Attention>;
  currentStep: number;
  edited: ReadonlyMap<string, DiffFile>;
  positionId: string | null;
  running: boolean;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  /** Search result ids; null when the box is empty. */
  matches: ReadonlySet<string> | null;
  labels: boolean;
  /** Particle ids the agent walked, in order, up to the scrub cutoff. */
  trailIds: readonly string[];
  /** Identity of that walk: a change means replay, which never animates. */
  trailToken: string;
  animate: boolean;
  /** Every worker agent's walk across the same field, in its own colour. */
  workerTrails: readonly WorkerTrail[];
  /** Particle id to the agents on it, so a shared one shows all of them. */
  presence: ReadonlyMap<string, readonly string[]>;
  /** The agent drawn at full strength — hover or isolate. Null for all. */
  focusAgent: string | null;
  /**
   * Whether a worker's new waypoints animate. Separate from `animate`: a
   * worker runs on its own clock, so its trail moves while the primary
   * session sits idle.
   */
  animateWorkers: boolean;
  /** Incremented by the toolbar to ask for a fit. */
  fitToken: number;
  onZoom: (k: number) => void;
  emptyText: string | null;
}
