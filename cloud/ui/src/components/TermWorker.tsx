import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { filesLine } from "../external";
import { shortSha, truncate } from "../format";
import {
  agentKindLabel,
  agentLabel,
  agentState,
  offersApply,
  statusMark,
  workerCalls,
  type WorkerHue,
  type WorkerState,
} from "../workers";
import { Call, Cont, ContMore } from "./TermLine";

/** How much of an agent's own trail shows before you ask for the rest. */
export const WORKER_TAIL = 3;

interface Props {
  worker: WorkerState;
  /** 1-based spawn position — the `worker-1` in the line. */
  no: number;
  /** The colour this agent's trail is drawn in on the graph. */
  hue: WorkerHue;
  /** 0 for a top-level agent, 1 for a subagent drawn under its parent. */
  depth?: 0 | 1;
  canApply: boolean;
  onApply: () => void;
  /** Narrow the graph to what this agent touched. */
  onFocus?: () => void;
}

/**
 * An agent, as one call in the parent's transcript.
 *
 * A worker of ours:
 *
 *     ⏺ Agent(worker-1 · Add a one-line docstring to Command.invoke)
 *       ⎿  $ rg -n "def invoke" src/click/core.py
 *          … +7 earlier commands
 *       ⎿  ✓ reported · 2 files · a80d4c46  [apply] [open]
 *
 * And one we only watch — a Claude Code or Codex session on someone's
 * machine, mirrored onto this stream:
 *
 *     ⏺ Agent(claude-code · fix the flaky test)  external
 *       ⎿  Edit src/click/core.py
 *       ⎿  3 files · src/click/core.py, src/click/parser.py …
 *       ⎿  … working · 12 steps  [focus] [open]
 *
 * Same grammar, same hues, one difference that matters: an external agent
 * has no patch, so it is never offered `[apply]`. Everything it prints was
 * written on a machine we do not control and is rendered as text, clipped.
 */
export default function TermWorker({
  worker,
  no,
  hue,
  depth = 0,
  canApply,
  onApply,
  onFocus,
}: Props) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  const calls = workerCalls(worker);
  const activity = worker.activity;
  const shown = open ? activity : activity.slice(-WORKER_TAIL);
  const hidden = activity.length - shown.length;
  const files = worker.filesChanged;
  const applied = worker.appliedFiles;
  const external = worker.isExternal;
  /* A button whose only possible outcome is a 400 is not an offer. A worker
     that changed nothing has nothing to merge, one that already landed must
     not invite a re-merge of a patch that is in the tree (HAR-84 P2-8), and
     an external agent has no patch at all. */
  const canOfferApply = offersApply(worker);
  /* Where it works. Known only for an external agent — the graph focus is
     offered exactly when there is something to focus on. */
  const seen = worker.files;

  const summary = [
    `${statusMark(worker.status)} ${agentState(worker)}`,
    calls > 0 ? `${calls} step${calls === 1 ? "" : "s"}` : null,
    !external && files.length > 0
      ? `${files.length} file${files.length === 1 ? "" : "s"}`
      : null,
    !external && worker.patchSha ? shortSha(worker.patchSha) : null,
    external && worker.externalCwd ? truncate(worker.externalCwd, 40) : null,
    worker.closedReason ? worker.closedReason : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <section
      className={`worker is-${worker.status} ${external ? "is-external" : ""} ${
        depth > 0 ? "is-child" : ""
      }`}
      aria-label={external ? `external agent ${no}` : `worker ${no}`}
    >
      <Call
        tool="Agent"
        arg={`${agentKindLabel(worker, no)} · ${truncate(agentLabel(worker), 72)}`}
        after={
          external ? (
            <span
              className="worker-ext"
              style={{ ["--worker-hue" as string]: hue.css }}
            >
              {"  "}external
            </span>
          ) : undefined
        }
      />

      {hidden > 0 && (
        <ContMore>
          <button type="button" className="cont-more" onClick={() => setOpen(true)}>
            … +{hidden} earlier {external ? "step" : "command"}
            {hidden === 1 ? "" : "s"}
          </button>
        </ContMore>
      )}

      {shown.map((item) => (
        <Cont
          key={item.key}
          tone={item.gt ? "gt" : item.isError ? "error" : "dim"}
        >
          {/* A worker of ours only ever runs a shell command, so its line
              is a `$`. An external agent names the tool it called, and a
              `$` in front of `Read` would be a lie. */}
          {item.gt ? "GroundTruth " : item.tool ? `${item.tool} ` : "$ "}
          {truncate(item.command, 88)}
        </Cont>
      ))}

      {open && activity.length > WORKER_TAIL && (
        <ContMore>
          <button type="button" className="cont-more" onClick={() => setOpen(false)}>
            … show less
          </button>
        </ContMore>
      )}

      {/* What it is doing right now, in its own words, updated from the
          stream rather than from a poll. It changes length constantly, so
          it is clipped on one line: nothing below it may move as it
          updates. */}
      {worker.doing && (
        <Cont tone="dim">
          <span className="worker-doing">{truncate(worker.doing, 96)}</span>
        </Cont>
      )}

      {/* Where it is working. The whole point of watching an agent we do
          not run: the files, in the order it last touched them. */}
      {seen.length > 0 && <Cont tone="dim">{filesLine(seen, 2)}</Cont>}

      {worker.reply && (
        <Cont tone={worker.status === "applied" ? "ok" : ""}>
          {truncate(worker.reply, 220)}
        </Cont>
      )}

      <Cont tone={worker.status === "applied" ? "ok" : "dim"}>
        {summary}
        <span className="worker-actions">
          {"   "}
          {applied ? (
            <span className="is-ok">✓ applied</span>
          ) : (
            canOfferApply && (
              <button
                type="button"
                className="bracket"
                disabled={worker.applying || !canApply}
                title={
                  canApply
                    ? "3-way merge this worker's diff into this workspace"
                    : "the session has to be idle to take a worker's changes"
                }
                onClick={onApply}
              >
                [{worker.applying ? "applying…" : "apply"}]
              </button>
            )
          )}{" "}
          {seen.length > 0 && onFocus && (
            <button
              type="button"
              className="bracket"
              title="show only what this agent has touched, on the graph"
              onClick={onFocus}
            >
              [focus]
            </button>
          )}{" "}
          <button
            type="button"
            className="bracket"
            onClick={() => navigate(`/sessions/${worker.id}`)}
          >
            [open]
          </button>
        </span>
      </Cont>

      {applied && (
        <Cont tone="ok">
          applied {applied.length} file{applied.length === 1 ? "" : "s"}
          {applied.length > 0 ? `: ${applied.join(", ")}` : ""}
        </Cont>
      )}

      {/* A 409 leaves the parent workspace byte-for-byte as it was. */}
      {worker.conflicts?.map((path) => (
        <Cont key={path} tone="error">
          ✗ conflict: {path}
        </Cont>
      ))}
      {worker.conflicts && worker.conflicts.length > 0 && (
        <ContMore>
          <span className="dim">nothing was applied; the workspace is unchanged</span>
        </ContMore>
      )}

      {worker.applyError && <Cont tone="error">✗ {worker.applyError}</Cont>}
    </section>
  );
}
