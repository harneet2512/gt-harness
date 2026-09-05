# Benchmark repair implementation register

Status: implementation in progress; **not benchmark ready**. No paid run is
authorized by this document. Preserve the local closeout artifacts. Follow
AGENTS.md and the dispatch checklist before any external execution.

## Acceptance contract

GT is the engine supporting Mini-SWE's reasoning and actions on the canonical
Actions / DeepSWE / Harbor / Mini-SWE 2.4.6 / certified producer path.
Feature emission is not evidence of delivery, execution, usefulness, or reward.
Every capability needs a positive native consumer test and relevant negative,
stale-state, and failure tests. Installed-bundle tests must bind the exact source.

Benchmark readiness requires functional completeness, conserved task artifacts,
honest missing/failed results, and provider-free installed acceptance. Benchmark
superiority requires subsequent official outcomes and measured resource use.
Neither is established by passing unit tests. "Exponentially more efficient"
is not an acceptance criterion without a scaling variable and measured curve.
Report input/output/cache tokens, requests, tool work, startup, indexing,
embedding, time, and resource use including failed attempts. Do not manufacture
efficiency with arbitrary context cutoffs or omit cold-start costs.

Keep the retained Muse baseline read-only; do not rerun it. DeepSeek results are
not a causal GT-on/off experiment against a different Muse model. After exact
provider-free acceptance, request approval for one task; the remaining fixed20
needs separate approval. Full113 repeated trials follow diagnostics and a frozen
configuration, never smoke repair.

## Observed defects and implementation order

Evidence anchor: investigated harness de683e73e7f05040be8f28ac725d79cbd82d6218,
producer 84e19be7011fd3b94d8e28616402898e73849bc0, failed task run 33954483663.
The task spent about 25 minutes before provider attachment, versus 39.260 seconds
reported for the Go graph build. It then made 95 requests / 94 recorded responses
and left an empty patch and missing terminal receipts at the outer timeout.
This is evidence of lifecycle failure, not evidence that retrieval is useless.

| ID | Reproduced problem | Repair and required proof | State |
| --- | --- | --- | --- |
| F1 | Startup precedes agent timer; in-flight work can overrun; finalization depends on return | Absolute supervisor deadline before startup, cancellable work, independent patch/receipt finalizer; hang/startup/termination subprocess tests | Supervisor, conservation and real Linux detached-descendant tests pass; progressive dense startup and exact installed release proof pending |
| F2 | CAST on indexed resolution native ID causes repeated scans | Cast node ID to canonical text; preserve nodes-only and derived-ID fallbacks; exact row parity and query-plan proof | Implemented; surrounding source tests passed |
| F3 | Empty repository diff retains guessed external edit; view commands can overclaim | Authoritative complete snapshots including empty diffs, contained fallback paths, actual successful view receipts; external/internal/compound/symlink tests | Edit containment implemented; view receipts pending |
| F4 | Any overlay disables the entire graph query path | Base+overlay query facade with dependency coverage; migrate consumers before allowing selective old facts | Pending |
| F5 | Frozen input omits configs/history; temporary roots destroy reuse; duplicate builds | Complete producer input manifest, stable cache identity, running/pending/completed dedup; revision and byte-change tests | Coordinator dedup and resolver/discovery configs implemented; frozen history and stable staging pending |
| F6 | TS overload newline parser defect and TSX misdispatch; incomplete registry/coverage | Pinned local TS/TSX bindings and scanner correction; malformed syntax still rejected; build-bound dialect registry and per-file parse coverage; exact Linux bundle | Parser correction implemented in producer checkout; full Go suite passed; registry, coverage and repackaging pending |
| F7 | Arbitrary character limits remove evidence and history without native exact recovery | Complete evidence units, content-addressed allowlisted evidence.read, supersession/dedup, reasoning/tool protocol conservation; physical model-limit handling | Pending |
| F8 | Selection advances chain/consumes candidates before confirmed exposure; recovery bypasses delivery | Pending exposure transaction committed only on exact provider payload; rejection rollback/requeue and state-bound recovery | Gateway, verification and recovery commit at exact-byte request binding; whole-request chain prevalidation tested; installed native all-feature proof pending |
| F9 | Producer edit/test exclusive branch loses multi-event work; execution not separately witnessed | Independent execution records before arbitration, parser/check/verification receipts; advisory compute separate from enforcement | Compound-event gateway repaired and installed pipeline tested; full execution-record conservation pending |
| F10 | Warm/cold embeddings use different text; tokenizer silently truncates long contracts | One versioned representation, complete model-window chunks, independent lexical/dense retrieval, source-span results and dedup fusion | Pending |
| F11 | Catalog/context packet/LSP/richer graph features lack full native consumers | Native typed consumers and graph-bound execution/scheduling receipts; test execution, cancellation, freshness, fallback | Pending |
| F12 | Paid acceptance allows zero exercised features; stale tracked matrix reused on issuer failure | Native eligible-case corpus, fresh per-run RED matrix with diagnostics, isolated Git fixture identity, exact bundle/workflow closure | Fresh per-run RED matrix implemented; native corpus, diagnostics and full closure pending |

The downloaded request history had 14 distinct GT payloads, 62 occurrences
across requests, and zero extra duplicates inside an individual request.
Repeated history is not itself duplicate injection. Preserve this distinction.
Five of six graph invalidations were for outside-repository scratch files.
The prior SQL comparison returned identical 3,778 rows; measured query times
29.1994 seconds versus 0.1331 seconds are query-specific, not end-to-end claims.

Work packages: A lifecycle/startup/F2; B producer inputs/parser; C repository
state/coordinator; D evidence/exposure/native multi-event; E retrieval and missing
consumers; F acceptance. Small independently verifiable fixes may land earlier;
no package is complete while its required boundaries remain untested.

## All 19 feature identities

These are intended contracts, not claims of current completion. Each must acquire
an installed native positive witness and its failure/freshness counterexample.

| Identity | Required capability | Main repair dependency |
| --- | --- | --- |
| caller_contract | Expose source-bound caller assumptions affected by an edit | F4,F6,F7 |
| cochange_prior | Retrieve history-supported companion edits, explicitly as priors | F5,F7 |
| covering_red | Link actual failing test execution to affected edit surface | F9,F11 |
| def_partition | Distinguish declarations, references, and ambiguous resolution | F4,F6 |
| localization | Rank actionable symbols and recoverable source spans | F10 |
| newfile_precedent | Find destination/template/registration precedents for new files | F3,F4,F10 |
| obligations | Track task requirements and their actual supporting evidence | F7,F8,F9 |
| recovery | Supply state-bound falsification evidence after unsuccessful edits | F8,F9 |
| signature_delta | Compare callable contracts and identify impacted callers | F4,F6,F9 |
| submit_refusal | Enforce explicit configured RED policy, not arbitrary agent blocking | F8,F9 |
| syntax_result | Report executed parser/check results with dialect and completeness | F6,F9 |
| GT_CERT_DELIVERY | Deliver exact completion-state evidence bound to provider bytes | F7,F8 |
| GT_CHANGE_SURFACE | Execute change-surface analysis and expose its witnessed result | F4,F9 |
| GT_EDIT_CHECK | Execute edit checks and retain actual outputs/failure status | F6,F9 |
| GT_HYPOTHESIS | Execute recovery/hypothesis checks and bind results to current state | F8,F9 |
| GT_LOC_RESLOT | Deliver localization at the useful native lifecycle boundary | F8,F10 |
| GT_PATCH_DELTA | Describe one actual multi-file transaction, including new/deleted files | F3,F9 |
| GT_SS_SUBMIT_RED | Suppress submission only under explicit policy with exact receipt | F8,F9 |
| select_catalog | Let the model select from a versioned, source-bound capability catalog | F11 |

## Architectural capabilities beyond the 19 names

| Capability | Intended behavior / proof obligation | Dependencies |
| --- | --- | --- |
| Source content addressing | Exact source bytes and complete producer inputs identify reusable work | F3,F5 |
| Behavioral contracts | Deterministic structured facts with original row provenance, not invented prose | F2,F6 |
| Contract embeddings | Same versioned representation on warm and cold paths; no unrepresented tails | F1,F10 |
| Hybrid retrieval | Independent lexical and dense candidates, fused without losing source identity | F10 |
| Semantic invalidation | Invalidate affected facts/dependents while preserving proven unaffected facts | F4,F5 |
| Communities and cochange | Structural communities and historical priors remain distinct evidence types | F5,F11 |
| Test-witnessed processes | Actual test execution supports process/coverage relationships | F9,F11 |
| Declaration taxonomy | Dialect-correct definition/reference classification | F6 |
| Typed relations and ambiguity | Preserve alternatives/unknowns instead of fabricating unique resolution | F4,F6 |
| Why-edge explanations | Explain an edge through producer evidence and dependency provenance | F4,F11 |
| Pass provenance | Bind each result to producer/version/input and executed pass | F5,F6,F11 |
| Overload narrowing | Resolve candidates only to the extent syntax/types support | F6,F11 |
| MRO and inheritance | Expose inheritance lookup with language-specific ordering and ambiguity | F6,F11 |
| Expensive-pass scheduling | Coalesce work, cancel superseded jobs, reuse exact inputs, measure cost | F1,F5,F11 |
| Core-analysis publication | Publish immutable graph artifacts only for matching source revision | F4,F5 |
| LSP integration | Execute graph-bound requests; record unavailable/cancelled/failed states honestly | F11 |
| Observation reconciliation | Reconcile actual filesystem and execution evidence, not command intent | F3,F9 |
| Context/evidence retrieval | Complete evidence units with safe exact-byte recovery and visible freshness | F7,F8,F11 |
| Verification planning | Choose relevant checks, then separately witness actual execution/results | F9,F11 |
| Execution conservation | Preserve patch, trajectory, request/response accounting and typed terminal state | F1,F12 |
| Attribution/acceptance | Bind eligible feature execution, delivered bytes and outcomes to exact build | F8,F12 |

## Research constraints

GitNexus comparison is mechanism-level, not proof GT inherits its behavior:
[independent hybrid branches and fusion](https://github.com/abhigyanpatwari/GitNexus/blob/a049b2dac6433b3c13185e226483fa85743dab1e/gitnexus/src/core/search/hybrid-search.ts),
[pinned analysis/cache pipeline](https://github.com/abhigyanpatwari/GitNexus/blob/7e993ab8972386294fb96bf14a8665d0b5325397/gitnexus/src/core/run-analyze.ts).
Use [ContextBench](https://arxiv.org/html/2602.05892v1) to distinguish retrieval
from retention, [AgentRetrievalBench](https://arxiv.org/html/2607.24882v1) for
complementary retrieval intents, and [LocAgent](https://aclanthology.org/2025.acl-long.426/)
for graph localization mechanisms. These motivate ablations, not promised gains.
[Salsa's dependency/revision algorithm](https://github.com/salsa-rs/salsa/blob/master/book/src/reference/algorithm.md)
informs explicit dependency validity.
[Complexity Trap](https://arxiv.org/html/2508.21433v2) cautions against assuming
extra orchestration is automatically efficient. Do not implement hidden-state
pruning from [SWEPruner Pro](https://arxiv.org/html/2607.18213v1) through a provider
API that does not expose the required hidden states.

## Verification ledger

Initial new regression run: four intended failures (indexed join, repeated
build, external native-loop edit invalidation, external preimage read) and one
passing failed-build retry check. Subsequent outcomes must be recorded with
commands and scope; do not round local source tests up to installed acceptance.

Implementation checks so far:

- First contract/coordinator/runtime group: 75 passed.
- Matrix issuer/workflow group: 8 passed.
- Runtime/integration/supervisor/parity group: 104 passed before later input changes.
- Resolver-configuration and omission checks: 6 passed.
- Latest matrix/runtime/supervisor group: 60 passed; lint on the changed runtime,
  coordinator, query, indexer, issuer and supervisor modules passed. The runtime
  test module now supplies its own disposable Git identity rather than relying
  on global CI configuration.
- Producer: `go test -tags sqlite_fts5 ./...` passed all packages, including real
  inspection-binary execution. This is a local Windows build, not the certified
  Linux shipping binary.
- Harness-wide source run: `python -m pytest -q -p no:cacheprovider -o addopts='' tests`:
  1,305 passed, 92 skipped, 2 failed in 235.27 seconds. Both failures were
  `source_closure_differs_from_head`: acceptance correctly rejects this uncommitted
  worktree. Do not weaken that guard. Later edits require rerunning affected tests.
- Downloaded task graph replay after the query edit: 3,778 ordered rows,
  byte-identical serialized row digest
  `83b7db1f2f0599abdb51689c70cfbf53624dc53b21e2f71bb9b78426d1102c4f`.
  Original CAST join: 4.844691 seconds; indexed join: 0.019492 seconds in this
  warm-cache run. Environment/cache state differs from the earlier investigation;
  neither pair is a promised end-to-end speedup.
- Docker Linux daemon is not running locally. Linux subreaper/setsid-descendant
  teardown and the rebuilt installed bundle remain unverified.

Additional packaging finding: the configured Go producer source checkout at
84e19be7 does not contain `src/groundtruth/runtime/gateway.py`, although the
vendored wheel does. The local Groundtruth checkout at f2863f87 has byte-identical
gateway source (SHA-256 bbceffea7c69cec27d6f3a15c9af33d10c16c64f2708096380ebb2ea00d97182).
Do not rebuild a complete wheel from the Go producer checkout and assume runtime
parity. Resolve and record the Python runtime source lineage before F9 or bundle
reissuance. The other checkout's existing hook/research changes remain untouched.

### Unified source continuation

User approved the isolated unified source branch. Candidate source is
`473ebdb8324d175fc911a727d9d63e5efcbcfc08`, tree
`7147a587ee387318ea2dc838b2a8afcf6ae269f5`, pushed to
`final_hardening/har83-unified-source` in Groundtruth. Local checkout:
`D:/gt-har83-unified-source`. Existing producer/runtime worktrees are preserved.

The exact wheel import and parser repair provenance is disclosed in that
branch's `UNIFIED_SOURCE_PROVENANCE.md`. This is a disclosed reconstruction
from the shipped artifact, not a claim that the old producer commit built it.

New executable correspondence check: `scripts/verify_wheel_source.py`.
It compares every package source/resource byte and rejects missing, extra,
changed, duplicate, or unsafe wheel paths. Six regression cases pass. The old
producer source fails with 63 missing and 128 byte-different package files;
the unified source passes all 317. No implicit newline normalization is used.

Candidate wheel built using Hatchling 1.32.0:
`D:/gt-har83-unified-source/dist/groundtruth_mcp-1.0.0-py3-none-any.whl`, SHA-256
`2e718638039def2b08cd5c14a66dd0fee5d3934c5780d1118d340f7ff820406e`.
All 317 runtime package files match the current shipping wheel exactly, and
dependency metadata is identical. Other archive differences are the backend
generator/metadata version, producer README description, license line endings,
and RECORD. Repeated local builds produced the same wheel hash; cross-platform
whole-wheel reproducibility is not yet proven.

The candidate was installed into a fresh isolated target. Import origin was
verified; 121 focused harness tests passed against that installed package.
Unified-source TypeScript/TSX specs tests passed. Full Go suite and remote
checks are pending at this ledger entry:

- Linux producer build: Groundtruth Actions run `33982276921`.
- Source CI: Groundtruth Actions run `33982276955`.

Release wheel/producer pins have NOT been replaced. Independent review,
source-bound Linux identity, and installed provider-free bundle acceptance
remain required; these candidate checks do not establish benchmark readiness.

Subsequent results for this candidate:

- Full local unified-source `go test -tags sqlite_fts5 ./...`: all packages
  passed, including the real inspection executable tests.
- Another 32 harness supervisor/parity/matrix/workflow/correspondence tests
  passed against the candidate installation; changed-file Ruff passed.
- Linux build `33982276921`: SUCCESS. Downloaded producer SHA-256
  `8a15ca95a820035bb25b584a7ccd1893ae28d8b885fe192630c1dd906dc82622`.
  Receipt reports complete identity, candidate commit, Go 1.22.5, and tags
  `netgo,osusergo,sqlite_fts5`. Independent re-hashing of all 182 compiler
  inputs from the Git archive with `core.autocrlf=false` matched receipt
  fingerprint `e4e6eeac413661aef96ec9cbbc43253506573b5e6c80882523e781a0a6cc79de`.
  Using Windows autocrlf conversion initially mismatched; it is not the Linux
  compiler input representation. No receipt was changed to make it match.
- Source CI lint FAILED. Local reproduction: 145 findings (34 F401, 36 E741,
  27 E702, 19 F841, 18 E402, and 11 other findings). The runtime pyproject
  import also omitted the producer branch's explicit Ruff policy; reconcile
  packaging dependency declarations separately from tooling configuration.
  Do not erase the lint failure or claim re-certification. Fixture benchmark
  passed; other source CI jobs were still running when this entry was written.

### CI and runtime repair continuation

Source repair commit `cac42da5c15a6c1dca20280a8d6d75abd488efd4`, tree
`44bd08931b86826a02645a4eb5c7c6f029dcbb2a`, is pushed on the unified-source branch.
It restores the pre-integration producer Ruff policy, fixes remaining lint,
and applies formatting with an AST-equality check for the formatting phase.
It also repairs raw trace hint CWD dependence, verified relative-path escape,
and missing verified-witness precedence in terminal evidence reranking.
The flat-score confidence gate is preserved. Stale tests were reconciled with
the recovered source contracts, not with weaker production safety behavior.

Verification for that commit:

- Clean isolated dev environment: 4,110 passed, 166 skipped, four xfailed,
  43 warnings in 422.39s. Same three historical CI exclusions remain; skips,
  warnings and expected failures are not full capability acceptance.
- Earlier shared-global-environment full run timed out importing unrelated
  TensorFlow/Google packages. It was not counted as a passing run.
- Repaired installed wheel: 147 focused harness checks passed. Wheel SHA-256
  `abf1f119a9ec0f974459506337bb68e6e6f3bb0a23cc04372dce2a6c0ffc7123`;
  every packaged file matched its repaired source (317 files).
- Mutation proof: removing witness precedence only in an isolated process
  made the real generated-brief ranking regression fail; the unmutated source
  and trace boundary checks subsequently passed.
- Source CI run `33983786327`: lint, fixture benchmark, Go and Linux/macOS
  Python jobs passed at last inspection; Windows jobs remained running.
- Linux producer run `33983786330`: SUCCESS. Downloaded executable SHA-256
  `3aed92540158500c1492a7d88cd86e73cda0a735c5b924cc8cf0d5dee2138be6`,
  complete receipt bound to cac42da5c and unchanged compiler input fingerprint
  `e4e6eeac413661aef96ec9cbbc43253506573b5e6c80882523e781a0a6cc79de`.

Additional harness fix: public `gt-miniswe-run` now points at the supervisor,
not the unsupervised worker. The new configuration check failed before the
change; 18 supervisor/parity checks passed after. A freshly built and installed
harness console entry was invoked with a zero budget: exit 3, timeout, typed
ERROR receipts, no worker launch and unknown provider counts (not fabricated
zero counts). This harness change is still uncommitted with the earlier repairs.

F9 continuation beyond cac42da5c: a real covering-failure envelope was produced
for a test-only event but suppressed for an otherwise identical edit+test
event. Independent `if` dispatch replaces exclusive edit/test/search branches.
Three source regressions cover a real failing subprocess, compound-event
preservation, and authoritative-empty-event non-invention. Twenty adjacent
gateway/localizer/trace checks pass. This does not yet close all F9 execution
receipt and native delivery obligations. Rebuild/re-certify again after it lands.

### Independent review and execution-proof gate repair

The user authorized an independent read-only review. Its verdict is NOT
BENCHMARK-READY (high confidence), not a formal source certification packet.
Concrete findings remain: shipping pins still select old artifacts; package/source
correspondence is not a mandatory gate; the paid threshold permits zero witnesses;
recovery consumes delivery state before provider exposure; a partial pending
exposure chain can count delivery before chain commitment; graph access globally
abstains after edits instead of providing dependency-aware overlay queries;
character truncation has no demonstrated exact evidence recovery; Linux setsid
descendant teardown is not runtime-proven. Native all-capability acceptance and
outcome/efficiency evidence remain absent.

One additional reproduced gate defect is now repaired locally: feature-matrix
issuance accepted pytest exit zero for skipped and xfailed tests as WITNESSED.
Real subprocess RED witnesses confirmed both false positives, plus acceptance
without any execution receipt. The collector now retains collected node IDs and
setup/call/teardown outcomes. Issuance and verification require every selected
node (including parameterizations) to execute all three phases successfully,
without skip/xfail/XPASS. Missing reports and missing selected nodes fail closed.
Digest-valid legacy exit-code-only WITNESSED cells are rejected even without
the require-witnessed option. This proves test execution, NOT native feature
delivery or behavioral relevance; legacy fixture bindings still need replacement.

Latest source F9 commit is `1ecd03674f7eb6a79f401c95bf147423379d5143`.
Its installed wheel SHA-256 is
`4c4ba9ac08ee8f352e125be69bc0e60d9fc540af1a04b4fe5010d9ac8c1f488f`.
Installed evidence/runtime/integration checks: 105 passed. Prior source CI
33983786327 subsequently finished fully green. Latest producer build
33984442405 succeeded; latest source CI 33984442290 was still running at this
entry. Release pins and formal certification remain unchanged.

### Recovery exposure repair

Reproduced premature recovery consumption: after the first post-edit recurrence,
`_recovery_delivered` was already 1 with no provider request. Scheduling now
records `recovery_prepared` and preserves the comparison epoch and delivery
budget. Recovery is admitted at the transport boundary through ordinary evidence
admission, included in the final request before physical-context validation,
and committed only when its exact rendered bytes bind to that request.
Refusal and omission preserve retry; an intervening edit invalidates the old
pending proposal without spending the budget. Epoch/failure-bound rendered
identity permits a genuinely later recurrence without duplicate identity refusal.
Recovery is transient to the transport request, not inserted permanently into
agent history. The two-exposure task ceiling is unchanged.

Verification: 109 evidence/runtime/integration/recovery tests passed; Ruff passed.
The runtime hook test uses a recording transport (no provider call): an oversized
request refuses before transport and retains recovery; retry transmits the exact
bytes and commits once; the next request omits the consumed steer. Separate
tests cover formatter omission, missing delivery, same-epoch repetition, and
intervening edit. Request binding is not a provider-response or efficacy witness.
These harness changes remain uncommitted. Full installed release acceptance and
the other F8 partial-chain issue remain open.

Latest unified-source CI 33984442290 subsequently completed SUCCESS for
`1ecd03674f7eb6a79f401c95bf147423379d5143`. This does not certify the dirty
harness or update shipping artifact pins.

### Whole-request exposure-chain validation

The F8 partial-chain defect was reproduced in two forms: exposing B without
its prepared predecessor A, and a valid A followed by a conflicting B. Both
requests previously returned normal delivery while logging a conflict after
delivery counters/receipts had already changed.

`bind_provider_payload` now validates all matched pending chain transitions
against a provisional head before storing the request or mutating delivery,
dedup, verification-consumption, iteration, or chain state. A conflict records
a `request_refused` disposition and raises `ExposureChainConflict`; runtime
discards the refused proposals and does not call transport. Correctly re-prepared
evidence can be admitted. An omitted successor does not invalidate its valid
prefix. This is atomicity with respect to chain validation, not a claim of
transactional filesystem durability under arbitrary journal-write failure.

Verification: 112 recovery/evidence/runtime/integration/chain checks passed,
followed by four focused chain checks including two added cases for valid-prefix
acceptance and preserving a pending verification candidate. Ruff passed on the
implementation and initial tests; diff whitespace check passed (CRLF warnings).
Recording-transport integration confirms refusal makes no transport call, leaves
delivery count zero, and allows correctly re-prepared evidence through once.
No provider call or certification mutation occurred. Changes remain local and
uncommitted; whole-product benchmark readiness remains NOT PROVEN.

### Mandatory source correspondence and Linux lifecycle proof

Groundtruth lineage verification now includes mandatory byte-for-byte package
correspondence and manifest wheel-hash validation. The existing canonical
provider-free workflow calls this verifier, so this is no longer a manual-only
supplement. The verifier script and pytest execution collector are included in
the bundle source closure. A synthetic clean-source/review fixture first
accepted a different wheel with its correctly updated manifest hash (RED).
It now rejects it, and malformed ZIP input produces a typed FAIL measurement.
Ten provenance/package checks passed; Ruff passed.

Actual component recheck: current shipping wheel versus declared producer
source FAILS (317 packaged files, 63 missing source files, 128 changed).
Unified-source candidate wheel versus candidate source PASSES all 317 files.
This is not certification; old release pins deliberately remain unchanged.

Started the installed Docker Desktop application to make the Linux engine
available. In an ephemeral network-disabled container, repository mounted
read-only, seven supervisor tests passed in 9.33s. Image identity:
`sha256:7e4fe4cb58b024222e8638e0afa4a52fb4f77f80a061b55502fda369790da839`.
Two new tests use real setsid descendants ignoring SIGTERM: deadline and external
TERM both reap owned descendants before finalization, retain the last edit,
and preserve an unrelated sibling process. The CLI patch/error-receipt test
also passed on Linux. This validates the supervisor component in that image,
not the exact certified shipping bundle or a killed paid task.

Full Windows harness regression is being audited separately. The first run
disabled plugin auto-loading and therefore lacked pytest-asyncio; these failures
are an invocation defect, not passing product evidence. Explicitly loading the
declared plugin gives 13 passing Mini-SWE parity tests. Other full-suite failures
still need classification before any all-green claim.

### Immutable checkpoint and strengthened interrupted-attempt proof

Harness checkpoint `8ac9f8139cdb171b36b86cce05618b7f4666ef3e` is committed
and pushed. Protected-pattern scan and repository construction hook passed;
local closeout artifacts remain untracked and untouched. Its canonical CI
33985762952 failed solely on `wheel_source_correspondence_mismatch` (63 missing,
128 changed): expected rejection of the old pinned package, not a new crash.
The two bundle tests formerly blocked by dirty/untracked closure now pass.
Seven attestation test failures were stale synthetic execution receipts;
fixtures now carry the required phases. The 77-test affected attestation/parity/
closure suite passes. A corrected complete harness rerun is in progress.

Built harness wheel SHA-256:
`00f5dc83fc97c35d9d08effe983f2ed77c13742db60d981db12eed4c4425a672`.
Installed this and the repaired Groundtruth wheel into an ephemeral Linux
container with networking disabled, mounting only wheels and tests (no source
checkout). Nine recovery/chain/descendant tests passed in 8.48s.

Independent review of 8ac9f813: 32 focused checks passed in 85.07s; no new
blocking regression found in the four repaired surfaces. Recovery consumption,
partial-chain commitment, skipped-test witnessing and manual-only package
correspondence findings are closed. This is not a formal release certificate.

Additional Linux tests now join the interrupted worker and finalization paths:
unchanged supervisor main, teardown, Git patch export and failure receipt issuer
run with only the worker command substituted by a real hanging fixture that
edits the repository and spawns a detached SIGTERM-ignoring child. Both deadline
and external TERM produce exit 3; descendants are gone; model.patch includes
the edit; the original index is untouched; journal hash/byte count match; both
receipts are ERROR; provider counts remain unknown. Nine supervisor checks
passed in 14.29s. This is actual supervisor-main finalization proof with a
controlled worker, not paid Mini-SWE or official-verifier execution.

Corrected full Windows harness rerun completed: **1,336 passed, 94 skipped**
in 353.35s with pytest-asyncio explicitly loaded. Skips are not acceptance
witnesses. Production code under test is the 8ac9f813 checkpoint; subsequent
commit `45d99c7b0dc13722e0a46defa136fcbe71e9e260` adds only the stronger Linux
tests and this ledger. Canonical release acceptance remains RED on the pinned
source/wheel mismatch despite the passing source regression suite.

## Approved model-agnostic implementation program

The user approved the detailed implementation plan: one mandatory GTSession
boundary, authoritative observation/execution receipts, dependency-aware base
plus overlay queries, consistent complete embeddings, recoverable context,
native consumers for every capability, then immutable installed acceptance.
GT is not an optional hint channel. Mini-SWE chooses actions; GT owns evidence
and state processing. Automatic relevant checks are the target, without initial
submit enforcement. Prove one frozen model first, keep model-specific logic at
the provider adapter, and compare the complete product descriptively against
retained Muse. The chosen outcome is a better solve/resource frontier, not a
promised exponential speedup. Paid stages still need separate approval.

### Work package A, first implementation increment

Reproduced a validity bypass: a degraded GT session could inherit an engine's
`verified=True`, and a healthy provider journal alone could yield a valid GT-on
reproducibility manifest without any engine-integrity receipt. Completion now
invalidates inherited verification on degraded/missing/disabled engine state;
GT-on manifests require a matching typed integrity receipt. GT-off preserves its
separate provider/journal contract. Agent output and patch conservation remain
independent of research validity.

Shell executions (including submission paths) and typed execution dispatch now
pass through GTSession.execute. Start/terminal records bind an action digest,
stable execution ID, action index and exact result digest or exception type.
Failures of receipt writing preserve the original result/exception but invalidate
GT assurance. Reopening a journal initially reproduced ID reuse; identity now
includes its preceding journal head. A real native three-action batch covers
success, empty output and a failed subprocess. These are execution-boundary
repairs, not completion of work package A: suppressed-action dispositions,
whole-lifecycle receipt coverage and all-capability acceptance still remain.

Verification for this increment: 149 adjacent tests passed with one skip before
the journal-reopen regression; latest execution tests five passed; native real
three-action batch passed. Built harness plus repaired Groundtruth wheel passed
44 session/execution/reproducibility checks in a network-disabled Linux container
with only wheels and tests mounted. Broad regression: 1,345 passed, 96 skipped,
two failures solely `source_closure_differs_from_head` before committing this
increment. Ruff and diff whitespace checks passed. Skips and synthetic provider
fixtures do not establish full capability or benchmark efficacy.
