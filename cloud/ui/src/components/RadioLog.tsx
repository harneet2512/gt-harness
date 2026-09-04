import type { ReactNode } from "react";
import type { Message, Session } from "../api";
import {
  orphanSteering,
  type ChatState,
  type ThreadGroup,
  type TurnGroup,
} from "../chatState";
import { formatClock, formatCost, shortSha } from "../format";
import type { StepSteering, SurveyStep } from "../survey";
import { useAutoScroll } from "../useAutoScroll";
import ExpeditionMenu from "./ExpeditionMenu";
import Prose from "./Prose";
import RadioIndicator, { type RadioMode } from "./RadioIndicator";
import TransmissionStrip from "./TransmissionStrip";
import Transmitter from "./Transmitter";

interface Props {
  sessionId: string | null;
  session: Session | null;
  chat: ChatState;
  groups: ThreadGroup[];
  stepsByTurn: Record<string, SurveyStep[]>;
  edited: ReadonlySet<string>;
  selectedTurnId: string | null;
  currentTurnId: string | null;
  onSelectTurn: (turnId: string) => void;
  /** Steps visible at the scrub position, for the selected turn only. */
  cutoff: number;
  running: boolean;
  terminal: boolean;
  radioMode: RadioMode;
  phase: string | null;
  elapsed: number | null;
  /** Epoch seconds, ticking only while a turn runs. */
  now: number;
  locked: boolean;
  lockedReason: string;
  sendError: string | null;
  onSend: (content: string) => Promise<boolean>;
  onStop: () => void;
  onClose: () => void;
  onContinue: () => void;
  /** Shown on narrow screens, where the field is a toggle. */
  fieldToggle: ReactNode;
  shrunk: boolean;
}

/** The left column: the radio, the conversation, and the transmitter. */
export default function RadioLog({
  sessionId,
  session,
  chat,
  groups,
  stepsByTurn,
  edited,
  selectedTurnId,
  currentTurnId,
  onSelectTurn,
  cutoff,
  running,
  terminal,
  radioMode,
  phase,
  elapsed,
  now,
  locked,
  lockedReason,
  sendError,
  onSend,
  onStop,
  onClose,
  onContinue,
  fieldToggle,
  shrunk,
}: Props) {
  const scroll = useAutoScroll();

  let turnNo = 0;

  return (
    <section className={`log ${shrunk ? "is-shrunk" : ""}`}>
      <div className="log-bar">
        <ExpeditionMenu activeId={sessionId} active={session} />
        {fieldToggle}
      </div>

      <div className="log-status">
        <RadioIndicator mode={radioMode} phase={phase} elapsed={elapsed} />
        <span className="spacer" />
        {running && (
          <button type="button" className="btn-text is-hot" onClick={onStop}>
            cut transmission
          </button>
        )}
        {sessionId && !terminal && (
          <button type="button" className="btn-text" onClick={onClose}>
            close
          </button>
        )}
      </div>

      <div className="log-wrap">
        <div className="log-scroll" ref={scroll.ref}>
          {!sessionId && (
            <p className="log-empty">
              Pick an expedition or start a new one.
            </p>
          )}

          {sessionId && groups.length === 0 && (
            <p className="log-empty">
              Radio the surveyor. Every message walks the terrain once.
            </p>
          )}

          {groups.map((group) => {
            if (group.kind === "note") {
              return group.message.role === "user" ? (
                <div className="exchange arrives" key={group.message.id}>
                  <Said message={group.message} />
                </div>
              ) : (
                <p className="log-note arrives" key={group.message.id}>
                  {group.message.content}
                </p>
              );
            }

            turnNo += 1;
            const selected = group.turnId === selectedTurnId;
            const steps = stepsByTurn[group.turnId] ?? [];
            return (
              <Exchange
                key={group.turnId}
                no={turnNo}
                group={group}
                chat={chat}
                steps={steps}
                edited={edited}
                selected={selected}
                running={running && group.turnId === currentTurnId}
                cutoff={selected ? cutoff : steps.length}
                now={now}
                onSelect={() => onSelectTurn(group.turnId)}
                onContinue={onContinue}
              />
            );
          })}
        </div>

        {scroll.detached && (
          <button type="button" className="jump" onClick={scroll.jumpToLatest}>
            Jump to latest ↓
          </button>
        )}
      </div>

      <Transmitter
        locked={locked || !sessionId}
        lockedReason={
          sessionId ? lockedReason : "Pick an expedition to start talking."
        }
        isRunning={running}
        error={sendError}
        onSend={onSend}
      />
    </section>
  );
}

function Exchange({
  no,
  group,
  chat,
  steps,
  edited,
  selected,
  running,
  cutoff,
  now,
  onSelect,
  onContinue,
}: {
  no: number;
  group: TurnGroup;
  chat: ChatState;
  steps: SurveyStep[];
  edited: ReadonlySet<string>;
  selected: boolean;
  running: boolean;
  cutoff: number;
  now: number;
  onSelect: () => void;
  onContinue: () => void;
}) {
  const turn = chat.turns[group.turnId];
  const endedAt = turn?.finishedAt ?? (running ? now : null);
  const elapsed =
    turn?.startedAt != null && endedAt != null ? endedAt - turn.startedAt : null;

  const extraSteering: StepSteering[] = orphanSteering(group, turn).map((m) => ({
    key: m.id,
    content: m.content,
  }));

  return (
    <section className="exchange arrives">
      {group.prompt && <Said message={group.prompt} />}

      <TransmissionStrip
        no={no}
        steps={steps}
        edited={edited}
        running={running}
        selected={selected}
        cutoff={cutoff}
        cost={turn?.cost ?? null}
        elapsed={elapsed}
        extraSteering={extraSteering}
        onSelect={onSelect}
      />

      {group.replies.map((message) => (
        <Heard key={message.id} message={message} onContinue={onContinue} />
      ))}
      {group.notes.map((message) => (
        <p className="log-note" key={message.id}>
          {message.content}
        </p>
      ))}
    </section>
  );
}

function Said({ message }: { message: Message }) {
  return (
    <div className={`said ${message.meta.pending ? "is-pending" : ""}`}>
      <span className="said-mark" aria-hidden="true">
        you ▸
      </span>
      <p className="said-text">{message.content}</p>
    </div>
  );
}

function Heard({
  message,
  onContinue,
}: {
  message: Message;
  onContinue: () => void;
}) {
  const { finish_reason, n_calls, cost, patch_sha256, files_changed } =
    message.meta;

  return (
    <div className="heard">
      <Prose text={message.content} />

      <div className="heard-tail">
        {finish_reason === "question" && (
          <span className="cap cap-orange">waiting for you</span>
        )}
        {finish_reason === "stopped" && (
          <span className="cap">transmission cut</span>
        )}
        {finish_reason === "error" && (
          <span className="cap" style={{ color: "var(--error)" }}>
            turn failed
          </span>
        )}
        {finish_reason === "step_limit" && (
          <>
            <span className="cap">step limit reached</span>
            <button
              type="button"
              className="link"
              onClick={(e) => {
                e.stopPropagation();
                onContinue();
              }}
            >
              keep going
            </button>
          </>
        )}

        <span className="spacer" />
        <span className="mono muted">
          {[
            typeof n_calls === "number" ? `${n_calls} steps` : null,
            typeof cost === "number" ? formatCost(cost) : null,
            Array.isArray(files_changed) && files_changed.length > 0
              ? `${files_changed.length} file${files_changed.length === 1 ? "" : "s"}`
              : null,
            patch_sha256 ? shortSha(patch_sha256) : null,
            formatClock(message.created_at),
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </div>
    </div>
  );
}
