from pathlib import Path


def test_arb_and_live_share_one_frozen_retrieval_profile():
    from gt_engine.retrieval_profile import FINAL_RETRIEVAL_PROFILE
    from scripts.arb_adapter import ARB_RETRIEVAL_PROFILE

    assert ARB_RETRIEVAL_PROFILE is FINAL_RETRIEVAL_PROFILE
    assert FINAL_RETRIEVAL_PROFILE.channel_limit == 100
    assert FINAL_RETRIEVAL_PROFILE.top_k == 20
    assert FINAL_RETRIEVAL_PROFILE.selection_limit == 8
    assert FINAL_RETRIEVAL_PROFILE.token_budget == 1_200
    assert FINAL_RETRIEVAL_PROFILE.dense_candidate_limit == 32
    assert FINAL_RETRIEVAL_PROFILE.cold_start_timeout_sec >= 25.0
    assert FINAL_RETRIEVAL_PROFILE.steady_state_timeout_sec == 2.0


def test_live_agent_defaults_match_the_frozen_retrieval_profile(tmp_path):
    from eval.gt_central_agent import MiniSweCentralAgent
    from gt_engine.retrieval_profile import FINAL_RETRIEVAL_PROFILE

    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")

    assert (
        agent.preemptive_retrieval_token_budget
        == FINAL_RETRIEVAL_PROFILE.token_budget
    )
    assert (
        agent.preemptive_retrieval_dense_candidate_limit
        == FINAL_RETRIEVAL_PROFILE.dense_candidate_limit
    )
    assert (
        agent.preemptive_retrieval_selection_limit
        == FINAL_RETRIEVAL_PROFILE.selection_limit
    )
    assert (
        agent.preemptive_retrieval_cold_start_timeout_sec
        == FINAL_RETRIEVAL_PROFILE.cold_start_timeout_sec
    )
    assert (
        agent.preemptive_retrieval_timeout_sec
        == FINAL_RETRIEVAL_PROFILE.steady_state_timeout_sec
    )


def test_paid_context_arms_enable_the_pinned_live_retriever():
    root = Path(__file__).resolve().parents[1]
    for name in ("tb2_miniswe_engine.yml", "tb2_miniswe_central.yml"):
        workflow = (root / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "python -m pip install -e '.[retrieval]'" in workflow
        assert "GH_TOKEN: ${{ github.token }}" in workflow
        assert "SNOWFLAKE_MODEL_SHA256:" in workflow
        assert "SNOWFLAKE_TOKENIZER_SHA256:" in workflow
        assert "tokenizer.json\" | sha256sum -c -" in workflow
        assert "Provision pinned Snowflake ONNX runtime asset" in workflow
        assert (
            "if: ${{ inputs.arm == 'certified_context' || inputs.arm == 'certified_full' }}"
            in workflow
        )
        assert workflow.count("--ak enable_preemptive_retrieval=true") == 2
        assert workflow.count("--ak enable_decision_sufficiency=true") == 2
        assert workflow.count(
            '--ak preemptive_retrieval_model_dir="$RUNNER_TEMP/snowflake-arctic-embed-m"'
        ) == 2
        assert "--ak enable_preemptive_retrieval=false" in workflow


def test_central_matrix_provisions_and_proves_dense_backend_inside_each_run_job():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "tb2_miniswe_central.yml").read_text(
        encoding="utf-8"
    )
    run_block = workflow[workflow.index("  run:"): workflow.index("  merge:")]
    harbor_block = run_block[run_block.index("      - name: Run harbor"):]
    assert "python -m pip install -e '.[retrieval]'" in run_block
    assert "Provision pinned Snowflake ONNX runtime asset in task job" in run_block
    assert "Verify pinned dense backend before Mini-SWE" in run_block
    assert "SnowflakeOnnxDenseBackend.from_directory" in run_block
    assert "embed_query" in run_block and "embed_documents" in run_block
    assert "sha256sum -c -" in run_block
    assert '"model_revision": os.environ["SNOWFLAKE_MODEL_REVISION"]' in run_block
    assert '"model_sha256": os.environ["SNOWFLAKE_MODEL_SHA256"]' in run_block
    assert '"tokenizer_sha256": os.environ["SNOWFLAKE_TOKENIZER_SHA256"]' in run_block
    assert 'Path("dense-backend-proof.json").write_text' in run_block
    assert "results/terminal-bench/dense-backend-proof.json" not in run_block
    assert "audit_treatment_runtime" in workflow
    assert "invalid_treatment_release_tasks" in workflow
    upload_paths = (
        "path: |\n"
        "            results/terminal-bench/\n"
        "            dense-backend-proof.json"
    )
    assert upload_paths in run_block
    assert run_block.index("Verify pinned dense backend before Mini-SWE") < run_block.index(
        "      - name: Run harbor"
    )
    assert (
        'preemptive_retrieval_model_dir="$RUNNER_TEMP/snowflake-arctic-embed-m"'
        in harbor_block
    )


def test_central_merge_fails_closed_on_any_provider_delivery_integrity_error():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "tb2_miniswe_central.yml").read_text(
        encoding="utf-8"
    )
    merge_block = workflow[workflow.index("  merge:") :]

    assert "from gt_engine.delivery_audit import audit_provider_deliveries" in merge_block
    assert "provider_delivery_failures" in merge_block
    assert "invalid_provider_deliveries" in merge_block
    assert "invalid_intelligence or invalid_dense or invalid_provider_deliveries" in merge_block
    assert "INVALID TREATMENT RELEASE" in merge_block
    assert "invalid_treatment_release" in merge_block


def test_central_merge_requires_explicit_dense_backend_success():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "tb2_miniswe_central.yml").read_text(
        encoding="utf-8"
    )
    merge_block = workflow[workflow.index("  merge:") :]

    assert 'dense_receipt.get("available") is True' in merge_block
    assert 'dense_proof.get("available") is True' in merge_block
    assert "or dense_receipt\n" not in merge_block


def test_central_merge_labels_same_run_control_hash_as_intervention_accounting():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "tb2_miniswe_central.yml").read_text(
        encoding="utf-8"
    )
    merge_block = workflow[workflow.index("  merge:") :]

    assert "call1_gt_changed_tasks" in merge_block
    assert "This is intervention " in merge_block
    assert "control_prompt_mismatch_tasks" not in merge_block


def test_provider_free_workflow_sets_runner_temp_at_step_runtime():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "central_provider_free.yml").read_text(
        encoding="utf-8"
    )

    assert "${{ runner.temp }}" not in workflow
    assert 'model_dir="$RUNNER_TEMP/snowflake-arctic-embed-m"' in workflow
    assert 'echo "GT_TEST_SNOWFLAKE_MODEL_DIR=$model_dir" >> "$GITHUB_ENV"' in workflow
