/* ------------------------------------------------------------------ *
 * Agents against the field.
 *
 * Three questions the graph has to answer honestly once there is more
 * than one mind on it:
 *
 *   · does this agent's work land on *this* map at all, or is it working
 *     in a checkout we never cloned (`repoFit`);
 *   · which agents are standing on the same particle, and in what order,
 *     so a shared particle can show all of them rather than the last one
 *     drawn (`presenceOf`);
 *   · what one chip in the legend says, and what clicking or hovering it
 *     narrows the map to (`agentChip`, `agentMatches`).
 *
 * All of it is pure, and none of it knows about canvases or React.
 * ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ *
 * Working outside this repo
 * ------------------------------------------------------------------ */

export interface RepoFit {
  /** Distinct repo-relative paths the agent has named. */
  reported: number;
  /** How many of those are particles on this map. */
  resolved: number;
  /**
   * The agent has said where it is working and *none* of it is here.
   *
   * That is the honest reading of an external agent — a Claude Code or
   * Codex session on somebody's laptop — running against a different
   * checkout from the one this session cloned. Its paths are dropped by
   * `resolve`, so without this it would sit on the map in a live hue with
   * an empty trail, looking broken. Reported nothing yet is *not* this:
   * an agent that has not spoken is not an agent that is elsewhere.
   */
  outside: boolean;
}

export const NO_FIT: RepoFit = { reported: 0, resolved: 0, outside: false };

/**
 * How much of what this agent says it touched exists here.
 *
 * Only ever `outside` for an agent we watch rather than run. One of our
 * own workers reports no paths at all — its files are inferred from its
 * commands against the tree — so a worker whose `ls -la` resolved to
 * nothing is ordinary, not elsewhere.
 */
export function repoFit(
  isExternal: boolean,
  reported: Iterable<string>,
  resolve: ReadonlyMap<string, string>,
): RepoFit {
  const seen = new Set<string>();
  let resolved = 0;
  for (const path of reported) {
    if (!path || seen.has(path)) continue;
    seen.add(path);
    if (resolve.has(path)) resolved += 1;
  }
  return {
    reported: seen.size,
    resolved,
    outside: isExternal && seen.size > 0 && resolved === 0,
  };
}

/** The words the card and the chip both use, so they cannot drift. */
export const OUTSIDE_REPO = "working outside this repo";

/* ------------------------------------------------------------------ *
 * Who is standing where
 * ------------------------------------------------------------------ */

/** Just enough of an agent's trail to say which particles it has touched. */
export interface Presence {
  id: string;
  attention: ReadonlyMap<string, unknown>;
  positionId: string | null;
}

/**
 * Past this many agents on one particle the ring segments are thinner
 * than the hairline they are drawn with, and a fifth slice says nothing a
 * fourth did not. Four is also how many hues there are.
 */
export const MAX_SLOTS = 4;

/**
 * Particle id → the agents on it, in trail order.
 *
 * Built once per render rather than once per frame: it changes only when
 * an agent touches a new file, and the painter must not be allocating
 * maps sixty times a second. Only particles more than one agent shares
 * would strictly need an entry, but every touched particle gets one so
 * the painter has a single list to walk.
 */
export function presenceOf(
  agents: readonly Presence[],
): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const agent of agents) {
    for (const id of agent.attention.keys()) {
      const bucket = out.get(id);
      if (bucket) {
        if (bucket.length < MAX_SLOTS) bucket.push(agent.id);
      } else {
        out.set(id, [agent.id]);
      }
    }
    /* An agent standing somewhere it has no attention for cannot happen
       today, but a replayed session is allowed to be odd. */
    const here = agent.positionId;
    if (here && !out.has(here)) out.set(here, [agent.id]);
  }
  return out;
}

/* ------------------------------------------------------------------ *
 * The legend
 * ------------------------------------------------------------------ */

/** Just enough of an agent to write its chip. */
export interface Chippable {
  no: number;
  isExternal: boolean;
  kind: string | null;
  state: string;
  outsideRepo: boolean;
  depth: 0 | 1;
  task: string;
}

export interface AgentChip {
  /** `1`, `2` … the flat spawn position, which also picks the hue. */
  no: number;
  /** `worker` for one of ours; the kind for one we only watch. */
  kind: string;
  /** `running`/`reported` for ours, `working`/`idle`/`done` for theirs. */
  state: string;
  /** The one-line truth when the agent's files are not on this map. */
  note: string;
  /** A subagent hangs off the line above it, the way the transcript does. */
  child: boolean;
  /** What the chip reads, hue aside. */
  text: string;
  /** The long form, for the title attribute. */
  title: string;
}

/**
 * One legend chip: hue, number, kind, state — and, when it is the truth,
 * that this agent's work is not on this map at all.
 */
export function agentChip(agent: Chippable): AgentChip {
  const kind = agent.isExternal ? agent.kind || "external" : "worker";
  const note = agent.outsideRepo ? OUTSIDE_REPO : "";
  const state = note ? "elsewhere" : agent.state;
  const text = `${agent.no} ${kind} · ${state}`;
  return {
    no: agent.no,
    kind,
    state,
    note,
    child: agent.depth > 0,
    text,
    title: [
      agent.depth > 0 ? `subagent · ${kind} ${agent.no}` : `${kind} ${agent.no}`,
      agent.task,
      note,
    ]
      .filter(Boolean)
      .join(" — "),
  };
}

/* ------------------------------------------------------------------ *
 * Focus and isolate
 * ------------------------------------------------------------------ */

/** Just enough of a trail to narrow the map to it. */
export interface Focusable {
  id: string;
  ids: ReadonlySet<string>;
}

/**
 * The particles the map narrows to.
 *
 * Isolating (a click, or `[focus]` in the transcript) wins over hovering,
 * so passing the pointer over the legend cannot silently change what a
 * reader deliberately pinned. An agent that touched nothing here still
 * narrows to nothing — that *is* the answer, and it is the same answer
 * the chip is already spelling out in words.
 */
export function agentMatches(
  agents: readonly Focusable[],
  isolated: string | null,
  hovered: string | null = null,
): ReadonlySet<string> | null {
  const wanted = isolated ?? hovered;
  if (!wanted) return null;
  const agent = agents.find((one) => one.id === wanted);
  return agent ? agent.ids : null;
}

/** The agent the canvas draws at full strength; every other one dims. */
export function focusAgent(
  isolated: string | null,
  hovered: string | null,
): string | null {
  return isolated ?? hovered;
}
