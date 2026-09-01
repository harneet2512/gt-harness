# archdone

As-built architecture record for `gt-harness` at harness commit
`5295a5b6ade1eaa6c93fb3c6115fa80746d4bfd4` (the exact `origin/main` tip when
this record was created). This document is versioned with the repository. A
future architecture, feature, receipt, or review-transport change must update
this file in the same commit; a stale copy is a defect.

## System boundary

`gt-harness` is the Python control plane and evidence ledger. The separate
Groundtruth repository owns the compiled graph producer and its SQLite graph.
The harness invokes or consumes producer artifacts only when the
`gt.producer_artifact.v2` receipt verifies source commit/tree, toolchain, build
tags, capabilities, module closure, binary bytes, and binary digest. No
provider, benchmark, or GCP operation is part of the normal evidence path.

The runtime flow is:

1. A task enters `gt_engine/task_contract.py` and persistent state is loaded by
   `gt_engine/persistent_execution_state.py`.
2. `gt_engine/miniswe_controller.py` and `gt_engine/miniswe_runtime.py` plan
   bounded actions. `gt_engine/miniswe_typed_actions.py` is the public typed
   observation surface.
3. Evidence producers (`graph_evidence.py`, `repository_intelligence.py`,
   `hybrid_retrieval.py`, `resolution_provenance.py`, and the compiled producer)
   emit typed, revision-pinned observations.
4. `gt_engine/evidence_router.py` admits or refuses claims at the model
   boundary. `gt_engine/attribution.py` records feature ownership without
   persisting raw prompts or provider payloads.
5. `gt_engine/event_journal.py`, `replay.py`, `replay_bundle.py`, and
   `miniswe_receipt.py` hash-chain decisions, replay inputs, and outcomes.
6. `gt_finalstand/receipts/` stores canonical JSON evidence. Issuers write
   atomically; verifiers recompute canonical bytes and reject tampering.

## State on disk

- `gt_finalstand/receipts/*.json`: versioned receipts, each with a schema and
  the applicable digest field. Important current receipts include HAR-5,
  HAR-9, HAR-37, HAR-62, HAR-63, HAR-64, HAR-66, HAR-72, the producer artifact,
  and the public re-audit.
- `gt_finalstand/feature_matrix.json`: the 18-row digest-bound feature proof
  matrix; `scripts/verify_feature_matrix.py` recomputes every cell.
- `gt_engine/indexer.py`: content-addressed index reuse key and validation;
  invalid hits rebuild into a temporary database and publish atomically.
- `gt-review-inbox` (separate ref): append-only `inbox/<ticket>/<packet>.json`
  packets plus `inbox/INDEX.json`. Packet bytes are canonicalized without the
  digest field for SHA-256 verification.
- Groundtruth's graph database is SQLite. Its graph-completion receipt and
  producer build identity are verified before graph evidence is trusted.

## Evidence and authority rules

Candidate sets, retrieval ranking, communities, calibration, process metadata,
and model-delivery explanations are evidence only. They never upgrade an
authority or verification status. Missing, stale, ambiguous, incomplete, or
tampered evidence abstains with a typed reason. A complete result has a known
true total and returns exactly that total; a truncated result names a known
larger total; legacy readers map conservatively to `legacy_unknown`.

## Certified feature registry (18 identities)

The source of this census is `gt_engine/attribution.py::DIRECT_FEATURES` and
the projection is `gt_finalstand/direct_capabilities.csv`. The implementation
module and proof surface for every identity are:

| Identity | Kind | Implementation | Proof / receipt class |
|---|---|---|---|
| `caller_contract` | FACT | `gt_engine/graph_evidence.py` | caller-contract evidence and HAR-63 resolution receipt |
| `covering_red` | FACT | `gt_engine/miniswe_covering.py` | executed covering-test receipt |
| `def_partition` | FACT | `gt_engine/repository_intelligence.py` | typed definition/reference partition |
| `localization` | FACT | `gt_engine/graph_context.py` | ranked localization observation |
| `newfile_precedent` | FACT | `gt_engine/repository_intelligence.py` | change-surface receipt |
| `obligations` | FACT | `gt_engine/task_contract.py` | task-bound obligation evidence |
| `recovery` | FACT | `gt_engine/miniswe_controller.py` | recovery/governor evidence |
| `signature_delta` | FACT | `gt_engine/resolution_provenance.py` | patch-delta evidence |
| `submit_refusal` | FACT | `gt_engine/verification_contract.py` | submit-gate refusal receipt |
| `syntax_result` | FACT | `gt_engine/miniswe_evidence.py` | executed edit-check evidence |
| `GT_CERT_DELIVERY` | CAP | `gt_engine/evidence_router.py` | HAR-64 eligibility and delivery receipts |
| `GT_CHANGE_SURFACE` | CAP | `gt_engine/repository_intelligence.py` | attributed change-surface fact |
| `GT_EDIT_CHECK` | CAP | `gt_engine/miniswe_evidence.py` | attributed syntax result |
| `GT_HYPOTHESIS` | CAP | `gt_engine/miniswe_controller.py` | exact repeated-failure recovery |
| `GT_LOC_RESLOT` | CAP | `gt_engine/graph_context.py` | attributed ranked localization |
| `GT_PATCH_DELTA` | CAP | `gt_engine/resolution_provenance.py` | atomic before/after patch evidence |
| `GT_SS_SUBMIT_RED` | CAP | `gt_engine/verification_contract.py` | certified submit refusal |
| `select_catalog` | CAP | `gt_engine/persistent_execution_state.py` | catalog-selection receipt |

The HAR-73 matrix records 18/18 identities with witnessed evidence. Its issuer
is `scripts/issue_feature_matrix.py`; its verifier is
`scripts/verify_feature_matrix.py`. A mutated cell or a missing identity is a
hard failure.

## Specialized proof machinery

- Calibration: `gt_engine/trust_calibration_report.py` emits
  `gt.trust_calibration_report.v2` overall, per-class, and per-mechanism
  metrics. Unsupported probabilities stay absent; calibration never changes
  authority.
- Why-this-edge: `gt_engine/why_this_edge.py` and the HAR-63 producer receipt
  retain dispatch state, candidates, flow witnesses, revisions, and a digest.
  Candidate/witness conservation is atomic and fail-closed.
- Eligibility: `gt_engine/evidence_router.py` seals HAR-64 receipts with
  admitted/refused claims, byte counts, request hashes, and prior-event links.
- Intent retrieval: `gt_engine/hybrid_retrieval.py` performs vec0 union,
  lexical/graph inclusion, exact rescore, deterministic ties, and named
  fallback reporting for INSPECT, EDIT, and VALIDATE.
- Communities: the HAR-66 certificate binds deterministic refinement,
  modularity, membership, connectivity witnesses, and input digests.
- Honesty: `gt_engine/result_envelope.py` and
  `gt_engine/miniswe_typed_actions.py` attach `gt.honesty_envelope.v1` to
  typed results and preserve conservative abstention semantics.
- Producer identity: `gt_engine/producer_artifact.py` verifies the shipped
  `gt.producer_artifact.v2` receipt before a configured binary is accepted.
- Re-audit: `scripts/gt_reaudit.py` replays canonical RED producers, checks
  shipped receipt blobs, runs mutation checks, and emits
  `gt.public_reaudit.v1`.

## Framework resolution status

The current main commit's shipped producer artifact is pinned by
`gt_finalstand/receipts/producer_artifact.json`. HAR-70 is landed at harness
commit `ff578719fef2a360af24d2076fe7ab3bc989c780`, with the Groundtruth
producer capability at `db9daf9ecf3a6ec1c92c40fba214ee66e4d09d14`. The shipped
producer now includes the coordinator-minted Python, TypeScript, JavaScript,
Go, and Java framework overlays, and the digest-bound
`gt.har70.framework_resolution.v1` receipt records a certified-pair increase
and a RED witness for each language. The currency rule requires this section
and the producer receipt to change in the same landing commit.

## Review transport

`gt.review_packet.v1` is the machine-readable review channel. A packet contains
ticket, PR, exact head SHA, source/check, kind, severity, status, detail,
supersession, creation time, and `packet_digest_sha256`. Packets are immutable;
state changes append a packet with `supersedes`. `inbox/INDEX.json` lists live
packet IDs and ticket membership. The transport is never merged into main and
never force-pushed except compaction with a tombstone.

## Repository connection map

| Concern | gt-harness | Groundtruth |
|---|---|---|
| Runtime orchestration | `gt_engine/miniswe_*`, task contracts, router | none |
| Evidence and receipts | `gt_engine`, `scripts`, `gt_finalstand/receipts` | graph completion and producer build receipts |
| Durable graph/index | consumer configuration and reuse gate in `gt_engine/indexer.py` | `gt-index` SQLite producer |
| Resolution | typed harness query/receipt surfaces | `gt-index/internal/resolver` and retained candidates |
| Provenance | attribution, replay, eligibility, honesty envelopes | graph source/tree/build identity |
| Pin model | producer receipt binds commit/tree/binary bytes | `git` commit and stamped executable |

Every cross-repository claim names both exact revisions. A changed producer
commit invalidates the old artifact receipt and requires a new re-pin.

## Approval boundaries

HAR-72 is design-only until a user approval receipt binds design hash, task
manifest hash, model/provider/configuration hash, account identity reference,
and a hard cost ceiling. The committed design therefore keeps
`benchmark_ready=false`, `provider_calls=0`, and `benchmark_runs=0`.
