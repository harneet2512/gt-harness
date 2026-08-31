from __future__ import annotations

import json
import subprocess
import sys

from scripts.gt_reaudit import run_reaudit, verify_reaudit_receipt


def test_public_reaudit_is_deterministic_and_content_addressed(tmp_path):
    first = run_reaudit(tmp_path)
    second = run_reaudit(tmp_path)
    assert first == second
    assert first["schema"] == "gt.public_reaudit.v1"
    assert first["status"] == "ABSTAINED"
    assert first["failure_code"] == "SOURCE_MISSING"
    assert verify_reaudit_receipt(first)
    assert not verify_reaudit_receipt({**first, "receipt_sha256": "0" * 64})


def test_cli_returns_stable_nonzero_source_failure(tmp_path):
    output = tmp_path / "receipt.json"
    result = subprocess.run(
        [sys.executable, "scripts/gt_reaudit.py", "--groundtruth-root", str(tmp_path), "--output", str(output)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["failure_code"] == "SOURCE_MISSING"

