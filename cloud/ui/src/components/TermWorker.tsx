import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { shortSha, truncate } from "../format";
import { shortId, workerCalls, type WorkerState } from "../workers";
import { Call, Cont, ContMore } from "./TermLine";

/** How much of a worker's own trail shows before you ask for the rest. */
export const WORKER_TAIL = 3;

interface Props {
  worker: WorkerState;
  /** 1-based spawn position — the `worker-1` in the line. */
  no: number;
  canApply: boolean;
  onApply: () => void;
}

const MARK: Record<string, string> = {
  running: "…",
  reported: "✓",
  applied: "✓",
  closed: "·",
};

/**
 * A worker agent, as one call in the parent's transcript:
 *
 *     ⏺ Agent(worker-1 · Add a one-line docstring to Command.invoke)
 *       ⎿  $ rg -n "def invoke" src/click/core.py
 *          … +7 earlier commands
 *       ⎿  ✓ reported · 2 files · a80d4c46  [apply] [open]
 *
 * Its own activity is folded to the last few lines: the primary transcript
 * is the thing you read, and a worker that ran forty commands must not
 * take forty lines of it.
 */
export default function TermWorker({ worker, no, canApply, onApply }: Props) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  const calls = workerCalls(worker);
  const activity = worker.activity;
  const shown = open ? activity : activity.slice(-WORKER_TAIL);
  const hidden = activity.length - shown.length;
  const files = worker.filesChanged;
  const applied = worker.appliedFiles;
  /* A button whose only possible outcome is a 400 is not an offer. A worker
     that changed nothing has nothing to merge, and one that already landed
     must not invite a re-merge of a patch that is in the tree (HAR-84
     P2-8). */
  const offersApply =
    worker.status !== "closed" && worker.reply !== "" && files.length > 0;

  const summary = [
    `${MARK[worker.status] ?? "·"} ${worker.status}`,
    calls > 0 ? `${calls} step${calls === 1 ? "" : "s"}` : null,
    files.length > 0
      ? `${files.length} file${files.length === 1 ? "" : "s"}`
      : null,
    worker.patchSha ? shortSha(worker.patchSha) : null,
    worker.closedReason ? worker.closedReason : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <section className={`worker is-${worker.status}`} aria-label={`worker ${no}`}>
      <Call
        tool="Agent"
        arg={`worker-${no} · ${truncate(worker.task || shortId(worker.id), 72)}`}
      />

      {hidden > 0 && (
        <ContMore>
          <button type="button" className="cont-more" onClick={() => setOpen(true)}>
            … +{hidden} earlier command{hidden === 1 ? "" : "s"}
          </button>
        </ContMore>
      )}

      {shown.map((item) => (
        <Cont
          key={item.key}
          tone={item.gt ? "gt" : item.isError ? "error" : "dim"}
        >
          {item.gt ? "GroundTruth " : "$ "}
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
            offersApply && (
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
