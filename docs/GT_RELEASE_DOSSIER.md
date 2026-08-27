Exit code: 0
Wall time: 0.2 seconds
Output:
# GroundTruth final benchmark release dossier

Current status (2026-08-27): the GT Harness implementation candidate is
`NOT_CERTIFIED` until its new exact SHA passes the hosted provider-free Linux
campaign. The older certified and live-run identities below are historical
evidence, not authority for this candidate. Release is `HOLD` and paid
benchmarking is `NOT_AUTHORIZED`.

## Release status authority

Paid-benchmark eligibility is a machine-verifiable state plus explicit user
authorization, not a sentence in this document. The historical central product
required the now-retired `central_provider_free.yml` run for that exact SHA to
succeed, and its uploaded
`central_provider_free_receipt.json` records the same commit, zero provider
calls, no provider credentials, and `mechanical_completeness: PASS`. Local tests
establish implementation behavior; the hosted proof establishes the
source-built Linux indexer, pinned dense runtime, full integration suite,
release ancestry, and workflow contract for the exact frozen identity.

The retained historical central proof is:
- workflow [32526386608](https://github.com/harneet2512/gt-harness/actions/runs/32526386608)
- runtime SHA `77db941152d0d33929348590c7ce9528b3be64d6`
- status: `provider_calls=0`, `provider_credentials_present=false`,
  `READY`, `SMOKE_APPROVED`, `mechanical_completeness: PASS`
- receipt SHA `209fe2445362e149a5d09860ff14b1139839407b64a70cdd5d937bb0cb3cff55`
- mechanical JSON SHA `0628d9b3af03b980ac40a30987b56be508ff3f3a08cb1a728a0568cb10ff26d8`

This is not GT Harness product authority. The current Harness proof is
[`codespaces-product-certification.json`](../audit/receipts/codespaces-8931876/codespaces-product-certification.json):
exact clean SHA, all 13 gates PASS, provider calls zero, and provider credentials
not inspected.

The run URL and its content-addressed artifacts are the status record. This
dossier deliberately does not embed a mutable latest-run ID: changing this file
after certification would change the release SHA and invalidate that proof.

The legacy central release identity is
[`eval/release/active_release.json`](../eval/release/active_release.json). No
workflow or merge script owns a second dated “active” prediction path.

## Product bound by the current Harness subject

- Agent: `eval.harbor_gt_harness_adapter:GtHarnessMiniSwe246Agent`
- Scaffold: Mini-SWE-Agent 2.4.6 only
- Treatment: `gt-harness run --treatment groundtruth`, `hybrid_required`
- Denominator: `repair20-v1`, exactly 20 tasks
- Selection: deterministic, revision-bound decision-context compiler
- Retrieval: exact, lexical, BM25, pinned local dense, and certified structure
- Delivery: bounded same-observation contributions, no extra executor call
- Authority: exact symbols/paths and certified graph facts; dense/BM25/lexical
  results remain inspection-only
- Replay: mandatory and content-addressed
- Repository graph: required when supported source is present; dynamically
  activated when supported source is created
- Deadline: Harbor `task.toml` remains authoritative; model/actions shrink to
  remaining GT time and the supervisor owns terminal receipt finalization

## What “GT did this” means

For every provider-visible effect, the retained artifacts identify the legal
source observation, certified fact or claim, current source/graph revision,
selection decision, exact provider request, changed message index, delivery
time, and next observable model action. The intervention chain reports
observable behavioral uptake without inventing hidden reasoning. Causal
positive or negative flip labels require a matched trajectory or controlled
mechanism ablation; delivery alone is not called causation.

## Mechanical proof chain

1. The canonical release manifest verifies content hashes and Git ancestry.
   The no-spend gate also requires a clean tracked worktree; uncommitted source
   cannot inherit certification from the manifest's older runtime commit.
2. The secret-free release job rejects profile or post-freeze drift.
3. The provider-free workflow builds `gt-index` from current Go source.
4. Parser/spec tests prove declaration-free files retain identity without
   leaking identity-only `File` nodes as semantic symbols.
5. The pinned Snowflake ONNX and tokenizer hashes are verified.
6. The complete central suite exercises repository intelligence, lifecycle,
   retrieval, delivery, persistent state, semantic composition, replay,
   intervention joins, promotion accounting, and mutation-sensitive gates.
   It also proves selected provider claims have exact information-value
   certificates and that rejected/uncertain facts remain controller-only.
7. Static, readiness, legal-source integrity, and pre-smoke checks pass.
8. The authoritative no-spend gate emits one machine-readable verdict.
   The paid caller verifies the certified commit/status before its canary, and
   merge retains and revalidates the provider-free receipt and documentation
   proof.
9. During a task, every executor call passes the provider barrier.
10. At terminal state, every task receives an independently re-audited
    execution certificate.
11. Merge requires all 20 receipts, artifacts, certificates, benchmark
    manifests, trials, and frozen identities before any outcome verdict.
    Canonical result ingestion uses `scripts.harbor_results`; missing or
    conflicting task rows cannot be converted into a score.

## GitNexus research applied without downgrading GT

Source-level research is retained in
[`07_GITNEXUS_ARCHITECTURE.md`](gt_gitnexus_program/07_GITNEXUS_ARCHITECTURE.md),
[`08_GITNEXUS_RESOLUTION_AND_UNCERTAINTY.md`](gt_gitnexus_program/08_GITNEXUS_RESOLUTION_AND_UNCERTAINTY.md),
and
[`09_GITNEXUS_DELIVERY_AND_LIFECYCLE.md`](gt_gitnexus_program/09_GITNEXUS_DELIVERY_AND_LIFECYCLE.md).
GT adopted bounded process composition, process-aware packing,
same-observation augmentation, and stronger atomic graph publication. GT did
not adopt GitNexus’s global unique-name CALLS guesses, silent delivery/setup
failure, static post-edit index behavior, weak cache identity, or reliance on
embeddings as authority. GT retains its stronger LSP/compiler-ready evidence
boundary, revision certification, post-change validation, persistent execution
state, obligation logic, exact delivery receipts, and legal-source audit.

## Historical evidence boundary

The numbered documents under [`docs/gt_gitnexus_program`](gt_gitnexus_program/01_GT_CURRENT_ARCHITECTURE.md) are
research and run snapshots. Their embedded commit IDs and “live-unverified”
statements describe the dated evidence they analyze; they are not the Harness
release identity. `active_release.json` and `central_relational_v2` are historical
central-contract terms, not alternate Harness product paths.

The latest historical 20-task evidence demonstrated that lifecycle and
delivery failures were real and that raw flips were not causally attributable
without baseline trajectories. It does not certify the current candidate. The
new exact-commit provider-free proof certifies mechanics only; solve rate and
efficiency remain unproven until a separately authorized same-model comparison.

## Paid-dispatch decision

Current verdict: `NOT_AUTHORIZED`. Prior limited authorization was consumed by
the two GT-only repair20 smokes. A new run requires a newly frozen contract and
explicit user authorization.

Do not spend while any of these is absent:

- exact final runtime commit and two-file release freeze;
- `GT_MECHANICAL_COMPLETENESS=PASS` for that frozen SHA;
- complete provider-free receipt and artifacts;
- clean documentation/configuration audit; or
- user authorization after reviewing the proof.

After those conditions pass, the next action is one matched 20-task treatment
run—not repeated random smoke runs and not a broader benchmark. Report
integrity, solves, efficiency, and interventions separately.
