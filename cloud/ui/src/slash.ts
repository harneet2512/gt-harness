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
  | "settings"
  | "spawn"
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
  { name: "graph", arg: null, hint: "show or hide the graph panel" },
  { name: "settings", arg: null, hint: "model, ground truth and the per-turn budgets" },
  { name: "spawn", arg: "<task>", hint: "hand a task to a worker agent" },
  { name: "help", arg: null, hint: "list these commands" },
];

const BY_NAME = new Map<string, SlashCommand>(
  SLASH_COMMANDS.map((command) => [command.name, command]),
);

export interface ParsedSlash {
  command: SlashCommand;
  /** Everything after the name, trimmed. Empty where nothing followed. */
  arg: string;
}

/** A known command and its argument, or null for an ordinary message. */
export function parseSlash(text: string): ParsedSlash | null {
  const match = /^\/([a-z]+)(?:\s+([\s\S]*))?$/.exec((text ?? "").trim());
  if (!match) return null;
  const command = BY_NAME.get(match[1]);
  if (!command) return null;
  return { command, arg: (match[2] ?? "").trim() };
}

/**
 * What to offer while the reader is typing a command name. Empty unless the
 * draft is a bare `/name` with no argument yet — once an argument is being
 * typed the menu is in the way.
 */
export function slashSuggestions(text: string): readonly SlashCommand[] {
  const match = /^\/([a-z]*)$/.exec(text ?? "");
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
