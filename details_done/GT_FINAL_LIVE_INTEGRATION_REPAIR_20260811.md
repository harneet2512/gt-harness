# GT final live-integration repair — 2026-08-11

## Stop state

`VERIFIED_COMPLETE`

The source repair and release verification are complete at commit `338b391`.
Exact GitHub provider-free workflow `31544885372` built the current Go indexer,
loaded the pinned Snowflake ONNX asset, passed the runtime/readiness/static
gates, and printed `READY` plus `SMOKE_APPROVED`. No paid smoke was launched.

## Reproduced defects

1. The 10-task treatment used runner-specific kernel release/version in the
   first Mini-SWE prompt, invalidating byte-level A/B prompt parity.
2. The matrix `run` jobs did not provision the Snowflake ONNX asset; the model
   was downloaded in the separate plan job whose filesystem is not shared.
3. Preemptive retrieval ran on tasks already classified
   `not_applicable_no_supported_source`.
4. Live retrieval state retained raw shell text through the legacy adapter,
   including a risk of heredoc or interpreter program text becoming query
   material.
5. Exact evidence certification accepted substring symbol/path matches.
6. Claim identity included the global source revision, so unrelated edits made
   unchanged evidence appear new and bypassed delivery deduplication.
7. Delivery reporting counted only feature guidance. It omitted preemptive,
   graph-frontier, progress, and completion surfaces.
8. The old audit matched request hashes but did not require a persisted
   semantic support certificate or verify the exact provider-message position.

## Minimal repair

- Project shared `ProposedAction` values into bounded `RetrievalActionState`.
- Exclude raw commands/program bodies from live query state.
- Require canonical full paths or unique explicit non-common identifiers for
  exact certification.
- Make claim identity semantic and revision-independent while keeping stale
  revision validation separate.
- Persist `support_kind` and `supporting_channels` for every selected
  preemptive evidence row.
- Abstain for transfer-time source-less tasks and clean passing validations.
- Replace host-kernel prompt fields with container-owned OS identity fields.
- Record pre-GT control request/provider hashes for every call.
- Add the canonical `gt_engine.delivery_audit` stream and use it in trajectory
  audit, run diff, deep metrics, and matrix merge.
- Require unique claims, semantic support, exact call hashes, first-eligible
  timing, and an in-range GT-changed provider-message index.
- Install retrieval extras, provision/verify the pinned ONNX asset in each
  matrix task job, and fail merge unless both pre-run and live receipts prove
  the dense backend on every applicable source task.

## Historical correction

Outcome smoke `31535815764` contains:

- 60 model-visible deliveries;
- 44,372 visible GT characters;
- 44 preemptive deliveries / 41,855 characters;
- 9 repository-frontier deliveries / 1,352 characters;
- 7 feature-guidance deliveries / 1,165 characters;
- 60/60 first-eligible;
- 0 late, 0 predictive, 0 duplicate.

It nevertheless fails the repaired deterministic audit with 44
`preemptive_delivery_semantic_support_missing` failures. Those old rows cannot
prove why their evidence was eligible. The smoke is diagnostic, not release
evidence.

## Verification performed

- Full central-agent tests: pass; real Snowflake test skipped locally because
  the content-hashed asset is intentionally provisioned in GitHub.
- Hybrid/repository/ARB/preemptive tests: 97 passed.
- Delivery/audit/run-diff/deep-metrics tests: 40 passed after provider-index
  hardening.
- Workflow profile/gate tests: 8 passed.
- All changed Python files: `py_compile` pass and Ruff pass.
- Workflow: YAML parse pass, seven embedded Python heredocs compile, and all
  23 shell blocks pass `bash -n` under Git Bash.
- Biting perturbation: reintroducing source revision into claim identity makes
  both stability tests fail; removing semantic-support enforcement makes the
  fail-closed audit test fail. Both perturbations were reverted and GREEN was
  restored.
- The larger provider-free local suite has exactly six failures, all flowing
  from the stale checked-in Windows `gt-index.exe` missing `objective_c`.
  Changed runtime/audit/workflow tests do not fail.
- First exact GitHub attempt `31544619116` passed the current-source graph,
  real ONNX asset, language contract, and complete central runtime suite. It
  then failed the structural readiness audit because that audit accepted only
  the old unquoted YAML spelling `off`, while the dispatch workflow correctly
  uses YAML-safe `"off"`. The audit now accepts either spelling while still
  requiring the exact five arms, default, and mode arguments; a focused RED/GREEN
  test covers the quoted form.
- Exact rerun `31544885372` at `338b391` passed every step. Log audit found all
  required all-17 census markers, `READY`, and `SMOKE_APPROVED`. The uploaded
  `gt.central.provider-free.v1` receipt binds run ID `31544885372`, the intended
  branch, and `provider_calls: 0`.

## Remaining TODOs

1. Only after a separate authorization, run a matched paid smoke and require
   zero invalid delivery rows plus complete baseline/control request parity.
2. Keep the 89-task run blocked until the matched smoke passes.
