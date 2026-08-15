# GroundTruth Phase II Checkpoint History

This file is the historical and superseded checkpoint archive for
[LIVE_TODO.md](LIVE_TODO.md). Every status and aggregate below records what was asserted at its
checkpoint; later integrity corrections are explicit. None of these entries overrides the current
live queue, `closeout_status.csv`, or the latest validation receipt.

## Archived checkpoints

### 2026-08-01T20:41:33Z

- Machine authority created: 17 DIRECT, 129 role-audit, 30 languages, 210 language-operation pairs, and 26 FS statuses all validate.
- Current state: 3 `COMPLETE`, 23 `IN_PROGRESS`, 0 `REMOVED`.
- Local focused receipts recorded: harness 524 collected/521 passed/3 skipped; core 120/120 plus contracts 14/14; producers 132/132 plus focused 9/9.
- External Go receipt remains `GO_WORKFLOW_RECEIPT_PENDING_PARENT_REPLACEMENT`.
- FS-024 remains protocol-only; no paid experiment was launched.
- Next closure focus: capture immutable GitHub Actions Go receipt, then update FS-003/FS-007 without changing any unrelated status.

### 2026-08-01T20:49:29Z

- Added provider-free Phase II tooling for live language-manifest validation, runtime/registration scans, offline action/evidence/freshness/leak/determinism/cost cases, six-arm dry-run and paired analysis, promotion refusal, and runbook validation.
- Positive receipts: offline synthetic suite passed; runbook structure passed; six-arm dry-run passed with `executed=false` and `provider_calls=0`.
- Negative receipts: AST-backed forbidden-capability scan found 13 live definitions/runtime references; promotion refused because paid analysis, Go source/binary receipt, and rollback rehearsal are absent.
- No status promotion occurred. FS-015, FS-022, FS-023, FS-024, FS-025, and FS-026 remain `IN_PROGRESS` because their terminal criteria are not satisfied by provider-free scaffolding or synthetic tests.

### 2026-08-01T21:02:50Z

- Codespace GroundTruth branch identity: `7fcb019104f0e43311e2db009950e60014bc1f4b`.
- Native receipt: `CGO_ENABLED=1 go test -tags sqlite_fts5 ./...` passed across all Go packages.
- Harness receipt remains 524 collected, 521 passed, and 3 skipped.
- Runtime implementation cluster FS-002/FS-005/FS-006/FS-007/FS-009/FS-012/FS-013 plus delivery portions of FS-010/FS-011/FS-016/FS-020/FS-025 is implemented; focused receipt is 121 relevant tests with 113 passed and 8 conditional skips.
- Evidence cluster FS-017/FS-018/FS-019/FS-021 is implemented; widened receipt is 67 tests passed.
- Cloud Python clean-run remains iterative. The latest run stopped at 375 passed and 17 skipped because a workflow assertion still expected stale `brief.txt` behavior.
- No status promotion occurred at this checkpoint. The Go command passed, but immutable workflow/artifact binding, conditional-skip closure, full clean-run success, and each roadmap item's remaining acceptance conditions still govern terminal status.

### 2026-08-01T21:06:36Z

- FS-015 moved to `COMPLETE`: the GroundTruth live-registry compatibility authority contains all 210 pairs with exact=35, execution-specific=30, removed=145; the generated Mini-SWE schema now advertises only exact literal search, certified syntax, and execution-specific verification.
- Patch impact, definition, references, and callers are absent from the public kind enum and are rejected by the runtime certification gate when manually constructed. Syntax is extension-gated to all registered extensions of Go, JavaScript, Python, Ruby, and TypeScript.
- FS-022 moved to `REMOVED`: package import isolation and an AST import-closure scan from `scripts/miniswe_gt_run.py` cover 66 reachable Python files with zero embedding reranker, automatic co-change injection, or prohibited public identifier findings.
- Embedding/localization comparison implementations remain available only through isolated legacy/comparison paths; importing the current Mini-SWE runtime no longer registers them.
- Added `.github/workflows/gt_finalstand_provider_free.yml` for explicit provider-free Python, Go/CGO/FTS5, live language-manifest, compatibility parity, finalstand, forbidden-scan, and receipt-artifact checks. It was written only; it was not dispatched or pushed.
- Current state after evidence-backed promotion: 4 `COMPLETE`, 21 `IN_PROGRESS`, 1 `REMOVED`.

### 2026-08-01T21:39:09Z

- Final harness authority receipt: 129 tests collected, 128 passed, and 1 environment-conditional skip. The skip is recorded as conditional, not silently counted as a pass. The independent typed-action contract passed 16/16.
- FS-002, FS-005, FS-006, FS-007, FS-009, FS-012, and FS-013 moved to `COMPLETE` for their implemented runtime-authority contracts: snapshot/revision binding, provider delivery lineage, feature control, graph freshness, atomic transactions, raw-preserving verification, and invalidate/refresh/fallback behavior.
- GroundTruth terminal evidence receipt: the widened artifact suite passed 67/67. FS-017, FS-018, and FS-021 moved to `COMPLETE` for episode-wired obligation deltas, exact-identity recovery, and closed fresh blocker suppression with zero-provider-byte receipts.
- FS-019 moved to `COMPLETE` for the deliberately honest build-adapter contract: on-demand adapters emit deterministic `sound_overapprox` slices and explicitly report unresolved targets, membership, generated inputs, dependency edges, and other omissions instead of claiming false exactness.
- FS-015 external evidence was reconciled: Codespace Go specs passed, the Python language-compatibility suite passed 17/17, and the full `CGO_ENABLED=1 go test -tags sqlite_fts5 ./...` command passed across all packages at GroundTruth `7fcb019104f0e43311e2db009950e60014bc1f4b`.
- The cloud clean Python run remains iterative rather than terminal. It progressed beyond 2,085 tests after the real v7 path-IDF correction; no final all-green receipt is claimed from that in-progress run.
- FS-024, FS-025, and FS-026 remain `IN_PROGRESS`. No paid experiment, default promotion, final clean-machine artifact, or rollback rehearsal is inferred from provider-free test success.
- Current state after this evidence reconciliation: 15 `COMPLETE`, 10 `IN_PROGRESS`, 1 `REMOVED`.

### 2026-08-01T22:03:51Z

- FS-023 moved to `COMPLETE` after receipt reconciliation against its exact terminal criterion. The immutable Codespaces `gt.finalstand.offline_suite.v2` receipt reports `terminal=true`, `ok=true`, and no limitations or failures.
- Native evidence includes 10 cold `sqlite_fts5` builds, `semantic_artifacts_byte_identical=true`, adversarial coverage for dirty non-source files, dynamic imports, generated files, multiple languages, overloads, shadowing, and symlinks, plus cold/incremental/query cost measurements.
- Runtime and static evidence includes GT-off parity, stock Bash parse parity, typed exact-literal execution, freshness invalidation, certified sentinel-clean replacement, sentinel leak rejection, and `provider_calls=0`.
- The GroundTruth-built binary SHA-256 is `7c762a9a4cf27b6a08ae17e30a75b6c2b7f6fb9e94b3d5df73b4cb07bb58b2f5`.
- No other status changed. FS-024, FS-025, and FS-026 remain `IN_PROGRESS`; the provider-free FS-023 receipt does not substitute for the paid experiment, measured default promotion, or final project/rollback attestation.
- Current state: 16 `COMPLETE`, 9 `IN_PROGRESS`, 1 `REMOVED`.

### 2026-08-01T22:14:01Z

- FS-003 moved to `COMPLETE` after its exact native-registry and harness-consumption criterion became executable end to end.
- GroundTruth Codespaces evidence passed the manifest/parity specs and the full `CGO_ENABLED=1 go test -tags sqlite_fts5 ./...` suite. The registry manifest contains exactly 30 sorted language identities, rejects duplicate names/extensions, validates extension ownership, and emits deterministic canonical bytes.
- The generated compatibility authority carries source manifest SHA-256 `53afc44596f72668e11b5511f47424c5911f2b24682730bfb90b58a3d2297631`. `scripts/generate_gt_finalstand.py` now emits that hash and all 30 identities into `gt_engine/generated_typed_capabilities.py`; `gt_engine/miniswe_typed_actions.py` uses the hash as the default core `ConfigurationBinding` instead of an empty value.
- The harness-focused manifest-consumption contract and generator drift gate passed 28/28 plus `python scripts/generate_gt_finalstand.py --check`.
- No other status changed. FS-024, FS-025, and FS-026 remain `IN_PROGRESS`.
- Current state: 17 `COMPLETE`, 8 `IN_PROGRESS`, 1 `REMOVED`.

### 2026-08-01T22:17:27Z

- Audited FS-010, FS-011, FS-014, FS-016, and FS-020 only against the exact terminal criteria in the live queue. The focused closure receipt passed 66/66; the final harness Codespaces suite exited zero with one complementary graph-smoke skip; the typed contract passed 16/16.
- FS-010 moved to `COMPLETE`: every changed file receives an immediate post-revision syntax row, with exact Python status, deletion marked not applicable, and every other file explicitly unsupported.
- FS-011 moved to `COMPLETE`: untruncated patch data plus exact postimage bytes and hashes reconstruct create/modify/delete/text/binary outcomes; Python signatures are exact, while callers are explicitly `graph_recorded` and never claim completeness.
- FS-014 moved to `COMPLETE`: definition, reference, and caller operations are absent from the generated public typed schema for every language-operation pair, and manually constructed requests are rejected by the runtime certification gate.
- FS-020 moved to `COMPLETE`: new-file precedent is same-action, revision/transaction-bound, path-specific, reasoned, sibling-source inspectable, advisory-only, raw-preserving, quiet without precedent, exact-rename suppressing, and incapable of raw replacement or hidden execution.
- FS-016 remains `IN_PROGRESS`. Stable anchors/scores/reasons, deterministic ties, dirty files, no-match behavior, and unavailable-graph fallback are proven, but the written criterion explicitly requires a stale fixture. No test supplies an existing graph with `graph_fresh=false`, and `task_start_localization()` currently checks only `graph_db`, not `graph_fresh`, before attempting graph localization.
- No other status changed. Current state: 21 `COMPLETE`, 4 `IN_PROGRESS`, 1 `REMOVED`.

### 2026-08-01T22:25:25Z

- FS-016 moved to `COMPLETE` after the exact held clause was implemented and verified in the harness Codespace.
- `task_start_localization()` now attempts graph localization only when both `graph_db` exists and `graph_fresh` is true. An existing stale graph therefore cannot execute the graph path; localization falls back to the deterministic lexical producer and records omission `graph_localization_stale`.
- The fixture uses an existing graph with `graph_fresh=false` and fails if graph localization executes. Together with the previously passing advisory, stable-anchor, score/reason, deterministic-tie, dirty-file, unavailable-graph, and no-match fixtures, this satisfies the complete written FS-016 criterion.
- Codespaces focused suite/lint cell 620 exited zero with 27 tests passing and `All checks passed!`.
- No other status changed. FS-024, FS-025, and FS-026 remain `IN_PROGRESS`; FS-022 remains `REMOVED`.
- Current state: 22 `COMPLETE`, 3 `IN_PROGRESS`, 1 `REMOVED`.

### 2026-08-01 integrity correction

- The earlier FS-023 promotion was invalid. Its literal criterion requires the complete offline battery to pass in one immutable external workflow with artifacts and hashes.
- The available provenance explicitly records `workflow_execution_identity_bound=false` and lacks the harness execution commit, GitHub Actions run ID/URL, and uploaded artifact-bundle SHA-256. A suite-terminal Codespaces result does not satisfy that criterion.
- FS-023 returned to `IN_PROGRESS`; FS-024, FS-025, and FS-026 remain `IN_PROGRESS`; FS-022 remains `REMOVED`.
- Corrected current state: 21 `COMPLETE`, 4 `IN_PROGRESS`, 1 `REMOVED`.

### 2026-08-02T03:05:41Z

- FS-023 moved from `IN_PROGRESS` to `COMPLETE` only after its literal external-workflow criterion was satisfied. GitHub Actions [run 30729901088](https://github.com/harneet2512/gt-harness/actions/runs/30729901088) passed every step at harness/workflow commit `e87cada097f55fe5df203c339148c65fff75c36a` and GroundTruth commit `61cfdbce2c42751c11028e46e863b3231f0bb70e`, with `provider_calls=0`.
- Uploaded artifact `8827623572` has GitHub API digest `sha256:1de4fa253719edf851484d8ab98b7e9b7077f11552a6f8c18ecf0401c328ac74`. The independently downloaded outer archive matches that digest, and its inner deterministic provider-free bundle has SHA-256 `64f416aee72fdc3ed6828ca0cb68ceda68455b3d363997053280cc71cf92150f`.
- Four environment/dependency/runtime-layout failures were exposed and repaired on clean hosted runners: run `30729131218` lacked `litellm`; run `30729473520` lacked `harbor`; run `30729588061` lacked Git fixture author/committer identity and could not resolve `gt-index`, falling back to a v1.1.0 release URL that returned HTTP 404; run `30729715858` proved Git identity fixed but exposed that graph wake-up still searched the runtime-standard indexer path and again fell back to the nonexistent release asset.
- Run `30729289426` was a separate provenance bootstrap/integrity-gate failure, not an environment failure: the validator correctly rejected FS-023 until provenance either enumerated every unavailable workflow identity or cross-bound a successful Actions receipt and artifact API digest.
- Run `30729901088` installed the built indexer at the runtime-standard path, completed compatibility, generation, validation, harness, runtime, Ruff, receipt, bundle, and upload steps successfully, and supplied the post-run linkage that the deliberately pre-artifact `fs023_provenance.json` could not contain.
- FS-024 remains `IN_PROGRESS` because no user-authorized six-arm provider experiment or independent paired outcome analysis has run. FS-025 remains `IN_PROGRESS` because measured FS-024 evidence, default promotion, duplicate-path removal, GT-off parity, and rollback proof remain absent. FS-026 remains `IN_PROGRESS` because final attestation depends on terminal FS-024/FS-025 receipts plus the final clean-machine release bundle and rollback rehearsal.
- Current state: 22 `COMPLETE`, 3 `IN_PROGRESS`, 1 `REMOVED`; FS-022 remains `REMOVED`.

### 2026-08-02 owner-approved terminal override

- The project owner replaced the unexecuted six-arm/60-run protocol with exactly one provider-bound GroundTruth advisory run compared against the frozen local GT-off baseline for `fix-code-vulnerability`. The old manifest and dry-run remain preserved as superseded historical artifacts with `executed=false` and `provider_calls=0`; they are not required for closure.
- FS-024 moved to `COMPLETE`. Both arms used Mini-SWE `2.2.8`, `deepseek-v4-flash`, provider fingerprint `fp_a18b46594c_prod0820_fp8_kvcache_20260402`, temperature `1.0`, step limit `100`, cost limit `$3`, command timeout `30`, timeout multiplier `1.0`, one attempt, and the same task checksum. Reward tied `1.0` to `1.0`. Calls changed `33 -> 25`, actions `33 -> 37`, pre-edit exploration `19 -> 25`, and raw pre-edit bytes `34,696 -> 43,009`.
- Candidate Actions run `30731388242` at harness `cdefd9a52c915364d346b790a65dde3104c17286` produced artifact `8828119172` with API SHA-256 `bbd7b620bfd9285c8a88a12714ce1331586052286fe2d96f9efcd36e2d6d12b5`. The provider trial and verifier passed. The workflow failed only during offline result-shape postprocessing; the analyzer was corrected and rerun locally against the immutable downloaded artifact without another provider trial.
- The baseline lacks a resolved container-image digest. GT delivery is visible in `events.jsonl` and trajectory observation augmentations, while the Mini-SWE report's aggregate delivery counter is zero; that instrumentation discrepancy remains explicitly unresolved.
- The result is explicitly descriptive. One stochastic task cannot establish benchmark-wide efficacy, non-inferiority, solve-rate improvement, or exploration reduction.
- FS-025 moved to `COMPLETE` with decision `KEEP`: stock Mini-SWE remains the default; GroundTruth remains explicit opt-in behind existing controls. The lower call count does not outweigh the lack of inferential evidence and the higher action, exploration, and byte counts.
- FS-026 moved to `COMPLETE` as a bounded one-task final attestation. This closes the requested project scope without converting the witness into a general benchmark claim.
- Terminal state: 25 `COMPLETE`, 0 `IN_PROGRESS`, 1 `REMOVED`; FS-022 remains `REMOVED`.
