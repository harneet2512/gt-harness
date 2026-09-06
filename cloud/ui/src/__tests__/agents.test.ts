/* ------------------------------------------------------------------ *
 * Several minds on one map.
 *
 * Everything here is the pure half of "make four working agents legible":
 * who travels and in what order, how much may be in the air, what happens
 * when two of them stand on the same particle, what reduced motion turns
 * a hop into, and how the graph tells the truth about an agent that is
 * not working in this repository at all.
 * ------------------------------------------------------------------ */

import { describe, expect, it } from "vitest";
import {
  agentChip,
  agentMatches,
  focusAgent,
  MAX_SLOTS,
  OUTSIDE_REPO,
  presenceOf,
  repoFit,
  type Chippable,
  type Focusable,
  type Presence,
} from "../agentField";
import {
  MAX_IN_FLIGHT,
  MAX_PENDING,
  PRIMARY_AGENT,
  PRIMARY_RGB,
  SignalDirector,
  SignalQueue,
  SIGNAL_GAP_MS,
  SIGNAL_MS,
  STAGGER_MS,
  STILL_MS,
  STILL_STAGGER_MS,
} from "../signals";

const A = "1, 2, 3";
const B = "4, 5, 6";

/**
 * The render loop, at 60fps, for `frames` frames from `from`. The director
 * releases at most one signal per call by design, so a test that jumps the
 * clock in one leap is testing something the browser never does.
 */
function run(
  director: SignalDirector,
  frames: number,
  from = 0,
  onFrame?: (now: number, live: ReturnType<SignalDirector["update"]>) => void,
): number {
  let now = from;
  for (let i = 0; i < frames; i += 1) {
    const live = director.update(now);
    onFrame?.(now, live);
    now += 16;
  }
  return now;
}

/* ------------------------------------------------------------------ *
 * One agent's queue
 * ------------------------------------------------------------------ */

describe("SignalQueue — one agent's hops", () => {
  it("carries the agent's id and hue onto every signal it emits", () => {
    const queue = new SignalQueue("w1", A);
    queue.push("a", "b");
    queue.release(0);

    const [signal] = queue.update(100);
    expect(signal.agentId).toBe("w1");
    expect(signal.rgb).toBe(A);
  });

  it("refuses a hop that goes nowhere", () => {
    const queue = new SignalQueue("w1", A);
    queue.push("a", "a");
    queue.push("", "b");
    queue.push("a", "");
    expect(queue.waiting).toBe(0);
  });

  it("holds its own rhythm between departures", () => {
    const queue = new SignalQueue("w1", A);
    queue.push("a", "b");
    queue.push("b", "c");

    expect(queue.wants(0)).toBe(true);
    queue.release(0);
    expect(queue.wants(SIGNAL_GAP_MS - 1)).toBe(false);
    expect(queue.wants(SIGNAL_GAP_MS)).toBe(true);
  });

  it("retires a signal once it has arrived", () => {
    const queue = new SignalQueue("w1", A);
    queue.push("a", "b");
    queue.release(0);

    expect(queue.update(SIGNAL_MS - 1)).toHaveLength(1);
    expect(queue.update(SIGNAL_MS)).toHaveLength(0);
    expect(queue.busy).toBe(false);
  });

  it("keeps the newest hops when a replay dumps a whole history in", () => {
    const queue = new SignalQueue("w1", A);
    for (let i = 0; i < MAX_PENDING + 40; i += 1) queue.push(`p${i}`, `p${i + 1}`);

    expect(queue.waiting).toBe(MAX_PENDING);
    queue.release(0);
    // The first one out is from the tail of the history, not its head.
    const [signal] = queue.update(1);
    expect(signal.from).toBe("p40");
  });

  it("allocates nothing new per frame: the signal objects are reused", () => {
    const queue = new SignalQueue("w1", A);
    queue.push("a", "b");
    queue.release(0);

    const first = queue.update(10)[0];
    const second = queue.update(20)[0];
    expect(second).toBe(first);
    expect(second.progress).toBeGreaterThan(0);
  });
});

/* ------------------------------------------------------------------ *
 * Every agent travels
 * ------------------------------------------------------------------ */

describe("SignalDirector — every agent's work travels", () => {
  it("gives each agent its own queue, hue and id", () => {
    const director = new SignalDirector();
    director.push(PRIMARY_AGENT, PRIMARY_RGB, "a", "b");
    director.push("w1", A, "c", "d");
    director.push("w2", B, "e", "f");

    const seen = new Map<string, string>();
    run(director, 40, 0, (_now, live) => {
      for (const signal of live) seen.set(signal.agentId, signal.rgb);
    });

    expect(seen.get(PRIMARY_AGENT)).toBe(PRIMARY_RGB);
    expect(seen.get("w1")).toBe(A);
    expect(seen.get("w2")).toBe(B);
  });

  it("emits an agent's hops in the order it walked them", () => {
    const director = new SignalDirector();
    director.push("w1", A, "a", "b");
    director.push("w1", A, "b", "c");
    director.push("w1", A, "c", "d");

    const order: string[] = [];
    run(director, 120, 0, (_now, live) => {
      for (const signal of live) {
        const hop = `${signal.from}>${signal.to}`;
        if (!order.includes(hop)) order.push(hop);
      }
    });

    expect(order).toEqual(["a>b", "b>c", "c>d"]);
  });

  it("re-hues an agent without losing its queue", () => {
    const director = new SignalDirector();
    director.push("w1", A, "a", "b");
    const queue = director.queue("w1", B);
    expect(queue.rgb).toBe(B);
    expect(queue.waiting).toBe(1);
  });
});

/* ------------------------------------------------------------------ *
 * Four at once, legibly
 * ------------------------------------------------------------------ */

describe("SignalDirector — four agents read as four minds", () => {
  const four = () => {
    const director = new SignalDirector();
    for (const [id, rgb] of [
      ["w1", A],
      ["w2", B],
      ["w3", A],
      ["w4", B],
    ] as const) {
      for (let i = 0; i < 12; i += 1) director.push(id, rgb, `${id}-${i}`, `${id}-${i + 1}`);
    }
    return director;
  };

  it("never lets two agents leave on the same frame", () => {
    const director = four();
    let previous = new Set<string>();

    run(director, 400, 0, (_now, live) => {
      const now = new Set(live.map((signal) => `${signal.agentId}:${signal.from}`));
      let departures = 0;
      for (const key of now) if (!previous.has(key)) departures += 1;
      expect(departures).toBeLessThanOrEqual(1);
      previous = now;
    });
  });

  it("spaces two agents' departures by at least a stagger", () => {
    const director = four();
    const departures: number[] = [];
    let previous = new Set<string>();

    run(director, 400, 0, (now, live) => {
      const keys = new Set(live.map((s) => `${s.agentId}:${s.from}`));
      for (const key of keys) {
        if (!previous.has(key)) departures.push(now);
      }
      previous = keys;
    });

    expect(departures.length).toBeGreaterThan(8);
    for (let i = 1; i < departures.length; i += 1) {
      expect(departures[i] - departures[i - 1]).toBeGreaterThanOrEqual(STAGGER_MS);
    }
  });

  it("caps what is in the air however many agents are pushing", () => {
    const director = four();
    run(director, 600, 0, (_now, live) => {
      expect(live.length).toBeLessThanOrEqual(MAX_IN_FLIGHT);
    });
  });

  it("takes turns, so a busy agent cannot crowd out a quiet one", () => {
    const director = new SignalDirector();
    for (let i = 0; i < 30; i += 1) director.push("loud", A, `l${i}`, `l${i + 1}`);
    director.push("quiet", B, "q0", "q1");

    let sawQuiet = false;
    run(director, 60, 0, (_now, live) => {
      if (live.some((signal) => signal.agentId === "quiet")) sawQuiet = true;
    });

    expect(sawQuiet).toBe(true);
  });

  it("forgets an agent that is gone, and never the primary", () => {
    const director = new SignalDirector();
    director.push(PRIMARY_AGENT, PRIMARY_RGB, "a", "b");
    director.push("w1", A, "c", "d");
    director.push("w2", B, "e", "f");

    director.retain(["w2"]);
    expect([...director.agentIds].sort()).toEqual([PRIMARY_AGENT, "w2"]);
  });

  it("drops a scrubbed agent's flight without touching the others", () => {
    const director = new SignalDirector();
    director.push("w1", A, "a", "b");
    director.push("w2", B, "c", "d");
    run(director, 20);

    director.clear("w1");
    const live = director.update(400);
    expect(live.every((signal) => signal.agentId !== "w1")).toBe(true);
  });
});

/* ------------------------------------------------------------------ *
 * Reduced motion
 * ------------------------------------------------------------------ */

describe("SignalDirector — prefers-reduced-motion", () => {
  it("holds the signal still at its destination instead of moving it", () => {
    const director = new SignalDirector({ reduced: true });
    director.push("w1", A, "a", "b");

    const [signal] = director.update(0);
    expect(signal.still).toBe(true);
    expect(signal.progress).toBe(1);

    const [later] = director.update(STILL_MS / 2);
    expect(later.progress).toBe(1);
  });

  it("lets a still highlight leave rather than pop", () => {
    const director = new SignalDirector({ reduced: true });
    director.push("w1", A, "a", "b");

    expect(director.update(0)[0].fade).toBe(1);
    expect(director.update(STILL_MS * 0.9)[0].fade).toBeLessThan(0.4);
    expect(director.update(STILL_MS)).toHaveLength(0);
  });

  it("arrives slowly enough to be read one at a time", () => {
    const director = new SignalDirector({ reduced: true });
    for (let i = 0; i < 6; i += 1) director.push("w1", A, `p${i}`, `p${i + 1}`);

    const departures: number[] = [];
    let previous = new Set<string>();
    run(director, 300, 0, (now, live) => {
      const keys = new Set(live.map((s) => `${s.from}>${s.to}`));
      for (const key of keys) if (!previous.has(key)) departures.push(now);
      previous = keys;
    });

    expect(departures.length).toBeGreaterThan(1);
    for (let i = 1; i < departures.length; i += 1) {
      expect(departures[i] - departures[i - 1]).toBeGreaterThanOrEqual(
        STILL_STAGGER_MS,
      );
    }
  });

  it("does not leave motion mid-flight when the preference turns on", () => {
    const director = new SignalDirector();
    director.push("w1", A, "a", "b");
    run(director, 4);
    expect(director.inFlight).toBe(1);

    director.setReduced(true);
    expect(director.inFlight).toBe(0);
    expect(director.busy).toBe(false);
  });
});

/* ------------------------------------------------------------------ *
 * Two agents, one particle
 * ------------------------------------------------------------------ */

function agent(id: string, ids: string[], position: string | null): Presence {
  return {
    id,
    attention: new Map(ids.map((one) => [one, { reads: 1, last: 1 }])),
    positionId: position,
  };
}

describe("presenceOf — who is standing where", () => {
  it("keeps both agents on a particle they share, in trail order", () => {
    const presence = presenceOf([
      agent("w1", ["core.py", "cli.py"], "cli.py"),
      agent("w2", ["core.py"], "core.py"),
    ]);

    expect(presence.get("core.py")).toEqual(["w1", "w2"]);
    expect(presence.get("cli.py")).toEqual(["w1"]);
  });

  it("never silently drops the second agent for the first", () => {
    const presence = presenceOf([
      agent("w1", ["core.py"], "core.py"),
      agent("w2", ["core.py"], "core.py"),
      agent("w3", ["core.py"], "core.py"),
    ]);

    expect(presence.get("core.py")).toHaveLength(3);
  });

  it("stops at the point where another wedge would say nothing", () => {
    const many = Array.from({ length: MAX_SLOTS + 3 }, (_, i) =>
      agent(`w${i}`, ["core.py"], "core.py"),
    );
    expect(presenceOf(many).get("core.py")).toHaveLength(MAX_SLOTS);
  });

  it("keeps an agent standing somewhere it has no attention for", () => {
    const presence = presenceOf([
      { id: "w1", attention: new Map(), positionId: "odd.py" },
    ]);
    expect(presence.get("odd.py")).toEqual(["w1"]);
  });

  it("is empty when nobody has touched anything", () => {
    expect(presenceOf([]).size).toBe(0);
    expect(
      presenceOf([{ id: "w1", attention: new Map(), positionId: null }]).size,
    ).toBe(0);
  });
});

/* ------------------------------------------------------------------ *
 * Working outside this repo
 * ------------------------------------------------------------------ */

describe("repoFit — is this agent even working on our repo", () => {
  const here = new Map([
    ["src/core.py", "src/core.py"],
    ["src/cli.py", "src/cli.py"],
  ]);

  it("says so when an external agent's files are all somewhere else", () => {
    const fit = repoFit(true, ["other/thing.rs", "other/main.rs"], here);
    expect(fit).toEqual({ reported: 2, resolved: 0, outside: true });
  });

  it("does not say so before the agent has reported anything", () => {
    const fit = repoFit(true, [], here);
    expect(fit.outside).toBe(false);
    expect(fit.reported).toBe(0);
  });

  it("does not say so when even one file lands here", () => {
    const fit = repoFit(true, ["other/thing.rs", "src/cli.py"], here);
    expect(fit.resolved).toBe(1);
    expect(fit.outside).toBe(false);
  });

  it("never says so about a worker of ours, which reports no paths", () => {
    // Its files are inferred from commands; an unmatched `ls` is ordinary.
    expect(repoFit(false, ["other/thing.rs"], here).outside).toBe(false);
  });

  it("counts a path once however often it is named", () => {
    const fit = repoFit(true, ["src/cli.py", "src/cli.py", ""], here);
    expect(fit.reported).toBe(1);
    expect(fit.resolved).toBe(1);
  });

  it("resolves a folded path through the same map the graph uses", () => {
    const folded = new Map([["deep/nested/a.py", "dir:deep/nested"]]);
    expect(repoFit(true, ["deep/nested/a.py"], folded).outside).toBe(false);
  });
});

/* ------------------------------------------------------------------ *
 * The legend
 * ------------------------------------------------------------------ */

function chippable(over: Partial<Chippable> = {}): Chippable {
  return {
    no: 1,
    isExternal: false,
    kind: null,
    state: "running",
    outsideRepo: false,
    depth: 0,
    task: "port the parser",
    ...over,
  };
}

describe("agentChip — number, kind and state on one chip", () => {
  it("writes a worker of ours", () => {
    const chip = agentChip(chippable({ no: 2 }));
    expect(chip.text).toBe("2 worker · running");
    expect(chip.child).toBe(false);
  });

  it("writes an agent we only watch, by its kind", () => {
    const chip = agentChip(
      chippable({ no: 3, isExternal: true, kind: "claude-code", state: "working" }),
    );
    expect(chip.text).toBe("3 claude-code · working");
  });

  it("falls back to a word rather than an empty chip", () => {
    const chip = agentChip(chippable({ isExternal: true, kind: null }));
    expect(chip.text).toContain("external");
  });

  it("says plainly when the agent's work is not on this map", () => {
    const chip = agentChip(
      chippable({ isExternal: true, kind: "codex", outsideRepo: true }),
    );
    expect(chip.note).toBe(OUTSIDE_REPO);
    // Its state is no longer "working here", because it is not.
    expect(chip.text).toBe("1 codex · elsewhere");
    expect(chip.title).toContain(OUTSIDE_REPO);
  });

  it("marks a subagent as hanging off the agent above it", () => {
    const chip = agentChip(chippable({ depth: 1, isExternal: true, kind: "codex" }));
    expect(chip.child).toBe(true);
    expect(chip.title).toContain("subagent");
  });
});

describe("agentMatches — hover focuses, click isolates", () => {
  const agents: Focusable[] = [
    { id: "w1", ids: new Set(["a", "b"]) },
    { id: "w2", ids: new Set(["c"]) },
  ];

  it("narrows to nothing in particular when nobody is picked", () => {
    expect(agentMatches(agents, null, null)).toBeNull();
  });

  it("narrows to the hovered agent", () => {
    expect([...(agentMatches(agents, null, "w2") ?? [])]).toEqual(["c"]);
  });

  it("narrows to the isolated agent", () => {
    expect([...(agentMatches(agents, "w1") ?? [])]).toEqual(["a", "b"]);
  });

  it("lets a deliberate isolate outrank a passing hover", () => {
    expect([...(agentMatches(agents, "w1", "w2") ?? [])]).toEqual(["a", "b"]);
    expect(focusAgent("w1", "w2")).toBe("w1");
  });

  it("falls back to whatever else the panel wanted when the agent is gone", () => {
    expect(agentMatches(agents, "vanished")).toBeNull();
    expect(focusAgent(null, null)).toBeNull();
  });

  it("narrows to nothing at all for an agent that touched nothing here", () => {
    const elsewhere: Focusable[] = [{ id: "w3", ids: new Set() }];
    expect(agentMatches(elsewhere, "w3")?.size).toBe(0);
  });
});
