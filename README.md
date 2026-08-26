# GT Harness

GT Harness is a model-agnostic benchmarking product for testing whether
deterministic repository intelligence improves coding-agent outcomes and
efficiency. The prerelease product owns exact-revision graph construction,
hybrid retrieval, requirement-scoped context, provider-visible delivery,
trajectories, official-outcome binding, and paired comparison.

Current status: **implementation complete; exact-SHA Linux certification
pending; paid benchmark not authorized**. Historical green dashboards and
smoke scores are not evidence for the current revision.

## Product guarantees

- One installed command: `gt-harness`.
- One agent scaffold: Mini-SWE-Agent 2.4.6 for both `bare` and `groundtruth`.
- One exact product allowlist: `production-surface.toml`.
- One immutable graph generation selected through atomic `CURRENT` publication.
- No graph-derived delivery unless commit, source revision, builder, schema,
  manifest, checksum, database health, and query readiness all match.
- A required dense index in official `hybrid_required` treatments, using the
  checksum-pinned local Snowflake ONNX model and no provider calls.
- Typed context-v7 requirements and separate edit, inspection, public-surface,
  integration, and validation roles.
- Exact delivery receipts and durable trajectories; hidden reasoning is never
  invented as evidence uptake.
- Official verifier results, not workflow success or self-reported outcomes,
  determine solves.

## Canonical product path

```bash
pip install -e .
gt-harness doctor
gt-harness graph build --root /path/to/repository
gt-harness graph status --root /path/to/repository
gt-harness graph query definition Symbol --root /path/to/repository
gt-harness run "task" --model exact/provider-model --treatment bare
gt-harness run "task" --model exact/provider-model --treatment groundtruth
gt-harness record-harbor-outcomes --harbor-run-dir /path/to/job --output-dir /path/to/evaluated
gt-harness compare --baseline /path/to/bare/evaluated --treatment /path/to/gt/evaluated
gt-harness certify --receipt-dir /path/to/campaign --expected-commit "$(git rev-parse HEAD)"
```

The `groundtruth` treatment fails before provider call 1 when the exact graph,
dense index, or initial decision packet is unavailable. `NOT_APPLICABLE` is
reserved for genuinely unsupported tasks. A nominal GT task with missing or
invalid delivery cannot enter a normal paired comparison.

## Benchmark workflows

| Suite | Workflow | Adapter |
| --- | --- | --- |
| Terminal-Bench 2 | `tb2_miniswe_product.yml` | `eval.harbor_gt_harness_adapter` |
| DeepSWE | `deepswe_gt_harness_product.yml` | `eval.pier_gt_harness_adapter` |
| SWE-bench Live Lite | `swe_live_lite_gt_harness_product.yml` | `eval.swe_live_lite_gt_harness_adapter` |

Each workflow accepts `bare` or `groundtruth` through the same Mini-SWE 2.4.6
surface. The other two dispatchable workflows are provider-free certification
and repository-intelligence audit. Historical central, Nano, MCP, diagnostic,
split, and baseline-only workflows are not dispatchable or installed.

## Verification

```bash
python -m pytest
python scripts/lint_product_surface.py
go test -tags sqlite_fts5 ./...
python scripts/verify_gt_harness.py --output artifacts/verification/latest
```

The real-CLI verifier creates a temporary Git repository, runs doctor, builds
and queries the graph, changes source, proves the old graph becomes `STALE`,
rebuilds to a different immutable generation, and cleans up. The hosted
`prerelease_product_matrix.yml` workflow runs the complete Linux campaign and
retains every receipt even on failure.

## Architecture and evidence

- [Canonical architecture](CANONICAL_ARCHITECTURE.md)
- [Detailed architecture contract](arch_type.md)
- [Benchmark integration contract](AGENTS.md)
- [Current implementation handoff](docs/BENCHMARK_READINESS_HANDOFF_2026-08-26.md)
- [Historical implementation evidence](docs/GT_CONTEXT_V6_IMPLEMENTATION_2026-08-25.md)

GT is not a GitNexus clone. Both products use graph-backed repository
intelligence, but GT's release claim is an auditable benchmark treatment:
exact-revision truth, fail-closed lifecycle, deterministic requirement coverage,
identical bare/GT scaffold control, and official outcome/efficiency analysis.
Competitor ideas are useful only when they improve a repository fact and a
measurable agent decision without weakening those guarantees.

## License

MIT.
