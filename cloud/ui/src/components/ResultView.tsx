import { useEffect, useState } from "react";
import { ApiError, getResult, type SessionResult } from "../api";

interface Props {
  sessionId: string;
}

const RETRY_MS = 2000;

export default function ResultView({ sessionId }: Props) {
  const [result, setResult] = useState<SessionResult | null>(null);
  const [error, setError] = useState("");
  const [receiptOpen, setReceiptOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const load = () => {
      getResult(sessionId)
        .then((r) => {
          if (!cancelled) setResult(r);
        })
        .catch((e: unknown) => {
          if (cancelled) return;
          // 409 = the run is still finalizing; the record appears shortly.
          if (e instanceof ApiError && (e.status === 409 || e.status === 404)) {
            retry = setTimeout(load, RETRY_MS);
            return;
          }
          setError(e instanceof Error ? e.message : String(e));
        });
    };

    load();
    return () => {
      cancelled = true;
      if (retry) clearTimeout(retry);
    };
  }, [sessionId]);

  if (error) {
    return (
      <div className="card" style={{ color: "var(--error)" }}>
        {error}
      </div>
    );
  }
  if (!result) {
    return <div className="card">Loading result...</div>;
  }

  const receipt = result.receipt;
  const steps = receipt?.n_calls;
  const cost = receipt?.cost;

  return (
    <div className="result-view">
      <div className="result-summary">
        <div>
          <span className="label">Outcome: </span>
          <strong>{result.terminal_outcome || "unknown"}</strong>
        </div>
        {receipt && (
          <>
            <div>
              <span className="label">Steps: </span>
              {typeof steps === "number" ? steps : "-"}
            </div>
            <div>
              <span className="label">Cost: </span>$
              {typeof cost === "number" ? cost.toFixed(3) : "0.000"}
            </div>
          </>
        )}
      </div>

      {result.patch ? (
        <div className="card">
          <h3>Patch</h3>
          <pre>{result.patch}</pre>
        </div>
      ) : (
        <div className="card" style={{ color: "var(--text-muted)" }}>
          No patch produced.
        </div>
      )}

      {receipt && (
        <div className="card">
          <h3
            className={`collapsible-header ${receiptOpen ? "open" : ""}`}
            onClick={() => setReceiptOpen(!receiptOpen)}
          >
            Receipt
          </h3>
          {receiptOpen && (
            <pre style={{ marginTop: 8 }}>{JSON.stringify(receipt, null, 2)}</pre>
          )}
        </div>
      )}
    </div>
  );
}
