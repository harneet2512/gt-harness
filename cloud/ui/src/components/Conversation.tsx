import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CAP_REASONS, capLabel, type Message, type Session } from "../api";
import {
  orphanSteering,
  type ChatState,
  type ThreadGroup,
  type TurnGroup,
} from "../chatState";
import {
  formatClock,
  formatCost,
  repoShort,
  sessionClosedBlurb,
  shortSha,
  turnOutcomeNote,
} from "../format";
import { callCount, type StepSteering, type TrailStep } from "../trail";
import { useAutoScroll } from "../useAutoScroll";
import type { ParsedSlash } from "../slash";
import Composer from "./Composer";
import CreationLine from "./CreationLine";
import Prose from "./Prose";
import TrailPanel from "./TrailPanel";
import TransmissionStrip from "./TransmissionStrip";

/** A line this page wrote itself: `/help`, `/spawn`, a refused command. */
export interface LocalNote {
  id: number;
  role: "user" | "agent" | "system";
  text: string;
}

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
  hereStep: number | null;
  running: boolean;
  /** Stop was pressed; the turn is winding down. */
  stopping: boolean;
  /** A sent message is queued and undelivered. */
  steeringQueued: boolean;
  phase: string | null;
  /** Epoch seconds, ticking only while a turn runs. */
  now: number;
  locked: boolean;
  lockedReason: string;
  sendError: string | null;
  /** Why the GT index is unavailable, when it is. */
  gtError: string | null;
  /** Why the session failed, in the server's words, when it has. */
  failureError: string | null;
  /**
   * The first message, typed on the landing page and not yet accepted: the
   * server answers 409 until the workspace is up. Shown the moment the page
   * opens, so the wait belongs to the prompt rather than to a blank screen.
   */
  pendingFirst: string | null;
  notes: readonly LocalNote[];
  onSend: (content: string) => Promise<boolean>;
  onCommand: (parsed: ParsedSlash) => void;
  onStop: () => void;
  onContinue: () => void;
  onPickFile: (path: string) => void;
  focusSignal: number;
}

/** The transcript: what you asked, what it did, what came back. */
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
  hereStep,
  running,
  stopping,
  steeringQueued,
  phase,
  now,
  locked,
  lockedReason,
  sendError,
  gtError,
  failureError,
  pendingFirst,
  notes,
  onSend,
  onCommand,
  onStop,
  onContinue,
  onPickFile,
  focusSignal,
}: Props) {
  const navigate = useNavigate();
  const scroll = useAutoScroll();
  const [gtDismissed, setGtDismissed] = useState(false);
  const [failDismissed, setFailDismissed] = useState(false);
  let turnNo = 0;

  const gtMode = String(session?.gt_mode ?? "off");
  const gtStatus = String(session?.gt_status ?? "off");
  // Asked for ground truth and did not get it: say so, once, until dismissed.
  const gtFailed = gtStatus === "unavailable" && gtMode !== "off";

  const sessionStatus = String(session?.status ?? "");
  const closedBlurb = sessionClosedBlurb(sessionStatus, session?.closed_reason);
  const failNotice =
    sessionStatus === "failed" && Boolean(failureError) && !failDismissed;
  const creating = sessionStatus === "creating";

  return (
    <section className="talk">
      <div className="talk-wrap">
        <div className="talk-scroll" ref={scroll.ref}>
          {/* The server said why. Saying it is the difference between a
              dead end and something the reader can act on. */}
          {failNotice && (
            <div className="gt-warn is-fail">
              <div className="gt-warn-line">
                <span>This session failed.</span>
                <button
                  type="button"
                  className="btn-text"
                  aria-label="Dismiss this notice"
                  onClick={() => setFailDismissed(true)}
                >
                  ✕
                </button>
              </div>
              <p className="mono gt-warn-detail">{failureError}</p>
            </div>
          )}

          {gtFailed && !gtDismissed && (
            <div className="gt-warn">
              <div className="gt-warn-line">
                <span>
                  GroundTruth index unavailable for this session — running
                  without graph evidence
                </span>
                <button
                  type="button"
                  className="btn-text"
                  aria-label="Dismiss this notice"
                  onClick={() => setGtDismissed(true)}
                >
                  ✕
                </button>
              </div>
              {gtError && (
                <details className="gt-warn-why">
                  <summary className="cap">why</summary>
                  <p className="mono">{gtError}</p>
                </details>
              )}
            </div>
          )}

          {closedBlurb !== null && session && !failNotice && (
            <div className="closed-note">
              <div className="closed-note-line">
                <span>{closedBlurb}</span>
                <button
                  type="button"
                  className="btn btn-orange btn-restart"
                  onClick={() =>
                    navigate(
                      `/?repo=${encodeURIComponent(session.repo)}&ref=${encodeURIComponent(session.ref)}`,
                    )
                  }
                >
                  Start a new session on this repo
                </button>
              </div>
            </div>
          )}

          {/* The prompt that created this session, before the server will
              take it. It is already the first thing in the transcript. */}
          {pendingFirst && <p className="said is-pending">{pendingFirst}</p>}

          {creating && session && (
            <CreationLine repo={repoShort(session.repo)} phase={phase} />
          )}

          {sessionId && groups.length === 0 && !pendingFirst && !creating && (
            <p className="talk-empty">
              Tell the agent what to do. Every message walks the graph once.
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
                hereStep={selected ? hereStep : null}
                now={now}
                onSelect={() => onSelectTurn(group.turnId)}
                onContinue={onContinue}
                onPickFile={onPickFile}
              />
            );
          })}

          {notes.map((note) =>
            note.role === "user" ? (
              <p className="said arrives" key={note.id}>
                {note.text}
              </p>
            ) : (
              <p
                className={`landing-say arrives ${note.role === "system" ? "is-system" : ""}`}
                key={note.id}
              >
                {note.text}
              </p>
            ),
          )}
        </div>

        {scroll.detached && (
          <button type="button" className="jump" onClick={scroll.jumpToLatest}>
            Jump to latest ↓
          </button>
        )}
      </div>

      <Composer
        stopping={stopping}
        locked={locked || !sessionId}
        lockedReason={
          sessionId ? lockedReason : "Pick a session to start talking."
        }
        isRunning={running}
        steeringQueued={steeringQueued}
        error={sendError}
        focusSignal={focusSignal}
        onSend={onSend}
        onCommand={onCommand}
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
  hereStep,
  now,
  onSelect,
  onContinue,
  onPickFile,
}: {
  no: number;
  group: TurnGroup;
  chat: ChatState;
  steps: TrailStep[];
  edited: ReadonlySet<string>;
  selected: boolean;
  running: boolean;
  cutoff: number;
  hereStep: number | null;
  now: number;
  onSelect: () => void;
  onContinue: () => void;
  onPickFile: (path: string) => void;
}) {
  const turn = chat.turns[group.turnId];
  const endedAt = turn?.finishedAt ?? (running ? now : null);
  const elapsed =
    turn?.startedAt != null && endedAt != null ? endedAt - turn.startedAt : null;

  /* A turn that ended without an answer still has to end on screen.
     HAR-84 G-08: a restart left the card reading "Working" for 300 s. */
  const outcome = turnOutcomeNote(turn?.finishReason ?? null);

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
        calls={callCount(steps, turn?.nCalls ?? null)}
        edited={edited}
        running={running}
        selected={selected}
        cutoff={cutoff}
        cost={turn?.cost ?? null}
        elapsed={elapsed}
        extraSteering={extraSteering}
        outcome={group.replies.length === 0 ? outcome : null}
        onSelect={onSelect}
      />

      {/* The terminal part: thought, `$ command`, output, in the thread
          where it happened rather than in a panel you have to go and find. */}
      {(steps.length > 0 || running) && (
        <div className="acts-inline">
          <TrailPanel
            steps={steps}
            cutoff={cutoff}
            hereStep={hereStep}
            edited={edited}
            running={running}
            onPickFile={onPickFile}
          />
        </div>
      )}

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
        {finish_reason === "interrupted" && (
          <span className="cap">interrupted by a server restart</span>
        )}
        {finish_reason === "error" && (
          <span className="cap cap-error">turn failed</span>
        )}
        {/* Out of steps and out of time are the same event to the reader:
            the agent stopped at a cap, not at an answer, and the only
            question is whether to spend another one. */}
        {typeof finish_reason === "string" &&
          CAP_REASONS.has(finish_reason) && (
            <>
              <span className="cap">{capLabel(finish_reason)} reached</span>
              <button
                type="button"
                className="link"
                onClick={(e) => {
                  e.stopPropagation();
                  onContinue();
                }}
              >
                continue
              </button>
            </>
          )}

        <span className="spacer" />
        <span className="mono muted">
          {[
            typeof n_calls === "number"
              ? `${n_calls} step${n_calls === 1 ? "" : "s"}`
              : null,
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
