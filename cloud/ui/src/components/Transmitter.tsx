import { useRef, useState } from "react";

interface Props {
  /** True while the session cannot accept input (creating/closed/failed). */
  locked: boolean;
  lockedReason: string;
  /** True while a turn is in flight — the message lands mid-turn. */
  isRunning: boolean;
  error: string | null;
  /** Resolves true when the message was accepted; false keeps the draft. */
  onSend: (content: string) => Promise<boolean>;
}

const MAX_ROWS = 10;

/** Your side of the radio. */
export default function Transmitter({
  locked,
  lockedReason,
  isRunning,
  error,
  onSend,
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
      const accepted = await onSend(content);
      if (accepted) {
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
      {error && <div className="notice composer-error">{error}</div>}

      <div className="composer-row">
        <textarea
          ref={areaRef}
          className="composer-input"
          rows={1}
          value={text}
          disabled={locked}
          placeholder={locked ? lockedReason : "Radio the surveyor…"}
          aria-label="Message to the surveyor"
          onChange={(e) => {
            setText(e.target.value);
            resize(e.target, e.target.value);
          }}
          onKeyDown={onKeyDown}
        />
        <button
          type="button"
          className="composer-send"
          disabled={!canSend}
          onClick={() => void submit()}
        >
          {busy ? "sending…" : "transmit ▸"}
        </button>
      </div>

      <div className="composer-hints">
        <span className="cap cap-muted">
          enter to send · shift+enter newline
        </span>
        {isRunning && !locked && (
          <span className="cap cap-orange">delivered at the next step</span>
        )}
      </div>
    </div>
  );
}

function resize(el: HTMLTextAreaElement | null, value: string) {
  if (!el) return;
  const rows = Math.min(MAX_ROWS, value.split("\n").length);
  el.rows = Math.max(1, rows);
}
