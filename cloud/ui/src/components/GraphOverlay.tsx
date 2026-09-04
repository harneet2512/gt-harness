import type { DiffFile } from "../api";
import { formatBytes } from "../format";
import type { Size } from "../useSize";

export interface HoverInfo {
  id: string;
  path: string;
  size: number;
  reads: number;
  edit: DiffFile | undefined;
  x: number;
  y: number;
}

interface Props {
  emptyText: string | null;
  hover: HoverInfo | null;
  size: Size;
}

/** What sits on top of the canvas: the empty note and the hover tooltip. */
export default function GraphOverlay({ emptyText, hover, size }: Props) {
  return (
    <>
      {emptyText && (
        <div className="graph-empty">
          <span className="cap">{emptyText}</span>
        </div>
      )}

      {hover && (
        <div
          className="graph-tip"
          style={{
            left: Math.min(hover.x + 14, Math.max(0, size.width - 250)),
            top: Math.min(hover.y + 14, Math.max(0, size.height - 70)),
          }}
        >
          <span className="graph-tip-path mono">{hover.path}</span>
          <span className="graph-tip-meta">
            {formatBytes(hover.size)}
            {hover.reads > 0 && ` · reads ×${hover.reads}`}
            {hover.edit &&
              ` · edited +${hover.edit.additions} −${hover.edit.deletions}`}
          </span>
        </div>
      )}
    </>
  );
}
