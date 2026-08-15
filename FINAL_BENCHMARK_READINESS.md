# GroundTruth final benchmark readiness

## Artifact inventory

### Authoritative raw stores

| Store | Contents | Authority |
|---|---|---|
| `C:\Users\Lenovo\Downloads\gt-off-baseline deepseeknew\` | frozen TB2.0 GT-off 89-task merged result, 89 trajectories, token table, config metadata | High for that historical rollout |
| `D:\gt_runs\31355487270\` | current-generation `certified_full` 89-task raw results, receipts, trajectories, metrics, replay | High; treatment invalid due 11 graph failures |
| `D:\gt_runs\31421610097\` | 20-task regression mix, exact replay bundles for all tasks, results/receipts | High; commit `18720f2`, pre-HEAD repairs |
| `D:\gt_runs\30924339091` through `31352963297` | central-runtime smoke generations and receipts | High for individual observed runs; comparison validity varies |
| `D:\gt_runs\30960106490\` | partial 73-task rewards/trajectories and all-17 generation artifacts | Engineering evidence; not a complete 89-task result |
| `C:\Users\Lenovo\Downloads\GT_METRICS_COMPARE_20260721\gold\swebench_live_lite.jsonl` | 300 SWE-bench-Live Lite rows including gold patch/test fields | Dataset evidence; not a current executable GT adapter |
| `D:\Groundtruth\deepswe-bench\` | 113-task DeepSWE checkout plus old GT integration files | Dataset/legacy adapter evidence; not central runtime |
| GitHub Actions run artifacts | task configs, result.json, agent receipts, verifier output | High where downloaded and hash-checked |

Exact current replay bundles contain `manifest.json`, `calls.jsonl`, and gzip-compressed request/response blobs. They prove transport and allow deterministic trajectory reconstruction. They cannot recreate a provider's temperature-1 sample because no provider sampling/checkpoint state is available.

## Historical experiment table

Historical generation names are approximate labels from project history. Run metadata and code paths take precedence.

| Experiment | Architecture / dataset | Model/harness | Baseline -> GT | Valid comparison? | Main finding |
|---|---|---|---:|---|---|
| V2 / early proactive | broad task-start context, small Terminal subsets | changing nano templates/models | e.g. 1/5 -> 3/5 (`30599727385`) | No | descriptive gain confounded by prompt/thinking/agent changes |
| v3.x passive | repeated capsules | old nano + GT | 1/5 -> 1/5 (`30603432233`) | Partly engineering-only | +171% input demonstrated repetition overhead |
| v4.x active tools/files | model-selected GT tool/file access | old nano/bridge | sparse tool consumption | No clean A/B | discoverability and extra-turn cost, not automatic context |
| v5 validation/autocorrect | post-edit checks plus rewrite/KB mechanisms | old engine | mixed | No | correct failures occasionally useful; rewrite precision/ownership unsafe |
| v6 obligation/runtime KB | task contract, recovery, submit controls | old engine | 7/9 -> 3/9 (`30615578051`) | No | exposed incomplete obligations, refresh, validation, timeout defects |
| Phase 1 inline ENGINE | in-process post-action timing | Mini-SWE | `30736459512` | Mechanism-only | delivery timing worked but zero deterministic facts |
| Phase 2A central all-17 | central runtime, bounded guidance | Mini-SWE central | `30954660207`: historical 9/10 | No causal control | delivery/integrity plus large descriptive resource reduction |
| Phase 2B all-17 census | central runtime receipts | Mini-SWE central | `30976148466`: 9/10 | No | 15/17 fired, 361 applied, 36 visible; counts clarified |
| Context compiler | exact accounting/shadow preflight | Mini-SWE central | `31061665540`: 9/10 | No | transport clean; normalized tokens +13.33% |
| Outcome preservation | compiler/timeout fixes | Mini-SWE central | `31068690296`: reward 9/10, uncensored 8/10 | No | rewarded outer-timeout task is not a preserved solve |
| Regression exposure | bundled central controllers | Mini-SWE central | 9/10 -> 7/10 (`31078501162`) | No | deliverable, progress, compaction, request-budget defects |
| Graph-degraded smoke | graph unavailable | Mini-SWE central | `31136099371`: 10/10 | Invalid treatment | outcome alone cannot validate missing repository substrate |
| Efficiency-neutral smoke | central full | Mini-SWE central | 9/10 -> 9/10 (`31145623534`) | No | +0.38% tokens and +55 calls on common solves |
| Conservative full smoke | central full | Mini-SWE central | 9/10 -> 8/10 (`31282615178`) | No | common solves +24.54% tokens, +26.82% calls/steps, +22.74% actions |
| Later repaired smoke | central full | Mini-SWE central | 9/10 (`31352963297`) | Descriptive only | strong common-solve savings in one rollout |
| Current-generation full89 | TB2.0 `certified_full` | DeepSeek V4 Flash; central wrapper | 66/89 -> 60/89 (`31355487270`) | **No** | 4 gains, 10 losses, 11 graph-invalid; treatment rejected |
| Current regression mix20 | TB2.0 `certified_full` | same model alias; central wrapper vs frozen stock baseline | 17/20 -> 15/20 (`31421610097`) | **No** | exact replay exposed false progress and cwd graph-transfer bugs |
| Current HEAD | provider-free only | local Mini-SWE 2.3 / workflow 2.2.8 | no outcome | n/a | all tests/gates pass; no post-repair paid result |

### Evidence classification

- **Scientifically usable today:** deterministic unit/integration/fixture proof; exact request/response and receipt integrity; within-run descriptive outcomes; official verifier rewards for each individual run.
- **Useful engineering observations:** concrete graph failures, false progress frames, wrong typing, context overflow, timeouts, sparse/low-value payloads.
- **Invalid/confounded treatment estimates:** every historical score comparison for current GT. None combines current HEAD, same central wrapper, context-only arm, immutable model metadata, valid graph substrate, and contemporaneous control.

## Regression taxonomy from trajectories

“Model use” is reported as `UNIDENTIFIABLE` unless exact counterfactual model state exists. Later matching actions are behavioral proxies only.

### Five historical gains

| Task/run | Useful evidence | GT had/retrieved/delivered | Model use | Classification |
|---|---|---|---|---|
| `count-dataset-tokens`, `31355487270` | runtime/package/data inspection, not repository graph | no applicable source; 0 GT chars | UNIDENTIFIABLE; first request matched baseline class | `NOT_A_CONTEXT_PROBLEM` / sampled or controller/harness difference |
| `largest-eigenval`, `31355487270` | numerical algorithm behavior, implementation/test anchors | delivered issue-named definition plus later precedent; not decisive math insight | UNIDENTIFIABLE; next action did not follow precedent | correct low-value evidence; gain not attributable |
| `protein-assembly`, `31355487270` | domain algorithm/runtime evidence | repository non-applicable; 0 guidance/frontier | UNIDENTIFIABLE | `NOT_A_CONTEXT_PROBLEM` |
| `torch-pipeline-parallelism`, `31355487270` | distributed tensor-shape contract and executable test | repository non-applicable at start; 0 context in this run | UNIDENTIFIABLE | `NOT_A_CONTEXT_PROBLEM` |
| `make-mips-interpreter`, partial `30960106490` | ISA semantics and failing instruction witness | two repeated `GT_EDIT_CHECK` messages naming `node vm.js`; no certified graph frontier | UNIDENTIFIABLE | weak/redundant evidence; partial-run gain not creditable |

### Five historical losses

| Task/run | Useful evidence | GT had/retrieved/delivered | Model use | First meaningful failure |
|---|---|---|---|---|
| `extract-elf`, `31355487270`/`31421610097` | alternate-ELF semantic verifier/input properties | zero feature/frontier in full89; later smoke delivered false progress | UNIDENTIFIABLE; trajectory diverged before GT text | `NOT_A_CONTEXT_PROBLEM`, then `WRONG_EVIDENCE` progress |
| `prove-plus-comm`, `31355487270` | exact Coq file/goal and proof context | graph transfer failed; 44/44 actions failed in full89 | no useful payload to use | `INDEX_MISS` + `HARNESS_FAILURE` |
| `regex-chess`, `31355487270` | chess regex semantics and minimal counterexample | 4 feature frames + 1 frontier, mainly generated-file signature/precedent; 1,200 GT chars | UNIDENTIFIABLE; step cap reached | `RANKING_MISS` / `WRONG_EVIDENCE` / `STEP_LIMIT` |
| `sanitize-git-repo`, full89/mix20 | preserved-object invariant and correct repo root | graph transfer failed; later false `STALLED` progress | UNIDENTIFIABLE; first action diverged pre-GT | `INDEX_MISS` + `WRONG_TIMING/WRONG_EVIDENCE` controller state |
| `video-processing`, `31421610097` | alternate-video scale/resource contract and executable check | no feature guidance; provider-visible false progress | UNIDENTIFIABLE; first action diverged pre-GT | `VALIDATION_MISS` + false controller evidence |

### Five both-fail cases

| Task, full89 | Useful evidence | GT had/retrieved/delivered | Model use | Classification |
|---|---|---|---|---|
| `gpt2-codegolf` | checkpoint-format/runtime implementation evidence | graph non-applicable; 0 visible GT | UNIDENTIFIABLE | `NOT_A_CONTEXT_PROBLEM` / task complexity |
| `caffe-cifar-10` | environment/framework/data behavior | graph non-applicable; only 138 progress chars | UNIDENTIFIABLE | `NOT_A_CONTEXT_PROBLEM` / runtime setup |
| `chess-best-move` | image/board recognition and engine behavior | no source-backed context; 0 GT chars | UNIDENTIFIABLE | `NOT_A_CONTEXT_PROBLEM` |
| `configure-git-webserver` | Git/webserver operational configuration | no source-backed context; 0 GT chars | UNIDENTIFIABLE | `NOT_A_CONTEXT_PROBLEM` |
| `db-wal-recovery` | SQLite WAL/file-state invariants and recovery validation | one 176-char guidance item, no frontier | UNIDENTIFIABLE | likely runtime/data-recovery problem; exact attribution unknown |

This taxonomy does not support “LLM variance” as a universal explanation. It separates pre-GT sampling divergence, real GT defects, graph substrate failures, low-value ranking, missing executable semantics, task complexity, and experimental confounds.

## Target-suite readiness

### A. Agent Retrieval Bench V2

Official V2 has 427 samples: 106 `code2test`, 80 `comment2context`, 101 `trace2code`, 58 `edit2ripple`, and 82 no-gold cases across 25 repositories. It reports MRR, Recall@20, BCY@8k, and selective success. Official baselines include RepoMap MRR 0.2158, Recall@20 0.6333, BCY@8k 0.3788; Qwen3-8B reaches Recall@20 0.7029. Source: [official site](https://agent-retrieval-bench.github.io/), [repository](https://github.com/eyuansu62/agent-retrieval-bench).

- Dataset downloaded locally: **No**.
- Adapter: **No**.
- Expected input: frozen base-commit corpus plus workflow-specific query/given/anchored files.
- Required GT mapping: construct a `TaskContract`/query state for each subset, index corpus at exact commit, return ranked repository-relative files or abstain. Never expose gold fields.
- Evaluator: official CLI exists, not installed/integrated here.
- Estimated engineering: 4–8 focused hours.
- Estimated runtime: 2–8 CPU hours after corpus download/indexing; no model API cost. Low-confidence until corpus cache size/index throughput is measured.
- Ready today: **NO**.

### B. SWE-bench-Live Lite

The frozen local JSONL has exactly 300 rows and includes `instance_id`, repo/base commit, problem statement, tests, and gold patch fields. The official Microsoft repository keeps `lite`/`verified` frozen and recommends its Python-only evaluation path for the Python dataset: [official repository](https://github.com/microsoft/SWE-bench-Live).

- Dataset metadata: **Yes**, local 300-row JSONL.
- Containers/images verified for current machine/workflow: **No**.
- Current Mini-SWE central adapter: **No**.
- Existing workflow: `swe_gt.yml` is SWE-bench Verified plus legacy `GTNanoSweAgent`, not SWE-bench-Live central GT.
- Baseline/context arms: **No executable matched pair**.
- Evaluator: old checkout exists under `D:\Groundtruth\SWE-bench-Live`, but is not wired/tested here.
- Estimated engineering: 8–16 hours before a smoke.
- Estimated runtime: 300 tasks / parallel 20; task timeout can be 3,000 s, giving a 12.5-hour worst-case agent envelope per arm before setup/evaluation. Real time unknown.
- Estimated cost: unknown because the gateway price is absent and artifacts record `$0`; calculate from provider rates and captured uncached/cache/output tokens before dispatch.
- Ready today: **NO**.

### C. DeepSWE v1.1

Official DeepSWE has 113 original long-horizon tasks across 91 repositories and TypeScript, Go, Python, JavaScript, and Rust. It uses Harbor-format tasks but Pier >=0.3.0 for separate verifier environments. Official quickstart can run `mini-swe-agent`: [official repository](https://github.com/datacurve-ai/deep-swe), [paper](https://arxiv.org/abs/2607.07946).

- Dataset checkout: **Yes**. The workflow pins immutable commit
  `435ee89ec2f2e2289f33b0da4f992f0b7b7266b9` and proves its 113 task IDs match
  the public v1.1 catalog; upstream publishes no v1.1 Git tag or digest.
- Current API accessibility: public GitHub/Pier.
- Central agent adapter: **Yes**. `.github/workflows/deepswe_miniswe_central.yml`
  invokes `eval.gt_central_agent:MiniSweCentralAgent` through
  `datacurve-pier==0.3.1`.
- GT compatibility: implemented for source-backed repositories through the
  current graph/retrieval/runtime contract.
- Grader: the workflow runs `verifier.collect` commit-to-patch handling and
  grades in Pier's separate pristine verifier environment.
- Estimated engineering: exact-commit certification and matched-control
  production remain; no new Pier adapter is required.
- Estimated benchmark runtime: long-horizon and model-dependent. The public
  v1.1 page currently includes configurations up to roughly 268 average steps,
  so the older 61–102 range is not a valid planning ceiling.
- Estimated cost: model-dependent and not inferable from local `$0` artifacts. Official leaderboard costs are for other model/effort combinations and must not be substituted.
- Ready today: **NO** for paid comparison until this repaired exact commit
  passes the source-built provider-free workflow and a matched uncensored
  GT-off artifact passes the full identity gate. The executable adapter exists.

### D. Terminal-Bench 2.0 product diagnostic

The frozen product contract uses Terminal-Bench 2.0 with Mini-SWE. Official
2.1 changes 28 of the 89 tasks, so versions are not interchangeable. A TB2.0
result is diagnostic product evidence, not a current TB2.1 leaderboard claim.

- Harbor integration: **Yes for the frozen 2.0 contract**.
- 89-task availability: pinned by the checked-in language/task contract.
- Agent adapter: central adapter exists.
- GT applicability: mixed; must report all 89 plus source-applicable denominator and substrate failures.
- Required trials: one matched same-wrapper OFF arm and one frozen GT-on arm
  for the product diagnostic; no leaderboard-grade claim.
- Estimated engineering: exact-commit re-certification and workflow audit; no
  2.1 port belongs to this benchmark phase.
- Estimated runtime: the last 89-task TB2.0 workflow occupied roughly five
  hours at parallel 20; budget 5–7 hours per arm until measured.
- Token scale: historical off used ~242.5M prompt+output tokens; full89 treatment used ~218.8M, but outcome differences confound that delta. An A/B may therefore expose roughly 0.4–0.5B total cached+uncached+output token accounting.
- Estimated dollars: **unknown** without the private gateway's cache/input/output price schedule.
- Ready today: **NO**.

## Gap matrix

| Requirement | Retrieval Bench | SWE-Live Lite | DeepSWE | Terminal-Bench 2.0 |
|---|---|---|---|---|
| Dataset ready | No local V2 | 300-row metadata only | External checkout exists | Remote official, current config 2.0 |
| Harness ready | Yes, provider-free | No current path | Yes, pending exact-SHA gate | Yes, 2.0 diagnostic |
| Agent adapter | Yes | Legacy nano only | Yes, Mini-SWE/Pier | Yes |
| GT integration | Shared production retriever | Central runtime not wired | Current central runtime | Current central runtime |
| Baseline mode | Official retriever baselines | No matched pair | Exact artifact gate; producer required if artifact fails | Same-wrapper off arm exists |
| Evaluator | ARB evaluator implemented | Old external checkout | Official Pier verifier | Harbor 2.0 verifier |
| Instrumentation | Complete ranking/delivery views | Central receipts needed | Receipt/result bridge implemented | Strong existing receipts |
| Reproducibility | Pinned corpus/model artifacts | No current manifest | Pinned task commit/runner; exact control pending | Pinned 2.0 diagnostic contract |
| Estimated engineering | complete | 8–16 h | certification/control only | certification only |
| Estimated runtime | 2–8 CPU h | <=12.5 h/arm envelope | 6–12+ h/arm | 5–7 h/arm |
| Ready today? | **NO** | **NO** | **NO** | **NO** |

## Overall readiness judgment

The retrieval adapter and DeepSWE/Pier execution path now exist. The immediate
blockers are exact-commit provider-free certification and a schema-, identity-,
and outcome-valid matched DeepSWE control. Terminal-Bench 2.0 remains the next
product diagnostic; it must never be relabeled as TB2.1. SWE-bench-Live still
lacks a current same-wrapper path.
