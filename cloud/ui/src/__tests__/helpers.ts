import type { Message, Receipt, Session, SessionEvent } from "../api";

/** One SSE envelope, as `parseEventFrame` would have produced it. */
export function ev(
  id: number,
  type: string,
  data: Record<string, unknown> = {},
  timestamp = id,
): SessionEvent {
  return { id, type, timestamp, data } as SessionEvent;
}

/** The raw `data:` payload of one frame, for the ingest tests. */
export function frame(
  id: number,
  type: string,
  data: Record<string, unknown> = {},
  timestamp = id,
): string {
  return JSON.stringify({ id, type, timestamp, data });
}

export function msg(over: Partial<Message> & { id: string }): Message {
  return {
    session_id: "s1",
    turn_id: null,
    role: "user",
    content: "",
    created_at: 0,
    meta: {},
    ...over,
  };
}

export function session(over: Partial<Session> = {}): Session {
  return {
    id: "s1",
    status: "idle",
    repo: "https://github.com/octocat/Hello-World",
    ref: "main",
    model: "deepseek/deepseek-v4-flash",
    gt_mode: "advisory",
    gt_status: "ready",
    created_at: 0,
    updated_at: 0,
    last_message: null,
    turns: 0,
    steps: 0,
    cost: 0,
    current_turn_id: null,
    ...over,
  };
}

export function receipt(over: Partial<Receipt> = {}): Receipt {
  return {
    turn_id: "t1",
    started_at: 100,
    finished_at: 160,
    n_calls: 4,
    cost: 0,
    finish_reason: "reply",
    patch_sha256: null,
    gt_status: "ready",
    model: "deepseek/deepseek-v4-flash",
    ...over,
  };
}
