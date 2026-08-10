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
    assert "prepare_arb_redacted_inputs.py" in text
    assert "--shard-index" in text
    assert "--shard-count" in text
    assert "--candidate-filter all_files" in text
    assert "arb-official-baselines-${{ github.run_id }}" in text
