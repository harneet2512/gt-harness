# GT Final Regression Repair and 89-Task Gate — 2026-08-09

## Decision

The previous ten-task treatment is rejected. This repair is provider-free verified, but it is not live outcome evidence. No 89-task run may start until the repaired ten-task GT-on smoke passes outcome, provider-evidence, graph, timing, and common-solved efficiency gates.

Confidence in the deterministic diagnosis: **high**. Confidence in future solve/efficiency uplift before the paid smoke: **unknown**.

## Root causes confirmed

1. **Non-semantic source revision:** source identity included mtime/ctime/size, so identical source could change GT state and visible graph text.
2. **No persistent graph-fact origin/window:** eligibility was assigned to the current call, making timing tautological and allowing spill.
3. **Model-authored facts could be re-described as repository intelligence.**
4. **Precedent contamination:** a model-created source file could become repository precedent.
5. **Compaction could inject state it had never removed and repeat frames.**
6. **Prepared requests counted as API calls and deliveries even when never sent.**
7. **Replay v1 was lossy because of fixed per-call and bundle-size caps.**
8. **Efficiency mixed failed tasks and treatment-only controller work with model efficiency.**
9. **Portable capture retried a missing Python interpreter after every edit.**
10. **Local verification selected an obsolete indexer before the repository-pinned binary.**

Archived evidence:

- failed treatment: D:\gt_runs\31329364101
- prior 10/10 treatment: D:\gt_runs\31325108446
- post-repair replay: D:\gt_runs\31329364101\post_repair_replay_20260809.json
- archived run diff: D:\gt_runs\31329364101\post_repair_run_diff_20260809.json

The replay passed all ten archived tasks. This proves repaired deterministic reachability and accounting, not counterfactual model outcomes.

## Implemented architecture

### Semantic source identity

SourceRevisionReceipt contains a content-addressed revision, completeness bit, admitted source paths, and missing-digest paths. Only canonical path plus full-content SHA-256 contributes. Raw workspace revision remains separate for audit/change detection.

An incomplete digest set fails graph refresh and completion certification closed while Mini-SWE execution remains fail-open. Internal revision hashes are no longer model-visible.

### Repository fact provenance and one-window delivery

RepositoryFactProvenance records TASK_START, MODEL_AUTHORED, OBSERVED_EXTERNAL, or UNKNOWN plus origin action, evidence action, eligible call, source path, and revision. A fact has exactly one eligibility call. Represented, budget-omitted, or unselected facts cannot leak into call N+2.

New claims on model-authored paths remain controller-only. A new cross-file consequence on an unchanged repository path can remain eligible. Empty-signature definitions are represented by concrete path plus symbol.

### Precedent boundary

newfile_precedent may choose only a non-empty, validation-relevant, language/package-compatible task-start source. The payload records precedent_origin=task_start_repository. A model-created sibling cannot become repository precedent.

### Unified provider evidence

ProviderEvidenceLedger records graph_frontier, feature_fact, state_frame, progress_frame, and preflight_return events. Each event joins fact/claim IDs, evidence action, eligible/prepared/dispatched call, exact message indices, exact request hash, characters, source revision, disposition, and reasons.

Represented context can be useful with zero newly inserted characters. The invariant is complete accounting and exact provider exposure, not forced advice or nonzero text on every task.

A prepared-but-unsent request now relabels both newly selected and already represented evidence as prepared_not_sent. Neither form can be counted as model-visible without an actual model.query invocation and request hash.

### Provider lifecycle counters

Receipts distinguish provider_requests_prepared, model_query_invocations, provider_responses_received, provider_requests_not_sent, and assistant_steps. api_calls equals model_query_invocations. Prepared deadline/context exits are prepared_not_sent, consume zero API calls, and confirm no delivery. Hash coverage uses invoked requests.

### Exact replay v2

Replay capture is content-addressed:

    gt_replay/
      manifest.json
      calls.jsonl
      blobs/<sha256>.json.gz

Bodies are canonicalized, stored once, hash-verified, and referenced from ordered rows. Legacy size truncation is removed. load_replay_bundle() fails closed on schema, log, blob, JSON, hash, or order corruption.

### Compaction and context

Before measured provider pressure, Mini-SWE history stays unchanged except for separately certified evidence. During compaction, distinct assistant content/reasoning survives; only old tool bodies may become receipts; a state fact is restored only when compaction removed its last concrete representation; generic controller state remains private; identical adjacent frames are not regenerated; facts are complete or quiet.

Metrics separate newly inserted context, represented facts, stock context, feature facts, graph facts, state restoration, and progress facts.

### Bounded progress fact

Progress remains a controller, not an eighteenth feature. StallAggregateFact is produced only from deterministic repetition/cycle/budget state. It names observed state, operation, targets, repeat count, result/timeout, remaining budget, and unresolved anchors. It is declarative, at most 320 characters, at most twice per task, source-bound, one-window, and non-predictive.

### Capture and graph runtime

WorkspaceSensor caches its capture backend. Missing python3 is tried once, then POSIX base64 is reused.

Local resolution now prefers the checked-out vendor/gt-index-src/gt-index.exe. The real gate proves certified directed edges for Python, Scheme, COBOL, R, Verilog, Red, POV-Ray, Coq, Stan, LaTeX, Vim, G-code, Make, and CMake, plus complete registered-parser file coverage.

### Efficiency boundary

Provider/model efficiency aggregates only common uncensored solves: tokens, actual model invocations, model-selected actions, assistant responses, normalized cost, and wall time.

Controller work is separate: effective actions, controller environment executions, cache reads, and sensor executions. Outcome losses/censors still fail. Cheap failed tasks cannot improve the denominator. effective_actions is diagnostic, not the primary model-efficiency gate.

## Provider-free evidence completed

Locally passed:

- all 394 tests in the exact provider-free workflow test scope;
- the two final unsent-evidence and adjacent-restoration regression cases independently after RED-first reproduction;
- all 17 producer, consumer, trigger, payload, timing, and context-accounting gates;
- no blocked actions;
- real vendored graph runtime fixture;
- readiness audit: READY;
- direct and module archived ten-task replay: REPLAY_OK;
- archived run diff;
- Ruff and explicit Python compilation;
- git diff --check.

The repository-wide catch-all collected 1,133 tests but exceeded the ten-minute local command budget before pytest emitted its buffered summary. It produced no failure output, but it is recorded as inconclusive, not passed. The release workflow's exact 394-test scope passed in 104 seconds.

The provider-free workflow and pre-smoke suite now cover semantic revision, provenance, precedent, provider evidence, replay v2, bounded progress, and common-solved metrics.

## Remaining gate

The repair was committed and pushed on `inline-engine`. The exact pushed-commit gate passed every subcheck and printed `SMOKE_APPROVED`; no paid smoke was started as part of this implementation audit.

1. Dispatch only the ten-task certified_full/integrated GT-on smoke: ACTIVE integration, SHADOW preflight, exact replay capture, graph required, and completion/progress controls. Reuse the frozen GT-off baseline in Downloads.
2. Audit official and uncensored outcomes first, then graph completeness, all effect and provider-evidence dispositions, actual dispatch counters, timing, duplication, payload semantics, and common-solved deltas.
3. Keep 89 blocked unless the ten-task smoke has no solve regression/censor, valid graph for every applicable task, zero invalid/late/predictive/duplicate delivery, and an aggregate common-solved provider/model efficiency win.

## Rollback

integration_mode=off restores the baseline loop. Preflight remains SHADOW in the next smoke. No rewrite or feature-driven suppression is enabled.
