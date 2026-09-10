# GT Engine Map

The single reference for what GroundTruth stores, when it stores it, and what
each capability does from task start to submission. Written against
`codex/product-completion` at `2cbb9939`.

## Current implementation overlay (2026-09-10)

The run measurements and legacy-path findings below remain historical evidence.
For the current candidate, follow `docs/plans/context-plan-implementation-status.md`.
Harness `b68de522` pins producer `efa70e52`; `ab58d4af` corrects and verifies
the updated lineage seal. Canonical provider-free CI `34438826174` passed at
`440c337b`. These are scoped candidate commits, not a benchmark-ready release.

- The workflow now supplies `max_iterations=0` (Mini-SWE's unlimited-step mode).
  GT represents unlimited remaining steps as null, not zero. Wall-clock limits
  and the finalization reserve remain in force. Installed Mini-SWE executed 301
  synthetic queries and rejected the next query at its wall deadline.
- `CheckSpec` is explicit argv, not a shell receipt. Checks are coalesced after
  edits and drained at submission. Passing a bound check is `CHECK_PASSED`, not
  proof of arbitrary behavior; unbound full-suite/lexical matches no longer grant
  GREEN. The supported exact-assertion and filesystem paths remain separate.
- The legacy `-file` path in section 3.2 remains conservatively incomplete and
  its in-place capability remains undeclared. The candidate uses a different,
  staged `-amend-parent` batch API, declared as `batch_parser_node_reuse_v1`.
  It copies the certified parent once, retains unchanged parser-owned node IDs,
  loads complete content-keyed parser inputs, and executes the complete existing
  resolver/analysis pipeline once. It does not narrowly guess affected callees.
- Parser inventories bind retained nodes, properties, assertions and structural
  edges to exact parser facts. Unchanged fact rows retain physical identities;
  assertions are freshly resolved before comparison. History/cochanges and
  derived analyses still run. This is structural reuse, not a claim that
  every graph layer is incrementally maintained. Parser cache storage survives
  graph revisions outside the indexed input; the same coordinator still owns
  one active and one coalesced pending build.
- The real installed automatic refresh handled five changed paths, including
  configuration, in one batch and preserved its parent bytes. Nine source
  transition fixtures matched fresh normalized graph facts. All-consumer parity
  and fixed-workload performance are still open; no measured end-to-end speedup
  or no-regression benchmark claim follows from these checks.
- Baseline commands use the existing isolated process-tree boundary and full
  captured output. A new failing test is not labeled previously passing unless
  its identity appears in the baseline passing set. Unknown or unattributed
  failures are never reported as an intact baseline.

Producer CI `34436549266` and static Linux build `34436550584` passed at
`efa70e52`. Installed candidate 44 passed 200 checks with one explicit
source-registry deselection; 14 complementary source checks passed. The
remaining tracker requirements are separate gates.

Every number in this document was measured, not estimated. Where a measurement
comes from a specific run it is named, so a future reader can check it rather
than trust it. Where something is a known defect it says so in the same
sentence as the thing it describes, because a map that only records intentions
is how the same hour gets spent twice.

---

## 1. The three artefacts

GT produces exactly three durable things during a task. Everything else is
derived from them.

| artefact | where it lives | written by | read by |
|---|---|---|---|
| the **code graph** | `gt-state/<reuse-key>/revisions/<rev>/graph.db`, SQLite | the `gt-index` producer, a Go binary | localization, callers, covering tests, the plan's anchors |
| the **journal** | `gt-state/<task>/events.jsonl`, append-only, hash-chained | the engine, on every boundary | receipts, attestation, every diagnostic in this document |
| the **evidence store** | `gt-state/<task>/output_evidence/`, `deliveries/`, `persistent_plans/` | the engine | receipt verification, replay |

A published graph revision is **immutable**. Its manifest pins its exact bytes,
so amending one in place would invalidate the certificate readers already hold.
Amendment always works on a copy, which is then published as its own revision.

---

## 2. What is in the graph

### 2.1 Tables

| table | holds | keyed by |
|---|---|---|
| `nodes` | every definition, plus the resolution overlay | `id` autoincrement, `file_path` |
| `edges` | relationships between nodes | `source_id`, `target_id`, `type`, `source_file` |
| `resolution_symbols` | one row per declared symbol | `stable_id`, and `path` for the file |
| `resolution_callsites` | one row per call, resolved or not | `callsite_id`, `source_file`, `callee` |
| `resolution_candidates` | every target a callsite could mean | `callsite_id` + `target_id`, cascades on callsite delete |
| `properties` | parsed attributes of definitions | `node_id` |
| `assertions` | assertions found in source | `node_id` |
| `file_hashes` | content hash per file, for short-circuiting | `file_path` |
| `cochanges` | files historically changed together | pair |
| `closure` | transitive reachability sidecar | full-index only, dropped on amend |
| `project_meta` | `analysis_state`, `resolution_complete`, revisions | key |

### 2.2 Node kinds

Structural nodes carry a `file_path`. So does every overlay node, which is what
makes per-file scoping possible.

| `node_type` | `label` | what it is | scoped by file? |
|---|---|---|---|
| *(null)* | `Function`, `Class`, … | a real definition | yes |
| `callsite` | `Callsite` | one call, with its dispatch state | yes |
| `completeness_fact` | `CompletenessFact` | what the analysis could and could not prove | yes |
| `derivation_fact` | — | how a candidate was derived | yes |
| `unresolved_fact` | — | a call that resolved to nothing | yes |
| `query_policy_version` | `QueryPolicyVersion` | repository-wide metadata | **no** — must survive every scoped delete |

Measured on a three-file Python fixture: 6 definitions produce 44 nodes. The
other 38 are overlay. That ratio is why an unscoped overlay delete looks like
catastrophic data loss — because it is one.

### 2.3 Edge kinds

| type | meaning | carries `source_file`? |
|---|---|---|
| `CALLS` | caller to callee | yes |
| `IMPORTS` | file to imported module | yes |
| `HAS_CALLSITE` | definition to its callsite node | yes |
| `CANDIDATE`, `CANDIDATE_TARGET` | callsite to a possible target | yes |
| `SELECTED_TARGET` | callsite to the chosen target | yes |
| `HAS_COMPLETENESS_FACT` | node to its completeness fact | **no** — scope via `source_id`/`target_id` |
| `HAS_DERIVATION_FACT`, `HAS_UNRESOLVED_FACT` | node to its fact | no |

---

## 3. How the graph is built

### 3.1 Full build

```
gt-index -root <repo> -output graph.db
```

Parse every file with tree-sitter → build name, file, inheritance, assignment,
return-shape and parameter-type indices → `resolver.ResolveWithProvenance` →
publish nodes, edges and the resolution overlay in one atomic transaction →
certify → publish as an immutable revision.

Cost is dominated by parsing and scales with repository size, not with the size
of the change. Measured per build on run 34374028796:

| repository | mean full build |
|---|---|
| arktype | 116s |
| boa | 55s |
| awilix | 18s |
| oxvg | 22s |
| pest | 1.9s |
| claude-code | 1.8s |

### 3.2 Incremental amend, and why it is not used

```
gt-index -root <repo> -output graph.db -file <one/path>
```

The flag exists and works. The producer re-parses one file, restores the
incoming cross-file edges it would otherwise strip, and re-resolves that file's
own calls against indices built **from the database**, not from re-parsing.

It is nevertheless **switched off**, and deliberately. `AMEND_CAPABILITY` in
`gt_engine/indexer.py` requires the producer to *declare*
`incremental_amend_in_place`. The certified producer `c3b9f16e` declares
`incremental_stale_suppression` instead, and the difference is not cosmetic:
after a single-file amend it deletes the **entire repository-wide resolution
overlay** and marks the graph incomplete.

Measured, on the three-file fixture, amending one file:

| | before | after |
|---|---|---|
| nodes | 44 | 8 |
| edges | 57 | 13 |
| callsites | 6 | 0 |
| symbols | 6 | 0 |
| `analysis_state` | complete | not_run |

In production the same behaviour discarded 177,390 of 181,200 nodes for a
twenty-symbol edit. The capability is therefore withheld on purpose: nothing
observable at the command line separates a producer that amends correctly from
one that empties the graph, **so the binary has to say which it is**. Adding the
string without repairing the behaviour would trade the graph for speed.

The wholesale delete is itself intentional, and its own comment explains why: a
single-file refresh cannot prove repository-wide candidate parity, so rather
than leave stale answers readable it removes them. `resolution_complete` is one
repository-wide boolean, and with one boolean the only honest answer after a
partial update is "none of this is complete".

**The consequence.** Every edit pays for a full rebuild. On run 34374028796,
across six tasks, **256 builds started and 134 published**: 122 were superseded
before they finished, because on arktype the agent edits about every 67 seconds
while a full build takes 116. A build is obsolete on arrival by arithmetic.

**The fix, designed and proven but not landed.** Parity *is* provable:
`resolution_candidates.target_id` joined to `nodes.file_path` names exactly the
callsites a change can affect. Scoping both deletes to the changed file and its
dependents lifts an amend from 8 of 44 nodes to 32 of 44 with symbols intact.
The remaining work is re-resolving the dependent files, which needs the analysis
phase run over the affected subset, because a callsite cannot be re-resolved
from its stored row alone — `resolution_callsites` keeps the callee's last name
component but not its scope, qualified name, argument arity or AST path. Patch
kept at `incremental-scoping.patch`; it is not applied, because the vendored
source must hash to the fingerprint the certified binary declares.

### 3.3 Scheduling

`GraphBuildCoordinator` runs one build at a time and keeps at most one
coalesced pending request. It already works: on arktype 352 refresh requests
collapsed to 36 builds. The waste is not scheduling, it is that each build is
full.

---

## 4. What happens on an edit

The order matters, and each step writes a journal row of the same name.

```
before_action        pre-image of the worktree
                     (reused from the previous action's post-image; recaptured
                      only after the submit gate may have run the test suite)
execution_started    the agent's command runs, 30s cap
execution_finished
                     post-image captured
edit_transaction     diff_workspace: what actually changed on disk
graph_invalidated    the graph no longer describes the code
graph_refresh_scheduled
                     coordinator coalesces
graph_build_mode     full | incremental, with elapsed_ms and dirty_path_count
graph_publication    only if the result still matches current source
obligation_invalidation
                     workspace epoch bumps; every receipt bound to the old
                     epoch stops counting
obligation_reverified
                     evidence that still holds is re-proven for free
```

**Why the epoch matters.** Evidence is bound to the state of the code it was
observed against. An edit invalidates it, and reverification re-establishes what
is still true. This is what stops a receipt outliving the code it described.

**The snapshot.** `capture_workspace` used to run before *and* after every
action: 601 captures on arktype at 1.08s each, 652s of a 6,049s task. The two
describe the same tree, since between one action's post-image and the next
action's pre-image the agent is not running and GT's own writes go to excluded
state directories. The post-image is now carried forward. The carry is dropped
before any submit, because the gate may re-run the repository's suite and a
suite can write to tracked files.

---

## 5. Task contract, obligations and receipts

```
task prompt
  └─ extract_task_contract        normative lines → obligations
      └─ compile_obligation_predicates
          └─ predicates, each UNKNOWN | GREEN | RED
              └─ record_receipt        evidence, bound to a workspace epoch
                  └─ evaluate_passing_observation
                        a passing command whose footprint matches a predicate
                        turns it GREEN
```

A predicate is never GREEN because the model said so. It is GREEN because a
command ran and its observed result matched. That is the whole point of the
engine, and section 8 explains why it is also rare.

---

## 6. The persistent plan

Built once, before the first edit, from the prompt, the repository at base
commit, and the graph. Never from the tests, the fail-to-pass list, or anything
under the benchmark harness.

### 6.1 Phase 0 — deterministic, no model call

| step | module | produces |
|---|---|---|
| requirement ledger | `persistent_plan/ledger.py` | every normative line, verbatim, one row each, `req-<sha12>` |
| graph anchors | `persistent_plan/anchors.py` | definitions per row, callers, covering tests, mode candidates, callee-first edit order |
| regression baseline | `persistent_plan/baseline.py` | the repository's own suite result before any edit |
| deterministic plan | `persistent_plan/deterministic.py` | every row with an anchor and a check |

Bounds: 6 anchors per row, 12 callers per anchor, 24 mode candidates, 16
members per mode. Baseline capped at 120s and 3% of the budget; a slow suite
abstains rather than spend the agent's clock.

**A check comes from the discovered test command, not from a successful
baseline run.** These are different questions. Tying them together meant that on
four of six tasks the baseline reported `no_tests_observed`, the command was
discarded, and every row shipped with no way to prove it — awilix carried 27
requirements and 0 checks, boa 41 and 0.

### 6.2 Phase 1 — exactly one provider call

`bootstrap_persistent_plan()` in `miniswe_runtime.py`, a sibling of the catalog
bootstrap. One call, counted at the transport, `temperature=0`, no retries.
Failure degrades the plan to the deterministic one; it never raises.

The model receives the ledger, the anchors with signatures and callers, the
interaction pairs, the baseline and the known gaps. It returns design intent,
a design and a differential acceptance criterion per requirement, and the
interaction cells that apply.

**The ask shrinks as the ledger grows.** Above `MAX_ROWS_FOR_INTERACTION_SWEEP`
(20) requirements the interaction sweep is not offered at all. The call spends
its output budget on reasoning before it writes anything: on run 34374028796
every task burned essentially the whole 32,768 tokens thinking, and the two with
27 and 41 requirements never reached the tool call, while the two with 12 and 13
did. Raising the ceiling from 16,384 had only moved which tasks fell on which
side of that line.

Interaction pairs are the union of two rules — the modes a requirement's own
definitions reach, and the modes living in the same file. On the measured task
that is 119 cells against a 1,248-cell full product, reaching 34 of 52
requirements and 19 of 24 modes. Call edges alone reached only 18 and 10.

### 6.3 Phase 2 — delivery

| where | what | size | when |
|---|---|---|---|
| durable task message | `[GT_PERSISTENT_PLAN]` block | ≤8,000 chars, ~1,555 tokens | once, before the first main call |
| request tail | `[GT_PLAN_CURSOR]` | ~80 tokens | when a row's evidence changes |
| request tail | `[GT_OBLIGATION_DELTA]` | bounded | when obligation status changes |

The block is 1.94% of a median 80,169-token request and is cached after the
first call. The cursor across 161 turns is 12,880 tokens, **0.057%** of that
task's 22.7M prompt tokens. Token cost has never been the constraint here.

**The cursor must never claim completion.** Its first version reported
progress -- "9/12 requirements proven", naming the satisfied rows. Measured on
run 34404529920 that was a disaster: a row turns GREEN through
`evaluate_passing_observation`, which matches a passing command lexically
against an obligation, and commands the agent ran while merely EXPLORING
satisfied nine of twelve rows on a task where it had written no code at all.
The agent read the count, drew the obvious conclusion, and submitted after 208
turns and zero edits. pest fell from 85 of 104 tests to none, and awilix from a
pass to 22 of 24. The inaccuracy was not new; promoting it to the most
action-guiding position in the context, phrased as a confident count, is what
made it expensive. **A steering signal may only assert what it can prove.** The
cursor now names the requirement to work on, its design and the command that
would demonstrate it, and says nothing about what is done.

**The cursor is the part that makes the plan act.** The block alone was a
document filed at turn one and never mentioned again: across a 300-turn task the
tail was touched three times. The cursor names one requirement, its design and
the command that proves it, walks the graph's callee-first order, supersedes its
predecessor so it never accretes, and **advances only when a receipt proves the
row** — never on the model's assertion. A survey of thirteen coding agents found
not one that verifies a checked box against execution evidence.

### 6.4 Phase 3 — the submit gate

`persistent_plan/gate.py`, a pure function of the facts.

| constant | value | why |
|---|---|---|
| `MIN_REMAINING_SECONDS` | 600 | refusing later turns a near-miss into a zero |
| `MIN_REMAINING_STEPS` | 20 | same, in steps |
| `MAX_REFUSALS_WITHOUT_PROGRESS` | 3 | the stall catch |

The gate refuses while rows are unproven **and** the budget is healthy **and**
refusals are still converting into proven rows. Progress resets the stall
counter, so an agent that keeps proving rows is never cut off. Three refusals
that prove nothing and the gate concedes, recording `refusals_without_progress`,
because a gate that refuses forever scores zero and an incomplete patch does not.

It previously refused exactly once. On run 34374028796 that meant refusing with
rows unmet and accepting 34 seconds later with 4,260 seconds and 142 steps still
available.

---

## 7. The 21 features and where each acts

`kind=FACT` supplies evidence; `kind=CAP` performs an action. `CAPABILITY_OWNERS`
maps each CAP to the FACT that must have fired first.

| feature | kind | boundaries |
|---|---|---|
| `obligations` | FACT | task_start |
| `localization` | FACT | task_start, search_result |
| `def_partition` | FACT | search_result |
| `caller_contract` | FACT | file_view, edit_result |
| `cochange_prior` | FACT | file_view, edit_result |
| `newfile_precedent` | FACT | search_result, edit_result |
| `signature_delta` | FACT | edit_result |
| `syntax_result` | FACT | edit_result, submit |
| `covering_red` | FACT | edit_result, submit |
| `recovery` | FACT | test_result, tool_result |
| `submit_refusal` | FACT | submit |
| `select_catalog` | CAP | task_start |
| `persistent_plan` | CAP | task_start |
| `plan_gate` | CAP | submit |
| `GT_LOC_RESLOT` | CAP | task_start, search_result |
| `GT_CHANGE_SURFACE` | CAP | search_result |
| `GT_PATCH_DELTA` | CAP | edit_result |
| `GT_EDIT_CHECK` | CAP | edit_result, submit |
| `GT_HYPOTHESIS` | CAP | test_result, tool_result |
| `GT_SS_SUBMIT_RED` | CAP | submit |
| `GT_CERT_DELIVERY` | CAP | submit |

Every feature must carry a **positive witness test**, pinned by fully qualified
name in `gt_engine/feature_matrix.py`. Renaming a test without repointing its
pin leaves the feature with no proof, and readiness refuses the commit. That is
not a nuisance; it caught exactly that mistake on run 34402254504.

Historical note: `persistent_plan` and `plan_gate` audited as `INELIGIBLE` with
`no_trigger_observed`, because the plan block is appended directly to the task
message rather than admitted through `admit_decision_packet`. The feature works
— the journal proves it — but the attribution census cannot see it. Known gap.

---

## 8. Where the time goes

Current plan audit: the rendering is stored by content hash and verified against
the immediate provider request (including message-CAS storage) and matching
response. `WITNESSED` means exact-byte exposure and response linkage, not
semantic use or correctness. Missing/tampered bytes and unmatched boundaries
stay `DELIVERED_UNEXPOSED`. `plan_gate` attribution remains open; its decision
journal alone is not evidence that a directive reached the model.

Measured across six tasks on run 34374028796, 29,972 seconds total.

| bucket | seconds | share |
|---|---|---|
| waiting for the model | 22,778 | 76% |
| graph rebuilds, blocking portion | 1,933 | 6% |
| worktree snapshots | 1,760 | 6% |
| the agent's own shell commands | 1,523 | 5% |
| semantic observation and context assembly | 790 | 3% |
| evidence delivery preparation | 501 | 2% |

The planning call sits inside the model bucket at 5,887 seconds, **26% of all
model time** for one call per task.

Two cautions for anyone reading these numbers later. Graph builds run on a
background thread, so their 4,175 seconds of *work* on arktype is not 4,175
seconds of *waiting* — occupancy was 69%, blocking was far less. And the
dominant multiplier is not in the table at all: tasks that fail run to the
300-step cap and therefore take twice as long as the 152.9-step reference. A
task that succeeds stops at around 160 turns, and claude-code minus its planning
call took 1,445 seconds against a 1,439-second reference.

---

## 9. Budget

| quantity | value | source |
|---|---|---|
| benchmark agent timeout | 5,400s | `task.toml` `[agent] timeout_sec` |
| GT overhead extension | 1,500s | `GT_OVERHEAD_EXTENSION_SECONDS`, **a declared deviation** |
| supervisor grace | 240s | measured: 100s setup + 68s finalization |
| agent receives | 6,660s | |
| step limit | 300 | **ours**, not the benchmark's |

The extension exists because the graph build and the planning call precede the
agent's first step — 949 seconds of it on one measured task, 18% of the budget.
It is added after any stage cap, recorded beside an untouched benchmark base,
and flagged `deviates_from_benchmark_budget`. **Token and step counts remain
comparable to the leaderboard; wall clock does not, and no result produced under
this budget may be reported as a stock 5,400-second run.**

---

## 10. Rules that are not negotiable

- Never read the benchmark's tests, its fail-to-pass list, or its verifier while
  the agent or harness is working. The reward is the only signal.
- Never run a GT-off evaluation. Fetch the published baseline.
- A published graph revision is immutable. Amend a copy.
- A capability is a promise about behaviour. Do not declare one the binary does
  not keep, and read a fail-closed flag's rationale before changing it.
- Every feature needs a positive witness test, pinned by name.
- Correct-or-quiet: a GT fault degrades the feature, never the run.
