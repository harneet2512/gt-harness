from __future__ import annotations

import os
import subprocess
import sys

from gt_harness.miniswe_runner import TreatmentMiniSweAgent
from gt_harness.treatments import BareTreatment


class _Treatment(BareTreatment):
    def prepare(self, task: str) -> str:
        return "deterministic repository fact"

    def after_action(self, name, arguments, output, is_error) -> None:
        self.observed = (name, dict(arguments), output, is_error)


class _Model:
    def __init__(self) -> None:
        self.queries = []

    def get_template_vars(self):
        return {}

    def format_message(self, *, role, content, extra=None):
        return {"role": role, "content": content, "extra": extra or {}}

    def query(self, messages):
        self.queries.append(list(messages))
        return {
            "role": "assistant",
            "content": "run it",
            "extra": {"actions": [{"command": "echo ok"}]},
        }

    def format_observation_messages(self, message, outputs, variables):
        return (
            {
                "role": "exit",
                "content": outputs[0]["output"],
                "extra": {"exit_status": "Submitted", "submission": "done"},
            },
        )

    def serialize(self):
        return {}


class _Environment:
    def get_template_vars(self):
        return {}

    def execute(self, action):
        assert action == {"command": "echo ok"}
        return {"output": "ok", "returncode": 0, "exception_info": ""}

    def serialize(self):
        return {}


def test_miniswe_treatment_is_advisory_and_observes_unmodified_action() -> None:
    model = _Model()
    treatment = _Treatment()
    agent = TreatmentMiniSweAgent(
        model,
        _Environment(),
        system_template="system",
        instance_template="task={{task}}",
        step_limit=3,
        cost_limit=0.0,
        treatment=treatment,
    )

    result = agent.run("fix it")

    assert result["exit_status"] == "Submitted"
    assert model.queries[0][1]["content"] == (
        "task=fix it\n\ndeterministic repository fact"
    )
    assert treatment.observed == ("bash", {"command": "echo ok"}, "ok", False)


def test_miniswe_product_import_is_quiet_under_non_utf8_console(tmp_path) -> None:
    env = dict(os.environ)
    env.pop("MSWEA_SILENT_STARTUP", None)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        env.pop(name, None)
    config_dir = tmp_path / "mini-config"
    config_dir.mkdir()
    (config_dir / ".env").write_text(
        "OPENAI_API_KEY=must-not-enter-product-environment\n",
        encoding="utf-8",
    )
    env["MSWEA_GLOBAL_CONFIG_DIR"] = str(config_dir)
    env["PYTHONIOENCODING"] = "cp1252"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; import gt_harness.miniswe_runner; "
                "print('READY', 'OPENAI_API_KEY' in os.environ)"
            ),
        ],
        text=True,
        encoding="cp1252",
        errors="strict",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout
    assert completed.stdout.strip() == "READY False"
