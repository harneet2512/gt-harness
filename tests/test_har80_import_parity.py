from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from gt_engine.har80_import_parity import GT_ENGINE_IMPORTS, check_import_parity


def test_route_b_import_parity_uses_wheel_surface() -> None:
    receipt = json.loads(Path("gt_finalstand/receipts/har80_route_b.json").read_text())
    body = dict(receipt)
    supplied = body.pop("receipt_sha256")
    assert hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest() == supplied
    assert receipt["schema"] == "gt.har80.route_b.v1"
    assert receipt["route"] == "B"
    assert receipt["python_runtime"]["import_parity_schema"] == "gt.har80.import_parity.v1"
    assert tuple(receipt["modules"]) == GT_ENGINE_IMPORTS
    result = check_import_parity(source_root=os.environ.get("GROUNDTRUTH_ROOT"))
    assert result["passed"], result


def test_route_b_import_parity_rejects_declared_source_root() -> None:
    result = check_import_parity()
    assert result["passed"], result
    origin = Path(result["origins"]["groundtruth.runtime.gateway"])
    rejected = check_import_parity(source_root=origin.parents[2])
    assert not rejected["passed"]
    assert any("uncertified source tree" in error for error in rejected["errors"])
