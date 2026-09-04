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
import {
  buildSteps,
  EMPTY_INDEX,
  indexFiles,
  trailView,
  type Attention,
  type TrailStep,
} from "./trail";

interface Input {
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

  field: ParticleField;
  neighbours: ReadonlyMap<string, ReadonlySet<string>>;
  relations: ReadonlyMap<string, Relations>;
  /** Co-touch pairs, keyed by `pairKey`. */
  cotouch: ReadonlySet<string>;
  particleId: (path: string) => string;

  editedFiles: ReadonlyMap<string, DiffFile>;
  editedPaths: ReadonlySet<string>;
  editedById: ReadonlyMap<string, DiffFile>;
  attentionById: ReadonlyMap<string, Attention>;
  trailIds: readonly string[];
  positionId: string | null;
}

/**
 * Everything drawn from the session's raw data: the turn the reader is
 * looking at, the steps in it, and the particle field those steps move
 * through. All of it is memoised on content, not on object identity — a
 * new event must not rebuild the layout unless the graph really changed.
 */
export function useGraphView(input: Input): GraphView {
  const { chat, tree, graph, diff, currentTurnId, turnEpoch } = input;

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
    fieldRef.current = next;
    return next;
  }, [graph, cotouch]);

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
    field,
    neighbours,
    relations,
    cotouch,
    particleId,
    editedFiles,
    editedPaths,
    editedById,
    attentionById,
    trailIds,
    positionId: view.position ? particleId(view.position) : null,
  };
}
