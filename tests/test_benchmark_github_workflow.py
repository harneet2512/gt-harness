from pathlib import Path

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "arb_gt_retrieval.yml"


def test_arb_workflow_is_dispatch_only_and_pinned() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "ARB_COMMIT: 07014c986f3deadb1548c62b32c0ffbe6a81465d" in text
    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in text
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in text
    assert "local_benchmark_execution\": False" in text


def test_arb_workflow_uses_gold_free_shards_and_github_baseline() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "benchmarks/arb/redacted/*.jsonl" in text
    assert "ARB_RELEASE_TAG: arb-v2-pinned-07014c9" in text
    assert "hf download" not in text
    assert "huggingface_hub" not in text
    assert "--shard-index" in text
    assert "--shard-count" in text
    assert "--candidate-filter all_files" in text
    assert "arb-official-baselines-${{ github.run_id }}" in text
    assert "Install pinned GroundTruth runtime" in text
    assert "fromJSON(needs.prepare.outputs.shards)" in text
    assert "arb-complete-report-${{ github.run_id }}" in text
    assert "scripts/arb_evaluate.py" in text
    assert "--expected-samples" in text
    assert "arb-gt-ranked-evaluation.json" in text
    assert "arb-gt-delivered-evaluation.json" in text
    assert "arb-gt-ranked-evaluation.selective.json" in text
    assert "arb-gt-ranked-evaluation.selective.md" in text
    assert "arb-gt-delivered-evaluation.selective.json" in text
    assert "arb-gt-delivered-evaluation.selective.md" in text
    assert "--view ranked" in text
    assert "--view delivered" in text
    assert "merge-corpus-manifests" in text
    assert "ln -sfn \"$RUNNER_TEMP/arb-data/corpus\" \"$RUNNER_TEMP/arb-data/data/corpus\"" in text
    assert "run_gt" in text


def test_arb_workflow_runs_the_pinned_local_snowflake_onnx_dense_channel() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "SNOWFLAKE_MODEL_REVISION: 7802add0519e4bf94c46ef23552176697c7a1ac7" in text
    assert (
        "SNOWFLAKE_MODEL_SHA256: "
        "564e6c65ee0c739a486702e9e3e9b33c3f697c19c34dbe886bce9eec497ce971"
    ) in text
    assert 'GT_DENSE_INTRA_OP_THREADS: "4"' in text
    assert 'GT_DENSE_INTER_OP_THREADS: "1"' in text
    assert "arb-snowflake-onnx-${{ github.run_id }}" in text
    assert "--dense-model-dir" in text
    assert "--require-dense" in text
    assert ".[dev,retrieval]" in text
    assert "inference_api\": False" in text
    assert "huggingface_hub" not in text
    assert "hf download" not in text
    assert '\n          done\n      - name: Run official all-files lexical baselines' not in text
