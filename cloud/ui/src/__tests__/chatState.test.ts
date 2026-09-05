import { describe, expect, it } from "vitest";
import {
  buildGroups,
  chatReducer,
  emptyChat,
  orphanSteering,
  type ChatState,
  type TurnGroup,
} from "../chatState";
import { ev, msg } from "./helpers";

/** Fold a list of actions over the reducer. */
function run(
  actions: readonly Parameters<typeof chatReducer>[1][],
  from: ChatState = emptyChat,
): ChatState {
  return actions.reduce(chatReducer, from);
}

const turnOf = (state: ChatState, id: string): TurnGroup =>
  buildGroups(state).find(
    (g): g is TurnGroup => g.kind === "turn" && g.turnId === id,
  )!;

describe("chatState — messages", () => {
  it("appends hydrated messages in arrival order", () => {
    const state = run([
      { type: "hydrate", messages: [msg({ id: "a" }), msg({ id: "b" })] },
    ]);
    expect(state.messages.map((m) => m.id)).toEqual(["a", "b"]);
    expect(state.index).toEqual({ a: 0, b: 1 });
  });

  it("merges a second record with the same id rather than duplicating it", () => {
    const state = run([
      { type: "hydrate", messages: [msg({ id: "a", content: "first" })] },
      {
        type: "hydrate",
        messages: [msg({ id: "a", content: "", turn_id: "t1" })],
      },
    ]);
    expect(state.messages).toHaveLength(1);
    // A replayed frame that omits the body must not blank the body.
    expect(state.messages[0].content).toBe("first");
    expect(state.messages[0].turn_id).toBe("t1");
  });

  it("settles an optimistic message onto its server id", () => {
    const state = run([
      {
        type: "optimistic",
        message: msg({ id: "local-1", content: "hi", meta: { pending: true } }),
      },
      {
        type: "settle",
        tempId: "local-1",
        message: msg({ id: "m9", content: "hi", turn_id: "t1" }),
      },
    ]);
    expect(state.messages.map((m) => m.id)).toEqual(["m9"]);
    expect(state.index).toEqual({ m9: 0 });
  });

  it("reindexes after a drop", () => {
    const state = run([
      {
        type: "hydrate",
        messages: [msg({ id: "a" }), msg({ id: "b" }), msg({ id: "c" })],
      },
      { type: "drop", id: "b" },
    ]);
    expect(state.messages.map((m) => m.id)).toEqual(["a", "c"]);
    expect(state.index).toEqual({ a: 0, c: 1 });
  });
});

describe("chatState — per-turn activity", () => {
  it("groups activity under the turn each frame names", () => {
    const state = run([
      { type: "event", event: ev(1, "turn_started", { turn_id: "t1" }) },
      {
        type: "event",
        event: ev(2, "assistant", { turn_id: "t1", content: "look first" }),
      },
      {
        type: "event",
        event: ev(3, "tool_call", { turn_id: "t1", command: "ls" }),
      },
      {
        type: "event",
        event: ev(4, "tool_result", {
          turn_id: "t1",
          command: "ls",
          output: "a\n",
          returncode: 0,
        }),
      },
      {
        type: "event",
        event: ev(5, "assistant", { turn_id: "t2", content: "other turn" }),
      },
    ]);

    expect(state.turnOrder).toEqual(["t1", "t2"]);
    expect(state.turns.t1.items.map((i) => i.kind)).toEqual([
      "assistant",
      "tool_call",
      "tool_result",
    ]);
    expect(state.turns.t1.commands).toBe(1);
    expect(state.turns.t1.startedAt).toBe(1);
    expect(state.turns.t2.items).toHaveLength(1);
  });

  it("keeps an is_reply frame as a step but renders no content for it", () => {
    const state = run([
      {
        type: "event",
        event: ev(1, "assistant", {
          turn_id: "t1",
          content: "the whole answer, again",
          actions: ["echo hi"],
          is_reply: true,
        }),
      },
    ]);
    const item = state.turns.t1.items[0];
    expect(item.kind).toBe("assistant");
    if (item.kind !== "assistant") throw new Error("unreachable");
    expect(item.isReply).toBe(true);
    // `agent_reply` carries this text; printing it here would print it twice.
    expect(item.content).toBe("");
    expect(item.actions).toEqual([]);
  });

  it("drops an empty non-reply assistant frame entirely", () => {
    const state = run([
      { type: "event", event: ev(1, "assistant", { turn_id: "t1" }) },
    ]);
    expect(state.turns.t1).toBeUndefined();
    expect(state.turnOrder).toEqual([]);
  });

  it("ignores a frame with no turn to attach to", () => {
    const state = run([
      { type: "event", event: ev(1, "tool_call", { command: "ls" }) },
    ]);
    expect(state.turnOrder).toEqual([]);
    // The raw frame is still kept for the replay slider.
    expect(state.events).toHaveLength(1);
  });

  it("marks a tool_result as an error on a non-zero return code", () => {
    const state = run([
      {
        type: "event",
        event: ev(1, "tool_result", {
          turn_id: "t1",
          command: "pytest",
          returncode: 1,
        }),
      },
    ]);
    const item = state.turns.t1.items[0];
    if (item.kind !== "tool_result") throw new Error("unreachable");
    expect(item.isError).toBe(true);
  });
});

describe("chatState — steering", () => {
  it("places a steering message inside the turn, not as its prompt", () => {
    const state = run([
      {
        type: "hydrate",
        messages: [msg({ id: "m1", turn_id: "t1", content: "do the thing" })],
      },
      {
        type: "event",
        event: ev(2, "steering", {
          turn_id: "t1",
          message_id: "m2",
          content: "also check the tests",
        }),
      },
    ]);

    expect(state.steeringIds).toEqual({ m2: true });
    expect(state.turnByMessage.m2).toBe("t1");

    const group = turnOf(state, "t1");
    expect(group.prompt?.id).toBe("m1");
    expect(group.steering.map((m) => m.id)).toEqual(["m2"]);
    expect(state.turns.t1.items.map((i) => i.kind)).toEqual(["steering"]);
  });

  it("does not re-render a steering message the activity block already shows", () => {
    const state = run([
      {
        type: "event",
        event: ev(1, "steering", {
          turn_id: "t1",
          message_id: "m2",
          content: "hurry up",
        }),
      },
    ]);
    const group = turnOf(state, "t1");
    expect(orphanSteering(group, state.turns.t1)).toEqual([]);
  });

  it("falls back to rendering a steering message with no frame behind it", () => {
    const state = run([
      {
        type: "hydrate",
        messages: [
          msg({ id: "m1", turn_id: "t1", content: "prompt" }),
          msg({ id: "m2", turn_id: "t1", content: "mid-turn, unframed" }),
        ],
      },
    ]);
    const group = turnOf(state, "t1");
    expect(orphanSteering(group, state.turns.t1).map((m) => m.id)).toEqual([
      "m2",
    ]);
  });
});

describe("chatState — turn_finished", () => {
  it("records the server's totals over the live ones", () => {
    const state = run([
      { type: "event", event: ev(1, "turn_started", { turn_id: "t1" }) },
      {
        type: "event",
        event: ev(9, "turn_finished", {
          turn_id: "t1",
          finish_reason: "time_limit",
          n_calls: 34,
          cost: 0.12,
        }),
      },
    ]);
    const turn = state.turns.t1;
    expect(turn.finishedAt).toBe(9);
    expect(turn.finishReason).toBe("time_limit");
    expect(turn.nCalls).toBe(34);
    expect(turn.cost).toBe(0.12);
  });

  it("creates the turn when the finish frame is the first one seen", () => {
    const state = run([
      {
        type: "event",
        event: ev(1, "turn_finished", { turn_id: "t7", n_calls: 2 }),
      },
    ]);
    expect(state.turnOrder).toEqual(["t7"]);
    expect(state.turns.t7.nCalls).toBe(2);
    // Still shows up in the thread, with no prompt and no activity.
    expect(turnOf(state, "t7").prompt).toBeNull();
  });

  it("leaves the totals alone when the frame omits them", () => {
    const state = run([
      {
        type: "event",
        event: ev(1, "turn_finished", {
          turn_id: "t1",
          finish_reason: "reply",
          n_calls: 5,
        }),
      },
      { type: "event", event: ev(2, "turn_finished", { turn_id: "t1" }) },
    ]);
    expect(state.turns.t1.nCalls).toBe(5);
    expect(state.turns.t1.finishReason).toBe("reply");
  });
});

describe("chatState — agent_error", () => {
  it("attaches an error to its turn", () => {
    const state = run([
      {
        type: "event",
        event: ev(1, "agent_error", { turn_id: "t1", error: "boom" }),
      },
    ]);
    const item = state.turns.t1.items[0];
    expect(item).toMatchObject({ kind: "error", error: "boom" });
  });

  it("surfaces a turn-less error as a session note", () => {
    const state = run([
      { type: "event", event: ev(4, "agent_error", { error: "no sandbox" }) },
    ]);
    expect(state.turnOrder).toEqual([]);
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]).toMatchObject({
      id: "ev-4",
      role: "system",
      content: "no sandbox",
    });
    expect(buildGroups(state)[0].kind).toBe("note");
  });
});

describe("chatState — agent_reply", () => {
  it("upserts the reply with its receipt meta", () => {
    const state = run([
      {
        type: "event",
        event: ev(1, "agent_reply", {
          turn_id: "t1",
          message_id: "m5",
          content: "done",
          finish_reason: "time_limit",
          n_calls: 12,
          cost: 0,
          files_changed: ["a.py"],
        }),
      },
    ]);
    expect(state.messages[0]).toMatchObject({
      id: "m5",
      role: "agent",
      content: "done",
    });
    expect(state.messages[0].meta.finish_reason).toBe("time_limit");
    expect(state.messages[0].meta.n_calls).toBe(12);
    expect(turnOf(state, "t1").replies.map((m) => m.id)).toEqual(["m5"]);
  });
});

/* ------------------------------------------------------------------ *
 * HAR-84 G-09 — two tabs on one session disagreed: the second rendered
 * the turn, the trail and the reply, but never the prompt, because
 * `turn_started` carried only an id. It now carries the text.
 * ------------------------------------------------------------------ */
describe("chatState — the prompt on turn_started", () => {
  it("creates the user message from a frame for an id it has never seen", () => {
    const state = run([
      {
        type: "event",
        event: ev(1, "turn_started", {
          turn_id: "t1",
          message_id: "m1",
          role: "user",
          content: "TABSYNC",
        }),
      },
    ]);

    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]).toMatchObject({
      id: "m1",
      role: "user",
      content: "TABSYNC",
      turn_id: "t1",
    });
    expect(turnOf(state, "t1").prompt?.content).toBe("TABSYNC");
  });

  it("merges rather than duplicates a message id it already has", () => {
    const state = run([
      { type: "hydrate", messages: [msg({ id: "m1", content: "TABSYNC" })] },
      {
        type: "event",
        event: ev(2, "turn_started", {
          turn_id: "t1",
          message_id: "m1",
          role: "user",
          content: "TABSYNC",
        }),
      },
    ]);

    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].content).toBe("TABSYNC");
    // The frame is what told us which turn the message belongs to.
    expect(state.messages[0].turn_id).toBe("t1");
    expect(state.turnByMessage.m1).toBe("t1");
  });

  it("yields to the sending tab's own copy until the POST settles it", () => {
    const sent = run([
      {
        type: "optimistic",
        message: msg({
          id: "local-1",
          content: "TABSYNC",
          meta: { pending: true },
        }),
      },
      // The stream beats the HTTP response back.
      {
        type: "event",
        event: ev(2, "turn_started", {
          turn_id: "t1",
          message_id: "m1",
          role: "user",
          content: "TABSYNC",
        }),
      },
    ]);
    expect(sent.messages.map((m) => m.content)).toEqual(["TABSYNC"]);

    const settled = run(
      [
        {
          type: "settle",
          tempId: "local-1",
          message: msg({ id: "m1", content: "TABSYNC", turn_id: "t1" }),
        },
      ],
      sent,
    );
    expect(settled.messages.map((m) => m.id)).toEqual(["m1"]);
  });

  it("still creates a different message while one of ours is pending", () => {
    const state = run([
      {
        type: "optimistic",
        message: msg({ id: "local-1", content: "mine", meta: { pending: true } }),
      },
      {
        type: "event",
        event: ev(2, "turn_started", {
          turn_id: "t1",
          message_id: "m9",
          role: "user",
          content: "from the other tab",
        }),
      },
    ]);
    expect(state.messages.map((m) => m.content)).toEqual([
      "mine",
      "from the other tab",
    ]);
  });

  it("links the id and nothing else on a server that sends no content", () => {
    const state = run([
      {
        type: "event",
        event: ev(1, "turn_started", { turn_id: "t1", message_id: "m1" }),
      },
    ]);
    expect(state.messages).toHaveLength(0);
    expect(state.turnByMessage.m1).toBe("t1");
    expect(state.turns.t1.startedAt).toBe(1);
  });
});

/* ------------------------------------------------------------------ *
 * HAR-84 G-08 — a server restart left the turn card on "Working" for
 * 300 s: no turn_finished on the wire, and the system note nowhere.
 * ------------------------------------------------------------------ */
describe("chatState — interrupted turns and system notes", () => {
  it("ends the turn on an interrupted finish, with no reply to show", () => {
    const state = run([
      {
        type: "event",
        event: ev(1, "turn_started", {
          turn_id: "t4",
          message_id: "m1",
          content: "sleep 240",
        }),
      },
      {
        type: "event",
        event: ev(9, "turn_finished", {
          turn_id: "t4",
          finish_reason: "interrupted",
          n_calls: 1,
        }),
      },
    ]);
    const turn = state.turns.t4;
    expect(turn.finishedAt).toBe(9);
    expect(turn.finishReason).toBe("interrupted");
    expect(turnOf(state, "t4").replies).toEqual([]);
  });

  it("puts a system note in the turn it interrupted", () => {
    const state = run([
      {
        type: "event",
        event: ev(1, "turn_started", { turn_id: "t4", message_id: "m1" }),
      },
      {
        type: "event",
        event: ev(10, "system_note", {
          turn_id: "t4",
          message_id: "sys1",
          content: "Server restarted; turn interrupted",
        }),
      },
    ]);
    expect(state.messages[0]).toMatchObject({
      id: "sys1",
      role: "system",
      turn_id: "t4",
      content: "Server restarted; turn interrupted",
    });
    expect(turnOf(state, "t4").notes.map((m) => m.content)).toEqual([
      "Server restarted; turn interrupted",
    ]);
  });

  it("renders a note that belongs to no turn as a standalone line", () => {
    const state = run([
      {
        type: "event",
        event: ev(5, "system_note", { content: "Server restarted" }),
      },
    ]);
    expect(state.messages[0]).toMatchObject({ id: "ev-5", role: "system" });
    expect(buildGroups(state)[0].kind).toBe("note");
  });

  it("ignores an empty note rather than printing a blank line", () => {
    const state = run([
      { type: "event", event: ev(5, "system_note", { turn_id: "t1" }) },
    ]);
    expect(state.messages).toHaveLength(0);
  });

  it("writes a muted line when the sandbox is restarted underneath", () => {
    const state = run([
      {
        type: "event",
        event: ev(7, "lifecycle", { status: "sandbox_restarted" }),
      },
    ]);
    expect(state.messages[0]).toMatchObject({
      role: "system",
      content: "sandbox restarted",
    });
  });

  it("keeps every other lifecycle frame out of the thread", () => {
    for (const status of ["idle", "running", "indexing", "failed", "closed"]) {
      const state = run([
        { type: "event", event: ev(1, "lifecycle", { status }) },
      ]);
      expect(state.messages).toHaveLength(0);
    }
  });
});

/* HAR-84 G-19 — GT's routing hook emitted a tool_call with `command: ""`. */
describe("chatState — empty commands", () => {
  it("drops a tool_call frame carrying no command", () => {
    const state = run([
      { type: "event", event: ev(1, "tool_call", { turn_id: "t1" }) },
      {
        type: "event",
        event: ev(2, "tool_call", { turn_id: "t1", command: "ls" }),
      },
    ]);
    expect(state.turns.t1.items.map((i) => i.kind)).toEqual(["tool_call"]);
    expect(state.turns.t1.commands).toBe(1);
  });
});
