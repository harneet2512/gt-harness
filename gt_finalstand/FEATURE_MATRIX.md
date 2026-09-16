# GT Feature Proof Matrix

Schema: `gt.feature_matrix.md.v2`
Source revision: `2171c00db189a703cecc358e220c28540a025d58`
Generated at: `2026-09-16T23:26:13.267561Z`
Matrix digest: `4e638389b7baf821b8701db863794400139cf326c650664d5ae7ef2ed01b97b6`

| Identity | Kind | Disposition | Trigger | Evidence exit | Cell digest |
| --- | --- | --- | --- | ---: | --- |
| GT_CERT_DELIVERY | CAP | WITNESSED | `tests/test_gt_engine.py::test_bridge_proves_exact_delivery_exposure` | 0/0 | `7e403cbd1aa02b6908c8225a4d9b2e478d0e9b14c9b6ca804f72fe2cb27f3ef7` |
| GT_CHANGE_SURFACE | CAP | not_run | `tests/test_gt_engine.py::test_repeated_failed_search_fires_newfile_precedent_and_change_surface` | 0/0 | `814511ace16647b9bc8986c6c19b82410d125ad4ab651639c8e599e5efe7ec5d` |
| GT_EDIT_CHECK | CAP | WITNESSED | `tests/test_gt_attribution.py::test_executed_clean_edit_check_is_witnessed_but_no_target_is_ineligible` | 0/0 | `59914ea0b2e92cf36e5a62a6be87bd2e655dd936544a7aa00f37a50c4452e1e4` |
| GT_HYPOTHESIS | CAP | not_run | `tests/test_gt_engine.py::test_recovery_fires_on_same_failure_recurring_across_edit` | 0/0 | `f43fdc1d14a13a77ca4c0d8daffdbecd1cead2d70e242316ec739bccb4a0365f` |
| GT_LOC_RESLOT | CAP | not_run | `tests/test_gt_engine.py::test_search_fires_ranked_localization_and_loc_reslot` | 0/0 | `d876830d47a527f17d38e060b097454a8faa5bbc076f80e897bd56514dec34c6` |
| GT_PATCH_DELTA | CAP | WITNESSED | `tests/test_runtime_observation.py::test_python_signature_delta_distinguishes_body_and_signature_edits` | 0/0 | `f94f1325f5d0174a7cb4e13224eba20b48e4208fa091be30624dd042768438ba` |
| GT_SS_SUBMIT_RED | CAP | not_run | `tests/test_gt_engine.py::test_submit_red_blocks_on_unresolved_observed_fail` | 0/0 | `1c7134f6307b1f2746feb7ee9503ac35ce184d20ac675321d6748e0dab373f30` |
| caller_contract | FACT | not_run | `tests/test_gt_engine.py::test_file_view_fires_verified_caller_contract` | 0/0 | `b41e10046000ec8337cbba64c97e26a5c636da4f564d3f19c196f1666c14e9d2` |
| cochange_prior | FACT | WITNESSED | `tests/test_gt_attribution.py::test_cochange_evidence_binds_to_dark_trigger_identity` | 0/0 | `d458f4d1262b9058131f22cbc7af24e539d931763dd2ba1b7e047b28304d9668` |
| covering_red | FACT | not_run | `tests/test_gt_engine.py::test_covering_red_fires_at_post_edit` | 0/0 | `00c28a93266d1078ca309eae9671c46b74a5c80c2360c94932ec69a32730773b` |
| def_partition | FACT | not_run | `tests/test_gt_engine.py::test_bridge_delivers_sealed_pure_suffix` | 0/0 | `7cee21f53b8a4966c7c8de2e7b25dd0d22cfb53e1038160214258d941d873132` |
| localization | FACT | not_run | `tests/test_gt_engine.py::test_search_fires_ranked_localization_and_loc_reslot` | 0/0 | `0b6f17b2399459c556665439a8a4b68f3a3867e3dae623fca9b10aecda9d9817` |
| newfile_precedent | FACT | WITNESSED | `tests/test_miniswe_runtime.py::test_newfile_precedent_delivered_on_file_create` | 0/0 | `e9a13a1f24744f11e8f8fd612903de92c5c1c61b5503b9e4745258a57cb4dbe3` |
| obligations | FACT | not_run | `tests/test_gt_engine.py::test_submit_certificate_receives_obligation_coverage` | 0/0 | `036bce682e6247958f7ff0961a5228a1298ae46650d6e422e38a612d8ebf85b9` |
| persistent_plan | CAP | WITNESSED | `tests/test_miniswe_runtime.py::test_plan_render_receipt_matches_native_request_bytes` | 0/0 | `b48cd925ea9de1e5fcfd8b9a6e13bafd2889dc9d58d8bb0ebc7a21de543f01e3` |
| plan_gate | CAP | WITNESSED | `tests/test_miniswe_runtime.py::test_plan_gate_directive_is_audited_through_native_provider_request` | 0/0 | `ee542e11856e6f0be21132d7814bd5add6d440bd54c3286b292e9d15c49ce571` |
| recovery | FACT | not_run | `tests/test_gt_engine.py::test_recovery_fires_on_same_failure_recurring_across_edit` | 0/0 | `6a0690f5110f71664a3ee6f1bca9c6a015bd25abd5a33a6aa41a8d23dd897783` |
| select_catalog | CAP | WITNESSED | `tests/test_persistent_execution_state.py::test_feature18_selection_lifecycle_is_content_safe_and_action_bound` | 0/0 | `207aeeee231bb4d9c1f5573bbbaf5ae647b2a22a3033098cb8871630866bb838` |
| signature_delta | FACT | not_run | `tests/test_gt_engine.py::test_edit_fires_signature_mismatch_under_profile_2` | 0/0 | `504e1e359f6667761734226b0f9a3db92edf6c8b1a01592c38ae0e66323c837b` |
| submit_refusal | FACT | not_run | `tests/test_gt_engine.py::test_sdlc_submit_refuses_edit_without_post_edit_verification` | 0/0 | `fae2ed8f6446c429d75d6bf82bc85951e3f0909ee33d9c7232d536cd816b08ea` |
| syntax_result | FACT | not_run | `tests/test_gt_engine.py::test_post_edit_syntax_failure_delivers_immediately` | 0/0 | `4428c11a53253de89842f81a74f3e1e342ae0bc40355d0c58612a993ed3cfe20` |

