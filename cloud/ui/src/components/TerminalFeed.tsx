import { useEffect, useRef } from "react";

export interface EventEntry {
  id: number;
  type: string;
  data: Record<string, unknown>;
  timestamp: number;
}

interface Props {
  events: EventEntry[];
}

export default function TerminalFeed({ events }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const autoScroll = useRef(true);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const handler = () => {
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      autoScroll.current = atBottom;
    };
    el.addEventListener("scroll", handler);
    return () => el.removeEventListener("scroll", handler);
  }, []);

  useEffect(() => {
    if (autoScroll.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [events.length]);

  return (
    <div className="terminal-feed" ref={containerRef}>
      {events.length === 0 && (
        <div className="event-lifecycle">Waiting for agent to start...</div>
      )}
      {events.map((ev) => (
        <div key={ev.id} className={`event-entry ${eventClass(ev)}`}>
          {renderEvent(ev)}
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

function eventClass(ev: EventEntry): string {
  switch (ev.type) {
    case "assistant":
      return "event-assistant";
    case "tool_call":
      return "event-tool-call";
    case "tool_result":
      return ev.data.is_error ? "event-tool-result error" : "event-tool-result";
    case "steering":
      return "event-steering";
    case "lifecycle":
      return "event-lifecycle";
    case "error":
      return "event-error";
    default:
      return "";
  }
}

function renderEvent(ev: EventEntry) {
  const d = ev.data;
  switch (ev.type) {
    case "assistant":
      return (
        <>
          <span className="event-label">Agent</span>
          {d.content && <div>{String(d.content)}</div>}
          {Array.isArray(d.actions) && d.actions.length > 0 && (
            <div style={{ marginTop: 4, fontSize: 12, color: "var(--text-muted)" }}>
              {(d.actions as string[]).length} action(s) pending
            </div>
          )}
        </>
      );
    case "tool_call":
      return (
        <>
          <span className="event-label">Run</span>
          <span className="command">$ {String(d.command || "")}</span>
        </>
      );
    case "tool_result": {
      const output = String(d.output || "");
      const rc = d.returncode as number;
      return (
        <>
          <span className="event-label">
            {d.is_error ? "Error" : "Output"}
            {rc !== undefined && rc !== 0 && ` (rc=${rc})`}
          </span>
          {output && <pre>{output}</pre>}
        </>
      );
    }
    case "steering":
      return (
        <>
          <span className="event-label">You</span>
          {String(d.content || "")}
        </>
      );
    case "lifecycle":
      return (
        <>
          <span className="event-label">Status</span>
          {String(d.status || d.data && typeof d.data === "object" ? (d.data as Record<string, unknown>).status : JSON.stringify(d))}
          {d.exit_status && ` — ${String(d.exit_status)}`}
          {typeof d.n_calls === "number" && ` (${d.n_calls} steps, $${(d.cost as number || 0).toFixed(3)})`}
        </>
      );
    case "error":
      return (
        <>
          <span className="event-label">Error</span>
          {String(d.error || JSON.stringify(d))}
        </>
      );
    default:
      return <>{JSON.stringify(d)}</>;
  }
}
