import json
import os
import subprocess
import sys

from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.persistent_plan import build_plan_inputs
from gt_engine.persistent_plan.bootstrap import build_plan, build_planning_messages
from gt_engine.persistent_plan.ledger import build_requirement_ledger
from gt_engine.persistent_plan.render import render_plan_block


def test_full_source_survives_planning_and_installed_cli_retrieval(tmp_path):
    task = ("# Requirements\nThe serializer must preserve this output.\n\n"
            "    {\n        value: '" + "long-value" * 1500 + "'\n    }\n\n"
            "~~~js\n  render({value: 3})\n~~~\n")
    inputs = build_plan_inputs(task, repo_root=str(tmp_path), capture_baseline=False)
    plan = build_plan(None, inputs)
    assert task in build_planning_messages(inputs, task)[1]["content"]
    adapter = MiniSweAdapter(task_id="source", repo_root=str(tmp_path), state_dir=tmp_path / "state", predicates=())
    adapter.persistent_plan = plan
    adapter.publish_plan_state()
    result = subprocess.run([sys.executable, "-m", "gt_engine.persistent_plan.cli", "show", "--source"],
                            env={**os.environ, "GT_PLAN_ROOT": str(adapter.store.root / "plan")},
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    source = json.loads(result.stdout)
    assert source["source_spans"] == [[number, line] for number, line in enumerate(task.splitlines(), 1)]
    assert "gt-plan show --source" in render_plan_block(plan, limit=1)


def test_fence_length_and_delimiter_preserve_embedded_fences():
    task = "The renderer must retain nested fences.\n````md\n```python\npass\n```\n````\n"
    ledger = build_requirement_ledger(task)
    assert len(ledger.rows) == 1
    assert ledger.rows[0].examples == ("````md\n```python\npass\n```\n````",)


def test_tilde_example_is_not_promoted_to_a_requirement():
    ledger = build_requirement_ledger("The renderer must work.\n~~~py\nexample_only()\n~~~\n")
    assert len(ledger.rows) == 1
    assert ledger.rows[0].examples == ("~~~py\nexample_only()\n~~~",)


def test_source_retrieval_rejects_mutating_operations(tmp_path, monkeypatch, capsys):
    from gt_engine.persistent_plan.cli import main

    (tmp_path / "current.json").write_text(json.dumps({"rows": [], "source_spans": [[1, "retained"]]}))
    monkeypatch.setenv("GT_PLAN_ROOT", str(tmp_path))
    assert main(["revise", "--source"]) == 1
    assert not (tmp_path / "requests").exists()
    assert "only valid with show" in capsys.readouterr().err


def test_unterminated_leading_example_keeps_its_actual_source_line():
    ledger = build_requirement_ledger("\n# Example\n\n~~~py\nx = 1\n")
    assert ledger.unclassified_spans == ((4, "~~~py\nx = 1"),)
