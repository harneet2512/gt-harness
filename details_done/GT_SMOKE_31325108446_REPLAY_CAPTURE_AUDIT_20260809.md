# GT-on capture smoke audit — workflow 31325108446

Date: 2026-08-09  
Commit: `cf9411ccae9a580f5f91500d7e9c43dec12551ae`  
Workflow: `tb2_miniswe_central.yml`  
Arm: `certified_full` / `integrated`  
Replay capture: explicitly enabled (`replay_capture=true`)  
Tasks: the frozen ten-task GT-off smoke slice

This was the authorized paid smoke after the provider-free certification. It is
diagnostic evidence for the capture and integrity path, not approval for the
89-task run.

## Outcome

The ten task jobs all completed and produced verifier results. The merge job
exited 2 because the merged result correctly marked `gpt2-codegolf` as an
invalid treatment: repository intelligence was required but the task had no
usable graph substrate (`repository_intelligence_status=not_applicable`,
`repository_graph_schema_valid=0`, `repository_graph_nodes=0`, and
`repository_graph_edges=0`). The merged artifact still contains all ten task
results.

| Outcome | GT-off baseline | GT-on capture smoke |
|---|---:|---:|
| Official verifier resolves | 9/10 | 9/10 |
| Uncensored resolves | 9/10 | 9/10 |
| Solve regressions | — | 0 |
| Treatment censored tasks | — | 0 |
| Invalid treatment tasks | — | 1 (`gpt2-codegolf`) |

Per-task outcome was identical to the frozen baseline: nine rewards of 1 and
`gpt2-codegolf` reward 0. Matching reward is not a causal improvement claim,
and the invalid graph substrate means this run cannot approve the treatment.

## GT integrity and timing

Every task receipt reports all 17 feature IDs enabled (`feature_count=17`),
249 produced effects in aggregate, and 249/249 effects applied. Nine feature
IDs fired naturally in at least one task:

`obligations`, `localization`, `GT_LOC_RESLOT`, `GT_CHANGE_SURFACE`,
`GT_PATCH_DELTA`, `GT_EDIT_CHECK`, `syntax_result`, `GT_CERT_DELIVERY`, and
`newfile_precedent`.

The other eight IDs were not naturally eligible in this slice; this is not a
claim that their producer/consumer paths are unimplemented. All 17 paths are
still covered by the provider-free census and forced-trigger tests.

There were exactly five model-visible guidance deliveries:

| Task | Feature | Eligible call | Delivered before | Timely | Predictive | Characters | Semantic proxy |
|---|---|---:|---:|---|---|---:|---|
| headless-terminal | `newfile_precedent` | 10 | 10 | yes | no | 101 | no match |
| headless-terminal | `signature_delta` | 29 | 29 | yes | no | 261 | stale source |
| portfolio-optimization | `GT_EDIT_CHECK` | 16 | 16 | yes | no | 128 | stale source |
| schemelike-metacircular-eval | `GT_EDIT_CHECK` | 12 | 12 | yes | no | 135 | stale source |
| write-compressor | `newfile_precedent` | 5 | 5 | yes | no | 82 | no match |

All five were present in the first eligible provider request, had concrete
anchors, and were delivered before `model.query()`. There were zero late and
zero predictive deliveries. `semantic_utilization` and anchor-following are
behavioral proxies only; they do not prove model causality.

## Replay-capture audit

The capture switch worked: ten `gt_replay_bundle.json` files were emitted and
each receipt hash matched its adjacent bundle. Eight bundles were complete and
trajectory-replay-ready. Two were bounded-capture incomplete:

| Task | Calls | Missing material | Reason |
|---|---:|---|---|
| gpt2-codegolf | 41 | response bodies | the 25 MB bundle budget was exceeded |
| schemelike-metacircular-eval | 78 | request bodies | the 25 MB bundle budget was exceeded |

These bundles remain useful for deterministic receipt/request-hash inspection,
but they are not complete trajectory replay inputs. All ten have
`model_causal_replay_ready=false` by design: captured provider requests and
responses cannot establish what the same stochastic model would have emitted
under a counterfactual intervention. No model-causal claim is made.

## Resource comparison to frozen GT-off

The local deep-metrics comparison is in
`D:\gt_runs\31325108446\delta\DEEP_DELTA.md` and
`D:\gt_runs\31325108446\delta\deep_delta.json`. Deltas are treatment minus
baseline; negative model-resource deltas are better.

| Aggregate metric | GT-off | GT-on | Delta |
|---|---:|---:|---:|
| Total tokens | 29,223,016 | 17,893,285 | -11,329,731 |
| API calls | 420 | 368 | -52 |
| Assistant steps | 420 | 367 | -53 |
| Model actions | 483 | 378 | -105 |
| Uncached input tokens | 354,433 | 190,967 | -163,466 |

The aggregate reduction is descriptive only. The strict gate failed: nine of
ten solved tasks failed strict per-task Pareto, and `cobol-modernization`
increased by +1,651,739 tokens, +36 calls, +17 model actions, and +36 assistant
steps. `llm-inference-batching-scheduler` also violated the per-task bound;
`portfolio-optimization` increased by +98,333 tokens. The treatment therefore
does not demonstrate reliable efficiency even though the aggregate is lower.

## Interpretation and release decision

What is proven:

1. The host integration enabled all 17 features on all ten tasks.
2. Effects were applied and classified; private engine state is not being
   counted as model-visible guidance.
3. The five visible payloads were grounded, first-eligible, non-late, and
   non-predictive.
4. The capture switch emitted bounded, hash-linked replay artifacts.
5. The official and uncensored solve counts matched the frozen baseline.

What is not proven:

1. GT caused any model action or outcome change.
2. A full counterfactual replay is available for all ten tasks.
3. The treatment is efficient on a per-task or strict Pareto basis.
4. Repository intelligence works for `gpt2-codegolf` in this workflow.

Decision: **REJECTED as an efficiency/89-task gate; implementation integrity
CERTIFIED for this run.** Keep the 89-task run blocked.

## Required next steps

1. Fix the `gpt2-codegolf` graph-runtime substrate and make the readiness gate
   fail before provider use when the certified index is unavailable.
2. Replace the fixed 25 MB replay cap with a compressed or per-call artifact
   layout so every task can be trajectory-replay-ready without changing the
   provider request. This requires a new authorized paid smoke.
3. Isolate the COBOL and batching resource expansions with provider-free replay
   and a separately authorized component-ablation smoke; do not optimize from
   the misleading aggregate token reduction.
4. Re-run the exact provider-free census, readiness audit, pre-smoke gate, and
   archived trajectory audit before any further paid run.
5. Do not dispatch the 89-task benchmark until a treatment is valid for every
   task, preserves uncensored outcomes, and passes the outcome-first efficiency
   gate.
