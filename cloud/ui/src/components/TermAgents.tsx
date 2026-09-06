import { formatDuration, formatTokens, truncate } from "../format";
import {
  fleetMark,
  NO_AGENTS,
  type AgentLine,
  type RootLine,
} from "../workers";
import { Call, Cont, ContMore } from "./TermLine";

/** How much of an activity line survives before the column clips it. */
const DOING_MAX = 96;

interface Props {
  lines: readonly AgentLine[];
  /** The session's own agent — the `● main` the fleet hangs off. */
  root: RootLine | null;
  /** Narrow the graph to one agent. Omitted where there is no graph. */
  onFocus?: (agentId: string) => void;
}

/**
 * `/agents` — the fleet, as a live tree:
 *
 *     ⏺ Agents(3)
 *       ⎿  ● main             answering the question       21m 04s · 402.7k
 *       ⎿  ◯ worker-1         port the parser              18m 38s · 169.1k
 *       ⎿  ● claude-code      Wiring worker turn slots     18m 04s · 205.8k
 *       ⎿    ◯ codex (+2)     Extracting hook schemas       4m 12s ·  12.4k
 *
 * One local Claude Code session with N subagents reads as a tree even
 * though it is a single session: `parent_agent_id` is what nests them, and
 * a subagent of a subagent is drawn at level two with a `(+N)` on its
 * parent rather than stepping further right for ever.
 *
 * The middle column is the agent's own words about what it is doing right
 * now. It changes length constantly, so it is clipped and never wrapped:
 * the row's height is fixed and nothing below it may move as it updates.
 */
export default function TermAgents({ lines, root, onFocus }: Props) {
  return (
    <section className="agents" aria-label="agents">
      <Call tool="Agents" arg={String(lines.length)} />

      {root && (
        <Cont tone="dim">
          <Row
            mark={fleetMark(root.state)}
            /* The primary agent's own orange, the colour its trail has had
               since before there were any others. */
            hue="var(--orange)"
            name={root.label}
            doing={root.doing}
            state={root.state}
            steps={root.steps}
            elapsed={root.elapsed}
            tokens={root.tokens}
          />
        </Cont>
      )}

      {lines.length === 0 && <Cont tone="dim">{NO_AGENTS}</Cont>}

      {lines.map((line) => (
        <Cont key={line.id} tone="dim">
          <Row
            depth={line.depth}
            mark={fleetMark(line.state)}
            hue={line.hue.css}
            name={`${line.no} ${line.kind}${
              line.collapsed > 0 ? ` (+${line.collapsed})` : ""
            }`}
            doing={line.doing || line.label}
            state={line.state}
            steps={line.steps}
            elapsed={line.elapsed}
            tokens={line.tokens}
            file={line.file}
            onFocus={onFocus ? () => onFocus(line.id) : undefined}
          />
        </Cont>
      ))}

      {lines.length > 0 && (
        <ContMore>
          <span className="dim">
            a mark is the colour that agent&apos;s trail is drawn in
          </span>
        </ContMore>
      )}
    </section>
  );
}

/**
 * One fleet row. Three columns: who, what, and what it has spent. The
 * middle one is the only elastic one and it clips — everything else is
 * `nowrap`, so a long activity string cannot push the numbers onto a
 * second line and make the whole list jump.
 */
function Row({
  depth = 0,
  mark,
  hue,
  name,
  doing,
  state,
  steps,
  elapsed,
  tokens,
  file,
  onFocus,
}: {
  depth?: 0 | 1;
  mark: string;
  hue: string;
  name: string;
  doing: string;
  state: string;
  steps: number;
  elapsed: number | null;
  tokens: number | null;
  file?: string;
  onFocus?: () => void;
}) {
  const spent = [
    state,
    `${steps} step${steps === 1 ? "" : "s"}`,
    elapsed === null ? "" : formatDuration(elapsed),
    /* Null is not zero: an agent nobody counted prints nothing here, and
       never a `0` that would read as "did no work". */
    tokens === null ? "" : `${formatTokens(tokens)} tokens`,
    file ?? "",
  ]
    .filter((part) => part !== "")
    .join(" · ");

  return (
    <span className={`agent-row ${depth > 0 ? "is-child" : ""}`}>
      <span
        className="agent-mark"
        aria-hidden="true"
        style={{ ["--worker-hue" as string]: hue }}
      >
        {mark}
      </span>
      <span className="agent-name">
        {onFocus ? (
          <button
            type="button"
            className="bracket"
            title="show only what this agent has touched, on the graph"
            onClick={onFocus}
          >
            {name}
          </button>
        ) : (
          name
        )}
      </span>
      <span className="agent-doing">{truncate(doing, DOING_MAX)}</span>
      <span className="agent-spent">{spent}</span>
    </span>
  );
}
