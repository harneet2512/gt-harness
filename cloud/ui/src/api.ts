const API = "/api";
const AUTH = "/auth";

export interface SessionCreate {
  repo: string;
  ref: string;
  task: string;
  model: string;
  gt_mode: string;
  step_limit: number;
  temperature: number;
}

export type SessionStatusValue =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "stopped";

export const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "stopped",
]);

export interface SessionStatus {
  id: string;
  status: SessionStatusValue | string;
  repo: string;
  ref: string;
  task: string;
  model: string;
  gt_mode: string;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  steps: number;
  cost: number;
}

export interface SessionResult {
  id: string;
  patch: string | null;
  receipt: Record<string, unknown> | null;
  trajectory: Record<string, unknown> | null;
  terminal_outcome: string;
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
 * ------------------------------------------------------------------ */

export const EVENT_TYPES = [
  "lifecycle",
  "assistant",
  "tool_call",
  "tool_result",
  "steering",
  "error",
] as const;

export type EventType = (typeof EVENT_TYPES)[number];

interface Envelope<T extends string, D> {
  id: number;
  type: T;
  timestamp: number;
  data: D;
}

export type LifecycleStatus =
  | "cloning"
  | "building_agent"
  | "gt_ready"
  | "gt_unavailable"
  | "running"
  | "completed"
  | "stopped"
  | "failed";

export interface LifecycleData {
  status: LifecycleStatus | string;
  exit_status?: string;
  n_calls?: number;
  cost?: number;
  error?: string;
  repo?: string;
  ref?: string;
  [key: string]: unknown;
}

export interface AssistantData {
  content?: string;
  actions?: string[];
  n_calls?: number;
  cost?: number;
}

export interface ToolCallData {
  command?: string;
  n_calls?: number;
}

export interface ToolResultData {
  command?: string;
  output?: string;
  returncode?: number;
  is_error?: boolean;
}

export interface SteeringData {
  content?: string;
}

export interface ErrorData {
  error?: string;
  traceback?: string;
}

export type SessionEvent =
  | Envelope<"lifecycle", LifecycleData>
  | Envelope<"assistant", AssistantData>
  | Envelope<"tool_call", ToolCallData>
  | Envelope<"tool_result", ToolResultData>
  | Envelope<"steering", SteeringData>
  | Envelope<"error", ErrorData>
  | Envelope<"unknown", Record<string, unknown>>;

function isEventType(value: unknown): value is EventType {
  return (
    typeof value === "string" && (EVENT_TYPES as readonly string[]).includes(value)
  );
}

/**
 * Parse an SSE `data:` payload into a typed envelope. Returns null for
 * malformed frames and for native EventSource error events (which have no
 * `data` at all, yet arrive on the same "error" listener as server frames).
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
    typeof envelope.timestamp === "number" ? envelope.timestamp : Date.now() / 1000;

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
    throw new ApiError(resp.status, body || resp.statusText);
  }
  if (resp.status === 204) return undefined as T;
  const text = await resp.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export function createSession(body: SessionCreate): Promise<SessionStatus> {
  return request(`${API}/sessions`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listSessions(): Promise<SessionStatus[]> {
  return request(`${API}/sessions`);
}

export function getSession(id: string): Promise<SessionStatus> {
  return request(`${API}/sessions/${encodeURIComponent(id)}`);
}

export function getResult(id: string): Promise<SessionResult> {
  return request(`${API}/sessions/${encodeURIComponent(id)}/result`);
}

export function steerSession(id: string, content: string): Promise<unknown> {
  return request(`${API}/sessions/${encodeURIComponent(id)}/steer`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export function stopSession(id: string): Promise<unknown> {
  return request(`${API}/sessions/${encodeURIComponent(id)}/stop`, {
    method: "POST",
  });
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
  return new EventSource(
    `${API}/sessions/${encodeURIComponent(id)}/events${suffix}`,
    { withCredentials: true },
  );
}
