# Context + plan implementation tracker

Updated: 2026-09-09. Owner: current implementation session.

## Scope and release state

Implement the approved targeted repairs to evidence, persistent planning,
verification scheduling, incremental graph amendments, snapshots, submission,
and installed product acceptance. Preserve the existing engine, graph consumers,
coordinator, and single planning call. No architectural replacement.

**Overall: IN PROGRESS, NOT BENCHMARK READY.** Work is checkpointed in bounded commits.
Checkboxes below mean the stated bounded work is done, not whole-product proof.
Source tests, installed tests, graph equivalence, and official reward are distinct.

Do not dispatch paid benchmarks, change provider routing, perform
GCP operations, weaken required capabilities, or enable an unproven amendment
capability. The user authorized continuing pushes and CI on 2026-09-09;
push only the scoped working branches and run provider-free CI. No paid calls.

## Exact worktrees and bases

| Component | Worktree | Branch | Base |
|---|---|---|---|
| Harness | `D:/gt-context-plan` | `codex/context-plan-integrity` | `ce309e90613de3aad6ccfa9a8251b009cdef5bea` |
| Producer | `D:/gt-context-producer` | `codex/context-plan-producer` | `193b9d93b0650721be6120ae519ab3a1d8e8137f` |

Both initial HEADs and merge-bases were checked directly against these bases.
The unrelated dirty diagnostics worktree `D:/gt-harness` is untouched.

## 1. Evidence integrity — PARTIAL

- [x] Remove full-suite and lexical-overlap promotion of arbitrary behavior to GREEN.
- [x] Preserve the supported exact relation assertion and live filesystem assertion paths.
- [x] Add explicit CheckSpec and distinguish CHECK_PASSED from PROVEN.
- [x] Reject stale source, incomplete capture, missing test identities, environment
  mismatch, and nonpassing execution in the bound-check classifier tests.
- [ ] Finish evidence metadata propagation through controller receipts, persistence,
  summaries, and consumers; historical receipts must not gain stronger authority.
- [x] Enforce captured test-source/configuration digest matching for bound checks.
  Missing identifiable source remains unverified; implementation edits do not
  silently change the bound test definition.
- [ ] Complete supported-positive and negative protocol coverage, including skipped
  tests, no tests, contradictory output, timeouts, and late-output failures.
- [ ] Prove no unbound or unsupported observation can advance the cursor or gate.

## 2. Verification execution and queue — PARTIAL

- [x] Remove replay of original shell receipts after edits and in numeric reverify.
- [x] Add direct argv execution through the existing isolated environment/worker.
- [x] Add pending-check deduplication and submission-boundary draining.
- [x] Add exact equivalent agent-check observation to discharge queued work.
- [x] Record automatic-check before/after source transactions and invalidate on edits.
- [x] Catch automatic-check exceptions without letting a check submit the agent task.
- [ ] Prove the complete queue lifecycle with real installed executions: repeated
  edits, superseding revisions, multiple bindings, equivalent agent execution,
  mutation during checks, timeouts, and the bounded total verification allowance.
- [ ] Finish verification-boundary draining beyond explicit submission.
- [ ] Verify replay/restoration behavior across persisted state and interrupted runs.
- [x] Automatically bind admissible initial plan commands through CheckSpec and
  group identical executions across requirement bindings. The CLI is supplementary.

## 3. Requirement ledger, plan, and cursor — PARTIAL

- [x] Stop dropping requirements solely because they exceed 500 characters.
- [x] Retain original source lines, fenced examples, and unclassified spans in the ledger.
- [x] Stop truncating executable cursor commands.
- [x] Keep unmapped rows visible in the outstanding set.
- [x] Refresh cursor identity on source/design/check/state changes; prioritize failed
  checks and leave deferred rows after normal pending work.
- [x] Add `rows_with_check_commands`; retain the old misleading count only as a
  labeled compatibility alias, not as executed verification.
- [x] Add gt-plan show/revise/defer/bind-check requests through the existing engine
  and journal owner. A real CLI-to-engine test rejects direct proof grants.
- [x] Add an all-row ID index and retrieval instructions to the plan rendering.
- [ ] Finish full source-span/example delivery and long/unfenced code-block cases.
- [ ] Make omitted designs and interaction cells explicit pending work under the
  existing planner limits; preserve stable requirement identities.
- [ ] Finish anchor/source-revision validation and distinguish existing versus
  proposed symbols/tests in plan data and rendering.
- [ ] Complete counts and exposure evidence: existing/proposed/executed/passed/proven/
  unmapped/model-exposed, plus exact row IDs and rendered digests.
- [ ] Test CLI stale requests, multiple requests, restart handling, invalid check
  specs, deferral, and complete installed automatic cursor delivery.

## 4. Graph reuse and batch amendment — MOSTLY OPEN

- [x] Implement persistent complete ParseResult cache in the producer.
- [x] Cache returns independent parser-local objects; remapped database IDs do not leak.
- [x] Test source/producer/classification invalidation and corruption reparsing.
- [x] Wire cache into the full producer pipeline with cache hit/miss reporting.
- [x] Real producer CLI fixture: cold 0 hits/3 misses; warm 3 hits/0 misses;
  one-file edit 2 hits/1 miss. Cached versus fresh output matched nodes, edges,
  properties, assertions, resolution symbols/callsites/candidates, closure, and
  cochanges for this fixture.
- [x] Implement a staged batch amendment API over one frozen revision, retaining
  unchanged parser nodes and consuming cached complete parser inputs. Properties,
  assertions, and derived layers are still reconstructed; this is not full structural reuse.
- [ ] Copy the certified parent, retain unchanged structural rows, replace only
  changed/deleted structural rows, and remap cached parser-local references.
- [x] Run the existing complete resolver/analysis exactly once per batch; republish
  derived layers without stale facts. Do not guess a narrowly complete resolver.
- [ ] Reuse eligible history/cochange work; preserve embedding caches and LSP bindings.
- [x] Wire a persistent external cache root and batch API through the harness's
  existing one-active/one-pending coordinator.
- [ ] Prove add/delete/rename/import/inheritance/ambiguity/new-resolution-target cases,
  immutable parent behavior, failure fallback, and every graph consumer's semantics.
- [x] Build/certify the actual producer before declaring or enabling its amendment
  capability. The bounded batch capability is now declared and installed automatic
  selection passes. This does not close all-consumer equivalence or release acceptance.

## 5. Snapshots and regression baseline — PARTIAL

- [x] Report surviving command descendants and conservatively disable carried
  snapshots when writers/capture guarantees are unknown.
- [x] Invalidate snapshot carry across automatic-check generations.
- [x] Parse complete baseline output, not the first 20,000 characters.
- [x] Remove destructive source restoration from the baseline checker.
- [x] Detect disappearing/replaced passing test identities as incomplete; aggregate
  counts alone cannot establish intactness.
- [x] Add source/environment/command/output-keyed baseline recheck reuse.
- [x] Record final baseline source mutations and invalidate affected evidence.
- [x] Filter sensitive environment names from baseline child execution.
- [ ] Finish baseline source/config/test/environment identities at initial capture
  and final comparison, including skipped/missing test conservation.
- [x] Route baseline execution through the existing isolated process-tree boundary;
  real installed Linux timeout test confirms the sleeping grandchild is reaped.
- [ ] Exercise background writers, typed mutations, automatic checks, incomplete
  captures, and carried snapshots together in installed Linux tests.

## 6. Submission and official patch conservation — PARTIAL

- [x] Check reserve before expensive submission verification; reread budget afterward.
- [x] Reset progress-related stall state before deciding whether to escape.
- [x] Remove the false promise that the next submission is accepted unconditionally.
- [ ] Close the workflow's 300-step cutoff end to end. Pushed repair `42300c15`
  sets max_iterations=0 and represents unlimited remaining steps explicitly in
  the gate, preserving wall-time reserve. Two RED witnesses now pass; the related
  integration/gate/adapter run passed 49 tests. Installed 301-query/deadline-stop
  proof passed in candidate 07. Successor canonical acceptance remains required.
- [ ] Finish gate reasons separating verified completion, mapping gaps, check evidence,
  baseline uncertainty, and budget/stall acceptance. Acceptance is not correctness.
- [ ] Prove exact 600-second/20-step/three-stall boundaries, budget consumption during
  checks, and progress recovery before a previously reached stall limit.
- [ ] Add current committed-diff/uncommitted-state reporting and bounded finalization
  reminders. Do not automatically commit model changes.
- [ ] Verify official submitted patch versus supervisor recovery artifact separately.

## 7. Installed release and performance — OPEN

- [ ] Complete and audit the 21-capability inventory; retain historical 19-capability
  records without rewriting their evidence.
- [ ] Rebuild current harness and producer artifacts; bind actual source/wheel/binary
  hashes and update the candidate manifest only from real build evidence.
- [ ] Run required installed tests with zero unexplained skips and real journal audit.
- [ ] Run canonical provider-free product acceptance and installed full-flow rehearsal.
- [ ] Run five alternating offline baseline/candidate repetitions over the fixed six
  repository transitions with equal budgets. Report graph blocked versus background
  time, parsed files, resolver passes, checks, snapshots, memory, and semantic parity.
- [ ] Review the final diff, rerun invalidated checks, then commit verified coherent
  changes on the exact branches/bases above. Scoped pushes and provider-free CI
  are authorized; paid dispatch is not.

## Evidence retained so far

- Real pytest shutdown-hook RED exposed `intact` despite a passing summary and
  exit code 1. Final baseline comparison now reports `unknown` for a nonzero
  exit without parsed failure/error attribution, preserving named regressions
  and ordinary preexisting failures. Rebuilt wheel 13 passed 70 installed
  baseline/integrity/gate/check tests, zero skips, in 18.84 seconds
  (`D:/gt-context-proof/baseline-exit-green.xml`).
- Missing-design cursor delivery now explicitly says `design pending` and names
  the existing revision command, without another planner call or graph work.
  The installed omission witness failed before the repair; rebuilt wheel 12
  passed 72 cursor/prefix/check/gate tests, zero skips, in 8.28 seconds
  (`D:/gt-context-proof/design-pending-green.xml`). Interaction omissions and
  full automatic delivery coverage remain open; no broad item is closed here.
- Malformed plan revision values could crash the engine request queue with an
  uncaught AttributeError. Installed RED reproduced two list-payload failures;
  object-shape validation now journals rejection and processes the next valid
  request. Rebuilt wheel 11 passed 54 installed check/cursor/gate tests, zero
  skips, in 7.81 seconds (`D:/gt-context-proof/invalid-request-green.xml`).
  Replay/restart and the complete CLI lifecycle remain open.
- Independent evidence-guard witnesses passed 18 installed tests, zero skips,
  in 6.74 seconds (`D:/gt-context-proof/check-guards.xml`). Stale source,
  incomplete capture, missing IDs, and environment mismatch now each use an
  otherwise valid test-source binding so one guard cannot mask another.
  Protocol/command mismatch, timeout, contradictory exit status, and explicit
  failure classifications also have isolated witnesses. Broader protocol
  conservation remains open.
- Fixture repairs are pushed as `1e1b45fff4f804f7ad3d11078b0aec9a76505e7b`;
  successor provider-free acceptance is `34425339144` (pending at this checkpoint).
- Current checkbox count: 41 completed bounded items, 27 open, 68 total. The
  step-limit release proof above was missing from the earlier 67-item census;
  it is now tracked explicitly. Counts are not an estimate of remaining effort.
- Current candidate pins bind producer `350cb156b01bac708c4ca29674e95392efa9ac68`,
  tree `03afbb192e64c8ed4bd0d39df31530237de9420f`, wheel
  `62e1dca7046fd3df39e8b63749d10dcb8c645a48c4800898eae37125cb555718`, binary
  `2f283b819928be5e3ea38df0eefee11cb9fc55a23006976a9f0f6d9458875e52`.
  Exact build `34424183550` passed. The actual lineage verifier passed with 317
  matching wheel files and ten review packets, including bounded installed proof
  on review branch commit `fb1705edf488de25cdfafb7853dcf5e91e896879`.
- Installed candidate 09: 126 passed, two explained skips, 54.66 seconds,
  network-disabled with current producer wheel/binary and installed harness.
  `D:/gt-context-proof/installed-09.xml`. Repin lineage/adapter regressions also
  passed 17 tests in a separate container with the exact installed producer.
  Shared Windows venv correctly refused the new pin; it remains unchanged.
- CI `34423608011` FAILED eight old fixture expectations already corrected in
  harness `f226dcee`; it predates the producer repin. Final producer CI
  `34424185145` is still pending at this checkpoint. Successor repinned harness
  CI is required; no whole-product acceptance or paid reward is claimed.
- Producer full CI `34424185145` subsequently PASSED all jobs on exact source
  `350cb156`: six Python/OS matrix jobs, lint, Go suite/build, offline fixture
  benchmark. Repinned harness `5c2e4965b49fed28976b438045f46e620f6cd243` is pushed;
  canonical provider-free run `34424824590` is in progress.
- Additional baseline attribution defect reproduced in the installed runtime:
  a newly created failing test was labeled as previously passing. Only the
  intersection with baseline passing identities now receives regression status;
  other new failures remain explicitly `new_failures_unattributed`, never intact.
  Installed regression set passed 62 tests in 16.70 seconds, zero skips:
  `D:/gt-context-proof/baseline-attribution-green.xml`; RED retained beside it.
- Repinned harness CI `34424824590` FAILED two fixture setup assumptions:
  direct GT_INDEX_BINARY lookup instead of the normal resolver, and a fallback
  mock disabling only legacy capability while the real batch capability remained
  available. Both assertions are preserved; fixtures now use actual binary
  resolution and explicitly model no amendment capability. Installed tests with
  GT_INDEX_BINARY unset and the verified binary on PATH passed 45 checks, zero
  skips, 15.85 seconds (`D:/gt-context-proof/ci-fixture-green.xml`).
- New uncommitted producer summary exposes retained/inserted parser nodes, cache
  hits/misses and resolver passes; the harness now parses its batch result. Both
  missing-summary RED witnesses reproduced before repair. Producer batch tests
  passed in 34.059 seconds; harness index tests passed 25 with one explicit
  installed-Linux-only skip. Rebuilt installed summary proof remains required.
- Producer work-counter implementation is now pushed as `8583930b`; its exact
  Linux build is running. Installed harness candidate 07 finished 124 passed,
  1 failed, 2 explained skips in 46.97 seconds. Failure: the preexisting legacy
  `-file` producer path has no `symbols_reminted` field and invalidates the
  resolution sidecar. Keep this compatibility gap open; batch tests passed.
  The installed 301-query/deadline regression passed with synthetic model output
  and zero provider calls. Evidence: `D:/gt-context-proof/installed-07.xml`.
- Harness `42300c15c943a0ea69ae3430d7a94ee18a54cb61` is pushed. Its provider-free
  CI `34423608011` passed the feature matrix and entered the full Python suite.
- Producer `99a5a55620be01531c8e83b9e29bca4a46a9ac8d` declares the bounded
  `batch_parser_node_reuse_v1` capability after nine batch/fresh transition tests
  and the store/parser/command suites passed (command package 143.314 seconds).
  Exact static Linux build `34423978913` passed. Installed automatic selection
  then passed: five changed paths including config, one batch/result/resolver pass,
  parser cache reuse, unchanged nodes retained, immutable parent. Combined graph
  checks: 27 passed, one source-registry-only skip (no producer source mount),
  17.75 seconds; `D:/gt-context-proof/batch-selection-04.xml`.
- The inherited symbol-conservation test now exercises the producer's declared
  batch API when available, preserving the same changed/unchanged symbol checks.
  Legacy single-file behavior remains conservatively incomplete, not falsely
  certified as complete and not an enabled fallback for this producer.
- Exact archive wheel for 99a5a556 matched all 317 package files, SHA256
  `37a526b951e78a4fe7e01bfb1cfa0be038e5d565923e0bbb92f49fa85bce0eb5`.
  Producer CI `34423980548` exposed a Ruff quote-format failure; repaired in
  pushed `350cb156`, 19 protocol tests passed. New exact build and CI dispatched;
  that source formatting change invalidates final artifact pins, not the observed
  earlier runtime results. Release manifest is still on the historical producer.

- Harness checkpoint `8f955cd63e222ac22569b582ebb8deab6aeb7631` is pushed on
  `codex/context-plan-integrity`, with direct parent `ce309e90`.
- Producer cache checkpoint `2e58cb32e7be31e8eaacf29baaa4b3fc1af435fa` is pushed
  on `codex/context-plan-producer`, with direct parent `193b9d93`. Author identity
  is the user-requested `harneet2512`, configured locally to this repository.
- Provider-free CI run `34420192236` FAILED at the feature-proof matrix:
  `persistent_plan: disposition is not WITNESSED` and positive witness did not
  pass. The full Python suite was skipped downstream. This remains unresolved;
  the targeted installed results below do not override this failure.
- CI failure reproduced locally: the persistent-plan positive binding referenced
  a removed test. Rebound it to real isolated queued-check execution and added
  unbound-suite rejection as a negative witness. Both witnesses passed against
  installed candidate 02 on Linux (2 passed, 7.05 seconds). Successor CI required.
- Harness checkpoint `5eaaef8b6f42e7ee2bcbd4a582701950591d616b` repairs the
  feature matrix bindings. Successor CI `34421020506` passed that matrix but
  failed nine Python tests retaining old lexical-GREEN/shell-replay expectations.
  The current working tree updates those fixtures to explicit supported assertions
  and conservatively rejects unbound full-suite observations; successor CI remains required.
- Producer `9df478286816976a71ca227a418bd9b80327e332` implements staged batch
  amendments and recognizes quoted/absolute Python test runner paths. Its Go
  store/parser/command suites passed with `sqlite_fts5`; 105 Python protocol tests passed.
- Producer `3f5e965385a140ebfe18462982df9f5fd6976989` permits empty SQLite WAL
  sidecars while retaining rejection of uncheckpointed data. The actual installed
  Linux batch test exposed the original over-strict guard before this repair.
  Provider-free binary build `34422715093` passed for this exact SHA.
- Installed batch candidate 02: 1 passed, 3.16 seconds, network disabled, real
  compiled Linux producer and installed harness. Verified immutable parent bytes,
  unchanged parser node identity, 1 cache hit/1 miss, complete analysis, committed
  core receipt and no foreign-key violations. Evidence:
  `D:/gt-context-proof/batch-installed-02.xml`. Automatic capability selection and
  full graph-consumer equivalence remain unproven; capability is still undeclared.
- Installed candidate 05: 96 passed, one explained complementary graph-unavailable
  skip, 34.45 seconds. Covers baseline process-tree cleanup, shared check bindings,
  submission reserve and conservative evidence fixtures. Evidence:
  `D:/gt-context-proof/installed-05/results.xml`. This used the previous Go binary,
  not the batch producer, and does not establish whole-product acceptance.
- RED witnesses reproduced lexical/full-suite false GREEN, original-shell replay,
  dropped long requirements, clipped commands, truncated baseline analysis,
  disappearing test identities, and destructive baseline cleanup.
- Focused harness tests have passed after those repairs. Broader eight-file Windows
  regression run passed with five explicitly reported Linux/installed-only skips.
- Producer parser and command-package tests passed; the additional CLI cache
  equivalence fixture passed in 16.722 seconds.
- Installed candidate 01: 59 passed, zero failures/skips, 76.50 seconds. Network
  disabled, installed wheels, no source-package mounts. JUnit:
  `D:/gt-context-proof/installed-01/results.xml`.
- Candidate 01 predates later baseline/check-state changes and is NOT proof of the
  current working tree. Its read-only pytest cache warning is retained.
- Installed candidate 02: 62 passed, zero failures/skips, 18.07 seconds, network
  disabled and no source-package mounts. Includes actual isolated argv execution,
  CLI-to-engine journal admission, and a real queued check. Evidence:
  `D:/gt-context-proof/installed-02/results.xml`; wheel in `wheels-02` beside it.
  This is targeted installed evidence, not whole-product acceptance or a speed study.
- No official benchmark outcome, complete batch-amendment proof, or measured
  end-to-end speedup has been established by this implementation.

## Immediate next work (do not expand scope)

1. Close typed evidence/check-queue integrity and baseline identity/isolation gaps.
2. Add installed lifecycle tests and rebuild the current harness candidate.
3. Implement and prove batch amendments over the existing cached parser/resolver.
4. Finish plan/gate/patch conservation and the feature census.
5. Run installed release and fixed-workload performance gates; then review commits.

Update this document after each material implementation change or verification
result. Do not mark an item complete merely because its type, flag, or test file exists.
