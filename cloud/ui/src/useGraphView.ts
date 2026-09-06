import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DiffFile, SessionDiff, SessionGraph, TreeFile } from "./api";
import { buildGroups, type ChatState, type ThreadGroup } from "./chatState";
import {
  buildField,
  buildRelations,
  neighboursOf,
  pairKey,
  type ParticleField,
  type Relations,
} from "./graph";
import { applySavedLayout, saveLayout } from "./layoutStore";
import { splitPatch } from "./patch";
import {
  buildSteps,
  callAt,
  callCount,
  EMPTY_INDEX,
  indexFiles,
  matchFiles,
  trailView,
  WRITES,
  type Attention,
  type TrailStep,
} from "./trail";
import { hueFor, workerList, type WorkerState } from "./workers";

/** How often a settled layout is written back to localStorage. */
const LAYOUT_SAVE_MS = 5000;

interface Input {
  /** Persistence key for the layout and the camera. */
  sessionId: string | null;
  chat: ChatState;
  tree: readonly TreeFile[];
  graph: SessionGraph;
  diff: SessionDiff | null;
  currentTurnId: string | null;
  /** Bumped when a turn starts, which pulls the view back to live. */
  turnEpoch: number;
}

export interface GraphView {
  groups: ThreadGroup[];
  turnIds: string[];
  stepsByTurn: Record<string, TrailStep[]>;
  selectedTurnId: string | null;
  steps: readonly TrailStep[];
  pickTurn: (turnId: string) => void;

  /** Replay: `live` follows the newest step, otherwise `cutoff` holds. */
  live: boolean;
  cutoff: number;
  hereStep: number | null;
  setScrub: (n: number | null) => void;
  /** Model calls in the selected turn. See the rule in `trail.ts`. */
  calls: number;
  /** The model call the scrub position is standing on. */
  hereCall: number;

  field: ParticleField;
  neighbours: ReadonlyMap<string, ReadonlySet<string>>;
  relations: ReadonlyMap<string, Relations>;
  /** Co-touch pairs, keyed by `pairKey`. */
  cotouch: ReadonlySet<string>;
  particleId: (path: string) => string;

  editedFiles: ReadonlyMap<string, DiffFile>;
  editedPaths: ReadonlySet<string>;
  /**
   * The diff as of the scrub position — the server's own answer while live,
   * an approximation behind it. `diffNote` says which, and is null at live.
   */
  diffAtCutoff: SessionDiff | null;
  editedAtCutoff: ReadonlyMap<string, DiffFile>;
  diffNote: string | null;
  editedById: ReadonlyMap<string, DiffFile>;
  attentionById: ReadonlyMap<string, Attention>;
  trailIds: readonly string[];
  positionId: string | null;
  /** One per worker agent, in spawn order, each in its own colour. */
  workerTrails: readonly WorkerTrail[];
}

/**
 * A worker's walk across the same field. Built exactly like the primary
 * trail — commands resolved against the tree — but from the worker's own
 * mirrored activity, so the two never mix.
 */
export interface WorkerTrail {
  id: string;
  /** 1-based spawn position: the number on the legend chip. */
  no: number;
  task: string;
  status: WorkerState["status"];
  /** `r, g, b` for the canvas, and the same colour as CSS for the chip. */
  rgb: string;
  css: string;
  trailIds: readonly string[];
  attention: ReadonlyMap<string, Attention>;
  positionId: string | null;
  /** Every particle this worker touched — what "isolate" narrows to. */
  ids: ReadonlySet<string>;
  steps: number;
}

/**
 * Everything drawn from the session's raw data: the turn the reader is
 * looking at, the steps in it, and the particle field those steps move
 * through. All of it is memoised on content, not on object identity — a
 * new event must not rebuild the layout unless the graph really changed.
 */
export function useGraphView(input: Input): GraphView {
  const { sessionId, chat, tree, graph, diff, currentTurnId, turnEpoch } = input;

  const [pickedTurnId, setPickedTurnId] = useState<string | null>(null);
  const [scrub, setScrub] = useState<number | null>(null);

  useEffect(() => {
    setPickedTurnId(null);
    setScrub(null);
  }, [turnEpoch]);

  const groups = useMemo(() => buildGroups(chat), [chat]);
  const turnIds = useMemo(
    () =>
      groups
        .filter((group) => group.kind === "turn")
        .map((group) => (group as { turnId: string }).turnId),
    [groups],
  );

  const fileIndex = useMemo(
    () => (tree.length > 0 ? indexFiles(tree) : EMPTY_INDEX),
    [tree],
  );

  const stepsByTurn = useMemo(() => {
    const out: Record<string, TrailStep[]> = {};
    for (const turnId of turnIds) {
      out[turnId] = buildSteps(chat.turns[turnId], fileIndex);
    }
    return out;
  }, [turnIds, chat.turns, fileIndex]);

  const lastTurnId = turnIds.length > 0 ? turnIds[turnIds.length - 1] : null;
  const selectedTurnId = pickedTurnId ?? currentTurnId ?? lastTurnId ?? null;

  const steps = useMemo(
    () => (selectedTurnId ? (stepsByTurn[selectedTurnId] ?? []) : []),
    [selectedTurnId, stepsByTurn],
  );

  const editedFiles = useMemo(() => {
    const out = new Map<string, DiffFile>();
    for (const file of diff?.files ?? []) out.set(file.path, file);
    return out;
  }, [diff]);
  const editedPaths = useMemo(() => new Set(editedFiles.keys()), [editedFiles]);

  const live = scrub === null;
  const cutoff = live
    ? steps.length
    : Math.min(Math.max(scrub, 1), Math.max(steps.length, 1));

  const view = useMemo(() => trailView(steps, cutoff), [steps, cutoff]);
  const hereStep =
    view.trail.length > 0 ? view.trail[view.trail.length - 1].n : null;

  /* ---- co-touch: relations the agent made rather than declared ---- */
  const cotouchKey = useMemo(() => {
    const declared = new Set(
      graph.edges.map((edge) => pairKey(edge.source, edge.target)),
    );
    const found = new Set<string>();
    for (const turnId of turnIds) {
      let previous: string | null = null;
      for (const step of stepsByTurn[turnId] ?? []) {
        for (let i = 0; i < step.files.length; i += 1) {
          for (let j = i + 1; j < step.files.length; j += 1) {
            const key = pairKey(step.files[i], step.files[j]);
            if (!declared.has(key)) found.add(key);
          }
        }
        const head = step.files[0];
        if (!head) continue;
        if (previous && previous !== head) {
          const key = pairKey(previous, head);
          if (!declared.has(key)) found.add(key);
        }
        previous = head;
      }
    }
    // A string, so the set below only changes identity when a pair does.
    return [...found].sort().join("|");
  }, [graph.edges, turnIds, stepsByTurn]);

  const cotouch = useMemo(
    () => new Set(cotouchKey ? cotouchKey.split("|") : []),
    [cotouchKey],
  );

  /* ---- the field ---- */
  const fieldRef = useRef<ParticleField | undefined>(undefined);
  const field = useMemo(() => {
    const next = buildField(graph, cotouch, fieldRef.current);
    // Whatever the rebuild could not carry over is looked up in the layout
    // this session last left behind, so a reload opens on the same picture.
    if (next !== fieldRef.current) applySavedLayout(sessionId, next);
    fieldRef.current = next;
    return next;
  }, [graph, cotouch, sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    const save = () => {
      const current = fieldRef.current;
      if (current) saveLayout(sessionId, current);
    };
    const timer = setInterval(save, LAYOUT_SAVE_MS);
    window.addEventListener("pagehide", save);
    return () => {
      clearInterval(timer);
      window.removeEventListener("pagehide", save);
      save();
    };
  }, [sessionId]);

  const neighbours = useMemo(() => neighboursOf(field), [field]);
  const relations = useMemo(() => buildRelations(graph), [graph]);

  const particleId = useCallback(
    (path: string) => field.resolve.get(path) ?? path,
    [field],
  );

  const attentionById = useMemo(() => {
    const out = new Map<string, Attention>();
    for (const [path, seen] of view.attention) {
      const id = particleId(path);
      const before = out.get(id);
      if (before) {
        before.reads += seen.reads;
        before.last = Math.max(before.last, seen.last);
      } else {
        out.set(id, { reads: seen.reads, last: seen.last });
      }
    }
    return out;
  }, [view.attention, particleId]);

  const editedById = useMemo(() => {
    const out = new Map<string, DiffFile>();
    for (const [path, file] of editedFiles) out.set(particleId(path), file);
    return out;
  }, [editedFiles, particleId]);

  const trailIds = useMemo(() => {
    const out: string[] = [];
    for (const waypoint of view.trail) {
      const id = particleId(waypoint.path);
      if (out.length === 0 || out[out.length - 1] !== id) out.push(id);
    }
    return out;
  }, [view.trail, particleId]);

  /* ---- the diff, replayed ------------------------------------------- *
   * The server has no per-step diff, so behind the live position we show
   * the files a write-shaped command had touched by then, intersected with
   * the patch we do have. It is an approximation and it says so: a file
   * written and then reverted never appears, and a file written twice
   * shows its final patch, not the one that existed at step N.
   * ------------------------------------------------------------------- */
  const writtenUpTo = useMemo(() => {
    const out = new Set<string>();
    if (live) return out;
    const collect = (list: readonly TrailStep[], upTo: number) => {
      for (let i = 0; i < Math.min(upTo, list.length); i += 1) {
        const step = list[i];
        if (!step.command || !WRITES.test(step.command)) continue;
        for (const path of step.files) out.add(path);
      }
    };
    // Earlier turns wrote into the same workspace, so they count in full.
    for (const turnId of turnIds) {
      if (turnId === selectedTurnId) break;
      collect(stepsByTurn[turnId] ?? [], Number.MAX_SAFE_INTEGER);
    }
    collect(steps, cutoff);
    return out;
  }, [live, cutoff, steps, turnIds, selectedTurnId, stepsByTurn]);

  const diffAtCutoff = useMemo(() => {
    if (live || !diff) return diff;
    const files = diff.files.filter((file) => writtenUpTo.has(file.path));
    if (files.length === diff.files.length) return diff;
    const keep = new Set(files.map((file) => file.path));
    const patch = splitPatch(diff.patch)
      .filter((section) => keep.has(section.path))
      .map((section) => section.lines.join("\n"))
      .join("\n");
    return { ...diff, files, patch };
  }, [live, diff, writtenUpTo]);

  const editedAtCutoff = useMemo(() => {
    if (diffAtCutoff === diff) return editedFiles;
    const out = new Map<string, DiffFile>();
    for (const file of diffAtCutoff?.files ?? []) out.set(file.path, file);
    return out;
  }, [diffAtCutoff, diff, editedFiles]);

  const hereCall = callAt(steps, cutoff);
  const diffNote = live
    ? null
    : `diff at step ${hereCall} is approximate — showing files ` +
      "written up to this step";

  /* ---- the workers' trails --------------------------------------- *
   * A worker's frames arrive mirrored on this session's stream and were
   * kept out of `chat.turns` on the way in, so there is nothing to filter
   * here: its activity is its own, and it walks the parent's field because
   * it is a clone of the same repository at the same ref.
   * ----------------------------------------------------------------- */
  const workers = chat.workers;
  const workerTrails = useMemo<WorkerTrail[]>(() => {
    return workerList(workers).map((worker, i) => {
      const hue = hueFor(i);
      const attention = new Map<string, Attention>();
      const trailIds: string[] = [];
      const ids = new Set<string>();

      worker.activity.forEach((item, index) => {
        const step = index + 1;
        const files = matchFiles(item.command, fileIndex);
        for (const path of files) {
          const id = particleId(path);
          ids.add(id);
          const seen = attention.get(id);
          if (seen) {
            seen.reads += 1;
            seen.last = step;
          } else {
            attention.set(id, { reads: 1, last: step });
          }
        }
        const head = files[0];
        if (!head) return;
        const id = particleId(head);
        if (trailIds.length === 0 || trailIds[trailIds.length - 1] !== id) {
          trailIds.push(id);
        }
      });

      return {
        id: worker.id,
        no: i + 1,
        task: worker.task,
        status: worker.status,
        rgb: hue.rgb,
        css: hue.css,
        trailIds,
        attention,
        positionId: trailIds.length > 0 ? trailIds[trailIds.length - 1] : null,
        ids,
        steps: worker.activity.length,
      };
    });
  }, [workers, fileIndex, particleId]);

  const pickTurn = useCallback((turnId: string) => {
    setPickedTurnId(turnId);
    setScrub(null);
  }, []);

  return {
    groups,
    turnIds,
    stepsByTurn,
    selectedTurnId,
    steps,
    pickTurn,
    live,
    cutoff,
    hereStep,
    setScrub,
    calls: callCount(steps, chat.turns[selectedTurnId ?? ""]?.nCalls ?? null),
    hereCall,
    field,
    neighbours,
    relations,
    cotouch,
    particleId,
    editedFiles,
    editedPaths,
    diffAtCutoff,
    editedAtCutoff,
    diffNote,
    editedById,
    attentionById,
    trailIds,
    positionId: view.position ? particleId(view.position) : null,
    workerTrails,
  };
}
