const API = "/api";
const AUTH = "/auth";

/* ------------------------------------------------------------------ *
 * Domain types (see the HAR-84 API contract).
 * ------------------------------------------------------------------ */

/**
 * The contract does not pin a wire format for `created_at` / `updated_at`.
 * Accept both epoch seconds (what the previous server emitted) and ISO
 * strings so the UI cannot be broken by whichever the backend picks.
 */
export type Timestamp = number | string;

export type SessionStatusValue =
  | "creating"
  | "idle"
  | "running"
  | "failed"
  | "closed";

export type GtStatus = "off" | "ready" | "unavailable" | "pending";

/**
 * How much ground truth the agent gets. These are the server's `GTMode`
 * members verbatim: an unknown value is a `ValueError` on the first turn,
 * so the picker never offers one. (`engine` was never a member — HAR-84
 * G-02 — and has been removed.)
 */
export const GT_MODES = ["off", "advisory", "assistive", "enforced"] as const;

export type GtMode = (typeof GT_MODES)[number];

/** One line per mode, for the picker. Nothing here is a guess. */
export const GT_MODE_HELP: Record<GtMode, string> = {
  off: "no GroundTruth",
  advisory: "evidence offered, agent may ignore",
  assistive: "evidence delivered and preferred",
  enforced: "GT controls tool routing (fail-closed)",
};

export function isGtMode(value: string): value is GtMode {
  return (GT_MODES as readonly string[]).includes(value);
}

/** Statuses in which the composer must be disabled. */
export const COMPOSER_LOCKED: ReadonlySet<string> = new Set([
  "creating",
  "closed",
  "failed",
]);

/** Statuses after which the event stream will never produce more frames. */
export const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  "closed",
  "failed",
]);

/**
 * Why a session is no longer open. `user` is a close from the UI, `expired`
 * is the reaper collecting an idle workspace, `failed` is a session that
 * broke. Null on an open session, and absent from a server that predates
 * the field — treat "closed with no reason" as simply closed.
 */
export type ClosedReason = "user" | "expired" | "failed";

export interface Session {
  id: string;
  status: SessionStatusValue | string;
  repo: string;
  ref: string;
  model: string;
  gt_mode: string;
  gt_status: GtStatus | string;
  /**
   * Why the GT index is unavailable, in the indexer's own words. Unlike the
   * `gt_unavailable` lifecycle frame — fired once, at index time — this
   * survives a reload. Null when GT is fine, absent on an older server.
   */
  gt_error?: string | null;
  created_at: Timestamp;
  updated_at: Timestamp;
  last_message: string | null;
  turns: number;
  steps: number;
  cost: number;
  current_turn_id: string | null;
  /** Why it closed. Optional: older servers do not send it. */
  closed_reason?: ClosedReason | string | null;
  /** Wall-clock seconds spent across every turn of the session. */
  total_wall_seconds?: number | null;
  /** Typed GroundTruth actions across every turn of the session. */
  gt_actions?: number;

  /* ---- worker agents (HAR-84) ---------------------------------- *
   * A worker *is* a session: same shape, same routes, one extra set of
   * fields saying whose it is and what it was asked to do. On a session a
   * person created they are `null` / `"primary"`. Optional throughout, so
   * a server that predates them still parses.
   * -------------------------------------------------------------- */

  /** The session that spawned this one. Null on a primary session. */
  parent_id?: string | null;
  role?: SessionRole | string;
  /** The task a worker was spawned with; its opening message. */
  task?: string | null;
  /** The worker's last report to its parent. Null until a turn of its own ends. */
  report?: WorkerReport | null;
  /** When this worker's patch was merged into the parent workspace. */
  applied_at?: number | null;
}

export type SessionRole = "primary" | "worker";

/** What a worker told its parent when a turn of its own ended. */
export interface WorkerReport {
  finish_reason: FinishReason | string;
  /** The opening of the reply, bounded by the server for a list view. */
  reply_excerpt?: string;
  patch_sha256?: string | null;
  files_changed?: string[];
  /** Whether this worker's patch is already in the parent workspace. */
  applied?: boolean;
}

/** True for a session that was spawned by another one. */
export function isWorker(session: Pick<Session, "role" | "parent_id">): boolean {
  return session.role === "worker" || Boolean(session.parent_id);
}

/** The most tasks one `/spawn` may carry, as the server enforces it. */
export const MAX_TASKS_PER_SPAWN = 4;

export interface SessionCreate {
  repo: string;
  ref: string;
  model: string;
  gt_mode: string;
  step_limit: number;
  temperature: number;
  /**
   * Per-turn wall-clock budget in seconds, 60..3600. Omitted means "use the
   * server's TURN_WALL_SECONDS", which is the only place that default should
   * live. Running past it ends the turn with `finish_reason: "time_limit"`.
   */
  wall_seconds?: number;
  /**
   * The first turn's prompt. The server starts it by itself the moment the
   * workspace is ready, so creating and sending are one call rather than a
   * create, a poll for `idle`, and a POST that races it. Blank is a 422.
   */
  first_message?: string;
}

/** The bounds the server enforces on `SessionCreate.wall_seconds`. */
export const WALL_SECONDS_MIN = 60;
export const WALL_SECONDS_MAX = 3600;

export type FinishReason =
  | "reply"
  | "question"
  | "step_limit"
  /** The turn ran out of wall-clock budget, as `step_limit` runs out of steps. */
  | "time_limit"
  | "stopped"
  | "error"
  /** A server restart ended the turn; nothing failed and nothing finished. */
  | "interrupted"
  | "submitted";

/** Finish reasons that stopped a turn at a cap rather than at an answer. */
export const CAP_REASONS: ReadonlySet<string> = new Set([
  "step_limit",
  "time_limit",
]);

/** What the cap was, in the reader's words. */
export function capLabel(reason: string): string {
  return reason === "time_limit" ? "time budget" : "step limit";
}

export type MessageRole = "user" | "agent" | "system";

export interface MessageMeta {
  finish_reason?: FinishReason | string;
  n_calls?: number;
  cost?: number;
  patch_sha256?: string;
  files_changed?: string[];
  /**
   * Set on a `role: "agent"` message a **worker** reported into its parent's
   * conversation. Absent on the parent's own replies — which is the whole
   * protocol: a message that has it belongs to that worker's card.
   */
  agent_id?: string;
  /** Local-only marker for an optimistically appended message. */
  pending?: boolean;
  [key: string]: unknown;
}

export interface Message {
  id: string;
  session_id: string;
  /**
   * The contract types this as a plain string, but a session-level system note
   * has no turn to belong to. Treated as nullable so such a note still renders.
   */
  turn_id: string | null;
  role: MessageRole | string;
  content: string;
  created_at: Timestamp;
  meta: MessageMeta;
}

export type MessageDelivery = "turn_started" | "queued_for_running_turn";

export interface SendMessageResult {
  message: Message;
  delivery: MessageDelivery | string;
}

export type DiffFileStatus = "added" | "modified" | "deleted";

export interface DiffFile {
  path: string;
  status: DiffFileStatus | string;
  additions: number;
  deletions: number;
  /**
   * Not in the published contract, but the server sends it and the inspector
   * wants a single file's patch. Optional: when absent, the whole-session
   * `patch` is split per file instead (see `splitPatch`).
   */
  patch?: string;
}

export interface SessionDiff {
  patch: string;
  files: DiffFile[];
  base_sha: string;
}

/** One tracked file in the workspace, as reported by the tree endpoint. */
export interface TreeFile {
  path: string;
  size: number;
}

/** Every tracked file in the workspace, excluding `.git/` and `.gt_state/`. */
export interface SessionTree {
  base_sha: string;
  files: TreeFile[];
}

/* ------------------------------------------------------------------ *
 * The relation graph: every tracked file as a node, every known relation
 * between two of them as an edge.
 * ------------------------------------------------------------------ */

export type GraphEdgeKind = "import" | "gt_call" | "gt_ref" | "gt_import";

export interface GraphNode {
  id: string;
  path: string;
  size: number;
  lang: string;
  /** Top-level directory, "" for a file at the repository root. */
  dir: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  kind: GraphEdgeKind | string;
  weight: number;
}

export interface SessionGraph {
  base_sha: string;
  gt: boolean;
  /** The server dropped edges to stay within its own budget. */
  truncated?: boolean;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export const EMPTY_GRAPH: SessionGraph = {
  base_sha: "",
  gt: false,
  nodes: [],
  edges: [],
};

export interface Receipt {
  turn_id: string;
  started_at: Timestamp | null;
  finished_at: Timestamp | null;
  n_calls: number;
  cost: number;
  finish_reason: FinishReason | string;
  patch_sha256: string | null;
  gt_status: string;
  model: string;
  /** Wall-clock seconds the turn took, as the server measured it. */
  wall_seconds?: number | null;
  /**
   * How many typed GroundTruth actions this turn ran, and how many of them
   * actually **answered** (`semantics == "exact"` and `match_count > 0`).
   * Optional: a server that predates them sends neither, and 0 actions is
   * a turn where GT never ran rather than one where it failed.
   */
  gt_actions?: number;
  gt_exact_matches?: number;
}

export interface User {
  sub: string;
  login: string;
  name?: string;
  avatar_url?: string;
}

/* ------------------------------------------------------------------ *
 * SSE event envelopes. The server emits one frame per event:
 *   id: <int>\nevent: <type>\ndata: {"id","type","timestamp","data"}
 * Because every frame carries an `event:` field, EventSource.onmessage
 * never fires — listeners must be registered per type.
 *
 * Note the error frame is `agent_error`, not `error`: a server frame named
 * `error` would be delivered to EventSource.onerror as well and is
 * indistinguishable from a transport failure.
 * ------------------------------------------------------------------ */

export const EVENT_TYPES = [
  "lifecycle",
  "turn_started",
  "assistant",
  "tool_call",
  "tool_result",
  "steering",
  "agent_reply",
  "turn_finished",
  "agent_error",
  "system_note",
  /* One per typed GroundTruth action: what was asked, what scope was really
     searched, and what the evidence was. Mirrored like the other four. */
  "gt_action",
  /* Worker agents. The first four are the parent's own frames about its
     workers; the mirrored `assistant`/`tool_call`/`tool_result`/
     `turn_started`/`turn_finished` frames above carry `agent_id` instead. */
  "agent_spawned",
  "agent_report",
  "agent_applied",
  "agent_closed",
] as const;

export type EventType = (typeof EVENT_TYPES)[number];

interface Envelope<T extends string, D> {
  id: number;
  type: T;
  timestamp: number;
  data: D;
}

export type LifecycleStatus =
  | "creating"
  | "cloning"
  | "indexing"
  | "gt_ready"
  | "gt_unavailable"
  /** The sandbox container was re-created under a live session. */
  | "sandbox_restarted"
  | "idle"
  | "running"
  | "stopped"
  | "failed"
  | "closed";

/** Lifecycle phases that are progress steps, not a settled session status. */
const TRANSIENT_LIFECYCLE: ReadonlySet<string> = new Set([
  "cloning",
  "indexing",
  "gt_ready",
  "gt_unavailable",
  "sandbox_restarted",
  "stopped",
]);

/**
 * Map a lifecycle frame onto a `Session.status`, or null when the frame only
 * reports progress inside an existing status (cloning, indexing, gt_*).
 */
export function lifecycleToSessionStatus(
  status: string,
): SessionStatusValue | null {
  if (TRANSIENT_LIFECYCLE.has(status)) return null;
  switch (status) {
    case "creating":
    case "idle":
    case "running":
    case "failed":
    case "closed":
      return status;
    default:
      return null;
  }
}

export interface LifecycleData {
  status: LifecycleStatus | string;
  error?: string;
  /** On `closed`/`failed`: why. Mirrors `Session.closed_reason`. */
  reason?: ClosedReason | string;
  [key: string]: unknown;
}

export interface TurnStartedData {
  /**
   * Present on every frame a **worker** produced, copied onto its parent's
   * stream. Absent — not null — on the parent's own frames. A frame that has
   * it belongs to that worker and to nothing else: never to the primary
   * turn, never to the primary step count.
   */
  agent_id?: string;
  turn_id: string;
  message_id: string;
  /**
   * The prompt that opened the turn. Added for HAR-84 G-02/G-09: without it
   * a second tab can render the turn and the reply but never what was asked.
   */
  content?: string;
  role?: MessageRole | string;
}

/**
 * A message the *server* wrote into the thread — "Server restarted; turn
 * interrupted" and its kind. It is not an error and not a reply; it is the
 * product speaking in its own voice, at the point in the thread where it
 * happened.
 */
export interface SystemNoteData {
  turn_id?: string;
  message_id?: string;
  content?: string;
}

export interface AssistantData {
  /** Set when this frame was mirrored from a worker. See `TurnStartedData`. */
  agent_id?: string;
  turn_id?: string;
  content?: string;
  actions?: string[];
  n_calls?: number;
  cost?: number;
  /**
   * The model call that produced the turn's text reply, rather than a call
   * that chose actions. It counts as a step — without it the live count is
   * one short of `turn_finished.n_calls` — but its content arrives again as
   * `agent_reply`, so nothing may render it twice.
   */
  is_reply?: boolean;
}

export interface ToolCallData {
  /** Set when this frame was mirrored from a worker. See `TurnStartedData`. */
  agent_id?: string;
  turn_id?: string;
  command?: string;
  n_calls?: number;
}

export interface ToolResultData {
  /** Set when this frame was mirrored from a worker. See `TurnStartedData`. */
  agent_id?: string;
  turn_id?: string;
  command?: string;
  output?: string;
  returncode?: number;
  is_error?: boolean;
}

export interface SteeringData {
  turn_id?: string;
  message_id?: string;
  content?: string;
}

export interface AgentReplyData {
  turn_id?: string;
  message_id?: string;
  content?: string;
  finish_reason?: FinishReason | string;
  n_calls?: number;
  cost?: number;
  patch_sha256?: string;
  files_changed?: string[];
}

export interface TurnFinishedData {
  /** Set when this frame was mirrored from a worker. See `TurnStartedData`. */
  agent_id?: string;
  turn_id?: string;
  finish_reason?: FinishReason | string;
  n_calls?: number;
  cost?: number;
}

export interface AgentErrorData {
  turn_id?: string;
  error?: string;
}

/**
 * `gt_action` — one typed GroundTruth query and its answer.
 *
 * `semantics`/`coverage` characterise the evidence (`exact` · `complete`);
 * `reason_codes` and `omissions` are why the producer would not answer.
 * A frame with none of those is a query whose result is not in yet.
 */
export interface GtActionData {
  agent_id?: string;
  turn_id?: string;
  step?: number;
  kind?: string;
  arguments?: Record<string, unknown>;
  scope?: string[] | string;
  returncode?: number;
  semantics?: string;
  coverage?: string;
  match_count?: number;
  omissions?: string[];
  reason_codes?: string[];
  duration_ms?: number;
  evidence_artifact_id?: string;
}

/** `agent_spawned` — one per worker, on the parent's stream, as it is created. */
export interface AgentSpawnedData {
  worker_id?: string;
  task?: string;
}

/** `agent_report` — a worker's turn ended; this is the whole reply. */
export interface AgentReportData {
  worker_id?: string;
  message_id?: string;
  finish_reason?: FinishReason | string;
  content?: string;
  patch_sha256?: string;
  files_changed?: string[];
  n_calls?: number;
  cost?: number;
}

/** `agent_applied` — a worker's patch landed in the parent's workspace. */
export interface AgentAppliedData {
  worker_id?: string;
  files?: string[];
  patch_sha256?: string;
}

/** `agent_closed` — a worker closed, by itself, by its parent, or by the reaper. */
export interface AgentClosedData {
  worker_id?: string;
  reason?: ClosedReason | string;
}

export type SessionEvent =
  | Envelope<"lifecycle", LifecycleData>
  | Envelope<"turn_started", TurnStartedData>
  | Envelope<"assistant", AssistantData>
  | Envelope<"tool_call", ToolCallData>
  | Envelope<"tool_result", ToolResultData>
  | Envelope<"steering", SteeringData>
  | Envelope<"agent_reply", AgentReplyData>
  | Envelope<"turn_finished", TurnFinishedData>
  | Envelope<"agent_error", AgentErrorData>
  | Envelope<"system_note", SystemNoteData>
  | Envelope<"gt_action", GtActionData>
  | Envelope<"agent_spawned", AgentSpawnedData>
  | Envelope<"agent_report", AgentReportData>
  | Envelope<"agent_applied", AgentAppliedData>
  | Envelope<"agent_closed", AgentClosedData>
  | Envelope<"unknown", Record<string, unknown>>;

/**
 * Whose frame this is. A mirrored worker frame carries `agent_id` inside
 * `data`; a primary session's frame does not carry the key at all.
 */
export function agentIdOf(event: SessionEvent): string | null {
  const value = (event.data as { agent_id?: unknown }).agent_id;
  return typeof value === "string" && value !== "" ? value : null;
}

/** Frame types the server mirrors from a worker onto its parent's stream. */
export const MIRRORED_EVENT_TYPES: ReadonlySet<string> = new Set([
  "assistant",
  "tool_call",
  "tool_result",
  "gt_action",
  "turn_started",
  "turn_finished",
]);

function isEventType(value: unknown): value is EventType {
  return (
    typeof value === "string" &&
    (EVENT_TYPES as readonly string[]).includes(value)
  );
}

/**
 * Parse an SSE `data:` payload into a typed envelope. Returns null for
 * malformed frames and for native EventSource error events, which carry no
 * `data` at all.
 */
export function parseEventFrame(raw: unknown): SessionEvent | null {
  if (typeof raw !== "string" || raw.length === 0) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;

  const envelope = parsed as Record<string, unknown>;
  const data =
    typeof envelope.data === "object" && envelope.data !== null
      ? (envelope.data as Record<string, unknown>)
      : {};
  const type = isEventType(envelope.type) ? envelope.type : "unknown";
  const id = typeof envelope.id === "number" ? envelope.id : Number.NaN;
  const timestamp =
    typeof envelope.timestamp === "number"
      ? envelope.timestamp
      : Date.now() / 1000;

  return { id, type, timestamp, data } as SessionEvent;
}

/* ------------------------------------------------------------------ *
 * REST
 * ------------------------------------------------------------------ */

export class ApiError extends Error {
  readonly status: number;
  /**
   * The paths a 3-way merge could not reconcile. The server puts `conflicts`
   * at the top level of the 409 body, beside `detail`, because it is the
   * answer rather than a decoration on the error string.
   */
  readonly conflicts: readonly string[];

  constructor(status: number, message: string, conflicts: readonly string[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.conflicts = conflicts;
  }
}

/** `conflicts: ["a", "b"]` out of an error body, or nothing. */
export function conflictsOf(body: string): string[] {
  try {
    const parsed: unknown = JSON.parse(body);
    if (typeof parsed !== "object" || parsed === null) return [];
    const list = (parsed as { conflicts?: unknown }).conflicts;
    if (!Array.isArray(list)) return [];
    return list.filter((path): path is string => typeof path === "string");
  } catch {
    return [];
  }
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) },
  });
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new ApiError(
      resp.status,
      describe(body) || resp.statusText,
      conflictsOf(body),
    );
  }
  if (resp.status === 204) return undefined as T;
  const text = await resp.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** Prefer a JSON `detail`/`error` field over dumping the raw body at the user. */
function describe(body: string): string {
  if (!body) return "";
  try {
    const parsed: unknown = JSON.parse(body);
    if (typeof parsed === "object" && parsed !== null) {
      const rec = parsed as Record<string, unknown>;
      for (const key of ["detail", "error", "message"]) {
        if (typeof rec[key] === "string") return rec[key] as string;
      }
    }
  } catch {
    /* not JSON — fall through to the raw body */
  }
  return body;
}

const path = (id: string) => `${API}/sessions/${encodeURIComponent(id)}`;

export function createSession(body: SessionCreate): Promise<Session> {
  return request(`${API}/sessions`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listSessions(): Promise<Session[]> {
  return request(`${API}/sessions`);
}

export function getSession(id: string): Promise<Session> {
  return request(path(id));
}

export function getMessages(id: string): Promise<Message[]> {
  return request(`${path(id)}/messages`);
}

export function sendMessage(
  id: string,
  content: string,
): Promise<SendMessageResult> {
  return request(`${path(id)}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export function getDiff(id: string): Promise<SessionDiff> {
  return request(`${path(id)}/diff`);
}

/** Every tracked file with its size. */
export function getTree(id: string): Promise<SessionTree> {
  return request(`${path(id)}/tree`);
}

/** The particles and the filaments: files, and how they relate. */
export function getGraph(id: string): Promise<SessionGraph> {
  return request(`${path(id)}/graph`);
}

export function getReceipts(id: string): Promise<Receipt[]> {
  return request(`${path(id)}/receipts`);
}

export function stopSession(id: string): Promise<unknown> {
  return request(`${path(id)}/stop`, { method: "POST" });
}

export function closeSession(id: string): Promise<unknown> {
  return request(`${path(id)}/close`, { method: "POST" });
}

/* ------------------------------------------------------------------ *
 * Worker agents
 * ------------------------------------------------------------------ */

export interface AgentsSpawned {
  workers: Session[];
}

export interface AgentApplied {
  worker_id: string;
  files: string[];
  patch_sha256?: string | null;
}

/**
 * One worker per task, 1..4 of them, **all of them or none**: the server
 * takes the creation slots for the whole set up front and answers 429 with
 * nothing created if it cannot cover them.
 */
export function spawnAgents(
  id: string,
  tasks: readonly string[],
  over?: { model?: string; gt_mode?: string },
): Promise<AgentsSpawned> {
  return request(`${path(id)}/agents`, {
    method: "POST",
    body: JSON.stringify({ tasks, ...(over ?? {}) }),
  });
}

/** This session's workers, oldest first. Full `Session` rows. */
export function listAgents(id: string): Promise<Session[]> {
  return request(`${path(id)}/agents`);
}

/**
 * Merge a worker's cumulative diff into this session's workspace. A 409
 * carries `conflicts` and leaves the workspace byte-for-byte as it was.
 */
export function applyAgent(id: string, workerId: string): Promise<AgentApplied> {
  return request(
    `${path(id)}/agents/${encodeURIComponent(workerId)}/apply`,
    { method: "POST" },
  );
}

/** Close one worker. Identical to closing it as a session. */
export function closeAgent(id: string, workerId: string): Promise<Session> {
  return request(
    `${path(id)}/agents/${encodeURIComponent(workerId)}/close`,
    { method: "POST" },
  );
}

/**
 * Who is signed in, and — when nobody is — why. A 401 whose `detail` names
 * an expired sign-in is not the same as never having signed in, and the
 * card that comes up has to say so rather than let someone mid-task
 * conclude the app broke (HAR-84 P2-7).
 */
export interface AuthState {
  user: User | null;
  /** The sentence the sign-in card shows above the button, or null. */
  notice: string | null;
}

/** The server's own words for a token whose `exp` has passed. */
const EXPIRED_DETAILS = ["sign-in expired", "session expired"];

/** `sign-in expired; sign in again` → the line a reader needs. */
export function expiryNotice(detail: string): string | null {
  const text = detail.toLowerCase();
  return EXPIRED_DETAILS.some((phrase) => text.includes(phrase))
    ? "your sign-in expired — sign in again"
    : null;
}

/** Resolves to the signed-in user, or to why there is not one. */
export async function getMe(): Promise<AuthState> {
  try {
    return { user: await request<User>(`${AUTH}/me`), notice: null };
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      return { user: null, notice: expiryNotice(err.message) };
    }
    throw err;
  }
}

export const LOGIN_URL = `${AUTH}/login`;

/**
 * Open the event stream. `afterId` replays only events newer than the last one
 * already rendered, so a reconnect does not duplicate the whole history.
 */
export function streamUrl(id: string, afterId: number): string {
  /* A non-integer `after_id` is a 400 on a strict server and a full history
     replay on a lax one (HAR-84 G-17). Neither is worth risking over a
     value that only ever comes from our own ingest, so it is floored to a
     positive integer here or left off entirely. */
  const n = Math.floor(afterId);
  const suffix = Number.isFinite(n) && n > 0 ? `?after_id=${n}` : "";
  return `${path(id)}/events${suffix}`;
}

export function subscribeEvents(id: string, afterId = 0): EventSource {
  return new EventSource(streamUrl(id, afterId), { withCredentials: true });
}
