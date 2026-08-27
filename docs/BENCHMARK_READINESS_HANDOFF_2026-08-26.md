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

### Exact-SHA run 33037863387

Run `33037863387` audited commit
`1b68176fd43b7d749f1f502c519636208888e8a6`. All non-localization product
gates passed. Localization produced exact-edit precision 1.0, mean
required-fact coverage 0.85, ambiguity recall 1.0, and implementation-role
precision 0.6333. No treatment failed, dense retrieval was ready for every
task, and no task received false edit authority.

Three tasks fell below half required coverage. Boa received
`core/engine/src/builtins/error/eval.rs` instead of an accepted engine owner;
fd received `src/filter/size.rs` instead of a CLI/configuration owner; Claude
received `backend/handlers/agentConversations.ts` ahead of
`backend/handlers/multiAgentChat.ts`. The path fallback had treated distant
task words as though they named one scoped path, and fallback rows could tie
or outrank exact/retrieved implementation evidence.

The corrective candidate closes the complete boundary. Explicit node IDs are
materialized before path-only rows at the document limit. Module fallback now
requires a locally named leaf/parent scope or all components of a compound
filename; unrelated issue words cannot be assembled into ownership. Exact
non-edit symbols become implementation owners only when a task facet names
them as owners, so the argument `handle` cannot bind an unrelated function.
Direct evidence also precedes fallback evidence at equal identity affinity.

Warm production-path replays preserve KaTeX and Bandit at mean required
coverage 1.0 and recover Boa, fd, and Claude at mean required coverage 1.0;
both cohorts have zero false edit authority, treatment failures, or dense
failures. The full Python suite, Go SQLite-FTS5 suite, changed-file Ruff,
product lint, built-wheel verification, and CLI lifecycle pass locally. Full
exact-SHA Linux certification remains the authority.

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

## 2026-08-27 implementation closure

This candidate closes four defects found by the P-stack architecture,
capability, blast-radius, root-cause, and verification passes:

1. A single deterministic provider planner now preserves distinct obligation
   owners and high-value decision roles, validates edit authority, prices the
   serialized output, and receipts every omission. The second hidden compactor
   and initial role slices are gone.
2. Repository architecture is production-reachable. One bounded manifest scan
   projects package, workspace, public, entry, build, test, and dependency
   facts; task-scoped delivery is bound to the exact source revision and
   manifest hashes.
3. Harbor no longer uploads `src/groundtruth`, the package root no longer
   exports legacy fail-open `create_bridge`, SWE-Live addresses
   `vendor/gt-index-src`, and certification rejects nonexistent workflow-local
   paths before dispatch.
4. Certification no longer rewards abstention. Defaults require exact-edit
   precision 1.0, mean coverage 0.90, per-task coverage 0.50, ambiguity recall
   1.0, implementation precision 0.80, and implementation recall 0.85 when the
   corresponding roles are present. The AST anti-leak scan and generic
   metamorphic tests guard against benchmark-specific learning.

Local evidence before the final commit: 56 architecture/treatment tests and 86
combined planner, product-surface, workflow, and generalization tests pass.
Product lint, Ruff, runtime leak scan, wheel build, and wheel-content checks
pass. A full-suite run exposed one test-order pollution defect; its focused
30-test ordering reproduction is green after correction and the full suite is
being rerun.

The historical localization report is not relabeled. Benchmark readiness
remains `NOT_CERTIFIED` until a fresh exact-SHA hosted Linux
`hybrid_required` smoke20 receipt passes the strengthened gates. No paid agent
benchmark is authorized by these implementation changes alone.

## Exact-SHA run 33054007515 and provider-boundary correction

Run `33054007515` tested commit
`808172e4813fa173e0a9b825db808a5edf259d13`. Install, doctor, Python, Go,
product surface, CLI, real-repository matrix, graph truth, graph lifecycle,
language lifecycle, dense model, localization execution, harness E2E, and the
failure campaign all passed. The release gate correctly failed on the
provider-visible localization result: compiled required-fact coverage was
0.90, but delivery coverage was 0.40. Exact-edit precision fell from 0.8704 in
the complete compiler packet to 0.50 at the provider boundary.

The failure was not graph absence and not model behavior. The provider planner
discarded the compiler's stable relevance order and used serialized row cost
as its last tie-break. A cheaper lower-ranked claim could replace the first
ranked exact identity for the same obligation. Requirement-count priority also
collapsed distinct edit, owner, public, and integration responsibilities.

The corrective candidate now carries the compiler rank into the input-order-
invariant plan, preserves role diversity, and records the selected plan in the
20-task audit receipt. Dense retrieval also carries the exact task-obligation
IDs that produced each top hit. Those hits may become typed implementation
inspection candidates, but never edit authority. Hybrid consensus now ranks
before bare path overlap, and the compiler retains a bounded candidate pool so
the provider planner, rather than an early three-row slice, makes the final
budget decision.

The verifier now distinguishes two different quantities. Implementation fact
recall asks whether at least one independently accepted path for each required
owner fact reached the agent. Implementation path recall reports how much of
the complete alternative/change-surface set was delivered. The latter remains
diagnostic because forcing every acceptable alternative into a 500-token
packet would reward context flooding and contradict the oracle's any-path fact
semantics. Precision, mean coverage, and the per-task floor are unchanged.

Local proof for this candidate: the complete Python suite passes with one
declared missing-Pier skip; the focused compiler, treatment, planner,
localization, and generalization suite passes; changed-file Ruff, product lint,
wheel build, and built-wheel product-surface verification pass. Full-repository
Ruff is not a release gate and currently reports pre-existing errors in the
separate experimental `gt_engine.engine` tree. A fresh exact-SHA hosted Linux
run remains required before certification.

## Exact-SHA run 33062346788 and evidence-first delivery correction

Run `33062346788` tested commit
`4d3f914fc61637b4f3841f637f419643e90c1068`. Every product gate except
localization passed, including clean install, Python and Go suites, product
surface, CLI, real-repository matrix, graph truth, graph and language
lifecycle, pinned dense model, harness E2E, and the failure campaign. The
localization result improved exact-edit precision from 0.50 to 1.00 and
eliminated false edit authority, but remained release-blocking: 19/20 cases
ran, implementation fact recall was 15/19 (0.7895), implementation precision
was 15/28 (0.5357), and required coverage was 0.7895.

The receipts isolated a provider-boundary failure. A pathless semantic Rust
task produced a healthy exact-revision graph and correct dense hits, but
provider metadata consumed the 500-token allowance.
The planner reached a zero-claim packet that fit and then raised
`FAILED:context_evidence_empty`. A clean pinned oxvg reproduction confirmed
that `selectors.rs`, `style.rs`, and `remove_empty_containers.rs` existed in
the compiler evidence before provider compaction.

The first corrective candidate, commit
`95d4a2bb322ad1c55a6348be763a0c9f4764b074`, correctly made compaction
evidence-first, but also placed broad facet count ahead of lexical and scoped
identity. Exact-SHA run `33072270921` falsified that ranking change: all 20
cases executed and oxvg stopped failing treatment delivery, but fact recall
fell to 12/20 (0.60), implementation precision fell to 15/29 (0.5172), and
required coverage fell to 0.60. The broad-coverage ranking is therefore
rejected and reverted; it is not part of the candidate architecture.

The surviving correction is general. Planner authority reflects the evidence
source instead of the optimistic output label: exact identities, hybrid
semantic support, and rank-only lexical/dense candidates remain distinct. A
READY compiler packet cannot compact to metadata-only output. Unique exact
owners remain single. An exact disconnected identity set outranks a heuristic
owner for the same decision, and uncertain rank-only ownership is represented
as a bounded two-candidate set rather than a false singleton. Public surface,
integration, validation, process, impact, and new-file precedent remain
separate roles. Provider receipts include a complete candidate ledger with
role, authority, requirement bindings, serialized cost, rank, selection, and
omission reason.

Product status remains `NOT_CERTIFIED` until a fresh exact-SHA hosted
twenty-task receipt passes every product gate; no paid benchmark is
authorized.

Focused production-path replays on the current candidate pass at both failure
boundaries. Boa uses exact ambiguity and scores coverage/ambiguity recall/
implementation recall of 1.0 at 330 tokens with precision 0.6667. Oxvg uses
bounded rank-only ownership, exposes `selectors.rs`, and scores coverage and
implementation recall of 1.0. Neither replay grants edit authority, degrades
the graph, or fails treatment delivery. The full Python suite, SQLite-FTS5 Go
suite, changed-file Ruff, product lint, 25-document consistency audit, built
wheel surface, and real CLI lifecycle pass locally.
