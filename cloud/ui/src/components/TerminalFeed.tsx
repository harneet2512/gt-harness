import { useEffect, useRef } from "react";
import type { SessionEvent } from "../api";

interface Props {
  events: SessionEvent[];
}

export default function TerminalFeed({ events }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const autoScroll = useRef(true);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const handler = () => {
      autoScroll.current =
        el.scrollHeight - el.scrollTop - el.clientHeight < 40;
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
          <EventBody event={ev} />
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

function eventClass(ev: SessionEvent): string {
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

function EventBody({ event }: { event: SessionEvent }) {
  switch (event.type) {
    case "assistant": {
      const { content, actions, n_calls, cost } = event.data;
      return (
        <>
          <span className="event-label">Agent</span>
          {content && <div>{content}</div>}
          {Array.isArray(actions) && actions.length > 0 && (
            <div className="event-meta">
              {actions.length} action{actions.length === 1 ? "" : "s"}:{" "}
              {actions.join(" ; ")}
            </div>
          )}
          <Meta n_calls={n_calls} cost={cost} />
        </>
      );
    }
    case "tool_call": {
      const { command, n_calls } = event.data;
      return (
        <>
          <span className="event-label">Run</span>
          <span className="command">$ {command ?? ""}</span>
          <Meta n_calls={n_calls} />
        </>
      );
    }
    case "tool_result": {
      const { command, output, returncode, is_error } = event.data;
      return (
        <>
          <span className="event-label">
            {is_error ? "Error" : "Output"}
            {typeof returncode === "number" &&
              returncode !== 0 &&
              ` (rc=${returncode})`}
          </span>
          {command && <div className="event-meta">$ {command}</div>}
          {output ? <pre>{output}</pre> : <pre className="event-meta">(no output)</pre>}
        </>
      );
    }
    case "steering":
      return (
        <>
          <span className="event-label">You</span>
          {event.data.content ?? ""}
        </>
      );
    case "lifecycle": {
      const { status, exit_status, n_calls, cost, error } = event.data;
      return (
        <>
          <span className="event-label">Status</span>
          {status}
          {exit_status && ` — ${exit_status}`}
          {error && ` — ${error}`}
          <Meta n_calls={n_calls} cost={cost} />
        </>
      );
    }
    case "error": {
      const { error, traceback } = event.data;
      return (
        <>
          <span className="event-label">Error</span>
          {error ?? "unknown error"}
          {traceback && <pre>{traceback}</pre>}
        </>
      );
    }
    default:
      return <pre>{JSON.stringify(event.data, null, 2)}</pre>;
  }
}

function Meta({ n_calls, cost }: { n_calls?: number; cost?: number }) {
  if (typeof n_calls !== "number" && typeof cost !== "number") return null;
  const parts: string[] = [];
  if (typeof n_calls === "number") parts.push(`${n_calls} steps`);
  if (typeof cost === "number") parts.push(`$${cost.toFixed(3)}`);
  return <div className="event-meta">{parts.join(" | ")}</div>;
}
