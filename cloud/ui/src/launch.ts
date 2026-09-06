/* ------------------------------------------------------------------ *
 * From a prompt to a running turn.
 *
 * The reader types once. Everything between that and the agent's first
 * command — creating the session, cloning, the sandbox, the index, and the
 * first message the server would have refused a moment earlier — is this
 * module's job to describe, and the page's job to show.
 * ------------------------------------------------------------------ */

import { ApiError, type Session, type SessionCreate } from "./api";

/** The three visible phases of a session coming up, in order. */
export const CREATION_STEPS = ["cloning", "sandbox", "indexing"] as const;

export type CreationStep = (typeof CREATION_STEPS)[number];

/**
 * Which of `CREATION_STEPS` the lifecycle phase is standing on. Returns
 * `CREATION_STEPS.length` once the workspace is up: every step is behind us.
 */
export function creationStage(phase: string | null | undefined): number {
  switch (phase) {
    case "cloning":
      return 0;
    case "sandbox_starting":
    case "sandbox_ready":
    case "sandbox_restarted":
      return 1;
    case "indexing":
      return 2;
    case "gt_ready":
    case "gt_unavailable":
    case "idle":
    case "running":
      return CREATION_STEPS.length;
    default:
      /* `creating`, null, and anything a newer server invents: the first
         step has been asked for and has not reported back. */
      return 0;
  }
}

/**
 * The first turn's prompt, where the reader had to be asked which
 * repository. Both messages are the one intent — the ask and the URL — and
 * the agent gets both, in the order they were typed.
 */
export function combinePrompt(pending: string | null, next: string): string {
  const first = (pending ?? "").trim();
  const second = next.trim();
  if (first === "") return second;
  if (second === "") return first;
  return `${first}\n\n${second}`;
}

/** How many distinct files a turn's steps resolved to. */
export function turnFileCount(
  steps: readonly { files: readonly string[] }[],
): number {
  const seen = new Set<string>();
  for (const step of steps) {
    for (const path of step.files) seen.add(path);
  }
  return seen.size;
}

/**
 * A turn wide enough to be worth watching. Below this the graph is a
 * distraction from the transcript; at three files it is the fastest way to
 * see what the agent is actually doing.
 */
export const GRAPH_AUTO_FILES = 3;

export function shouldAutoOpenGraph(fileCount: number): boolean {
  return fileCount >= GRAPH_AUTO_FILES;
}

/** The line under the composer while a session is still coming up. */
export function creationLabel(repoShortName: string, stage: number): string {
  if (stage >= CREATION_STEPS.length) return "workspace ready";
  if (stage === 0) return `cloning ${repoShortName}…`;
  return `${CREATION_STEPS[stage]}…`;
}

/* ------------------------------------------------------------------ *
 * Create and run, in one call
 *
 * `SessionCreate.first_message` starts the first turn by itself the moment
 * the workspace is ready. The old path — create, poll for `idle`, then POST
 * the message — is kept as a fallback for a server that predates the field
 * and answers 4xx for it. Nothing else changes: the creation lines still
 * animate off the lifecycle frames, because that is where they come from.
 * ------------------------------------------------------------------ */

export interface Started {
  session: Session;
  /** True when the server took the prompt with the creation. */
  sent: boolean;
}

/**
 * A 4xx that is specifically about `first_message`, rather than a bad model
 * or a bad ref. Only that is worth retrying without the field: retrying a
 * real rejection would create a session the reader never asked for.
 */
export function rejectsFirstMessage(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false;
  if (err.status !== 400 && err.status !== 422) return false;
  return /first_message/i.test(err.message);
}

export async function createAndStart(
  body: SessionCreate,
  content: string,
  create: (body: SessionCreate) => Promise<Session>,
): Promise<Started> {
  try {
    return { session: await create({ ...body, first_message: content }), sent: true };
  } catch (err) {
    if (!rejectsFirstMessage(err)) throw err;
    return { session: await create(body), sent: false };
  }
}
