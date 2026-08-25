# GT Harness benchmark integration contract

This repository has one current benchmark product: **GT Harness 0.9.0 running
Mini-SWE-Agent 2.4.6 through `gt-harness run`**. Benchmark runners may provision
repositories and grade patches differently, but they must not replace, patch,
or reimplement the GT treatment or the Mini-SWE agent loop.

The machine-readable source of truth is
`eval/benchmark_product_contract.json`. If this file and a workflow disagree,
stop before provider spend and fix the workflow.

## Current benchmark entrypoints

| Suite | Current workflow | Orchestration adapter |
| --- | --- | --- |
| terminal-bench-2 | `tb2_miniswe_product.yml` | `eval.harbor_gt_harness_adapter:GtHarnessMiniSwe246Agent` |
| deepswe | `deepswe_gt_harness_product.yml` | `eval.pier_gt_harness_adapter:PierGtHarnessMiniSwe246Agent` |
| swe-live-lite | `swe_live_lite_gt_harness_product.yml` | `eval.swe_live_lite_gt_harness_adapter` |

DeepSWE uses the registered dispatch wrapper
`deepswe_miniswe_central.yml`, which may only call the current DeepSWE product
workflow. The word `central` in that filename is historical GitHub registration,
not a permitted runtime architecture.

## Runtime integration

Every task must install the checked-out GT Harness source plus exactly
`mini-swe-agent==2.4.6`. It must provision the source-built static `gt-index`
binary and the checksum-pinned Snowflake ONNX model before the model is called.

The task environment must contain:

```text
OPENAI_API_KEY=<runner secret>
OPENAI_BASE_URL=https://openrouter.ai/api/v1
GT_INDEX_BINARY=<in-container static gt-index path>
GT_DENSE_MODEL_DIR=<in-container model directory>
GT_RETRIEVAL_MODE=hybrid_required
```

The runner must supply the real issue text, exact task ID, exact 40-character
GT product source SHA, task-derived time budget, repository root, private state
directory, and receipt output. The effective product call is:

```text
gt-harness run <real issue text> \
  --model openai/stealth/ox-alpha \
  --base-url https://openrouter.ai/api/v1 \
  --temperature 1.0 \
  --max-iterations 300 \
  --time-budget-seconds <task-derived budget> \
  --treatment groundtruth \
  --root <actual checked-out task repository> \
  --state-dir <private persistent task state> \
  --task-id <official task id> \
  --trial-id 1 \
  --output <artifact root>/gt-run.json
```

Harbor and Pier call this through their installed-agent protocols. SWE-Live
Lite invokes it inside the immutable official task image through the direct
adapter. Adapter code may translate runner APIs, install files, and preserve
receipts; it may not generate benchmark-specific context or alter agent
reasoning.

## Graph and delivery gate

Before any graph-derived bytes reach Mini-SWE, the product must prove:

```text
current_repo_SHA == graph_repo_SHA
AND graph_status in {READY, READY_WITH_DECLARED_LIMITATIONS}
AND query_ready == true
AND, for an ACTIVE hybrid treatment, dense_index.query_ready == true
```

A stale, partial, corrupt, wrong-revision, or query-ineligible graph must never
be described as ready. A genuinely unsupported repository may emit an explicit
`NOT_APPLICABLE` treatment and no graph context. Any other inability to satisfy
the gate is a product failure, not a normal benchmark loss.

## Required per-task evidence

No task counts as a GT treatment unless its uploaded artifacts contain:

```text
benchmark-adapter.json
gt-run.json
gt-run.trajectory.json
official-verifier-result.json
```

The adapter receipt must bind the suite, task, attempt, product source SHA,
Mini-SWE version, exact model route, time budget, and `gt-harness run` command.
The GT run receipt must be terminal and contain a treatment receipt. An ACTIVE
treatment must prove graph readiness, exact repository identity, hybrid dense
readiness, delivery timing, and delivery token bounds. The trajectory must
contain the exact observations delivered to the model and its provider-call
accounting must match `gt-run.json`. The official verifier result must bind the
same task and patch.

For an ACTIVE context-v6 treatment, the union of
`provider_delivery_receipts[].serialized_claim_ids` must exactly equal
`delivered_claim_ids`, every receipt must identify `delivered_before_call`, and
the trajectory may contain at most one GT augmentation for each assistant
provider turn. Candidate-only paths do not count as delivered or followed.

The suite attestation must fail when any expected task, receipt, trajectory,
official reward, or source identity is absent. Upload artifacts under `always()`
even when the task or product fails so failures remain diagnosable.

## Comparison and release rules

Compare GT only against a baseline containing the identical official task IDs,
repository revisions, model route, scaffold version, reasoning settings,
budgets, attempts, and verifier. If an historical baseline uses a different
Mini-SWE version, label the comparison directional rather than causal. Always
report solved tasks, regressions, positive flips, provider calls, input/output/
cached tokens, wall time, graph build time, and treatment delivery status.

Read every trajectory in a smoke run. A solved task does not prove GT helped,
and an unresolved task does not prove GT caused the failure. Verify that each
delivered fact is real, relevant to the next decision, compact, and consumed or
ignored in the recorded trajectory.

## Forbidden production paths

- Do not use Nano. Mini-SWE-Agent 2.4.6 is the only current scaffold.
- Do not use MCP as the benchmark treatment boundary.
- Do not use `gt_mini_patch.py` or `gt_headless_runner.py`.
- Do not use `eval.gt_central_agent` or `eval.pier_gt_adapter`.
- Do not inject benchmark-specific hints, reference patches, tests, or expected
  files into GT context.
- Do not treat a benchmark script, mock graph, cached brief, or precomputed
  repository packet as the GT Harness product.

Historical code may remain only under an explicitly non-runnable legacy or
research location with a disposition ledger. It must not appear as a dispatchable
GitHub Actions workflow or a current integration example.

## Pre-dispatch verification

Before any paid smoke, run provider-free checks in the GitHub Codespace:

```text
pytest -q tests/test_generalized_benchmark_product.py
pytest -q tests/test_harbor_gt_harness_product.py tests/test_deepswe_gt_harness_product.py
pytest -q -m "not external_evidence" --ignore=tests/test_gt_finalstand.py
```

Parse every current workflow with PyYAML, build the Go indexer with
`sqlite_fts5`, provision and checksum the pinned ONNX assets, and run a real
local CLI witness that produces a ready graph, `gt-run.json`, and
`gt-run.trajectory.json`. Only then dispatch the selected suite workflow at an
immutable source SHA.
