/* ------------------------------------------------------------------ *
 * The trail: what the agent did, step by step, and which files each
 * step touched.
 *
 * The event stream never says which file a command touched, so we infer
 * it: tokens of the command are matched against the real tree. Only a
 * token that resolves to exactly one tracked path counts, which keeps
 * `python hello.py` honest and `grep -r foo .` quiet.
 * ------------------------------------------------------------------ */

import type { TreeFile } from "./api";
import type { ActivityItem, TurnState } from "./chatState";
import { stripFences } from "./fences";

export interface StepSteering {
  key: string;
  content: string;
}

export interface TrailStep {
  key: string;
  /** Envelope id of the frame that opened the step; used for replay. */
  eventId: number;
  /** 1-based position within the turn. */
  n: number;
  /**
   * True when an `assistant` frame opened this step, i.e. it stands for one
   * model call. See `callCount` for why that is the unit we count in.
   */
  isCall: boolean;
  /** A call that produced the reply: it earns a tick, and nothing else. */
  isReply: boolean;
  thought: string;
  actions: string[];
  command: string | null;
  output: string;
  returncode: number | null;
  isError: boolean;
  /** Tree paths this step's command resolved to, in first-seen order. */
  files: string[];
  steering: StepSteering[];
  errors: string[];
}

export type StepKind = "read" | "edit" | "error";

/* ------------------------------------------------------------------ *
 * The file index
 * ------------------------------------------------------------------ */

export interface FileIndex {
  paths: ReadonlySet<string>;
  byBasename: ReadonlyMap<string, string[]>;
  /** Every path ending in `/name`, for suffix matches like `app/cli.py`. */
  bySuffix: ReadonlyMap<string, string[]>;
}

export const EMPTY_INDEX: FileIndex = {
  paths: new Set(),
  byBasename: new Map(),
  bySuffix: new Map(),
};

export function indexFiles(files: readonly TreeFile[]): FileIndex {
  const paths = new Set<string>();
  const byBasename = new Map<string, string[]>();
  const bySuffix = new Map<string, string[]>();

  const push = (map: Map<string, string[]>, key: string, value: string) => {
    const bucket = map.get(key);
    if (bucket) bucket.push(value);
    else map.set(key, [value]);
  };

  for (const file of files) {
    if (!file.path) continue;
    paths.add(file.path);
    const parts = file.path.split("/");
    push(byBasename, parts[parts.length - 1], file.path);
    // Every trailing sub-path, so `routes/auth.py` resolves too.
    for (let i = parts.length - 2; i >= 1; i -= 1) {
      push(bySuffix, parts.slice(i).join("/"), file.path);
    }
  }

  return { paths, byBasename, bySuffix };
}

/** Shell punctuation that never belongs to a path. */
const SPLIT = /[\s"'`=(){}[\]<>|;&,]+/;
const STRIP_EDGES = /^[-.\/]+|[.:,;]+$/g;
const MAX_FILES_PER_STEP = 6;

/**
 * Paths a command plausibly touched. Ambiguous tokens (a basename shared
 * by several files) are dropped rather than guessed at.
 */
export function matchFiles(
  command: string,
  index: FileIndex,
): string[] {
  if (!command || index.paths.size === 0) return [];
  const hits: string[] = [];
  const seen = new Set<string>();

  for (const raw of command.split(SPLIT)) {
    if (hits.length >= MAX_FILES_PER_STEP) break;
    if (raw.length < 2) continue;

    const token = raw.replace(/^\.\//, "").replace(STRIP_EDGES, "");
    if (token.length < 2) continue;

    let hit: string | null = null;
    if (index.paths.has(token)) {
      hit = token;
    } else {
      const bucket =
        (token.includes("/") ? index.bySuffix.get(token) : undefined) ??
        index.byBasename.get(token);
      if (bucket && bucket.length === 1) hit = bucket[0];
    }

    if (hit && !seen.has(hit)) {
      seen.add(hit);
      hits.push(hit);
    }
  }

  return hits;
}

/* ------------------------------------------------------------------ *
 * Steps
 * ------------------------------------------------------------------ */

/** `ev-1004` -> 1004. Synthetic ids are negative and still parse. */
function eventIdOf(key: string): number {
  const parsed = Number.parseInt(key.replace(/^ev-/, ""), 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * The thought as prose: fenced blocks belong in the command stub, not in
 * the agent's voice, and models like to prefix the label itself.
 */
function cleanThought(content: string): string {
  return stripFences(content)
    .replace(/^\s*(THOUGHT|PLAN|REASONING)\s*:\s*/i, "")
    .trim();
}

/**
 * Fold a turn's raw activity items into steps. A step opens on an
 * `assistant` frame, or on a `tool_call` when the previous step already
 * ran one — which is exactly how the agent loop is structured.
 */
export function buildSteps(
  turn: TurnState | undefined,
  index: FileIndex,
): TrailStep[] {
  if (!turn) return [];
  const steps: TrailStep[] = [];
  let current: TrailStep | null = null;

  const make = (item: ActivityItem): TrailStep => {
    const step: TrailStep = {
      key: item.key,
      eventId: eventIdOf(item.key),
      n: steps.length + 1,
      isCall: item.kind === "assistant",
      isReply: item.kind === "assistant" && item.isReply,
      thought: "",
      actions: [],
      command: null,
      output: "",
      returncode: null,
      isError: false,
      files: [],
      steering: [],
      errors: [],
    };
    steps.push(step);
    return step;
  };

  for (const item of turn.items) {
    switch (item.kind) {
      case "assistant": {
        current = make(item);
        current.thought = cleanThought(item.content);
        current.actions = item.actions;
        break;
      }

      case "tool_call": {
        if (current === null || current.command !== null) current = make(item);
        current.command = item.command;
        current.files = matchFiles(item.command, index);
        break;
      }

      case "tool_result": {
        if (current === null) current = make(item);
        current.output = item.output;
        current.returncode = item.returncode;
        current.isError = current.isError || item.isError;
        if (current.command === null && item.command) {
          current.command = item.command;
          current.files = matchFiles(item.command, index);
        }
        break;
      }

      case "steering": {
        if (current === null) current = make(item);
        current.steering.push({ key: item.key, content: item.content });
        break;
      }

      case "error": {
        if (current === null) current = make(item);
        current.errors.push(item.error);
        current.isError = true;
        break;
      }

      default:
        break;
    }
  }

  return steps;
}

/* ------------------------------------------------------------------ *
 * Counting steps
 *
 * THE RULE — one word, one meaning: **a step is a model call.** Every
 * number labelled "steps" in this UI counts model calls and nothing else.
 *
 *   · Once a turn has ended, the count is the server's `n_calls`
 *     (`turn_finished.n_calls`, echoed by the reply meta and the receipt).
 *     That is the authority.
 *   · While the turn is still running there is no `n_calls` yet, so the
 *     count is the number of `assistant` frames seen — one per model call.
 *   · The tick row draws one tick per `assistant` frame, so what you count
 *     on screen is what the label says.
 *
 * The final model call of a turn produces the reply and issues no command,
 * so it emits no `assistant` frame: expect the live count to settle one
 * higher when `n_calls` lands. Steps opened by a stray `tool_call` with no
 * `assistant` frame of its own are continuations of the call before them —
 * they appear in the trail, and they are not counted twice.
 * ------------------------------------------------------------------ */

/** Steps that stand for a model call — the ones the tick rows draw. */
export function callSteps(steps: readonly TrailStep[]): TrailStep[] {
  return steps.filter((step) => step.isCall);
}

/** Which model call a trail-step cutoff is standing on. 1-based. */
export function callAt(steps: readonly TrailStep[], cutoff: number): number {
  let n = 0;
  for (const step of steps) {
    if (step.n > cutoff) break;
    if (step.isCall) n += 1;
  }
  return Math.max(1, n);
}

/** Model calls seen so far. `nCalls` from the server wins once it exists. */
export function callCount(
  steps: readonly TrailStep[],
  nCalls: number | null,
): number {
  return nCalls ?? callSteps(steps).length;
}

/**
 * Commands that write. Without this, every step that so much as `cat`s a
 * file which later changed would read as an edit, and the strip would be
 * one solid colour. Exported because the inspector re-reads the diff after
 * a step that plausibly changed something.
 */
export const WRITES =
  /(^|[\s;&|(])(tee|patch|mv|cp|rm|mkdir|touch|truncate|install)\s|>>?[^&]|sed\s+-[a-z]*i|perl\s+-[a-z]*i|git\s+(apply|checkout|restore|revert|mv|rm)|apply_patch|python3?\s+-\s*<<|python3?\s+-c\b/;

/** How a step's tick reads on the transmission strip and the scrubber. */
export function stepKind(
  step: TrailStep,
  edited: ReadonlySet<string>,
): StepKind {
  if (step.isError) return "error";
  const command = step.command ?? "";
  if (
    command &&
    WRITES.test(command) &&
    step.files.some((path) => edited.has(path))
  ) {
    return "edit";
  }
  return "read";
}

/* ------------------------------------------------------------------ *
 * Attention, trail, position
 * ------------------------------------------------------------------ */

/** Steps over which a visited file's orange tint fades back to paper. */
export const DECAY_STEPS = 6;

export interface Attention {
  /** Times a step resolved to this file. */
  reads: number;
  /** 1-based step number of the most recent visit. */
  last: number;
}

export interface Waypoint {
  n: number;
  path: string;
}

export interface TrailView {
  attention: Map<string, Attention>;
  trail: Waypoint[];
  /** The file the agent is standing on. */
  position: string | null;
  /** Steps actually included, i.e. the scrub cutoff. */
  upTo: number;
}

/**
 * Everything the graph needs for a turn, replayed up to `upTo` steps
 * (inclusive). Pass `steps.length` for live.
 */
export function trailView(
  steps: readonly TrailStep[],
  upTo: number,
): TrailView {
  const attention = new Map<string, Attention>();
  const trail: Waypoint[] = [];
  const limit = Math.max(0, Math.min(upTo, steps.length));

  for (let i = 0; i < limit; i += 1) {
    const step = steps[i];
    for (const path of step.files) {
      const seen = attention.get(path);
      if (seen) {
        seen.reads += 1;
        seen.last = step.n;
      } else {
        attention.set(path, { reads: 1, last: step.n });
      }
    }
    // One waypoint per step that landed somewhere, skipping repeats so a
    // file read twice in a row does not fire a signal at itself.
    const head = step.files[0];
    if (head && (trail.length === 0 || trail[trail.length - 1].path !== head)) {
      trail.push({ n: step.n, path: head });
    }
  }

  return {
    attention,
    trail,
    position: trail.length > 0 ? trail[trail.length - 1].path : null,
    upTo: limit,
  };
}

/** 1 at the moment of the visit, 0 once `DECAY_STEPS` have passed. */
export function attentionAlpha(last: number, current: number): number {
  const age = current - last;
  if (age < 0) return 0;
  return Math.max(0, (DECAY_STEPS - age) / DECAY_STEPS);
}
