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
 *
 * or, when the producer would not answer, that line and only that line:
 *
 *     ⏺ GroundTruth(syntax "class Command")
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

/**
 * The `reason_codes` entry GT puts on an action that **answered**. The
 * abstention codes are the other three — `SEMANTICS_NOT_EXACT`,
 * `COVERAGE_NOT_COMPLETE`, `EVIDENCE_HAS_OMISSIONS`.
 */
export const ANSWER_CODE = "EXACT_COMPLETE_EQUIVALENCE";

export interface GtEvidence {
  /** `exact`, `incomplete`, … — how the producer characterised its answer. */
  semantics: string | null;
  /** `complete`, `partial`, … */
  coverage: string | null;
  matches: number | null;
  /**
   * True when the producer **answered**: it exited 0, the semantics are
   * `exact` and the coverage is `complete`. Anything else is an abstention,
   * whatever codes came with it (`cloud/README.md`, "GroundTruth typed
   * actions"). `EXACT_COMPLETE_EQUIVALENCE` is the code for an *answer*, so
   * a reason code on its own never means "refused" — HAR-84 P0-1, where
   * every answering action printed as an abstention.
   */
  answered: boolean;
  /** Why it would not answer. Null on an answer, and on an abstention that
   * carried no code of its own. */
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
 * `literal`, `syntax` calls it `pattern`; the other kinds name a symbol or
 * a file. First one wins, and nothing is invented.
 *
 * `path` is deliberately **not** here: it is a scope key (`scopeOf` reads
 * it), and having it in both lists printed the same value as the query and
 * as the scope — HAR-84 P2-10, where `GroundTruth(syntax)` lost its
 * `pattern` argument entirely.
 */
const QUERY_KEYS = ["literal", "pattern", "query", "symbol", "name", "text"];

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
  const omissions = strings(data.omissions);
  const semantics = str(data.semantics) || null;
  const coverage = str(data.coverage) || null;
  const matches = num(data.match_count);
  /* Absent means "the frame did not say", which is not a failure. */
  const returncode = num(data.returncode) ?? 0;
  const answered =
    returncode === 0 && semantics === "exact" && coverage === "complete";
  /* The answer code is not a reason for refusing; on an abstention that
     carries nothing else, the omissions are what there is to say. */
  const reasons = strings(data.reason_codes).filter(
    (code) => code !== ANSWER_CODE,
  );
  const abstained = answered
    ? null
    : reasons.length > 0
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
      coverage,
      matches,
      answered,
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
  if (!evidence.answered) {
    return evidence.abstained ? `abstained: ${evidence.abstained}` : "abstained";
  }
  const parts: string[] = [];
  if (evidence.matches !== null) {
    parts.push(`${evidence.matches} match${evidence.matches === 1 ? "" : "es"}`);
  }
  if (evidence.semantics) parts.push(evidence.semantics);
  if (evidence.coverage) parts.push(evidence.coverage);
  return parts.length > 0 ? parts.join(" · ") : null;
}
