/* ------------------------------------------------------------------ *
 * Every agent's departures, on one clock, for whichever view is drawing.
 *
 * The flat canvas and the 3D one show the same session, and a signal is
 * the same event in both: one agent's attention moving from one file to
 * the next. There is exactly one set of rules for when those may leave —
 * one queue per agent, one departure per frame, a stagger between two
 * agents, a ceiling on what is in the air — and it lives in
 * `SignalDirector`.
 *
 * This hook is that director plus the plumbing that feeds it, so neither
 * view owns a copy of the plumbing either. Switching modes builds a new
 * director, which is correct: nothing should still be halfway along a
 * filament that is no longer on screen.
 * ------------------------------------------------------------------ */

import { useEffect, useRef, type MutableRefObject } from "react";
import { PRIMARY_AGENT, PRIMARY_RGB, SignalDirector } from "./signals";
import { ArrivalWatch, walkStep, type Walked } from "./signalsView";
import type { WorkerTrail } from "./useGraphView";

export interface SignalInput {
  /** Particle ids the primary agent walked, in order, up to the cutoff. */
  trailIds: readonly string[];
  /** Identity of that walk: a change means replay, which never animates. */
  trailToken: string;
  animate: boolean;
  /** Every other agent's walk across the same field, in its own colour. */
  workerTrails: readonly WorkerTrail[];
  /** Workers run on their own clock, so this is separate from `animate`. */
  animateWorkers: boolean;
  reduced: boolean;
  /** Ask the view for a frame. */
  kick: () => void;
}

export interface Signals {
  director: MutableRefObject<SignalDirector>;
  /** Where hops finished. Empty in a view that does not draw arrivals. */
  arrivals: MutableRefObject<ArrivalWatch>;
}

export function useSignals(input: SignalInput): Signals {
  const { trailIds, trailToken, animate, workerTrails, animateWorkers } = input;
  const { reduced, kick } = input;

  const director = useRef(new SignalDirector());
  const arrivals = useRef(new ArrivalWatch());

  /* ---- the primary agent ---- */
  const walked = useRef<Walked>({ token: "", length: 0 });

  useEffect(() => {
    const queue = director.current.queue(PRIMARY_AGENT, PRIMARY_RGB);
    const action = walkStep(
      walked.current,
      trailToken,
      trailIds.length,
      animate,
    );
    if (action.kind === "reset") {
      queue.clear();
      arrivals.current.clear();
    } else if (action.kind === "hops") {
      for (let i = action.from; i < trailIds.length; i += 1) {
        queue.push(trailIds[i - 1], trailIds[i]);
      }
    }
    walked.current = { token: trailToken, length: trailIds.length };
    kick();
  }, [trailIds, trailToken, animate, kick]);

  /* ---- every other agent ---- *
   * Same rule, with no token of its own: a worker's trail is never
   * scrubbed, so the only replay it can suffer is the one a shrinking
   * list already reports.
   * ------------------------------------------------------------------ */
  const workerWalked = useRef(new Map<string, number>());

  useEffect(() => {
    for (const worker of workerTrails) {
      const queue = director.current.queue(worker.id, worker.rgb);
      const before: Walked = {
        token: "",
        length: workerWalked.current.get(worker.id) ?? 0,
      };
      const action = walkStep(
        before,
        "",
        worker.trailIds.length,
        animateWorkers,
      );
      if (action.kind === "reset") queue.clear();
      else if (action.kind === "hops") {
        for (let i = action.from; i < worker.trailIds.length; i += 1) {
          queue.push(worker.trailIds[i - 1], worker.trailIds[i]);
        }
      }
      workerWalked.current.set(worker.id, worker.trailIds.length);
    }

    const alive = new Set(workerTrails.map((worker) => worker.id));
    director.current.retain(alive);
    for (const id of [...workerWalked.current.keys()]) {
      if (!alive.has(id)) workerWalked.current.delete(id);
    }
    kick();
  }, [workerTrails, animateWorkers, kick]);

  /* Turning the preference on mid-flight drops whatever was travelling
     rather than letting it finish its arc. */
  useEffect(() => {
    director.current.setReduced(reduced);
    arrivals.current.clear();
    kick();
  }, [reduced, kick]);

  return { director, arrivals };
}
