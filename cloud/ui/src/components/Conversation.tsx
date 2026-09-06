import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { CAP_REASONS, capLabel, type Receipt, type Session } from "../api";
import {
  orphanSteering,
  type ChatState,
  type ThreadGroup,
  type TurnGroup,
} from "../chatState";
import {
  costUntracked,
  formatCost,
  formatDuration,
  gtCountsLabel,
  repoShort,
  sessionClosedBlurb,
  shortSha,
  turnOutcomeNote,
} from "../format";
import type { ConnectBlock } from "../external";
import { repoChipLabel } from "../repoUrl";
import type { Prefs } from "../prefs";
import type { ParsedSlash } from "../slash";
import { callCount, type StepSteering, type TrailStep } from "../trail";
import { useAutoScroll } from "../useAutoScroll";
import { useSize } from "../useSize";
import { agentLines, agentRows, rootLine } from "../workers";
import Composer from "./Composer";
import TermAgents from "./TermAgents";
import TermConnect from "./TermConnect";
import Prose from "./Prose";
import TermActivity from "./TermActivity";
import TermSettings from "./TermSettings";
import TermStatus, { verbFor } from "./TermStatus";
import TermWorker from "./TermWorker";
import { Cont, Line } from "./TermLine";

/**
 * A block this page drew rather than a line it wrote. `/agents` is a live
 * listing; `/connect` is the one-liner and the one place its token exists.
 */
export type NoteBlock =
  | { kind: "agents" }
  | { kind: "connect"; connect: ConnectBlock };

/** A line this page wrote itself: `/help`, a refused command, a spawn echo. */
export interface LocalNote {
  id: number;
  role: "user" | "agent" | "system";
  text: string;
  block?: NoteBlock;
}

interface Props {
  sessionId: string | null;
  session: Session | null;
  chat: ChatState;
  /** One per finished turn, keyed by `turn_id` — the GT counts live here. */
  receipts: readonly Receipt[];
  groups: ThreadGroup[];
  stepsByTurn: Record<string, TrailStep[]>;
  edited: ReadonlySet<string>;
  selectedTurnId: string | null;
  currentTurnId: string | null;
  onSelectTurn: (turnId: string) => void;
  /** Steps visible at the scrub position, for the selected turn only. */
  cutoff: number;
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
  /** The first message, typed on the landing page and not yet in the thread. */
  pendingFirst: string | null;
  notes: readonly LocalNote[];
  /** `/close` is waiting for a yes or a no, in the transcript. */
  closeAsk: boolean;
  onCloseAnswer: (confirmed: boolean) => void;
  canApply: boolean;
  onApplyWorker: (workerId: string) => void;
  /** Narrow the graph to one agent — the `[focus]` on a card and a row. */
  onFocusAgent: (agentId: string) => void;
  /**
   * Agents that have said where they are working and whose files are not
   * on this graph at all — a Claude Code or Codex session on a different
   * checkout. Their cards say so; see `repoFit`.
   */
  outsideRepo: ReadonlySet<string>;
  /** `/settings`, drawn in the transcript rather than over it. */
  settingsOpen: boolean;
  prefs: Prefs;
  onPrefs: (next: Prefs) => void;
  onCloseSettings: () => void;
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
  receipts,
  groups,
  stepsByTurn,
  edited,
  selectedTurnId,
  currentTurnId,
  onSelectTurn,
  cutoff,
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
  closeAsk,
  onCloseAnswer,
  canApply,
  onApplyWorker,
  onFocusAgent,
  outsideRepo,
  settingsOpen,
  prefs,
  onPrefs,
  onCloseSettings,
  onSend,
  onCommand,
  onStop,
  onContinue,
  onPickFile,
  focusSignal,
}: Props) {
  const navigate = useNavigate();
  const scroll = useAutoScroll();
  /* The composer is a solid block under the scroll area; the transcript
     keeps that much clear space beneath its last line (HAR-84 P1-5). */
  const [composerRef, composerSize] = useSize<HTMLDivElement>();
  const talkRef = useRef<HTMLElement>(null);
  useEffect(() => {
    talkRef.current?.style.setProperty(
      "--composer-h",
      `${Math.round(composerSize.height)}px`,
    );
  }, [composerSize.height]);

  const byTurn = new Map(receipts.map((receipt) => [receipt.turn_id, receipt]));
  let turnNo = 0;

  const gtMode = String(session?.gt_mode ?? "off");
  const gtStatus = String(session?.gt_status ?? "off");
  const gtFailed = gtStatus === "unavailable" && gtMode !== "off";

  const sessionStatus = String(session?.status ?? "");
  const closedBlurb = sessionClosedBlurb(sessionStatus, session?.closed_reason);
  const creating = sessionStatus === "creating";
  const repo = session ? repoShort(session.repo) : "";

  const liveSteps = stepsByTurn[currentTurnId ?? ""] ?? [];
  const last = liveSteps[liveSteps.length - 1];
  const currentTurn = currentTurnId ? chat.turns[currentTurnId] : undefined;
  const elapsed =
    running && currentTurn?.startedAt != null ? now - currentTurn.startedAt : null;
  /* Workers and external agents are one list, in `created_at` order, each
     subagent under its parent. The number and the hue stay the flat spawn
     position, so a card and its trail on the graph are the same colour
     however the tree indents them. */
  const rows = agentRows(chat.workers);
  const lines = agentLines(chat.workers, now);
  const fleetRoot = rootLine(session, now);
  const agentsWorking = rows.filter(
    (row) => row.worker.status === "running",
  ).length;

  const gtLabel =
    gtMode === "off" ? "GT off" : `GT ${gtMode}${gtStatus ? ` (${gtStatus})` : ""}`;
  /* `owner/name @ ref`, the way the banner and the landing status line
     write it. One spelling everywhere (HAR-84 P2-11). */
  const statusLine = session
    ? [repoChipLabel(session.repo, session.ref), session.model, gtLabel].join(" · ")
    : "";

  return (
    <section className="talk" ref={talkRef}>
      <div className="talk-wrap">
        <div className="talk-scroll" ref={scroll.ref}>
          {sessionStatus === "failed" && failureError && (
            <Cont tone="error">This session failed: {failureError}</Cont>
          )}

          {gtFailed && (
            <>
              <Cont tone="dim">
                GroundTruth index unavailable — running without graph evidence
              </Cont>
              {gtError && <Cont tone="dim">{gtError}</Cont>}
            </>
          )}

          {closedBlurb !== null && session && sessionStatus !== "failed" && (
            <Cont tone="dim">
              {closedBlurb}{" "}
              <button
                type="button"
                className="bracket"
                onClick={() =>
                  navigate(
                    `/?repo=${encodeURIComponent(session.repo)}&ref=${encodeURIComponent(session.ref)}`,
                  )
                }
              >
                [new session on this repo]
              </button>
            </Cont>
          )}

          {/* The prompt that created this session, before the server has it. */}
          {pendingFirst && <Said text={pendingFirst} pending />}

          {sessionId && groups.length === 0 && !pendingFirst && !creating && (
            <Line tone="dim">
              Tell the agent what to do. Every message walks the graph once.
            </Line>
          )}

          {groups.map((group) => {
            if (group.kind === "note") {
              return group.message.role === "user" ? (
                <Said key={group.message.id} text={group.message.content} />
              ) : (
                <Cont key={group.message.id} tone="dim">
                  ✱ {group.message.content}
                </Cont>
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
                receipt={byTurn.get(group.turnId) ?? null}
                steps={steps}
                edited={edited}
                selected={selected}
                gtStatus={gtStatus}
                running={running && group.turnId === currentTurnId}
                cutoff={selected ? cutoff : steps.length}
                now={now}
                onSelect={() => onSelectTurn(group.turnId)}
                onContinue={onContinue}
                onPickFile={onPickFile}
              />
            );
          })}

          {/* What you typed comes before what it did — including the
              `/spawn` lines that produced the agents below them. */}
          {notes.map((note) => {
            if (note.block?.kind === "agents") {
              return (
                <TermAgents
                  key={note.id}
                  lines={lines}
                  root={fleetRoot}
                  onFocus={onFocusAgent}
                  outside={outsideRepo}
                />
              );
            }
            if (note.block?.kind === "connect") {
              return <TermConnect key={note.id} block={note.block.connect} />;
            }
            return note.role === "user" ? (
              <Said key={note.id} text={note.text} />
            ) : (
              <Cont key={note.id} tone="dim">
                {note.text}
              </Cont>
            );
          })}

          {/* The one destructive command asks where every other command
              answers: in the transcript (HAR-84 P2-9). */}
          {closeAsk && (
            <Cont tone="dim">
              close this session?{" "}
              <button
                type="button"
                className="bracket"
                onClick={() => onCloseAnswer(true)}
              >
                [y]
              </button>{" "}
              <button
                type="button"
                className="bracket"
                onClick={() => onCloseAnswer(false)}
              >
                [n]
              </button>
            </Cont>
          )}

          {rows.map((row) => (
            <TermWorker
              key={row.worker.id}
              worker={row.worker}
              no={row.no}
              hue={row.hue}
              depth={row.depth}
              canApply={canApply}
              onApply={() => onApplyWorker(row.worker.id)}
              onFocus={() => onFocusAgent(row.worker.id)}
              outsideRepo={outsideRepo.has(row.worker.id)}
            />
          ))}

          {settingsOpen && (
            <TermSettings
              prefs={prefs}
              onChange={onPrefs}
              onClose={onCloseSettings}
              note={
                sessionId
                  ? "this session keeps the settings it started with"
                  : undefined
              }
            />
          )}
        </div>

        {scroll.detached && (
          <button type="button" className="bracket jump" onClick={scroll.jumpToLatest}>
            [jump to latest ↓]
          </button>
        )}
      </div>

      <TermStatus
        running={running}
        preparing={creating}
        phase={phase}
        repo={repo}
        stopping={stopping}
        elapsed={elapsed}
        steps={callCount(liveSteps, currentTurn?.nCalls ?? null)}
        verb={verbFor(last?.command)}
        agents={agentsWorking}
      />

      <div className="composer-measure" ref={composerRef}>
      <Composer
        stopping={stopping}
        locked={locked || !sessionId}
        lockedReason={sessionId ? lockedReason : "pick a session to start talking"}
        isRunning={running}
        steeringQueued={steeringQueued}
        error={sendError}
        focusSignal={focusSignal}
        status={statusLine}
        onSend={onSend}
        onCommand={onCommand}
        onStop={onStop}
      />
      </div>
    </section>
  );
}

/** `> the prompt` */
function Said({ text, pending = false }: { text: string; pending?: boolean }) {
  return (
    <p className={`termsaid ${pending ? "is-pending" : ""}`}>
      <span className="termsaid-mark" aria-hidden="true">
        &gt;
      </span>
      <span>{text}</span>
    </p>
  );
}

function Exchange({
  no,
  group,
  chat,
  receipt,
  steps,
  edited,
  selected,
  gtStatus,
  running,
  cutoff,
  now,
  onSelect,
  onContinue,
  onPickFile,
}: {
  no: number;
  group: TurnGroup;
  chat: ChatState;
  receipt: Receipt | null;
  steps: TrailStep[];
  edited: ReadonlySet<string>;
  selected: boolean;
  gtStatus: string;
  running: boolean;
  cutoff: number;
  now: number;
  onSelect: () => void;
  onContinue: () => void;
  onPickFile: (path: string) => void;
}) {
  const turn = chat.turns[group.turnId];
  const endedAt = turn?.finishedAt ?? (running ? now : null);
  const elapsed =
    turn?.startedAt != null && endedAt != null ? endedAt - turn.startedAt : null;
  const outcome = turnOutcomeNote(turn?.finishReason ?? null);

  const extraSteering: StepSteering[] = orphanSteering(group, turn).map((m) => ({
    key: m.id,
    content: m.content,
  }));

  const reply = group.replies[group.replies.length - 1];
  const finish = String(reply?.meta.finish_reason ?? turn?.finishReason ?? "");
  const capped = CAP_REASONS.has(finish);

  return (
    <section className="exchange">
      {group.prompt && <Said text={group.prompt.content} />}

      {extraSteering.map((message) => (
        <p className="termsaid is-mid" key={message.key}>
          <span className="termsaid-mark" aria-hidden="true">
            &gt;
          </span>
          <span>(mid-turn) {message.content}</span>
        </p>
      ))}

      <TermActivity
        steps={steps}
        cutoff={cutoff}
        edited={edited}
        running={running}
        onPickFile={onPickFile}
      />

      {group.replies.map((message) => (
        <Line key={message.id}>
          <Prose text={message.content} />
        </Line>
      ))}

      {group.notes.map((message) => (
        <Cont key={message.id} tone="dim">
          ✱ {message.content}
        </Cont>
      ))}

      {!running && (
        <button
          type="button"
          className={`receipt ${selected ? "is-selected" : ""}`}
          aria-pressed={selected}
          onClick={onSelect}
        >
          <span className="tline-bullet" aria-hidden="true">
            ⏺
          </span>{" "}
          <span className="tname">Receipt</span>
          <span className="targ">(turn {no})</span>
          {" · "}
          {receiptTail({
            calls: callCount(steps, turn?.nCalls ?? null),
            elapsed,
            cost: turn?.cost ?? null,
            patch: reply?.meta.patch_sha256 ?? null,
            gtStatus,
            gtCounts: gtCountsLabel(
              receipt?.gt_actions,
              receipt?.gt_exact_matches,
            ),
            outcome: group.replies.length === 0 ? outcome : null,
            finish,
          })}
        </button>
      )}

      {capped && (
        <Cont tone="dim">
          {capLabel(finish)} reached{" "}
          <button
            type="button"
            className="bracket"
            onClick={(e) => {
              e.stopPropagation();
              onContinue();
            }}
          >
            [continue]
          </button>
        </Cont>
      )}
    </section>
  );
}

/** `12 steps · 1m 20s · patch a80d4c46 · GT ready · GT 3 actions / 2 exact` */
function receiptTail({
  calls,
  elapsed,
  cost,
  patch,
  gtStatus,
  gtCounts,
  outcome,
  finish,
}: {
  calls: number;
  elapsed: number | null;
  cost: number | null;
  patch: string | null | undefined;
  gtStatus: string;
  gtCounts: string | null;
  outcome: string | null;
  finish: string;
}): string {
  /* A provider that reports no price is not a run that was free. The
     receipts pane says so in as many words, in a column that has the room;
     here the honest thing is to leave the number out (HAR-84 P2-13). */
  const untracked = cost !== null && costUntracked([cost]);
  const parts = [
    `${calls} step${calls === 1 ? "" : "s"}`,
    elapsed !== null ? formatDuration(elapsed) : null,
    cost !== null && !untracked ? formatCost(cost) : null,
    patch ? `patch ${shortSha(patch)}` : null,
    gtStatus && gtStatus !== "off" ? `GT ${gtStatus}` : null,
    gtCounts,
    finish === "question" ? "waiting for you" : null,
    outcome,
  ].filter(Boolean);
  return parts.join(" · ");
}

/** Named so the strip's old export site keeps compiling. */
export { Said };
