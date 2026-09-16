# Offline ability audit — gateway producers → admission → provider wire (D1, D5)

- **Date:** 2026-09-16 (local run, this working tree)
- **Scope:** `localization`, `GT_LOC_RESLOT`, `caller_contract`, `cochange_prior`,
  `def_partition`, `newfile_precedent`, `GT_CHANGE_SURFACE`, `signature_delta`,
  `GT_PATCH_DELTA`, `select_catalog`; scenarios D1 (localization → view → caller
  → signature edit → affected-caller evidence) and D5 (simultaneous candidates →
  budget refusal → retry → exactly one exposure).
- **Evidence:** `tests/test_audit_producers.py` — **28 tests, all passing**
  (provider-free). Every integrated case runs the real shipping seam:
  `install_runtime_hooks` → `GTSession.before_model`/`admit_decision_packet` →
  `admit_model_visible_delivery` → exact prepared message bytes → recording
  transport → `bind_provider_payload`/`bind_provider_response`, with journal
  rows (`producer_invocation`, `delivery_prepared`, `delivery_refused`,
  `evidence_delivery`/`context_addition_delivery`, `provider_delivery`,
  `provider_response`, `delivery_recipe_unresolved`) joined to the exact bytes
  the provider received.
- **Fixtures:** real temporary repositories indexed by the real producer binary
  `C:\Users\Lenovo\.groundtruth\bin\gt-index.exe` (SHA-256
  `3248b6e9…4cb4d3`), sealed like a benchmark-bound build (manifest +
  index-resource + lsp-promotion sidecars over real graph bytes). Commands
  execute through Git Bash against the real worktree; provider transport is a
  deterministic recording fake or a `LitellmModel` with monkeypatched
  `litellm.completion`. No provider call, benchmark dispatch, GT-off rerun or
  GCP action was performed.
- **Product diff from this slice:** one owner-local fix in
  `gt_engine/miniswe_integration.py` (`task_start_localization`, D-α below).
  All other worktree modifications belong to parallel audit workers and were
  not touched.

## Ability verdicts

| Ability | Verdict | Evidence |
|---|---|---|
| `localization` (task-start ranked answer) | **FIXED — WORKING OFFLINE** | `test_localization_without_catalog_ships_and_binds` — delivered (`evidence_delivery`), bound to exactly one `provider_delivery`, confirmed by the `provider_response` row's `delivery_ids`; rendered `[GT_EVIDENCE:localization]` bytes contain `src/service.py`. Was silently dead on every catalog-enabled run until D-α fix. |
| `GT_LOC_RESLOT` (drift re-rank) | **WORKING OFFLINE** | `test_loc_reslot_rerenders_on_search_drift` — post-delivery search drift re-queues the recipe and its admission outcome is journaled. Reachable again only because D-α restored the pending markers the catalog peek consumed. |
| `caller_contract` — view boundary | **PARTIAL** | Producer correct: `test_caller_contract_view_producer_direct` — repo-relative `viewed_files` (the POSIX-seam shape) yields FACT-tier callers `src/util.py`, `src/other.py`, leaky `tests/` filtered out. Shipping seam on Windows dev host abstains typed `no_verified_caller_contract` (`test_caller_contract_view_windows_seam_seam_abstains_closed`): `_viewed_files` resolves OS-absolute paths, the wheel's `_to_repo_rel` relativizes only `/`-prefixed paths. Correct-or-quiet — never fabricates a caller set. Linux task image emits `/testbed/…` paths so the gap is dev-host only; installed-container recheck still required → **UNPROVEN there**. |
| `caller_contract` — edit boundary (`caller_break`) | **BROKEN (D-β, cross-boundary)** | Producers return the fact when fed (`test_signature_edit_producers_direct_pipeline`: `signature_mismatch` + `caller_break` naming `src/util.py`, test path excluded). On the shipping edit turn every event arrives with `edit_before_after=None` → typed abstention `no_edited_before_after_pair` — see defect spec. |
| `cochange_prior` | **WORKING OFFLINE** | `test_cochange_history_unavailable_is_typed_not_negative` — empty table journals `delivery_recipe_unresolved` reason `cochange_history_unavailable`, never "no partners". `test_cochange_prior_delivers_when_history_exists` — populated table delivers `cochange_partner` bound to the next request, rendered with `status=prior_not_resolution` + per-row `provenance=cochanges(file_a=…,file_b=…)`. Ghost-partner mutation confirms honesty labeling (`test_cochange_ghost_partner_is_labeled_prior_not_existence`). |
| `def_partition` | **WORKING OFFLINE** | Ambiguous two-definition search: `def_ref_partition` `returned_fact` at `gateway.search.def_ref_partition`, dose delivered and request-bound (`test_def_partition_on_ambiguous_hit`). Exact-hit search: producer not entered / silent, no delivery (`test_def_partition_not_reached_on_exact_hit`). |
| `newfile_precedent` | **WORKING OFFLINE** | `test_file_creation_delivers_newfile_precedent` — `cat > src/fmt.py` delivers exactly one `new_file_destination` dose naming `src/fmt.py` + `inspect=` sibling precedents, bound to the next request. `test_modify_existing_file_no_newfile_delivery` — modify mints nothing. |
| `GT_CHANGE_SURFACE` | **BROKEN on edit path (D-β collateral) — PARTIAL** | The `gateway.edit.change_surface` producer is dispatched on edit turns but skips `not_file_creation` even for a real creation, because `_event_creates_new_file()` needs the before/after pair that D-β drops. Flag `GT_CS_EDIT_TRIGGER=1` confirmed in the production env fan-out (`test_profile_env_fans_out_production_producer_flags`). Missing-role/registration rows on a fed event: **UNPROVEN** here. |
| `signature_delta` / `GT_PATCH_DELTA` | **BROKEN (D-β, cross-boundary)** | Producer correct when fed (direct pipeline control). On shipping path always `no_edit_before_after` — defect spec below. |
| `select_catalog` | **WORKING OFFLINE** | `test_select_catalog_valid_selection_litellm` — real `LitellmModel`/`_parse_actions` seam: offer → valid selection → `selection_accepted` lifecycle → `GT_SELECT_CATALOG_RESULT` reaches the next agent request. `test_select_catalog_malformed_selection_abstains` — obsolete id journaled attempted-vs-selected honestly, no result minted. `test_catalog_transport_failure_abstains` — wire failure abstains typed, nothing fabricated. |
| Scenario **D1** | **PARTIAL** — blocked at last leg by D-β | `test_d1_localization_to_caller_evidence` — localization ships; `cat` reaches `gateway.view.caller_contract_view`; edit reaches `gateway.edit.*`; affected-caller delivery withheld by D-β (typed abstention asserted; the `else` branch pins the post-fix contract). |
| Scenario **D5** | **WORKING OFFLINE** | `test_d5_simultaneous_candidates_budget_refusal_retry` — 4,036-byte sealed candidate refused `delivery_byte_ceiling` on `delivery_refused` naming `dedup_key=d5:big`; the small candidate delivers + binds; refusal consumed no dedup/chain state so the retry refuses typed again; `d5:big` never appears in any `provider_delivery.delivery_ids`. |

## Defect specifications

### D-α — FIXED (owner-local): catalog bootstrap peek consumed the localization resolution

- **Faulty boundary:** `gt_engine/miniswe_integration.py` `task_start_localization(commit=False)` — peek path.
- **Root cause:** `prepare_select_catalog` (`gt_session.py:390`) and the shadow
  path (`gt_session.py:878`) call `task_start_localization(commit=False)` to
  read the resolved localization without shipping it. Every resolve runs
  `_render_localization_now`, which records `_localization_render_revision`
  and `_localization_drift_at_render` unconditionally (`miniswe_integration.py:5805-5806`).
  A peek therefore left `localization_resolution_pending()` (`:5889-5903`)
  reporting False forever while `_task_start_shipped` could never latch —
  `before_model` (`gt_session.py:876`) never queued the localization recipe
  again. **Any run whose catalog was merely prepared (the production default)
  silently lost the task-start localization AND every later drift re-rank.**
- **Expected vs actual:** catalog build must not consume the agent-facing
  resolution. Actual: first `query` journaled `select_catalog` +
  `context_contract` deliveries and never `localization`.
- **Fix:** the `commit=False` branch now resolves, then restores both markers
  before returning (`miniswe_integration.py:5905-5931`). `commit=True`
  semantics unchanged.
- **Regression test:** `test_localization_survives_catalog_bootstrap` — failed
  RED before the fix (deliveries were `select_catalog` + `context_contract`
  only), green after; control `test_localization_without_catalog_ships_and_binds`
  unchanged.
- **Recheck:** none beyond the suite — the fix is seam-internal and
  deterministic.

### D-β — CONFIRMED, cross-boundary, UNFIXED HERE: `_run_evidence` drops `edit_before_after` before `classify_event`

- **Faulty boundary:** `gt_engine/miniswe_runtime.py:737-748` (owned by another
  audit worker — not edited in this slice).
- **Shipping call path:** `execute_actions` wrapper → workspace
  `capture_workspace`/`diff_workspace` computes the transaction
  (`miniswe_runtime.py:1987-2039`) → `edit_before_after` dict built
  (`:2031-2040`) → passed into `_run_evidence` (`:2152-2154`, parameter at
  `:633`) → **dropped**: the `classify_event` call at `:737-748` forwards
  `changed_files`, `viewed_files`, `covering`, `test_outcome`,
  `output_artifact` — but not `edit_before_after`, though `classify_event`
  accepts it (`gt_engine/miniswe_evidence.py:267`) and forwards it to
  `normalize_event` (`:299`).
- **Observed behavior (journal):** `edit_result` still fires
  (`_derive_semantic_events` needs only `changed_files`), then every
  edit-boundary producer starves typed:
  - `patch_delta` @ `gateway.edit.patch_delta` → `no_edit_before_after`
  - `caller_contract` @ `gateway.edit.caller_contract` → `no_edited_before_after_pair`
  - `change_surface` @ `gateway.edit.change_surface` → dispatch-skip
    `not_file_creation` even on a real `cat > newfile` creation
  - Result: `signature_delta`/`GT_PATCH_DELTA`/`caller_break`/
    `GT_CHANGE_SURFACE`-on-edit are reachable, producer-correct, and
    **deliver nothing, ever** — matching the measured "signature_delta
    eligible on 15 signature entries across 19 edit transactions, delivered
    nothing, ever" note at `miniswe_runtime.py:650-652`.
- **Producer-side control:** `test_signature_edit_producers_direct_pipeline`
  feeds the same repo+graph with `edit_before_after` supplied →
  `signature_mismatch` ("`helper() call passes 2 positional arg(s); tokenize()
  now takes 1-1`") and `caller_break` ("`tokenize() signature changed — 2
  caller(s) in 2 file(s)`") both produced; the leaky `tests/` path is excluded.
- **Correction at existing owner:** add `edit_before_after=edit_before_after`
  to the `classify_event` call at `miniswe_runtime.py:737`. One line; no
  signature changes needed anywhere.
- **Regression tests (already written, will flip to the strict assertions on
  fix):** `test_signature_edit_seam_drops_edit_before_after` pins the typed
  abstention + zero fabricated deliveries today; the `else` branch of
  `test_d1_localization_to_caller_evidence` and the direct-pipeline control
  pin the post-fix contract (delivery + exactly-one-request binding).
- **Recheck required:** rerun `tests/test_audit_producers.py` — the three
  tests above plus `test_body_only_edit_abstains_signature_producers` (which
  must still abstain, now for a *content* reason).

### Pre-existing documented gap (not new): Windows dev seam path shape

`_viewed_files` resolves to OS-absolute `D:\…` paths; the certified wheel's
`_to_repo_rel` only relativizes `/`-prefixed POSIX paths, so the view producer
abstains `no_verified_caller_contract` on Windows. Fails closed; on the Linux
task image the seam emits `/testbed/…` and relativizes correctly. Owners:
`miniswe_runtime._viewed_files` (emit repo-relative) or the wheel's
`_to_repo_rel` (accept OS paths). Pinned by
`test_caller_contract_view_windows_seam_abstains_closed`. **Installed Linux
recheck still required.**

## Journal/audit findings worth noting

- `producer_invocation` rows carry `invocation_site`, `outcome`
  (`entered`/`returned_fact`/`returned_nothing`/`not_entered`),
  `abstention_reasons`, `skip_reason`, `registry_allowed` — the
  eligibility-vs-delivery gap is auditable per ability from the run journal
  alone (`_producer_invocation_record`, `miniswe_integration.py:5261`).
- `delivery_prepared` ≠ exposure: `test_transport_failure_prepared_is_not_exposed`
  shows a failed wire attempt journals `delivery_prepared` +
  `provider_attempt_failed` but no `provider_delivery`/`evidence_delivery`;
  the same delivery identity binds the retry request and its
  `provider_response`. (The offline *accounting* defect that counts
  prepared-only rows as delivered is tracked in the sibling
  `runtime-lanes-report.md` as a confirmed cross-boundary item.)
- Internal calls (catalog bootstrap, persistent plan) bind only the kinds they
  carry (`pending_kinds`), never drain queued agent evidence
  (`miniswe_runtime.py:1153-1161`).

## Limitations / UNPROVEN

- All provider transport is synthetic; model *judgment* about delivered
  evidence is out of scope for offline proof.
- The local graph was hand-sealed over real producer bytes; the benchmark
  certification path (`ensure_index` → full publish) is covered by sibling
  slices, not re-proven here. On Windows `ensure_index` refuses
  `GT_INDEX_RESOURCE_GUARD_UNAVAILABLE` by design — tests invoke the binary
  directly.
- Localization ranking *quality* on ambiguous names/duplicate basenames is
  exercised only at fixture scale; correctness of the ranking on large repos
  is **UNPROVEN**.
- `cochange_prior` stale-revision demotion and mid-run history growth are
  **UNPROVEN** (table is index-time derived; benchmark checkouts are depth-1 →
  expect `cochange_history_unavailable` on most tasks).
- `GT_CHANGE_SURFACE` missing-role/registration output and the
  `GT_CS_EDIT_TRIGGER` path *with* a valid before/after pair are **UNPROVEN**
  pending the D-β correction.
- `select_catalog` delayed-offer and DELIVERED→CONSUMED transitions are
  covered by the existing `tests/test_miniswe_runtime.py` suite (referenced,
  not re-derived here).

## Test commands and counts

| Command | Result |
|---|---|
| `python -m pytest tests/test_audit_producers.py -q` | **28 passed** |
| `python -m pytest tests/test_miniswe_integration.py -q` | **71 passed** |
| `python -m pytest tests/test_agent_loop_soak.py -q` | **5 passed** |

Layer-4 mutations run (one boundary corrupted per test, evidence goes red):
producer disconnected (`delivery_recipe_unresolved`, no delivery), dispatch
disconnected (`GT_GATEWAY=0` → zero `gateway.*` invocations), required graph
absent (typed abstention), admission dropping candidates (no
`delivery_prepared`, evidence absent from request bytes), response binding
absent (prepared ≠ exposed, retry binds), ghost co-change partner (labeled
prior, never an existence claim), unchanged before/after (no invented
`signature_mismatch`), catalog transport failure (typed abstention, no
fabricated result).
