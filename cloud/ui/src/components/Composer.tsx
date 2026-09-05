import { useEffect, useRef, useState } from "react";
import { lockedRows } from "../format";
import { parseSlash, slashSuggestions, type ParsedSlash } from "../slash";

interface Props {
  /** `landing` is the blank prompt at `/`; `thread` sits under a transcript. */
  variant?: "landing" | "thread";
  placeholder?: string;
  /** True while the session cannot accept input (creating/closed/failed). */
  locked: boolean;
  lockedReason: string;
  /** True while a turn is in flight — the message lands mid-turn. */
  isRunning?: boolean;
  /**
   * Stop was pressed and the turn has not reached a step boundary yet. It
   * can take as long as the model call in flight (HAR-84 G-14), so the
   * button has to show it was heard rather than invite a second press.
   */
  stopping?: boolean;
  /** A sent message is queued and has not reached the agent yet. */
  steeringQueued?: boolean;
  error: string | null;
  /** Resolves true when the message was accepted; false keeps the draft. */
  onSend: (content: string) => Promise<boolean>;
  onStop?: () => void;
  /** A `/name` the reader ran. Handled by the page, never sent to the agent. */
  onCommand?: (parsed: ParsedSlash) => void;
  /** Bumped to pull focus back here — Ctrl/Cmd+K, and after a command. */
  focusSignal?: number;
  /** Replaces the keyboard hint: the landing puts the repo chip here. */
  footLeft?: React.ReactNode;
  /** Sits before Stop and Send: the gear. */
  footRight?: React.ReactNode;
  autoFocus?: boolean;
}

const MAX_ROWS = 10;
const LANDING_ROWS = 3;

/** Your side of the conversation — and, on `/`, the whole page. */
export default function Composer({
  variant = "thread",
  placeholder,
  locked,
  lockedReason,
  isRunning = false,
  stopping = false,
  steeringQueued = false,
  error,
  onSend,
  onStop,
  onCommand,
  focusSignal = 0,
  footLeft,
  footRight,
  autoFocus = false,
}: Props) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [pick, setPick] = useState(0);
  const [menuOff, setMenuOff] = useState(false);
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const landing = variant === "landing";
  const baseRows = landing ? LANDING_ROWS : 1;

  const suggestions = menuOff ? [] : slashSuggestions(text);
  const canSend = !locked && !busy && text.trim().length > 0;

  useEffect(() => {
    if (focusSignal > 0) areaRef.current?.focus();
  }, [focusSignal]);

  useEffect(() => {
    setPick(0);
  }, [text]);

  function complete(name: string) {
    const next = `/${name} `;
    setText(next);
    setMenuOff(true);
    resize(areaRef.current, next, baseRows);
    areaRef.current?.focus();
  }

  async function submit() {
    if (!canSend) return;
    const content = text.trim();

    /* A known `/name` never reaches the agent: it is this page's own
       vocabulary, and sending it would be a wasted turn. */
    const command = onCommand ? parseSlash(content) : null;
    if (command) {
      setText("");
      resize(areaRef.current, "", baseRows);
      onCommand?.(command);
      areaRef.current?.focus();
      return;
    }

    setBusy(true);
    try {
      if (await onSend(content)) {
        setText("");
        resize(areaRef.current, "", baseRows);
      }
    } finally {
      setBusy(false);
      areaRef.current?.focus();
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (suggestions.length > 0) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const step = e.key === "ArrowDown" ? 1 : suggestions.length - 1;
        setPick((n) => (n + step) % suggestions.length);
        return;
      }
      /* `/help` is a finished command, not a prefix waiting to be completed:
         Enter runs it. Anything shorter completes first. */
      const finished = parseSlash(text) !== null;
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey && !finished)) {
        e.preventDefault();
        complete(suggestions[Math.min(pick, suggestions.length - 1)].name);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setMenuOff(true);
        return;
      }
    }

    if (e.key !== "Enter" || e.shiftKey) return;
    // Do not steal Enter from an active IME composition.
    if (e.nativeEvent.isComposing) return;
    e.preventDefault();
    void submit();
  }

  return (
    <div className={`composer ${landing ? "is-landing" : ""}`}>
      {error && <div className="notice">{error}</div>}

      <div className="composer-box">
        <textarea
          ref={areaRef}
          className="composer-input"
          /* A locked composer's placeholder is the whole explanation — a
             failed session's reason is a sentence, not a word — and a
             one-row box clips it. */
          rows={locked && !landing ? lockedRows(lockedReason) : baseRows}
          value={text}
          disabled={locked}
          placeholder={
            locked ? lockedReason : (placeholder ?? "Message the agent…")
          }
          title={locked ? lockedReason : undefined}
          aria-label={landing ? "What should I work on?" : "Message the agent"}
          autoFocus={autoFocus}
          onChange={(e) => {
            setMenuOff(false);
            setText(e.target.value);
            resize(e.target, e.target.value, baseRows);
          }}
          onKeyDown={onKeyDown}
        />

        {suggestions.length > 0 && (
          <ul className="slash" role="listbox" aria-label="Commands">
            {suggestions.map((command, i) => (
              <li key={command.name}>
                <button
                  type="button"
                  role="option"
                  aria-selected={i === pick}
                  className={`slash-item ${i === pick ? "is-on" : ""}`}
                  onMouseEnter={() => setPick(i)}
                  onClick={() => complete(command.name)}
                >
                  <span className="mono slash-name">
                    /{command.name}
                    {command.arg ? ` ${command.arg}` : ""}
                  </span>
                  <span className="slash-hint">{command.hint}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="composer-foot">
        {footLeft ?? (
          <span className="cap cap-muted">
            {/* Only while something is actually waiting. Bound to `isRunning`
                alone it kept claiming a delivery that had already happened. */}
            {steeringQueued && !locked
              ? "Delivered at the next step"
              : "Enter to send · Shift+Enter newline"}
          </span>
        )}
        <span className="spacer" />
        {footRight}
        {isRunning && onStop && (
          <button
            type="button"
            className="btn-text"
            disabled={stopping}
            title={
              stopping
                ? "Stopping at the end of the model call in flight"
                : "Ctrl/Cmd + Shift + Backspace"
            }
            onClick={onStop}
          >
            {stopping ? "Stopping…" : "Stop"}
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

function resize(el: HTMLTextAreaElement | null, value: string, base: number) {
  if (!el) return;
  const rows = Math.min(MAX_ROWS, value.split("\n").length);
  el.rows = Math.max(base, rows);
}
