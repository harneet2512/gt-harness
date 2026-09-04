import { useEffect, useState } from "react";
import type { DiffFile, SessionDiff } from "../api";
import { formatBytes } from "../format";
import type { Particle, Relations } from "../graph";
import type { TrailStep } from "../trail";
import DiffView from "./DiffView";
import FileActivity from "./FileActivity";
import RelationsList from "./RelationsList";

const TABS = ["diff", "relations", "activity"] as const;
type TabId = (typeof TABS)[number];

interface Props {
  particle: Particle | null;
  open: boolean;
  pinned: boolean;
  onTogglePin: () => void;
  onClose: () => void;
  diff: SessionDiff | null;
  diffFile: DiffFile | undefined;
  diffLoading: boolean;
  diffError: string | null;
  relations: Relations;
  cotouch: readonly string[];
  reads: number;
  steps: readonly TrailStep[];
  cutoff: number;
  onScrubTo: (n: number) => void;
  onPick: (path: string) => void;
}

/** The IDE pane: one file, its patch, its relations, and what touched it. */
export default function Inspector({
  particle,
  open,
  pinned,
  onTogglePin,
  onClose,
  diff,
  diffFile,
  diffLoading,
  diffError,
  relations,
  cotouch,
  reads,
  steps,
  cutoff,
  onScrubTo,
  onPick,
}: Props) {
  const [tab, setTab] = useState<TabId>("diff");
  const path = particle?.path ?? "";

  // A file the agent has already changed opens on its patch; an untouched
  // one opens on what it is connected to.
  useEffect(() => {
    if (!particle) return;
    setTab(diffFile ? "diff" : "relations");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [particle?.id]);

  return (
    <aside className={`ins ${open ? "is-open" : ""}`} aria-hidden={!open}>
      {particle && (
        <>
          <header className="ins-head">
            <div className="ins-title">
              <span className="ins-path mono">{path}</span>
              <div className="ins-actions">
                <button
                  type="button"
                  className={`btn-text ${pinned ? "is-on" : ""}`}
                  aria-pressed={pinned}
                  title={pinned ? "Unpin the inspector" : "Keep it open"}
                  onClick={onTogglePin}
                >
                  {pinned ? "pinned" : "pin"}
                </button>
                <button
                  type="button"
                  className="btn-text"
                  aria-label="Close the inspector"
                  onClick={onClose}
                >
                  ✕
                </button>
              </div>
            </div>

            <div className="ins-chips">
              {particle.kind === "dir" ? (
                <span className="chip">{particle.count} files</span>
              ) : (
                <>
                  {particle.lang && <span className="chip">{particle.lang}</span>}
                  <span className="chip is-quiet">
                    {formatBytes(particle.size)}
                  </span>
                </>
              )}
              <span className={`chip ${statusTone(diffFile)}`}>
                {statusLabel(diffFile)}
              </span>
              {reads > 0 && (
                <span className="chip is-hot">reads ×{reads}</span>
              )}
            </div>
          </header>

          <nav className="ins-tabs" role="tablist">
            {TABS.map((id) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={tab === id}
                className={`ins-tab ${tab === id ? "is-active" : ""}`}
                onClick={() => setTab(id)}
              >
                {id}
              </button>
            ))}
          </nav>

          <div className="ins-body">
            {particle.kind === "dir" ? (
              <p className="ins-empty">
                This particle stands for a whole directory. Individual files
                are folded away above the particle cap.
              </p>
            ) : tab === "diff" ? (
              <DiffView
                path={path}
                diff={diff}
                file={diffFile}
                loading={diffLoading}
                error={diffError}
              />
            ) : tab === "relations" ? (
              <RelationsList
                relations={relations}
                cotouch={cotouch}
                onPick={onPick}
              />
            ) : (
              <FileActivity
                path={path}
                steps={steps}
                cutoff={cutoff}
                onScrubTo={onScrubTo}
              />
            )}
          </div>
        </>
      )}
    </aside>
  );
}

function statusLabel(file: DiffFile | undefined): string {
  if (!file) return "unchanged";
  if (file.status === "added") return "added";
  if (file.status === "deleted") return "deleted";
  return `modified +${file.additions} −${file.deletions}`;
}

function statusTone(file: DiffFile | undefined): string {
  if (!file) return "is-quiet";
  return file.status === "deleted" ? "is-bad" : "is-edit";
}
