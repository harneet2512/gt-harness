from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_tb2_merge_module_is_import_safe_without_harbor_artifacts(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GT_")
        and key
        not in {
            "EXPECTED_TASKS_JSON",
            "PREDICTION_SHA256",
            "PROVIDER_FREE_COMMIT",
            "PROVIDER_FREE_STATUS",
            "COMPARISON_PROFILE",
            "GITHUB_STEP_SUMMARY",
        }
    }
    environment["PYTHONPATH"] = str(root)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import scripts.tb2_merge_results as module; assert callable(module.merge_results)",
        ],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert not tuple(tmp_path.iterdir())
