import { describe, expect, it } from "vitest";
import { agentIdOf, ApiError, conflictsOf, isWorker, type Session } from "../api";
import {
  buildGroups,
  chatReducer,
  emptyChat,
  type ChatState,
} from "../chatState";
import { createAndStart, rejectsFirstMessage } from "../launch";
import { parseSlash, parseSpawn, slashSuggestions } from "../slash";
import { buildSteps, callCount, indexFiles } from "../trail";
import {
  hueFor,
  nestSessions,
  shortId,
  workerCalls,
  workerList,
  workerNo,
  WORKER_HUES,
} from "../workers";
import { ev, msg, session } from "./helpers";

function run(
  actions: readonly Parameters<typeof chatReducer>[1][],
  from: ChatState = emptyChat,
): ChatState {
  return actions.reduce(chatReducer, from);
}

const FILES = [
  { path: "src/click/core.py", size: 900 },
  { path: "src/click/termui.py", size: 400 },
];

/* ------------------------------------------------------------------ *
 * The frame protocol
 * ------------------------------------------------------------------ */

describe("workers — agent_id is the whole protocol", () => {
  it("reads agent_id off a mirrored frame and nothing off a primary one", () => {
    expect(agentIdOf(ev(1, "assistant", { turn_id: "t1", agent_id: "w1" }))).toBe(
      "w1",
    );
    expect(agentIdOf(ev(2, "assistant", { turn_id: "t1" }))).toBeNull();
    // A null (rather than absent) agent_id is still not a worker's frame.
    expect(agentIdOf(ev(3, "assistant", { agent_id: null }))).toBeNull();
  });

  it("keeps a worker's frames out of the primary turn", () => {
    const state = run([
      { type: "event", event: ev(1, "turn_started", { turn_id: "t1", message_id: "m1", content: "go" }) },
      { type: "event", event: ev(2, "assistant", { turn_id: "t1", content: "mine", actions: ["ls"] }) },
      { type: "event", event: ev(3, "tool_call", { turn_id: "t1", command: "ls" }) },
      // Everything below belongs to the worker, on the same stream.
      { type: "event", event: ev(4, "turn_started", { turn_id: "wt1", agent_id: "w1" }) },
      { type: "event", event: ev(5, "assistant", { turn_id: "wt1", content: "theirs", agent_id: "w1" }) },
      { type: "event", event: ev(6, "assistant", { turn_id: "wt1", content: "theirs 2", agent_id: "w1" }) },
      { type: "event", event: ev(7, "tool_call", { turn_id: "wt1", command: "pytest", agent_id: "w1" }) },
      { type: "event", event: ev(8, "tool_result", { turn_id: "wt1", command: "pytest", output: "ok", returncode: 0, agent_id: "w1" }) },
    ]);

    // The primary turn has exactly its own two items and its own command.
    expect(Object.keys(state.turns)).toEqual(["t1"]);
    expect(state.turns.t1.items).toHaveLength(2);
    expect(state.turns.t1.commands).toBe(1);

    // And the worker's frames are all on the worker.
    const [worker] = workerList(state.workers);
    expect(worker.id).toBe("w1");
    expect(worker.calls).toBe(2);
    expect(worker.activity).toHaveLength(1);
    expect(worker.activity[0]).toMatchObject({
      command: "pytest",
      output: "ok",
      returncode: 0,
    });
  });

  it("never counts a mirrored assistant frame as a primary step", () => {
    const state = run([
      { type: "event", event: ev(1, "assistant", { turn_id: "t1", content: "one", actions: ["a"] }) },
      { type: "event", event: ev(2, "assistant", { turn_id: "t1", content: "two", actions: ["b"] }) },
      { type: "event", event: ev(3, "assistant", { turn_id: "wt", content: "not mine", agent_id: "w1" }) },
      { type: "event", event: ev(4, "assistant", { turn_id: "wt", content: "nor this", agent_id: "w1" }) },
    ]);
    const steps = buildSteps(state.turns.t1, indexFiles(FILES));
    expect(callCount(steps, state.turns.t1.nCalls)).toBe(2);
  });

  it("never lets a worker's command resolve a file into the primary trail", () => {
    const state = run([
      { type: "event", event: ev(1, "tool_call", { turn_id: "t1", command: "cat src/click/termui.py" }) },
      { type: "event", event: ev(2, "tool_call", { turn_id: "t1", command: "cat src/click/core.py", agent_id: "w1" }) },
    ]);
    const steps = buildSteps(state.turns.t1, indexFiles(FILES));
    expect(steps.flatMap((step) => step.files)).toEqual(["src/click/termui.py"]);
  });

  it("keeps a worker's turn_finished off the primary turn", () => {
    const state = run([
      { type: "event", event: ev(1, "turn_finished", { turn_id: "wt", n_calls: 42, agent_id: "w1" }) },
    ]);
    expect(state.turns).toEqual({});
    expect(workerCalls(workerList(state.workers)[0])).toBe(42);
  });
});

/* ------------------------------------------------------------------ *
 * The card, live
 * ------------------------------------------------------------------ */

describe("workers — the card", () => {
  it("opens on agent_spawned with the task and runs", () => {
    const state = run([
      { type: "event", event: ev(1, "agent_spawned", { worker_id: "w1", task: "write the tests" }) },
    ]);
    const [worker] = workerList(state.workers);
    expect(worker).toMatchObject({ id: "w1", task: "write the tests", status: "running" });
  });

  it("takes the whole reply, the files and the sha from agent_report", () => {
    const state = run([
      { type: "event", event: ev(1, "agent_spawned", { worker_id: "w1", task: "t" }) },
      {
        type: "event",
        event: ev(2, "agent_report", {
          worker_id: "w1",
          message_id: "m9",
          finish_reason: "reply",
          content: "added the docstring",
          patch_sha256: "abcdef0123456789",
          files_changed: ["src/click/core.py"],
          n_calls: 7,
        }),
      },
    ]);
    const [worker] = workerList(state.workers);
    expect(worker.status).toBe("reported");
    expect(worker.reply).toBe("added the docstring");
    expect(worker.filesChanged).toEqual(["src/click/core.py"]);
    expect(worker.patchSha).toBe("abcdef0123456789");
    expect(workerCalls(worker)).toBe(7);
  });

  it("moves to applied, then to closed, and closed is terminal", () => {
    const state = run([
      { type: "event", event: ev(1, "agent_spawned", { worker_id: "w1", task: "t" }) },
      { type: "event", event: ev(2, "agent_applied", { worker_id: "w1", files: ["a.py", "b.py"] }) },
      { type: "event", event: ev(3, "agent_closed", { worker_id: "w1", reason: "user" }) },
      { type: "event", event: ev(4, "agent_report", { worker_id: "w1", content: "late" }) },
    ]);
    const [worker] = workerList(state.workers);
    expect(worker.appliedFiles).toEqual(["a.py", "b.py"]);
    expect(worker.status).toBe("closed");
    expect(worker.closedReason).toBe("user");
  });

  it("puts a 409's conflict paths on the card and leaves it un-applied", () => {
    const state = run([
      { type: "event", event: ev(1, "agent_spawned", { worker_id: "w1", task: "t" }) },
      { type: "worker_applying", workerId: "w1" },
      {
        type: "worker_conflict",
        workerId: "w1",
        conflicts: ["src/click/core.py"],
        detail: "patch does not apply",
      },
    ]);
    const [worker] = workerList(state.workers);
    expect(worker.conflicts).toEqual(["src/click/core.py"]);
    expect(worker.applying).toBe(false);
    expect(worker.appliedFiles).toBeNull();
    expect(worker.status).toBe("running");
  });
});

/* ------------------------------------------------------------------ *
 * Reload
 * ------------------------------------------------------------------ */

describe("workers — reconstructed after a reload", () => {
  const rows: Session[] = [
    session({
      id: "w1",
      parent_id: "s1",
      role: "worker",
      task: "docstring for Command.invoke",
      status: "idle",
      report: {
        finish_reason: "reply",
        reply_excerpt: "added the doc…",
        patch_sha256: "deadbeefcafe",
        files_changed: ["src/click/core.py"],
        applied: false,
      },
    }),
    session({
      id: "w2",
      parent_id: "s1",
      role: "worker",
      task: "docstring for Group.add_command",
      status: "idle",
      applied_at: 1700,
      report: {
        finish_reason: "reply",
        reply_excerpt: "done",
        patch_sha256: "0011223344",
        files_changed: ["src/click/core.py"],
        applied: true,
      },
    }),
  ];

  it("rebuilds both cards from GET /agents alone", () => {
    const state = run([{ type: "workers", rows }]);
    const workers = workerList(state.workers);
    expect(workers.map((w) => w.id)).toEqual(["w1", "w2"]);
    expect(workers[0]).toMatchObject({
      task: "docstring for Command.invoke",
      status: "reported",
      reply: "added the doc…",
      patchSha: "deadbeefcafe",
    });
    expect(workers[1].status).toBe("applied");
    expect(workers[1].appliedFiles).toEqual(["src/click/core.py"]);
  });

  it("prefers the whole report message over the row's bounded excerpt", () => {
    const state = run([
      { type: "workers", rows },
      {
        type: "hydrate",
        messages: [
          msg({
            id: "m1",
            role: "agent",
            content: "added the docstring, and here is the whole story",
            meta: {
              agent_id: "w1",
              finish_reason: "reply",
              files_changed: ["src/click/core.py"],
              patch_sha256: "deadbeefcafe",
            },
          }),
        ],
      },
    ]);
    expect(workerList(state.workers)[0].reply).toBe(
      "added the docstring, and here is the whole story",
    );
  });

  it("keeps a worker's report out of the parent's thread", () => {
    const state = run([
      {
        type: "hydrate",
        messages: [
          msg({ id: "m0", role: "user", content: "do the thing", turn_id: "t1" }),
          msg({ id: "m1", role: "agent", content: "worker says", meta: { agent_id: "w1" } }),
          msg({ id: "m2", role: "agent", content: "I say", turn_id: "t1" }),
        ],
      },
    ]);
    expect(state.messages.map((m) => m.id)).toEqual(["m0", "m2"]);
    expect(buildGroups(state)).toHaveLength(1);
    expect(workerList(state.workers)[0].reply).toBe("worker says");
  });

  /* ---------------------------------------------------------------- *
   * HAR-84 P1-4. Live, worker-1 was the one spawned first. After a reload
   * the labels — and the graph hues that follow the same index — swapped,
   * because the report messages hydrate before the `/agents` rows and the
   * number was the order of first touch.
   * ---------------------------------------------------------------- */

  const spawnedFirst = session({
    id: "w-old",
    parent_id: "s1",
    role: "worker",
    task: "31 steps of work",
    status: "idle",
    created_at: 1_700_000_232.6488,
  });
  const spawnedSecond = session({
    id: "w-new",
    parent_id: "s1",
    role: "worker",
    task: "4 steps of work",
    status: "idle",
    created_at: 1_700_000_232.6617,
  });

  /** The reload order the QA run hit: the fast worker reported first. */
  const reportsFirst = [
    {
      type: "hydrate" as const,
      messages: [
        msg({ id: "m2", role: "agent", content: "done", meta: { agent_id: "w-new" } }),
        msg({ id: "m1", role: "agent", content: "done", meta: { agent_id: "w-old" } }),
      ],
    },
    { type: "workers" as const, rows: [spawnedFirst, spawnedSecond] },
  ];

  it("numbers workers by created_at however the records arrive", () => {
    const reloaded = run(reportsFirst);
    expect(workerList(reloaded.workers).map((w) => w.id)).toEqual([
      "w-old",
      "w-new",
    ]);
    expect(workerNo(reloaded.workers, "w-old")).toBe(1);
    expect(workerNo(reloaded.workers, "w-new")).toBe(2);
  });

  it("gives a reload the same numbers and hues the live page had", () => {
    /* Live: the frames arrive in spawn order and there are no rows yet. */
    const live = run([
      { type: "event", event: ev(1, "agent_spawned", { worker_id: "w-old", task: "a" }) },
      { type: "event", event: ev(2, "agent_spawned", { worker_id: "w-new", task: "b" }) },
      { type: "event", event: ev(3, "agent_report", { worker_id: "w-new", content: "done" }) },
    ]);
    const reloaded = run(reportsFirst);

    const ids = (state: ChatState) => workerList(state.workers).map((w) => w.id);
    expect(ids(reloaded)).toEqual(ids(live));
    expect(ids(live)).toEqual(["w-old", "w-new"]);
    // The hue is picked by position in that list, so it follows.
    expect(hueFor(ids(reloaded).indexOf("w-old"))).toEqual(
      hueFor(ids(live).indexOf("w-old")),
    );
  });

  it("puts a worker spawned after a reload last, not first", () => {
    const state = run([
      ...reportsFirst,
      { type: "event", event: ev(9, "agent_spawned", { worker_id: "w-third", task: "c" }) },
    ]);
    expect(workerList(state.workers).map((w) => w.id)).toEqual([
      "w-old",
      "w-new",
      "w-third",
    ]);
    expect(workerNo(state.workers, "w-third")).toBe(3);
  });

  it("does not resurrect a closed worker from its row", () => {
    const state = run([
      {
        type: "workers",
        rows: [
          session({ id: "w1", parent_id: "s1", role: "worker", task: "t", status: "closed", closed_reason: "user" }),
        ],
      },
    ]);
    expect(workerList(state.workers)[0].status).toBe("closed");
  });
});

/* ------------------------------------------------------------------ *
 * Errors, colours, nesting
 * ------------------------------------------------------------------ */

describe("workers — the rest of it", () => {
  it("lifts conflicts out of a 409 body", () => {
    expect(
      conflictsOf('{"detail":"nope","conflicts":["a.py","b.py"]}'),
    ).toEqual(["a.py", "b.py"]);
    expect(conflictsOf('{"detail":"nope"}')).toEqual([]);
    expect(conflictsOf("not json")).toEqual([]);
  });

  it("carries the conflicts on the error the UI catches", () => {
    const err = new ApiError(409, "nope", ["a.py"]);
    expect(err.conflicts).toEqual(["a.py"]);
    expect(new ApiError(429, "cap").conflicts).toEqual([]);
  });

  it("gives every worker a colour that is neither the orange nor the teal", () => {
    const used = new Set(WORKER_HUES.map((hue) => hue.rgb));
    expect(used.size).toBe(WORKER_HUES.length);
    expect(used.has("240, 102, 47")).toBe(false);
    expect(used.has("21, 154, 135")).toBe(false);
    // Past the fourth worker the palette wraps rather than running out.
    expect(hueFor(4)).toEqual(hueFor(0));
  });

  it("shortens an id enough to tell two workers apart", () => {
    expect(shortId("0123456789abcdef")).toBe("01234567");
  });

  it("nests workers under the session that spawned them", () => {
    const rows = nestSessions([
      session({ id: "s1" }),
      session({ id: "w1", parent_id: "s1", role: "worker", task: "one" }),
      session({ id: "s2" }),
      session({ id: "w2", parent_id: "s1", role: "worker", task: "two" }),
    ]);
    expect(rows.map((row) => [row.session.id, row.depth])).toEqual([
      ["s1", 0],
      ["w1", 1],
      ["w2", 1],
      ["s2", 0],
    ]);
  });

  it("keeps an orphaned worker visible rather than dropping it", () => {
    const rows = nestSessions([
      session({ id: "w1", parent_id: "gone", role: "worker", task: "one" }),
    ]);
    expect(rows).toEqual([{ session: rows[0].session, depth: 0 }]);
  });

  it("knows a worker from a session someone started", () => {
    expect(isWorker(session({ id: "w1", role: "worker", parent_id: "s1" }))).toBe(true);
    expect(isWorker(session({ id: "s1" }))).toBe(false);
  });
});

/* ------------------------------------------------------------------ *
 * /spawn
 * ------------------------------------------------------------------ */

describe("/spawn", () => {
  it("takes one task per line, in order", () => {
    expect(parseSpawn("/spawn port the parser\n/spawn write the tests")).toEqual({
      tasks: ["port the parser", "write the tests"],
      error: null,
    });
  });

  it("ignores blank lines between the tasks", () => {
    expect(parseSpawn("/spawn a\n\n  \n/spawn b").tasks).toEqual(["a", "b"]);
  });

  it("refuses a message that is only partly /spawn lines", () => {
    const draft = parseSpawn("/spawn fix it\nand then deploy");
    expect(draft.tasks).toEqual([]);
    expect(draft.error).toContain("and then deploy");
  });

  it("refuses a bare /spawn and a fifth task", () => {
    expect(parseSpawn("/spawn").error).toContain("needs a task");
    expect(parseSpawn("/spawn a\n/spawn b\n/spawn c\n/spawn d\n/spawn e").error)
      .toContain("At most 4");
  });

  it("carries the whole draft on the parse, so multi-line /spawn survives", () => {
    const parsed = parseSlash("/spawn a\n/spawn b");
    expect(parsed?.command.name).toBe("spawn");
    expect(parsed?.raw).toBe("/spawn a\n/spawn b");
    expect(parseSpawn(parsed!.raw).tasks).toEqual(["a", "b"]);
  });

  it("completes the second /spawn line as readily as the first", () => {
    expect(slashSuggestions("/spawn a\n/sp").map((c) => c.name)).toEqual(["spawn"]);
    expect(slashSuggestions("/spawn a\n/spawn b ")).toEqual([]);
  });
});

/* ------------------------------------------------------------------ *
 * Create and run, in one call
 * ------------------------------------------------------------------ */

describe("createAndStart", () => {
  const body = {
    repo: "https://github.com/pallets/click",
    ref: "main",
    model: "m",
    gt_mode: "advisory",
    step_limit: 60,
    temperature: 0,
  };

  it("creates with first_message and does not post the prompt itself", async () => {
    const seen: unknown[] = [];
    const started = await createAndStart(body, "add a docstring", async (b) => {
      seen.push(b);
      return session({ id: "s9" });
    });
    expect(started.sent).toBe(true);
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ first_message: "add a docstring" });
  });

  it("falls back to create-then-send only when first_message is what was refused", async () => {
    const seen: unknown[] = [];
    const started = await createAndStart(body, "hello", async (b) => {
      seen.push(b);
      if ("first_message" in (b as object)) {
        throw new ApiError(422, "first_message must not be blank");
      }
      return session({ id: "s9" });
    });
    expect(started.sent).toBe(false);
    expect(seen).toHaveLength(2);
  });

  it("never creates a second session over an unrelated 400", async () => {
    let calls = 0;
    await expect(
      createAndStart(body, "hello", async () => {
        calls += 1;
        throw new ApiError(400, "model not available: no such model");
      }),
    ).rejects.toThrow("model not available");
    expect(calls).toBe(1);
    expect(rejectsFirstMessage(new ApiError(400, "model not available"))).toBe(false);
  });
});
