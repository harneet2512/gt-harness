# GT parser-certification blocker: implementation plan

## Decision

The blocker is a repository-intelligence substrate failure, not a delivery or
model-utilization failure. The vendored `gt-index` registry currently contains
Tree-sitter specifications for 30 languages, but no certified specification or
grammar for COBOL (`.cob`, `.cbl`) or Scheme/Racket (`.scm`, `.ss`, `.rkt`).
The runtime therefore correctly reports `UNSUPPORTED_LANGUAGE` or
`INCOMPLETE_COVERAGE` and refuses to claim a source-backed frontier. We must
not repair this with regex extraction, task-specific rules, or fabricated
symbols/callers.

Evidence in the current tree:

- `vendor/gt-index-src/internal/specs` has no COBOL/Scheme/Racket spec files.
- `vendor/gt-index-src/internal/specs/spec.go` makes the Tree-sitter `Spec`
  registry the source of truth for language parsing.
- `vendor/gt-index-src/go.mod` pins `go-tree-sitter`; the production Linux
  binary is `vendor/gt-index-linux-amd64`.
- `gt_engine/language_registry.py` deliberately classifies these suffixes as
  validation-relevant but not structurally indexable.
- `gt_engine/repository_intelligence.py` fails closed for an absent or
  uncertified graph, so an active task cannot receive invented context.

## Non-negotiable contract

1. A language is marked indexable only after the shipped binary parses it and
   emits the certified `graph.db` schema.
2. Definitions, references, and call edges must come from a parser tree and
   resolver evidence. Regex may not create typed symbols or relationships.
3. Ambiguous or unsupported constructs remain unknown/private; they never
   become provider facts.
4. Existing supported languages must have byte-for-byte/semantic regression
   coverage before the new language is enabled.
5. A missing parser, stale graph, or failed certification blocks the treatment
   gate; it does not silently downgrade into a fake frontier.

## Phase 0 — reproduce and scope the blocker (provider-free)

1. Add a machine-readable language capability report from the actual binary:
   binary version/hash, accepted suffixes, grammar availability, parser status,
   and graph schema version.
2. Build fixtures for `.cob`, `.cbl`, `.scm`, `.ss`, and `.rkt`; include one
   valid file, one malformed file, declarations, a call/reference, and a
   construct whose relationship is intentionally ambiguous.
3. Run the current indexer against each fixture and record the exact failure
   (`UNSUPPORTED_LANGUAGE`, parse error, empty graph, or incomplete coverage).
4. Inspect the real smoke-task repositories and record source suffixes rather
   than inferring language from task names. Produce a task-to-language matrix;
   mixed repositories must be represented explicitly.

Exit: a checked-in fixture and a reproducible report showing every affected
task and every unsupported suffix. No paid workflow is allowed before this
report exists.

## Phase 1 — choose the parser implementation

Evaluate two implementations against the same fixtures:

### Preferred: extend the single `gt-index` binary

Add pinned Tree-sitter grammars and `Spec` registrations under
`vendor/gt-index-src/internal/specs`. This preserves one parser, one resolver,
one schema, one hash/manifest, and one incremental-update path. The build must
be reproducible in GitHub Actions because Go is not installed in the local
Windows environment.

### Fallback: certified adapter with the same graph contract

Use this only if a maintained grammar cannot be built into the vendored binary.
The adapter must write the same schema, node/edge roles, source lines,
revision/hash fields, and manifest consumed by `repository_intelligence.py`.
It must be deterministic and parser-backed. A second regex indexer is not an
acceptable fallback.

The decision record must include grammar provenance/license, pinned commit,
build hash, parse-error behavior, and why the preferred option was rejected if
the adapter is selected.

## Phase 2 — define minimum certified graph coverage

For each target language, specify and test:

- top-level/module declarations;
- callable/procedure/function definitions with source line and signature;
- call/reference nodes and directed edges only when structurally proven;
- imports/includes where the grammar exposes them;
- test-like units only where the parser identifies them;
- malformed-source behavior and explicit unknown nodes;
- deterministic ordering and stable node IDs.

For COBOL, include paragraphs/sections, `PERFORM` targets, and copybook
references where the grammar supports them. For Scheme/Racket, include
`define`/function forms, module imports, and direct call forms; higher-order or
macro-expanded calls must remain unresolved rather than guessed. The frontier
may use a definition without a caller edge, but it must label the evidence
class and confidence.

## Phase 3 — implement and certify the parser

1. Add grammar dependencies and checksum pins; add `cobol.go`, `scheme.go`, and
   `racket.go` specs only for grammars that passed Phase 2.
2. Map grammar node types to the existing `Spec` fields. Do not add language
   branches to the generic parser unless a grammar-specific field is required.
3. Add parser tests for valid, malformed, multiline, nested, and ambiguous
   fixtures. Assert node type, name, line, signature, edge direction, and
   deterministic output.
4. Add negative tests proving unsupported constructs do not create edges.
5. Run full rebuild, single-file incremental update, closure rebuild, deletion,
   and stale-source checks. Validate SQLite quick-check, `nodes`, `nodes_fts`,
   `symbol_content_fts`, manifest, graph revision, and binary hash.
6. Build Linux amd64 (paid workflow) and Windows-compatible test artifacts in
   GitHub Actions; publish only content-addressed artifacts. Update the runtime
   verifier to reject an unlisted binary hash or schema version.

## Phase 4 — enable the runtime only after certification

1. Change `gt_engine/language_registry.py` from unsupported to indexable only
   for suffixes whose binary certification passed.
2. Keep any still-unsupported suffix explicitly fail-closed.
3. Extend `IndexBuildReceipt`, repository-health metrics, and the central
   receipt with parser capability, source-file counts, unsupported suffixes,
   parse errors, graph nodes/edges, and index timing.
4. Ensure mixed repositories report `INCOMPLETE_COVERAGE` until every
   validation-relevant authored suffix is certified; artifacts and task output
   remain excluded from source revision and payloads.
5. Ensure the context frontier emits only positive-line, source-backed facts.
   Empty retrieval, stale graph, unsupported language, or ambiguous edge means
   controller-only failure—not visible advice.

## Phase 5 — prove the engine path

Add provider-free tests that prove, for each newly supported language:

1. index build is certified;
2. graph revision equals source revision;
3. frontier compilation returns at least one bounded fact for a fixture with a
   resolvable symbol;
4. the fact is accounted exactly once and is in the first eligible request;
5. no duplicate or predictive payload is emitted;
6. postflight and preflight receipts retain the same action ID;
7. malformed/ambiguous fixtures remain private and do not fabricate context;
8. full-rebuild and incremental graphs produce equivalent retrieval results.

Run existing archived trajectory replays after parser changes. A replay that
cannot build a certified graph must be reported as invalid, never counted as a
solve or efficiency witness.

## Phase 6 — gates and smoke sequence

Run in this order, stopping on the first failure:

1. full `pytest -q` and targeted release tests;
2. `python -m scripts.central_feature_census`;
3. `python scripts/central_readiness_audit.py`;
4. `python scripts/verify_gt_index_runtime.py` for every shipped binary;
5. repository-intelligence and frontier fixture suite;
6. archived regression and efficiency replays;
7. `python scripts/central_pre_smoke_gate.py` at the pushed commit, requiring
   `SMOKE_APPROVED` and zero unsupported/incomplete active smoke tasks.

Only after that gate receives separate authorization may the 10-task matched
smoke run. The audit must show per task: language coverage, graph status,
source/indexed revisions, nodes/edges, parser errors, frontier candidates and
accounted facts, first-eligible delivery timing, payload grounding, duplicate
count, model/API/action/effective-action counts, tokens, wall time, mirror and
index latency, official reward, uncensored solve, and outer/inner timeout.
The 89-task run stays blocked until repeated matched trials preserve outcomes
and pass the outcome-first efficiency gate.

## Failure policy and rollback

- Parse error, missing grammar, stale graph, schema mismatch, or timeout:
  record the receipt and fail the affected treatment gate.
- False edge or wrong definition: disable that language capability and keep the
  prior unsupported classification; do not ship a heuristic workaround.
- Latency regression: retain the old binary/registry behind the existing
  `integration_mode` switch and compare indexed-file and frontier timings.
- Binary/build drift: reject the artifact by hash and schema certification.
- Mixed-language repository: block only the affected treatment; do not hide the
  gap by dropping files from accounting.

Rollback is a one-line configuration/binary pin change (`integration_mode=off`
or the prior certified index artifact). No parser change may alter the existing
postflight engine or baseline path.

## Acceptance criteria

The blocker is fixed only when every active smoke task has a certified graph,
no task is `UNSUPPORTED_LANGUAGE` or `INCOMPLETE_COVERAGE`, all newly supported
fixtures pass structural and negative tests, every frontier fact is grounded
and accounted, the pre-smoke gate prints `SMOKE_APPROVED`, and the authorized
matched smoke reports no zero-frontier task. Parser certification alone is not
evidence of solve improvement; outcome and efficiency claims require the
matched smoke and repeated trials.

## Implemented before parser certification (2026-08-07)

The fail-closed substrate gate is now implemented independently of the parser
work. Paid active workflows pass `require_graph_ready=true`. Before the first
provider call, the agent rejects a missing, stale, empty, schema-invalid, or
incompletely covered graph with `RepositoryGraphGateFailed`; the receipt
records the reasons and provider calls remain zero. Graph manifests now bind
the certified database to the validation-relevant source revision, and the
runtime fixture verifies that binding. This prevents an unsupported language
from being measured as a successful graph-less treatment while the certified
COBOL/Scheme parser work remains a separate blocker.

Provider-free proof after this change: the strict no-provider graph-gate test,
repository refresh/revision tests, `verify_gt_index_runtime.py`, the central
feature census, readiness audit, Ruff, and the full repository suite pass. No
paid smoke was started.

## Explicit non-goals

- no regex-based symbol/call extraction;
- no LLM or predictive context generation inside GT;
- no disabling the strict repository gate to make a benchmark green;
- no task-ID-specific parser rules;
- no full-file injection or generic advice stream;
- no paid smoke or 89-task run while parser coverage is uncertified.

## Parser certification completed (2026-08-07, provider-free)

The preferred single-binary implementation is now in place for COBOL and
Scheme. Pinned upstream Tree-sitter generated sources are vendored under
`vendor/gt-index-src/internal/specs/cobol` and `.../scheme`, with matching
`Spec` registrations. COBOL indexes procedure paragraphs/sections and
`PERFORM`/`CALL` constructs when the grammar exposes them; Scheme indexes
`define` bindings and direct procedure calls. Racket remains explicitly
unsupported because its available grammar was not certified for the required
definition/call contract.

The runtime verifier now builds a three-language fixture (Python, COBOL,
Scheme) and checks the actual binary's SQLite graph: complete source coverage,
schema/FTS health, Python caller-to-target edge, and nonzero COBOL/Scheme node
counts. With the rebuilt Windows binary, the proof returned
`source_files=3`, `indexable_files=3`, `language_counts={'cobol': 2,
'python': 2, 'scheme': 2}`, and two verified call edges. The Go parser/spec
packages and Linux-style `sqlite_fts5` build both pass. The walker now
canonicalizes case-insensitive extensions so `.CBL` is not silently omitted.

The central provider-free and paid workflows build `gt-index` from the checked-
out vendored source with Go before installing the runtime, and export that
fresh binary through `GT_INDEX_BINARY`; they no longer rely on a stale committed
binary for certification. The exact pre-smoke gate must be rerun on the pushed
commit. No paid smoke was started during this repair. Parser certification is
not outcome or efficiency evidence; the 10-task smoke remains the next
authorized step only after `SMOKE_APPROVED` on the rebuilt-binary commit.

## Local stale-binary resolution (2026-08-08)

The readiness failure observed on the Windows workstation was caused by the
cached `C:\Users\Lenovo\.groundtruth\bin\gt-index.exe` predating commit
`c5f2983`; it did not contain the newly vendored COBOL/Scheme parsers. The
checked-in source and GitHub workflows were already correct, but the local
cache was stale. Rebuilding from `vendor/gt-index-src` with Go 1.22 and
`-tags sqlite_fts5` produced binary SHA-256
`3248b6e98d146359c1925e0e4724677f74035900609d25e229c8d967343cb4d3`.

Provider-free verification with that binary returned:

```text
source_files=3 indexable_files=3 node_count=6 edge_count=2
language_counts={'cobol': 2, 'python': 2, 'scheme': 2}
definition_count=4 call_count=2 schema_valid=True
```

The repaired anchor-frontier commit was pushed as `fe5d873`. With
`GT_INDEX_BINARY` pointed at the rebuilt binary, the direct census, module
census, repository-substrate check, readiness audit, and exact pre-smoke gate
all passed; the gate printed `SMOKE_APPROVED`. No paid smoke or 89-task run
was started.
