import { useEffect, useRef, useState } from "react";
import {
  parseSlash,
  slashSuggestions,
  SLASH_COMMANDS,
  type ParsedSlash,
} from "../slash";
import { BoxBottom, BoxRow, BoxTop } from "./Box";

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
   * hint has to show it was heard rather than invite a second esc.
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
  /** The right half of the hint line: repo, model, GT. */
  status?: string;
  autoFocus?: boolean;
}

const MAX_ROWS = 10;

const HINT = "? for shortcuts · /help · ⏎ send · shift+⏎ newline · esc interrupt";

/**
 * The input, as a terminal draws one:
 *
 *     ╭──────────────────────────────────────────╮
 *     │ > █                                      │
 *     ╰──────────────────────────────────────────╯
 *     ? for shortcuts · /help · ⏎ send      click@main · GT advisory
 *
 * The frame is box-drawing characters, the caret is the accent, and the
 * hint line under it is the only chrome on the page.
 */
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
  status,
  autoFocus = false,
}: Props) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [pick, setPick] = useState(0);
  const [menuOff, setMenuOff] = useState(false);
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const landing = variant === "landing";

  /* `?` on an empty line is the whole menu — the shortcut the hint names. */
  const suggestions = menuOff
    ? []
    : text === "?"
      ? SLASH_COMMANDS
      : slashSuggestions(text);
  const canSend = !locked && !busy && text.trim().length > 0;

  useEffect(() => {
    if (focusSignal > 0) areaRef.current?.focus();
  }, [focusSignal]);

  useEffect(() => {
    setPick(0);
  }, [text]);

  function complete(name: string) {
    const lines = text.split("\n");
    lines[lines.length - 1] = `/${name} `;
    const next = lines.join("\n");
    setText(next);
    setMenuOff(true);
    resize(areaRef.current, next);
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
      resize(areaRef.current, "");
      onCommand?.(command);
      areaRef.current?.focus();
      return;
    }

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

    /* esc interrupts the turn — the hint line says so, and it is the only
       way to stop one now that the header is gone. */
    if (e.key === "Escape" && isRunning && onStop) {
      e.preventDefault();
      onStop();
      return;
    }

    if (e.key !== "Enter" || e.shiftKey) return;
    // Do not steal Enter from an active IME composition.
    if (e.nativeEvent.isComposing) return;
    e.preventDefault();
    void submit();
  }

  const hint = locked
    ? lockedReason
    : stopping
      ? "stopping at the end of the model call in flight…"
      : steeringQueued
        ? "delivered at the next step"
        : busy
          ? "sending…"
          : HINT;

  return (
    <div className={`composer ${landing ? "is-landing" : ""}`}>
      {error && (
        <div className="cont is-error">
          <span className="cont-mark" aria-hidden="true">
            ⎿
          </span>
          <span className="cont-body">{error}</span>
        </div>
      )}

      <div className="composer-box">
        <BoxTop />
        <BoxRow>
          <span className="composer-caret" aria-hidden="true">
            &gt;
          </span>{" "}
          <textarea
            ref={areaRef}
            className="composer-input"
            rows={1}
            value={text}
            disabled={locked}
            placeholder={
              locked ? lockedReason : (placeholder ?? "what should I work on?")
            }
            title={locked ? lockedReason : undefined}
            aria-label={landing ? "What should I work on?" : "Message the agent"}
            autoFocus={autoFocus}
            onChange={(e) => {
              setMenuOff(false);
              setText(e.target.value);
              resize(e.target, e.target.value);
            }}
            onKeyDown={onKeyDown}
          />
        </BoxRow>
        <BoxBottom />

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
                  <span className="slash-name">
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

      <div className="termfoot">
        <span>{hint}</span>
        {status && <span className="termfoot-right">{status}</span>}
      </div>
    </div>
  );
}

function resize(el: HTMLTextAreaElement | null, value: string) {
  if (!el) return;
  el.rows = Math.max(1, Math.min(MAX_ROWS, value.split("\n").length));
}
