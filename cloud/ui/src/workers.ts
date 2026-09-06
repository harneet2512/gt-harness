/* ------------------------------------------------------------------ *
 * Worker agents, as the parent's page sees them.
 *
 * A worker is a session of its own, but from here it is a **card**: the
 * task it was given, how far it has got, what it did, what it reported,
 * and whether its patch has landed. Everything on the card is folded out
 * of the parent's single event stream, where a worker's frames arrive
 * mirrored with an `agent_id`, plus the two records that survive a reload
 * (`GET /agents` and the report messages).
 *
 * `agent_id` is the whole protocol. A frame that has it belongs to that
 * worker and to nothing else — never to the primary turn, never to the
 * primary step count. That separation is enforced here and in `chatState`.
 * ------------------------------------------------------------------ */

import type { Message, Session, SessionEvent } from "./api";
import { foldFiles, sanitizePaths, sanitizeText } from "./external";
import { formatDuration, formatTokens, toEpochSeconds } from "./format";
import { actionLine, fromFrame, type GtAction } from "./gt";

/** Where a worker is, in the four words the card shows. */
export type WorkerStatus = "running" | "reported" | "applied" | "closed";

/** One `$ command` → output row of a worker's own trail. */
export interface WorkerActivity {
  key: string;
  command: string;
  output: string;
  returncode: number | null;
  isError: boolean;
  /**
   * Set when the row is a typed GroundTruth query rather than a shell
   * command. `command` then holds the query as it reads on the line, so a
   * collapsed card needs nothing else to draw it.
   */
  gt: GtAction | null;
  /**
   * The tool an **external** agent called, by name — `Read`, `Edit`,
   * `Bash`. Null for a worker of ours, which only ever runs one tool and
   * whose line is therefore a `$` and nothing else.
   */
  tool: string | null;
  /**
   * Repo-relative paths this row touched, as the frame reported them. Only
   * an external agent sends these: a worker's files are inferred from its
   * command against the tree, in `trail.ts`, and inferring is not needed
   * when the agent simply says.
   */
  files: readonly string[];
  /**
   * True once a result has landed on this row. It is what a later
   * `tool_result` merges into the row above it by, rather than "this row
   * has no output yet": an external agent's result often carries neither
   * output nor a return code, and two of those in a row used to collapse
   * into one step.
   */
  answered: boolean;
}

export interface WorkerState {
  id: string;
  task: string;
  status: WorkerStatus;
  /** Mirrored `assistant` frames seen so far — one per model call. */
  calls: number;
  /** The server's own count, once a turn of the worker's has ended. */
  nCalls: number | null;
  activity: WorkerActivity[];
  /** The worker's reply, whole. Empty until it reports. */
  reply: string;
  finishReason: string | null;
  filesChanged: readonly string[];
  patchSha: string | null;
  /** Files this worker's patch put into the parent workspace. */
  appliedFiles: readonly string[] | null;
  /** Paths a 3-way merge refused, from a 409. */
  conflicts: readonly string[] | null;
  /** Any other reason an apply did not happen, in the server's words. */
  applyError: string | null;
  /** True between pressing Apply and the server answering. */
  applying: boolean;
  closedReason: string | null;
  /**
   * When the server created this worker, in epoch seconds. It is what
   * `worker-N` counts by: a reload rebuilds the cards out of two records
   * that arrive in whatever order they finish in, and numbering by arrival
   * swapped worker-1 and worker-2 — and the graph hues with them — every
   * time the second worker reported first (HAR-84 P1-4). Null until the
   * `/agents` rows land, when spawn order is all there is.
   */
  createdAt: number | null;

  /* ---- external agents ------------------------------------------- *
   * An agent we watch but do not run. Not a second structure: a row in
   * this same list, in this same order, under the same hue rule — which
   * is the point, because the question the page answers is "who is
   * working where", not "whose process is it".
   * ----------------------------------------------------------------- */

  /** True for an agent this server mirrors rather than executes. */
  isExternal: boolean;
  /** `"claude-code" | "codex" | "other"`. Null for a worker of ours. */
  agentKind: string | null;
  /** What to call it, where the task is not the whole story. */
  label: string;
  /** The external agent this one is a **subagent** of, if any. */
  parentAgentId: string | null;
  /** The directory it runs in, on its own machine. Shown, never trusted. */
  externalCwd: string | null;
  /**
   * Where it works: every file its frames have named, most recent first,
   * de-duplicated and capped. Distinct from `filesChanged`, which is what
   * a **patch** touched — and an external agent has no patch.
   */
  files: readonly string[];
  /** The tool of the most recent step, for the card's `⎿` line. */
  lastTool: string | null;

  /* ---- the fleet line -------------------------------------------- *
   * What this agent is doing right now, and what it has spent. Both are
   * live: they arrive on the mirrored frames as well as on the `/agents`
   * rows, so the card does not wait for a poll to tell the truth.
   * ----------------------------------------------------------------- */

  /**
   * One line: what it is doing right now. Named `doing` because
   * `activity` is already this card's trail, and one word may only mean
   * one thing here.
   */
  doing: string;
  /**
   * Cumulative tokens. **Null is not zero** — it means nobody reported a
   * count, and the row prints nothing rather than a confident `0`.
   */
  tokens: number | null;
  /** When the server last heard from it, in epoch seconds. */
  updatedAt: number | null;
}

export interface WorkersState {
  byId: Record<string, WorkerState>;
  /** Spawn order — the order the cards are drawn in. */
  order: readonly string[];
}

export const emptyWorkers: WorkersState = { byId: {}, order: [] };

export function emptyWorker(id: string, task = ""): WorkerState {
  return {
    id,
    task,
    status: "running",
    calls: 0,
    nCalls: null,
    activity: [],
    reply: "",
    finishReason: null,
    filesChanged: [],
    patchSha: null,
    appliedFiles: null,
    conflicts: null,
    applyError: null,
    applying: false,
    closedReason: null,
    createdAt: null,
    isExternal: false,
    agentKind: null,
    label: "",
    parentAgentId: null,
    externalCwd: null,
    files: [],
    lastTool: null,
    doing: "",
    tokens: null,
    updatedAt: null,
  };
}

/**
 * The ids in the order the cards are drawn and numbered: by `created_at`
 * where the server has told us one, by first touch where it has not. Both
 * keys are used together rather than either alone — the first is the truth
 * and survives a reload, the second is all a live spawn has until the rows
 * arrive, and a worker spawned now is younger than one already hydrated.
 */
export function orderedIds(state: WorkersState): string[] {
  return state.order
    .map((id, index) => ({ id, index, at: state.byId[id]?.createdAt ?? null }))
    .sort((a, b) => {
      const left = a.at ?? Number.POSITIVE_INFINITY;
      const right = b.at ?? Number.POSITIVE_INFINITY;
      if (left !== right) return left - right;
      return a.index - b.index;
    })
    .map((row) => row.id);
}

/** The cards, oldest first. */
export function workerList(state: WorkersState): WorkerState[] {
  return orderedIds(state)
    .map((id) => state.byId[id])
    .filter(Boolean);
}

/** Model calls to show: the server's count once it exists, ours until then. */
export function workerCalls(worker: WorkerState): number {
  return worker.nCalls ?? worker.calls;
}

/** Enough of an id to tell two workers apart without reading a UUID. */
export function shortId(id: string): string {
  return id.slice(0, 8);
}

function put(
  state: WorkersState,
  id: string,
  update: (worker: WorkerState) => WorkerState,
): WorkersState {
  const existing = state.byId[id];
  const next = update(existing ?? emptyWorker(id));
  if (existing === next) return state;
  return {
    byId: { ...state.byId, [id]: next },
    order: existing ? state.order : [...state.order, id],
  };
}

/** A closed worker stays closed: nothing arriving later re-opens the card. */
function moveTo(worker: WorkerState, status: WorkerStatus): WorkerStatus {
  return worker.status === "closed" ? "closed" : status;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** The larger of two counts; null only when neither exists. */
function pickTokens(
  current: number | null,
  incoming: number | null | undefined,
): number | null {
  const next = num(incoming);
  if (next === null) return current;
  if (current === null) return next;
  return Math.max(current, next);
}

function paths(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

/* ------------------------------------------------------------------ *
 * Live frames
 * ------------------------------------------------------------------ */

/**
 * A frame the server mirrored from a worker. `agentId` has already been
 * read off it, and the frame never reaches the primary turn state.
 */
export function applyMirrored(
  state: WorkersState,
  agentId: string,
  event: SessionEvent,
): WorkersState {
  const key = `w-${agentId}-${event.id}`;

  /* `activity` and `tokens` ride on whichever mirrored frame has them —
     a `tool_call`, a status frame, one this build has never heard of.
     Reading them off `data` before the switch means a frame type we do
     not model still keeps the fleet line current, and a frame that
     carries neither costs nothing. */
  const doing = sanitizeText((event.data as Record<string, unknown>).activity, 200);
  const tokens = num((event.data as Record<string, unknown>).tokens);
  if (doing || tokens !== null) {
    state = put(state, agentId, (worker) => ({
      ...worker,
      doing: doing || worker.doing,
      /* A count that went backwards is a client restarting its own
         tally, not work being undone. The larger number stands. */
      tokens:
        tokens === null
          ? worker.tokens
          : Math.max(tokens, worker.tokens ?? tokens),
    }));
  }

  switch (event.type) {
    case "turn_started":
      return put(state, agentId, (worker) => ({
        ...worker,
        status: moveTo(worker, "running"),
        nCalls: null,
      }));

    case "assistant":
      return put(state, agentId, (worker) => ({
        ...worker,
        calls: worker.calls + 1,
      }));

    case "tool_call": {
      const tool = sanitizeText(event.data.tool_name, 40);
      const raw = str(event.data.command);
      /* A command from someone else's machine is cleaned; one of our own
         sandbox's is left exactly as the shell saw it. `tool_name` is what
         tells the two apart, and only an external agent sends it. */
      const command = tool ? sanitizeText(raw, 400) : raw;
      const files = sanitizePaths(event.data.files);
      /* A worker of ours always has a command. An external agent may send
         a tool and a file list and no command at all — a frame with none
         of the three is nothing to draw. */
      if (!command && !tool && files.length === 0) return state;
      return put(state, agentId, (worker) => ({
        ...worker,
        activity: [
          ...worker.activity,
          {
            key,
            command,
            output: "",
            returncode: null,
            isError: false,
            gt: null,
            tool: tool || null,
            files,
            answered: false,
          },
        ],
        files: foldFiles(worker.files, files),
        lastTool: tool || worker.lastTool,
      }));
    }

    /* A worker's GroundTruth query. Same protocol, same card, and never a
       `$` line: it is not a shell command and must not read as one. */
    case "gt_action": {
      const action = fromFrame(event.data as Record<string, unknown>);
      if (!action) return state;
      return put(state, agentId, (worker) => ({
        ...worker,
        activity: [
          ...worker.activity,
          {
            key,
            command: actionLine(action),
            output: "",
            returncode: null,
            isError: false,
            gt: action,
            tool: null,
            files: [],
            answered: false,
          },
        ],
      }));
    }

    case "tool_result": {
      const tool = sanitizeText(event.data.tool_name, 40);
      const raw = str(event.data.command);
      const command = tool ? sanitizeText(raw, 400) : raw;
      const output = str(event.data.output);
      const returncode = num(event.data.returncode);
      /* An external tool has no exit code, so `ok: false` is what a
         non-zero return is for one of ours. Absent is not a failure. */
      const isError =
        event.data.ok === false ||
        event.data.is_error === true ||
        (returncode ?? 0) > 0;
      const files = sanitizePaths(event.data.files);
      return put(state, agentId, (worker) => {
        const seen = foldFiles(worker.files, files);
        const lastTool = tool || worker.lastTool;
        const last = worker.activity[worker.activity.length - 1];
        if (last && last.gt === null && !last.answered) {
          const activity = worker.activity.slice(0, -1);
          activity.push({
            ...last,
            output,
            returncode,
            isError,
            tool: last.tool ?? (tool || null),
            files: files.length > 0 ? files : last.files,
            answered: true,
          });
          return { ...worker, activity, files: seen, lastTool };
        }
        return {
          ...worker,
          activity: [
            ...worker.activity,
            {
              key,
              command,
              output,
              returncode,
              isError,
              gt: null,
              tool: tool || null,
              files,
              answered: true,
            },
          ],
          files: seen,
          lastTool,
        };
      });
    }

    case "turn_finished":
      return put(state, agentId, (worker) => ({
        ...worker,
        nCalls: num(event.data.n_calls) ?? worker.nCalls,
      }));

    default:
      return state;
  }
}

/** The parent's own frames *about* its workers. Keyed by `worker_id`. */
export function applyWorkerEvent(
  state: WorkersState,
  event: SessionEvent,
): WorkersState {
  const id = str((event.data as { worker_id?: unknown }).worker_id);
  if (!id) return state;

  switch (event.type) {
    /* One frame for both kinds. `external: true` is the only thing that
       tells an agent we run from one we merely watch; everything else on
       the frame is optional, and an older server sends none of it. */
    case "agent_spawned": {
      const external = event.data.external === true;
      const task = external
        ? sanitizeText(event.data.task, 300)
        : str(event.data.task);
      const label = sanitizeText(event.data.label, 120);
      const kind = sanitizeText(event.data.agent_kind, 40);
      const parent = sanitizeText(event.data.parent_agent_id, 120);
      return put(state, id, (worker) => ({
        ...worker,
        task: task || worker.task,
        isExternal: worker.isExternal || external,
        agentKind: kind || worker.agentKind,
        label: label || worker.label,
        parentAgentId: parent || worker.parentAgentId,
      }));
    }

    /* An external agent has no patch and no diff: its report is a summary
       in `reply_excerpt`, and `patch_sha256` never arrives. Both fields
       are read the same way for both kinds — a missing one changes
       nothing, which is what makes an old server harmless. */
    case "agent_report":
      return put(state, id, (worker) => ({
        ...worker,
        status: moveTo(worker, "reported"),
        reply:
          str(event.data.content) ||
          sanitizeText(event.data.reply_excerpt, 600) ||
          worker.reply,
        finishReason: str(event.data.finish_reason) || worker.finishReason,
        filesChanged: paths(event.data.files_changed),
        patchSha: str(event.data.patch_sha256) || worker.patchSha,
        nCalls: num(event.data.n_calls) ?? worker.nCalls,
      }));

    case "agent_applied":
      return put(state, id, (worker) => ({
        ...worker,
        status: moveTo(worker, "applied"),
        appliedFiles: paths(event.data.files),
        conflicts: null,
        applyError: null,
        applying: false,
      }));

    case "agent_closed":
      return put(state, id, (worker) => ({
        ...worker,
        status: "closed",
        closedReason: str(event.data.reason) || worker.closedReason,
        applying: false,
      }));

    default:
      return state;
  }
}

/* ------------------------------------------------------------------ *
 * Reload: the cards rebuilt from records rather than from a stream
 * ------------------------------------------------------------------ */

/** Where a worker row from `GET /agents` puts the card. */
export function statusOfRow(row: Session): WorkerStatus {
  if (row.status === "closed" || row.status === "failed") return "closed";
  if (row.applied_at != null || row.report?.applied === true) return "applied";
  if (row.report) return "reported";
  return "running";
}

/**
 * `GET /api/sessions/:id/agents` — task, status, report and `applied_at`
 * all come with the rows, so a reload has every card back before a single
 * frame arrives. The stream then fills in the live detail.
 */
export function hydrateWorkers(
  state: WorkersState,
  rows: readonly Session[],
): WorkersState {
  let next = state;
  for (const row of rows) {
    if (!row?.id) continue;
    const external = row.role === "external";
    next = put(next, row.id, (worker) => ({
      ...worker,
      createdAt: toEpochSeconds(row.created_at) ?? worker.createdAt,
      updatedAt: toEpochSeconds(row.updated_at) ?? worker.updatedAt,
      /* The stream runs ahead of any poll, so a live `doing` wins over the
         row's. `tokens` takes the larger of the two for the same reason. */
      doing: worker.doing || sanitizeText(row.activity, 200),
      tokens: pickTokens(worker.tokens, row.tokens),
      task:
        (external ? sanitizeText(row.task, 300) : row.task || "") ||
        worker.task,
      isExternal: worker.isExternal || external,
      agentKind: sanitizeText(row.agent_kind, 40) || worker.agentKind,
      parentAgentId:
        sanitizeText(row.parent_agent_id, 120) || worker.parentAgentId,
      externalCwd: sanitizeText(row.external_cwd, 200) || worker.externalCwd,
      status: statusOfRow(row),
      reply:
        worker.reply ||
        (external
          ? sanitizeText(row.report?.reply_excerpt, 600)
          : (row.report?.reply_excerpt ?? "")) ||
        "",
      finishReason: row.report?.finish_reason
        ? String(row.report.finish_reason)
        : worker.finishReason,
      filesChanged:
        worker.filesChanged.length > 0
          ? worker.filesChanged
          : (row.report?.files_changed ?? []),
      patchSha: worker.patchSha ?? row.report?.patch_sha256 ?? null,
      appliedFiles:
        row.applied_at != null || row.report?.applied === true
          ? (worker.appliedFiles ?? row.report?.files_changed ?? [])
          : worker.appliedFiles,
      closedReason:
        row.status === "closed"
          ? (worker.closedReason ?? String(row.closed_reason ?? "") ?? null)
          : worker.closedReason,
    }));
  }
  return next;
}

/**
 * The report as it survives in the parent's own `messages`: a `role:
 * "agent"` message carrying `meta.agent_id`. It holds the **whole** reply,
 * where the row's `reply_excerpt` is bounded — so it wins.
 */
export function hydrateReport(
  state: WorkersState,
  message: Message,
): WorkersState {
  const id = str(message.meta.agent_id);
  if (!id) return state;
  return put(state, id, (worker) => ({
    ...worker,
    status: moveTo(worker, worker.status === "running" ? "reported" : worker.status),
    reply: message.content || worker.reply,
    finishReason: message.meta.finish_reason
      ? String(message.meta.finish_reason)
      : worker.finishReason,
    filesChanged:
      Array.isArray(message.meta.files_changed) &&
      message.meta.files_changed.length > 0
        ? message.meta.files_changed
        : worker.filesChanged,
    patchSha: message.meta.patch_sha256 ?? worker.patchSha,
  }));
}

/* ------------------------------------------------------------------ *
 * Applying, from the button rather than from the stream
 * ------------------------------------------------------------------ */

export function markApplying(state: WorkersState, id: string): WorkersState {
  return put(state, id, (worker) => ({
    ...worker,
    applying: true,
    conflicts: null,
    applyError: null,
  }));
}

export function markApplied(
  state: WorkersState,
  id: string,
  files: readonly string[],
): WorkersState {
  return put(state, id, (worker) => ({
    ...worker,
    status: moveTo(worker, "applied"),
    appliedFiles: files,
    conflicts: null,
    applyError: null,
    applying: false,
  }));
}

/**
 * A 409: the paths the merge refused. The parent's workspace is untouched,
 * so the card says which files clashed and leaves the worker where it was.
 */
export function markConflict(
  state: WorkersState,
  id: string,
  conflicts: readonly string[],
  detail: string,
): WorkersState {
  return put(state, id, (worker) => ({
    ...worker,
    conflicts,
    applyError: conflicts.length > 0 ? null : detail,
    applying: false,
  }));
}

export function markApplyError(
  state: WorkersState,
  id: string,
  error: string,
): WorkersState {
  return put(state, id, (worker) => ({
    ...worker,
    applyError: error,
    conflicts: null,
    applying: false,
  }));
}

/* ------------------------------------------------------------------ *
 * Colour
 *
 * Four hues, none of them the primary agent's orange or the edited-file
 * teal, so a worker's trail is never mistaken for the session's own.
 * ------------------------------------------------------------------ */

export interface WorkerHue {
  /** `r, g, b` — the form the canvas painter interpolates alpha into. */
  rgb: string;
  /** The same colour as CSS, for the chips and the card rules. */
  css: string;
}

export const WORKER_HUES: readonly WorkerHue[] = [
  { rgb: "124, 92, 214", css: "#7C5CD6" },
  { rgb: "43, 108, 214", css: "#2B6CD6" },
  { rgb: "194, 75, 160", css: "#C24BA0" },
  { rgb: "138, 155, 46", css: "#8A9B2E" },
];

/** Spawn order picks the hue, wrapping past the fourth worker. */
export function hueFor(index: number): WorkerHue {
  const n = Number.isFinite(index) && index >= 0 ? Math.floor(index) : 0;
  return WORKER_HUES[n % WORKER_HUES.length];
}

/** 1-based, for the "worker N" chips. Stable across a reload. */
export function workerNo(state: WorkersState, id: string): number {
  return orderedIds(state).indexOf(id) + 1;
}

/* ------------------------------------------------------------------ *
 * The agent list: workers and external agents, one list
 *
 * The number and the hue are still the flat `created_at` position, so an
 * agent keeps its colour on the graph whatever the tree does to the order
 * it is drawn in. Nesting only indents.
 * ------------------------------------------------------------------ */

/** One row of the list: the agent, how far it is indented, and its colour. */
export interface AgentRow {
  worker: WorkerState;
  /** 0 for a top-level agent, 1 for a subagent under its parent. */
  depth: 0 | 1;
  /** 1-based position in the flat `created_at` order. */
  no: number;
  hue: WorkerHue;
  /**
   * Children of this agent that are **not** drawn as rows beneath it — the
   * `(+2)` on a fleet line. A top-level agent's children are indented
   * directly under it and so are never counted here; a subagent's own
   * children have nowhere deeper to go, so they are.
   */
  collapsed: number;
}

/**
 * Every agent, in `created_at` order, each subagent drawn under the agent
 * it belongs to.
 *
 * Two levels and no more. A subagent of a subagent is still drawn at level
 * two rather than stepping further right: the indent is there to say
 * "this one belongs to that one", and past two levels it stops saying
 * anything and starts eating the column.
 */
export function agentRows(state: WorkersState): AgentRow[] {
  const ids = orderedIds(state);
  const rank = new Map(ids.map((id, index) => [id, index]));

  /* A parent we have never heard of is no parent at all: the agent stays
     at the top level rather than disappearing (the same rule the resume
     rail keeps for an orphaned worker). */
  const parentOf = (id: string): string | null => {
    const parent = state.byId[id]?.parentAgentId ?? null;
    if (!parent || parent === id || !rank.has(parent)) return null;
    return parent;
  };

  const children = new Map<string, string[]>();
  for (const id of ids) {
    const parent = parentOf(id);
    if (!parent) continue;
    const bucket = children.get(parent);
    if (bucket) bucket.push(id);
    else children.set(parent, [id]);
  }

  const rows: AgentRow[] = [];
  const drawn = new Set<string>();

  const push = (id: string, depth: 0 | 1) => {
    const worker = state.byId[id];
    if (!worker || drawn.has(id)) return;
    drawn.add(id);
    const no = (rank.get(id) ?? 0) + 1;
    const mine = children.get(id) ?? [];
    rows.push({
      worker,
      depth,
      no,
      hue: hueFor(no - 1),
      collapsed: depth > 0 ? mine.length : 0,
    });
    // Deeper than level 2 flattens onto level 2 rather than growing.
    for (const child of mine) push(child, 1);
  };

  for (const id of ids) if (parentOf(id) === null) push(id, 0);
  /* A cycle in `parent_agent_id` would leave agents unreachable above.
     They are someone else's data; they do not get to hide. */
  for (const id of ids) push(id, 0);

  return rows;
}

/** What this agent is called: its label, else its task, else its id. */
export function agentLabel(worker: WorkerState): string {
  return worker.label || worker.task || shortId(worker.id);
}

/** `worker-1` for one of ours; the kind for one we only watch. */
export function agentKindLabel(worker: WorkerState, no: number): string {
  if (!worker.isExternal) return `worker-${no}`;
  return worker.agentKind || "external";
}

/**
 * An external agent is idle, working, or done. Those are the same three
 * states our own workers have — `running` is working, a report means it
 * has stopped and is waiting, and closed is done — said in the words that
 * fit an agent whose next turn is not ours to start.
 */
const EXTERNAL_STATE: Record<WorkerStatus, string> = {
  running: "working",
  reported: "idle",
  applied: "idle",
  closed: "done",
};

export function agentState(worker: WorkerState): string {
  return worker.isExternal
    ? (EXTERNAL_STATE[worker.status] ?? "idle")
    : worker.status;
}

/**
 * The mark at the head of a fleet row: filled while it is working, hollow
 * while it is idle, and a spent dot once it is done. It carries the agent's
 * own hue, so the row and its trail on the graph are read as one thing.
 *
 * Both vocabularies land here — a worker of ours reports `running` where an
 * external agent reports `working` — because one glyph should not need two
 * call sites to pick it.
 */
export function fleetMark(state: string): string {
  switch (state) {
    case "working":
    case "running":
      return "●";
    case "idle":
    case "reported":
    case "applied":
      return "◯";
    default:
      return "·";
  }
}

/** The glyph in front of the state on a card. */
export function statusMark(status: WorkerStatus | string): string {
  switch (status) {
    case "running":
      return "…";
    case "reported":
    case "applied":
      return "✓";
    default:
      return "·";
  }
}

/**
 * Whether the card may offer `[apply]`.
 *
 * Never for an external agent: it has no patch and no diff, and a button
 * whose only possible outcome is a 400 is not an offer (HAR-84 P2-8).
 */
export function offersApply(worker: WorkerState): boolean {
  if (worker.isExternal) return false;
  return (
    worker.status !== "closed" &&
    worker.reply !== "" &&
    worker.filesChanged.length > 0
  );
}

/**
 * The file to name on a one-line summary. An external agent says where it
 * is, so that is where it is; one of our workers never does, so the best
 * we honestly have is the last file its patch touched — and nothing at all
 * until it reports.
 */
export function agentLastFile(worker: WorkerState): string | null {
  if (worker.files.length > 0) return worker.files[0];
  const changed = worker.filesChanged;
  return changed.length > 0 ? changed[changed.length - 1] : null;
}

/**
 * How long this agent has been going, in seconds. A running one is timed
 * to now; a finished one stops at the last thing the server heard from it.
 * Null until we know when it started — a missing start is not zero.
 */
export function agentElapsed(worker: WorkerState, now: number): number | null {
  if (worker.createdAt === null) return null;
  const end = worker.status === "running" ? now : (worker.updatedAt ?? now);
  return Math.max(0, end - worker.createdAt);
}

/** One `/agents` row, already reduced to the words it prints. */
export interface AgentLine {
  id: string;
  no: number;
  depth: 0 | 1;
  hue: WorkerHue;
  isExternal: boolean;
  kind: string;
  label: string;
  state: string;
  steps: number;
  file: string;
  /** What it is doing right now, or "" — never a stale guess. */
  doing: string;
  /** Seconds since it started, or null where we cannot know. */
  elapsed: number | null;
  /** Cumulative tokens, or null. Null prints nothing, never a zero. */
  tokens: number | null;
  /** Children not drawn beneath it — the `(+2)`. */
  collapsed: number;
}

export function agentLines(state: WorkersState, now: number): AgentLine[] {
  return agentRows(state).map(({ worker, depth, no, hue, collapsed }) => ({
    id: worker.id,
    no,
    depth,
    hue,
    isExternal: worker.isExternal,
    kind: agentKindLabel(worker, no),
    label: agentLabel(worker),
    state: agentState(worker),
    steps: workerCalls(worker),
    file: agentLastFile(worker) ?? "",
    doing: worker.doing,
    elapsed: agentElapsed(worker, now),
    tokens: worker.tokens,
    collapsed,
  }));
}

/**
 * The session's own agent: the `● main` the rest of the fleet hangs off.
 *
 * It is not in `WorkersState` — it is the session — so it is reduced to
 * the same few words here rather than being special-cased in the view.
 */
export interface RootLine {
  label: string;
  state: string;
  doing: string;
  elapsed: number | null;
  tokens: number | null;
  steps: number;
}

/** The three words a session's status becomes on a fleet row. */
function sessionState(status: string): string {
  if (status === "running") return "working";
  if (status === "closed" || status === "failed") return "done";
  return "idle";
}

export function rootLine(
  session: Session | null | undefined,
  now: number,
): RootLine | null {
  if (!session) return null;
  const status = String(session.status ?? "");
  const started = toEpochSeconds(session.created_at);
  const ended =
    status === "running" ? now : (toEpochSeconds(session.updated_at) ?? now);
  return {
    label: "main",
    state: sessionState(status),
    doing: sanitizeText(session.activity, 200),
    elapsed: started === null ? null : Math.max(0, ended - started),
    tokens: typeof session.tokens === "number" ? session.tokens : null,
    steps: typeof session.steps === "number" ? session.steps : 0,
  };
}

/** What `/agents` says when there is nothing to list. */
export const NO_AGENTS = "no agents on this session — /spawn one, or /connect one";

/** The listing as plain text: the same words, without the swatch. */
export function agentsText(state: WorkersState, now = 0): string {
  const lines = agentLines(state, now);
  if (lines.length === 0) return NO_AGENTS;
  return lines
    .map((line) => {
      const indent = line.depth > 0 ? "  " : "";
      const kind = `${line.kind}${line.collapsed > 0 ? ` (+${line.collapsed})` : ""}`;
      const parts = [
        `${line.no}. ${kind}`,
        line.doing || line.label,
        line.state,
        `${line.steps} step${line.steps === 1 ? "" : "s"}`,
        line.elapsed === null ? "" : formatDuration(line.elapsed),
        formatTokens(line.tokens),
        line.file,
      ].filter((part) => part !== "");
      return `${indent}${parts.join(" · ")}`;
    })
    .join("\n");
}

/* ------------------------------------------------------------------ *
 * The resume rail
 *
 * `GET /api/sessions` returns workers alongside the sessions that spawned
 * them, and a flat list makes four workers look like four things you
 * started. They belong under their parent, labelled with the task.
 * ------------------------------------------------------------------ */

export interface RailRow {
  session: Session;
  /** 0 for a session you started, 1 for a worker under its parent. */
  depth: 0 | 1;
}

/**
 * Parents in the order the server gave them, each followed by its workers
 * in the same order. A worker whose parent is not in the list stays at the
 * top level rather than disappearing.
 */
export function nestSessions(sessions: readonly Session[]): RailRow[] {
  const present = new Set(sessions.map((session) => session.id));
  const children = new Map<string, Session[]>();

  for (const session of sessions) {
    const parent = session.parent_id ?? "";
    if (!parent || !present.has(parent)) continue;
    const bucket = children.get(parent);
    if (bucket) bucket.push(session);
    else children.set(parent, [session]);
  }

  const rows: RailRow[] = [];
  for (const session of sessions) {
    const parent = session.parent_id ?? "";
    if (parent && present.has(parent)) continue;
    rows.push({ session, depth: 0 });
    for (const child of children.get(session.id) ?? []) {
      rows.push({ session: child, depth: 1 });
    }
  }
  return rows;
}
