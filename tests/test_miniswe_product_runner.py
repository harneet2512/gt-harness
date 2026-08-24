from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time

from gt_harness.miniswe_runner import (
    MODEL_REQUEST_TIMEOUT_SECONDS,
    CredentialIsolatedEnvironment,
    TreatmentMiniSweAgent,
    build_miniswe_agent,
)
from gt_harness.treatments import BareTreatment


class _Treatment(BareTreatment):
    def prepare(self, task: str) -> str:
        return "deterministic repository fact"

    def after_action(self, name, arguments, output, is_error) -> None:
        self.observed = (name, dict(arguments), output, is_error)


class _AugmentingTreatment(_Treatment):
    def before_model_call(self, iteration: int) -> str:
        # The runner must use this hook only as an integrity barrier.  Context
        # returned here must never become a synthetic user turn.
        return "late context must not be injected"

    def after_action(self, name, arguments, output, is_error):
        from gt_harness.treatments import ObservationAugmentation

        super().after_action(name, arguments, output, is_error)
        return ObservationAugmentation(
            content="<groundtruth-repository-context>fresh fact</groundtruth-repository-context>",
            raw_output_sha256="2689367b205c16ce32ed4200942b8b1e",
            context_sha256="fb1ad99f7e71862b188faec62afc5e8e",
            delivery_index=2,
            source_revision="revision-2",
        )


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


def test_gt_update_is_appended_to_same_observation_without_mutating_raw_output() -> None:
    model = _Model()
    treatment = _AugmentingTreatment()
    agent = TreatmentMiniSweAgent(
        model,
        _Environment(),
        system_template="system",
        instance_template="task={{task}}",
        step_limit=3,
        cost_limit=0.0,
        treatment=treatment,
    )

    assistant = {
        "role": "assistant",
        "content": "read",
        "extra": {"actions": [{"command": "echo ok"}]},
    }
    observations = agent.execute_actions(assistant)

    assert treatment.observed == ("bash", {"command": "echo ok"}, "ok", False)
    assert len(observations) == 1
    assert observations[0]["content"].startswith("ok")
    assert "fresh fact" in observations[0]["content"]
    assert not any(
        message.get("role") == "user" and "late context" in message.get("content", "")
        for message in agent.messages
    )


def test_time_budget_exits_cleanly_before_harbor_kills_the_agent() -> None:
    agent = TreatmentMiniSweAgent(
        _Model(),
        _Environment(),
        system_template="system",
        instance_template="task={{task}}",
        step_limit=3,
        cost_limit=0.0,
        treatment=_Treatment(),
        time_budget_seconds=-1,
    )

    result = agent.run("fix it")

    assert result["exit_status"] == "LimitsExceeded"
    assert result["limit_reason"] == "time_budget"
    assert agent.messages[-1]["content"] == "TimeBudgetExceeded"


def test_timed_shell_action_terminates_descendants(tmp_path) -> None:
    """A timed action must not leave a child holding Harbor's pipe open."""

    if os.name == "nt":
        return
    marker = tmp_path / "orphan-marker"
    child = (
        "import pathlib,time; time.sleep(5); "
        f"pathlib.Path({str(marker)!r}).write_text('orphan')"
    )
    launcher = (
        "import subprocess,time; "
        f"subprocess.Popen({[sys.executable, '-c', child]!r}); "
        "time.sleep(60)"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(launcher)}"
    environment = CredentialIsolatedEnvironment(
        config_class=type(
            "Config",
            (),
            {"cwd": str(tmp_path), "timeout": 30, "env": {}},
        ),
        cwd=str(tmp_path),
        timeout=30,
    )
    started = time.monotonic()
    result = environment.execute({"command": command}, timeout=1)
    elapsed = time.monotonic() - started

    assert result["returncode"] == -1
    assert "timed out" in result["exception_info"]
    assert elapsed < 4
    time.sleep(1)
    assert not marker.exists()


def test_product_model_calls_are_transport_bounded_and_boundedly_retried(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT", "10")

    agent = build_miniswe_agent(
        model="openai/test-model",
        root=tmp_path,
        treatment=BareTreatment(),
        base_url="https://example.invalid/v1",
        temperature=1.0,
        max_iterations=3,
        time_budget_seconds=720,
        trajectory_path=None,
    )

    assert agent.model.config.model_kwargs["timeout"] == MODEL_REQUEST_TIMEOUT_SECONDS
    assert os.environ["MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT"] == "3"


def test_query_near_deadline_shrinks_provider_transport_timeout(monkeypatch) -> None:
    model = _Model()
    model.config = type("Config", (), {"model_kwargs": {"timeout": 60.0}})()
    agent = TreatmentMiniSweAgent(
        model,
        _Environment(),
        system_template="system",
        instance_template="task={{task}}",
        step_limit=3,
        cost_limit=0.0,
        treatment=_Treatment(),
        time_budget_seconds=20,
    )
    agent._started_clock = 100.0
    monkeypatch.setattr("gt_harness.miniswe_runner.time.monotonic", lambda: 115.0)

    agent.query()

    assert model.config.model_kwargs["timeout"] == 5.0


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
