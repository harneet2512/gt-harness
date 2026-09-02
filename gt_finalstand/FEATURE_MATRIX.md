# GT Feature Proof Matrix

Schema: `gt.feature_matrix.md.v1`
Source revision: `4d805cb5ad9dbdeb7c660277df473be7443a6765`
Generated at: `2026-09-02T02:18:20.199786Z`
Matrix digest: `1c53464e999474eae50c8a58fc1efefe55e5d2a0ab40660bc28a9697489af89a`

| Identity | Kind | Disposition | Trigger | Evidence exit | Cell digest |
| --- | --- | --- | --- | ---: | --- |
| GT_CERT_DELIVERY | CAP | WITNESSED | `tests/test_gt_engine.py::test_bridge_proves_exact_delivery_exposure` | 0 | `ccdd9148e50397d4fe407fdd311313c64ec47a662e45232361421fa98874d52e` |
| GT_CHANGE_SURFACE | CAP | WITNESSED | `tests/test_gt_engine.py::test_repeated_failed_search_fires_newfile_precedent_and_change_surface` | 0 | `7cdecb3bf9b8a23deb779bc1843c284e3c7350788ab2a578b0cbf2c698473b3c` |
| GT_EDIT_CHECK | CAP | WITNESSED | `tests/test_gt_attribution.py::test_executed_clean_edit_check_is_witnessed_but_no_target_is_ineligible` | 0 | `310148659f327c609cd6dfc8bbc8560970dfdd6a4804d36dd31013b643342c97` |
| GT_HYPOTHESIS | CAP | WITNESSED | `tests/test_gt_engine.py::test_recovery_fires_on_same_failure_recurring_across_edit` | 0 | `0b71722c40d2901d8aaf0b32ea6d219e90b07fde75ed17d41eee5bff0f30e75e` |
| GT_LOC_RESLOT | CAP | WITNESSED | `tests/test_gt_engine.py::test_search_fires_ranked_localization_and_loc_reslot` | 0 | `8fdd5cc906ae932f005e3f5bfe077872286afad11f3a0598f213d229ec4c001a` |
| GT_PATCH_DELTA | CAP | WITNESSED | `tests/test_runtime_observation.py::test_python_signature_delta_distinguishes_body_and_signature_edits` | 0 | `7d6b566d32d06e50a9c4295704bb928232144243ad153a1bd32fc7064f10c258` |
| GT_SS_SUBMIT_RED | CAP | WITNESSED | `tests/test_gt_engine.py::test_submit_red_blocks_on_unresolved_observed_fail` | 0 | `61ac618da46d326812787ef869d126f3ac15e1d60a4a4658137252ed3dd876cd` |
| caller_contract | FACT | WITNESSED | `tests/test_gt_engine.py::test_file_view_fires_verified_caller_contract` | 0 | `35a2c1242ced5dd831232e86d5edccc62b2530fc0ea825352d1d323cd218d5e4` |
| cochange_prior | FACT | WITNESSED | `tests/test_gt_attribution.py::test_cochange_evidence_binds_to_dark_trigger_identity` | 0 | `131414ca25440da25454fb07f88c55e4922bec4d29c95ab0b99bcc836c920e94` |
| covering_red | FACT | WITNESSED | `tests/test_gt_engine.py::test_covering_red_fires_at_post_edit` | 0 | `a4686a2a496c0916d959fab600e909ad2556caa51286f545bc6463a59defccbb` |
| def_partition | FACT | WITNESSED | `tests/test_gt_engine.py::test_bridge_delivers_sealed_pure_suffix` | 0 | `78b07ccb5d552e3b4ce1ee4455ec120e59ff04ff94ca46bb6bc0ac754faecb30` |
| localization | FACT | WITNESSED | `tests/test_gt_engine.py::test_search_fires_ranked_localization_and_loc_reslot` | 0 | `85df6fa31f7b1e76e84aadd4d0580871685db70aea9620f6d1b0c7a240286b6c` |
| newfile_precedent | FACT | WITNESSED | `tests/test_miniswe_runtime.py::test_newfile_precedent_delivered_on_file_create` | 0 | `ac3961b3b4ffd357571a9b56ab45fee4c6ef64671ebe5a07325cd7fe09f0c089` |
| obligations | FACT | WITNESSED | `tests/test_gt_engine.py::test_submit_certificate_receives_obligation_coverage` | 0 | `9d309b0847ccf0002ab72e005c1594091851c42c1a09675e01e336048b0670f6` |
| recovery | FACT | WITNESSED | `tests/test_gt_engine.py::test_recovery_fires_on_same_failure_recurring_across_edit` | 0 | `dfc42ab579c6c0863b8e2826de6dedb1d99fa820425979adf6f5e15a655e42fa` |
| select_catalog | CAP | WITNESSED | `tests/test_persistent_execution_state.py::test_feature18_selection_lifecycle_is_content_safe_and_action_bound` | 0 | `94a6f820c3d03baa8e60deea8e560b81ba0f917d53892e01233dc3165007293c` |
| signature_delta | FACT | WITNESSED | `tests/test_gt_engine.py::test_edit_fires_signature_mismatch_under_profile_2` | 0 | `4d3abef5311b5f02422350a8cdc362509ccb939b442e80c9f579bbea6ba6c5b3` |
| submit_refusal | FACT | WITNESSED | `tests/test_gt_engine.py::test_sdlc_submit_refuses_edit_without_post_edit_verification` | 0 | `e0b416b4b8c7d939b06ad72095fd744d1a8ed992f7126ce0797c744ddc088906` |
| syntax_result | FACT | WITNESSED | `tests/test_gt_engine.py::test_post_edit_syntax_failure_delivers_immediately` | 0 | `3a891fbbeaf96d2942bf0143f20bf705719dba190047546a76c31155a9bbbdbe` |

