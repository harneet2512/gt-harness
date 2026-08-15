# GroundTruth Final Stand

> **Current architecture notice (2026-08-03):** the installed inline engine is
> retained only as a historical and rollback path. Active GT-on development is
> the host-owned central runtime documented in
> [CENTRAL_RUNTIME_IMPLEMENTATION.md](CENTRAL_RUNTIME_IMPLEMENTATION.md). Round
> 11 established neither efficacy nor efficiency and its readiness claim has
> been superseded.

This directory is the terminal execution authority for GroundTruth Phase II. It converts the accepted deterministic-observation monograph into implementation, validation, release, and closeout work. It does not reopen the research question.

There is no open-ended backlog. Every item ends as `BUILD`, `MODIFY`, `KEEP`, or `REMOVE`, and every execution item ends with evidence of completion or removal.

## Current state

The machine authority records **25 `COMPLETE`, 0 `IN_PROGRESS`, and 1 `REMOVED`**. FS-023 is `COMPLETE`: the complete provider-free battery passed in immutable GitHub Actions [run 30729901088](https://github.com/harneet2512/gt-harness/actions/runs/30729901088) at harness commit `e87cada097f55fe5df203c339148c65fff75c36a` and GroundTruth commit `61cfdbce2c42751c11028e46e863b3231f0bb70e`, every workflow step was green, and the run made zero provider calls. Artifact `8827623572` has GitHub API digest `sha256:1de4fa253719edf851484d8ab98b7e9b7077f11552a6f8c18ecf0401c328ac74`; its inner deterministic bundle has SHA-256 `64f416aee72fdc3ed6828ca0cb68ceda68455b3d363997053280cc71cf92150f`.

There are no open FS rows. Under the project owner's terminal override, FS-024 used exactly one provider-bound GT advisory run against the frozen local GT-off baseline for `fix-code-vulnerability`. Candidate Actions run `30731388242` produced artifact `8828119172`; the provider trial and verifier passed, although the workflow concluded failure at later offline postprocessing. The corrected analyzer ran locally against that immutable artifact without another provider trial. Reward tied at `1.0`; provider calls fell from 33 to 25, while total actions rose from 33 to 37, pre-edit exploration rose from 19 to 25, and raw pre-edit bytes rose from 34,696 to 43,009. This is a one-task descriptive witness, not evidence of benchmark-wide efficacy or exploration reduction. FS-025 therefore records the conservative decision `KEEP`: stock Mini-SWE remains the default and GroundTruth remains explicit opt-in. FS-026 is a bounded final attestation over that decision and makes no general solve-rate or non-inferiority claim.

## Authority order

1. [PHASE_II_IMPLEMENTATION_ROADMAP.md](PHASE_II_IMPLEMENTATION_ROADMAP.md) defines the architecture, priorities, gates, and engineering TODOs.
2. [direct_capabilities.csv](direct_capabilities.csv) is the machine-checkable 17-row DIRECT inventory.
3. [role_audit.csv](role_audit.csv) is the generated machine-checkable 129-row role inventory.
4. [language_support.csv](language_support.csv) is the 30-language registry inventory.
5. [language_operation_certification.csv](language_operation_certification.csv) terminally classifies all 210 language-operation pairs.
6. [closeout_status.csv](closeout_status.csv) is the 26-row execution-state authority.
7. [execution_ledger.md](execution_ledger.md) records implementation receipts, external gates, and exact limitations.
8. [LIVE_TODO.md](LIVE_TODO.md) is the current-only FS-001 through FS-026 closure loop.
9. [LIVE_TODO_HISTORY.md](LIVE_TODO_HISTORY.md) preserves superseded checkpoint history and is not current status authority.
10. [POST_AUDIT_HARDENING.md](POST_AUDIT_HARDENING.md) records bounded post-audit implementation and provider-free Codespaces receipts without changing open terminal gates.
11. [validation_receipt.json](validation_receipt.json) is the latest machine-validation result.
12. [CLEAN_MACHINE_RUNBOOK.md](CLEAN_MACHINE_RUNBOOK.md) defines the provider-free external validation flow.
13. [ROLLBACK_RUNBOOK.md](ROLLBACK_RUNBOOK.md) defines rollback triggers, execution, verification, and receipts.
14. [SINGLE_WITNESS_CONTRACT.md](SINGLE_WITNESS_CONTRACT.md) is the terminal owner-approved FS-024 validation contract.
15. [phase2_experiment_manifest.json](phase2_experiment_manifest.json) and [EXPERIMENT_EXECUTION_CONTRACT.md](EXPERIMENT_EXECUTION_CONTRACT.md) preserve the superseded six-arm/60-run design. It was never executed and is not required for project closure.
16. [receipts/](receipts/) contains provider-free positive and negative machine receipts.
17. [language_operation_compatibility.json](language_operation_compatibility.json) freezes the
    GroundTruth-produced compatibility authority used to generate the public typed-action schema.
18. [gt_finalstand_provider_free.yml](../.github/workflows/gt_finalstand_provider_free.yml) is the
    dispatch-only GitHub Actions implementation of the provider-free closeout gate.

Within [receipts/](receipts/), `provider_free_workflow.json` binds successful run `30729901088`, both repository commits, the workflow identity, and every input-receipt hash; `fs023_provenance.json` remains the deliberately pre-artifact receipt whose declared missing post-run identities are supplied by the successful Actions run and artifact API record. `final_codespace_verification.json` binds the product, harness, smoke, Go, worktree, and clean-fixture evidence. `fs024_single_witness_baseline.json` freezes the local GT-off comparator and `fs024_single_witness_analysis.json` freezes the matched descriptive result. `experiment_execution_plan.json` and `experiment_dry_run.json` remain historical proof that the superseded 10-task × 6-arm plan was never executed (`executed=false`, `provider_calls=0`).

The accepted monograph under `.research/gt-deterministic-interface/` remains the evidence basis. This directory owns the implementation decision. If prose here conflicts with a CSV inventory, the roadmap governs intent and the CSV must be repaired before work continues.

## Non-negotiable invariants

- GroundTruth does not replace Mini-SWE reasoning.
- The planner chooses the action before GroundTruth intervenes.
- Stock Bash semantics remain literal.
- Unknown, compound, mixed read/write, stale, ambiguous, or incomplete operations preserve raw behavior.
- Builds and tests retain complete native diagnostics.
- Replacement requires a typed action and a mechanically certified result contract.
- GT-off remains a first-class stock-compatible mode.
- Every capability has a kill switch and fail-open path.
- Unsupported functionality is removed from advertised support rather than left pending.
- Project closure requires completed controlled validation, frozen artifact hashes, and terminal receipts for every TODO.

## Machine checks

The complete provider-free release check runs through the dispatch-only
`gt_finalstand_provider_free.yml` GitHub Actions workflow. Fast document checks can also run in a
GitHub Codespace from the repository root:

```powershell
python scripts/generate_gt_finalstand.py --check
python scripts/validate_gt_finalstand.py
python -m pytest tests/test_gt_finalstand.py tests/test_phase2_closeout.py -q
```

The validator enforces the current terminal contract: FS-024 requires the hash-bound single-witness execution and analysis receipts, FS-025 requires the conservative `KEEP`/no-mutation decision, and FS-026 requires the bounded final attestation. The historical six-arm artifacts remain validated as unexecuted records, but they are not a closure requirement.
