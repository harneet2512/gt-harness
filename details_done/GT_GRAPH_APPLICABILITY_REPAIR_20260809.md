# Graph applicability repair — 2026-08-09

## Diagnosis

The `gpt2-codegolf` receipt from workflow `31325108446` was reported as an
invalid GT treatment because the workflow merge gate treated every required
non-`passed` repository-intelligence status as a graph failure. That task did
not contain a supported source file: its workspace contained a GPT-2
checkpoint (`.ckpt`), vocabulary data (`.bpe`), and model-generated scratch C
files under `/tmp`, which are outside the task source mirror. The repository
session correctly returned:

- `status=no_supported_source`;
- `applicability=not_applicable_no_supported_source`;
- `denominator_excluded=true`;
- zero graph nodes and edges;
- no graph facts delivered.

This was a classification/gate bug, not a missing parser. Creating graph nodes
for model artifacts or `/tmp` scratch programs would fabricate repository
intelligence and violate the source-revision contract.

## Repair

`MiniSweCentralAgent` now treats `not_applicable_no_supported_source` as a
valid deterministic abstention:

1. It does not activate the graph gate for a source-less task.
2. It does not mark the task as degraded fallback.
3. It does not require repository frontier delivery.
4. It records no repository-intelligence failure for that task.
5. Source-backed tasks with missing, stale, schema-invalid, incomplete, or
   unavailable graphs still fail closed exactly as before.

The workflow merge gate now reads the receipt's
`repository_intelligence_denominator_excluded` flag and excludes only the
explicit no-source case. It still rejects any source-backed task with an
invalid graph or degraded fallback.

## Verification

Passed locally:

```text
python -m ruff check eval/gt_central_agent.py tests/test_gt_central_agent.py tests/test_gt_repository_intelligence.py
python -m compileall -q eval gt_engine scripts
python -m pytest -q tests/test_gt_central_agent.py                 # 59 passed
python -m pytest -q tests/test_gt_central_agent.py -k 'source_less_task or task_graph_failure or strict_graph_gate'
python -m pytest -q tests/test_gt_repository_intelligence.py -k 'source_less or source_backed_graph_failure'
workflow YAML parse                                             # OK
```

The new tests prove both sides of the boundary: source-less tasks are
denominator-excluded and quiet, while a source-backed unavailable index still
produces hard graph-gate failures. The existing paid smoke remains historical
rejected evidence; no paid rerun was started by this repair.

Provider-free GitHub certification `31328363512` on commit `9c69f00` passed
the complete central test suite, readiness audit, all-17 census, strict agent
lifecycle gate, and printed `SMOKE_APPROVED`. This certifies the repair without
spending on another task-container run.
