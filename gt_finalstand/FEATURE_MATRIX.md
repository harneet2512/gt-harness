# GT Feature Proof Matrix

Schema: `gt.feature_matrix.md.v1`
Source revision: `ca744e7ebb7d03b6d11e31eeab8600cdb3669bdd`
Generated at: `2026-09-01T23:16:06.832377Z`
Matrix digest: `a69cf772ead08c8f63c5c6599d5c917458601357bf378180bfef7ee1f1c235d4`

| Identity | Kind | Disposition | Trigger | Evidence exit | Cell digest |
| --- | --- | --- | --- | ---: | --- |
| GT_CERT_DELIVERY | CAP | WITNESSED | `tests/test_gt_engine.py::test_bridge_proves_exact_delivery_exposure` | 0 | `c747ac9427c67d4a627c47cbae7895c894e5b95e53df1d14ec0d0af186814695` |
| GT_CHANGE_SURFACE | CAP | WITNESSED | `tests/test_gt_engine.py::test_repeated_failed_search_fires_newfile_precedent_and_change_surface` | 0 | `547621a64fb41b75e74f802d29a01d396bbc6478ea5cc940293b69042c44c01b` |
| GT_EDIT_CHECK | CAP | WITNESSED | `tests/test_gt_attribution.py::test_executed_clean_edit_check_is_witnessed_but_no_target_is_ineligible` | 0 | `de01949772a8ca3cac44fea91e3c37a3bfaee4436295cdbc1fb3fc9f631868ad` |
| GT_HYPOTHESIS | CAP | WITNESSED | `tests/test_gt_engine.py::test_recovery_fires_on_same_failure_recurring_across_edit` | 0 | `7541ab21a03a96e7754c1018450b228cfa0663b907b5f3d4b5f9b1bd2260d44f` |
| GT_LOC_RESLOT | CAP | WITNESSED | `tests/test_gt_engine.py::test_search_fires_ranked_localization_and_loc_reslot` | 0 | `955aeb6917023508b8765f178c3aca45f5cb98e083991ff9572237abb20d3855` |
| GT_PATCH_DELTA | CAP | WITNESSED | `tests/test_runtime_observation.py::test_python_signature_delta_distinguishes_body_and_signature_edits` | 0 | `52b1bb33a65ac2aba0c5fbf5efcc7e42745af4dc318c5d436c36e30c5ad95c70` |
| GT_SS_SUBMIT_RED | CAP | WITNESSED | `tests/test_gt_engine.py::test_submit_red_blocks_on_unresolved_observed_fail` | 0 | `1df482f0ac52576f9691e6c2455d162d41f403e6b63f1d1ff9a31983b9764400` |
| caller_contract | FACT | WITNESSED | `tests/test_gt_engine.py::test_file_view_fires_verified_caller_contract` | 0 | `9613cd7d19e02d066ed87365122fbcccff7f5db0f4e21085430d5108828cca7b` |
| cochange_prior | FACT | WITNESSED | `tests/test_gt_attribution.py::test_cochange_evidence_binds_to_dark_trigger_identity` | 0 | `da497b68381227ac0ad8e2e9e177f2d3e230f5bb8ba6b425fc33c561649ae828` |
| covering_red | FACT | WITNESSED | `tests/test_gt_engine.py::test_covering_red_fires_at_post_edit` | 0 | `541b4b8be3f440a2d54672a2e9f1fe74023d6c3978449ba7fbf1d9363ec64f74` |
| def_partition | FACT | WITNESSED | `tests/test_gt_engine.py::test_bridge_delivers_sealed_pure_suffix` | 0 | `8171dfb5bc33cacd8f8da442ef32bd7ad689df6d57d811fe38197123ea4c045f` |
| localization | FACT | WITNESSED | `tests/test_gt_engine.py::test_search_fires_ranked_localization_and_loc_reslot` | 0 | `a5eabb8ffb0307ae140af99f73fc07828e92268d7fd92e3e56ed3885dc70b65e` |
| newfile_precedent | FACT | WITNESSED | `tests/test_miniswe_runtime.py::test_newfile_precedent_delivered_on_file_create` | 0 | `c4750cd2834ac3521c05d805928d6dcf88c068f45e682cad3f96959fa3b73a5e` |
| obligations | FACT | WITNESSED | `tests/test_gt_engine.py::test_submit_certificate_receives_obligation_coverage` | 0 | `58a3928cf5e33c473741b4239c0d3aee53d62ecf50aaace61d36e1c5982d6d56` |
| recovery | FACT | WITNESSED | `tests/test_gt_engine.py::test_recovery_fires_on_same_failure_recurring_across_edit` | 0 | `254079c13cdecd1cedc2f23e8611a47188f1193614dd0dae63e75d43ed6b9e9d` |
| select_catalog | CAP | WITNESSED | `tests/test_persistent_execution_state.py::test_feature18_selection_lifecycle_is_content_safe_and_action_bound` | 0 | `c6b5cdc7e128ce219ef402840c05aa739f13cad45004eec5ffd3c98dc025475f` |
| signature_delta | FACT | WITNESSED | `tests/test_gt_engine.py::test_edit_fires_signature_mismatch_under_profile_2` | 0 | `99524742f089ad86044134379b16fe9133fb8f2a2eab13fcb7e23b512edf7944` |
| submit_refusal | FACT | WITNESSED | `tests/test_gt_engine.py::test_sdlc_submit_refuses_edit_without_post_edit_verification` | 0 | `94a19bf5212e7b44acf5b5ab39f14438a817634b8439a7944646e1c27507dd6a` |
| syntax_result | FACT | WITNESSED | `tests/test_gt_engine.py::test_post_edit_syntax_failure_delivers_immediately` | 0 | `9b0fd3db4bbcedad2852323981b99646c794d29243d29a3d5938d3b626d3b73e` |
