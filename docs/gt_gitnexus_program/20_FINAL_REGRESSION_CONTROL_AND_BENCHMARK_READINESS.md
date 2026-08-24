Exit code: 0
Wall time: 0.3 seconds
Output:
# Final regression control and benchmark-readiness record

> Current-state note (2026-08-24): this file is a retained historical central-engine
> research record. It is not the GT Harness release authority. The current product
> boundary and decision are [canonical architecture](../../CANONICAL_ARCHITECTURE.md)
> and [final release decision](../../FINAL_RELEASE_DECISION.md). The legacy
> `eval/release/active_release.json` applies only to its central experiment.

Status: **historical research snapshot; superseded for product status.**

Audit date: 2026-08-19.

## 1. Evidence that forced this repair

The most recent GT treatment, workflow `32287093005`, returned 20 rows, graded
18, solved 12, and had two provider errors. Repository intelligence passed on
all 20. Its first provider request differed from the same-run pre-GT control on
19 tasks. The run is therefore evidence that substrate availability was no
longer the primary problem; delivery policy was changing the model too often.

The fresh GT-off workflow `32292828255` downloaded 19 readable graded task
results. The twentieth task, `largest-eigenval`, was absent from the downloaded
artifacts and must remain `missing_expected_task`, not be imputed from logs. The
canonical reconstruction reports 15 solves among the complete 19 rows and
fails closed on the 20-task denominator. Its generated local audit artifacts
are `.research/baseline-32292828255-canonical.json` and
`.research/baseline-32292828255-canonical.md`.

On the 19 artifact-complete tasks:

| quadrant | count | tasks |
|---|---:|---|
| both solved | 11 | cobol, count, FEAL, fix-code, headless, MCMC, portfolio, prove-plus-comm, qemu, sanitize, write-compressor |
| fresh baseline only, both graded | 2 | regex-chess, torch-tensor-parallelism |
| GT only | 1 | winning-avg-corewars |
| both failed, both graded | 3 | extract-elf, torch-pipeline-parallelism, video-processing |
| baseline solved, GT provider-censored | 2 | LLM batching, schemelike |

Outcome confidence is **high** for those raw quadrants and **moderate** for a
GT-causal loss on regex/tensor. One stochastic trial per arm is not causal
proof. Treatment-side trajectory evidence nevertheless shows the defect:

- regex used the same first repository read in both arms and reached 100 calls;
  early GT context did not replace that read or reduce the trajectory;
- tensor received instruction-derived deliverable state early, while the fresh
  baseline solved by continuing to inspect and support both weight
  orientations;
- the old policy treated grounded, timely truth as sufficient delivery
  authority even when the provider already had the fact or no model operation
  was replaced.

## 2. GitNexus result used, and its limit

The latest official GitNexus `main` at audit time is
[`aac7515d2a8c50a1f8f923c6fb77218b333560d6`](https://github.com/abhigyanpatwari/GitNexus/commit/aac7515d2a8c50a1f8f923c6fb77218b333560d6).
Confidence in the source findings below is **high**.

What GT adopts:

1. The public `native_augment` evaluator appends compact graph context to the
   observation produced by a search rather than requiring another provider
   decision ([source](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/eval/agents/gitnexus_agent.py#L78-L131)).
2. The augmentation engine bounds callers, callees, and process membership
   ([source](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/gitnexus/src/core/augmentation/engine.ts#L91-L177)).
3. Scope resolution has explicit ambiguous/unresolved/external outcomes
   ([source](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/gitnexus/src/core/ingestion/scope-resolution/resolution-outcome.ts)).
4. Its separate workflow-candidate harness requires paired valid runs, rejects
   unequal/excluded runs and quality regressions, and caps per-task efficiency
   regressions before promotion
   ([source](https://github.com/abhigyanpatwari/GitNexus/blob/aac7515d2a8c50a1f8f923c6fb77218b333560d6/eval/workflow_bench/evolution.py#L478-L645)).

What GT deliberately does not copy:

- automatic augmentation errors that collapse to an empty string;
- setup failure that continues as an apparently valid treatment;
- commit-only staleness for uncommitted working-tree edits;
- prompt changes mixed with tool/delivery changes in one causal comparison;
- aggregate-only solve and cost accounting.

GitNexus's main graph evaluator has no provider-view hash, changed-message
index, first-eligible claim receipt, per-task negative-flip attribution, or
source-edit freshness barrier. GT keeps those stronger contracts.

## 3. Implemented regression-control contract

### Provider information value

[contributions.py](../../gt_engine/contributions.py) now defines a replayable
`ProviderValueCertificate`. A claim can be provider-visible only when it is
exact and belongs to one of three classes:

1. certified action-local/nonlocal relation;
2. new execution contradiction or attributable validation state; or
3. certified pre-decision information gap.

The certificate must name authority, materiality, revision, anchors, novelty
basis, decision point, and the ordinary operation it replaces. Missing proof becomes
`value_uncertified`; rejected proof becomes `value_rejected`. Only the former
is a release-integrity error. Instruction restatements, generic anchors,
deliverable presence, local file facts already in the tool observation,
generic persistent state, and partial/ambiguous relations remain private.

### Producer and provider-boundary enforcement

- [task_semantic_substrate.py](../../gt_engine/task_semantic_substrate.py)
  keeps instruction-derived deliverable and generic-anchor facts private while
  retaining genuinely workspace-derived binary/check evidence.
- [repository_context.py](../../gt_engine/repository_context.py) removes
  semantic, process, and impact facts whose endpoints are confined to the path
  just read/searched; only certified consequences that cross the observation
  boundary remain eligible.
- The feature authority table is exhaustive over all 17 historical mechanisms.
  Ten are explicitly controller-only; syntax/test/repeated-failure/submission
  contradictions retain execution authority; signature and precedent facts
  require a certified nonlocal relation; and edit-check delivery requires the
  typed `validation_debt` pre-decision gap.
- [gt_central_agent.py](../../eval/gt_central_agent.py) attaches certificates
  before the shared contribution compiler and records
  `gt.provider_value.v1` in every final-profile receipt.
- [delivery_audit.py](../../gt_engine/delivery_audit.py) joins every visible
  claim to exactly one selected admissible certificate, separates explicit
  provider claim IDs from lower-level fact/effect IDs, and independently
  validates task-semantic, feature-guidance, frontier, repository-context, and
  persistent-state support.
- [central_release_gate.py](../../scripts/central_release_gate.py) rejects a
  final-profile receipt with a missing contract, an uncertified selected
  contribution, duplicate certificate identity, partial/ambiguous selected
  evidence, or a certificate for an unselected contribution.

### Solve and efficiency amplifiers

- `CoupledChangeObligation` remains advisory and composes a changed endpoint,
  certified dependents, tests, and a declared check without claiming that every
  dependent must be edited.
- `ResolvedConventionRecord` is now emitted only when an exact signature/type,
  a certified caller, and a certified test agree. Conflicting return/type
  evidence increments `conflicting_type_evidence` and abstains; it never guesses
  a singleton convention.
- Completion plans and predicate observations now carry typed schemas. The
  release gate rejects stale/missing/failing predicate evidence, partial-plan
  auto-submission, and inconsistent submit accounting.
- Final `central_relational_v2` no longer performs soft character-pressure
  compaction. Compaction is permitted only when the measured provider request
  exceeds the provider-budget reserve. The release gate rejects any final-v2
  `character_pressure` epoch.

### Exploration replacement and reasoning observability

[delivery_audit.py](../../gt_engine/delivery_audit.py) adds an
`exploration_replacement_receipt` to each visible delivery. It records the
expected replaced operation, first follow-up action, whether the model first
used the supplied anchor without rediscovery, and whether exploration was
replaced, accompanied, or followed. [tb2_regression_forensics.py](../../scripts/tb2_regression_forensics.py)
joins the preceding GT delivery and certificate to the first divergent model
command and visible reasoning anchor references.

This is the strongest audit available without reading hidden model state:
provider receipt proves exposure; reasoning text and action alignment prove
observable uptake; a matched arm or ablation is still required for causality.

### Canonical Harbor artifact ingestion

[harbor_results.py](../../scripts/harbor_results.py) loads both Harbor aggregate
and per-trial schemas, ignores job summaries, deduplicates only identical rows,
rejects conflicts, and checks the exact expected task set. A verifier-graded
row is not called infrastructure-censored merely because Harbor also recorded a
nonzero agent exit after submission.

[stage_harbor_artifacts.py](../../scripts/stage_harbor_artifacts.py) stages one
readable shard tree, verifies its expected tasks, and writes a hashed artifact
manifest before upload. The baseline sharded workflow now uses both scripts and
sets `if-no-files-found: error`. This prevents the previous false 0/20 merge and
prevents a missing shard from becoming a valid denominator.

## 4. Verification performed on this candidate

1. Focused engine/delivery/release/agent/forensics/Harbor suites passed; one
   intentional skip required the real pinned Snowflake ONNX asset.
2. Final full local Python suite after the regression shield, convention, and
   completion repairs: **2,005 passed, 5 explicitly skipped, 0 failed** out of
   2,010
   collected tests. The skips are the unprovisioned real ONNX fixture, one
   POSIX-shell test, one redundant graph smoke, Unix mode bits, and Windows
   symlink privilege.
3. Current Go source, Windows with production `sqlite_fts5` tag: all packages
   passed and a new binary built.
4. The rebuilt Windows binary passed the declaration-free shell repository-
   intelligence witness end to end. The stale binary was retained locally as a
   recoverable backup.
5. The exact source candidate was reconstructed in a clean Linux Codespaces
   worktree at base commit `592ef7a63a8722c3bf6e2099fe8b49bf010a948d`
   by applying only the candidate patch and four intended new files. Current Go
   source with the production `sqlite_fts5` tag passed all packages and built
   binary SHA-256
   `f3be26b1aac319d0d09b7d6ad5ddf84c77b2aabbbc133cdeeb997fc1153a10d4`.
6. Codespaces exposed a Windows-hidden test defect: the POSIX manifest witness
   invoked `bash -lc`, allowing Codespaces login-profile code to change the
   test working directory. The production command is executed directly in the
   task workspace, so the witness now uses `bash -c`. The repaired witness and
   all 39 repository-intelligence tests passed on Linux.
7. The pinned ONNX model was transferred and independently matched SHA-256
   `564e6c65ee0c739a486702e9e3e9b33c3f697c19c34dbe886bce9eec497ce971`.
   With that real dense asset and the source-built indexer, Codespaces passed
   the complete strict lifecycle suite, both feature-census entrypoints,
   repository-intelligence substrate proof, pinned language contract, and
   readiness audit. The pre-smoke wrapper ended `SMOKE_BLOCKED` solely because
   the intentionally dirty detached candidate is not an exact pushed commit;
   every implementation subgate printed `PASSED` and readiness printed
   `READY`. The raw provider-free log is
   `.research/codespace-provider-value-pre-smoke-final.log`, SHA-256
   `8c5342e72be7aa05f42b690465a8dd53721672d67b5e7527b6d22c052a9b1a28`.
8. Final local static proofs after the provider-value repair passed:
   `ALL_17_PRODUCERS_PROVEN`, `ALL_18_PRODUCT_MECHANISMS_PROVEN`, `READY`,
   documentation consistency `PASS`, `EVIDENCE_SOURCE_ALLOWLIST_PROVEN`, and
   `NO_GRADER_ACCESS_PROVEN` across 287 audited receipts. The configuration
   portion of mechanical completeness passed.
9. The audit then exposed a stale-identity defect in the no-spend entry point:
   it could print `GT_MECHANICAL_COMPLETENESS=PASS` for release runtime
   `855b0deb...` while tracked source changes existed outside that commit. The
   gate now checks `git status --porcelain --untracked-files=no` before release
   proof. On this uncommitted candidate it correctly returns `BLOCKED` with
   `tracked_worktree_not_clean`; artifact:
   `.research/final-mechanical-dirty-guard-20260819.json`.
10. Fresh baseline canonical replay: 19/20 rows, 19 graded, 15 solved, zero
   infrastructure errors, exact failure `missing_expected_task:largest-eigenval`.

The candidate's mechanics are source-built and cross-platform verified. The
release identity is not: the implementation remains an uncommitted worktree,
so the fail-closed exact-SHA gate correctly refuses promotion. Until the exact
committed SHA repeats the same provider-free proof, the honest engineering
state remains **IMPLEMENTED_UNVERIFIED**, not benchmark-ready.

## 5. Exact remaining gate before money is spent

1. Commit/freeze the candidate and repeat the provider-free proof for that exact
   SHA. Update `active_release.json` only after the SHA exists and all recorded
   artifact hashes match it.
2. Retrieve or explicitly rerun only the missing baseline artifact
   `largest-eigenval`; do not rerun the other 19 baseline tasks.
3. Only then run one GT treatment over the same frozen 20. Compare it once with
   the now-complete fresh baseline. Report integrity, solve, efficiency, and
   intervention results separately.

No repeated 20-task treatment runs are part of this gate. If the single run
finds a new integrity defect, the release is blocked and repaired using a
deterministic witness. Outcome variance is handled by paired trajectory and
mechanism evidence, not by rerunning until a favorable score appears.
