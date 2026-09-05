import type { Receipt } from "../api";
import {
  costUntracked,
  formatClock,
  formatCost,
  receiptWall,
  shortSha,
} from "../format";

interface Props {
  receipts: readonly Receipt[];
  error: string | null;
  loading: boolean;
  onRefresh: () => void;
}

/** One card per finished turn: the evidence you can keep. */
export default function ReceiptsPanel({
  receipts,
  error,
  loading,
  onRefresh,
}: Props) {
  const newestFirst = [...receipts].reverse();
  /* Every turn costing exactly $0.000 is not a bargain, it is a provider
     that reports no price. Saying "cost" over that column invites the
     reader to conclude the run was free. */
  const untracked = costUntracked(receipts.map((r) => r.cost));

  return (
    <>
      <div className="panel-head">
        <span className="cap">
          {receipts.length} receipt{receipts.length === 1 ? "" : "s"}
        </span>
        <span className="spacer" />
        <button
          type="button"
          className="btn-text"
          onClick={onRefresh}
          disabled={loading}
        >
          {loading ? "…" : "refresh"}
        </button>
      </div>

      {error && <div className="notice">{error}</div>}

      {!error && receipts.length === 0 ? (
        <p className="panel-empty">No turns have finished yet.</p>
      ) : (
        <div className="receipts">
          {newestFirst.map((receipt, i) => (
            <dl className="receipt" key={`${receipt.turn_id}-${i}`}>
              <Line term="turn" value={`${newestFirst.length - i}`} />
              <Line term="model" value={receipt.model} />
              <Line term="steps" value={String(receipt.n_calls)} />
              <Line
                term={untracked ? "cost (untracked)" : "cost"}
                value={formatCost(receipt.cost)}
              />
              <Line term="took" value={receiptWall(receipt)} />
              <Line
                term="finish"
                value={String(receipt.finish_reason)}
                tone={
                  receipt.finish_reason === "error"
                    ? "bad"
                    : receipt.finish_reason === "reply" ||
                        receipt.finish_reason === "submitted"
                      ? "ok"
                      : undefined
                }
              />
              <Line term="patch" value={shortSha(receipt.patch_sha256)} />
              <Line
                term="ground truth"
                value={receipt.gt_status}
                tone={
                  receipt.gt_status === "ready"
                    ? "ok"
                    : receipt.gt_status === "unavailable"
                      ? "bad"
                      : undefined
                }
              />
              <Line term="at" value={formatClock(receipt.finished_at)} />
            </dl>
          ))}
        </div>
      )}
    </>
  );
}

function Line({
  term,
  value,
  tone,
}: {
  term: string;
  value: string;
  tone?: "ok" | "bad";
}) {
  return (
    <div className="receipt-line">
      <dt className="cap">{term}</dt>
      <dd
        className={`mono ${tone === "ok" ? "is-ok" : tone === "bad" ? "is-bad" : ""}`}
        title={value}
      >
        {value}
      </dd>
    </div>
  );
}
