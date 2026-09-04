import { useEffect, useState } from "react";
import { getResult, type SessionResult } from "../api";

interface Props {
  sessionId: string;
}

export default function ResultView({ sessionId }: Props) {
  const [result, setResult] = useState<SessionResult | null>(null);
  const [error, setError] = useState("");
  const [receiptOpen, setReceiptOpen] = useState(false);

  useEffect(() => {
    getResult(sessionId)
      .then(setResult)
      .catch((e) => setError(String(e)));
  }, [sessionId]);

  if (error) {
    return <div className="card" style={{ color: "var(--error)" }}>{error}</div>;
  }
  if (!result) {
    return <div className="card">Loading result...</div>;
  }

  return (
    <div className="result-view">
      <div className="result-summary">
        <div>
          <span className="label">Outcome: </span>
          <strong>{result.terminal_outcome || "unknown"}</strong>
        </div>
        {result.receipt && (
          <>
            <div>
              <span className="label">Steps: </span>
              {(result.receipt as Record<string, unknown>).n_calls ?? "-"}
            </div>
            <div>
              <span className="label">Cost: </span>$
              {((result.receipt as Record<string, unknown>).cost as number || 0).toFixed(3)}
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

      {result.receipt && (
        <div className="card">
          <h3
            className={`collapsible-header ${receiptOpen ? "open" : ""}`}
            onClick={() => setReceiptOpen(!receiptOpen)}
          >
            Receipt
          </h3>
          {receiptOpen && (
            <pre style={{ marginTop: 8 }}>
              {JSON.stringify(result.receipt, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
