import type { FinishReason, Message, SessionEvent } from "./api";

/* ------------------------------------------------------------------ *
 * Thread state: the messages of a session plus the per-turn activity
 * (assistant thoughts, commands, command output) reconstructed from the
 * event stream. All updates are pure — every action returns a new state.
 * ------------------------------------------------------------------ */

export type ActivityItem =
  | {
      key: string;
      kind: "assistant";
      content: string;
      actions: string[];
      nCalls: number | null;
      cost: number | null;
    }
  | { key: string; kind: "tool_call"; command: string; nCalls: number | null }
  | {
      key: string;
      kind: "tool_result";
      command: string;
      output: string;
      returncode: number | null;
      isError: boolean;
    }
  | {
      key: string;
      kind: "steering";
      messageId: string | null;
      content: string;
    }
  | { key: string; kind: "error"; error: string };

export interface TurnState {
  id: string;
  items: ActivityItem[];
  startedAt: number | null;
  finishedAt: number | null;
  finishReason: FinishReason | string | null;
  nCalls: number | null;
  cost: number | null;
  commands: number;
}

export interface ChatState {
  messages: Message[];
  /** message id -> index into `messages`. */
  index: Record<string, number>;
  turns: Record<string, TurnState>;
  turnOrder: string[];
  /** message id -> turn id, learned from turn_started / steering / agent_reply. */
  turnByMessage: Record<string, string>;
  /** Message ids that belong inside an activity block rather than the thread. */
  steeringIds: Record<string, true>;
  /**
   * Every frame in arrival order, kept raw. The workspace panel (Progress,
   * Shell, Plan) and the replay slider read from here; the stream hook has
   * already de-duplicated by envelope id.
   */
  events: SessionEvent[];
}

export const emptyChat: ChatState = {
  messages: [],
  index: {},
  turns: {},
  turnOrder: [],
  turnByMessage: {},
  steeringIds: {},
  events: [],
};

export type ChatAction =
  | { type: "hydrate"; messages: Message[] }
  | { type: "event"; event: SessionEvent }
  | { type: "optimistic"; message: Message }
  | { type: "settle"; tempId: string; message: Message }
  | { type: "drop"; id: string };

function emptyTurn(id: string): TurnState {
  return {
    id,
    items: [],
    startedAt: null,
    finishedAt: null,
    finishReason: null,
    nCalls: null,
    cost: null,
    commands: 0,
  };
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** Insert or merge a message, preserving arrival order for new ids. */
function upsert(state: ChatState, incoming: Message): ChatState {
  const at = state.index[incoming.id];
  if (at === undefined) {
    return {
      ...state,
      messages: [...state.messages, incoming],
      index: { ...state.index, [incoming.id]: state.messages.length },
    };
  }
  const current = state.messages[at];
  const merged: Message = {
    ...current,
    ...incoming,
    // A replayed frame may omit fields the REST record already supplied.
    content: incoming.content || current.content,
    turn_id: incoming.turn_id ?? current.turn_id,
    session_id: incoming.session_id || current.session_id,
    meta: { ...current.meta, ...incoming.meta },
  };
  const messages = [...state.messages];
  messages[at] = merged;
  return { ...state, messages };
}

function withTurn(
  state: ChatState,
  turnId: string,
  update: (turn: TurnState) => TurnState,
): ChatState {
  const existing = state.turns[turnId];
  const next = update(existing ?? emptyTurn(turnId));
  return {
    ...state,
    turns: { ...state.turns, [turnId]: next },
    turnOrder: existing ? state.turnOrder : [...state.turnOrder, turnId],
  };
}

function appendItem(
  state: ChatState,
  turnId: string,
  item: ActivityItem,
): ChatState {
  return withTurn(state, turnId, (turn) => ({
    ...turn,
    items: [...turn.items, item],
    commands: turn.commands + (item.kind === "tool_call" ? 1 : 0),
  }));
}

function linkMessage(
  state: ChatState,
  messageId: string,
  turnId: string,
): ChatState {
  if (!messageId || state.turnByMessage[messageId] === turnId) return state;
  return {
    ...state,
    turnByMessage: { ...state.turnByMessage, [messageId]: turnId },
  };
}

function applyEvent(state: ChatState, event: SessionEvent): ChatState {
  const key = `ev-${event.id}`;
  switch (event.type) {
    case "turn_started": {
      const turnId = str(event.data.turn_id);
      if (!turnId) return state;
      let next = withTurn(state, turnId, (turn) => ({
        ...turn,
        startedAt: turn.startedAt ?? event.timestamp,
      }));
      next = linkMessage(next, str(event.data.message_id), turnId);
      return next;
    }

    case "assistant": {
      const turnId = str(event.data.turn_id);
      if (!turnId) return state;
      const content = str(event.data.content);
      const actions = Array.isArray(event.data.actions)
        ? event.data.actions.filter((a): a is string => typeof a === "string")
        : [];
      if (!content && actions.length === 0) return state;
      return appendItem(state, turnId, {
        key,
        kind: "assistant",
        content,
        actions,
        nCalls: num(event.data.n_calls),
        cost: num(event.data.cost),
      });
    }

    case "tool_call": {
      const turnId = str(event.data.turn_id);
      if (!turnId) return state;
      return appendItem(state, turnId, {
        key,
        kind: "tool_call",
        command: str(event.data.command),
        nCalls: num(event.data.n_calls),
      });
    }

    case "tool_result": {
      const turnId = str(event.data.turn_id);
      if (!turnId) return state;
      return appendItem(state, turnId, {
        key,
        kind: "tool_result",
        command: str(event.data.command),
        output: str(event.data.output),
        returncode: num(event.data.returncode),
        isError:
          event.data.is_error === true || (num(event.data.returncode) ?? 0) > 0,
      });
    }

    case "steering": {
      const turnId = str(event.data.turn_id);
      if (!turnId) return state;
      const messageId = str(event.data.message_id);
      const content = str(event.data.content);
      let next = appendItem(state, turnId, {
        key,
        kind: "steering",
        messageId: messageId || null,
        content,
      });
      if (messageId) {
        next = linkMessage(next, messageId, turnId);
        next = { ...next, steeringIds: { ...next.steeringIds, [messageId]: true } };
        next = upsert(next, {
          id: messageId,
          session_id: "",
          turn_id: turnId,
          role: "user",
          content,
          created_at: event.timestamp,
          meta: {},
        });
      }
      return next;
    }

    case "agent_reply": {
      const turnId = str(event.data.turn_id);
      const messageId = str(event.data.message_id);
      if (!messageId) return state;
      let next = turnId ? linkMessage(state, messageId, turnId) : state;
      return upsert(next, {
        id: messageId,
        session_id: "",
        turn_id: turnId || null,
        role: "agent",
        content: str(event.data.content),
        created_at: event.timestamp,
        meta: {
          finish_reason: event.data.finish_reason,
          n_calls: num(event.data.n_calls) ?? undefined,
          cost: num(event.data.cost) ?? undefined,
          patch_sha256: event.data.patch_sha256,
          files_changed: event.data.files_changed,
        },
      });
    }

    case "turn_finished": {
      const turnId = str(event.data.turn_id);
      if (!turnId) return state;
      return withTurn(state, turnId, (turn) => ({
        ...turn,
        finishedAt: event.timestamp,
        finishReason: event.data.finish_reason ?? turn.finishReason,
        nCalls: num(event.data.n_calls) ?? turn.nCalls,
        cost: num(event.data.cost) ?? turn.cost,
      }));
    }

    case "agent_error": {
      const turnId = str(event.data.turn_id);
      const error = str(event.data.error) || "agent error";
      if (turnId) {
        return appendItem(state, turnId, { key, kind: "error", error });
      }
      // No turn to attach to: surface it as a session-level system note.
      return upsert(state, {
        id: key,
        session_id: "",
        turn_id: null,
        role: "system",
        content: error,
        created_at: event.timestamp,
        meta: { finish_reason: "error" },
      });
    }

    default:
      return state;
  }
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "hydrate":
      return action.messages.reduce(upsert, state);

    case "event":
      return applyEvent(
        { ...state, events: [...state.events, action.event] },
        action.event,
      );

    case "optimistic":
      return upsert(state, action.message);

    case "settle": {
      const dropped = removeMessage(state, action.tempId);
      return upsert(dropped, action.message);
    }

    case "drop":
      return removeMessage(state, action.id);

    default:
      return state;
  }
}

function removeMessage(state: ChatState, id: string): ChatState {
  if (state.index[id] === undefined) return state;
  const messages = state.messages.filter((m) => m.id !== id);
  const index: Record<string, number> = {};
  messages.forEach((m, i) => {
    index[m.id] = i;
  });
  return { ...state, messages, index };
}

/* ------------------------------------------------------------------ *
 * Grouping: the thread renders one block per turn (prompt → activity →
 * reply), plus standalone system notes that belong to no turn.
 * ------------------------------------------------------------------ */

export interface TurnGroup {
  kind: "turn";
  turnId: string;
  prompt: Message | null;
  steering: Message[];
  replies: Message[];
  notes: Message[];
}

export interface NoteGroup {
  kind: "note";
  message: Message;
}

export type ThreadGroup = TurnGroup | NoteGroup;

export function buildGroups(state: ChatState): ThreadGroup[] {
  const groups: ThreadGroup[] = [];
  const byTurn = new Map<string, TurnGroup>();

  const groupFor = (turnId: string): TurnGroup => {
    const existing = byTurn.get(turnId);
    if (existing) return existing;
    const created: TurnGroup = {
      kind: "turn",
      turnId,
      prompt: null,
      steering: [],
      replies: [],
      notes: [],
    };
    byTurn.set(turnId, created);
    groups.push(created);
    return created;
  };

  for (const message of state.messages) {
    const turnId = message.turn_id ?? state.turnByMessage[message.id] ?? null;
    if (!turnId) {
      groups.push({ kind: "note", message });
      continue;
    }
    const group = groupFor(turnId);
    if (message.role === "agent") {
      group.replies.push(message);
    } else if (message.role === "system") {
      group.notes.push(message);
    } else if (group.prompt === null && !state.steeringIds[message.id]) {
      group.prompt = message;
    } else {
      group.steering.push(message);
    }
  }

  // A turn whose prompt has not reached us yet still has activity to show.
  for (const turnId of state.turnOrder) {
    if (!byTurn.has(turnId)) groupFor(turnId);
  }

  return groups;
}

/** Steering messages with no matching `steering` frame, rendered as a fallback. */
export function orphanSteering(
  group: TurnGroup,
  turn: TurnState | undefined,
): Message[] {
  if (!turn) return group.steering;
  const rendered = new Set(
    turn.items
      .filter((i) => i.kind === "steering" && i.messageId)
      .map((i) => (i as { messageId: string }).messageId),
  );
  return group.steering.filter((m) => !rendered.has(m.id));
}
