# GT Harness benchmark integration contract

## `arch_type`: authoritative architecture contract

`arch_type` means the product's complete architecture contract: intended
outcome, canonical runtime path, component boundaries, graph/dense/context
invariants, benchmark adapters, receipts, failure states, language limits, and
verification gates. Read [`arch_type.md`](arch_type.md) before changing the GT
treatment, graph, retrieval, context compiler, Mini-SWE integration, benchmark
workflow, receipt schema, or release gate. A change is complete only when the
implementation, executable product contract, tests, receipts, and `arch_type`
remain consistent.

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

There are no dispatch wrappers. Each suite has one product workflow, and both
`bare` and `groundtruth` treatments enter through that workflow's identical
Mini-SWE-Agent 2.4.6 boundary. `production-surface.toml` is the exact allowlist
for installed modules and dispatchable workflows; an undeclared workflow or
runtime import is a certification failure.

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

For an ACTIVE context-v7 treatment, the union of
`provider_delivery_receipts[].serialized_claim_ids` must exactly equal
`delivered_claim_ids`, every receipt must identify `delivered_before_call`, and
the trajectory may contain at most one GT augmentation for each assistant
provider turn. Every provider-visible fact must bind a typed requirement ID and
role; candidate-only paths do not count as delivered or followed.

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
python -m pytest
python scripts/lint_product_surface.py
go test -tags sqlite_fts5 ./...
python scripts/verify_gt_harness.py --output artifacts/verification/latest
```

If GitHub billing prevents a Codespace from starting, dispatch the registered
`prerelease_product_matrix.yml` workflow at the exact candidate SHA. It invokes
the same `scripts/codespaces_product_certification.sh` Linux campaign and
uploads the complete receipt/log tree under `always()`.

Parse every current workflow with PyYAML, build the Go indexer with
`sqlite_fts5`, provision and checksum the pinned ONNX assets, and run a real
local CLI witness that produces a ready graph, `gt-run.json`, and
`gt-run.trajectory.json`. Only then dispatch the selected suite workflow at an
immutable source SHA.

## Arktype: repository architecture facts

Arktype is GT Harness's source-bound architectural projection, implemented in
`gt_engine.repository_architecture`. It is not a second graph builder and it
must never infer edit authority. It parses bounded static manifests without
executing repository code and emits immutable package, workspace, public
surface, entrypoint, build target, test target, and dependency facts. Each
projection is bound to the graph receipt's exact `source_revision`, a manifest
set digest, and per-manifest hashes. Unsupported dynamic configuration is a
declared limitation.

The treatment caches Arktype only for the matching source revision, selects
task/path-scoped facts, and submits them to the common provider planner. Its
authority is `STRUCTURAL_PROJECTION`: below exact identity and certified
relation/process/impact, above loose semantic and rank-only support.
`ARCHITECTURE_FACT` is inspection context, never an edit instruction. A source
revision change invalidates the cache.
