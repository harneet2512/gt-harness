# Context + plan implementation tracker

Updated: 2026-09-10. Owner: current implementation session.

## Scope and release state

Forced-interruption rehearsal 05 collected a gradable exact repair and reaped
the observed process tree, but FAILED: a retried HTTP request advanced the
canned action ordinal and fabricated a later submit response. Rehearsal action
ordinals now count returned commands, so retries remain blocked at the same
action. The interruption census excludes bootstrap requests and permits
unanswered retries; the six-command/no-submit/receipt/teardown checks remain.
RED: `rehearsal-retry-red.xml`; installed wheel 59: 98 tests plus four subtests
passed (`rehearsal-retry-59.xml`). Successor actual interruption remains required.

Canonical provider-free CI `34442196007` PASSED at `f5b9c94f`.
Baseline capture completeness is now mandatory: missing/false capture receipts
cannot produce a captured passing baseline. Both RED cases reproduced the old
false capture; installed wheel 58 passed all 36 baseline/preservation/original
recovery tests (`baseline-capture-58.xml`). No new test execution or graph build
is added by this admission check. Full baseline dependency identity remains open.

Rehearsal 04 now earns verifier reward 1 with the exact official collected
repair patch, stable pre-edit source, bound execution evidence and native graph
refresh. Its original audit was RED because it compared agent turns against
provider calls without adding the two GT bootstrap calls. The audit now uses
the same population as runtime receipts: agent turns plus separately reported
catalog/planning calls. Malformed/negative/bool counters and count mismatches
remain errors. RED: `bootstrap-audit-red.xml`; installed wheel 57: 97 tests
plus four subtests passed. The actual preserved rehearsal journal re-audits
GREEN-delivered with zero integrity issues (`rehearsal04-audit57.json`).
This is synthetic integration evidence, not paid benchmark or all-feature proof.

Continuation: canonical run `34441061328` failed the static planning-off guard
test because it did not recognize the mandatory flag in an AND expression.
The checker now recognizes mandatory positive conjunctions, excludes ELSE
bodies, and rejects OR/negated guards. All 17 planning-off checks passed in
Linux (`plan-off-guard-56.xml`). Production flag behavior is unchanged.

Actual installed rehearsal 03 reached all eight synthetic actions at zero
provider cost, but failed verification: its task omitted the benchmark's
`verifier.collect` hook and the canned agent never committed its repair.
The fixture now tags its base, commits the repair, and uses the normal
`git diff --binary BASE HEAD` collector for `artifacts/model.patch`; the
supervisor recovery patch is not substituted. Planning and execution also
share an explicit existing test-file command so its source digest binds.
RED witnesses: `rehearsal-check-binding-red.xml`, `rehearsal-collector-red.xml`.
Installed wheel 56: 63 passed plus four subtests, zero skips
(`rehearsal-collector-56.xml`). Actual successor rehearsal remains required.

Latest continuation: canonical run `34438068308` rejected the `b68de522`
producer repin because its lineage block retained the previous self-seal.
The source/review verifier had not checked that seal. Both failures were
reproduced (`lineage-seal-red.xml`); the seal is recomputed from the actual
filed block and the provenance verifier now rejects missing or mismatched
seals. The focused Linux source checks passed; whole release acceptance
must pass again on the successor commit. Earlier local broad attempts are
retained: Windows worktree Git pointers, uncommitted source closure, and
offline build dependency availability prevented their release tests.

Rehearsal transport repair: a real HTTP RED showed `write_persistent_plan`
receiving a `bash` action and consuming repair ordinal zero. The synthetic
transport now answers the offered requirement IDs with a fixture-specific
plan/check and keeps both planning and catalog bootstraps outside action
ordinals. Installed Linux wheel 46 passed 30 tests plus four subtests, zero
skips (`rehearsal-lineage-installed-46.xml`); the combined source check passed
41 tests with three explicitly excluded release/Git-environment tests.
This proves transport behavior, not completion of the installed Harbor trial.

Canonical successor `34438826174` PASSED at `440c337b`. Persistent-plan
exposure now has independent exact-byte audit through both monolithic and
message-CAS provider requests, with the immediate response identity checked.
The installed native hook -> saved rendering -> provider request -> audit
test passed alongside the full focused matrix: 153 passed, zero skips,
`plan-exposure-installed-49.xml`. Missing/tampered renderings, wrong sizes,
late delivery and mismatched responses cannot promote exposure. This fixes
the plan portion of the census, not plan-gate attribution or semantic use.

Plan-gate continuation: the native runtime now records the exact queued
directive, and the same independent audit verifies its immediate provider
request/response exposure. A decision alone never becomes a delivery witness.
The real hook suppresses submission, delivers the directive and is audited on
the next native request. Installed wheel 50: 179 passed, zero skips,
`gate-exposure-installed-50.xml`. Positive and absent-byte negative witnesses
are bound into the existing feature matrix; no new steering path or provider
call was introduced.
Wheel 51 feature/attribution/native checks passed 49 tests, zero skips, with
the retained historical `gt_all17` marker warning outside repository config.
Three complementary issuer tests passed from a real Linux Git checkout.
The earlier installed issuer attempt failed two tests because installed
site-packages has no checkout HEAD; that artifact is retained, not counted as
passing evidence (`gate-feature-bindings-51.xml`).

CI `34440017657` failed only the degradation-stage census: the new
`plan_gate_delivery_receipt` stage was missing from the declared registry.
It is now registered, with an installed fault-injection case proving that a
receipt-write failure preserves the directive and records named degradation.
Five complementary source/census checks passed (`baseline-gate-source-53.xml`).

Initial ordering repair: a real runner RED observed index -> baseline, even
when the suite wrote source. Capture/restoration now precedes the single
initial index build; deterministic plan inputs reuse that capture rather than
running the suite again. Restart never recaptures; a failed baseline probe
still permits planning with an explicit gap. Installed wheel 53: 163 passed,
one source-census deselection; wheel 54 final fault/restart subset: 12 passed,
zero skips. This prevents initial anchors lagging baseline writes without an
extra graph rebuild; full baseline identity/conservation remains open.

Installed repair rehearsal 01 completed installation of all staged capabilities
but failed before model calls: the outer Docker runner lacked host PID/cgroup
visibility required by the resource evidence collector (`/proc/<pid>/cgroup`).
No verification was bypassed. Rehearsal retry must use the appropriate Docker
namespaces. The complete 10,762-file language-server tree was independently
verified in a Linux volume against manifest SHA256
`1245a4b6d36130483260e8cc3ceb7201af9034c047ab5ec87bee58f4bf4e08cf`;
the redundant slow Windows-bind verification was stopped after this passed.

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
- [x] Finish evidence metadata propagation through controller receipts, persistence,
  summaries, and consumers; historical receipts must not gain stronger authority.
- [x] Enforce captured test-source/configuration digest matching for bound checks.
  Missing identifiable source remains unverified; implementation edits do not
  silently change the bound test definition.
- [x] Complete supported-positive and negative protocol coverage, including skipped
  tests, no tests, contradictory output, timeouts, and late-output failures.
  DONE. Timeouts, nonzero exits, environment, command and protocol mismatches
  already had independent witnesses. The added negatives close the rest:
    no tests ran, a different test passed, a similarly named test passed --
      all UNVERIFIED. The observed ids are the PASSING names, so a SKIPPED test
      never appears among them and the subset guard rejects it. That was true by
      construction and unasserted; it now fails loudly if a future parser reports
      collected-or-skipped ids and turns a skip into proof.
    a partially satisfied selection is UNVERIFIED; every selected test must pass.
    a fail outcome at returncode 0 stays CHECK_FAILED, so output that ends in
      failure is never rescued by a green exit code.
    an unsupported or absent protocol abstains rather than inferring a pass.
  Mutation-checked rather than merely green: deleting the subset guard from
  classify_bound_check fails all four new cases plus one existing installed case,
  so these tests constrain the behaviour instead of describing it.
  Verified installed on wheel 66 against an LF checkout: 42 passed
  (D:/gt-context-proof/protocol-69.xml).
- [x] Prove no unbound or unsupported observation can advance the cursor or gate.
  DONE, through the REAL adapter rather than a classifier helper. A genuinely
  passing test that no requirement selected is executed in the real workspace and
  drained by the real queue; row ids, per-row states and the outstanding set are
  asserted before and after, and nothing moves. That is the failure mode that
  matters because it is invisible: the command really passes, so any channel that
  accepted it would report progress the agent never made and let the gate through.
  The same test then binds ONE row and drains again, proving the machinery does
  advance a bound row while the unbound sibling stays outstanding -- so the
  negative assertions are about binding rather than about a dead code path.
  Mutation-checked: deleting the 'not predicates' clause from unmet_plan_rows,
  which is what keeps an unmapped row outstanding, fails this test and four
  existing installed cases.
  Verified installed on wheel 66 against an LF checkout: 43 passed, no skips
  (D:/gt-context-proof/unbound-70.xml).

## 2. Verification execution and queue — PARTIAL

- [x] Remove replay of original shell receipts after edits and in numeric reverify.
- [x] Add direct argv execution through the existing isolated environment/worker.
- [x] Add pending-check deduplication and submission-boundary draining.
- [x] Add exact equivalent agent-check observation to discharge queued work.
- [x] Record automatic-check before/after source transactions and invalidate on edits.
- [x] Catch automatic-check exceptions without letting a check submit the agent task.
- [x] Prove the complete queue lifecycle with real installed executions: repeated
  edits, superseding revisions, multiple bindings, equivalent agent execution,
  mutation during checks, timeouts, and the bounded total verification allowance.
  The allowance is shared across checks in a pass; the task reserve bounds later
  passes. Controlled-clock tests prove deadline decisions, not measured speed.
- [x] Drain coalesced checks before the next model decision in VERIFY, in addition
  to submission. Preserve the existing per-pass cap and submission reserve;
  do not run checks on every implementation turn or rerun discharged work.
- [x] Verify replay/restoration behavior across persisted state and interrupted runs.
  Check definitions now recover once from the chain-validated startup journal,
  preserve revised shared bindings, and revalidate current test source. Historical
  passing observations are not restored. Hash-linked design revisions and deferrals
  now recover without replaying admitted requests or restoring proof. Original
  plan/context/baseline checkpoints now recover through native bootstrap without
  another planning call or post-edit baseline capture. Terminal interruption and
  externally anchored journal-tail conservation remain open.
  The forced-interruption rehearsal is now GREEN and no longer blocks this item:
  rehearsal 09 on committed source eb9965ed with wheel 63 and static producer 06
  reports VERIFIED_SYNTHETIC_INTERRUPTION, interruption_issues [], ten transport
  requests, a gradable collected patch and paid_smoke_eligible false. Rehearsal
  06's sole failure, unexpected_runtime_receipt_errors, was a stale expectation
  rather than a fault: nine token/cost conservation errors had disappeared
  because 7e9911ba repaired the counters, and the two that appeared are truthful
  absence-of-claims findings on a receipt frozen at interruption -- it declares
  provider_calls 6 while the journal holds 8 responses against 10 admissions, and
  its effective_model was never written. Traced against the preserved journal
  before the list was changed; committed at eb9965ed. The plan checkpoint layout
  moved to v2 in the same increment because BaselineResult gained two fields and
  the decoder requires exact field-set equality; older checkpoints reject by
  version rather than being migrated, since the only value available to backfill
  a missing source revision is the current workspace. Valid, truncated, tampered
  and externally anchored journal tails are still open.
  Both named gaps are now closed. Externally anchored journal-tail conservation
  landed in 9affba20: events.anchor.json records event_count and event_head
  beside the journal, written atomically AFTER each row, and startup requires
  the journal to still contain what the anchor witnessed. A hash chain proves
  self-consistency, never completeness -- cut the tail and sequence numbers
  still run 1..N with correct parent hashes, so an unanchored verify returns
  valid and recovery rebuilds from a journal that lost its most recent events.
  Containment rather than equality, because the anchor legitimately trails by
  one after an unclean stop; a shorter journal, a different head at equal
  length, or a wrong hash at the anchored depth all fail. Three tests carry
  truncated, crash-behind and rewritten-under-a-stale-anchor, and the first
  asserts the unanchored chain still looks perfect so the test states WHY the
  anchor is needed. Terminal interruption is covered by rehearsal 09 above.
  Verified installed on wheel 64: 81 passed and 4 subtests across the journal,
  all recovery, receipt, baseline and interruption suites
  (D:/gt-context-proof/anchor-surface-64.xml).
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
- [x] Finish full source-span/example delivery and long/unfenced code-block cases.
  The planner already receives the original task; retained line-numbered source
  is now explicitly retrievable with `gt-plan show --source`, including indentation,
  blank lines and examples. Existing workflow/test-identity filters remain active.
  Retrieval availability is not counted as actual model exposure.
- [x] Make omitted designs and interaction cells explicit pending work under the
  existing planner limits; preserve stable requirement identities.
- [x] Finish anchor/source-revision validation and distinguish existing versus
  proposed symbols/tests in plan data and rendering.
  `gt_engine/persistent_plan/provenance.py` classifies each row once, against the
  repository as it stood before the first edit: `symbol_basis` is `existing` only
  for an exact-name graph anchor, `name_guess` for a lexical one and `unmapped`
  for none; `check_basis` is `existing` only when every path the acceptance
  command names is present, `proposed` when one is absent, `unnamed` when the
  command names no file. A `verification_kind` of `existing_test` over an absent
  file is corrected to `new_test` (one-directional: a new case in an existing file
  stays a new test). Rendering names the missing file instead of printing an
  acceptance check that cannot collect. A revision that rewrites the command
  clears the classification rather than re-deriving it against the edited
  workspace, so a recovered revision replays to the same digest.
  `PlanInputs.observed_source_revision` records the revision this run actually
  indexed; when a restart moves the workspace off the captured revision the block
  leads with `STALE ANCHORS` naming both, and `anchors_are_current` is False.
  Witnesses: `tests/test_persistent_plan_provenance.py` (14 tests). Four mutations
  each kill at least one: dropping the one-directional correction, never seeing a
  missing path, treating an unanchored row as existing, and keeping the capture-
  time classification across a rewritten command. `tests/test_original_plan_recovery.py`
  now derives the checkpoint shape fingerprint from the dataclass graph the decoder
  walks, so any future field change fails until `recovery.LAYOUT` moves with it
  (now `gt.plan_checkpoint.v3`).
- [x] Complete counts and exposure evidence: existing/proposed/executed/passed/proven/
  unmapped/model-exposed, plus exact row IDs and rendered digests.
  `gt_engine/persistent_plan/accounting.py` places every row in exactly one bucket
  of four partitions and reports each bucket as a list of row IDs first, with the
  scalar derived from that list rather than counted separately: symbols
  (existing/name_guess/unmapped), checks (existing/proposed/unnamed/none/
  unclassified), evidence (proven/passed/failed/deferred/unverified) and exposure.
  `executed` is reported alongside evidence because a check that ran and failed is
  a different fact from one that never ran. Precedence is stated and tested: a
  current failure outranks a pass, a proof outranks a pass. Exposure counts a row
  as model-exposed ONLY when its whole block survived the rendering cap --
  `retrievable_row_ids` (the index, always complete) is reported separately and is
  strictly larger, so retrieval availability can never be read as delivery -- and
  it carries `rendered_sha256`, which pins the accounting to the exact text stored
  at `plan_renderings/<sha>.json`. `MiniSweAdapter.plan_accounting()` assembles it
  from the four sources only that object holds, `publish_plan_state` writes the
  full lists to `plan/current.json`, and a `plan_accounting` journal row carries
  the scalars plus the plan and rendering digests that tie back to them.
  Witnesses: `tests/test_persistent_plan_accounting.py` (8 tests), including the
  end-to-end file-and-journal publication with a valid hash chain. Four mutations
  each kill at least one: counting retrievable IDs as exposed, dropping the
  plan-membership filter on proven/executed, inverting failure precedence, and
  letting a count drift from its own list.
- [x] Test CLI stale requests, multiple requests, restart handling, invalid check
  specs, deferral, and complete installed automatic cursor delivery.
  `tests/test_plan_cli_requests.py` covers the queue between `gt-plan` and the
  engine: a request carrying an earlier turn's digest is rejected as stale and the
  queue keeps draining; an unusable check spec (a shell string the engine cannot
  bind to a test identity) is refused without stopping the requests behind it; a
  deferral is refused twice, by the CLI and again by the engine, when it carries no
  reason, and when accepted it is a state and not a proof (the row stays in
  `unmet_plan_rows`); an unsupported operation and an unknown row change nothing;
  each request file is read at most once.
  Multiple requests in one turn revealed a real defect and it is fixed here.
  `plan/current.json` is rewritten once, after the whole queue drains, so two
  `gt-plan revise` calls in the same turn necessarily read the SAME digest.
  `apply_plan_requests` compared each request against a digest that moved as the
  batch applied, so everything after the first was rejected -- silently from the
  agent's side, because the CLI had already answered "requested". Requests are now
  admitted against the base the CLI could actually have read, while the journal
  still chains on the true pre-application digest, so a batch replays through its
  own intermediate states.
  Restart handling remains covered by `tests/test_plan_check_recovery.py`:
  design and deferral replay without reapplying requests, a corrupted base,
  result, proof grant or request identity is rejected without partial
  application, and only current check definitions are restored.
  Installed automatic cursor delivery is proven end to end on the real
  `GTSession` in `tests/test_persistent_plan_integration.py`: the cursor arrives
  as a context addition from a real `before_model`, naming a row and its command;
  an unchanged world does not repeat it; a moved row state re-emits it under the
  same `plan_cursor:task` supersession key as a distinct unit, so the tail carries
  one cursor rather than a growing pile; an abstained plan delivers none.
  Two mutations kill these: dropping the candidate from `before_model`, and
  firing on a clock rather than on change.

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
  unchanged parser nodes and consuming cached complete parser inputs. Successor
  producer `efa70e52` also retains exact parser properties, freshly target-bound
  assertions, containment and taxonomy edges. Derived resolution still runs once.
- [x] Copy the certified parent, retain unchanged parser structural rows, replace only
  changed/deleted structural rows, and remap cached parser-local references.
  Nodes, parser properties, target-bound assertions, containment and taxonomy edges
  are retained. Derived analysis and FTS publication remain with existing owners.
- [x] Run the existing complete resolver/analysis exactly once per batch; republish
  derived layers without stale facts. Do not guess a narrowly complete resolver.
- [ ] Reuse eligible history/cochange work; preserve embedding caches and LSP bindings.
- [x] Wire a persistent external cache root and batch API through the harness's
  existing one-active/one-pending coordinator.
- [ ] Prove add/delete/rename/import/inheritance/ambiguity/new-resolution-target cases,
  immutable parent behavior, failure fallback, and every graph consumer's semantics.
  REOPENED. This was ticked on fixture-scale evidence and the tick was wrong at
  real-repository scale. The fixture evidence below stands and is worth keeping;
  the CLAIM it was used to close does not.
  Measured on `click` (105 parsed files, 43,679 nodes, 50,954 edges) with the
  certified producer: THE PRODUCER IS NOT DETERMINISTIC. Three full rebuilds of a
  byte-identical tree produced two different edge sets -- 50,954 edges and 50,957
  edges, with different content -- and the amend wobbles between exactly the same
  two. `-workers 1` is equally nondeterministic and lands on the identical pair of
  digests, so this is NOT resolver concurrency; it is a nondeterministic tie-break
  inside resolution, with Go's randomized map iteration the obvious candidate.
  Evidence: `determinism-click.txt`, `study-click.json`.
  The finding is now a test rather than a note. `tests/test_producer_determinism.py`
  carries two: a fixture of eight files with the right SHAPE (several classes each
  defining the same method name, reached through receivers whose type is not
  locally obvious) which is deterministic today and is kept as a regression guard
  for that scale -- measured, not assumed, and explicitly NOT evidence that the
  producer is deterministic; and a real-repository test that reproduces the defect,
  skipped unless `GT_DETERMINISM_REPO` names a checkout and marked xfail because
  the producer is a certified binary this project pins. Against `click` the first
  PASSES and the second XFAILS, so fixing the producer turns it into an XPASS
  rather than into silence.
  A static scan of the vendored producer source for first-match-over-a-map
  selections found 38 range-then-break sites, of which nearly all range over slices
  that were extracted from a map and then sorted -- the authors clearly made this
  pass already. One is not: `internal/resolver/promote.go:567` ranges `idx.fnl`
  (`map[fnlKey]int64`) and takes the first entry matching a (name, line) pair with
  `break`, which its own comment describes as accepting "any file with that
  (name,line) pair". That is a genuinely order-dependent target selection. It is
  NOT attributed to the observed symptom: it emits CO_SERIALIZES and the divergence
  measured is in CALLS targets and VTA flow facts. It is recorded as a confirmed
  site of the same class, found while looking for the cause.
  What diverges is receiver selection for same-named methods. Between two arms:
  `AliasedGroup.get_command` calls `Context.fail` in one graph and `ParamType.fail`
  in the other; `_AtomicFile.close` calls `Context.close` or `LazyFile.close`;
  `runner.invoke` binds to `Context.invoke` or `CliRunner.invoke` across five
  assertions; and 359 of 43,680 `vta_flow_edge_fact` nodes carry different content
  hashes at an identical row count (`arm-diff-click.txt`). A consumer asking who
  calls `fail` gets a different answer depending on which build it reads.
  CONSEQUENCE: amend-versus-rebuild digest parity cannot be established at real
  scale while the producer's own repeatability is weaker than the comparison. No
  route comparison can be tighter than the producer's run-to-run agreement. Making
  the tie-break deterministic is producer work and needs re-certification.
  The fixture-scale evidence is unaffected and remains recorded:
  Cases and immutable parent: `tests/test_batch_amend_parity.py` builds a parent,
  applies one mutation, then builds the graph BOTH ways and requires the amended
  graph to say exactly what a from-scratch rebuild says. Compared as content, not
  as row ids -- the amend retains parent ids on purpose and a rebuild has no reason
  to pick the same numbers -- across nodes, edges, properties, assertions,
  resolution symbols/callsites/candidates, closure, cochanges and file hashes.
  Equality rather than containment: a fact the amend keeps that the rebuild would
  not produce is a stale fact outliving its code, and one the rebuild produces that
  the amend drops is a fact no consumer will find.
  Eight cases pass installed on Linux with the certified producer, zero skips
  (`amend-parity-72.xml`): add a definition, delete one, rename one across two
  files, change an import, move inheritance up a new class, introduce an ambiguous
  same-named method, add a new resolution target, and delete a whole file. The
  parent's bytes are unchanged after every one, and each amended graph reports
  `analysis_state=complete` with a clean `PRAGMA foreign_key_check`.
  Two guards keep the parity honest. A matrix test rebuilds each mutation on its
  own and fails if any case left the graph unchanged -- it caught one inert case
  the first time it ran, which is the failure mode a parity suite dies of. And
  comparing the stale PARENT against the rebuild instead of the amend fails all
  eight cases, so the comparison demonstrably discriminates.
  Failure fallback was already covered: `tests/test_index_incremental.py` proves a
  failed amend falls back to a full rebuild and names why, that an undeclared
  capability and an uncertifiable parent both refuse, and that a delete and a
  rename are amendable rather than refused.
  Consumer semantics: `tests/test_batch_amend_consumer_parity.py` asks the
  question each consumer actually asks, of the amended graph and of the rebuild,
  and requires the same answer. Table equality is necessary and not sufficient --
  two databases can agree row for row while a consumer answers differently
  through an FTS index that was not republished, a closure whose depths were
  retained, or a symbol id form that changed under a caller, which is the class
  of failure that made the amend worth distrusting when caller coverage was
  answered on only 18 of 33 post-edit rebuilds.
  Eight consumers, eight mutations, all passing installed on Linux with zero
  skips: the task-start graph projection, the surface receipt census, the plan's
  anchors (with modes and abstentions), the plan's caller closure, the producer's
  ego graph and change impact, targeted covering-test selection, symbol contracts
  for every function/method/class, and the cochange row count.
  Answers are compared by NAME and PATH, and row-id-shaped keys are dropped at
  every depth. That is not a loosening, it is the point: the amend retains parent
  ids deliberately and a rebuild renumbers, so comparing addresses would fail on
  the amend's whole reason for existing. Two consumers appeared to disagree on
  every case until this was fixed -- `ego_and_impact` was comparing raw node ids
  and `symbol_contracts` was comparing `property_id` storage addresses inside
  `provenance` and `returns.shapes`, while every value was identical. The graphs
  agreed; the harness was reading addresses.
  A panel guard keeps that honest: the same consumers are asked of the
  pre-mutation parent and of the rebuild, at least one must disagree for every
  case, and at least four distinct consumers must move across the matrix.
  Otherwise the equality above would be satisfied by questions none of these
  consumers can answer.
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
- [x] Finish baseline source/config/test/environment identities at initial capture
  and final comparison, including skipped/missing test conservation.
  Filtered task-environment hashes are propagated from initial capture to
  comparison/reporting; changed or missing bindings remain unknown. The digest
  binds task variables before the private temporary capture-root override.
  The TEST identity half is now done. A name is not an identity: a test that
  passed before and passes now is conservation only if it is the same test, and
  rewriting an assertion into `assert True` conserves the name perfectly -- the
  exact substitution the bound-check path already refuses through
  `test_source_digest`. `BaselineResult.test_file_digests` records, per file,
  the digest of every file the observed test names live in, taken from the
  snapshot after the command so a suite that rewrites its own fixtures is
  recorded as it ended. `compare_results` refuses to carry a pass whose file
  moved or vanished, naming the affected tests, and returns `unknown` when no
  per-test identity was recorded at all rather than reading silence as
  conservation. Only the baseline's own files are compared, so adding test
  files -- the work itself on most tasks -- is never a conservation failure.
  Test CONFIGURATION and DECLARED dependency identities are recorded as grouped
  digests over the tree (`config_sha256`, `dependency_sha256`) and reported in
  `RegressionReport.changed_identities` with the intact verdict, not as
  blockers: adding a fixture to conftest.py or a package to requirements is
  ordinary work, and a report that quietly did not check is worse than one that
  says what it saw. Skipped/missing conservation was already covered -- a
  previously passing test observed as neither passing nor failing is
  `incomplete` -- and remains so.
  The comparison is split into a pure `compare_results(baseline, after)` so
  every rule is a statement about two recorded observations rather than about a
  subprocess. Witnesses: `tests/test_baseline_identities.py` (10 tests); four
  mutations each kill at least one -- never noticing a changed test source,
  treating an absent identity record as conserved, counting a newly added test
  file as a conservation failure, and dropping the config/dependency report.
  Checkpoint layout moved to `gt.plan_checkpoint.v4`; the shape fingerprint in
  `tests/test_original_plan_recovery.py` caught the field addition unprompted.
  INSTALLED PACKAGE versions and filesystem dependencies outside the repository
  remain out of scope: they are a fact about the container, not the repository,
  and probing them costs a subprocess per capture and per comparison.
  The source-revision half is now DONE and committed at c0455f36: BaselineResult
  carries source_revision and after_source_revision, captured with the existing
  workspace snapshotter before and after the command, incomplete snapshots are
  rejected, and differing revisions are labelled source_changed_during_baseline
  rather than captured. The candidate previously failed 14 of 36 installed tests
  with spawn_failed/ValidationError: minisweagent's environment declares
  timeout: int, so subtracting snapshot time made the allowance fractional and
  Pydantic refused to construct the environment. Reproduced directly (120.0
  accepted, 119.87 rejected, 1 accepted) and floored to whole seconds at that
  boundary. Verified installed on wheel 62: 55 passed across the three baseline
  and three recovery suites (baseline-source-62.xml). Config, test and dependency
  identities remain open.
- [x] Route baseline execution through the existing isolated process-tree boundary;
  real installed Linux timeout test confirms the sleeping grandchild is reaped.
- [x] Exercise background writers, typed mutations, automatic checks, incomplete
  captures, and carried snapshots together in installed Linux tests.
  `tests/test_snapshot_carry_interactions.py` drives the real `execute_actions`
  with a shell that actually writes and counts real `capture_workspace` calls,
  replacing a source-text assertion that only proved the code READ a certain
  way. Eight cases, zero skips, installed Linux (`carry-72.xml`): a carried
  post-image still charges the first action's write to the first action and the
  second action captures once instead of twice; a surviving descendant, a
  descendant scope the reaper does not own, and an incomplete capture each force
  a real recapture; an automatic check generation bump and an observed background
  writer each invalidate the carry; and a read-only typed query does NOT, because
  dropping it there would charge every query the 1.08s this carry exists to avoid.
  One real defect found and fixed. GT's own post-edit probes run AFTER the
  post-image is taken and write into the worktree -- `py_compile` drops bytecode
  beside every source it checks, and the covering lane runs the repository's own
  tests. The carry survived them, so the next action's diff charged GT's own
  bytecode to the agent: a phantom edit, a spurious epoch bump, and evidence
  invalidated for nothing. The module comment already claimed the carry was
  dropped wherever GT may run a subprocess against the worktree; this was the one
  place that did not honour it. Reachable in ASSISTIVE with both live-probe
  opt-ins, not on the advisory benchmark path. RED reproduced installed on Linux
  before the fix. Three mutations each kill at least one test: removing the
  live-probe drop, ignoring the check generation and background-writer flags, and
  carrying an untrustworthy post-image.

## 6. Submission and official patch conservation — PARTIAL

- [x] Check reserve before expensive submission verification; reread budget afterward.
- [x] Reset progress-related stall state before deciding whether to escape.
- [x] Remove the false promise that the next submission is accepted unconditionally.
- [x] Close the workflow's 300-step cutoff end to end. Pushed repair `42300c15`
  sets max_iterations=0 and represents unlimited remaining steps explicitly in
  the gate, preserving wall-time reserve. Two RED witnesses now pass; the related
  integration/gate/adapter run passed 49 tests. Installed 301-query/deadline-stop
  proof passed in candidate 07. Successor canonical acceptance `34426564301`
  passed on `2ef314ad`; wall-time and submission reserve remain enforced.
- [x] Finish gate reasons separating verified completion, mapping gaps, check evidence,
  baseline uncertainty, and budget/stall acceptance. Acceptance is not correctness.
- [x] Prove exact 600-second/20-step/three-stall boundaries, budget consumption during
  checks, and progress recovery before a previously reached stall limit.
- [x] Add current committed-diff/uncommitted-state reporting and bounded finalization
  reminders. Do not automatically commit model changes.
- [x] Verify official submitted patch versus supervisor recovery artifact separately.
  DONE. The four-state separation matrix is covered: committed-only, uncommitted-
  only, mixed and interrupted. The graded artifact is the task-collected
  `git diff --binary <baseline> HEAD`; the supervisor's export_patch conserves the
  whole workspace so an interrupted run stays diagnosable. Uncommitted-only is the
  case that proves they must stay separate: the graded patch is EMPTY while the
  recovery patch carries the change, so substituting one for the other would grade
  work the agent never committed. Mixed asserts the graded patch carries the
  committed half and not the uncommitted one. A second test asserts the split at
  the source: eval/miniswe_agent.py writes the supervisor artifact to
  agent/gt-worktree.patch and never to artifacts/model.patch.
  Verified installed on wheel 65 against a full checkout root: 77 passed, zero
  failures and zero skips across the repro, supervisor and interruption suites
  (D:/gt-context-proof/patch-surface-65b.xml). Scattered mounts are insufficient
  for these suites -- they read pyproject.toml and scripts from the repository
  root, so they need a real checkout rather than site-packages.

## 7. Installed release and performance — OPEN

- [x] Complete and audit the 21-capability inventory; retain historical 19-capability
  records without rewriting their evidence.
  DONE. Issued and independently verified at source revision 9affba20 in the
  installed Linux environment with the pinned producer wheel 06 and its static
  binary: all 21 identities WITNESSED, verify_feature_matrix reports 21/21 with
  21 witnessed cells, matrix digest
  112a46cbd219d74e9e5a159ffe0010dbd9b8178239dbfe08ba6f2b3c8b374b16.
  Result: D:/gt-context-proof/feature-matrix-linux-67.json.
  Structural audit first: all 21 features carry BOTH a positive and a negative
  binding, no orphans in either direction, and all 51 bound witnesses resolve to
  real tests. Three environment traps were the whole difficulty and none was a
  code fault: the shared Windows venv is refused by the producer identity gate,
  which is the gate working; an archived tree has no .git so the issuer's
  git rev-parse HEAD fails and every identity reports missing; and a bind-mounted
  producer binary arrives without its execute bit, so 13 graph-dependent
  witnesses SKIP and the issuer correctly refuses to count a skip as proof.
  Copy the binary and chmod 755 it, exactly as the rehearsal runner does.
  A witnessed capability is exposure and binding evidence, NOT proof of causal
  task benefit; that remains a separate question.
- [ ] Rebuild current harness and producer artifacts; bind actual source/wheel/binary
  hashes and update the candidate manifest only from real build evidence.
- [x] Run required installed tests with zero unexplained skips and real journal audit.
  DONE at 18a794b5 on wheel 66 against a full checkout with the static producer
  made executable: 2,119 tests, 0 errors, 3 failures and 11 skips in 504s
  (D:/gt-context-proof/full-suite-66.xml). Every skip carries a stated reason and
  every failure is environmental, each traced rather than assumed:
    sqlite_vec absent (1), real arktype graph absent (4), Go toolchain absent (1),
    GT_RETRIEVAL_TEST_GRAPH absent (1), GT_GITNEXUS_ROOT unset (3), and one
    deliberate complement -- 'graph available; covered by the full-smoke test'.
  The two test_product_acceptance failures were source_closure_differs_from_head,
  which is a WINDOWS CLONE artifact, not a defect: git clone applies autocrlf so
  the working tree is byte-different from HEAD's LF blobs while git status still
  reads clean, and the closure digest is computed over working bytes. Proven by
  re-cloning with core.autocrlf=false core.eol=lf, which moves the error on to
  harness_wheel_build_failed: those two build a harness wheel in-test and need
  hatchling, unavailable under --network none. Clone with LF for any closure or
  release test. The third failure needs the Go toolchain, the same dependency a
  sibling test SKIPS for; that inconsistency is noted below.
  Real journal audit run both ways to show the auditor discriminates:
  completed repair rehearsal 04 audits GREEN-delivered
  (D:/gt-context-proof/rehearsal04-audit66.json); interrupted rehearsal 09 audits
  RED (rehearsal09-audit66.json) for two unanswered provider requests and
  'api_calls 6 + bootstrap 0 != 10 requests'. Both are the truthful signature of a
  frozen receipt: the parked retries are real and the bootstraps were never
  reported. An interruption rehearsal is expected to audit RED; a completed run is
  the one that must be GREEN.
  That follow-up is now CLOSED and the suite is clean. The failure was gcc, not
  Go: the test passes cgo_enabled="1", which makes capture() resolve a C compiler
  even though its own command is sys.executable and its subject is prepared-replay
  provenance. It now guards on gcc exactly as the sibling guards on go, so one
  absent toolchain gives one verdict. The two product-acceptance failures needed
  the hatchling build backend, which they fetch to build a wheel in-test and which
  --network none forbids; a wheelhouse is staged once at
  D:/gt-context-proof/wheelhouse and supplied offline via PIP_NO_INDEX and
  PIP_FIND_LINKS.
  FINAL: 2,119 tests, 0 failures, 0 errors, 12 skips in 512s
  (D:/gt-context-proof/full-suite-68.xml). Every skip states its reason: absent
  arktype graph (3), GT_GITNEXUS_ROOT unset (3), sqlite_vec, Go, gcc,
  GT_RETRIEVAL_TEST_GRAPH, and one deliberate complement covered by full-smoke.
  The full recipe is: LF clone, wheelhouse for build backends, producer binary
  COPIED and chmod 755, GT_INDEX_BINARY set, run from the checkout root.
- [ ] Run canonical provider-free product acceptance and installed full-flow rehearsal.
- [ ] Run five alternating offline baseline/candidate repetitions over the fixed six
  repository transitions with equal budgets. Report graph blocked versus background
  time, parsed files, resolver passes, checks, snapshots, memory, and semantic parity.
  HARNESS BUILT, ONE OF SIX REPOSITORIES MEASURED. `scripts/graph_transition_study.py`
  stages a real transition per repository (one new top-level definition appended to
  the largest parseable file, so line numbers stay stable and the edit is a genuine
  new resolution target), builds the parent once and reuses it in both arms, then
  alternates baseline (full rebuild) and candidate (batch amend) so machine drift is
  not charged to whichever arm ran second. Per run it records wall seconds, return
  code, parsed files/nodes/edges, the amend result line, and peak memory as that
  run's own VmHWM polled every 50ms -- getrusage(RUSAGE_CHILDREN) would have been
  easier and wrong, since it is a high-water mark across every child ever reaped.
  FIRST RESULT, `click`, 5 alternating repetitions (`study-click.json`): the amend is
  SLOWER than the full rebuild. Baseline median 6.94s against candidate median 8.10s,
  a 0.86x speedup -- the amend costs about 17% more on a 105-file repository. That is
  one repository and the smallest of the six; the amend's advantage should grow with
  repository size, and that is exactly what the remaining five must establish rather
  than be assumed.
  Semantic parity across arms could not be established, and the reason is the
  producer nondeterminism recorded against the all-consumer parity item above, not
  the amend.
  NOT MEASURED by this harness, stated so its numbers are not read as more than they
  are: the blocked-versus-background split, checks and snapshots are properties of
  the coordinator and the run loop rather than of the producer, and need their own
  measurement against the live path.
- [ ] Review the final diff, rerun invalidated checks, then commit verified coherent
  changes on the exact branches/bases above. Scoped pushes and provider-free CI
  are authorized; paid dispatch is not.

## Evidence retained so far

- Candidate 44 repins the actual producer `efa70e52` wheel/static binary/build-info
  and exact source tree after its successful CI and static build. The real lineage
  verifier passed with 317 matching package files, 11 review packets, clean producer
  and review checkouts, and matching ancestry/diff. New scoped review packet lives
  at review branch `86293f71`; historical packets remain intact. Installed combined
  graph/recovery/baseline/check suites: 200 passed, one explicit source-registry
  deselection, zero skips, 107.69s (`producer-repin-44.xml`). Extended structural
  retention proof through the actual harness resource guard passed both installed
  cases, zero skips, 13.86s (`structural-resource-owner-44.xml`). Source registry and
  binding/provenance regressions passed 14 tests with zero skips
  (`producer-repin-source-44.xml`). Current wheel SHA256:
  `88a763e5202f0f3dcf22d221300d91ff163f621e41c6880f8c30331bc3804e7a`.
  Full successor acceptance and remaining release/implementation work remain open.

- Candidate 43 adds typed content-addressed checkpoints of the original plan,
  complete inputs and initial baseline. Native restart restores later design
  revisions into actual request bytes without another planning call, and never
  restores passing authority. The real build_agent path no longer recaptures a
  post-edit baseline; corrupt checkpoints leave baseline unknown. Original source/
  graph revision IDs now flow from the index receipt into fresh plan inputs, and
  rendering labels anchors as capture-time context, not current-workspace proof.
  REDs: `original-plan-restart-red.xml`, `restart-baseline-red.xml`, and the corrupt
  checkpoint case in `restart-baseline-42.xml`. Final installed run: 214 passed,
  four explicit source-file checks deselected, zero skips, 102.88s
  (`original-plan-restart-43-final.xml`). Those four plus flag-off source checks
  passed separately: 16 passed, zero skips, 3.821s (`original-plan-source-43.xml`).
  The initial broad run's four missing-source errors remain recorded in
  `original-plan-restart-43.xml`. Wheel SHA256:
  `28951b70ab92f5f63b7ea96f247fa04102b5bfdc465f7da8ff82ba20a8117c22`.

- Exact producer `efa70e52` Linux build `34436550584` and full CI `34436549266`
  passed. Actual downloaded static binary passed installed fixture proof
  (`structural-binary-06.xml`, one test, zero skips, 10.66s): retained property,
  assertion and structural-edge IDs; amended/fresh payload parity; immutable
  parent; no foreign-key violations; 2 cache hits/1 miss/1 resolver pass.
  All 317 Python wheel files match the exact archived commit, wheel SHA256
  `cb73ff2ed55c11d7babbdc32ef55c72268e8617913cadfa94b323ea852ca0ce9`.
  Candidate manifest repinning remains outstanding. Canonical harness acceptance
  `34436465709` passed at `81405e2c`, before these restart changes.

- Producer `efa70e52` retains exact parser-owned properties/assertions and
  containment/taxonomy edges using transactional inventories. Fresh assertion
  target/score changes invalidate reuse; other edge owners are conserved.
  Real CLI REDs showed unchanged property IDs 3 -> 25 and containment IDs
  37 -> 76 before their respective fixes. The final command suite passed in
  141.677s, including nine batch/fresh transition cases; store and parser suites
  also passed. Additional insertion-failure rollback test passed with all focused
  store cases (0.902s). Parent bytes remain immutable. Missing taxonomy kinds
  caused one intermediate regression, fixed by using the canonical registry.
  Exact installed Linux producer proof and build pins remain pending; no measured
  end-to-end speedup or full-consumer equivalence is claimed.

- Candidate 39 refuses contradictory exact semantic assertions for the same
  relation/literal identity, regardless of pass/fail order; unknown results also
  prevent promotion. Three installed RED witnesses advanced a predicate before
  repair (`assertion-contradiction-red.xml`). Final installed evidence/check/gate
  regressions passed 86 tests, zero skips, 16.47s
  (`assertion-contradiction-39-final.xml`); the preceding green attempt exposed a
  test reading a submission-only field before submission, corrected to inspect
  actual predicate receipts without changing the primary rejection assertion.
  Wheel SHA256: `cb2af2f0eb940788b29f53c7b6ca56c6185a14f6341dcbb593d5fbdaded72ff4`.
  Canonical acceptance `34434458732` passed at harness `1f4fba35`; producer CI
  `34434408677` passed at `ccf489ba`. These precede current structural-row work.

- Candidate 38 now rejects a missing parent graph/manifest before producer
  capability discovery. Two RED witnesses prove the unnecessary probe occurred.
  Capability checks and parent certification are unchanged for eligible parents.
  A capability-refusal fixture now supplies a manifest so it still exercises its
  intended branch. Final expanded installed run: 206 passed, one explicitly
  deselected source-registry check, 171.96s (`evidence-graph-38-final.xml`). The
  real registry mirror passed separately against source (one test, zero skips,
  3.699s, `evidence-graph-registry-38.xml`). The preceding run's changed-refusal
  fixture failure remains recorded in `evidence-graph-38.xml`. Canonical acceptance
  `34433223742` passed at `6d05713a`; later changes still need exact-commit CI.

- Candidate 37 carries predicate evidence kind, coverage basis, originating
  source revision, action and protocol through controller receipts, journal and
  final summaries; raw commands/output are not copied into summaries. Legacy
  unspecified metadata stays unspecified and startup does not restore historical
  passing authority. Empty predicate sets and unmapped plan rows cannot claim
  verified completion. Three corrected REDs retained in
  `predicate-metadata-red-corrected.xml` (the first fixture originally omitted
  predicate registration). Broad installed suites: 178 passed and one graph
  coordinator 3-second timeout (`predicate-metadata-37.xml`). That graph check
  failed once independently, then passed unchanged on old and current wheels
  (`graph-timeout-old-36.xml`, `graph-timeout-current-37.xml`). Investigation found
  capability probing occurs before checking a missing parent manifest; the
  bounded fast-refusal repair and full successor run remain next work, not waived.

- Candidate 36 fixes nested/tilde fence parsing and adds full source-span
  retrieval to the existing CLI/state owner. Long unfenced examples, structural
  lines and blank-line positions survive; unterminated leading examples keep
  their true starting line. No added provider call or repeated prompt payload.
  Six installed source/ledger/prefix/check/recovery/integration suites: 106 passed,
  zero skips, 28.09s (`plan-source-36.xml`); expanded source edge cases: five
  passed, zero skips, 7.60s (`plan-source-edge-36.xml`). Three pre-fix REDs:
  `plan-source-red.xml`. Canonical acceptance `34432337182` passed at `9b23e66a`,
  before finalization/source changes; successor acceptance remains required.

- Candidate 35 observes actual BASE-to-HEAD binary diff bytes, separately from
  tracked/untracked working changes and the supervisor's recovery export. An
  empty commit no longer implies a nonempty collectible patch. The bounded
  two-second read-only observer reports unavailable state as unknown. At most
  two finalization reminders use the existing context admission path (VERIFY
  entry and reserve), never committing or rebuilding a graph. Installed suites:
  86 passed, one source-only entrypoint check explicitly deselected, 54.52s
  (`submission-state-35.xml`). That entrypoint and all session source tests:
  65 passed, zero skips, 9.361s (`submission-source-35.xml`). Two REDs retained
  in `submission-state-red.xml`. Actual external collector/rehearsal remains open;
  the new observation explicitly sets `official_collection_observed=false`.

- Candidate 33 restored validated design revisions and deferrals from the existing
  chain-validated startup journal. Base/result digest mismatch, malformed request
  identity and attempted proof grants are rejected before mutation. Installed
  recovery/check/boundary/plan/native-runtime suites: 142 passed, zero skips,
  65.59 seconds (`plan-revision-recovery-33.xml`); the missing-recovery RED is
  retained in `plan-revision-recovery-red.xml`. This does not establish full native
  interruption/resume. Canonical acceptance `34431080747` passed at `b954e63f`,
  before these later changes; successor exact-commit acceptance remains required.

- Omitted interaction assessments now remain explicit pending work in the
  immutable rendering, current cursor and published plan state, alongside the
  existing missing-design warning. Only the canonical graph-connected mode pairs
  are considered; planner limits and its single call are unchanged. Invalid
  string/numeric/null boolean values are rejected instead of silently coerced.
  Candidate 31: 177 installed tests passed in 54.10 seconds; four source-reading
  fixtures were explicitly run in the separate 50-test source suite (3.511s).
  Native transport verifies pending assessment text in the exact plan bytes.
  Evidence: `interaction-31.xml`, `interaction-bootstrap-source-final.xml`.
  Earlier missing-input compatibility and wrongly addressed source deselections
  failed and are retained; five coercion REDs are in `interaction-type-red.xml`.

- Gate evidence now records separate predicate mapping, unmapped rows, bound
  check passes/failures, deferred/unverified rows and baseline assessment. Every
  branch retains `completion_assessment=not_established`; legacy callers cannot
  invent mapping evidence. Installed candidate 27: 119 passed, zero skips,
  54.30 seconds (`gate-evidence-green.xml`), including actual conflicting-evidence
  cursor/gate/journal paths. Two RED cases retained in `gate-evidence-red.xml`.

- Canonical acceptance `34430421499` failed the strict degrade-stage census:
  the new `plan_check_boundary` call was missing from the registry. Added the
  stage without weakening the census. All 64 source session tests passed in
  6.766 seconds (`check-stage-registry.xml`). Candidate 26: 72 installed session/
  boundary tests passed in 11.22 seconds, with the source-tree census explicitly
  deselected because it was tested separately against source. The first installed
  invocation used the wrong deselection node ID and failed that source-only
  fixture; retained as `check-stage-installed.xml`. Final proof is
  `check-stage-installed-final.xml`, including real fault-to-capability reporting.
  Successor canonical acceptance is required; no release-ready claim.

- Agent-run equivalent-check lifecycle: two real source edits coalesce; actual
  isolated pytest execution discharges pending automatic work; an attempted
  duplicate would fail the test. Two pending checks share one pass allowance;
  advancing the controlled clock after the first real execution leaves the other
  pending and the jointly bound row unverified. This complements installed
  shared-binding/revision, mutation, exception, phase and timeout cases.
  An added published-state assertion exposed stale current.json after agent-run
  checks; the observer now publishes once only when a bound observation changes.
  Candidate 25: 201 installed regressions passed, zero skips, 62.18 seconds
  (`agent-check-publication-green.xml`); RED `agent-check-publication-red.xml`.
  Full interrupted/native-agent restoration remains a separate open requirement.

- Two installed RED cases exposed failure masking across evidence channels:
  mapped GREEN hid a failed bound check; a passed bound check hid mapped RED.
  Outstanding-row calculation now preserves either current failure. Candidate 24:
  143 installed regressions passed without skips in 26.00 seconds
  (`evidence-precedence-green.xml`); seven final boundary cases passed in 6.50
  seconds, including actual cursor selection, gate refusal and journal validation
  (`evidence-precedence-final.xml`). Predicate receipts are explicit fixture
  inputs; the bound checks run real pytest. No stronger proof type is introduced.
- Canonical provider-free acceptance `34429663336` passed at `df9a471a`.
  Later scheduling/publication/precedence changes need successor acceptance.

- A follow-on installed RED found the CLI plan snapshot remained UNVERIFIED
  after a boundary check changed the in-memory row to CHECK_PASSED. The existing
  queue now publishes its final state once after draining, using the existing
  content-deduplicated atomic writer. Candidate 23: 87 installed tests passed,
  zero skips, 20.42 seconds (`check-publication-green.xml`); stale-state RED
  retained in `check-publication-red.xml`. No extra check or graph rebuild.

- Gate boundary characterization passed against installed candidate 22: 41 tests,
  zero skips, 7.72 seconds (`gate-boundaries.xml`). The exact 600-second/20-step
  boundary remains refusal-eligible, lower values escape, three stalled refusals
  concede, and progress resets a reached stall count before the decision. A
  controlled verification clock proves 603 -> 599 seconds changes the final
  decision to budget escape and never completion. Real execution/timeout tests
  are separate queue evidence; the clock fixture is not a measured speed result.

- Non-submission verification boundary: installed RED left a registered check
  pending in VERIFY despite ample budget. Candidate 22 now executes that real
  isolated pytest check before context selection. Implementation turns, exhausted
  reserve and fewer than 20 remaining steps do not execute it; a second decision
  does not duplicate it. Executor exceptions retain pending work and do not block
  the model. 146 installed regressions passed without skips in 55.33 seconds
  (`check-boundary-green.xml`); final five boundary cases passed in 5.40 seconds
  (`check-boundary-final.xml`). The initial fixture called start_task with an
  unsupported argument; corrected before the valid RED. This closes boundary
  scheduling, not the separate total-allowance/restart lifecycle requirements.

- Plan rendering now journals exact indexed/rendered/complete/omitted row IDs
  and the immutable block digest without increasing prompt bytes. Installed
  native request interception verifies the exact rendered byte range, separately
  from later steering text. Partial rows do not count as complete; abstention
  clears stale receipts. Candidate 21: 157 passed, zero skips, 56.15 seconds,
  `D:/gt-context-proof/plan-render-final.xml`. Native test initially hashed the
  following steering text too; corrected the test boundary, not runtime bytes.
  The missing receipt API RED is retained in `plan-render-red.xml`. This does not
  establish model consumption or close the broader exposure/count inventory.
- Canonical provider-free acceptance `34428047710` passed at `31cf1205`.
  Check-definition recovery is pushed as `52e5a4cb`; its installed suite passed
  80 tests without skips in 21.05 seconds (`check-recovery-20.xml`). It restores
  validated definitions as pending, never historical proof. Full restart remains open.

- Canonical acceptance `34428047710` PASSED on `31cf1205`.
- Check recovery RED reproduced loss of pending definitions after constructing
  a new adapter over the existing journal. Rebuilt wheel 20 passed 80 installed
  recovery/check/plan-integration/gate tests, zero skips, in 21.05 seconds
  (`D:/gt-context-proof/check-recovery-20.xml`). Covers unchanged/changed test
  sources, corrupt-chain rejection, shared-binding revision, one-time recovery,
  and real isolated execution after recovery. Historical pass records cannot
  grant evidence; recovered checks must execute again. The startup scan retains
  only relevant plan events and does not run at every decision boundary.
- Canonical acceptance `34427398314` PASSED on `e3a4e514`.
- Baseline environment RED reproduced an `intact` result after WIDGET_MODE
  changed. Initial capture now receives the actual task environment; the runner
  establishes GT_PLAN_ROOT before capture. Initial/final environment hashes are
  serialized, and mismatched or historical missing bindings remain unknown.
  Actual installed build_agent proof confirms environment identity alignment.
  Rebuilt wheel 18 passed 105 installed baseline/integrity/integration/check/gate
  tests, zero skips, in 36.75 seconds (`D:/gt-context-proof/baseline-env-final.xml`).
  The earlier broad installed attempt passed 141 but failed four source-reading
  fixture paths; all 45 bootstrap tests then passed separately with source
  available. These distinct proofs do not close restart or complete baseline
  dependency identity. No graph builds or extra checks were added by the repair.
- Final baseline rechecks had the same phase-transition defect as queued checks.
  Real pytest mutations reproduced crashes in VERIFY and SUBMIT. The baseline
  owner now transitions through the existing IMPLEMENT path before invalidating
  mutated evidence; source changes survive and the result remains unknown.
  Rebuilt wheel 16 passed 101 installed check/gate/integration/integrity/baseline
  tests, zero skips, in 29.85 seconds (`D:/gt-context-proof/baseline-phase-green.xml`).
- Canonical provider-free acceptance `34426564301` PASSED at `2ef314ad`.
  The full suite now executes the installed producer witnesses; remaining seven
  skips concern absent historical graph fixtures, unavailable sqlite_vec, and
  the complementary graph-unavailable case, not missing binary wiring.
- Real queued pytest checks now cover shared bindings and pass/fail/mutation/
  timeout outcomes in IMPLEMENT, VERIFY, and SUBMIT. Two installed RED cases
  exposed a lifecycle crash when a check changed source during VERIFY/SUBMIT.
  Automatic mutations now use the existing transition back to IMPLEMENT before
  invalidation, matching ordinary agent edits. Rebuilt wheel 15 passed 82
  installed check/gate/plan-integration/integrity tests, zero skips, 16.35 seconds
  (`D:/gt-context-proof/queue-phases-green.xml`). Restart restoration is still
  open; these execution witnesses do not establish persisted queue recovery.
- Canonical runs `34425339144` and `34425952156` failed because the full-suite
  step could not resolve the verified producer: staging `/opt/groundtruth/gt-index/gt-index`
  did not export a binary path. The workflow now supplies that exact verified
  path as step-local GT_INDEX_BINARY. The missing-env static RED is retained;
  six workflow checks passed in Linux and the tracked-workflow check passed on
  the host. Expanded installed graph tests passed 43 with one explained
  source-registry skip; seven source-history cases initially failed due to the
  wheel-only layout, then all seven passed in a disposable Linux Git clone with
  the pinned producer (`D:/gt-context-proof/producer-env-source-clone.xml`).
  Earlier claims that the resolver fixture alone repaired CI were incomplete.
  Successor full acceptance is required; no tests were disabled for this repair.
- Queue timing RED showed automatic verification starting with a 15-second
  timeout after snapshot capture consumed its entire 30-second allowance.
  The queue now rereads its deadline after capture and retains unexecuted work
  as pending. Rebuilt wheel 14 passed 99 installed baseline/integrity/gate/check/
  cursor/prefix tests, zero skips, in 19.27 seconds
  (`D:/gt-context-proof/queue-budget-green.xml`). The deadline witness uses a
  controlled clock and real snapshot capture; ordinary isolated checks also ran.
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
- Current checkbox count: 42 completed bounded items, 26 open, 68 total. The
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
