/* ------------------------------------------------------------------ *
 * Slash commands.
 *
 * Six of them, all client-side. A message that merely starts with a slash
 * (`/usr/bin/env`, `/api/sessions returns 409`) is a message, not a
 * command: only a known name followed by end-of-line or a space counts.
 * ------------------------------------------------------------------ */

export type SlashName =
  | "stop"
  | "close"
  | "graph"
  | "resume"
  | "settings"
  | "spawn"
  | "theme"
  | "help";

export interface SlashCommand {
  name: SlashName;
  /** What the argument is called, where the command takes one. */
  arg: string | null;
  hint: string;
}

export const SLASH_COMMANDS: readonly SlashCommand[] = [
  { name: "stop", arg: null, hint: "stop the turn in flight" },
  { name: "close", arg: null, hint: "close the session and discard its workspace" },
  { name: "graph", arg: null, hint: "show or hide the code graph — ctrl+g" },
  { name: "resume", arg: null, hint: "pick up a previous session — ctrl+r" },
  { name: "settings", arg: null, hint: "model, ground truth and the per-turn budgets" },
  {
    name: "spawn",
    arg: "<task>",
    hint: "hand a task to a worker agent — one /spawn line per worker, up to 4",
  },
  { name: "theme", arg: "[dark|light]", hint: "switch the terminal theme" },
  { name: "help", arg: null, hint: "list these commands" },
];

const BY_NAME = new Map<string, SlashCommand>(
  SLASH_COMMANDS.map((command) => [command.name, command]),
);

export interface ParsedSlash {
  command: SlashCommand;
  /** Everything after the name, trimmed. Empty where nothing followed. */
  arg: string;
  /** The whole draft, trimmed — a multi-line `/spawn` needs all of it. */
  raw: string;
}

/** A known command and its argument, or null for an ordinary message. */
export function parseSlash(text: string): ParsedSlash | null {
  const raw = (text ?? "").trim();
  const match = /^\/([a-z]+)(?:\s+([\s\S]*))?$/.exec(raw);
  if (!match) return null;
  const command = BY_NAME.get(match[1]);
  if (!command) return null;
  return { command, arg: (match[2] ?? "").trim(), raw };
}

/* ------------------------------------------------------------------ *
 * `/spawn`
 *
 * One worker per `/spawn` line. The server's rule is the same one, and it
 * is strict on purpose: `/spawn fix it` followed by prose is a 400 rather
 * than a turn that quietly runs the word `/spawn` past a model. Parsing it
 * here means the reader is told before the round trip.
 * ------------------------------------------------------------------ */

/** The server's cap: one call carries at most four tasks. */
export const MAX_SPAWN_TASKS = 4;

export interface SpawnDraft {
  tasks: string[];
  /** Why the draft is not a spawn, when it is not. Null when `tasks` is good. */
  error: string | null;
}

/**
 * Every non-blank line has to be a `/spawn <task>`. Returns the tasks in
 * the order they were typed, or the reason the draft cannot be sent.
 */
export function parseSpawn(text: string): SpawnDraft {
  const lines = (text ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");

  const tasks: string[] = [];
  for (const line of lines) {
    const match = /^\/spawn(?:\s+([\s\S]*))?$/.exec(line);
    if (!match) {
      return {
        tasks: [],
        error:
          "Every line of a /spawn message has to be a /spawn line. " +
          `This one is not: ${line}`,
      };
    }
    const task = (match[1] ?? "").trim();
    if (!task) {
      return { tasks: [], error: "/spawn needs a task: /spawn <what to do>" };
    }
    tasks.push(task);
  }

  if (tasks.length === 0) {
    return { tasks: [], error: "/spawn needs a task: /spawn <what to do>" };
  }
  if (tasks.length > MAX_SPAWN_TASKS) {
    return {
      tasks: [],
      error: `At most ${MAX_SPAWN_TASKS} tasks per /spawn; this one has ${tasks.length}.`,
    };
  }
  return { tasks, error: null };
}

/**
 * What to offer while the reader is typing a command name. Empty unless the
 * draft is a bare `/name` with no argument yet — once an argument is being
 * typed the menu is in the way.
 */
export function slashSuggestions(text: string): readonly SlashCommand[] {
  /* The *last* line, so a second `/spawn` on a multi-line draft completes
     the same way the first one did. */
  const lines = (text ?? "").split("\n");
  const match = /^\/([a-z]*)$/.exec(lines[lines.length - 1]);
  if (!match) return [];
  const prefix = match[1];
  return SLASH_COMMANDS.filter((command) => command.name.startsWith(prefix));
}

/** The `/help` answer, as one block of text for the thread. */
export function helpText(): string {
  return SLASH_COMMANDS.map(
    (command) =>
      `/${command.name}${command.arg ? ` ${command.arg}` : ""} — ${command.hint}`,
  ).join("\n");
}
