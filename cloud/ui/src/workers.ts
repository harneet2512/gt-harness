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
  };
}

/** The cards, in spawn order. */
export function workerList(state: WorkersState): WorkerState[] {
  return state.order.map((id) => state.byId[id]).filter(Boolean);
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
      const command = str(event.data.command);
      if (!command) return state;
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
          },
        ],
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
          },
        ],
      }));
    }

    case "tool_result": {
      const command = str(event.data.command);
      const output = str(event.data.output);
      const returncode = num(event.data.returncode);
      const isError =
        event.data.is_error === true || (returncode ?? 0) > 0;
      return put(state, agentId, (worker) => {
        const last = worker.activity[worker.activity.length - 1];
        if (last && last.gt === null && last.output === "" && last.returncode === null) {
          const activity = worker.activity.slice(0, -1);
          activity.push({ ...last, output, returncode, isError });
          return { ...worker, activity };
        }
        return {
          ...worker,
          activity: [
            ...worker.activity,
            { key, command, output, returncode, isError, gt: null },
          ],
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
    case "agent_spawned": {
      const task = str(event.data.task);
      return put(state, id, (worker) => ({
        ...worker,
        task: task || worker.task,
      }));
    }

    case "agent_report":
      return put(state, id, (worker) => ({
        ...worker,
        status: moveTo(worker, "reported"),
        reply: str(event.data.content) || worker.reply,
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
    next = put(next, row.id, (worker) => ({
      ...worker,
      task: row.task || worker.task,
      status: statusOfRow(row),
      reply: worker.reply || row.report?.reply_excerpt || "",
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

/** 1-based, for the "worker N" chips. */
export function workerNo(state: WorkersState, id: string): number {
  return state.order.indexOf(id) + 1;
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
