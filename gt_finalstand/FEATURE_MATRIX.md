# GT Feature Proof Matrix

Schema: `gt.feature_matrix.md.v2`
Source revision: `9010199412dd1cb4fb5cd60e9ebd63000cc2132f`
Generated at: `2026-09-05T03:05:12.672469Z`
Matrix digest: `51441a7f7eb9d385d5cfa1513dbfbca6c01166a01f52f6100a1ded3534c9e524`

| Identity | Kind | Disposition | Trigger | Evidence exit | Cell digest |
| --- | --- | --- | --- | ---: | --- |
| GT_CERT_DELIVERY | CAP | WITNESSED | `tests/test_gt_engine.py::test_bridge_proves_exact_delivery_exposure` | 0/0 | `ed7f84b309cac64975f17d171a484cf0fb5a417225473c6e2e337c9009e693e1` |
| GT_CHANGE_SURFACE | CAP | WITNESSED | `tests/test_gt_engine.py::test_repeated_failed_search_fires_newfile_precedent_and_change_surface` | 0/0 | `791cfa06a6f9ab31dc1a25b1159367e997aaf26f9d22d4d41ad6b48784f22da8` |
| GT_EDIT_CHECK | CAP | WITNESSED | `tests/test_gt_attribution.py::test_executed_clean_edit_check_is_witnessed_but_no_target_is_ineligible` | 0/0 | `2758606d84d024714fca24068496b81fa2a17a3d6711dbd39058877a52183311` |
| GT_HYPOTHESIS | CAP | WITNESSED | `tests/test_gt_engine.py::test_recovery_fires_on_same_failure_recurring_across_edit` | 0/0 | `fffb9e5458d9f77cbd03b0cd542b9baa16ca62e73f3597ed603c3ba2205ecbc0` |
| GT_LOC_RESLOT | CAP | WITNESSED | `tests/test_gt_engine.py::test_search_fires_ranked_localization_and_loc_reslot` | 0/0 | `806c6ae485d5172feb9d14e19386afea87b89607b30d4ad6bf9d0f9b4eab0279` |
| GT_PATCH_DELTA | CAP | WITNESSED | `tests/test_runtime_observation.py::test_python_signature_delta_distinguishes_body_and_signature_edits` | 0/0 | `82ec89ec4446b1070b65b6de0816797952bf79b5c07011e9baee6b0fbb4e3c84` |
| GT_SS_SUBMIT_RED | CAP | WITNESSED | `tests/test_gt_engine.py::test_submit_red_blocks_on_unresolved_observed_fail` | 0/0 | `911d66142b0c9dded19cc1ca3b2639eaa9c509516fb680bda2a090445a7a5118` |
| caller_contract | FACT | WITNESSED | `tests/test_gt_engine.py::test_file_view_fires_verified_caller_contract` | 0/0 | `91fbe98eb32c787d35ca67eba30a196599224482e97c2bcf8e5fcc8adeaa5fd4` |
| cochange_prior | FACT | WITNESSED | `tests/test_gt_attribution.py::test_cochange_evidence_binds_to_dark_trigger_identity` | 0/0 | `6d6d9630c61c8523e41126181e6bb4d81f286694e8aef732366ff143fd4263ad` |
| covering_red | FACT | WITNESSED | `tests/test_gt_engine.py::test_covering_red_fires_at_post_edit` | 0/0 | `8e9c446002367c4e3c2616053a3d01a5b58caa9c5b6981bacebe46df93e0ba18` |
| def_partition | FACT | WITNESSED | `tests/test_gt_engine.py::test_bridge_delivers_sealed_pure_suffix` | 0/0 | `19c4ea40d65e2935509e1ed5d7e891dceeeaa2d0ec742652ab7f7ae74e653c13` |
| localization | FACT | WITNESSED | `tests/test_gt_engine.py::test_search_fires_ranked_localization_and_loc_reslot` | 0/0 | `fa6dd3c8743aeab0310c4bf1af1d5c829b941b476240d10fd22761657d51f7bd` |
| newfile_precedent | FACT | WITNESSED | `tests/test_miniswe_runtime.py::test_newfile_precedent_delivered_on_file_create` | 0/0 | `7d7db5ac262f768415a5cb0ccf3e2d97dda062f977f13cd393de65a943265b16` |
| obligations | FACT | WITNESSED | `tests/test_gt_engine.py::test_submit_certificate_receives_obligation_coverage` | 0/0 | `142ca52e628407db53ed7194ece6d45f4e258ae98a10fc4840c36dc81e1cf896` |
| recovery | FACT | WITNESSED | `tests/test_gt_engine.py::test_recovery_fires_on_same_failure_recurring_across_edit` | 0/0 | `35cb815ced3ef87f39d2cd254ab10fe28fa003179d75e8b1f0282103b6a2ae65` |
| select_catalog | CAP | WITNESSED | `tests/test_persistent_execution_state.py::test_feature18_selection_lifecycle_is_content_safe_and_action_bound` | 0/0 | `0f7bd3a1c8dc6e3ab1441a23da450b377e2ec8f48fa159d3efa22cb8c1d91540` |
| signature_delta | FACT | WITNESSED | `tests/test_gt_engine.py::test_edit_fires_signature_mismatch_under_profile_2` | 0/0 | `74eb02efe8c4c4094eeea87cb69af604e3e35c549969d06b1efadfba3448a20c` |
| submit_refusal | FACT | WITNESSED | `tests/test_gt_engine.py::test_sdlc_submit_refuses_edit_without_post_edit_verification` | 0/0 | `74685e4a25f95392c2d4156d8a935c0e4d25ea0caebd8881f94a1ba1f2ccc96b` |
| syntax_result | FACT | WITNESSED | `tests/test_gt_engine.py::test_post_edit_syntax_failure_delivers_immediately` | 0/0 | `80f0d7897a7754c614cdb9aa885ce16c4535458bea805c4923f0e48b5dc9b749` |

