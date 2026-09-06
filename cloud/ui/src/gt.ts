/* ------------------------------------------------------------------ *
 * GroundTruth, in the transcript.
 *
 * A typed GT action is not a shell command and must not read as one. The
 * server has a frame for it — `gt_action`, with the kind, the arguments,
 * the scope it really searched, the semantics/coverage of the evidence and
 * the match count — and when that frame is on the stream this module only
 * has to format it:
 *
 *     ⏺ GroundTruth(exact_literal_search "class Command" in src/click)
 *       ⎿  2 matches · exact · complete
 *       ⎿  abstained: COVERAGE_NOT_COMPLETE
 *
 * When it is not — the deployment at HAR-84's HEAD does not emit it yet —
 * the same line is recovered structurally from the `tool_call` command,
 * which is the JSON of the typed action. That fallback is deliberately
 * narrow: a command that is not a `groundtruth` tool call is a shell
 * command, and is drawn as one.
 * ------------------------------------------------------------------ */

/** The GT tool name the typed-action protocol uses. */
export const GT_TOOL = "groundtruth";

export interface GtEvidence {
  /** `exact`, `incomplete`, … — how the producer characterised its answer. */
  semantics: string | null;
  /** `complete`, `partial`, … */
  coverage: string | null;
  matches: number | null;
  /** Why it would not answer: reason codes, or the omissions it recorded. */
  abstained: string | null;
}

export interface GtAction {
  /** `exact_literal_search`, `syntax`, `why_this_edge`, … */
  kind: string;
  /** The literal being looked for, where the action has one. */
  query: string | null;
  /** The paths the producer actually searched. */
  scope: readonly string[];
  evidence: GtEvidence | null;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function strings(value: unknown): string[] {
  if (typeof value === "string") return value ? [value] : [];
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * The literal the action is about. `exact_literal_search` calls it
 * `literal`; the other kinds name a symbol or a file. First one wins, and
 * nothing is invented.
 */
const QUERY_KEYS = ["literal", "query", "symbol", "name", "text", "path"];

function queryOf(args: Record<string, unknown>): string | null {
  for (const key of QUERY_KEYS) {
    const value = args[key];
    if (typeof value === "string" && value.trim() !== "") return value;
  }
  return null;
}

function scopeOf(args: Record<string, unknown>): string[] {
  return strings(args.paths ?? args.scope ?? args.path);
}

/** A `gt_action` frame's `data`, folded into what the line needs. */
export function fromFrame(data: Record<string, unknown>): GtAction | null {
  const kind = str(data.kind);
  if (!kind) return null;
  const args =
    typeof data.arguments === "object" && data.arguments !== null
      ? (data.arguments as Record<string, unknown>)
      : {};
  const scope = strings(data.scope);
  const reasons = strings(data.reason_codes);
  const omissions = strings(data.omissions);
  const semantics = str(data.semantics) || null;
  const matches = num(data.match_count);
  /* "Abstained" is the producer refusing to answer rather than answering
     nothing: a reason code, or an omission it recorded on the way. */
  const abstained =
    reasons.length > 0
      ? reasons.join(", ")
      : omissions.length > 0
        ? omissions.join(", ")
        : null;

  return {
    kind,
    query: queryOf(args),
    scope: scope.length > 0 ? scope : scopeOf(args),
    evidence: {
      semantics,
      coverage: str(data.coverage) || null,
      matches,
      abstained,
    },
  };
}

/**
 * The same action recovered from a `tool_call` command, for a server that
 * does not emit `gt_action` yet. The command of a typed action is its JSON
 * — `{"tool": "groundtruth", "kind": "...", "arguments": {...}}` — so this
 * parses rather than pattern-matches, and returns null for anything else.
 */
export function fromCommand(command: string): GtAction | null {
  const text = (command ?? "").trim();
  if (!text.startsWith("{") || !text.includes(GT_TOOL)) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const rec = parsed as Record<string, unknown>;
  if (str(rec.tool) !== GT_TOOL) return null;
  const kind = str(rec.kind);
  if (!kind) return null;
  const args =
    typeof rec.arguments === "object" && rec.arguments !== null
      ? (rec.arguments as Record<string, unknown>)
      : {};
  return {
    kind,
    query: queryOf(args),
    scope: scopeOf(args),
    // A command says what was asked. Only the frame says what came back.
    evidence: null,
  };
}

/** `exact_literal_search "class Command" in src/click` */
export function actionLine(action: GtAction): string {
  const parts: string[] = [action.kind];
  if (action.query) parts.push(`"${action.query}"`);
  if (action.scope.length > 0) parts.push(`in ${action.scope.join(", ")}`);
  return parts.join(" ");
}

/** `2 matches · exact · complete`, or `abstained: COVERAGE_NOT_COMPLETE`. */
export function evidenceLine(evidence: GtEvidence | null): string | null {
  if (!evidence) return null;
  if (evidence.abstained) return `abstained: ${evidence.abstained}`;
  const parts: string[] = [];
  if (evidence.matches !== null) {
    parts.push(`${evidence.matches} match${evidence.matches === 1 ? "" : "es"}`);
  }
  if (evidence.semantics) parts.push(evidence.semantics);
  if (evidence.coverage) parts.push(evidence.coverage);
  return parts.length > 0 ? parts.join(" · ") : null;
}
