# HANDOFF — 2026-09-13 — Benchmark Readiness Push

State at handoff: **fixes landed + partially verified, defect inventory complete, 2 implementation tracks + 4 audit sweeps pending (subagent rate limits killed them — prompts preserved below verbatim for re-dispatch).**

## Governing user directives (still in force)

- GT is the main engine: it owns perception/evidence/verification/planning/steering; Mini-SWE is the execution layer (provider calls, reasoning, tools, submission). Never revert this.
- Full benchmark rail: 5400s task + 1500s GT overhead = 6900s total (~115 min; agent budget 6660s ≈ 111 min). NO artificial graph-build limits or anything that weakens us.
- Benchmark pivot: use **SWE-bench-Live Lite** (small wall-clock) for the paid gate — 2 small tasks — not DeepSWE for the next smoke.
- Every paid dispatch needs **explicit fresh user approval** in-session.
- Never run GT-off; use frozen/online baselines.
- Adversarial standard: prove the build is strong and correct, not just plausible. Fail-closed; never convert degraded → working in a report.

## Landed work (uncommitted in D:\gt-context-plan working copy)

### A. Amend-failure escalation + stderr receipts — DONE, tests green
- `gt_engine/indexer.py`: `_amend_failure_reason(process_result)` builds `amend_failed:{code}:exit={n}:stderr={bounded tail}`; used at the single process-failure return in `_ensure_index_incremental_unlocked` (~line 1918). No more blind `GT_INDEX_PROCESS_FAILED` receipts.
- `gt_engine/miniswe_integration.py`: `self._amend_failure_streak` dict (init ~line 469), class const `AMEND_FAILURE_ESCALATION_STREAK = 2` (~2555), refusal branch in `_amend_graph_inline` (~2622-2651): on consecutive `amend_failed:` on same parent → journals `graph_amend_escalated` → calls `_recovery_build_inline(phase=phase)`. Streak resets on success/parent change. Sync path `_sync_amend_graph` intentionally untouched (still defers; boundary is where escalation lives). Note: escalation is prefix-matched on `amend_failed:` — includes timeout/OOM codes (deliberate: deterministic failure on immutable bytes can't self-heal; bound prevents storming).
- `tests/test_index_incremental.py`: +282 lines, 10 tests (naming, no-stderr degrade, escalation at bound, no-escalate reasons parametrized, parent-change reset, success reset). 42 pass, 1 Linux-skip.

### B. Capability reporter — DONE, tests green
- `gt_engine/gt_session.py` `_mandatory_capability_rows` (~1695-2045):
  - **dense**: rows classified measurement vs refusal. `query_ready=True` → WORKING; `query_ready=False` with measured reason → DEGRADED; `query_ready=False` + reason endswith `graph_snapshot_not_current` → pure refusal (sets `dense_refused`, never touches state). Only-refusals → DEGRADED `dense_index_never_queryable`. Newest measurement wins.
  - **lsp**: terminal selection now keyed to newest ADOPTED graph — collects `graph_publication` rows, joins last publication's `graph_sha256` to each published terminal's receipt `output_graph_sha256`; evaluates the terminal that produced the adopted graph. Fallbacks preserved. New `_terminal_receipt(row)` helper with per-blob cache; `_promotion_yield` delegates.
- `tests/test_gt_session.py`: +10 tests; the exact paid-run shape (pub cb98 → terminal published out 3190f → pub 3190f → terminal `no_edge_mutations` on 3190f) now asserts WORKING `terminal_succeeded:published:7_edges`. 75/75 pass.

### C. Wheel LSP parser-ownership fix — LANDED, mechanism verified, upstream commit pending wire-through
- `vendor/groundtruth_mcp-1.0.0-py3-none-any.whl`: `groundtruth/resolve.py` +25 lines — in the LSP type-enrichment phase, each `UPDATE nodes SET signature/return_type` now also `DELETE FROM parser_node_inventory WHERE node_id=?` in the same transaction (guarded by `sqlite_master` probe for older graphs).
- **Verified complete**: edge UPDATEs (resolution_method='lsp', confidence, target_id, trust_tier) hit CALLS edges which are NOT parser-owned — `parsedEdgeKinds()` = CONTAINS + 9 taxonomy kinds only (vendor/gt-index-src/internal/store/parsed_facts.go:29-47). Node UPDATE at ~883 mutates non-keyed columns. The 23 production-mutated nodes verified: evicting their inventory rows → Go amend check misses → insert-fresh path → stale row swept. Edges/properties/assertions reconcile via `reconcileParsedFacts` digest-check (parsed_facts.go:116-119) but only for inventoried rows (JOIN) — none of the mutated kinds qualify.
- `vendor/GROUNDTRUTH_WHEEL_SOURCE.txt` + `config/deepswe_product_bundle_v1.json`: sha256 updated to `dd02fcb19c04f5c20e172a8f46b9a6a9e7a97886b35d45aa5c32ae813037c337` with honest vendored-patch documentation.
- **UPSTREAM COMMIT MADE (unpushed)**: `D:\still_here\groundtruth` branch `wheel-patch-lsp-inventory`, commit `6b4c923dad7657609ec86a05771e0aefd0ce84c7`, tree `d5f6ebc4516ae08601530b3dd73f7c2122d2e246` — same +25-line patch on top of pinned `e85b9d75` (verified byte-identical resolve.py before patching).
- **Clean wheel rebuild DONE**: built from `git archive 6b4c923` (LF endings — a Windows working-tree `pip wheel` produces CRLF contamination, 68 members differ; archive build is clean). Vendored wheel sha `dd02fcb19c04f5c20e172a8f46b9a6a9e7a97886b35d45aa5c32ae813037c337` == archive rebuild; differs from pre-patch wheel ONLY in resolve.py + RECORD.
- **Bundle updated**: `groundtruth.source_commit`=6b4c923, `source_tree`=d5f6ebc, `wheel_sha256`=dd02fcb1; lineage `ancestry_path` appended (133 entries), `post_certification_changed_paths` unchanged (745, resolve.py already in set — verified identical), `attestation_digest_sha256` recomputed (30056b31…). `producer_build.source_commit` deliberately kept at e85b9d75 (binary sha 416fce39 unchanged — honest build provenance).
- **verify_wheel_source PASSES** vs LF source tree at 6b4c923: 323 files, changed=[]/missing=[]/extra=[]. (Local CRLF checkouts will false-FAIL — CI checks out LF.)
- **REMAINING provenance requirement**: gate `groundtruth_provenance.py:313-319` needs a `gt.review_packet.v1` with `head_sha == 6b4c923` in lineage `review_packets` (owner attestation process, `inbox/HAR-83/` packets) AND the commit pushed to `github.com/harneet2512/groundtruth` (CI fetches it) — **needs user approval for push; packet is owner-side**.

### D. SWE-bench-Live Lite adapter — DONE, statically verified
- `swelive-bench/` (new, untracked): `tasks/cyclotruc__gitingest-94/` + `tasks/dynaconf__dynaconf-1241/` each with `task.toml`, `instruction.md`, `image.lock.json`, `environment/Dockerfile`, `tests/{Dockerfile,test.sh,run_tests.sh,grade.py,spec.json,test_patch.diff}`; plus `manifest.json`, `README.md`, `.gitattributes`.
- Images verified pullable on DockerHub: `starryzhang/sweb.eval.x86_64.cyclotruc_1776_gitingest-94` (sha256:0698979e…, 454MB), `starryzhang/sweb.eval.x86_64.dynaconf_1776_dynaconf-1241` (sha256:c5930010…, 624MB). gitingest verifier needs github.com egress (`[verifier].network_mode="public"`); dynaconf fully no-network.
- Pier 0.3.1 contract mapped: `tests/` dir = verifier-image build context (NEVER agent-visible → test_patch/F2P/P2P sealed), reward via `/logs/verifier/reward.txt|json` (exit code ignored), `[[verifier.collect]]` runs in agent container post-agent. Official SWE-Live grading replicated verbatim (`git apply --whitespace=nowarn` test_patch → test_cmds → parse_log_pytest → F2P⊆passed ∧ no F2P/P2P failing).
- agent timeout_sec=1800 (small-clock per user). task_config_sha256 matches shipped bytes; TaskConfig parses under 0.3.1; TaskPaths.is_valid()=True.
- Open risks (ranked in swelive-bench/README.md): /testbed layout unverified (no docker on this host — first real run must verify), gitingest github.com egress, local pier 0.2.0 vs workflow 0.3.1 drift, `--maxfail=1` masking in dynaconf test_cmds.

### E. Full-journal audit — DONE, 6 new defects
See "Complete defect inventory" below. Verified-clean: provider integrity (119 admissions/0 denials/$0.169/83% cache), 116/116 executions, all deliveries landed, context assembly healthy, 336 predicate receipts GREEN, journal hash-chain intact, zero OOM/resource events.

## Complete defect inventory

### Fixed (this session, in working copy)
| # | Defect | Fix location | Verify |
|---|---|---|---|
| P0-1 | LSP-enriched graphs unamendable (resolve.py mutates parser-owned nodes.signature/return_type → DeepEqual fails → 601/601 amend refusals, graph ~95% stale) | wheel resolve.py +25 | mechanism proven on artifact DB; live proof pending paid run |
| P0-2 | Deterministic amend failure never escalates (loops forever, zero rebuilds) | miniswe_integration.py streak-2 → recovery build | 10 tests |
| P0-3 | Blind refusal receipts (stderr/exit dropped) | indexer.py `_amend_failure_reason` | tests |
| P0-4 | LSP reporter reads newest terminal not the adopted-graph producer | gt_session.py publication↔output_graph_sha256 join | 10 tests |
| P0-5 | Dense reporter last-row-wins on transient refusals | gt_session.py measurement/refusal split | 10 tests |
| (prior) | lsp_source_input_mismatch — manifest walked fs blind vs git-visible freeze | 56d29e69 `git_visible_paths` shared | proven in paid run (scheduled+published) |

### Found by audit — NOT yet fixed (F-series)
| # | Defect | Evidence | Severity |
|---|---|---|---|
| F1 | `plan_gate_decision accepted:true reason:no_blocking_evidence completion_proven:false` → receipt mints `terminal:submitted_verified`, `contract_shipped:true` — overstates unproven completion (30/31 rows unverified, baseline unknown) | seq 5555-5565, gt-run.json | evidence-integrity — benchmark attestation reads `terminal` |
| F2 | `plan_check_bound` binds with `selected_test_ids:[]`, `test_source_paths:[]`, `protocol:""`, `environment_sha256:""` → baseline recheck can never establish test-identity conservation (`passed_delta:+35` mixes agent-written tests) | seq 259-288, 5555 | evidence-integrity |
| F3 | `obligation_reverified` ×36 all `commands_run:0` — claims reverify, runs nothing | seq 520-521 pattern ×36 | evidence-integrity (name lies) |
| F4 | `verification_plan` (92×) + `cochange` (56×) recipes `empty_render` 100% — dead context lanes; model never got either class | delivery_recipe_unresolved ×148 | capability gap (lost context, honest) |
| F5 | `change_surface`/`newfile_precedent` producer `registry_allowed:false` all 36 edits — registered never permitted | producer_invocation ×36 | capability gap (registry/config) |
| F6 | `submission_patch_observed` only at seq 66 (start, empty) — no terminal row; journal can't attest shipped patch | events.jsonl | observability gap |
| F7 | Over-broad invalidation: `changes/460.enhancement` changelog edit discarded 11 predicates | seq ~5239 epoch 36 | secondary defect |
| F8 | 2× ~4min FormatError stalls (VERIFY phase, empty `error` fields) | seq 223-225, 475-477 | provider/format fragility, thin evidence |

### Real environmental facts (honest, correctly receipted — not defects)
- JS LSP leg can't init in Python task images (no `node_modules/typescript`, no `tsserver.path`) — 63 candidates env-blocked. Surface as named bounded-coverage, not failure.
- Process layer empty: `no_certified_path_from_any_witnessed_target` (manifest `derived_process_state`).

## Pending work — ordered

1. **Wheel provenance final step (owner-side)**: push `6b4c923` to github.com/harneet2512/groundtruth + produce review packet `head_sha=6b4c923` in `inbox/HAR-83/` + add it to lineage `review_packets` + recompute digest. Everything else done (clean wheel, correspondence PASS, bundle fields updated).
2. **F-series fixes** (agent F prompt + agent G prompt below — never landed; subagents died on rate limits).
3. **Sweeps H/I/J/K** (prompts below — died on rate limits; J's output is required before SWE-Live workflow wiring).
4. **SWE-Live workflow**: parameterize or duplicate `deepswe_gt_harness_product_p0731.yaml` for `swelive-bench/tasks` (J sweep enumerates every pinned input).
5. **Commit all landed work** (gt-context-plan): A/B/C/D changes — separate logical commits. NOTE: docs/*.md are conventionally committed in this repo (prior handoffs are tracked).
6. **Full regression suite** on combined state.
7. **Provider-free gates** on the new SHA (`installed_rehearsal.yml` + product acceptance workflow).
8. **Paid gate**: 2 SWE-Live small tasks (gitingest-94, dynaconf-1241) — **explicit user approval required**. First run must verify /testbed layout assumptions.
9. **Cohort decision** only after gate validates.
10. **Push upstream commit** 6b4c923 to github.com/harneet2512/groundtruth — needs user approval (CI provenance requires it).

## Subagent prompts to re-dispatch (verbatim — all context included)

### Agent F — plan/gate honesty (subagent_general)
Scope: gt_engine/persistent_plan/*, persistent_execution_state.py, miniswe_runtime.py, submit-decision region of miniswe_integration.py only. Do NOT touch retrieval/delivery_budget/registry code, vendor/, config/, swelive-bench/, or gt_session.py's `_mandatory_capability_rows`.
Fixes: F1 honest terminal naming under completion_proven:false (do NOT tighten the gate); F2 populate selected_test_ids/test_source_paths/protocol/environment_sha256 at bind (or honest `test_identity_basis` if structurally unavailable); F3 obligation_reverified — execute pending checks OR rename state honestly (check journal consumers before renaming event kinds); F6 emit terminal submission_patch_observed at submit with real bytes/digest/path; F7 narrow invalidation scope only for provably-wrong non-code-path case.
Tests RED-first. Preserve dense honest comments. Journal is append-only — prefer new honest states over renaming parsed kinds.

### Agent G — dead delivery lanes (subagent_general)
Scope: retrieval.py, delivery_budget.py, scoped_merge.py, contract_embeddings.py, graph_coordinator.py, churn_governor.py, engine_state.py, miniswe_evidence.py, repository_identity.py, runtime_observation.py, task_contract.py, run_diagnostics.py + tests/. NOT miniswe_integration.py, gt_session.py, miniswe_runtime.py, persistent_plan/*, persistent_execution_state.py, indexer.py, vendor/, config/, swelive-bench/. If a fix must live in a forbidden file, implement what you can outside + report the exact handoff change.
Fixes: F4 — why do verification_plan + cochange recipes `empty_render` 148× (missing inputs / renderer bug / unbound key `cochange-unbound`)? Fix render, or abstain-once-with-reason instead of spamming; F5 — why is `change_surface`/`newfile_precedent` `registry_allowed:false` (flag off? manifest gap? deliberately disabled → journal `disabled:<reason>`).

### Agent I — cross-task/language sweep (subagent_explore)
Cohort composition (config/deepswe_product_bundle_v1.json tasks + deepswe-bench/tasks — languages/repo sizes); JS/TS-primary LSP path (does env-block become dominant failure on TS tasks; does `all_promotable_languages_attempted:false` interact correctly with new attestation); large-repo economics (arktype was big TS — rebuild frequency under streak-2 escalation + churn governor; loop-detector for pathological rebuild cycles, NOT a build limit); non-git workspaces; thin/empty graphs; dense model staging under no-network; non-Python LSP node mutations (does resolve.py enrichment mutate for JS/TS/Java/Go/Rust — the inventory eviction is table-name based, confirm no per-language inventory tables).

### Agent J — integration surface (subagent_explore) — REQUIRED before SWE-Live workflow work
Every DeepSWE-pinned input in .github/workflows/*.yml for swelive-bench/tasks (parameterize vs duplicate); config/deepswe_product_bundle_v1.json field roles vs swelive-bench/manifest.json gaps; which scripts the paid workflow invokes + their DeepSWE assumptions; verifier reward binding (DeepSWE shape vs /logs/verifier/reward.txt); whether deepswe-bench external clone is needed for non-task files; provider_route.v1.json compatibility; timeout plumbing (agent 1800s vs hardcoded 5400/6900/1500 rail — grep); artifact collection naming (model.patch compatible?).

### Agent H — verdict-overstatement sweep (subagent_explore)
Disease class: success-named verdicts minted from absence-of-failure or last-row-wins. Sweep gt_engine/*.py, scripts/*.py, gt_harness/*.py for: journal-parse last-row-wins loops; verified/succeeded/proven/ready/complete/shipped/conserved/sealed verdicts from `not failed`/empty-failure-list; status:ok on skipped/refused/deferred paths; *_verified fields derived from row-existence not row-content; verdict laundering upward without re-check. Read end-to-end: run_diagnostics.py, miniswe_runtime.py (non-submit fields), engine_state.py, miniswe_evidence.py, attest_deepswe.py, gt_audit.py, gt_installed_rehearsal.py, runtime_receipts.py. Check for OTHER measurement+refusal-mixed journal streams like dense_index_ready.

### Agent K — artifact forensic remainder (subagent_explore)
Artifact: D:\tmp\gate-one-34766499875\...\aiomonitor-task-snapshots-diff__mtjTgnw\ — full diagnostics.json + gt-run.json reads (every field vs journal truth), trajectory structural pass (deliveries actually model-facing? FormatError response bodies), recovery store, gt-worktree.patch vs artifacts/model.patch identity, unexamined files enumeration, provider_responses count vs 118 journal rows, manifest derivation-chain consistency, wall-clock budget reconstruction (does 1500s GT overhead hold?).

## Key file/code pointers

- Amend invocation + escalation: `gt_engine/miniswe_integration.py` `_amend_graph_inline` ~2540-2651, `_sync_amend_graph` ~2047-2127, `_recovery_build_inline` — grep it
- Producer diagnostics: `gt_engine/indexer.py` `_run_index_bounded` ~840-904, `_ensure_index_incremental_unlocked` ~1776-1960, `_amend_failure_reason` ~1776
- Capability reporter: `gt_engine/gt_session.py` `_mandatory_capability_rows` ~1695-2045, `_terminal_receipt`, `_promotion_yield` ~1724
- Producer inventory checks: `vendor/gt-index-src/internal/store/batch_structure.go` (ReplaceParsedStructure: wipe at :109, node DeepEqual at :140-141, global sweeps :153-164), `internal/store/parsed_facts.go` (reconcileParsedFacts digest check :116-119, parsedEdgeKinds :29-31)
- Wheel patch site: `groundtruth/resolve.py` enrichment phase ~1903-2148 (in wheel + upstream commit 6b4c923)
- Paid run artifact: `D:\tmp\gate-one-34766499875\gt-harness-deepswe20-task-1-34766499875\deepswe-gt-harness-34766499875-aiomonitor-task-snapshots-diff\aiomonitor-task-snapshots-diff__mtjTgnw\` (agent/events.jsonl = GT journal 5565 events; enrichments/lsp-rf20zm6b/graph.db = LSP-derived adopted graph; revisions/55ac0bc1.../graph.db = native parent)
- SWE-Live dataset: `/tmp/swelive_lite.json` (300 rows); images `starryzhang/sweb.eval.x86_64.<id with __→_1776_>`
- Built gt-index binary for local repro: `/tmp/gt-index.exe` (fails executable_sha check vs 416fce39 — use for mechanism tests only)
- Original wheel backup: `/tmp/orig.whl`; patched-source rebuild (CRLF-contaminated): `/tmp/newwheel/`

## Run IDs / SHAs

- Original incident: workflow 34701523365 (arktype rebuild storm, 82.4min)
- Fix for source-domain mismatch: 56d29e69 (HEAD of gt-context-plan at handoff)
- Provider-free gates on 56d29e69: acceptance 34765941190 GREEN, rehearsal 34765942258 GREEN
- Paid gate-one: 34766499875 on 56d29e69 — task SOLVED, attestation failed (defects above)
- Upstream wheel-patch commit: 6b4c923dad7657609ec86a05771e0aefd0ce84c7 (D:\still_here\groundtruth, branch wheel-patch-lsp-inventory, UNPUSHED)
- Route: OpenRouter deepseek/deepseek-v4-flash-0731, relace-only, fallbacks disabled; credential identifier `final_openrouter_musecontributor` in cloud_access.md (never print value)
- Linear record: HAR-83 continuing doc (keep updated after material changes)

## Readiness verdict at handoff

- Task-solving path: proven (paid solve).
- GT evidence production: partially proven (graph/LSP/dense all worked; honesty layer was the failure).
- Graph amend chain: fixed in code (mechanism-verified), NOT yet proven live.
- Attestation layer: fixed in code (test-verified), NOT yet proven live.
- Evidence-plane honesty (F-series): defects found, fixes not started.
- SWE-Live path: adapter built + statically verified; workflow missing; in-image layout unverified.
- **NOT benchmark-ready** until: clean wheel rebuild + provenance green, F-series honesty fixes landed, H/I/J/K sweeps complete, regression suite green, provider-free gates green on new SHA, paid small-task gate validates.
