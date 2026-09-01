import json
from pathlib import Path

from scripts.issue_har72_design import issue


def test_har72_design_is_explicitly_not_ready(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "design.json"
    receipt = issue(root=root, output=output)
    assert receipt["benchmark_ready"] is False
    assert receipt["provider_calls"] == 0
    assert receipt["benchmark_runs"] == 0
    assert receipt["status"] == "BENCHMARK_READY_AWAITING_USER_RUN_APPROVAL"
    assert receipt["design_sha256"]
    assert json.loads(output.read_text())["design_sha256"] == receipt["design_sha256"]
