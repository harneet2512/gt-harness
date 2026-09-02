from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "eval" / "deepswe_smoke20_v1.json"
BASELINE = ROOT / "eval" / "muse_spark_1_2_smoke20_baseline.json"


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_smoke20_declares_the_retained_muse_openrouter_baseline() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    declaration = manifest["baseline"]
    assert declaration["model"] == "meta/muse-spark-1.2-contributor"
    assert declaration["effective_route"] == "openai/meta/muse-spark-1.2-contributor"
    assert declaration["provider"] == "openrouter"
    assert declaration["source"] == "HAR-82"
    assert declaration["receipt"] == BASELINE.name
    assert declaration["receipt_sha256"] == _canonical_sha256(baseline)


def test_retained_muse_slice_covers_exact_smoke20_order_with_four_trials() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    assert baseline["schema"] == "gt.deepswe_muse_baseline.v1"
    assert baseline["declared_model"] == "meta/muse-spark-1.2-contributor"
    assert baseline["effective_route"] == "openai/meta/muse-spark-1.2-contributor"
    assert baseline["provider"] == "openrouter"
    assert baseline["source_locator"] == "HAR-82"
    assert [row["task_name"] for row in baseline["tasks"]] == manifest["task_ids"]
    assert all(row["aggregate"]["trials"] == 4 for row in baseline["tasks"])
    assert all(len(row["trials"]) == 4 for row in baseline["tasks"])
    assert all(
        trial["provider"] == "openrouter"
        and trial["model"] == "muse-spark-1-2"
        and trial["config"] == "mini_swe_agent_muse_spark_1_2_xhigh"
        for row in baseline["tasks"]
        for trial in row["trials"]
    )
