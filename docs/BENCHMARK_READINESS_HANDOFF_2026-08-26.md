# Benchmark-readiness implementation handoff — 2026-08-26

Status: **IMPLEMENTATION COMPLETE; EXACT-SHA LINUX CERTIFICATION PENDING**.
No paid benchmark was run. Historical smoke results are diagnostic evidence,
not certification of this revision.

## Product identity

GT Harness 0.9.0 is a model-agnostic repository-intelligence treatment for
Mini-SWE-Agent 2.4.6. It builds an exact-revision structural graph and dense
index, compiles task requirements into bounded deterministic repository facts,
delivers those facts on the provider-visible action loop, preserves the exact
trajectory, and binds official verifier outcomes for paired analysis.

The authoritative boundaries are:

- installed command: `gt-harness`;
- exact source/workflow allowlist: `production-surface.toml`;
- architecture: `arch_type.md`;
- experiment contract: `eval/benchmark_product_contract.json`;
- Linux campaign: `scripts/codespaces_product_certification.sh`.

## Corrections in this candidate

| Boundary | Implemented correction | Fail-closed proof |
| --- | --- | --- |
| Repository identity and persistence | Graph receipt v5; immutable generation directories; atomic `CURRENT`; durable build-attempt record; generation, manifest, graph, builder, commit, and source-revision revalidation | tamper, stale-revision, interrupted-publication, cold/warm, and real-CLI lifecycle tests |
| Graph builder ownership | Canonical indexer invokes the source-certified Go binary directly; no runtime import from legacy `groundtruth` | production-surface reachability and wheel-equality checks |
| Task understanding | Context v7 converts directives into typed requirements with intent, entity, resolution, coverage, and mechanism | requirement extraction, duplicate-obligation, ambiguity, new-file, and uncovered-requirement tests |
| Localization authority | Edit, inspection, public surface, integration, and validation remain separate; package echo, unscoped collisions, dense-anchor poisoning, and weak path matches cannot manufacture edit authority | non-vacuous compiler/localization regression tests |
| Ambiguity | `AMBIGUOUS_IDENTITY` is delivered as an explicit claim with candidates instead of silently disappearing or becoming an edit target | treatment reconciliation and ambiguity-only delivery tests |
| Delivery | Provider receipt v2 and treatment receipt v4 bind context hash, exact revision, requirement/claim IDs, delivery timing, and token limits | canonical delivery auditor in all three suite attestations |
| Uptake | Followed evidence requires an exact normalized repository path in a later durable Mini-SWE action; hidden reasoning is never inferred | `gt_harness.analysis.uptake` tests |
| Outcomes | Official verifier dispositions and efficiency metrics are typed; invalid delivery cannot enter a normal paired result | comparison/outcome tests |
| Product surface | Wheel contents, runtime imports, console entry point, schemas, budgets, languages, and the exact five workflows are allowlisted | `gt.product_surface_verification.v1` receipt |
| Workflow surface | One workflow per suite, both treatments through Mini-SWE 2.4.6; obsolete central, Nano, baseline-only, split, and diagnostic dispatch files removed | workflow equality, YAML, adapter, and attestation tests |
| User-level verification | Project-local verification skill exercises doctor, cold build, definition query, edit-to-STALE, rebuild-to-new-generation, and cleanup on a real temporary Git repository | `gt.cli_verification.v1` receipt |

Historical source remains recoverable in Git. `src/groundtruth`, old central
agents, Nano paths, and MCP experiments are not installed, imported by the
canonical closure, or dispatchable. This is capability-preserving migration,
not a clone of GitNexus: GT retains its stronger exact-revision graph truth,
receipts, multi-language structural graph, deterministic semantic facts, and
benchmark-neutral delivery while adopting the useful higher-order lesson that
facts must be composed and delivered at a decision point.

## Current evidence

- canonical Python product suite: 495 passed after the final certification
  receipt additions;
- Go indexer: `go test -tags sqlite_fts5 ./...` passed locally;
- built wheel: exact equality with `production-surface.toml`, with no legacy
  module present;
- real CLI verification: PASS; stale status returned nonzero and the rebuild
  published a different immutable generation;
- provider calls during all of the above: 0.

The old localization report (precision 1.0, recall 0.0845) predates context v7
and remains historical evidence. It must not be relabeled. The Linux campaign
must regenerate the fingerprint-bound 20-task report under
`hybrid_required`; the candidate fails if precision, recall, treatment,
dense-readiness, or task-set gates do not pass.

## Remaining release actions

1. Run the full local Python, Ruff, Go, wheel-surface, workflow, and CLI gates.
2. Commit and push the exact candidate SHA to `harneet2512/gt-harness`.
3. Dispatch `prerelease_product_matrix.yml` at that immutable SHA.
4. Inspect every uploaded receipt. If a gate fails, reproduce and correct the
   cause, add regression coverage, create a new SHA, and repeat.
5. Only after the provider-free product bundle certifies may a controlled paid
   smoke be considered. Utility still requires identical bare/GT task,
   revision, model, scaffold, budget, environment, and official verifier.

## Verification commands

```powershell
python -m pytest
python scripts/lint_product_surface.py
go test -tags sqlite_fts5 ./...
python -m build --wheel
python scripts/verify_product_surface.py --wheel <wheel> --output <receipt>
python scripts/verify_gt_harness.py --output artifacts/verification/latest
```

Hosted proof uses only:

```text
.github/workflows/prerelease_product_matrix.yml
  -> scripts/codespaces_product_certification.sh <exact-sha>
```

The current verdict remains `NOT_CERTIFIED` until that exact-SHA Linux receipt
bundle passes. That is evidence discipline, not missing implementation.
