import type { WorkerTrail } from "../useGraphView";

export interface TurnOption {
  id: string;
  no: number;
}

interface Props {
  turns: readonly TurnOption[];
  selectedTurnId: string | null;
  currentTurnId: string | null;
  onSelectTurn: (turnId: string) => void;
  search: string;
  onSearch: (value: string) => void;
  onSearchEnter: () => void;
  matchCount: number | null;
  zoom: number;
  onFit: () => void;
  labels: boolean;
  onToggleLabels: () => void;
  panelOpen: boolean;
  onTogglePanel: () => void;
  gt: boolean;
  folded: number;
  /** The worker agents, each with the colour its trail is drawn in. */
  workers: readonly WorkerTrail[];
  /** The worker the map is narrowed to, or null for everything at once. */
  isolated: string | null;
  onIsolate: (workerId: string | null) => void;
  /** Hide the whole panel. The conversation is the page; this is a detour. */
  onCollapse: () => void;
}

const SWATCHES: readonly { key: string; label: string }[] = [
  { key: "read", label: "read" },
  { key: "edit", label: "edited" },
  { key: "here", label: "position" },
];

/** One hairline row above the canvas: what you are looking at, and how. */
export default function GraphToolbar({
  turns,
  selectedTurnId,
  currentTurnId,
  onSelectTurn,
  search,
  onSearch,
  onSearchEnter,
  matchCount,
  zoom,
  onFit,
  labels,
  onToggleLabels,
  panelOpen,
  onTogglePanel,
  gt,
  folded,
  workers,
  isolated,
  onIsolate,
  onCollapse,
}: Props) {
  return (
    <div className="bar">
      {turns.length > 0 && (
        <select
          className="bar-select"
          aria-label="Turn shown on the graph"
          value={selectedTurnId ?? ""}
          onChange={(e) => onSelectTurn(e.target.value)}
        >
          {turns.map((turn) => (
            <option key={turn.id} value={turn.id}>
              Turn {turn.no}
              {turn.id === currentTurnId ? " · live" : ""}
            </option>
          ))}
        </select>
      )}

      <label className="bar-search">
        <input
          type="search"
          value={search}
          placeholder="find a file"
          aria-label="Highlight particles by path"
          onChange={(e) => onSearch(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              onSearchEnter();
            }
          }}
        />
        {matchCount !== null && (
          <span className="bar-count cap cap-muted">{matchCount}</span>
        )}
      </label>

      <span className="legend">
        {SWATCHES.map((swatch) => (
          <span className="legend-item" key={swatch.key}>
            <span className={`legend-dot is-${swatch.key}`} />
            <span className="cap">{swatch.label}</span>
          </span>
        ))}
        <span className="legend-item">
          <span className="legend-line is-import" />
          <span className="cap">import</span>
        </span>
        {gt && (
          <span className="legend-item">
            <span className="legend-line is-gt" />
            <span className="cap">GT relation</span>
          </span>
        )}
        <span className="legend-item">
          <span className="legend-line is-cotouch" />
          <span className="cap">co-touch</span>
        </span>
        {/* One chip per worker, in the colour its trail is drawn in. Click
            to narrow the map to what that worker touched. */}
        {workers.map((worker) => (
          <button
            type="button"
            key={worker.id}
            className={`legend-worker ${isolated === worker.id ? "is-on" : ""}`}
            style={{ ["--worker-hue" as string]: worker.css }}
            aria-pressed={isolated === worker.id}
            title={worker.task || `worker ${worker.no}`}
            onClick={() =>
              onIsolate(isolated === worker.id ? null : worker.id)
            }
          >
            <span className="legend-dot is-worker" />
            <span className="cap">worker {worker.no}</span>
          </button>
        ))}
      </span>

      <span className="spacer" />

      {folded > 0 && (
        <span className="cap cap-muted">{folded} files collapsed</span>
      )}

      <button type="button" className="btn-text" onClick={onFit}>
        fit
      </button>
      <span className="cap cap-muted bar-zoom">{Math.round(zoom * 100)}%</span>
      <button
        type="button"
        className={`btn-text ${labels ? "is-on" : ""}`}
        aria-pressed={labels}
        onClick={onToggleLabels}
      >
        labels
      </button>
      <button
        type="button"
        className={`btn-text ${panelOpen ? "is-on" : ""}`}
        aria-pressed={panelOpen}
        onClick={onTogglePanel}
      >
        panel
      </button>
      <button
        type="button"
        className="btn-text"
        aria-label="Hide the graph"
        title="Hide the graph — Ctrl/Cmd + G"
        onClick={onCollapse}
      >
        <span aria-hidden="true">✕</span>
      </button>
    </div>
  );
}
