import { describe, expect, it } from "vitest";
import {
  agentIdOfRegistration,
  isExternalSession,
  isWorker,
  tokenOf,
  type Session,
} from "../api";
import { chatReducer, emptyChat, type ChatState } from "../chatState";
import {
  connectBlock,
  connectKind,
  connectOrigin,
  CONNECT_SECRET_NOTE,
  filesLine,
  foldFiles,
  MAX_AGENT_FILES,
  sanitizePath,
  sanitizePaths,
  sanitizeText,
} from "../external";
import { formatDuration, formatTokens } from "../format";
import { helpText, parseSlash, slashSuggestions } from "../slash";
import { EMPTY_INDEX, indexFiles } from "../trail";
import { stepFiles } from "../useGraphView";
import {
  agentElapsed,
  agentKindLabel,
  agentLabel,
  agentLines,
  agentRows,
  agentsText,
  agentState,
  fleetMark,
  NO_AGENTS,
  offersApply,
  rootLine,
  workerList,
} from "../workers";
import { ev, session } from "./helpers";

function run(
  actions: readonly Parameters<typeof chatReducer>[1][],
  from: ChatState = emptyChat,
): ChatState {
  return actions.reduce(chatReducer, from);
}

/** An `agent_spawned` frame for an agent we watch but do not run. */
function spawned(
  id: number,
  agentId: string,
  over: Record<string, unknown> = {},
) {
  return ev(id, "agent_spawned", {
    worker_id: agentId,
    external: true,
    agent_kind: "claude-code",
    label: "fix the flaky test",
    ...over,
  });
}

const FILES = [
  { path: "src/click/core.py", size: 900 },
  { path: "src/click/parser.py", size: 400 },
];

/* ------------------------------------------------------------------ *
 * One list
 * ------------------------------------------------------------------ */

describe("external agents — one list, not two", () => {
  it("folds an external agent into the agent list beside a worker", () => {
    const state = run([
      { type: "event", event: ev(1, "agent_spawned", { worker_id: "w1", task: "port the parser" }) },
      { type: "event", event: spawned(2, "x1") },
    ]);
    const agents = workerList(state.workers);
    expect(agents.map((a) => a.id)).toEqual(["w1", "x1"]);
    expect(agents[0].isExternal).toBe(false);
    expect(agents[1]).toMatchObject({
      isExternal: true,
      agentKind: "claude-code",
      label: "fix the flaky test",
    });
  });

  it("orders workers and external agents together by created_at", () => {
    /* The frames arrive in the wrong order on purpose: the external agent
       registered first but its row lands second. `created_at` decides. */
    const state = run([
      { type: "event", event: ev(1, "agent_spawned", { worker_id: "w1", task: "later" }) },
      { type: "event", event: spawned(2, "x1", { label: "earlier" }) },
      {
        type: "workers",
        rows: [
          session({ id: "w1", parent_id: "s1", role: "worker", created_at: 200 }),
          session({
            id: "x1",
            parent_id: "s1",
            role: "external",
            agent_kind: "claude-code",
            created_at: 100,
          }),
        ],
      },
    ]);
    expect(workerList(state.workers).map((a) => a.id)).toEqual(["x1", "w1"]);
    // The hue follows that same flat position, so the graph agrees.
    expect(agentRows(state.workers).map((row) => [row.worker.id, row.no])).toEqual([
      ["x1", 1],
      ["w1", 2],
    ]);
  });

  it("knows an external agent from a worker, whatever parent_id says", () => {
    const external = session({ id: "x1", parent_id: "s1", role: "external" });
    expect(isExternalSession(external)).toBe(true);
    // It has a parent, and it is still not a worker: it has no patch.
    expect(isWorker(external)).toBe(false);
    expect(isWorker(session({ id: "w1", parent_id: "s1", role: "worker" }))).toBe(
      true,
    );
  });
});

/* ------------------------------------------------------------------ *
 * Nesting
 * ------------------------------------------------------------------ */

describe("external agents — nesting, clamped at two levels", () => {
  const tree = [
    { type: "event" as const, event: spawned(1, "a", { label: "claude" }) },
    {
      type: "event" as const,
      event: spawned(2, "b", { label: "sub", parent_agent_id: "a" }),
    },
    {
      type: "event" as const,
      event: spawned(3, "c", { label: "sub of sub", parent_agent_id: "b" }),
    },
    { type: "event" as const, event: spawned(4, "d", { label: "another root" }) },
  ];

  it("draws a subagent under its parent, and never deeper than level 2", () => {
    const rows = agentRows(run(tree).workers);
    expect(rows.map((row) => [row.worker.id, row.depth])).toEqual([
      ["a", 0],
      ["b", 1],
      // A subagent of a subagent flattens onto level 2 rather than growing.
      ["c", 1],
      ["d", 0],
    ]);
  });

  it("marks the collapsed children of a subagent with (+N)", () => {
    const rows = agentRows(run(tree).workers);
    const by = new Map(rows.map((row) => [row.worker.id, row.collapsed]));
    // `a`'s child is drawn directly under it, so there is nothing to say.
    expect(by.get("a")).toBe(0);
    // `b`'s child had nowhere deeper to go, so `b` says it owns one row.
    expect(by.get("b")).toBe(1);
    expect(by.get("c")).toBe(0);
  });

  it("keeps an agent whose parent we never heard of at the top level", () => {
    const rows = agentRows(
      run([spawned(1, "orphan", { parent_agent_id: "ghost" })].map((event) => ({
        type: "event" as const,
        event,
      }))).workers,
    );
    expect(rows.map((row) => [row.worker.id, row.depth])).toEqual([["orphan", 0]]);
  });

  it("does not lose an agent to a cycle in parent_agent_id", () => {
    const rows = agentRows(
      run([
        { type: "event", event: spawned(1, "a", { parent_agent_id: "b" }) },
        { type: "event", event: spawned(2, "b", { parent_agent_id: "a" }) },
      ]).workers,
    );
    expect(rows.map((row) => row.worker.id).sort()).toEqual(["a", "b"]);
  });

  it("never lets an agent be its own parent", () => {
    const rows = agentRows(
      run([{ type: "event", event: spawned(1, "a", { parent_agent_id: "a" }) }])
        .workers,
    );
    expect(rows).toEqual([expect.objectContaining({ depth: 0 })]);
  });
});

/* ------------------------------------------------------------------ *
 * Where they work
 * ------------------------------------------------------------------ */

describe("external agents — the files they are in", () => {
  it("accumulates files most-recent-first, de-duplicated", () => {
    const state = run([
      { type: "event", event: spawned(1, "x1") },
      {
        type: "event",
        event: ev(2, "tool_call", {
          agent_id: "x1",
          tool_name: "Read",
          files: ["src/click/core.py", "src/click/parser.py"],
        }),
      },
      {
        type: "event",
        event: ev(3, "tool_result", {
          agent_id: "x1",
          tool_name: "Edit",
          files: ["src/click/core.py"],
          ok: true,
        }),
      },
    ]);
    const [agent] = workerList(state.workers);
    // core.py was touched last, so it leads; nothing appears twice.
    expect(agent.files).toEqual(["src/click/core.py", "src/click/parser.py"]);
  });

  it("caps the list rather than remembering a whole repository", () => {
    const many = Array.from({ length: MAX_AGENT_FILES + 15 }, (_, i) => `f${i}.py`);
    const folded = foldFiles([], many);
    expect(folded).toHaveLength(MAX_AGENT_FILES);
    // The most recent survives the cap; the oldest is what falls off.
    expect(folded[0]).toBe(`f${many.length - 1}.py`);
  });

  it("keeps the same array identity when nothing new arrived", () => {
    const files = ["a.py", "b.py"];
    expect(foldFiles(files, [])).toBe(files);
    /* Touching the same two files in the same order says nothing new. */
    expect(foldFiles(files, ["b.py", "a.py"])).toBe(files);
    expect(foldFiles(files, ["c.py"])).toEqual(["c.py", "a.py", "b.py"]);
  });

  it("takes a tool_name and no command as a step worth drawing", () => {
    const state = run([
      { type: "event", event: spawned(1, "x1") },
      {
        type: "event",
        event: ev(2, "tool_call", {
          agent_id: "x1",
          tool_name: "Read",
          files: ["src/click/core.py"],
        }),
      },
    ]);
    const [agent] = workerList(state.workers);
    expect(agent.activity).toHaveLength(1);
    expect(agent.activity[0].tool).toBe("Read");
    expect(agent.lastTool).toBe("Read");
  });

  it("reads ok: false as a failed step, and a missing ok as no opinion", () => {
    const state = run([
      { type: "event", event: spawned(1, "x1") },
      { type: "event", event: ev(2, "tool_result", { agent_id: "x1", tool_name: "Bash", ok: false }) },
      { type: "event", event: ev(3, "tool_result", { agent_id: "x1", tool_name: "Bash" }) },
    ]);
    const [agent] = workerList(state.workers);
    expect(agent.activity[0].isError).toBe(true);
    expect(agent.activity[1].isError).toBe(false);
  });

  it("names where it works in one line", () => {
    expect(filesLine(["a.py", "b.py", "c.py"], 2)).toBe("3 files · a.py, b.py …");
    expect(filesLine(["a.py"], 2)).toBe("1 file · a.py");
    expect(filesLine([])).toBe("");
  });
});

/* ------------------------------------------------------------------ *
 * The graph
 * ------------------------------------------------------------------ */

describe("external agents — on the graph", () => {
  const item = (files: string[]) => ({
    key: "k",
    command: "",
    output: "",
    returncode: null,
    isError: false,
    gt: null,
    tool: "Read",
    files,
    answered: true,
  });

  const resolve = new Map([
    ["src/click/core.py", "src/click/core.py"],
    ["src/click/parser.py", "dir:src/click"],
  ]);

  it("lights up the particles it named", () => {
    expect(
      stepFiles(true, item(["src/click/core.py"]), EMPTY_INDEX, resolve),
    ).toEqual(["src/click/core.py"]);
  });

  it("ignores a file this graph has never heard of, silently", () => {
    expect(
      stepFiles(
        true,
        item(["somewhere/else.py", "src/click/core.py"]),
        EMPTY_INDEX,
        resolve,
      ),
    ).toEqual(["src/click/core.py"]);
    // A step that named nothing we know contributes nothing at all.
    expect(stepFiles(true, item(["nope.py"]), EMPTY_INDEX, resolve)).toEqual([]);
  });

  it("still resolves a worker's command against the tree", () => {
    expect(
      stepFiles(
        false,
        { ...item([]), command: "cat src/click/core.py" },
        indexFiles(FILES),
        resolve,
      ),
    ).toEqual(["src/click/core.py"]);
  });
});

/* ------------------------------------------------------------------ *
 * No patch, therefore no apply
 * ------------------------------------------------------------------ */

describe("external agents — no patch", () => {
  it("never offers [apply] for an external agent, however it reports", () => {
    const state = run([
      { type: "event", event: spawned(1, "x1") },
      {
        type: "event",
        event: ev(2, "agent_report", {
          worker_id: "x1",
          reply_excerpt: "fixed the test",
          /* Even if a server sent these, an external agent has no patch to
             merge and the button must not appear. */
          files_changed: ["src/click/core.py"],
        }),
      },
    ]);
    const [agent] = workerList(state.workers);
    expect(agent.reply).toBe("fixed the test");
    expect(offersApply(agent)).toBe(false);
  });

  it("still offers [apply] for a worker that reported a patch", () => {
    const state = run([
      { type: "event", event: ev(1, "agent_spawned", { worker_id: "w1", task: "t" }) },
      {
        type: "event",
        event: ev(2, "agent_report", {
          worker_id: "w1",
          content: "done",
          files_changed: ["src/click/core.py"],
        }),
      },
    ]);
    expect(offersApply(workerList(state.workers)[0])).toBe(true);
  });

  it("says idle, working and done for an agent whose turns are not ours", () => {
    const state = run([{ type: "event", event: spawned(1, "x1") }]);
    const [agent] = workerList(state.workers);
    expect(agentState(agent)).toBe("working");
    expect(agentState({ ...agent, status: "reported" })).toBe("idle");
    expect(agentState({ ...agent, status: "closed" })).toBe("done");
    // A worker of ours keeps the words it has always had.
    expect(agentState({ ...agent, isExternal: false, status: "reported" })).toBe(
      "reported",
    );
  });

  it("marks a running agent differently from an idle and a finished one", () => {
    expect(fleetMark("working")).toBe("●");
    expect(fleetMark("running")).toBe("●");
    expect(fleetMark("idle")).not.toBe(fleetMark("working"));
    expect(fleetMark("done")).not.toBe(fleetMark("idle"));
    expect(fleetMark("done")).not.toBe(fleetMark("working"));
  });
});

/* ------------------------------------------------------------------ *
 * The fleet line
 * ------------------------------------------------------------------ */

describe("the fleet line", () => {
  it("formats a token count the way the column reads it", () => {
    expect(formatTokens(169_100)).toBe("169.1k");
    expect(formatTokens(205_800)).toBe("205.8k");
    expect(formatTokens(169_000)).toBe("169k");
    expect(formatTokens(2_400_000)).toBe("2.4M");
    expect(formatTokens(847)).toBe("847");
  });

  it("prints nothing for a count nobody reported — null is not zero", () => {
    expect(formatTokens(null)).toBe("");
    expect(formatTokens(undefined)).toBe("");
    expect(formatTokens(Number.NaN)).toBe("");
    // Zero is a real answer and prints as one.
    expect(formatTokens(0)).toBe("0");
  });

  it("formats elapsed as the fleet list writes it", () => {
    expect(formatDuration(1118)).toBe("18m 38s");
    expect(formatDuration(7)).toBe("7s");
    expect(formatDuration(3725)).toBe("1h 02m");
  });

  it("keeps the live activity and tokens current from the stream", () => {
    const state = run([
      { type: "event", event: spawned(1, "x1") },
      {
        type: "event",
        event: ev(2, "tool_call", {
          agent_id: "x1",
          tool_name: "Edit",
          activity: "Wiring worker turn slots in spawn_agents",
          tokens: 169_100,
        }),
      },
      /* A frame type this build does not model still carries the fleet
         line: the fields are read off `data`, not off the switch. */
      {
        type: "event",
        event: ev(3, "status", { agent_id: "x1", tokens: 205_800 }),
      },
    ]);
    const [agent] = workerList(state.workers);
    expect(agent.doing).toBe("Wiring worker turn slots in spawn_agents");
    expect(agent.tokens).toBe(205_800);
  });

  it("never lets a token count go backwards on a client restart", () => {
    const state = run([
      { type: "event", event: spawned(1, "x1") },
      { type: "event", event: ev(2, "tool_call", { agent_id: "x1", tokens: 900 }) },
      { type: "event", event: ev(3, "tool_call", { agent_id: "x1", tokens: 5 }) },
    ]);
    expect(workerList(state.workers)[0].tokens).toBe(900);
  });

  it("times a running agent to now and a finished one to its last word", () => {
    const state = run([
      { type: "event", event: spawned(1, "x1") },
      {
        type: "workers",
        rows: [
          session({
            id: "x1",
            role: "external",
            created_at: 1000,
            updated_at: 1300,
          }),
        ],
      },
    ]);
    const [agent] = workerList(state.workers);
    expect(agentElapsed(agent, 1500)).toBe(500);
    expect(agentElapsed({ ...agent, status: "closed" }, 1500)).toBe(300);
    expect(agentElapsed({ ...agent, createdAt: null }, 1500)).toBeNull();
  });

  it("reduces the session itself to the root of the tree", () => {
    const root = rootLine(
      session({
        id: "s1",
        status: "running",
        created_at: 1000,
        updated_at: 1100,
        steps: 12,
        activity: "answering the question",
        tokens: 402_700,
      }),
      1200,
    );
    expect(root).toMatchObject({
      label: "main",
      state: "working",
      doing: "answering the question",
      elapsed: 200,
      tokens: 402_700,
      steps: 12,
    });
    expect(rootLine(null, 0)).toBeNull();
    // An older server sends neither field, and the row still draws.
    expect(rootLine(session({ id: "s1" }), 0)).toMatchObject({
      doing: "",
      tokens: null,
    });
  });
});

/* ------------------------------------------------------------------ *
 * /agents
 * ------------------------------------------------------------------ */

describe("/agents", () => {
  it("is a command, with help in the same voice as the others", () => {
    expect(parseSlash("/agents")?.command.name).toBe("agents");
    expect(helpText()).toContain("/agents");
    expect(slashSuggestions("/ag").map((c) => c.name)).toEqual(["agents"]);
  });

  it("lists every agent, nested, with its kind, state, steps and file", () => {
    const state = run([
      { type: "event", event: ev(1, "agent_spawned", { worker_id: "w1", task: "port the parser" }) },
      { type: "event", event: spawned(2, "x1") },
      {
        type: "event",
        event: spawned(3, "x2", { label: "run the suite", agent_kind: "codex", parent_agent_id: "x1" }),
      },
      {
        type: "event",
        event: ev(4, "tool_call", {
          agent_id: "x1",
          tool_name: "Edit",
          files: ["src/click/core.py"],
          activity: "Wiring worker turn slots",
          tokens: 169_100,
        }),
      },
      { type: "event", event: ev(5, "assistant", { agent_id: "x1", content: "…" }) },
    ]);

    const lines = agentLines(state.workers, 0);
    expect(lines.map((line) => [line.no, line.kind, line.depth])).toEqual([
      [1, "worker-1", 0],
      [2, "claude-code", 0],
      [3, "codex", 1],
    ]);

    const claude = lines[1];
    expect(claude.state).toBe("working");
    expect(claude.steps).toBe(1);
    expect(claude.file).toBe("src/click/core.py");
    expect(claude.doing).toBe("Wiring worker turn slots");
    expect(claude.tokens).toBe(169_100);

    const text = agentsText(state.workers);
    expect(text).toContain("1. worker-1");
    expect(text).toContain("2. claude-code");
    expect(text).toContain("Wiring worker turn slots");
    expect(text).toContain("169.1k");
    // The subagent is indented under the agent it belongs to.
    expect(text.split("\n")[2].startsWith("  3. codex")).toBe(true);
  });

  it("says so plainly when there is nothing to list", () => {
    expect(agentsText(emptyChat.workers)).toBe(NO_AGENTS);
    expect(agentLines(emptyChat.workers, 0)).toEqual([]);
  });

  it("names an agent by its label, then its task, then its id", () => {
    const state = run([
      { type: "event", event: spawned(1, "0123456789abcdef", { label: "", task: "" }) },
    ]);
    const [agent] = workerList(state.workers);
    expect(agentLabel(agent)).toBe("01234567");
    expect(agentLabel({ ...agent, task: "a task" })).toBe("a task");
    expect(agentLabel({ ...agent, task: "a task", label: "a label" })).toBe(
      "a label",
    );
    expect(agentKindLabel({ ...agent, agentKind: null }, 2)).toBe("external");
    expect(agentKindLabel({ ...agent, isExternal: false }, 2)).toBe("worker-2");
  });
});

/* ------------------------------------------------------------------ *
 * /connect
 * ------------------------------------------------------------------ */

describe("/connect", () => {
  const target = {
    origin: "https://gt.example.com",
    ingestUrl: "https://gt.example.com/api/external-agents/a-9/events",
    agentId: "a-9",
    token: "sk-live-abcdef",
    kind: "claude-code",
  };

  it("is a command that takes a kind", () => {
    expect(parseSlash("/connect codex")?.arg).toBe("codex");
    expect(helpText()).toContain("/connect");
    expect(connectKind("")).toBe("claude-code");
    expect(connectKind("codex")).toBe("codex");
    expect(connectKind("Claude Code")).toBe("claude-code");
    expect(connectKind("vim")).toBeNull();
  });

  it("exports the three variables the adapters actually read", () => {
    const block = connectBlock(target);
    expect(block.exports.split("\n")).toEqual([
      "export GT_CLOUD_ORIGIN='https://gt.example.com'",
      "export GT_CLOUD_AGENT_ID='a-9'",
      "export GT_CLOUD_AGENT_TOKEN='sk-live-abcdef'",
    ]);
    /* The names the bridge does not read, and the binary that never
       existed, must not come back. */
    expect(block.exports).not.toContain("GT_INGEST");
    expect(JSON.stringify(block)).not.toContain("gt-connect");
  });

  it("never prints the user's own JWT variable", () => {
    /* `GT_CLOUD_TOKEN` is the user's session JWT. The browser cannot read
       it and this block must never name it: the agent token is the right
       secret to hand out. */
    const block = connectBlock(target);
    expect(block.exports).toContain("GT_CLOUD_AGENT_TOKEN=");
    expect(block.exports).not.toContain("export GT_CLOUD_TOKEN=");
    expect(CONNECT_SECRET_NOTE).not.toContain("export GT_CLOUD_TOKEN");
  });

  it("gives Claude Code the hook, because there is no command to run", () => {
    const block = connectBlock({ ...target, kind: "claude-code" });
    expect(block.stepCommand).toBe("");
    expect(block.step).toContain(
      "cloud/adapters/claude_code/settings.snippet.json",
    );
    expect(block.step).toContain("cloud/adapters/claude_code/gt_cloud_hook.py");
    expect(block.docs).toBe("docs/cloud/external-agents.md");
  });

  it("gives Codex the tailer it actually runs", () => {
    const block = connectBlock({ ...target, kind: "codex" });
    expect(block.stepCommand).toBe(
      "python cloud/adapters/codex/gt_cloud_codex.py",
    );
    expect(block.docs).toBe("docs/cloud/external-agents.md");
  });

  it("points a kind we have no adapter for at the documentation", () => {
    const block = connectBlock({ ...target, kind: "other" });
    expect(block.stepCommand).toBe("");
    expect(block.step).toContain("docs/cloud/external-agents.md");
  });

  it("puts the token in the exports and nowhere else", () => {
    const block = connectBlock(target);
    expect(block.exports.split("sk-live-abcdef")).toHaveLength(2);
    // Every other string this flow renders is token-free by construction.
    expect(block.step).not.toContain(target.token);
    expect(block.stepCommand).not.toContain(target.token);
    expect(block.docs).not.toContain(target.token);
    expect(CONNECT_SECRET_NOTE).not.toContain(target.token);
    expect(CONNECT_SECRET_NOTE).toContain("secret");
    expect(helpText()).not.toContain(target.token);
    expect(agentsText(emptyChat.workers)).not.toContain(target.token);
  });

  it("cannot be broken out of by a token or an origin with a quote in it", () => {
    const block = connectBlock({ ...target, token: "a'; rm -rf /; echo '" });
    expect(block.exports).toContain("export GT_CLOUD_AGENT_TOKEN='a; rm -rf /; echo '");
    // Every line closes its quote: nothing added an odd one.
    expect((block.exports.match(/'/g) ?? []).length % 2).toBe(0);
  });

  it("reads the token and the agent id however the server spelled them", () => {
    expect(tokenOf({ token: "a" })).toBe("a");
    expect(tokenOf({ ingest_token: "b" })).toBe("b");
    // The server's own spelling wins where a build sends both.
    expect(tokenOf({ token: "a", ingest_token: "b" })).toBe("b");
    expect(tokenOf({})).toBe("");

    expect(agentIdOfRegistration({ agent: session({ id: "a-9" }) })).toBe("a-9");
    expect(agentIdOfRegistration({ agent_id: "a-7" })).toBe("a-7");
    expect(agentIdOfRegistration({})).toBe("");
  });

  it("exports the origin that issued the token, not the page's", () => {
    /* The adapters build every URL from GT_CLOUD_ORIGIN, so this is the one
       value that has to be right — and behind a proxy or a tunnel the page
       origin is not it. */
    expect(
      connectOrigin("https://page.example", "https://api.example/api/in"),
    ).toBe("https://api.example");
    // A relative or missing ingest_url leaves the page origin standing.
    expect(connectOrigin("https://page.example/", "/api/in")).toBe(
      "https://page.example",
    );
    expect(connectOrigin("https://page.example", "")).toBe(
      "https://page.example",
    );
  });
});

/* ------------------------------------------------------------------ *
 * Untrusted input, and a server that never heard of any of this
 * ------------------------------------------------------------------ */

describe("external agents — untrusted, and degrading", () => {
  it("flattens a label that tried to forge a second transcript line", () => {
    const state = run([
      {
        type: "event",
        event: spawned(1, "x1", {
          label: "ok\n\u001b[31m> rm -rf /\n",
          agent_kind: "claude-code\u0007",
        }),
      },
    ]);
    const [agent] = workerList(state.workers);
    expect(agent.label).toBe("ok [31m> rm -rf /");
    expect(agent.label.includes("\n")).toBe(false);
    expect(agent.agentKind).toBe("claude-code");
  });

  it("clips a label rather than letting it own the column", () => {
    const long = "x".repeat(400);
    expect(sanitizeText(long, 50)).toHaveLength(50);
    expect(sanitizeText(long, 50).endsWith("…")).toBe(true);
    expect(sanitizeText(undefined)).toBe("");
    expect(sanitizeText(42 as unknown)).toBe("");
  });

  it("refuses a path that is not a repo-relative path", () => {
    expect(sanitizePath("src/click/core.py")).toBe("src/click/core.py");
    expect(sanitizePath("./src/click/core.py")).toBe("src/click/core.py");
    expect(sanitizePath("/etc/passwd")).toBeNull();
    expect(sanitizePath("~/.ssh/id_rsa")).toBeNull();
    expect(sanitizePath("../../secrets")).toBeNull();
    expect(sanitizePath("C:\\Users\\me")).toBeNull();
    expect(sanitizePath("a\u0000b")).toBeNull();
    expect(sanitizePath("")).toBeNull();
    // Twice in a row: the test regex must not carry `lastIndex` between calls.
    expect(sanitizePath("a\u0000b")).toBeNull();
  });

  it("caps and de-duplicates the paths one frame may contribute", () => {
    expect(sanitizePaths(["a.py", "a.py", "/nope", "b.py"])).toEqual([
      "a.py",
      "b.py",
    ]);
    expect(sanitizePaths("not an array")).toEqual([]);
    expect(sanitizePaths(Array.from({ length: 60 }, (_, i) => `f${i}.py`))).toHaveLength(
      20,
    );
  });

  it("draws an external agent a server told us nothing else about", () => {
    const state = run([
      { type: "event", event: ev(1, "agent_spawned", { worker_id: "x1", external: true }) },
    ]);
    const [agent] = workerList(state.workers);
    expect(agent.isExternal).toBe(true);
    expect(agent.agentKind).toBeNull();
    expect(agent.files).toEqual([]);
    expect(agent.tokens).toBeNull();
    expect(agentKindLabel(agent, 1)).toBe("external");
    expect(agentsText(state.workers)).toContain("1. external");
  });

  it("treats a row with no external fields as the worker it has always been", () => {
    const rows: Session[] = [
      session({ id: "w1", parent_id: "s1", role: "worker", task: "t" }),
    ];
    const state = run([{ type: "workers", rows }]);
    const [agent] = workerList(state.workers);
    expect(agent.isExternal).toBe(false);
    expect(agent.doing).toBe("");
    expect(agent.parentAgentId).toBeNull();
    expect(agentRows(state.workers)[0].depth).toBe(0);
  });

  it("rebuilds an external agent from GET /agents alone", () => {
    const state = run([
      {
        type: "workers",
        rows: [
          session({
            id: "x1",
            parent_id: "s1",
            role: "external",
            agent_kind: "codex",
            external_cwd: "/home/me/click",
            task: "fix the flaky test",
            activity: "Reading src/click/core.py",
            tokens: 12_400,
            created_at: 100,
            updated_at: 400,
          }),
        ],
      },
    ]);
    const [agent] = workerList(state.workers);
    expect(agent).toMatchObject({
      isExternal: true,
      agentKind: "codex",
      externalCwd: "/home/me/click",
      task: "fix the flaky test",
      doing: "Reading src/click/core.py",
      tokens: 12_400,
      createdAt: 100,
      updatedAt: 400,
    });
    expect(offersApply(agent)).toBe(false);
  });
});
