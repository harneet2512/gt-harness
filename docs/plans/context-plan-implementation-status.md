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
- [ ] Implement the approved batch amendment API over all changed paths in one
  frozen revision. **The current cache-backed path still rebuilds the graph.**
- [ ] Copy the certified parent, retain unchanged structural rows, replace only
  changed/deleted structural rows, and remap cached parser-local references.
- [ ] Run the existing complete resolver/analysis exactly once per batch; republish
  derived layers without stale facts. Do not guess a narrowly complete resolver.
- [ ] Reuse eligible history/cochange work; preserve embedding caches and LSP bindings.
- [ ] Wire a persistent external cache root and batch API through the harness's
  existing one-active/one-pending coordinator.
- [ ] Prove add/delete/rename/import/inheritance/ambiguity/new-resolution-target cases,
  immutable parent behavior, failure fallback, and every graph consumer's semantics.
- [ ] Build/certify the actual producer before declaring or enabling its amendment
  capability. No amendment capability has been declared or enabled by this work.

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
- [ ] Finish baseline process-tree/timeout isolation using the existing execution
  boundary; filtering environment variables alone does not close this item.
- [ ] Exercise background writers, typed mutations, automatic checks, incomplete
  captures, and carried snapshots together in installed Linux tests.

## 6. Submission and official patch conservation — PARTIAL

- [x] Check reserve before expensive submission verification; reread budget afterward.
- [x] Reset progress-related stall state before deciding whether to escape.
- [x] Remove the false promise that the next submission is accepted unconditionally.
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
  changes on the exact branches/bases above. No push or paid dispatch is implied.

## Evidence retained so far

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
- Batch-amendment RED test currently fails because `-amend-parent` is absent.
  The test is uncommitted; batch amendment is not implemented or release-ready.
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
