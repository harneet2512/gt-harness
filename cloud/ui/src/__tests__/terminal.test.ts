import { describe, expect, it } from "vitest";
import { chatReducer, emptyChat, type ChatState } from "../chatState";
import {
  actionLine,
  evidenceLine,
  fromCommand,
  fromFrame,
  GT_TOOL,
} from "../gt";
import { SLASH_COMMANDS, parseSlash, slashSuggestions } from "../slash";
import { isTheme, otherTheme, themeFromArg } from "../theme";
import { buildSteps, EMPTY_INDEX } from "../trail";
import { verbFor } from "../components/TermStatus";
import { ev } from "./helpers";

function run(
  actions: readonly Parameters<typeof chatReducer>[1][],
  from: ChatState = emptyChat,
): ChatState {
  return actions.reduce(chatReducer, from);
}

/* ------------------------------------------------------------------ *
 * GroundTruth lines
 * ------------------------------------------------------------------ */

describe("GroundTruth — the frame", () => {
  const frame = {
    step: 3,
    kind: "exact_literal_search",
    arguments: { literal: "class Command", paths: ["src/click"] },
    scope: ["src/click"],
    semantics: "exact",
    coverage: "complete",
    match_count: 2,
    omissions: [],
    reason_codes: [],
  };

  it("reads the query, the scope and the evidence off it", () => {
    const action = fromFrame(frame)!;
    expect(action.kind).toBe("exact_literal_search");
    expect(action.query).toBe("class Command");
    expect(action.scope).toEqual(["src/click"]);
    expect(actionLine(action)).toBe(
      'exact_literal_search "class Command" in src/click',
    );
    expect(evidenceLine(action.evidence)).toBe("2 matches · exact · complete");
  });

  it("prefers the scope the producer really searched over the one asked for", () => {
    const action = fromFrame({
      ...frame,
      arguments: { literal: "x", paths: ["src/click/**"] },
      scope: ["src/click"],
    })!;
    expect(action.scope).toEqual(["src/click"]);
  });

  it("says it abstained rather than reporting zero matches", () => {
    const action = fromFrame({
      ...frame,
      match_count: 0,
      semantics: "incomplete",
      reason_codes: ["COVERAGE_NOT_COMPLETE"],
    })!;
    expect(evidenceLine(action.evidence)).toBe(
      "abstained: COVERAGE_NOT_COMPLETE",
    );
  });

  it("falls back to the omissions when there is no reason code", () => {
    const action = fromFrame({
      ...frame,
      reason_codes: [],
      omissions: ["missing_scope:src/click/**"],
    })!;
    expect(evidenceLine(action.evidence)).toBe(
      "abstained: missing_scope:src/click/**",
    );
  });

  it("is nothing at all without a kind", () => {
    expect(fromFrame({ arguments: {} })).toBeNull();
  });
});

describe("GroundTruth — recovered from the command", () => {
  it("parses a typed action a server that has no gt_action frame still ran", () => {
    const command = JSON.stringify({
      tool: GT_TOOL,
      kind: "exact_literal_search",
      arguments: { literal: "def invoke", paths: ["src/click/core.py"] },
    });
    const action = fromCommand(command)!;
    expect(action.kind).toBe("exact_literal_search");
    expect(actionLine(action)).toBe(
      'exact_literal_search "def invoke" in src/click/core.py',
    );
    // A command says what was asked; only the frame says what came back.
    expect(action.evidence).toBeNull();
  });

  it("leaves a shell command alone", () => {
    expect(fromCommand("rg -n 'class Command' src/click")).toBeNull();
    expect(fromCommand('{"tool": "bash", "kind": "x"}')).toBeNull();
    expect(fromCommand("{not json")).toBeNull();
    expect(fromCommand("")).toBeNull();
  });
});

describe("GroundTruth — in the trail", () => {
  it("puts a gt_action frame on its own step, never on a Bash line", () => {
    const state = run([
      { type: "event", event: ev(1, "turn_started", { turn_id: "t1" }) },
      { type: "event", event: ev(2, "assistant", { turn_id: "t1", content: "look" }) },
      {
        type: "event",
        event: ev(3, "gt_action", {
          turn_id: "t1",
          kind: "exact_literal_search",
          arguments: { literal: "class Command" },
          scope: ["src/click"],
          semantics: "exact",
          coverage: "complete",
          match_count: 2,
        }),
      },
    ]);
    const steps = buildSteps(state.turns.t1, EMPTY_INDEX);
    expect(steps).toHaveLength(1);
    expect(steps[0].command).toBeNull();
    expect(steps[0].gt?.kind).toBe("exact_literal_search");
  });

  it("attaches a gt_action with no turn_id to the turn that is running", () => {
    const state = run([
      { type: "event", event: ev(1, "turn_started", { turn_id: "t1" }) },
      {
        type: "event",
        event: ev(2, "gt_action", { kind: "syntax", arguments: {} }),
      },
    ]);
    expect(buildSteps(state.turns.t1, EMPTY_INDEX)[0].gt?.kind).toBe("syntax");
  });

  it("sends a worker's gt_action to that worker, not to the primary turn", () => {
    const state = run([
      { type: "event", event: ev(1, "turn_started", { turn_id: "t1" }) },
      {
        type: "event",
        event: ev(2, "gt_action", {
          agent_id: "w1",
          turn_id: "wt",
          kind: "exact_literal_search",
          arguments: {},
        }),
      },
    ]);
    expect(buildSteps(state.turns.t1, EMPTY_INDEX)).toHaveLength(0);
    expect(state.workers.order).toEqual(["w1"]);
  });
});

/* ------------------------------------------------------------------ *
 * The status verb
 * ------------------------------------------------------------------ */

describe("the status verb", () => {
  it("is Thinking while a model call has produced no command yet", () => {
    expect(verbFor(null)).toBe("Thinking");
    expect(verbFor("   ")).toBe("Thinking");
  });

  it("reads the command for what it is", () => {
    expect(verbFor("cat src/click/core.py")).toBe("Reading");
    expect(verbFor("rg -n 'def invoke' src")).toBe("Reading");
    expect(verbFor("sed -i 's/a/b/' f.py")).toBe("Editing");
    expect(verbFor("tee f.py <<'EOF'")).toBe("Editing");
    expect(verbFor("pytest -q")).toBe("Checking");
    expect(verbFor("ruff check .")).toBe("Checking");
    expect(verbFor("./configure && make")).toBe("Running");
  });
});

/* ------------------------------------------------------------------ *
 * The theme, and the two commands that reach it
 * ------------------------------------------------------------------ */

describe("the theme", () => {
  it("toggles with no argument and obeys one when given", () => {
    expect(themeFromArg("", "dark")).toBe("light");
    expect(themeFromArg("", "light")).toBe("dark");
    expect(themeFromArg("light", "light")).toBe("light");
    expect(themeFromArg("DARK", "light")).toBe("dark");
    // Anything else is a toggle, not an error the reader has to read.
    expect(themeFromArg("solarized", "dark")).toBe("light");
  });

  it("knows the two it has", () => {
    expect(isTheme("dark")).toBe(true);
    expect(isTheme("light")).toBe(true);
    expect(isTheme("amber")).toBe(false);
    expect(otherTheme("dark")).toBe("light");
  });
});

describe("the command set", () => {
  it("offers /theme and /resume", () => {
    const names = SLASH_COMMANDS.map((command) => command.name);
    expect(names).toContain("theme");
    expect(names).toContain("resume");
    expect(parseSlash("/theme light")?.arg).toBe("light");
    expect(parseSlash("/resume")?.command.name).toBe("resume");
  });

  it("completes them from a prefix", () => {
    expect(slashSuggestions("/t").map((c) => c.name)).toEqual(["theme"]);
    expect(slashSuggestions("/r").map((c) => c.name)).toEqual(["resume"]);
  });
});
