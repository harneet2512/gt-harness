# GroundTruth Final Stand Execution Ledger

This ledger records the terminal implementation state through 2026-08-02. The machine authority for all 26 item states is [closeout_status.csv](closeout_status.csv). Under the project owner's final override, the one-run-vs-local-baseline witness replaces the previously planned six-arm experiment.

## Program manifest

| Field | Recorded value | Scope or limitation |
|---|---|---|
| Ledger schema | `gt.finalstand.ledger.v1` | This document and the CSV inventories |
| Harness HEAD | `2ec6cc56460549a83cba4163d81b95fdccb4fbb1` | Git commit identity only |
| Harness tracked-diff Git object | `010c93dc9e17044bea350a44c02f319c26e732a4` | Binary diff in the harness Codespace used by the final 592-test and smoke receipts; untracked files are not represented by this hash |
| GroundTruth HEAD | `7fcb019104f0e43311e2db009950e60014bc1f4b` | Git commit identity only |
| GroundTruth tracked-diff Git object | `9f5550b94ceef8e6fdd6413b15194a20ab71c7d7` | Binary diff in the product Codespace used by the terminal suite; the raw diff SHA-256 is `408e34de3891f9e2b7962e36aad8a1a6b92395acd3b272bbef43410d0db48dbd`, and untracked files are not represented by either hash |
| GroundTruth worktree status | 90 tracked changed paths; 24 untracked paths; porcelain-v1 SHA-256 `3540c75ab732812710fcb1b34b49225948be896d306b6200c6056b0637ccbccc` | Exact dirty-worktree input to the final Codespace suite; this is not a committed release manifest |
| Mini-SWE version | `2.3.0` | Installed harness package |
| Vendored GroundTruth wheel SHA-256 | `2d0483c43cd7209d7049439af963d420666bc853854b21e8a82e07236b00ee0e` | `vendor/groundtruth_mcp-1.0.0-py3-none-any.whl`; identical in the harness Codespace used for the final regression |
| Vendored Linux indexer SHA-256 | `024851815218f5ade0932f4a661287c743ce20d89e8ab2d1375f05d5b0b96c8a` | `vendor/gt-index-linux-amd64`; behavior must still be matched to source by FS-007 |
| Codespaces-built `sqlite_fts5` indexer SHA-256 | `7c762a9a4cf27b6a08ae17e30a75b6c2b7f6fb9e94b3d5df73b4cb07bb58b2f5` | Binary used by the recorded FS-023 provider-free battery; immutable workflow execution linkage remains missing |
| Accepted 129-row inventory SHA-256 | `419b74ad0580872cb93f4b7d21d0958cc13802e2a7494afb18041b41283c16b9` | Research authority used to generate `role_audit.csv` |
| Native language manifest SHA-256 | `53afc44596f72668e11b5511f47424c5911f2b24682730bfb90b58a3d2297631` | GroundTruth registry-derived source manifest; generated into and consumed by the harness configuration binding |
| Codespace Go receipt | GroundTruth `7fcb019104f0e43311e2db009950e60014bc1f4b`; `CGO_ENABLED=1 go test -tags sqlite_fts5 ./...` PASS | All Go packages compiled and passed in Codespaces; immutable workflow URL and artifact bundle remain a project-closeout input to FS-026 |
| Final Codespaces verification | `receipts/final_codespace_verification.json` | Binds product, harness, smoke, tagged-Go, worktree, and clean-fixture evidence; explicitly non-terminal for FS-023 through FS-026 |
| Phase II terminal witness | one GT advisory candidate compared with one frozen local GT-off baseline | FS-024 is complete under the owner's bounded override; the old six-arm/60-run plan was not executed |

The repository trees are dirty and contain pre-existing user work. The two tracked-diff hashes are not complete worktree manifests because Git diffs omit untracked paths. A release manifest must content-address the final tracked and untracked release set after FS-025 and FS-026 close it.

## Current status

Only these current states are legal:

- `COMPLETE`: the implemented scope has code and passing receipts.
- `IN_PROGRESS`: code or design exists, but at least one acceptance proof is absent.
- `REMOVED`: the implementation and advertised surface are absent and zero visibility is proven.

Current machine count: **25 COMPLETE, 0 IN_PROGRESS, 1 REMOVED**.

The complete 26-row status, decision, confidence, evidence, and missing-proof table is [closeout_status.csv](closeout_status.csv). A status is intentionally conservative: partial implementation never becomes `COMPLETE`, and a removal objective remains `IN_PROGRESS` while legacy execution paths still exist.

## Completed implementation receipts

### FS-001 — observation compiler contracts

- **Status:** `COMPLETE`
- **Primary implementation:** `D:/Groundtruth/src/groundtruth/runtime/observation_compiler.py`
- **Primary tests:** `D:/Groundtruth/tests/runtime/test_observation_compiler_contracts_20260801.py`
- **Observed receipt:** 14/14 new contract tests passed; the 120/120 focused core suite also passed.
- **Scope:** canonical action, evidence, interception, delivery, serialization, hashing, and validation contracts. This receipt does not claim the wider snapshot, delivery, or benchmark objectives are complete.

### FS-004 — native GroundTruth action tool

- **Status:** `COMPLETE`
- **Primary implementation:** `gt_engine/miniswe_typed_actions.py`
- **Primary tests:** `tests/test_miniswe_typed_actions.py` plus harness regressions.
- **Observed receipt:** the final provider-free harness suite collected 592 tests, passed 591, and recorded one declared complementary graph skip; JUnit SHA-256 `0a26259408a93879563f288c7f1433331aff27ecf20c59350ec32733ff075189`.
- **Scope:** seven typed action kinds coexist with literal stock Bash, execute without an added model call, and fail open as typed evidence. This receipt does not certify every producer for every language.

### FS-008 — exact lexical producer

- **Status:** `COMPLETE`
- **Primary implementation:** `D:/Groundtruth/src/groundtruth/runtime/deterministic_queries.py`
- **Primary tests:** `D:/Groundtruth/tests/unit/test_runtime_deterministic_queries.py`
- **Observed receipt:** 9/9 focused producer tests and 132/132 related GroundTruth regressions passed.
- **Scope:** explicit-scope byte search can claim exactness only when there are no omissions. Symlinks, unreadable files, invalid scope, and other omissions mechanically force incomplete semantics.

### FS-002/FS-005/FS-006/FS-007/FS-009/FS-012/FS-013 — runtime authority cluster

- **Status:** `COMPLETE` for each listed row.
- **Primary implementation:** `gt_engine/runtime_observation.py`, `gt_engine/gt_session.py`, `gt_engine/miniswe_runtime.py`, `gt_engine/miniswe_integration.py`, `gt_engine/miniswe_receipt.py`, `gt_engine/event_journal.py`, and `gt_engine/indexer.py`.
- **Observed receipt:** the focused harness authority suite collected 129 tests, passed 128, and recorded one environment-conditional skip; the independent typed-action suite passed 16/16. The final wider harness suite collected 592, passed 591, and recorded the same complementary graph skip. In Codespaces, GroundTruth `7fcb019104f0e43311e2db009950e60014bc1f4b` passed `CGO_ENABLED=1 go test -tags sqlite_fts5 ./...` across all packages.
- **Scope:** snapshot/revision binding, provider delivery lineage, feature control, graph freshness, atomic edit transactions, raw-preserving execution evidence, and invalidate/refresh/fallback behavior are terminal for their implemented contracts. The conditional skip is not represented as a pass and remains named in the receipt.

### FS-003 — generated language registry authority

- **Status:** `COMPLETE`.
- **Primary implementation:** GroundTruth `gt-index/internal/specs` registry manifest and compatibility generator; harness `scripts/generate_gt_finalstand.py`, `gt_engine/generated_typed_capabilities.py`, and `gt_engine/miniswe_typed_actions.py`.
- **Observed receipt:** GroundTruth Codespaces manifest/parity specs and full tagged Go suite passed; harness-focused contract passed 28/28 and generator drift check passed.
- **Identity:** exactly 30 sorted, collision-free languages bound to source manifest SHA-256 `53afc44596f72668e11b5511f47424c5911f2b24682730bfb90b58a3d2297631`.
- **Scope:** the harness generates its language identities and manifest hash from the GroundTruth compatibility authority, and Mini-SWE supplies that nonempty hash to the default core `ConfigurationBinding`.

### FS-017/FS-018/FS-019/FS-021 — terminal evidence cluster

- **Status:** `COMPLETE` for each listed row.
- **Primary implementation:** `D:/Groundtruth/src/groundtruth/runtime/terminal_evidence.py`, `D:/Groundtruth/src/groundtruth/runtime/miniswe_provider_boundary.py`, and the canonical Mini-SWE artifact seam.
- **Observed receipt:** 67/67 widened GroundTruth artifact tests passed.
- **Scope:** exact task-span obligation deltas, exact-identity failure recovery, honest on-demand build/configuration overapproximations, and fresh closed-scope submit suppression with a zero-provider-byte receipt. Build adapters expose unresolved targets and edges as omissions; they do not claim false exactness.

### FS-023 — provider-free offline validation

- **Status:** `COMPLETE`.
- **Immutable run:** GitHub Actions [30729901088](https://github.com/harneet2512/gt-harness/actions/runs/30729901088), attempt 1, completed successfully with every step green at harness/workflow commit `e87cada097f55fe5df203c339148c65fff75c36a` and GroundTruth commit `61cfdbce2c42751c11028e46e863b3231f0bb70e`.
- **Primary receipts:** `gt_finalstand/receipts/offline_suite.json`, schema `gt.finalstand.offline_suite.v2`, and `gt_finalstand/receipts/provider_free_workflow.json`, schema `gt.provider_free_workflow_receipt.v1`.
- **Observed battery:** `terminal=true`, `ok=true`, zero limitations/failures, 10 cold native `sqlite_fts5` builds, byte-identical semantic artifacts, complete declared adversarial corpus coverage, runtime/static probes, GT-off and stock-Bash parity, sentinel replacement/leak checks, cost measurements, and `provider_calls=0`.
- **Artifact binding:** uploaded artifact `8827623572` has API digest `sha256:1de4fa253719edf851484d8ab98b7e9b7077f11552a6f8c18ecf0401c328ac74`; the downloaded outer archive matches it exactly and contains inner deterministic bundle SHA-256 `64f416aee72fdc3ed6828ca0cb68ceda68455b3d363997053280cc71cf92150f`.
- **Provenance interpretation:** `fs023_provenance.json` is intentionally pre-artifact and explicitly lists the post-run identities it cannot know. `provider_free_workflow.json`, the successful run API record, and the artifact API digest supply those identities; all ten receipt-input hashes in the workflow receipt independently match the bundle entries.
- **Boundary:** this closes the provider-free FS-023 scope. FS-024 is separately closed by the owner-approved single matched witness; FS-025 keeps the baseline default; FS-026 is the bounded final attestation.

### FS-010/FS-011/FS-014/FS-020 — bounded evidence completion cluster

- **Status:** `COMPLETE` for each listed row.
- **Primary implementation:** `gt_engine/runtime_observation.py`, `gt_engine/miniswe_runtime.py`, `gt_engine/miniswe_covering.py`, generated typed-capability certification, and the Mini-SWE typed dispatcher.
- **Observed receipt:** focused closure suite passed 66/66; the final harness Codespaces suite exited zero with one complementary graph-smoke skip; typed public-surface contract passed 16/16.
- **Scope:** every changed file receives a revision-bound syntax/unsupported row; exact postimages reconstruct every edit shape; callers remain explicitly graph-recorded; uncertified definition/reference/caller actions are absent and rejected; and new-file precedent remains same-action, inspectable, path/provenance-rich advisory augmentation with raw preservation and exact-rename exclusion.
- **FS-016 completion:** localization remains advisory with stable anchors, deterministic scores/reasons/ties, dirty-file inclusion, and quiet no-match behavior. The existing-stale-graph fixture now proves `graph_fresh=false` prevents graph execution, falls back lexically, and records `graph_localization_stale`; Codespaces cell 620 passed 27/27 plus lint.

## Post-audit hardening receipt

[POST_AUDIT_HARDENING.md](POST_AUDIT_HARDENING.md) records the subsequent runtime hardening: injected runtime closure, action-specific freshness and snapshot manifests, same-byte syntax checking, removal of definition/reference/caller execution surfaces, response-committed receipts, Rust task-owned dependency provenance, bounded presubmit syntax execution, exact-only canonical submit suppression, restored clean-CI coverage, and independent acquisition/delivery counters.

The terminal provider-free Codespaces receipt records GroundTruth **9,980 passed, 415 skipped, 6 expected failures** with JUnit SHA-256 `2e96bae37da6761c1a1088a988d08ad0642ffc447190cdbf94cf160e8dbac688`; harness **592 collected, 591 passed, 1 skipped** with JUnit SHA-256 `0a26259408a93879563f288c7f1433331aff27ecf20c59350ec32733ff075189`; offline smoke **10 collected, 9 passed, 1 skipped** with JUnit SHA-256 `c420152806eabc9dfa73ef98394d3acf0482926ad8974913ef4a1795a5716234`; and a passing full tagged Go suite. `scripts/ci/substrate_proof.sh` also passed Bash syntax checking after LF normalization. Immutable Actions run `30729901088` subsequently exercised the committed provider-free closure and supplied the external run/artifact linkage that completes FS-023. FS-024 through FS-026 are closed separately under the owner-approved bounded witness and conservative default decision.

Focused Ruff and whitespace checks pass on the final package-isolation fixture repair. An exploratory wider Ruff sweep over 18 untracked Python closeout surfaces reports 49 diagnostics, including 26 auto-fixable findings. No global Ruff-green claim is made, and no broad mechanical lint rewrite was applied.

## External workflow receipt

The Go implementation has now been compiled and tested in Codespaces at GroundTruth commit `7fcb019104f0e43311e2db009950e60014bc1f4b`: the specs gate passed, the Python compatibility suite passed 17/17, and `CGO_ENABLED=1 go test -tags sqlite_fts5 ./...` passed across all Go packages. FS-003, FS-007, and FS-015 therefore have executable external evidence. The generated native registry is the harness language authority, identified by source manifest SHA-256 `53afc44596f72668e11b5511f47424c5911f2b24682730bfb90b58a3d2297631`; FS-026 owns the final workflow URL, logs, hashes, and rollback bundle.

The final dirty-worktree Python and smoke evidence is frozen in `receipts/final_codespace_verification.json`. For FS-023, the previously absent workflow identities are populated by run `30729901088`, artifact `8827623572`, API digest `1de4fa253719edf851484d8ab98b7e9b7077f11552a6f8c18ecf0401c328ac74`, and inner bundle SHA-256 `64f416aee72fdc3ed6828ca0cb68ceda68455b3d363997053280cc71cf92150f`. FS-026 incorporates that provider-free authority plus the bounded FS-024/FS-025 closure decision.

The immutable FS-023 workflow fields incorporated by the final FS-026 attestation are:

| Field | Required value |
|---|---|
| Repository and commit | exact GitHub repository plus immutable commit SHA |
| Workflow | workflow file path and workflow SHA |
| Run | immutable Actions run URL and run ID |
| Go environment | `go version`, OS, architecture |
| Commands | exact `go test` and build commands |
| Result | exit status and test/package counts |
| Artifacts | binary, manifest, and log SHA-256 values |

## Owner-approved matched-witness boundary

The original paper specified a six-arm paired causal experiment. The project owner superseded it with exactly one authorized GT advisory candidate run against the frozen local GT-off baseline on `fix-code-vulnerability`. The historical 10-task × 6-arm plan remains unexecuted (`executed=false`, `provider_calls=0`) and is not required for closure.

Candidate GitHub Actions run `30731388242`, attempt 1, job `91452315208`, executed at harness commit `cdefd9a52c915364d346b790a65dde3104c17286`. Artifact `8828119172` has API SHA-256 `bbd7b620bfd9285c8a88a12714ce1331586052286fe2d96f9efcd36e2d6d12b5`. The provider trial and verifier passed. The workflow's overall failure occurred later when offline postprocessing rejected Harbor's per-trial result shape; the analyzer was fixed and rerun locally against the immutable downloaded artifact, with no provider rerun.

Both arms earned reward `1.0`. Provider calls changed `33 -> 25`, total actions `33 -> 37`, exploration actions before the first edit `19 -> 25`, and raw bytes before the first edit `34,696 -> 43,009`. Both arms bind the same Mini-SWE `2.2.8`, model and provider fingerprint, task checksum, task-prompt hash, system-prompt hash, temperature, and budgets. GroundTruth's treatment was delivered through advisory observations, not a changed system prompt.

This is a descriptive one-task engineering witness. It cannot support a benchmark-wide solve-rate, efficacy, non-inferiority, confidence-interval, token-reduction, or exploration-reduction claim. FS-025 therefore keeps stock Mini-SWE as the default and GT as explicit opt-in. FS-026 closes the requested scope with that limitation intact.

The archived baseline does not bind a resolved container-image digest. The candidate's GT event journal and trajectory show advisory observation delivery, but `miniswe_report.json` reports aggregate `delivered_evidence=0`; the delivery-count instrumentation discrepancy remains unresolved and is not converted into a stronger delivery-frequency claim.

## Machine validation receipt

`scripts/validate_gt_finalstand.py` checks:

- 17 unique DIRECT identities, split into 10 FACTs and 7 CAP owners;
- valid BUILD/MODIFY/KEEP/REMOVE decisions on all four axes;
- 129 globally unique role-audit rows with ACQ=12, CAP=48, FACT=11, PERF=58;
- 30 unique native registry languages;
- 210 unique language-operation classifications across seven typed operations;
- exactly FS-001 through FS-026, each with a legal current status and nonempty evidence;
- terminal status counts equal 25 `COMPLETE`, 0 `IN_PROGRESS`, and 1 `REMOVED` under the owner-approved single-witness override;
- local Markdown links resolve;
- prohibited closeout-state text is absent from the final artifact set;
- forbidden product identifiers are absent from the Mini-SWE typed public surfaces;
- generated inventories match their research and native-source authorities.

The latest machine output is [validation_receipt.json](validation_receipt.json). The validator is designed to run inside the repository's GitHub Actions workflow or Codespace so the released receipt is external and reproducible.

Provider-free Phase II receipts are stored under [receipts](receipts/):

- `offline_suite.json` is the immutable terminal provider-free v2 receipt covering static identification/evidence/freshness/leak/determinism/cost cases, 10 cold native graph builds with byte-identical semantic artifacts, the declared adversarial corpus, runtime probes, GT-off parity, and zero provider calls;
- `fs023_provenance.json` binds every identity available before artifact upload and explicitly enumerates the post-run linkage it cannot know; `provider_free_workflow.json` plus run `30729901088` and artifact `8827623572` supply and cross-check that linkage;
- `final_codespace_verification.json` binds the terminal GroundTruth and harness regression suites, the ten-node provider-free smoke, full tagged Go result, exact Codespace worktree identities, and clean-fixture hashes without claiming an immutable workflow;
- `provider_free_smoke10.json` records the ten-node offline Mini-SWE runtime smoke and is not the owner-approved provider witness;
- `fs024_single_witness_baseline.json` freezes the local GT-off comparator identity and metrics;
- `fs024_single_witness_analysis.json` freezes the one-task matched identities, exact deltas, limitations, and `inferential_claim=false`;
- `experiment_execution_plan.json` and `experiment_dry_run.json` preserve the superseded deterministic 10-task × 6-arm design with `executed=false` and `provider_calls=0`; no part of that 60-trial plan is claimed as executed or required for closure;
- `forbidden_scan.json` proves the 66-file Mini-SWE import closure and public surface contain zero forbidden comparison-control registrations;
- `promotion_refusal.json` preserves the earlier six-arm promotion refusal. The final owner-approved decision reaches the same conservative outcome through a different terminal rule: keep stock Mini-SWE default and GT explicit opt-in;
- `runbook_validation.json` proves the clean-machine and rollback documents contain their required operational sections.

The provider-free receipts validate machinery, while the single-witness receipts validate only the frozen one-task comparison. Neither source supports a general benchmark-efficacy claim.

## All-language certification receipt

[language_operation_certification.csv](language_operation_certification.csv) contains exactly 210 language-operation pairs. Its current classifications are deliberately strict:

- explicit-scope literal byte search is certified `exact` for all 30 language identities because it is language-agnostic and drops exactness when omissions occur;
- syntax is certified `exact` only for Python, JavaScript, TypeScript, Go, and Ruby, matching the positive-evidence checker dispatch and all registered extensions;
- verification status is `execution_specific` for all 30 registry languages because it is bound to the exact command, configuration, and revision;
- definition, reference, caller, patch-impact, and uncertified syntax pairs are `removed`; the patch-impact producer remains incomplete/partial and therefore cannot appear in the typed schema.

The generated typed schema exposes only exact literal search, certified syntax, and execution-specific verification. Manually constructed removed actions are intercepted by the same generated runtime gate and return incomplete pass-through evidence rather than dispatching.

## Regression state

The stock Mini-SWE Bash surface remains present. Typed-router failure produces incomplete evidence and a pass-through decision rather than executing malformed typed content as shell. The runtime authority and immutable external workflow close provider-byte binding, feature control, atomic transactions, syntax/patch evidence, public analyzer narrowing, deterministic advisory localization, generated language-registry authority, bounded new-file precedent, freshness, and the complete provider-free validation battery for their implemented scope. No FS row remains `IN_PROGRESS`; stock Mini-SWE stays the release default and GT stays explicit opt-in.

## Project attestation

The project is **terminally complete under the owner's bounded one-task override**. The honest closure test is:

- 26 FS rows exist: yes;
- 17 DIRECT rows exist with four-axis decisions: yes;
- 129 role-audit rows exist exactly once: yes;
- 30 languages and 210 language-operation pairs are terminally classified: yes;
- all 26 FS rows have terminal implementation/removal decisions: yes, 25 `COMPLETE` and 1 `REMOVED`;
- Go source compiles and the complete tagged Go suite passes in Codespaces: yes;
- the complete offline battery is bound to one immutable external workflow execution with artifacts and hashes: yes, run `30729901088` and artifact `8827623572`; FS-023 is complete;
- the owner-approved one-task matched witness is frozen and honestly bounded: yes;
- the superseded six-arm/60-run experiment was executed: no, and it is explicitly not required;
- a general solve-rate or exploration-reduction claim is supported: no;
- the release decision preserves baseline safety: yes, stock Mini-SWE remains default and GT remains explicit opt-in.

`gt_finalstand` is the terminal bounded closeout authority. It must not be used to convert the single matched task into a benchmark-wide efficacy, non-inferiority, token-reduction, or exploration-reduction claim.
