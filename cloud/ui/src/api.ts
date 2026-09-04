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

export type GtMode = "off" | "advisory" | "engine";

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

export interface Session {
  id: string;
  status: SessionStatusValue | string;
  repo: string;
  ref: string;
  model: string;
  gt_mode: string;
  gt_status: GtStatus | string;
  created_at: Timestamp;
  updated_at: Timestamp;
  last_message: string | null;
  turns: number;
  steps: number;
  cost: number;
  current_turn_id: string | null;
}

export interface SessionCreate {
  repo: string;
  ref: string;
  model: string;
  gt_mode: string;
  step_limit: number;
  temperature: number;
}

export type FinishReason =
  | "reply"
  | "question"
  | "step_limit"
  | "stopped"
  | "error"
  | "submitted";

export type MessageRole = "user" | "agent" | "system";

export interface MessageMeta {
  finish_reason?: FinishReason | string;
  n_calls?: number;
  cost?: number;
  patch_sha256?: string;
  files_changed?: string[];
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
  [key: string]: unknown;
}

export interface TurnStartedData {
  turn_id: string;
  message_id: string;
}

export interface AssistantData {
  turn_id?: string;
  content?: string;
  actions?: string[];
  n_calls?: number;
  cost?: number;
}

export interface ToolCallData {
  turn_id?: string;
  command?: string;
  n_calls?: number;
}

export interface ToolResultData {
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
  turn_id?: string;
  finish_reason?: FinishReason | string;
  n_calls?: number;
  cost?: number;
}

export interface AgentErrorData {
  turn_id?: string;
  error?: string;
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
  | Envelope<"unknown", Record<string, unknown>>;

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

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
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
    throw new ApiError(resp.status, describe(body) || resp.statusText);
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

/** Resolves to the signed-in user, or null when the server answers 401. */
export async function getMe(): Promise<User | null> {
  try {
    return await request<User>(`${AUTH}/me`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return null;
    throw err;
  }
}

export const LOGIN_URL = `${AUTH}/login`;

/**
 * Open the event stream. `afterId` replays only events newer than the last one
 * already rendered, so a reconnect does not duplicate the whole history.
 */
export function subscribeEvents(id: string, afterId = 0): EventSource {
  const suffix = afterId > 0 ? `?after_id=${afterId}` : "";
  return new EventSource(`${path(id)}/events${suffix}`, {
    withCredentials: true,
  });
}
