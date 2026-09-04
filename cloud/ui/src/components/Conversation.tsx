import type { Message, Session } from "../api";
import {
  orphanSteering,
  type ChatState,
  type ThreadGroup,
  type TurnGroup,
} from "../chatState";
import { formatClock, formatCost, shortSha } from "../format";
import type { StepSteering, TrailStep } from "../trail";
import { useAutoScroll } from "../useAutoScroll";
import Composer from "./Composer";
import Prose from "./Prose";
import SessionSwitcher from "./SessionSwitcher";
import StatusLine, { type StatusMode } from "./StatusLine";
import TransmissionStrip from "./TransmissionStrip";

interface Props {
  sessionId: string | null;
  session: Session | null;
  chat: ChatState;
  groups: ThreadGroup[];
  stepsByTurn: Record<string, TrailStep[]>;
  edited: ReadonlySet<string>;
  selectedTurnId: string | null;
  currentTurnId: string | null;
  onSelectTurn: (turnId: string) => void;
  /** Steps visible at the scrub position, for the selected turn only. */
  cutoff: number;
  running: boolean;
  mode: StatusMode;
  phase: string | null;
  elapsed: number | null;
  liveSteps: number;
  /** Epoch seconds, ticking only while a turn runs. */
  now: number;
  locked: boolean;
  lockedReason: string;
  sendError: string | null;
  onSend: (content: string) => Promise<boolean>;
  onStop: () => void;
  onContinue: () => void;
}

/** The left column: who you are talking to, what was said, and the composer. */
export default function Conversation({
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
  mode,
  phase,
  elapsed,
  liveSteps,
  now,
  locked,
  lockedReason,
  sendError,
  onSend,
  onStop,
  onContinue,
}: Props) {
  const scroll = useAutoScroll();
  let turnNo = 0;

  return (
    <section className="talk">
      <header className="talk-head">
        <SessionSwitcher activeId={sessionId} active={session} />
        <StatusLine
          mode={mode}
          phase={phase}
          elapsed={elapsed}
          steps={liveSteps}
        />
      </header>

      <div className="talk-wrap">
        <div className="talk-scroll" ref={scroll.ref}>
          {!sessionId && (
            <p className="talk-empty">Pick a session or start a new one.</p>
          )}

          {sessionId && groups.length === 0 && (
            <p className="talk-empty">
              Message the agent. Every message walks the graph once.
            </p>
          )}

          {groups.map((group) => {
            if (group.kind === "note") {
              return group.message.role === "user" ? (
                <div className="exchange arrives" key={group.message.id}>
                  <Said message={group.message} />
                </div>
              ) : (
                <p className="talk-note arrives" key={group.message.id}>
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

      <Composer
        locked={locked || !sessionId}
        lockedReason={
          sessionId ? lockedReason : "Pick a session to start talking."
        }
        isRunning={running}
        error={sendError}
        onSend={onSend}
        onStop={onStop}
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
  steps: TrailStep[];
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
        <Reply key={message.id} message={message} onContinue={onContinue} />
      ))}
      {group.notes.map((message) => (
        <p className="talk-note" key={message.id}>
          {message.content}
        </p>
      ))}
    </section>
  );
}

function Said({ message }: { message: Message }) {
  return (
    <p className={`said ${message.meta.pending ? "is-pending" : ""}`}>
      {message.content}
    </p>
  );
}

function Reply({
  message,
  onContinue,
}: {
  message: Message;
  onContinue: () => void;
}) {
  const { finish_reason, n_calls, cost, patch_sha256, files_changed } =
    message.meta;

  return (
    <div className="reply">
      <Prose text={message.content} />

      <div className="reply-tail">
        {finish_reason === "question" && (
          <span className="cap cap-orange">waiting for you</span>
        )}
        {finish_reason === "stopped" && <span className="cap">stopped</span>}
        {finish_reason === "error" && (
          <span className="cap cap-error">turn failed</span>
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
