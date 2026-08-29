# Trust calibration and false-confidence measurement

**Status:** ready  
**Priority:** foundational. This may matter more than any borrowed capability because call edges feed retrieval, impact analysis, communities, process objects, planning, and verification.

## Problem statement

GT currently records resolution method, scalar confidence, candidate count, trust tier, and evidence type on a resolved call. The Go resolver defines that shape in [`vendor/gt-index-src/internal/resolver/resolver.go`](https://github.com/harneet2512/gt-harness/blob/2bf3f4954b123c222b7f6c2b98761654ef2ef007/vendor/gt-index-src/internal/resolver/resolver.go#L405-L425). Across resolver paths, tier is assigned from resolver-specific confidence choices, including calls to `tierFor(conf)` such as the import edges in [`imports.go`](https://github.com/harneet2512/gt-harness/blob/2bf3f4954b123c222b7f6c2b98761654ef2ef007/vendor/gt-index-src/internal/resolver/imports.go#L115-L127).

That is not calibration. A bucket over hand-assigned scores only repeats the resolver's belief. Any consumer that treats the tier as truth inherits the same error with a more authoritative label.

AST evidence is the route out of this problem, but "AST-derived" is not one confidence level. Exact lexical binding, an explicit import chain, a receiver/type fact, and a same-name match with N candidates are different mechanisms with different failure modes. Tier must first be derived from mechanism and explicit uncertainty. It becomes empirical only after the mechanism is tested against evidence the resolver did not generate.

## Goal and publishable deliverable

Produce a versioned calibration dataset and report that answer, for each resolution method and language:

1. How many existing call edges were eligible for audit?
2. How many received an external label?
3. How many agreed, disagreed, or remained indeterminate?
4. What is the measured error rate among labeled edges?
5. What is the 95% Wilson confidence interval?
6. How stable are the edges across a no-change reindex and a clean rebuild?
7. Does behavioral evidence corroborate or contradict the resolved target?

The headline number is:

```text
method_error_rate = externally_labeled_disagreements / externally_labeled_edges
```

Publish the numerator, denominator, oracle coverage, and Wilson interval beside every rate. Never publish a percentage without its counts. Also publish a micro-average, a macro-average across repositories, and a worst-repository rate. Do not merge languages or methods until their separate rows are visible.

## Part A: derived tier and actionable uncertainty

### A1. Resolution provenance vocabulary

Introduce a closed, versioned `resolution_provenance` enum. Initial values:

- `ast_lexical_exact`: AST reference binds to one declaration in the same lexical scope.
- `ast_member_exact`: AST member binds through an explicit receiver/type declaration available in parsed evidence.
- `ast_import_explicit`: explicit import or alias resolves through a complete import chain to one exported declaration.
- `ast_inheritance_exact`: receiver/member resolves through an explicit inheritance or implementation relation.
- `scope_unique_name`: no structural binding, but exactly one candidate exists in the declared search scope.
- `scope_ambiguous_name`: N candidates exist in the declared search scope.
- `dynamic_dispatch_set`: multiple targets are valid under dynamic dispatch.
- `external_unresolved`: target is outside the indexed repository set.
- `parser_incomplete`: required syntax or symbol facts were not produced.
- `unknown_legacy`: migrated row without sufficient provenance.

Do not encode these as numbers. Preserve the raw resolver method as a separate compatibility field.

### A2. Derived tier function

Tier is a pure function of provenance and explicit uncertainty, not confidence thresholds:

| Derived tier | Required condition | Meaning |
|---|---|---|
| `structural_exact` | exact AST provenance, one candidate, unique in declared scope, no unresolved dynamic dispatch | structurally bound, pending empirical calibration |
| `structural_set` | AST evidence identifies a finite valid target set, including dynamic dispatch | set-valued structural result; no single target is certified |
| `heuristic_unique` | `scope_unique_name` with one candidate but no AST binding | unique under a declared search scope, not structurally proved |
| `ambiguous` | candidate count greater than one without a valid set-valued dispatch proof | agent must inspect candidates or seek more evidence |
| `unresolved` | zero candidates, external target, incomplete parse, or legacy provenance | no target claim |

A later empirical grade may be attached as `calibration_grade` and `measured_error_rate`, but it must not rewrite the derived tier. Mechanism and observed reliability are separate facts.

### A3. Replace scalar confidence in agent-facing contracts

Keep legacy `confidence` only for backward-compatible reads during migration. New writes and agent-facing objects use:

```text
resolution_provenance
candidate_count
candidate_ids[]
unique_in_scope
scope_kind
scope_id
receiver_evidence_kind
receiver_type_id
receiver_type_source
dynamic_dispatch_possible
export_status
import_chain[]
parser_completeness
selected_target_id
selection_reason
verification_status
calibration_dataset_id
measured_error_rate
calibration_sample_count
```

Required invariants:

- `candidate_count == len(candidate_ids)`.
- `selected_target_id`, if set, is in `candidate_ids`.
- `unique_in_scope` requires `candidate_count == 1` and a nonempty `scope_kind`/`scope_id`.
- `structural_exact` requires one candidate and `dynamic_dispatch_possible == false`.
- `ambiguous` and `structural_set` cannot expose a verified single-target edge.
- `export_status` is `exported`, `not_exported`, `not_applicable`, or `unknown`, never a Boolean that conflates unknown with false.
- Consumers may use empirical error rate to choose inspection or abstention, but not to turn a candidate into a fact.

### A4. Migration

- Read old rows additively and map them to `unknown_legacy` unless existing provenance fields prove a stronger class.
- Never infer exact AST provenance from a high scalar confidence.
- Version the schema and emit migration counts by old method/tier and new provenance/tier.
- Round-trip old databases in tests.

The unattended run must not edit `vendor/`. If first-party adapters cannot obtain the required provenance or candidate set from current index output, stop this part and record the exact additive Go/SQLite output contract needed. Do not reconstruct missing candidates from a later name search and call that resolver provenance.

## Part B: false-confidence detection with independent oracles

A resolver cannot audit itself. Parser fields, resolver scores, and graph topology are useful features, but they are not labels. Run the following oracles against a frozen sample of existing edges.

### B1. Compiler/LSP definition oracle

For each supported language and sampled callsite:

1. Check out the exact indexed commit in an isolated worktree.
2. Start the language's compiler or LSP with the repository's own configuration.
3. Request definition or type-resolved target at the callsite span.
4. Normalize returned URI and span to GT symbol identity.
5. Compare the external target set to GT's candidate set and selected target.

Labels:

- `agree_exact`: external target set equals GT's single selected target.
- `agree_set`: external set equals GT's retained candidate set.
- `gt_false_positive`: GT selected a target outside the external set.
- `gt_false_negative`: external target absent from GT candidates.
- `set_incomplete`: some but not all external targets retained.
- `oracle_indeterminate`: timeout, unsupported construct, missing build state, generated source, or external dependency.

Only the first five are externally labeled. `oracle_indeterminate` contributes to coverage, never to accuracy. Capture tool name/version, command, repository commit, configuration hash, duration, stderr hash, and normalized result.

To reduce correlated error, compiler/LSP output must come from the language toolchain, not a GT graph query. Where compiler and LSP both exist, report their agreement first and keep disagreements as a separate adjudication queue.

### B2. Reindex persistence oracle

Run three index variants on identical source bytes:

- incremental no-change reindex,
- clean rebuild into a fresh database,
- deterministic row/file-order perturbation where supported.

Compare callsite-to-candidate-set identity, selected target, provenance, and tier.

Labels:

- `stable_all`: identical across all runs.
- `selection_flip`: candidate set stable but selected target changes.
- `candidate_drift`: candidate set changes.
- `edge_disappeared` or `edge_appeared` with unchanged source.
- `not_comparable`: tool failure or nondeterministic generated inputs.

Persistence is a reliability signal, not correctness ground truth. A stable wrong edge remains wrong. Report it beside external accuracy and use drift to prioritize oracle labeling.

### B3. Test and assertion outcome oracle

Use behavioral outcomes only when an edge participates in an executable, attributable witness:

- a test directly covering the caller and target,
- an assertion about dispatched type, return value, side effect, or called implementation,
- a controlled mutation that changes only the proposed target and produces the expected covering-test change.

Labels:

- `behavior_corroborates`;
- `behavior_contradicts`;
- `behavior_non_discriminating`;
- `behavior_not_run`.

Do not claim that a passing broad test proves a call edge. Require an attribution record tying the outcome to the callsite/target hypothesis. Mutation-based checks must run in a disposable worktree, restore cleanly, and never commit mutations.

### B4. Co-change witness

For historical commits that change a caller's invocation shape or callee signature/body, test whether the proposed pair co-changes within a bounded commit window. Record positive, negative, and unavailable witnesses. Co-change is corroboration, not a correctness label, and must never replace compiler/LSP disagreement.

## Sampling plan

Create a deterministic, checked-in manifest with:

- repository and commit;
- language;
- callsite identity and source span hash;
- resolution provenance and legacy method;
- derived tier;
- candidate count bucket: `0`, `1`, `2-3`, `4+`;
- receiver form;
- exported/non-exported/unknown;
- selection seed and stratum.

Use stratified random sampling by language, provenance, and candidate-count bucket. Include every rare stratum up to its population size. For common strata, target at least 100 externally labeled edges; if fewer labels are available, publish the smaller denominator and wider interval. Freeze the manifest before running oracles. Failed or indeterminate oracle calls stay in the dataset so coverage cannot be inflated by deletion.

Hold back at least 20% of labeled examples as a validation set. Use the calibration split to set agent policies or empirical grades, and report final error on the untouched validation split. Never tune and publish on the same labels.

## Output artifacts

Add first-party modules and versioned outputs such as:

- `gt_engine/resolution_provenance.py`
- `gt_engine/trust_calibration.py`
- `scripts/build_trust_calibration_manifest.py`
- `scripts/run_trust_oracles.py`
- `tests/test_resolution_provenance.py`
- `tests/test_trust_calibration.py`
- `artifacts/trust_calibration/<dataset-id>/manifest.jsonl`
- `artifacts/trust_calibration/<dataset-id>/oracle_results.jsonl`
- `artifacts/trust_calibration/<dataset-id>/summary.json`
- `docs/benchmarks/GT_TRUST_CALIBRATION_<dataset-id>.md`

`summary.json` must include schema version, dataset ID, source commit, tool versions, manifest hash, result hash, seed, per-stratum population/sample/labeled counts, confusion counts, error rates, Wilson intervals, oracle coverage, stability rates, behavioral corroboration counts, and exclusions by reason.

## Acceptance criteria

### Data and tier correctness

- Tier is derived only from the closed provenance/uncertainty contract.
- Unit tests cover every provenance-to-tier branch and invalid combination.
- No high legacy scalar can become `structural_exact` without structural provenance.
- Candidate sets and actionable uncertainty survive index-to-agent serialization.
- Existing databases load conservatively and migration counts reconcile to source row counts.

### Oracle independence and reproducibility

- Frozen manifest reproduces byte-for-byte from the same commit and seed.
- Compiler/LSP commands and versions are recorded.
- No GT-produced confidence, tier, graph neighborhood, or selected target is used as an oracle label.
- Re-running completed oracle cases produces the same normalized labels or records the drift.
- Indeterminate and failed cases remain visible in coverage denominators.

### Publishable measurement

- Each method/language row reports `errors / labeled`, rate, 95% Wilson interval, and oracle coverage.
- Report includes micro-average, repository macro-average, worst repository, and held-out validation results.
- At least one machine-readable artifact and one human-readable report are produced.
- A reader can reproduce every published number from `manifest.jsonl` and `oracle_results.jsonl` with one documented command.
- The report distinguishes correctness labels from persistence and behavioral corroboration.

### Safe failure

- Missing compiler/LSP, unsupported language, timeout, or configuration failure becomes a named indeterminate outcome.
- Oracle disagreement is preserved, not resolved by GT's own score.
- No vendor file is modified during unattended execution.
- If current index output lacks required provenance, implementation stops at the first-party contract and records the blocker in `instinct_work/NOTES.md`.

## Verification commands

Codex should adapt exact arguments to the implemented CLI but preserve this sequence:

```bash
python -m pytest -q tests/test_resolution_provenance.py tests/test_trust_calibration.py
python scripts/build_trust_calibration_manifest.py --seed 2512 --output artifacts/trust_calibration/candidate/manifest.jsonl
python scripts/run_trust_oracles.py --manifest artifacts/trust_calibration/candidate/manifest.jsonl --output artifacts/trust_calibration/candidate/oracle_results.jsonl
python -m gt_engine.trust_calibration summarize --manifest artifacts/trust_calibration/candidate/manifest.jsonl --results artifacts/trust_calibration/candidate/oracle_results.jsonl --output artifacts/trust_calibration/candidate/summary.json
python -m gt_engine.trust_calibration verify --summary artifacts/trust_calibration/candidate/summary.json
python -m pytest -q
(cd vendor/gt-index-src && go test ./...)
git diff --check
git diff --name-only -- vendor/
```

The final command must print nothing. If it prints a path, stop and revert only the unattended vendor changes before proceeding.
