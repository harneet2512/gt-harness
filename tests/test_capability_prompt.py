from __future__ import annotations

from scripts.miniswe_gt_run import _render_gt_advisory_system


def test_gt_system_text_is_optional_and_nonexclusive():
    rendered = _render_gt_advisory_system(
        "[GT_TASK_CONTRACT]\n- compute handles empty input",
        "src/mod.py: compute",
    )
    lowered = rendered.lower()
    assert "optional" in lowered
    assert "may ignore" in lowered
    assert "inspect any" in lowered
    assert "gt requires" not in lowered
    assert "binding instruction" not in lowered
    assert "work only" not in lowered
    assert "forbidden" not in lowered
