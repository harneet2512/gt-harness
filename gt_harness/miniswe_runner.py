"""Official Mini-SWE execution boundary for GT Harness.

Both benchmark arms use the same pinned Mini-SWE loop, prompt, Bash tool, and
environment.  The treatment difference is limited to the deterministic
``BareTreatment``/``GroundTruthTreatment`` context hook.
"""

# ruff: noqa: I001 -- the import guard must execute before Mini-SWE imports

from __future__ import annotations

import json
import os
import subprocess
import time
from copy import deepcopy
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gt_harness._miniswe_import_guard import restore_environment

import yaml
from minisweagent.agents.default import AgentConfig, DefaultAgent, LimitsExceeded
from minisweagent.config import builtin_config_dir
from minisweagent.environments.local import LocalEnvironment, LocalEnvironmentConfig
from minisweagent.models.litellm_model import BASH_TOOL, LitellmModel

from gt_harness.treatments import BareTreatment

# Mini-SWE 2.2.8 prints a Unicode startup banner and loads a user-global .env
# at import time.  The guard is imported before Mini-SWE (ruff sorts this
# before the scaffold imports, then this restores the explicit process environment.
restore_environment()

_SENSITIVE_ENV = {
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "HF_TOKEN",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
}

MODEL_REQUEST_TIMEOUT_SECONDS = 60.0
MODEL_RETRY_ATTEMPTS = 1


def _sensitive(name: str) -> bool:
    upper = str(name or "").upper()
    return upper in _SENSITIVE_ENV or upper.endswith(
        ("_API_KEY", "_ACCESS_TOKEN", "_AUTH_TOKEN", "_PASSWORD", "_SECRET")
    )


class CredentialIsolatedEnvironment(LocalEnvironment):
    """Keep provider credentials out of model-executed shell commands."""

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict:
        command = action.get("command", "")
        cwd = cwd or self.config.cwd or os.getcwd()
        execution_env = {
            key: value
            for key, value in (os.environ | self.config.env).items()
            if not _sensitive(key)
        }
        try:
            completed = subprocess.run(
                command,
                shell=True,
                text=True,
                cwd=cwd,
                env=execution_env,
                timeout=timeout or self.config.timeout,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            output = {
                "output": completed.stdout,
                "returncode": completed.returncode,
                "exception_info": "",
            }
        except Exception as exc:  # noqa: BLE001 - matches Mini-SWE's recoverable shell contract
            raw = getattr(exc, "output", None)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            output = {
                "output": str(raw or ""),
                "returncode": -1,
                "exception_info": f"An error occurred while executing the command: {exc}",
                "extra": {"exception_type": type(exc).__name__, "exception": str(exc)},
            }
        self._check_finished(output)
        return output

    def get_template_vars(self, **kwargs):
        values = super().get_template_vars(**kwargs)
        return {
            key: value for key, value in values.items() if not _sensitive(str(key))
        }


class TreatmentMiniSweAgent(DefaultAgent):
    """Stock Mini-SWE loop with a bounded, observation-only treatment seam."""

    def __init__(
        self,
        *args,
        treatment: BareTreatment,
        on_message: Callable[[dict[str, Any]], None] | None = None,
        time_budget_seconds: float | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.treatment = treatment
        self.on_message = on_message
        self.time_budget_seconds = (
            float(time_budget_seconds) if time_budget_seconds is not None else None
        )
        self._started_clock = 0.0

    def add_messages(self, *messages: dict) -> list[dict]:
        result = super().add_messages(*messages)
        if self.on_message is not None:
            for message in messages:
                self.on_message(json.loads(json.dumps(message, default=str)))
        return result

    def run(self, task: str = "", **kwargs) -> dict:
        self._started_clock = time.monotonic()
        initial_context = self.treatment.prepare(task)
        effective_task = task + ("\n\n" + initial_context if initial_context else "")
        return super().run(effective_task, **kwargs)

    def query(self) -> dict:
        remaining = self._remaining_seconds()
        if remaining is not None and remaining <= 0:
            raise LimitsExceeded(
                {
                    "role": "exit",
                    "content": "TimeBudgetExceeded",
                    "extra": {
                        "exit_status": "LimitsExceeded",
                        "limit_reason": "time_budget",
                        "submission": "",
                    },
                }
            )
        if remaining is not None:
            # A request beginning near the inner deadline must not consume the
            # supervisor gap. Litellm query kwargs override the static cap.
            self.model.config.model_kwargs["timeout"] = max(
                1.0, min(MODEL_REQUEST_TIMEOUT_SECONDS, remaining)
            )
        # Integrity barrier only. Context updates belong to the observation
        # that produced them; injecting a synthetic user turn here weakens
        # causal attribution and makes the model reason one step too late.
        self.treatment.before_model_call(self.n_calls + 1)
        return super().query()

    def _remaining_seconds(self) -> float | None:
        if self.time_budget_seconds is None:
            return None
        return self.time_budget_seconds - (time.monotonic() - self._started_clock)

    def execute_actions(self, message: dict) -> list[dict]:
        outputs: list[dict[str, Any]] = []
        for action in message.get("extra", {}).get("actions", []):
            remaining = self._remaining_seconds()
            action_timeout = None
            if remaining is not None:
                if remaining <= 0:
                    raise LimitsExceeded(
                        {
                            "role": "exit",
                            "content": "TimeBudgetExceeded",
                            "extra": {
                                "exit_status": "LimitsExceeded",
                                "limit_reason": "time_budget",
                                "submission": "",
                            },
                        }
                    )
                action_timeout = max(1, min(30, int(remaining)))
            if action_timeout is None:
                output = self.env.execute(action)
            else:
                output = self.env.execute(action, timeout=action_timeout)
            remaining = self._remaining_seconds()
            if remaining is not None and remaining <= 0:
                raise LimitsExceeded(
                    {
                        "role": "exit",
                        "content": "TimeBudgetExceeded",
                        "extra": {
                            "exit_status": "LimitsExceeded",
                            "limit_reason": "time_budget",
                            "submission": "",
                        },
                    }
                )
            raw_text = str(output.get("output") or output.get("exception_info") or "")
            augmentation = self.treatment.after_action(
                "bash",
                dict(action),
                raw_text,
                int(output.get("returncode") or 0) != 0,
            )
            provider_output = deepcopy(output)
            if augmentation is not None:
                separator = "\n\n" if raw_text else ""
                provider_output["output"] = raw_text + separator + augmentation.content
                provider_output["gt_delivery_receipt"] = augmentation.as_dict()
            outputs.append(provider_output)
        return self.add_messages(
            *self.model.format_observation_messages(
                message, outputs, self.get_template_vars()
            )
        )


@dataclass(frozen=True, slots=True)
class MiniSweRunResult:
    transcript: tuple[dict[str, Any], ...]
    stop_reason: str
    iterations: int
    total_input_tokens: int
    total_output_tokens: int
    total_cache_read_tokens: int
    exception: BaseException | None = None


def _templates() -> dict[str, Any]:
    return yaml.safe_load((builtin_config_dir / "mini.yaml").read_text(encoding="utf-8"))


def _usage(messages: list[dict]) -> tuple[int, int, int]:
    input_tokens = output_tokens = cached_tokens = 0
    for message in messages:
        response = message.get("extra", {}).get("response", {})
        usage = response.get("usage", {}) if isinstance(response, dict) else {}
        input_tokens += int(usage.get("prompt_tokens") or 0)
        output_tokens += int(usage.get("completion_tokens") or 0)
        details = usage.get("prompt_tokens_details") or {}
        if isinstance(details, dict):
            cached_tokens += int(details.get("cached_tokens") or 0)
    return input_tokens, output_tokens, cached_tokens


def build_miniswe_agent(
    *,
    model: str,
    root: Path,
    treatment: BareTreatment,
    base_url: str | None,
    temperature: float | None,
    max_iterations: int,
    time_budget_seconds: float | None,
    trajectory_path: Path | None,
    on_message: Callable[[dict[str, Any]], None] | None = None,
) -> TreatmentMiniSweAgent:
    config = _templates()
    model_name = str(model)
    model_kwargs = dict(config.get("model", {}).get("model_kwargs") or {})
    if time_budget_seconds is not None:
        model_kwargs["timeout"] = MODEL_REQUEST_TIMEOUT_SECONDS
        os.environ["MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT"] = str(
            MODEL_RETRY_ATTEMPTS
        )
    if temperature is not None:
        model_kwargs["temperature"] = float(temperature)
    if base_url:
        if "/" not in model_name:
            model_name = f"openai/{model_name}"
        model_kwargs["api_base"] = str(base_url)
    model_object = LitellmModel(
        model_name=model_name,
        model_kwargs=model_kwargs,
        observation_template=str(config["model"]["observation_template"]),
        format_error_template=str(config["model"]["format_error_template"]),
        cost_tracking="ignore_errors",
    )
    environment = CredentialIsolatedEnvironment(
        config_class=LocalEnvironmentConfig,
        cwd=str(root),
        timeout=30,
        env=dict(config.get("environment", {}).get("env") or {}),
    )
    return TreatmentMiniSweAgent(
        model_object,
        environment,
        config_class=AgentConfig,
        system_template=str(config["agent"]["system_template"]),
        instance_template=str(config["agent"]["instance_template"]),
        step_limit=max(1, int(max_iterations)),
        cost_limit=0.0,
        output_path=trajectory_path,
        treatment=treatment,
        on_message=on_message,
        time_budget_seconds=time_budget_seconds,
    )


def run_miniswe_agent(agent: TreatmentMiniSweAgent, task: str) -> MiniSweRunResult:
    exception: BaseException | None = None
    try:
        result = agent.run(task)
    except BaseException as exc:  # Mini-SWE uses typed exceptions for terminal submit
        exception = exc
        result = agent.messages[-1].get("extra", {}) if agent.messages else {}
        if type(exc).__name__ not in {"Submitted", "LimitsExceeded"}:
            raise
    stop_reason = str(
        result.get("exit_status") or (type(exception).__name__ if exception else "")
    )
    input_tokens, output_tokens, cached_tokens = _usage(agent.messages)
    return MiniSweRunResult(
        transcript=tuple(json.loads(json.dumps(agent.messages, default=str))),
        stop_reason=stop_reason,
        iterations=agent.n_calls,
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        total_cache_read_tokens=cached_tokens,
        exception=exception,
    )


__all__ = [
    "BASH_TOOL",
    "CredentialIsolatedEnvironment",
    "MiniSweRunResult",
    "MODEL_REQUEST_TIMEOUT_SECONDS",
    "MODEL_RETRY_ATTEMPTS",
    "TreatmentMiniSweAgent",
    "build_miniswe_agent",
    "run_miniswe_agent",
]
