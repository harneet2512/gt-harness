# GroundTruth Inline Engine — Authoritative Handoff (finalstand)

Status: ACCEPTED_WITH_BOUNDED_UNKNOWNS (architecture) / ENGINE IMPLEMENTED + FIXED + GATED / PROVIDER-FREE GATE GREEN / TEN-RUN WITNESS ROUND 2 IN PROGRESS.
Written: 2026-08-02 (updated after witness round 1 + fixes) · Branch `inline-engine` · This document is **uncommitted** and local-only.

> **Read this first.**
> This file governs the GroundTruth Inline Engine phase. Mini-SWE remains the
> planner and reasoner. Every GT-enabled Mini-SWE action must automatically
> cross the engine boundary; ENGINE cannot depend on a voluntary
> `groundtruth(...)` call. Action intent is bound after selection and never
> predicted. GT-off remains a stock-equivalent baseline and rollback path.
> Advisory trajectories are historical evidence, not Inline Engine benchmarks.
> Research status is ACCEPTED_WITH_BOUNDED_UNKNOWNS until empirical
> performance is tested. Production completion cannot be claimed without
> end-to-end runtime receipts. No recommendation or 129-row disposition uses
> the forbidden closeout token.

## 1. Authority and status

- The ENGINE is implemented as `gt_engine/engine/` plus the ENGINE-mode seam in
  `gt_engine/miniswe_runtime.py` and `gt_engine/gt_session.py`.
- Mode vocabulary: OFF (stock-equivalent), ENGINE (sole action-to-observation
  interface), ADVISORY (historical/diagnostic only — never a benchmark arm).
- The five-decision law, lifecycle state machine, mutation protocol, and
  observation compiler are executable and provider-free (see `tests/test_engine_*.py`).
- Ten-run witness: in progress (run `30735955619`, ten frozen tasks).

Live status:

| Track | Status | Evidence |
|---|---|---|
| Research (monograph) | ACCEPTED | `.research/gt-deterministic-interface/REPORT.md` + appendices |
| Architecture (Outcome B) | ACCEPTED_WITH_BOUNDED_UNKNOWNS | this document §7 |
| Implementation | IMPLEMENTED + FIXED + GATED | 95 engine tests green (clean Codespace + GHA) |
| Provider-free certification | **GREEN** (run `30738422522`, zero provider calls) | engine battery incl. 10 gates, 129-audit, validator, compliance |
| Ten-run ENGINE witness round 1 | COMPLETED (revealed empty payload) | run `30736459512` — gap analysis §4 |
| Ten-run ENGINE witness round 2 | IN PROGRESS | dispatched after fixes |

## 2. Frozen evidence manifest

Full hashes in `gt_finalstand/engine_manifest.json`. Summary:

- `harneet2512/gt-harness` `inline-engine` (implementation branch) — head frozen at
  the run's checkout.
- `harneet2512/groundtruth` `61cfdbce2` (producer).
- Frozen GT-off baseline at `C:/Users/Lenovo/Downloads/gt-off-baseline
  deepseeknew` — `SUMMARY.md`, `merged.json`, `per_task_tokens.json` hashes in
  the manifest; Mini-SWE **2.2.8**, DeepSeek V4 Flash, temp 1.0, step 100.
- Baseline result: **66/89 (74.2%)**; the ten smoke tasks = **9 solved +
  gpt2-codegolf failed (0.0, infra AgentTimeoutError)**.

Environment facts (no credentials): local venv python 3.12.0; hypothesis
6.165.0; Codespace basicLinux32gb on the harness repo; groundtruth 1.0.0;
harbor 0.20.0. **The ENGINE smoke pins `mini-swe-agent==2.2.8`** (local env has
2.3.0; the baseline used 2.2.8).

## 3. Research method and claim discipline

Evidence hierarchy: executed code + tests > frozen receipts > research
artifacts > advisory prose. Independent lanes A–J each keep their sources,
falsification attempts, and negative results (see §6). A plausible claim never
becomes an implementation requirement without a bounded validation experiment.
Claim confidence vocabulary: PROVEN / STRONGLY_SUPPORTED / PLAUSIBLE / MIXED /
DISPROVEN / UNKNOWN.

## 4. Current-runtime reconstruction (advisory) and its defects

Reconstructed path (advisory seam): provider call → model response → action
selection → action parsing → `execute_actions` wrapper → GT visibility →
pre-execution snapshot → execution → post-execution diff/evidence → splice
`<gt-facts>` into tool observation → `add_messages` → next provider call.

Documented defects (all confirmed in code, advisory era):

1. Runtime mode is historically hardcoded to advisory behavior (legacy path).
2. Ordinary Bash obtains GT facts only after the command has executed.
3. Typed GT calls are optional and therefore do not constitute an engine.
4. Existing typed PASS_THROUGH did not execute the original fallback command.
5. Batched actions can all execute before their observations are appended.
6. Current "atomic transaction" capture is post-hoc, not precommit CAS.
7. Arbitrary shell mutations do not expose proposed postimage bytes before mutation.
8. The provider boundary is not yet authoritative for all normal observations.
9. Exact payload construction does not cover all overridden query paths.
10. Mid-run fail-open does not establish byte-for-byte GT-off parity.

The ENGINE addresses 1–6 (ENGINE posture, normalization, literal fallback,
ordered execution, canonical observation, mutation CAS); 7–10 remain bounded
unknowns / follow-up hardening.

## 5. Advisory failure analysis

Why advisory GT failed the causal-timing requirement: the agent may have
committed to an acquisition before advisory context arrives; task-start
localization predicts future needs without a selected action; optional typed
actions require the model to remember GT exists; postflight facts are useful
only if compiled into the same canonical observation before the next call;
post-hoc mutation evidence cannot influence the mutation that already occurred;
advisory observations can increase actions and visible bytes without producing
new solves. The FS-024 matched witness (tied reward, fewer calls, more actions/
exploration/bytes) does **not** establish Inline Engine efficacy.

## 6. Research lanes A–J (condensed; sources preserved)

- A Mini-SWE control flow / causal seam — seam = `_prepare_messages_for_api`,
  `query`, `execute_actions`. ENGINE owns the middle. PROVEN.
- B agent-computer interfaces — typed ACI is optional unless the boundary is
  mandatory; ENGINE makes the boundary mandatory. STRONGLY_SUPPORTED.
- C deterministic repository intelligence — graph/lexical producers certified
  per language/scope; ambiguity/staleness revoke replacement. STRONGLY_SUPPORTED.
- D transactional mutation models — post-hoc diff is not precommit CAS;
  PROPOSE→COMMIT is the sound model. PROVEN.
- E canonical observation compilation — one observation per action with
  deterministic ordering + evidence-delta projection. PROVEN (observe.py).
- F verification/submit semantics — submit inspected after selection; fresh
  closed blockers may suppress; unknown never blocks. STRONGLY_SUPPORTED.
- G formal lifecycle semantics — transition table + exhaustive/Hypothesis
  oracle + TLA+/PlusCal to add. PROVEN (executable oracle).
- H benchmark causality/efficiency — ten-run witness is descriptive only;
  no general claim. UNKNOWN until trials finish.
- I provenance/replay/security — content-addressed deliveries + replay check.
  STRONGLY_SUPPORTED.
- J alternative architectures — typed-only ACI, standing prompt, two-model,
  full shell virtualization were considered; Outcome B wins (§7). MIXED → Outcome B.

## 7. Candidate architectures and Outcome B

Compared: (1) host-native action-to-observation middleware; (2) typed ACI with
raw Bash escape hatch; (3) proposal/commit mutation as primary ACI;
(4) observation virtualizer/sidecar; (5) standing prompt / optional GT tool;
(6) two-model architect/editor; (7) general shell virtualization.

Scored on causal timing, Mini-SWE freedom, model opt-in, Bash preservation,
mutation precommit, deterministic honesty, freshness, context efficiency,
single-call iterations, language reach, risk, benchmark upside, replayability.

**Outcome B (frozen):** host-native action-to-observation middleware,
strengthened with transparent normalization, certified decision execution,
canonical observation compilation, structured mutation CAS, batch dependency
barriers, exact receipts, evidence-delta projection, and passive PERF
isolation.

Red-team: can still fail where (a) the producer is not certified for a
language/scope and replacement must be revoked; (b) a mutation writes outside
the structured path (raw shell stays literal, postflight-only); (c) the provider
boundary is bypassed by an un-overridden query path; (d) PERF instrumentation
influences the behavior it measures; (e) staleness slips into REPLACE.

## 8. Public contracts

Implemented in `gt_engine/engine/contracts.py` (all versioned, strictly
validated, hash-stable): EngineMode, ActionRequest, RepositorySnapshot +
SnapshotToken, EvidenceArtifact, InterceptionDecision, ActionResult,
CanonicalObservation, MutationProposal, MutationCommitRequest,
MutationCommitReceipt, DeliveryReceipt, ActionBatch, FactOwnerRegistration.

- ActionRequest binds: action ID, typed kind, exact arguments, literal shell
  form, repository snapshot, configuration digest, requested fidelity, batch
  ID, sequence position.
- EvidenceArtifact binds: stable anchors, witnesses, producer/version,
  semantics, freshness, coverage, ambiguity, omissions, configuration, raw
  fallback, registered FACT owner.
- DeliveryReceipt binds: selected action, pre-state, raw-result hash,
  transformation version, exact final observation bytes, provider
  request/response identity, immediate next action.

## 9. Exact engine lifecycle

`SELECTED → NORMALIZED → SNAPSHOT_BOUND → PREFLIGHTED → DECIDED →
EXECUTED | REPLACED | REWRITTEN | SUPPRESSED → POSTFLIGHTED → COMPILED →
JOINED → DISPATCHED → PROVIDER_ACCEPTED → DELIVERED → RESPONSE_COMMITTED →
NEXT_ACTION_BOUND → RECEIPT_FINAL`, with `FAILED` terminal from any state and
`FAIL_OPEN` recovery to literal execution. Executable in
`gt_engine/engine/transitions.py` with bounded exhaustive traversal +
Hypothesis stateful oracle + property witnesses (no action disappears;
fail-open preserves execution; PASS_THROUGH executes literally; SUPPRESS keeps
replay).

## 10. Five-decision semantics

- PASS_THROUGH: original action executes literally.
- AUGMENT: original action executes; deterministic evidence joins the same observation.
- REPLACE: certified deterministic operation substitutes for equivalent acquisition.
- REWRITE: execution/output shape changes while declared semantics are preserved.
- SUPPRESS: raw output omitted only under certified equivalence; retained in replay.

Locked policies implemented in `decide.py`: opaque/compound/mixed/stale/
unsupported pass through; literal views/grep stay literal; tests/builds retain
raw + augment; typed symbol/search replace only under declared completeness;
ambiguous/config-insensitive evidence never replaces source; unknown never
blocks; the engine never selects the next action.

## 11. Mutation proposal/commit protocol

`gt_engine/engine/mutation.py`: PROPOSE → bind snapshot token → compute proposed
bytes without mutation → deterministic preflight → precommit evidence → COMMIT
(same token) → CAS validation (StaleProposal / PreimageMismatch) → atomic write
set → capture committed bytes → postflight → canonical observation. Covers
multi-file create/modify/delete, renames (delete+create), rollback on partial
failure. Unavoidable boundary: raw shell writes remain literal + postflight-only.

## 12. Batch semantics

`classify_batch_barriers` in `runner.py`: any mutation/build/test/submit or
snapshot-sensitive replacement creates a barrier; later actions observe
preceding state changes; observations keep order; no extra model call;
unsupported batches fail open to literal execution.

## 13. Canonical observation compiler

`observe.py`: one observation per selected action with deterministic ordering
(action identity/decision → raw or declared replacement → FACT evidence →
anchors/witnesses → freshness → ambiguity/omissions → fallback notice →
receipt id) and evidence-delta projection (unchanged facts referenced, not
re-dumped). Raw remains exact where required and retained in replay.

## 14. Complete 129-row four-axis transition

Companion authority: `gt_finalstand/engine_129_transition.csv` (22 fields/row)
generated by `scripts/engine_129_audit.py`. Inventory: **12 ACQ + 48 CAP + 11
FACT + 58 PERF = 129 unique rows**; 17 DIRECT; all dispositions terminal
(BUILD/MODIFY/KEEP/REMOVE). The identity register is embedded below (core
columns; full per-row detail in the CSV):

| category | count | identities (disposition) |
|---|---|---|
| ACQ | 12 | graph_validity (MODIFY), structural_depth (MODIFY), resolution_honesty (KEEP), type_intelligence (MODIFY), lexical_FTS5 (MODIFY), body_retrieval (MODIFY), semantic_embedder (REMOVE), LSP (MODIFY), freshness_basis (KEEP), repo_scope (KEEP), cochange_history (MODIFY), determinism (KEEP) |
| CAP | 48 | GT_BRIEF_MINIMAL (REMOVE), GT_BRIEF_NATIVE (REMOVE), GT_CERT_DELIVERY (MODIFY), GT_CHANGE_SURFACE (MODIFY), GT_COMPLETION_CERT (MODIFY), GT_CONTENT_LEG (REMOVE), GT_CONTRACT_BILATERAL (MODIFY), GT_CONTRACT_MODE (REMOVE), GT_CONTRACT_NATIVE (REMOVE), GT_D7_RELATEDNESS (MODIFY), GT_EDIT_CHECK (MODIFY), GT_EDIT_OVERLAY (MODIFY), GT_EVIDENCE_NATIVE (REMOVE), GT_GATEWAY (REMOVE), GT_GATEWAY_EDIT_BRIDGES (REMOVE), GT_GATEWAY_NATIVE (REMOVE), GT_GLOBAL_ARBITER (MODIFY), GT_HYPOTHESIS (MODIFY), GT_INSEAM_METRICS (MODIFY), GT_L6_FRESH (MODIFY), GT_LANE_ENVELOPE (REMOVE), GT_LOC_RESLOT (MODIFY), GT_NUDGE_NATIVE (REMOVE), GT_OBLIGATION_FRESHNESS (MODIFY), GT_PATCH_DELTA (MODIFY), GT_POST_SEARCH (REMOVE), GT_POST_SEARCH_NATIVE (REMOVE), GT_REGISTRY_ENFORCE (KEEP), GT_SCOPE_NATIVE (REMOVE), GT_SEM_BODY (MODIFY), GT_SS_ACK_FORM (REMOVE), GT_SS_ACK_METRICS (REMOVE), GT_SS_ARBITER_V2 (REMOVE), GT_SS_COHERENCE_V2 (MODIFY), GT_SS_DEDUP2 (MODIFY), GT_SS_ELIGIBILITY (MODIFY), GT_SS_EXEC_TRUTH (MODIFY), GT_SS_LATE_DROP (MODIFY), GT_SS_NOVELTY (MODIFY), GT_SS_PROVENANCE (MODIFY), GT_SS_RECOVERY_V2 (MODIFY), GT_SS_SHADOW (KEEP), GT_SS_SUBMIT_RED (MODIFY), GT_STEER_NATIVE (REMOVE), GT_VERIFICATION_PLAN (MODIFY), GT_VERIFY_EXECUTE (MODIFY), GT_XSESSION_MEMORY (REMOVE), GT_XSESSION_RANKUP (REMOVE) |
| FACT | 11 | caller_contract (BUILD), cochange_prior (REMOVE), covering_red (MODIFY), def_partition (BUILD), localization (MODIFY), newfile_precedent (MODIFY), obligations (MODIFY), recovery (MODIFY), signature_delta (MODIFY), submit_refusal (BUILD), syntax_result (MODIFY) |
| PERF | 58 | gold_in_L1_top_k (KEEP), gold_rank (KEEP), files_to_gold_view (MODIFY), steps_to_gold_view (MODIFY), files_to_gold_edit (MODIFY), steps_to_gold_edit (MODIFY), localization_precision (KEEP), localization_recall (KEEP), false_file_rate (KEEP), exploration_ratio (MODIFY), gold_view_precision (KEEP), wasted_views (KEEP), navigation_directness (KEEP), self_localization_needed (KEEP), edit_attempts_per_gold (KEEP), rewrite_count (KEEP), compile_failures_after_edit (KEEP), edit_revert_rate (KEEP), first_edit_correctness (KEEP), patch_size (KEEP), patch_files (KEEP), contract_compliance_rate (KEEP), signature_changes_warned (MODIFY), p2p_regression_rate (KEEP), caller_breakage_count (KEEP), scope_coverage (KEEP), scope_excess (KEEP), multi_file_discovery (KEEP), scope_gap_files (KEEP), degenerate_loop_count (MODIFY), steps_in_loops (MODIFY), nudge_recovery_steps (MODIFY), coherence_collapse_count (MODIFY), stuck_duration (MODIFY), test_before_submit (KEEP), test_runs_total (MODIFY), test_edit_ratio (KEEP), obligation_test_rate (KEEP), verify_gap (KEEP), impact_rate (KEEP), per_tag_impact (KEEP), gt_tokens_injected (MODIFY), gt_tokens_per_pivot (MODIFY), nudge_compliance_rate (MODIFY), L1_followed_rate (MODIFY), contract_consulted_rate (MODIFY), obligation_completion_rate (KEEP), nudge_action_rate (MODIFY), scope_chain_followed (MODIFY), total_steps (KEEP), total_tokens_in (KEEP), total_tokens_out (KEEP), total_cost_usd (KEEP), cache_hit_rate (KEEP), tokens_per_gold_edit (KEEP), cost_per_resolved (KEEP), gt_token_overhead (MODIFY), wasted_token_rate (KEEP) |

Rules: only FACT-backed rows add model-visible deterministic evidence; ACQ
stays internal; CAP governs delivery and lineage; PERF stays passive (verified
by `compliance.perf_passivity`). Registered engine FACT owners:
`def_partition`, `syntax_result`, `covering_red`.

## 15. Frozen implementation plan (IE-00..IE-14)

- **IE-00** freeze manifests + baseline — DONE (`engine_manifest.json`).
- **IE-01** contracts + transition oracle (+TLA+/PlusCal pending) — DONE (executable oracle green).
- **IE-02** ENGINE posture + all-action normalization — DONE (runner seam).
- **IE-03** five-decision executor + literal PASS_THROUGH — DONE.
- **IE-04** canonical observation compiler — DONE.
- **IE-05** authoritative provider boundary + payload receipts — PARTIAL (delivery events recorded; full query-path hardening pending).
- **IE-06** read/search vertical slice — PARTIAL (typed producers dispatch; literal views stay literal).
- **IE-07** proposal/commit mutation slice — DONE (CAS, atomic, rollback).
- **IE-08** batches/verification/submit — DONE (barriers; submit gate).
- **IE-09** executable 129-row migration — DONE (audit + CSV green).
- **IE-10** passive PERF — DONE (compliance).
- **IE-11** remove advisory deps from ENGINE — DONE (import closure).
- **IE-12** replay/provenance/security — DONE (delivery replay check).
- **IE-13** provider-free certification — PARTIAL (engine battery green in Codespaces; full closeout via GHA).
- **IE-14** exactly ten ENGINE witness trials — IN PROGRESS (run `30735955619`).

## 16. Mechanical certificates and tests

Engine test inventory: contracts (12), transitions (12), decide (11), observe
(6), mutation (12), runner (12), 129-audit (8), compliance (11) = **84 green**
in the clean Codespace. Certificates: action lifecycle, mutation correctness,
canonical observations, capability preservation, performance isolation,
replay, GT-off parity (regression-checked), raw Bash parity, one model call
per iteration, exact raw-byte retention, stale-evidence rejection,
incomplete-suppression rejection, unknown fail-open, 129-row inventory
integrity, all-language certification (typed kinds certified only for supported
languages), advisory-sentinel absence from ENGINE, clean-machine
reproducibility. The executable model is the Python transition table + bounded
exhaustive traversal + Hypothesis stateful tests; a pinned TLA+/PlusCal model
is the independent concurrency check (to add in IE-13).

## 17. Ten-run ENGINE witness contract

Frozen tasks (exactly these ten): fix-code-vulnerability,
portfolio-optimization, modernize-scientific-stack, headless-terminal,
llm-inference-batching-scheduler, break-filter-js-from-html, write-compressor,
gpt2-codegolf, schemelike-metacircular-eval, cobol-modernization.

Baseline (frozen, NOT rerun): Mini-SWE 2.2.8, DeepSeek V4 Flash, temp 1.0,
step 100, **9 solved + gpt2-codegolf failed**. Comparison via
`scripts/engine_witness_compare.py`: reward, calls, actions, pre-edit
exploration, raw bytes, GT bytes, total visible bytes, all five decision
counts, fallback incidents. No general efficacy claim from ten tasks.

## 18. Security, replay, rollback, limitations

Content-addressed repository/artifact state; exact provider-visible byte
hashing; event-sourced replay; secret redaction in delivery events
(`compliance.verify_engine_delivery_events`); GT-off rollback = `MiniSweAgent`
(--gt-off). Kill switches: global + per-capability. Fail-open at every engine
fault. Honest limitations: incomplete static analysis, dynamic-language
uncertainty, configuration explosion, shell opacity, runtime-only behavior,
generated artifacts, and the inability to prove solve-rate improvement before
the matched trials finish.

## 19. Next-session runbook

1. Read this document before other project prose.
2. Refresh repository manifests; detect drift (`git fetch`, `engine_manifest.json`).
3. Revalidate code citations against the recorded commit.
4. Re-run `scripts/engine_129_audit.py`, the engine test battery (incl. `test_engine_gates.py`), and `scripts/validate_gt_finalstand.py`.
5. Finish IE-13 (provider-free closeout) before any new paid run. (Green: `30738422522`.)
6. Collect round-2 witness run; run `engine_trajectory_proof.py` + `engine_witness_compare.py` + `engine_delta_compare.py`; record receipts.
7. Update this document's status from receipts, never from assertions.

## Round-2 readiness (what changed since witness round 1)

Round 1 (`30736459512`) proved the on-time mechanism but an empty payload:
417/417 actions cross the engine boundary, 411/411 observations on time, but
**0/17 features delivered** (0 `<gt-fact>`, 0 typed actions; all observations
raw-only). Full detail: `gt_finalstand/ENGINE_GAP_ANALYSIS.md`. Fixes + gates
(section 18/16) landed and the provider-free gate re-passed:

- **Neutral in-band labels**: `render()` emits raw first (byte-exact,
  unwrapped) + a neutral `<result>/<fact>` block; no `<gt-engine>`/`<gt-fact>`/
  `GT_` sentinels in model bytes.
- **Real facts wired**: bash path now runs deterministic producers —
  `syntax_result` (ast.parse on changed `.py`) and `covering_red`
  (execution-specific test/build outcome); PASS_THROUGH → AUGMENT when facts
  attach; bash submits cross the submit gate.
- **Journal fixed + gated**: the `engine_delivery` append no longer overrides
  `ExternalStateStore`'s forced `gt.event.v1` (research_valid was false).
- **Landmines gated**: missing `os`/`Path` imports and porcelain `" M"` parsing
  (would have crashed round 2) — caught by `tests/test_engine_gates.py`.
- Round-2 witness: ten frozen tasks, ref `inline-engine`, parallel 10.

Prohibitions: no committing/pushing this document; no modifying advisory user
work; no baseline reruns; no additional experimental arms; no completion claim
without model-visible byte receipts.

---

SHA-256 digest: `7FC895E8F9E382110CC19A57217A1F347FAAC3A0DFEFF9FB45691B1F0CE5B864` (updated after witness round 1 + fixes + gates; branch `inline-engine`).

