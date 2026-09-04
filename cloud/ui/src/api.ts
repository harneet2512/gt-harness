const API = "/api";

export interface SessionCreate {
  repo: string;
  ref: string;
  task: string;
  model: string;
  gt_mode: string;
  step_limit: number;
  temperature: number;
}

export interface SessionStatus {
  id: string;
  status: string;
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

export interface SessionEvent {
  id: number;
  type: string;
  data: Record<string, unknown>;
  timestamp: number;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`${resp.status}: ${body}`);
  }
  return resp.json();
}

export function createSession(body: SessionCreate): Promise<SessionStatus> {
  return request("/sessions", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listSessions(): Promise<SessionStatus[]> {
  return request("/sessions");
}

export function getSession(id: string): Promise<SessionStatus> {
  return request(`/sessions/${id}`);
}

export function getResult(id: string): Promise<SessionResult> {
  return request(`/sessions/${id}/result`);
}

export function steerSession(id: string, content: string): Promise<void> {
  return request(`/sessions/${id}/steer`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export function stopSession(id: string): Promise<void> {
  return request(`/sessions/${id}/stop`, { method: "POST" });
}

export function subscribeEvents(id: string): EventSource {
  return new EventSource(`${API}/sessions/${id}/events`);
}
