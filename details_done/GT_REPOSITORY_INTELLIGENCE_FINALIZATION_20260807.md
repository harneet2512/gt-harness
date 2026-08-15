# GT Repository-Intelligence Finalization — 2026-08-07

## Outcome

The approved repository-intelligence plan is implemented provider-free. GT now
has a measurable, source-revision-bound repository substrate and a bounded
incremental context frontier before every Mini-SWE model call. The experiment
cannot call a task healthy when `graph.db` is unavailable, stale, structurally
incomplete, schema-invalid, empty, or produces zero new model-visible
repository context.

No paid smoke or 89-task run was started. This document is implementation and
verification evidence, not a benchmark-improvement claim.

## The defect that this closes

The former system could produce hundreds of correct feature receipts while
still leaving a task with no model-visible repository intelligence. It could
also report `graph.db` as present without making language coverage, schema,
graph contents, revision freshness, or retrieval usefulness a treatment gate.
Finally, archived deep metrics counted feature guidance and compiler state but
would have omitted the new repository-frontier characters.

That was insufficient for an intelligence layer. Receipts prove observation;
they do not prove that Mini-SWE received current repository facts.

## Implemented architecture

```text
initial task workspace
  -> bounded host mirror
  -> full certified gt-index build
  -> task-linked graph retrieval
  -> source-backed RepositoryEvidence
  -> ContextFrontierCompiler(history, source revision)
  -> exact provider request
  -> model-selected Bash action
  -> typed ProposedAction + SHADOW preflight
  -> original environment execution
  -> authoritative postflight
  -> source transition
  -> incremental per-file graph refresh + closure rebuild
  -> next frontier
```

The existing 17-feature postflight engine remains intact. The repository
frontier is additive and coalesces with an existing one-shot feature frame in a
single runtime message. It does not add an MCP process, GT model call, hidden
task instruction, or benchmark-specific task rule.

## Changes by subsystem

### One language authority

`gt_engine/language_registry.py` is now the authority for validation-relevant
source, structurally indexable source, index-required source, and deterministic
syntax probes. The supported structural suffix set matches the vendored
`gt-index` language specs, including CSS, CUE, Elm, Groovy, HCL, HTML,
protobuf, SQL, Svelte, YAML/TOML, `.cxx`, `.hxx`, `.mli`, `.rake`, and `.sc`.

Authored but unsupported languages—including COBOL, Scheme, Racket,
Objective-C, Erlang, Haskell, Clojure, Dart, Zig, Perl, F#, and Visual Basic—
remain validation-relevant but are not falsely called structurally supported.
A repository containing them is `unsupported_language` or
`incomplete_source_coverage`, not `no_supported_source` and not a regex-derived
fake graph.

The central sensor, source-revision classifier, syntax probes, indexer, and
legacy bridge inventories now consume this registry.

### Certified and incremental graph lifecycle

`IndexBuildReceipt` records:

- status and exact failure type;
- graph path and graph SHA-256;
- index-binary SHA-256;
- elapsed milliseconds;
- source and indexable file counts;
- unsupported suffixes;
- schema validity;
- node and edge counts; and
- discovered FTS tables.

The required schema includes `nodes` and `nodes_fts`, and SQLite
`PRAGMA quick_check` must pass. Full builds publish `graph.db` and its
certification manifest atomically.

`RepositorySession` tracks both current source revision and indexed source
revision. The first refresh is full. Changed indexable files use the shipped
binary's real `-file` path followed by `-rebuild-closure`, validate the
candidate database, then atomically replace both graph and manifest. An
unchanged source revision is a zero-cost revision cache hit. Source deletion
requires a full rebuild. Unsafe paths, missing sensor content, or sensor
degradation invalidate the session.

### Positive-line graph semantics

FTS retrieval now joins back to canonical node identity so every selected
structural fact has a real positive source line and signature when available.
Duplicate rows reached through `nodes_fts`, `symbol_content_fts`, and content
passages collapse to one canonical node fact. Each fact carries semantic
certainty and retrieval relevance. Directed callers require a certified
`CALLS` edge with confidence at least 0.95, certified trust tier, and one
candidate.

### Incremental context frontier

`gt_engine/context_frontier.py` defines typed facts and dispositions. The
compiler compares current repository evidence with the exact messages about to
reach the provider. It selects only facts absent from that view and delivers:

- definitions/signatures;
- certified callers;
- concrete references; or
- concrete tests.

Each selected fact includes path, positive line, symbol, relation/value,
source revision, graph revision, semantic certainty, retrieval relevance, and
a stable fact ID. Limits are three facts and 1,200 characters per call, with a
6,000-character task cap. Complete facts are omitted rather than truncated.
Stale, unhealthy, low-precision, already represented, duplicate, and
over-budget facts get exact non-delivery dispositions.

Every model call receipts candidate count, accounted count, selection,
rendered text, request hashes, provider message index, exact characters,
first-eligible timing, and non-prediction. Duplicate delivery and incomplete
accounting invalidate the treatment.

### Typed action certainty and sensor cost

`ProposedAction` now records mutation certainty, parse coverage, opaque
segments, and unknown segments. Shell control words are structural syntax, not
fake executables. Heredoc/interpreter programs remain opaque. A workspace scan
is skipped only for `PROVEN_READ_ONLY`; `MAY_MUTATE` preserves the historical
postflight scan. This saves deterministic sensor executions without changing a
model command or weakening ambiguous cases.

### Experiment validity and deep metrics

The active treatment is invalid when any required task has:

- unavailable/incomplete/invalid repository evidence;
- a stale source revision;
- a substrate or stale frontier decision;
- incomplete frontier accounting;
- duplicate frontier fact delivery;
- a frontier task-budget violation; or
- zero model-visible incremental frontier deliveries.

Operational behavior still fails open: the model loop continues. Experimental
promotion fails closed: `compare_arms` rejects required repository-intelligence
failures, and the paid merge step exits nonzero while retaining artifacts.

Deep metrics now include frontier context in
`total_gt_context_chars_added`. Per-task output includes repository status and
failures, schema validity, source/indexable file counts, node/edge counts,
refresh count, frontier calls/candidates/accounting/deliveries/facts/chars,
duplicates, provider hashes, controller executions/cache hits, model actions,
API calls, tokens, time, outcome, and censoring.

The archived run-diff comparator now treats repository-frontier delivery as
visible evidence when locating whether model divergence preceded GT exposure.

## Workflow and gate changes

- Both paid workflows explicitly enable the context frontier.
- The provider-free workflow runs and statically checks the intelligence-layer
  and repository-index tests/modules.
- The pre-smoke gate includes frontier, graph-health, read-only cache, and
  invalid-treatment outcome tests.
- Readiness proves that frontier compilation precedes `model.query`, paid
  workflows enable it, provider-free tests cover it, and intelligence failure
  reaches the outcome gate.
- The all-17 census additionally emits `REPOSITORY_SUBSTRATE_PROVEN` and
  `CONTEXT_FRONTIER_PROVEN` using the real binary-to-SQLite fixture plus a
  source-backed frontier fixture.
- The paid merged report shows frontier deliveries/chars, graph nodes/edges,
  and intelligence status for every task. Any invalid active task fails the
  merge step.

## Provider-free evidence

At the time this document was written:

- the exact provider-free release surface passed 289/289 tests;
- the complete repository suite collected 1,042 tests and completed with 1,039
  passes plus three platform/coverage skips and zero failures;
- the all-17 census printed every permanent producer/consumer/timing/context
  line plus `REPOSITORY_SUBSTRATE_PROVEN` and `CONTEXT_FRONTIER_PROVEN`;
- `scripts/central_readiness_audit.py` printed `READY`; and
- the real index fixture certified 1/1 source coverage, schema validity,
  `nodes_fts` and `symbol_content_fts`, two nodes, one directed call edge, and
  matching graph/binary hashes;
- archived run `31223362041` passed `REPLAY_OK`,
  `ARCHIVED_REGRESSION_REPLAY_OK`, and `ARCHIVED_EFFICIENCY_REPLAY_OK`; and
- Ruff and `git diff --check` passed on the changed implementation.

The final verification command/results should be read from the handoff and git
state at dispatch time; exact pushed-commit approval is intentionally not
claimed while implementation changes remain uncommitted.

## Research basis

The design uses the model interface as a control surface rather than adding a
second reasoning agent. SWE-agent shows that agent-computer interface design
materially affects behavior: <https://arxiv.org/abs/2405.15793>. Agentless
supports a disciplined localization → repair → validation lifecycle instead
of gratuitous controller complexity: <https://arxiv.org/abs/2407.01489>.
RepoCoder supports iterative, selective repository retrieval rather than a
one-time repository dump: <https://arxiv.org/abs/2303.12570>. Lost in the
Middle supports the strict bounded-frontier policy because correct evidence
can become hard to retrieve inside long contexts:
<https://arxiv.org/abs/2307.03172>.

These sources motivate the architecture. They do not establish that this
implementation improves this model/task distribution.

## Guarantees and non-guarantees

Provider-free code/tests can guarantee deterministic classification,
source-revision binding, fail-open execution, fail-closed experiment validity,
bounded/deduplicated context, exact exposure receipts, and replayability.

They cannot guarantee zero stochastic solve regressions from a temperature-1
model. Deterministic GT can remove avoidable harness variance and prevent a
broken intelligence arm from being promoted; it cannot make every model sample
identical or know an unobserved counterfactual. A no-regression benchmark claim
still requires repeated matched runs and outcome-first gates.

## Remaining work

1. Add certified structural parsers for any authored languages in the matched
   smoke that the shipped binary does not support (notably COBOL/Scheme-family
   inputs if present). The new gate intentionally rejects rather than faking
   those tasks; therefore this implementation is not permission to fund a
   smoke known to contain unsupported structural coverage.
2. Commit and push only after the final diff is reviewed. The implementation
   is currently a verified working tree on HEAD `0b825ca`, not a pushed commit.
3. Run `scripts/central_pre_smoke_gate.py` at that exact pushed commit; it must
   print `SMOKE_APPROVED`.
4. Obtain separate authorization for a matched ten-task live smoke.
5. Require every task to pass graph/frontier/timing/payload/outcome accounting,
   then compare solve, tokens, API calls, model actions, effective task
   executions, controller work, and time against the frozen GT-off baseline.
6. Repeat the matched treatment before making a causal efficiency claim.
7. Keep the 89-task run blocked until those gates pass.
