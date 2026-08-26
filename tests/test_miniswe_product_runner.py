from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

import scripts.harness_real_repository_campaign as harness_campaign
from gt_harness.miniswe_runner import (
    MODEL_REQUEST_TIMEOUT_SECONDS,
    CredentialIsolatedEnvironment,
    TreatmentMiniSweAgent,
    build_miniswe_agent,
)
from gt_harness.treatments import BareTreatment
from scripts.harness_real_repository_campaign import _ObservationModel


def test_provider_free_campaign_uses_explicit_edit_intent() -> None:
    assert harness_campaign._E2E_TASK.startswith("Change `Signer`")


def test_harness_campaign_rejects_an_unpinned_miniswe_scaffold(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(harness_campaign, "version", lambda _name: "2.2.8")

    with pytest.raises(RuntimeError, match="expected 2.4.6, got 2.2.8"):
        harness_campaign.run_campaign(
            source_repository=tmp_path / "repository",
            commit="a" * 40,
            run_dir=tmp_path / "run",
            output=tmp_path / "receipt.json",
            dense_model_dir=tmp_path / "model",
        )


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


class _TurnAugmentingTreatment(BareTreatment):
    def __init__(self) -> None:
        super().__init__()
        self.turns = []

    def after_actions(self, observations):
        from gt_harness.treatments import ObservationAugmentation

        self.turns.append(tuple(observations))
        return ObservationAugmentation(
            content=(
                "<groundtruth-repository-context>one turn fact"
                "</groundtruth-repository-context>"
            ),
            raw_output_sha256=hashlib.sha256(
                observations[-1].output.encode("utf-8")
            ).hexdigest(),
            context_sha256="b" * 64,
            delivery_index=2,
            source_revision="revision-2",
            observation_count=len(observations),
            turn_observations_sha256="c" * 64,
        )


class _MultiActionModel(_Model):
    def format_observation_messages(self, message, outputs, variables):
        return tuple(
            {
                "role": "tool",
                "content": output["output"],
                "extra": dict(output.get("extra", {})),
            }
            for output in outputs
        )


class _MultiActionEnvironment(_Environment):
    def execute(self, action):
        return {
            "output": str(action["command"]),
            "returncode": 0,
            "exception_info": "",
        }


def test_provider_free_campaign_model_preserves_miniswe_trajectory_metadata() -> None:
    receipt = {"schema": "gt.observation_augmentation.v3", "delivery_index": 2}

    message = _ObservationModel().format_observation_messages(
        {},
        [
            {
                "output": "raw output\n\n<groundtruth-repository-context />",
                "extra": {
                    "gt_raw_output": "raw output",
                    "gt_delivery_receipt": receipt,
                },
            }
        ],
        {},
    )[0]

    assert message["extra"]["gt_raw_output"] == "raw output"
    assert message["extra"]["gt_delivery_receipt"] == receipt


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


def test_multiple_actions_produce_at_most_one_turn_level_augmentation() -> None:
    treatment = _TurnAugmentingTreatment()
    agent = TreatmentMiniSweAgent(
        _MultiActionModel(),
        _MultiActionEnvironment(),
        system_template="system",
        instance_template="task={{task}}",
        step_limit=3,
        cost_limit=0.0,
        treatment=treatment,
    )
    assistant = {
        "role": "assistant",
        "content": "inspect",
        "extra": {
            "actions": [
                {"command": "sed -n '1,20p' src/a.py"},
                {"command": "sed -n '1,20p' src/b.py"},
            ]
        },
    }

    observations = agent.execute_actions(assistant)

    assert len(treatment.turns) == 1
    assert len(treatment.turns[0]) == 2
    assert sum("one turn fact" in message["content"] for message in observations) == 1
    assert "one turn fact" in observations[-1]["content"]
    receipt = observations[-1]["extra"].get("gt_delivery_receipt")
    assert receipt is not None
    assert receipt["observation_count"] == 2
    assert receipt["raw_output_sha256"] == hashlib.sha256(
        b"sed -n '1,20p' src/b.py"
    ).hexdigest()
    assert receipt["turn_observations_sha256"]
    assert observations[-1]["extra"]["gt_raw_output"] == "sed -n '1,20p' src/b.py"


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
    assert os.environ["MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT"] == "6"


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
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout
    assert completed.stdout.strip() == "READY False"
