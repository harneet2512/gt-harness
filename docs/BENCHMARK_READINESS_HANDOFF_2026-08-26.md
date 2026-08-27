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
| Crash certification | Lifecycle and failure campaigns observe the durable v5 `build-attempt.json`, isolate the build process group, kill the complete tree, and require a non-queryable terminal state | real interrupted-build and interrupted-update campaigns |
| Decision-point recall | Existing PascalCase repository types used as behavioral subjects seed exact graph retrieval; production definitions outrank test/example homonyms; exact, path, and hybrid owner candidates are selected by bounded facet cover | source-backed regressions plus the exact 500-token provider-boundary replay |

Historical source remains recoverable in Git. `src/groundtruth`, old central
agents, Nano paths, and MCP experiments are not installed, imported by the
canonical closure, or dispatchable. This is capability-preserving migration,
not a clone of GitNexus: GT retains its stronger exact-revision graph truth,
receipts, multi-language structural graph, deterministic semantic facts, and
benchmark-neutral delivery while adopting the useful higher-order lesson that
facts must be composed and delivered at a decision point.

## Current evidence

- canonical Python product suite: 505 collected and passed after the hosted
  failure corrections;
- Go indexer: `go test -tags sqlite_fts5 ./...` passed locally;
- built wheel: exact equality with `production-surface.toml`, with no legacy
  module present;
- real CLI verification: PASS; stale status returned nonzero and the rebuild
  published a different immutable generation;
- provider calls during all of the above: 0.

The first full Linux certification of the preceding SHA was GitHub Actions run
`33013230307`. It was useful precisely because it failed. It exposed four
product-certification defects rather than a provider/model failure:

- both crash campaigns still polled the removed mutable graph receipt instead
  of the v5 build-attempt journal;
- the E2E fixture asked only to inspect a symbol while demanding an exact edit
  target;
- the localization replay omitted the top-level provider-free certification
  fields; and
- three of twenty localization cases exposed one false edit target and three
  incomplete owner sets at the real 500-token delivery boundary.

The candidate now fixes those causes. Local real-runtime proofs passed for an
interrupted build, an interrupted stale-revision update, and the complete
Mini-SWE-Agent 2.4.6 prepare/deliver/update/restart path. A source-backed replay
of the three failing localization cases now gives exact-edit precision 1.0 and
required-path coverage 1.0 for each case within the 500-token ceiling. These
focused results are not substituted for the full twenty-case Linux receipt;
the new exact-SHA hosted run remains the authority.

The old localization report (precision 1.0, recall 0.0845) predates context v7
and remains historical evidence. It must not be relabeled. The Linux campaign
must regenerate the fingerprint-bound 20-task report under
`hybrid_required`; the candidate fails if precision, recall, treatment,
dense-readiness, or task-set gates do not pass.

### Exact-SHA run 33024039628

Run `33024039628` audited commit `bde7fe1537393c5f01c6e731ccc192cff9e797e3`.
It proved the crash and E2E repairs on hosted Linux: graph lifecycle, language
lifecycle, real Mini-SWE-Agent 2.4.6 E2E, and the failure campaign all passed.
The run remained `NOT_CERTIFIED` because localization delivery failed with
mean exact-edit precision 0.9, mean required-fact coverage 0.75, and mean
ambiguity recall 0.5. One task had false edit authority and five tasks had zero
required-fact coverage.

Receipt inspection found two causes:

- capitalized grammatical subject `Handle` was allowed to case-fold onto a
  lowercase repository function `handle`, granting a false Boa edit target;
- owner selection and emergency compaction allowed broad facet count, graph
  degree, package echo, or an unrelated ambiguity to displace a more directly
  named implementation owner.

The next candidate requires exact-case behavioral subjects and word-bounded
edit matching. It ranks owner identities by how completely their own path and
symbol are named by the task, demotes package echo, retains a bounded internal
pool of 24 candidates before selecting three, and preserves a scoped owner at
the 500-token emergency floor ahead of unrelated ambiguity/rank noise.

A provider-free replay against the six affected real graph databases now
shows required-fact coverage 1.0 and zero false edit authority in every case.
The first owners are `Lexer.js`, `script.rs`, `array.ts`, `linter.go`,
`selectors.rs`, and `multiAgentChat.ts`, respectively. This is focused local
evidence; a fresh exact-SHA Linux twenty-case receipt is still required.

### Exact-SHA run 33031285044

Run `33031285044` audited commit
`0430310dfc5ff7d1a652651bb1588b67bb46f15a`. Every product gate except the
localization threshold and its downstream certifier passed, including the
real repository matrix, graph truth, graph lifecycle, language lifecycle,
dense model, real Mini-SWE-Agent E2E, and failure campaign. Localization
improved to exact-edit precision 1.0, required-fact coverage 0.9, ambiguity
recall 1.0, and implementation-role precision 0.6562. No treatment failed,
no dense index was unavailable, and no false edit authority remained.

Two tasks remained below the per-task half-coverage floor. Receipt inspection
showed two general product defects:

- KaTeX's exact graph contained `src/environments/array.ts`, but a single
  fused retrieval window could omit that literal task/path owner before the
  compiler saw it.
- Bandit's acceptable injection owners reached the compiled packet, but
  package/directory words and broad facet count caused the 500-token delivery
  to retain a less decision-relevant plugin.

The next candidate adds a bounded per-term path-identity augmentation over
the existing graph FTS index. It cannot grant edit authority: it only ensures
that task-named repository paths reach the typed compiler. Owner ranking now
uses symbol/file identity rather than shared repository directories, recognizes
leaf-plus-parent scope for common-noun modules, and uses uncovered obligation
coverage to break equal-identity ties. A fresh exact-revision replay of both
failed repositories now delivers `src/environments/array.ts` and
`bandit/plugins/injection_sql.py` at the provider boundary, with coverage 1.0,
zero false edit authority, and 474/223 tokens respectively. The next hosted
exact-SHA run remains the certification authority.

## Remaining release actions

1. Commit and push the locally verified candidate SHA to
   `harneet2512/gt-harness`.
2. Dispatch `prerelease_product_matrix.yml` at that immutable SHA.
3. Inspect every uploaded receipt. If a gate fails, reproduce and correct the
   cause, add regression coverage, create a new SHA, and repeat.
4. Only after the provider-free product bundle certifies may a controlled paid
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
