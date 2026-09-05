import { useRef, useState } from "react";

interface Props {
  /** True while the session cannot accept input (creating/closed/failed). */
  locked: boolean;
  lockedReason: string;
  /** True while a turn is in flight — the message lands mid-turn. */
  isRunning: boolean;
  /** A sent message is queued and has not reached the agent yet. */
  steeringQueued: boolean;
  error: string | null;
  /** Resolves true when the message was accepted; false keeps the draft. */
  onSend: (content: string) => Promise<boolean>;
  onStop: () => void;
}

const MAX_ROWS = 10;

/** Your side of the conversation. */
export default function Composer({
  locked,
  lockedReason,
  isRunning,
  steeringQueued,
  error,
  onSend,
  onStop,
}: Props) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const areaRef = useRef<HTMLTextAreaElement>(null);

  const canSend = !locked && !busy && text.trim().length > 0;

  async function submit() {
    if (!canSend) return;
    const content = text.trim();
    setBusy(true);
    try {
      if (await onSend(content)) {
        setText("");
        resize(areaRef.current, "");
      }
    } finally {
      setBusy(false);
      areaRef.current?.focus();
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key !== "Enter" || e.shiftKey) return;
    // Do not steal Enter from an active IME composition.
    if (e.nativeEvent.isComposing) return;
    e.preventDefault();
    void submit();
  }

  return (
    <div className="composer">
      {error && <div className="notice">{error}</div>}

      <textarea
        ref={areaRef}
        className="composer-input"
        rows={1}
        value={text}
        disabled={locked}
        placeholder={locked ? lockedReason : "Message the agent…"}
        aria-label="Message the agent"
        onChange={(e) => {
          setText(e.target.value);
          resize(e.target, e.target.value);
        }}
        onKeyDown={onKeyDown}
      />

      <div className="composer-foot">
        <span className="cap cap-muted">
          {/* Only while something is actually waiting. Bound to `isRunning`
              alone it kept claiming a delivery that had already happened. */}
          {steeringQueued && !locked
            ? "Delivered at the next step"
            : "Enter to send · Shift+Enter newline"}
        </span>
        <span className="spacer" />
        {isRunning && (
          <button
            type="button"
            className="btn-text"
            title="Ctrl/Cmd + Shift + Backspace"
            onClick={onStop}
          >
            Stop
          </button>
        )}
        <button
          type="button"
          className="composer-send"
          disabled={!canSend}
          onClick={() => void submit()}
        >
          {busy ? "sending…" : "Send"}
          <span className="composer-arrow" aria-hidden="true">
            →
          </span>
        </button>
      </div>
    </div>
  );
}

function resize(el: HTMLTextAreaElement | null, value: string) {
  if (!el) return;
  const rows = Math.min(MAX_ROWS, value.split("\n").length);
  el.rows = Math.max(1, rows);
}
