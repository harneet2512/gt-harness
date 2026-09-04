import type { Receipt } from "../api";
import {
  formatClock,
  formatCost,
  formatDuration,
  shortSha,
  toEpochSeconds,
} from "../format";

interface Props {
  receipts: readonly Receipt[];
  error: string | null;
  loading: boolean;
  onRefresh: () => void;
}

/** A survey stamp per finished turn: the evidence you can keep. */
export default function ReceiptsPanel({
  receipts,
  error,
  loading,
  onRefresh,
}: Props) {
  const newestFirst = [...receipts].reverse();

  return (
    <>
      <div className="inst-head">
        <span className="cap">
          {receipts.length} stamp{receipts.length === 1 ? "" : "s"}
        </span>
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
        <p className="inst-empty">No turns have finished yet.</p>
      ) : (
        <ul className="receipts">
          {newestFirst.map((receipt, i) => (
            <li className="receipt" key={`${receipt.turn_id}-${i}`}>
              <svg
                className="receipt-stamp"
                width="46"
                height="46"
                viewBox="0 0 46 46"
                aria-hidden="true"
              >
                <circle cx="23" cy="23" r="21" strokeWidth="1" />
                <circle
                  cx="23"
                  cy="23"
                  r="17"
                  strokeWidth="1"
                  strokeDasharray="2 3"
                />
                <text x="23" y="28">
                  GT
                </text>
              </svg>

              <dl className="receipt-body">
                <Line
                  term="turn"
                  value={`№${newestFirst.length - i}`}
                />
                <Line term="steps" value={String(receipt.n_calls)} />
                <Line term="cost" value={formatCost(receipt.cost)} />
                <Line term="took" value={duration(receipt)} />
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
                <Line term="model" value={receipt.model} />
                <Line term="at" value={formatClock(receipt.finished_at)} />
              </dl>
            </li>
          ))}
        </ul>
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
      <dt>{term}</dt>
      <dd
        className={tone === "ok" ? "is-ok" : tone === "bad" ? "is-bad" : ""}
        title={value}
      >
        {value}
      </dd>
    </div>
  );
}

function duration(receipt: Receipt): string {
  const start = toEpochSeconds(receipt.started_at);
  const end = toEpochSeconds(receipt.finished_at);
  if (start === null || end === null) return "—";
  return formatDuration(end - start);
}
