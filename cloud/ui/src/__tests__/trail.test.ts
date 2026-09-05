import { describe, expect, it } from "vitest";
import type { ActivityItem, TurnState } from "../chatState";
import {
  attentionAlpha,
  buildSteps,
  callAt,
  callCount,
  callSteps,
  DECAY_STEPS,
  EMPTY_INDEX,
  indexFiles,
  matchFiles,
  stepKind,
  trailView,
  WRITES,
  type TrailStep,
} from "../trail";

const FILES = [
  { path: "hello.py", size: 52 },
  { path: "README", size: 13 },
  { path: "src/app/cli.py", size: 1840 },
  { path: "src/app/routes/auth.py", size: 2210 },
  { path: "tests/routes/auth.py", size: 900 },
];

const index = indexFiles(FILES);

/** A turn carrying exactly the activity items given. */
function turn(items: TurnState["items"]): TurnState {
  return {
    id: "t1",
    items,
    startedAt: 0,
    finishedAt: null,
    finishReason: null,
    nCalls: null,
    cost: null,
    commands: items.filter((i) => i.kind === "tool_call").length,
  };
}

const assistant = (
  key: string,
  content: string,
  isReply = false,
): ActivityItem => ({
  key,
  kind: "assistant",
  content,
  actions: [],
  isReply,
  nCalls: null,
  cost: null,
});

const call = (key: string, command: string): ActivityItem => ({
  key,
  kind: "tool_call",
  command,
  nCalls: null,
});

const result = (
  key: string,
  command: string,
  returncode = 0,
): ActivityItem => ({
  key,
  kind: "tool_result",
  command,
  output: "",
  returncode,
  isError: returncode !== 0,
});

describe("trail — the file index", () => {
  it("resolves an exact path", () => {
    expect(matchFiles("cat src/app/cli.py", index)).toEqual(["src/app/cli.py"]);
  });

  it("resolves an unambiguous basename", () => {
    expect(matchFiles("python hello.py", index)).toEqual(["hello.py"]);
  });

  it("drops a basename two files share", () => {
    // auth.py is both src/app/routes/auth.py and tests/routes/auth.py.
    expect(matchFiles("vim auth.py", index)).toEqual([]);
  });

  it("resolves a trailing sub-path that is unique", () => {
    expect(matchFiles("cat app/routes/auth.py", index)).toEqual([
      "src/app/routes/auth.py",
    ]);
  });

  it("stays quiet on a command with nothing to resolve", () => {
    expect(matchFiles("grep -r foo .", index)).toEqual([]);
    expect(matchFiles("ls", EMPTY_INDEX)).toEqual([]);
  });
});

describe("trail — building steps", () => {
  it("folds assistant, tool_call and tool_result into one step", () => {
    const steps = buildSteps(
      turn([
        assistant("ev-1", "THOUGHT: look around"),
        call("ev-2", "ls src/app/cli.py"),
        result("ev-3", "ls src/app/cli.py"),
      ]),
      index,
    );

    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({
      n: 1,
      eventId: 1,
      isCall: true,
      isReply: false,
      thought: "look around",
      command: "ls src/app/cli.py",
    });
    expect(steps[0].files).toEqual(["src/app/cli.py"]);
  });

  it("builds a step with no command at all", () => {
    const steps = buildSteps(
      turn([assistant("ev-1", "THOUGHT: I already know the answer")]),
      index,
    );
    expect(steps).toHaveLength(1);
    expect(steps[0].command).toBeNull();
    expect(steps[0].files).toEqual([]);
    expect(steps[0].isCall).toBe(true);
  });

  it("opens a fresh step for a second command in the same call", () => {
    const steps = buildSteps(
      turn([
        assistant("ev-1", "two commands"),
        call("ev-2", "ls"),
        call("ev-3", "cat README"),
      ]),
      index,
    );
    expect(steps).toHaveLength(2);
    // Only the first stands for a model call; the second is a continuation.
    expect(steps.map((s) => s.isCall)).toEqual([true, false]);
  });

  it("takes the command from tool_result when tool_call never arrived", () => {
    const steps = buildSteps(
      turn([result("ev-2", "python hello.py")]),
      index,
    );
    expect(steps[0].command).toBe("python hello.py");
    expect(steps[0].files).toEqual(["hello.py"]);
    expect(steps[0].isCall).toBe(false);
  });

  it("returns nothing for a turn that does not exist yet", () => {
    expect(buildSteps(undefined, index)).toEqual([]);
  });

  it("counts model calls, not trail rows, and defers to the server", () => {
    const steps = buildSteps(
      turn([
        assistant("ev-1", "one"),
        call("ev-2", "ls"),
        call("ev-3", "pwd"),
        assistant("ev-4", "two"),
      ]),
      index,
    );
    // Three trail rows: the second command is a continuation of the first
    // call, not a call of its own.
    expect(steps).toHaveLength(3);
    expect(steps.map((step) => step.isCall)).toEqual([true, false, true]);
    expect(callSteps(steps)).toHaveLength(2);
    expect(callCount(steps, null)).toBe(2);
    // Once the server has spoken, its n_calls is the count — it also counts
    // the final model call that produced the reply and emitted no frame.
    expect(callCount(steps, 3)).toBe(3);
    // Standing on trail row 2 is still standing inside the first model call.
    expect(callAt(steps, 2)).toBe(1);
    expect(callAt(steps, 3)).toBe(2);
    expect(callAt(steps, 0)).toBe(1);
  });
});

describe("trail — stepKind", () => {
  const step = (over: Partial<TrailStep> = {}): TrailStep => ({
    key: "ev-1",
    eventId: 1,
    n: 1,
    isCall: true,
    isReply: false,
    thought: "",
    actions: [],
    command: null,
    output: "",
    returncode: null,
    isError: false,
    files: [],
    steering: [],
    errors: [],
    ...over,
  });

  it("reads a plain command as a read", () => {
    expect(
      stepKind(step({ command: "cat hello.py", files: ["hello.py"] }), new Set()),
    ).toBe("read");
  });

  it("reads an errored step as an error whatever it ran", () => {
    expect(
      stepKind(
        step({
          command: "sed -i s/a/b/ hello.py",
          files: ["hello.py"],
          isError: true,
        }),
        new Set(["hello.py"]),
      ),
    ).toBe("error");
  });

  it("needs a write-shaped command AND a changed file to read as an edit", () => {
    const edited = new Set(["hello.py"]);
    expect(
      stepKind(
        step({ command: "sed -i s/a/b/ hello.py", files: ["hello.py"] }),
        edited,
      ),
    ).toBe("edit");
    // Write-shaped, but nothing in the diff came of it.
    expect(
      stepKind(
        step({ command: "touch scratch.txt", files: ["hello.py"] }),
        new Set(),
      ),
    ).toBe("read");
    // The file did change — but not because of this command.
    expect(
      stepKind(step({ command: "cat hello.py", files: ["hello.py"] }), edited),
    ).toBe("read");
    // No command at all is never an edit.
    expect(stepKind(step({ files: ["hello.py"] }), edited)).toBe("read");
  });
});

/* The regex below is compared literally against the server's
   `looks_like_write` by `tests/test_cloud_workspace.py`. These cases mirror
   that module's own parametrisation, so a divergence fails on both sides. */
describe("trail — WRITES mirrors the server's looks_like_write", () => {
  const writes = [
    "echo patched >> README.md",
    "echo brand-new > newfile.txt",
    "touch a.txt",
    "rm -rf build",
    "mkdir -p src/new",
    "sed -i 's/a/b/' f.py",
    "perl -pi -e 's/a/b/' f.py",
    "git apply /tmp/p.diff",
    "git checkout -- src",
    "apply_patch <<'EOF'",
    "python3 - <<'EOF'",
    `python3 -c "open('f.py', 'w').write(text)"`,
    `python -c "import pathlib; pathlib.Path('f').write_text('x')"`,
    "cat x.py | tee y.py",
    "cd src && mv a.py b.py",
  ];

  const reads = [
    "",
    "ls -la",
    "cat README.md",
    "grep -rn needle src",
    "python -m pytest -q",
    "git status",
    "git log --oneline -5",
    "git diff --stat",
    "make 2>&1",
  ];

  it.each(writes)("treats %j as a write", (command) => {
    expect(WRITES.test(command)).toBe(true);
  });

  it.each(reads)("treats %j as a read", (command) => {
    expect(WRITES.test(command)).toBe(false);
  });
});

describe("trail — attention and the trail itself", () => {
  const steps = buildSteps(
    turn([
      assistant("ev-1", "a"),
      call("ev-2", "cat hello.py"),
      assistant("ev-3", "b"),
      call("ev-4", "cat hello.py"),
      assistant("ev-5", "c"),
      call("ev-6", "cat src/app/cli.py"),
    ]),
    index,
  );

  it("counts visits and lands the agent on the last file it touched", () => {
    expect(steps).toHaveLength(3);
    const view = trailView(steps, steps.length);
    expect(view.attention.get("hello.py")).toEqual({ reads: 2, last: 2 });
    expect(view.position).toBe("src/app/cli.py");
    // The repeat at step 2 is not a second waypoint: nothing moved.
    expect(view.trail.map((w) => w.path)).toEqual([
      "hello.py",
      "src/app/cli.py",
    ]);
  });

  it("rewinds cleanly to a scrub cutoff", () => {
    const view = trailView(steps, 2);
    expect(view.upTo).toBe(2);
    expect(view.position).toBe("hello.py");
    expect(view.attention.has("src/app/cli.py")).toBe(false);
  });

  it("clamps a cutoff past the end and below zero", () => {
    expect(trailView(steps, 99).upTo).toBe(3);
    expect(trailView(steps, -3).upTo).toBe(0);
    expect(trailView(steps, -3).position).toBeNull();
  });

  it("fades attention over DECAY_STEPS and never below zero", () => {
    expect(attentionAlpha(4, 4)).toBe(1);
    expect(attentionAlpha(4, 4 + DECAY_STEPS)).toBe(0);
    expect(attentionAlpha(4, 99)).toBe(0);
    // A visit in the future has not happened yet.
    expect(attentionAlpha(9, 4)).toBe(0);
  });
});
