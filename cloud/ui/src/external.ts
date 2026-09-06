/* ------------------------------------------------------------------ *
 * External agents.
 *
 * An external agent is one we do **not** run: a Claude Code session on
 * someone's laptop, a Codex session, or a subagent of either. The server
 * models it as a worker it never executes and mirrors its events onto this
 * session's stream with the frame types that already exist, so the UI folds
 * it into the same agent list, the same cards and the same graph hues.
 *
 * Everything that arrives with one of those frames — a label, a task, a
 * tool name, a command, a file path — was written on a machine we do not
 * control. It is rendered as text and never as markup or as a link target,
 * and it is cleaned **here, on the way in**, so nothing downstream has to
 * remember to. A path is not a path unless it looks like one; a label with
 * a newline in it cannot forge a second transcript line.
 * ------------------------------------------------------------------ */

/** The kinds the server names. Anything else is `other` and still renders. */
export const AGENT_KINDS = ["claude-code", "codex", "other"] as const;

export type AgentKind = (typeof AGENT_KINDS)[number];

/** The two a person can actually attach from their own machine. */
export const CONNECTABLE_KINDS: readonly AgentKind[] = ["claude-code", "codex"];

export function isAgentKind(value: string): value is AgentKind {
  return (AGENT_KINDS as readonly string[]).includes(value);
}

/* ------------------------------------------------------------------ *
 * Cleaning what someone else's machine sent
 * ------------------------------------------------------------------ */

/** C0 and C1, including the escapes a terminal would otherwise obey. */
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/g;

/**
 * One line of plain text, clipped. Control characters become a space
 * rather than disappearing, so `a\nb` reads as `a b` and never as two
 * lines of transcript.
 */
export function sanitizeText(value: unknown, max = 200): string {
  if (typeof value !== "string" || value === "") return "";
  const flat = value.replace(CONTROL, " ").replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, Math.max(1, max - 1))}…` : flat;
}

/* The same class without `g`. A global regex carries `lastIndex` between
   calls, so `.test()` on one silently alternates true and false. */
const HAS_CONTROL = new RegExp(CONTROL.source);

/** The longest repo-relative path we will believe in. */
const MAX_PATH = 240;

/**
 * A repo-relative path, or null for anything that is not one. Absolute
 * paths, Windows paths, `..` and control characters are dropped rather
 * than guessed at: a path the graph cannot resolve is worth nothing, and a
 * path that escapes the repository is worth less than nothing.
 */
export function sanitizePath(value: unknown): string | null {
  if (typeof value !== "string" || value === "") return null;
  if (HAS_CONTROL.test(value)) return null;
  const trimmed = value.trim().replace(/^\.\//, "");
  if (trimmed === "" || trimmed.length > MAX_PATH) return null;
  if (trimmed.startsWith("/") || trimmed.startsWith("~")) return null;
  if (/^[A-Za-z]:[\\/]/.test(trimmed) || trimmed.includes("\\")) return null;
  if (trimmed.split("/").some((part) => part === ".." || part === "")) {
    return null;
  }
  return trimmed;
}

/** How many paths one frame may contribute, however many it claims. */
export const MAX_PATHS_PER_FRAME = 20;

export function sanitizePaths(
  value: unknown,
  cap = MAX_PATHS_PER_FRAME,
): string[] {
  if (!Array.isArray(value)) return [];
  const out: string[] = [];
  for (const item of value) {
    const path = sanitizePath(item);
    if (path !== null && !out.includes(path)) out.push(path);
    if (out.length >= cap) break;
  }
  return out;
}

/* ------------------------------------------------------------------ *
 * Where an agent is working
 * ------------------------------------------------------------------ */

/** How many files an agent's "where they work" list remembers. */
export const MAX_AGENT_FILES = 40;

/**
 * The files an agent has been seen in, most recent first, de-duplicated
 * and capped. `incoming` is one frame's worth, in the order the frame
 * listed them, so its **last** entry is the most recent thing it touched.
 */
export function foldFiles(
  current: readonly string[],
  incoming: readonly string[],
  cap = MAX_AGENT_FILES,
): readonly string[] {
  if (incoming.length === 0) return current;
  const out: string[] = [];
  const seen = new Set<string>();
  const push = (path: string) => {
    if (seen.has(path) || out.length >= cap) return;
    seen.add(path);
    out.push(path);
  };
  for (let i = incoming.length - 1; i >= 0; i -= 1) push(incoming[i]);
  for (const path of current) push(path);
  /* Identity is load-bearing: an unchanged list must not re-render the
     card or re-key the graph layer. */
  if (out.length === current.length && out.every((p, i) => p === current[i])) {
    return current;
  }
  return out;
}

/** `3 files · src/click/core.py, src/click/parser.py …` */
export function filesLine(files: readonly string[], show = 2): string {
  if (files.length === 0) return "";
  const head = files.slice(0, Math.max(1, show));
  const more = files.length > head.length ? " …" : "";
  return `${files.length} file${files.length === 1 ? "" : "s"} · ${head.join(", ")}${more}`;
}


/* ------------------------------------------------------------------ *
 * `/connect` — what a person runs on their own machine
 *
 * There is no binary to install and nothing to invent here: the adapters
 * live in this repository (`cloud/adapters/`) and read three environment
 * variables. `/connect` registers the agent and hands back exactly those
 * three, plus the one step that differs by kind — a hook for Claude Code,
 * a tailer for Codex.
 * ------------------------------------------------------------------ */

/** The three the adapters read. `GT_CLOUD_ORIGIN` is required either way. */
export const ORIGIN_ENV = "GT_CLOUD_ORIGIN";
export const AGENT_ID_ENV = "GT_CLOUD_AGENT_ID";
/**
 * The ingest token, scoped to one agent. It is **not** `GT_CLOUD_TOKEN`:
 * that one is the user's own JWT, which this page cannot read and must
 * never print. Handing out the agent token is what "already registered,
 * stream straight in" means to the bridge.
 */
export const TOKEN_ENV = "GT_CLOUD_AGENT_TOKEN";

/** Where all of this is written down, for the line under the block. */
export const EXTERNAL_AGENTS_DOC = "docs/cloud/external-agents.md";

/** The adapter files, as the doc names them. */
export const CLAUDE_HOOK = "cloud/adapters/claude_code/gt_cloud_hook.py";
export const CLAUDE_SNIPPET = "cloud/adapters/claude_code/settings.snippet.json";
export const CODEX_TAILER = "cloud/adapters/codex/gt_cloud_codex.py";

/**
 * `/connect`'s argument. Empty means Claude Code, which is what most
 * people are running when they ask. A kind we have no adapter for is null,
 * and the command says so rather than issuing a token nothing can use.
 */
export function connectKind(arg: string): AgentKind | null {
  const value = sanitizeText(arg, 40).toLowerCase();
  if (value === "") return "claude-code";
  if (value === "codex") return "codex";
  if (value === "claude-code" || value === "claude" || value === "claude code") {
    return "claude-code";
  }
  return null;
}

export interface ConnectTarget {
  /** Where this UI is served from. The fallback for the origin export. */
  origin: string;
  /** `ingest_url` as the server returned it — absolute, from `url_for`. */
  ingestUrl: string;
  /** The agent row's id: half of "already registered, stream straight in". */
  agentId: string;
  token: string;
  kind: string;
}

/** Nothing that would end a shell word early may go inside one. */
function shellSafe(value: string, max = 512): string {
  return (value ?? "")
    .replace(CONTROL, "")
    .replace(/['\\]/g, "")
    .slice(0, max);
}

/**
 * The origin the adapters must post to.
 *
 * They build every URL themselves from `GT_CLOUD_ORIGIN`, so this is the
 * one value that has to be right. The server answers with an absolute
 * `ingest_url` (it is built with `url_for`), and its origin is by
 * definition the one that issued this token — so it wins over the page's
 * own, which can differ behind a proxy or a tunnel.
 */
export function connectOrigin(pageOrigin: string, ingestUrl: string): string {
  const match = /^(https?:\/\/[^/?#]+)/i.exec((ingestUrl ?? "").trim());
  const origin = match ? match[1] : (pageOrigin ?? "");
  return origin.replace(/\/+$/, "");
}

/** The whole answer to `/connect`, reduced to the lines it prints. */
export interface ConnectBlock {
  kind: string;
  /**
   * The three exports, one per line. The **only** string in this app that
   * carries the token: the step, the note, the heading and the doc line
   * are all written without it.
   */
  exports: string;
  /** The one step that differs by kind, in a sentence. */
  step: string;
  /** The command that step runs, or "" when the step is not a command. */
  stepCommand: string;
  docs: string;
}

export function connectBlock(target: ConnectTarget): ConnectBlock {
  const origin = shellSafe(connectOrigin(target.origin, target.ingestUrl));
  const agentId = shellSafe(target.agentId, 128);
  const token = shellSafe(target.token, 512);
  const kind = isAgentKind(target.kind) ? target.kind : "other";

  const exports = [
    `export ${ORIGIN_ENV}='${origin}'`,
    `export ${AGENT_ID_ENV}='${agentId}'`,
    `export ${TOKEN_ENV}='${token}'`,
  ].join("\n");

  if (kind === "codex") {
    return {
      kind,
      exports,
      step: "then run the tailer — it follows the newest rollout by itself:",
      stepCommand: `python ${CODEX_TAILER}`,
      docs: EXTERNAL_AGENTS_DOC,
    };
  }

  if (kind === "claude-code") {
    return {
      kind,
      exports,
      /* Claude Code is not a process you start against a session: it is a
         hook it calls. There is no command to run, and printing one would
         be the same lie the last version told. */
      step:
        `then add ${CLAUDE_SNIPPET} to your Claude Code settings ` +
        `(it runs ${CLAUDE_HOOK}) and start claude from that same shell.`,
      stepCommand: "",
      docs: EXTERNAL_AGENTS_DOC,
    };
  }

  return {
    kind,
    exports,
    step: `then run the adapter for this agent — ${EXTERNAL_AGENTS_DOC} lists them.`,
    stepCommand: "",
    docs: EXTERNAL_AGENTS_DOC,
  };
}

/** The sentence under the block. It must never contain the token. */
export const CONNECT_SECRET_NOTE =
  `the ${TOKEN_ENV} above is a secret — it is shown once, here, and ` +
  "anyone who has it can post events into this session";
