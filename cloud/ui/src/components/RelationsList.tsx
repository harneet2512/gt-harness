import type { GraphEdge } from "../api";
import type { Relations } from "../graph";

interface Props {
  relations: Relations;
  /** Files the agent touched alongside this one, in the same step or turn. */
  cotouch: readonly string[];
  onPick: (path: string) => void;
}

/** Who this file reaches, who reaches it, and who it travelled with. */
export default function RelationsList({ relations, cotouch, onPick }: Props) {
  const gt = [...relations.gtOut, ...relations.gtIn];
  const empty =
    relations.imports.length === 0 &&
    relations.importedBy.length === 0 &&
    gt.length === 0 &&
    cotouch.length === 0;

  if (empty) {
    return <p className="ins-empty">No relations recorded for this file.</p>;
  }

  return (
    <div className="rel">
      <Group
        title="imports"
        rows={relations.imports.map((edge) => row(edge, edge.target))}
        onPick={onPick}
      />
      <Group
        title="imported by"
        rows={relations.importedBy.map((edge) => row(edge, edge.source))}
        onPick={onPick}
      />
      <Group
        title="ground truth"
        rows={[
          ...relations.gtOut.map((edge) => row(edge, edge.target, "→")),
          ...relations.gtIn.map((edge) => row(edge, edge.source, "←")),
        ]}
        onPick={onPick}
      />
      <Group
        title="co-touched with"
        rows={cotouch.map((path) => ({ path, kind: "", weight: 0, arrow: "" }))}
        onPick={onPick}
      />
    </div>
  );
}

interface Row {
  path: string;
  kind: string;
  weight: number;
  arrow: string;
}

function row(edge: GraphEdge, path: string, arrow = ""): Row {
  return { path, kind: String(edge.kind), weight: edge.weight, arrow };
}

function Group({
  title,
  rows,
  onPick,
}: {
  title: string;
  rows: readonly Row[];
  onPick: (path: string) => void;
}) {
  if (rows.length === 0) return null;
  return (
    <section className="rel-group">
      <h4 className="cap">
        {title}
        <span className="rel-n">{rows.length}</span>
      </h4>
      <ul>
        {rows.map((item, i) => (
          <li key={`${item.path}-${i}`}>
            <button
              type="button"
              className="rel-row"
              onClick={() => onPick(item.path)}
            >
              {item.arrow && (
                <span className="rel-arrow" aria-hidden="true">
                  {item.arrow}
                </span>
              )}
              <span className="rel-path mono">{item.path}</span>
              {item.kind && <span className="rel-kind cap">{item.kind}</span>}
              {item.weight > 1 && (
                <span className="rel-weight mono">×{item.weight}</span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
