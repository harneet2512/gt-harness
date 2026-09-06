import { describe, expect, it } from "vitest";
import {
  combinePrompt,
  CREATION_STEPS,
  creationLabel,
  creationStage,
  GRAPH_AUTO_FILES,
  shouldAutoOpenGraph,
  turnFileCount,
} from "../launch";
import {
  DEFAULT_PREFS,
  MODELS,
  normalizePrefs,
  readGraphOpen,
  withGraphOpen,
} from "../prefs";
import { isRepoUrl, parseRepoRef, repoChipLabel } from "../repoUrl";
import { helpText, parseSlash, SLASH_COMMANDS, slashSuggestions } from "../slash";

describe("parseRepoRef", () => {
  it("reads a bare repository URL out of a sentence", () => {
    const found = parseRepoRef(
      "please fix the flaky test in https://github.com/pallets/click and open a PR",
    );
    expect(found).toEqual({ repo: "https://github.com/pallets/click", ref: null });
  });

  it("takes the ref from a /tree/ link, slashes and all", () => {
    expect(
      parseRepoRef("https://github.com/harneet2512/gt-harness/tree/cloud/internal-harness"),
    ).toEqual({
      repo: "https://github.com/harneet2512/gt-harness",
      ref: "cloud/internal-harness",
    });
  });

  it("takes the ref from the owner/name@ref shorthand", () => {
    expect(parseRepoRef("look at https://github.com/octocat/Hello-World@master")).toEqual({
      repo: "https://github.com/octocat/Hello-World",
      ref: "master",
    });
  });

  it("takes the ref from a /blob/ link and ignores other deep links", () => {
    expect(parseRepoRef("https://github.com/o/n/blob/dev/src/app.ts")?.ref).toBe("dev");
    expect(parseRepoRef("https://github.com/o/n/pull/12")).toEqual({
      repo: "https://github.com/o/n",
      ref: null,
    });
  });

  it("drops a .git suffix and trailing sentence punctuation", () => {
    expect(parseRepoRef("clone https://github.com/o/n.git.")?.repo).toBe(
      "https://github.com/o/n",
    );
    expect(parseRepoRef("see https://github.com/o/n/tree/main).")?.ref).toBe("main");
  });

  it("returns null when no repository is named", () => {
    expect(parseRepoRef("add a test for the parser")).toBeNull();
    expect(parseRepoRef("")).toBeNull();
    expect(parseRepoRef("https://gitlab.com/o/n")).toBeNull();
  });

  it("knows a message that is only a URL", () => {
    expect(isRepoUrl("https://github.com/o/n")).toBe(true);
    expect(isRepoUrl("use https://github.com/o/n")).toBe(false);
  });

  it("labels the chip as owner/name @ ref", () => {
    expect(repoChipLabel("https://github.com/pallets/click", "main")).toBe(
      "pallets/click @ main",
    );
    expect(repoChipLabel("https://github.com/pallets/click", null)).toBe("pallets/click");
  });
});

describe("slash commands", () => {
  it("parses a known command and its argument", () => {
    expect(parseSlash("/stop")?.command.name).toBe("stop");
    expect(parseSlash("/spawn  write the migration ")).toEqual({
      command: SLASH_COMMANDS.find((c) => c.name === "spawn"),
      arg: "write the migration",
      raw: "/spawn  write the migration",
    });
  });

  it("leaves an ordinary message alone", () => {
    expect(parseSlash("/usr/bin/env python")).toBeNull();
    expect(parseSlash("/nope")).toBeNull();
    expect(parseSlash("stop")).toBeNull();
    expect(parseSlash("what does /stop do")).toBeNull();
  });

  it("suggests only while a bare name is being typed", () => {
    expect(slashSuggestions("/").length).toBe(SLASH_COMMANDS.length);
    expect(slashSuggestions("/s").map((c) => c.name)).toEqual([
      "stop",
      "settings",
      "spawn",
    ]);
    expect(slashSuggestions("/stop ")).toEqual([]);
    expect(slashSuggestions("hello")).toEqual([]);
  });

  /* The composer reads both of these on Enter: a name that already parses
     is run, a name that only prefixes one is completed. Without the first
     half, `/help` + Enter silently became `/help ` and nothing happened. */
  it("treats a finished name as a command, not a prefix to complete", () => {
    expect(slashSuggestions("/help").length).toBe(1);
    expect(parseSlash("/help")).not.toBeNull();
    expect(slashSuggestions("/hel").length).toBe(1);
    expect(parseSlash("/hel")).toBeNull();
  });

  it("lists every command in /help", () => {
    const text = helpText();
    for (const command of SLASH_COMMANDS) {
      expect(text).toContain(`/${command.name}`);
    }
  });
});

describe("prefs", () => {
  it("defaults to nemotron, advisory, 60 steps and the server's wall clock", () => {
    expect(DEFAULT_PREFS.model).toBe(MODELS[0]);
    expect(DEFAULT_PREFS.model).toBe("nvidia/nemotron-3-super-120b-a12b:free");
    expect(DEFAULT_PREFS.gtMode).toBe("advisory");
    expect(DEFAULT_PREFS.stepLimit).toBe(60);
    expect(DEFAULT_PREFS.wallSeconds).toBeNull();
  });

  it("merges a stored blob field by field", () => {
    expect(normalizePrefs({ model: "acme/x", stepLimit: 12 })).toEqual({
      model: "acme/x",
      gtMode: "advisory",
      stepLimit: 12,
      wallSeconds: null,
    });
  });

  it("refuses a gt mode the server would reject", () => {
    expect(normalizePrefs({ gtMode: "engine" }).gtMode).toBe("advisory");
    expect(normalizePrefs({ gtMode: "enforced" }).gtMode).toBe("enforced");
  });

  it("clamps the budgets into the range the server accepts", () => {
    expect(normalizePrefs({ stepLimit: 0 }).stepLimit).toBe(1);
    expect(normalizePrefs({ stepLimit: 9999 }).stepLimit).toBe(500);
    expect(normalizePrefs({ wallSeconds: 10 }).wallSeconds).toBe(60);
    expect(normalizePrefs({ wallSeconds: 99999 }).wallSeconds).toBe(3600);
    expect(normalizePrefs({ wallSeconds: 900 }).wallSeconds).toBe(900);
    expect(normalizePrefs({ wallSeconds: "" }).wallSeconds).toBeNull();
  });

  it("survives junk", () => {
    expect(normalizePrefs(null)).toEqual(DEFAULT_PREFS);
    expect(normalizePrefs("nope")).toEqual(DEFAULT_PREFS);
    expect(normalizePrefs({ model: "   " }).model).toBe(DEFAULT_PREFS.model);
  });

  it("remembers the graph panel per session", () => {
    const first = withGraphOpen(null, "s1", true);
    expect(readGraphOpen(first, "s1")).toBe(true);
    expect(readGraphOpen(first, "s2")).toBeNull();

    const second = withGraphOpen(first, "s2", false);
    expect(readGraphOpen(second, "s1")).toBe(true);
    expect(readGraphOpen(second, "s2")).toBe(false);
    expect(readGraphOpen({ s1: "yes" }, "s1")).toBeNull();
  });
});

describe("launch", () => {
  it("walks the creation phases in order", () => {
    expect(creationStage(null)).toBe(0);
    expect(creationStage("creating")).toBe(0);
    expect(creationStage("cloning")).toBe(0);
    expect(creationStage("sandbox_starting")).toBe(1);
    expect(creationStage("sandbox_ready")).toBe(1);
    expect(creationStage("indexing")).toBe(2);
    expect(creationStage("gt_ready")).toBe(CREATION_STEPS.length);
    expect(creationStage("gt_unavailable")).toBe(CREATION_STEPS.length);
    expect(creationStage("idle")).toBe(CREATION_STEPS.length);
  });

  it("names the phase the reader is waiting on", () => {
    expect(creationLabel("pallets/click", 0)).toBe("cloning pallets/click…");
    expect(creationLabel("pallets/click", 2)).toBe("indexing…");
    expect(creationLabel("pallets/click", 3)).toBe("workspace ready");
  });

  it("joins the ask and the URL into one first turn", () => {
    expect(combinePrompt("add a test", "https://github.com/o/n")).toBe(
      "add a test\n\nhttps://github.com/o/n",
    );
    expect(combinePrompt(null, "just this")).toBe("just this");
    expect(combinePrompt("  ", "just this")).toBe("just this");
    expect(combinePrompt("only the ask", "  ")).toBe("only the ask");
  });

  it("counts distinct files across a turn", () => {
    const steps = [
      { files: ["a.py", "b.py"] },
      { files: ["b.py"] },
      { files: [] },
      { files: ["c.py"] },
    ];
    expect(turnFileCount(steps)).toBe(3);
    expect(turnFileCount([])).toBe(0);
  });

  it("opens the graph at three files, not two", () => {
    expect(GRAPH_AUTO_FILES).toBe(3);
    expect(shouldAutoOpenGraph(2)).toBe(false);
    expect(shouldAutoOpenGraph(3)).toBe(true);
    expect(shouldAutoOpenGraph(9)).toBe(true);
  });
});
