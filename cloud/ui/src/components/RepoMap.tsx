import { useMemo, useState } from "react";
import type { TreeFile } from "../api";
import { attentionAlpha, type Attention, type Waypoint } from "../survey";
import {
  buildTree,
  formatBytes,
  layoutTree,
  type MapCell,
} from "../treemap";
import { useSize } from "../useSize";

interface Props {
  files: readonly TreeFile[];
  /** Shown over a faint grid when there is no terrain yet. */
  emptyText: string;
  attention: ReadonlyMap<string, Attention>;
  /** Step the replay is at, which drives the attention decay. */
  currentStep: number;
  edited: ReadonlySet<string>;
  trail: readonly Waypoint[];
  position: string | null;
  running: boolean;
  search: string;
  focusPath: string | null;
  onPickFile: (path: string | null) => void;
  /** Directories the reader has folded shut. */
  collapsed: ReadonlySet<string>;
  onToggleDir: (path: string) => void;
}

/** Waypoint circles beyond this would bury the map in numbers. */
const MAX_WAYPOINTS = 24;
const LABEL_MIN_W = 44;
const LABEL_MIN_H = 13;
const CHAR_W = 6.3;

/**
 * The terrain. Every tracked file is a cell sized by sqrt(bytes); the
 * surveyor's reads glow orange and fade, edits stay teal, and the trail
 * is the path walked this turn.
 */
export default function RepoMap({
  files,
  emptyText,
  attention,
  currentStep,
  edited,
  trail,
  position,
  running,
  search,
  focusPath,
  onPickFile,
  collapsed,
  onToggleDir,
}: Props) {
  const [wrapRef, size] = useSize<HTMLDivElement>();
  const [hover, setHover] = useState<MapCell | null>(null);
  const [pointer, setPointer] = useState({ x: 0, y: 0 });

  const root = useMemo(() => buildTree(files, collapsed), [files, collapsed]);
  const layout = useMemo(
    () => layoutTree(root, size.width, size.height),
    [root, size.width, size.height],
  );

  const query = search.trim().toLowerCase();

  /** A folded or collapsed file still has an ancestor on screen. */
  const cellFor = (path: string): MapCell | undefined => {
    let probe = path;
    for (;;) {
      const found = layout.byPath.get(probe);
      if (found) return found;
      const cut = probe.lastIndexOf("/");
      if (cut === -1) return layout.byPath.get("…");
      probe = probe.slice(0, cut);
    }
  };

  const points = useMemo(() => {
    const out: { n: number; x: number; y: number }[] = [];
    for (const stop of trail) {
      const cell = cellFor(stop.path);
      if (!cell || cell.w <= 0) continue;
      const x = cell.x + cell.w / 2;
      const y = cell.y + cell.h / 2;
      const last = out[out.length - 1];
      if (last && Math.abs(last.x - x) < 0.5 && Math.abs(last.y - y) < 0.5) {
        out[out.length - 1] = { n: stop.n, x, y };
        continue;
      }
      out.push({ n: stop.n, x, y });
    }
    return out;
    // `cellFor` is a thin reader over `layout`, which is the real input.
  }, [trail, layout]);

  const trailLength = useMemo(() => {
    let total = 0;
    for (let i = 1; i < points.length; i += 1) {
      total += Math.hypot(
        points[i].x - points[i - 1].x,
        points[i].y - points[i - 1].y,
      );
    }
    return Math.max(1, Math.round(total));
  }, [points]);

  const here = position ? cellFor(position) : undefined;
  const empty = files.length === 0;

  return (
    <div
      className="map-pane"
      ref={wrapRef}
      onMouseMove={(e) => {
        const box = e.currentTarget.getBoundingClientRect();
        setPointer({ x: e.clientX - box.left, y: e.clientY - box.top });
      }}
      onMouseLeave={() => setHover(null)}
    >
      {empty ? (
        <div className="map-blank">
          <span>{emptyText}</span>
        </div>
      ) : (
        <svg
          className="map-svg"
          width={size.width}
          height={size.height}
          role="img"
          aria-label={`Map of ${files.length} tracked files`}
        >
          {layout.cells.map((cell) => (
            <Cell
              key={cell.path}
              cell={cell}
              alpha={alphaFor(cell, attention, currentStep)}
              edited={cell.kind === "file" && edited.has(cell.path)}
              found={
                query.length > 0 &&
                cell.kind === "file" &&
                cell.path.toLowerCase().includes(query)
              }
              focused={cell.path === focusPath}
              onEnter={() => setHover(cell)}
              onToggle={() => onToggleDir(cell.path)}
              onPick={() =>
                onPickFile(cell.path === focusPath ? null : cell.path)
              }
            />
          ))}

          {points.length > 1 && (
            <polyline
              key={`trail-${points.length}-${trailLength}`}
              className="trail-line"
              points={points.map((p) => `${p.x},${p.y}`).join(" ")}
              strokeDasharray={trailLength}
              strokeDashoffset={trailLength}
            />
          )}

          {points.slice(-MAX_WAYPOINTS).map((p) => (
            <g className="trail-stop" key={`${p.n}-${p.x}-${p.y}`}>
              <circle cx={p.x} cy={p.y} r={7} />
              <text x={p.x} y={p.y + 3}>
                {p.n}
              </text>
            </g>
          ))}

          {here && here.w > 0 && (
            <g>
              {running && (
                <circle
                  className="map-ping"
                  cx={here.x + here.w / 2}
                  cy={here.y + here.h / 2}
                  r={8}
                />
              )}
              <circle
                className="map-here-dot"
                cx={here.x + here.w / 2}
                cy={here.y + here.h / 2}
                r={3.5}
              />
            </g>
          )}
        </svg>
      )}

      {hover && (
        <div
          className="map-tip"
          style={{
            left: Math.min(pointer.x + 14, Math.max(0, size.width - 300)),
            top: Math.min(pointer.y + 14, Math.max(0, size.height - 70)),
          }}
        >
          <div>{hover.path || hover.name}</div>
          <div className="tip-meta">
            {hover.kind === "file"
              ? formatBytes(hover.bytes)
              : `${hover.count} files · ${formatBytes(hover.bytes)}`}
          </div>
          {readsOf(hover, attention) > 0 && (
            <div className="tip-read">read ×{readsOf(hover, attention)}</div>
          )}
          {hover.kind === "file" && edited.has(hover.path) && (
            <div className="tip-edit">edited</div>
          )}
        </div>
      )}
    </div>
  );
}

function readsOf(
  cell: MapCell,
  attention: ReadonlyMap<string, Attention>,
): number {
  return attention.get(cell.path)?.reads ?? 0;
}

function alphaFor(
  cell: MapCell,
  attention: ReadonlyMap<string, Attention>,
  currentStep: number,
): number {
  const seen = attention.get(cell.path);
  return seen ? attentionAlpha(seen.last, currentStep) : 0;
}

function Cell({
  cell,
  alpha,
  edited,
  found,
  focused,
  onEnter,
  onToggle,
  onPick,
}: {
  cell: MapCell;
  alpha: number;
  edited: boolean;
  found: boolean;
  focused: boolean;
  onEnter: () => void;
  onToggle: () => void;
  onPick: () => void;
}) {
  const isDir = cell.kind === "dir";
  const collapsedDir = isDir && cell.leaf;

  const classes = [
    "map-cell",
    isDir ? "map-dir" : cell.kind === "overflow" ? "map-overflow" : "map-file",
    collapsedDir ? "is-collapsed" : "",
    alpha > 0 ? "is-hit" : "",
    edited ? "is-edit" : "",
    found ? "is-found" : "",
    focused ? "is-focused" : "",
  ]
    .filter(Boolean)
    .join(" ");

  const fill = edited
    ? "rgba(15, 118, 110, 0.16)"
    : alpha > 0
      ? `rgba(228, 87, 46, ${(0.05 + 0.25 * alpha).toFixed(3)})`
      : undefined;

  const labelY = cell.hasLabel
    ? cell.y + 10
    : cell.y + cell.h / 2 + 3.5;
  const room = Math.floor((cell.w - 8) / CHAR_W);
  const showLabel =
    (cell.hasLabel || (cell.w >= LABEL_MIN_W && cell.h >= LABEL_MIN_H)) &&
    room >= 3;

  return (
    <g
      className={classes}
      onMouseEnter={onEnter}
      onClick={
        isDir ? (collapsedDir ? onToggle : undefined) : cell.kind === "file" ? onPick : undefined
      }
    >
      <rect
        x={cell.x + 0.5}
        y={cell.y + 0.5}
        width={Math.max(0, cell.w - 1)}
        height={Math.max(0, cell.h - 1)}
        fill={isDir && !collapsedDir ? undefined : fill}
      />
      {showLabel && (
        <text
          className={`map-label ${
            isDir ? "map-dir-label" : "map-file-label"
          }`}
          x={cell.x + 4}
          y={labelY}
          onClick={isDir ? onToggle : undefined}
        >
          {clip(collapsedDir ? `${cell.name}/ ${cell.count}` : cell.name, room)}
        </text>
      )}
    </g>
  );
}

function clip(text: string, room: number): string {
  if (text.length <= room) return text;
  return `${text.slice(0, Math.max(1, room - 1))}…`;
}
