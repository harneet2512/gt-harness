# GT Feature Proof Matrix

Schema: `gt.feature_matrix.md.v1`
Source revision: `039121bc62c1354a3b3685f58102790e524d7b83`
Generated at: `2026-08-31T22:59:15.628992Z`
Matrix digest: `49c47d6f798689b0b530dd630693e32c69fcaf54668e67bdcb3348c9aad074a3`

| Identity | Kind | Disposition | Trigger | Evidence exit | Cell digest |
| --- | --- | --- | --- | ---: | --- |
| GT_CERT_DELIVERY | CAP | WITNESSED | `tests/test_gt_engine.py::test_bridge_proves_exact_delivery_exposure` | 0 | `a34ede3eb9702fb40cf1dd0b42b755791bf97d0ea4ff838ba88834973623b832` |
| GT_CHANGE_SURFACE | CAP | WITNESSED | `tests/test_gt_engine.py::test_repeated_failed_search_fires_newfile_precedent_and_change_surface` | 0 | `e6d5439885c661619d6c164db080ece38d784bf389b939d4daac1ab8c44527a8` |
| GT_EDIT_CHECK | CAP | WITNESSED | `tests/test_gt_attribution.py::test_executed_clean_edit_check_is_witnessed_but_no_target_is_ineligible` | 0 | `621adc5b69b5bd95462c1dda5001bb32ce5dce69e179b52cd53bec6ed9097a9f` |
| GT_HYPOTHESIS | CAP | WITNESSED | `tests/test_gt_engine.py::test_recovery_fires_on_same_failure_recurring_across_edit` | 0 | `84d0b1523ae22acce6070fba9596f412f8a8892c2d4ddeaece713681b9ef722b` |
| GT_LOC_RESLOT | CAP | WITNESSED | `tests/test_gt_engine.py::test_search_fires_ranked_localization_and_loc_reslot` | 0 | `29918955c4e526a0fa472ae1dc2074f42d88362ce31ad98da17ac637e6e3ee63` |
| GT_PATCH_DELTA | CAP | WITNESSED | `tests/test_runtime_observation.py::test_python_signature_delta_distinguishes_body_and_signature_edits` | 0 | `d09b0992d55f07bf31674e0afc974731acf527ede979a2fdb01271593b6da99c` |
| GT_SS_SUBMIT_RED | CAP | WITNESSED | `tests/test_gt_engine.py::test_submit_red_blocks_on_unresolved_observed_fail` | 0 | `5714e40d336cd0091a46732751416c167b58f3c2689bab1606eb662c5280296e` |
| caller_contract | FACT | WITNESSED | `tests/test_gt_engine.py::test_file_view_fires_verified_caller_contract` | 0 | `48761cc7051da5e8a1d9da5f6484f3dcd818709895380eace45416b041c0b574` |
| covering_red | FACT | WITNESSED | `tests/test_gt_engine.py::test_covering_red_fires_at_post_edit` | 0 | `8268a597e40ed20f765da60363601b244ac4f245eafd51835b435e167f0060d7` |
| def_partition | FACT | WITNESSED | `tests/test_gt_engine.py::test_bridge_delivers_sealed_pure_suffix` | 0 | `4ecf70b8a4f713d85948567a066ca132bffafd899964a27dcc178d53a3a1de83` |
| localization | FACT | WITNESSED | `tests/test_gt_engine.py::test_search_fires_ranked_localization_and_loc_reslot` | 0 | `484521cb1b2fab9d621c34c8ec8c076293e7309194020707fa92a9602856f766` |
| newfile_precedent | FACT | WITNESSED | `tests/test_miniswe_runtime.py::test_newfile_precedent_delivered_on_file_create` | 0 | `a2f0d522630693e1a6976781bf0127a4f598f7ec77554bb76eed3501a5b6e550` |
| obligations | FACT | WITNESSED | `tests/test_gt_engine.py::test_submit_certificate_receives_obligation_coverage` | 0 | `96c695ecd21877d1c1094d1839d31288e4b8f09d7f9e0b42889f49fd02011c37` |
| recovery | FACT | WITNESSED | `tests/test_gt_engine.py::test_recovery_fires_on_same_failure_recurring_across_edit` | 0 | `8c611018a7b8141e5d553d1982d122ae13e7b8e2c920174b700b5219005903ff` |
| select_catalog | CAP | WITNESSED | `tests/test_persistent_execution_state.py::test_feature18_selection_lifecycle_is_content_safe_and_action_bound` | 0 | `8c8b71043034e2a3072e5a2a4316f5113a4dad5d45d8d3b4e161ef8745b5a3ea` |
| signature_delta | FACT | WITNESSED | `tests/test_gt_engine.py::test_edit_fires_signature_mismatch_under_profile_2` | 0 | `13d192176c3ee81e8220970a42b904ffae22d68af218e74cf0366946aecd16a2` |
| submit_refusal | FACT | WITNESSED | `tests/test_gt_engine.py::test_sdlc_submit_refuses_edit_without_post_edit_verification` | 0 | `e87f6ada0130e610f51a3aad4df3d8bce0939395248bb2a8ef27b21a00a42756` |
| syntax_result | FACT | WITNESSED | `tests/test_gt_engine.py::test_post_edit_syntax_failure_delivers_immediately` | 0 | `c1d756c521833df18f9f6931a962622b0950f9907747f702864a09e1a0ea8339` |

