"""Run pinned Mini-SWE-Agent 2.x with the GT lifecycle adapter (or GT-off).

``--gt-off`` builds the stock Mini-SWE agent only. Every gt_engine import is
lazy (inside ``build_agent``, behind the flag) so a GT-off run never imports
gt_engine/groundtruth and the container needs no groundtruth wheel.

Model routing: litellm refuses a bare model name when a gateway is configured,
so ``OPENAI_BASE_URL`` maps ``<model>`` to ``openai/<model>`` + ``api_base``.
``MSWEA_COST_TRACKING=ignore_errors`` keeps cost accounting from aborting
trials for a gateway model id litellm has no price for (tokens stay in the
trajectory; cost is derived at freeze time).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from gt_harness.process_boundary import harden_process_secret_boundary

if TYPE_CHECKING:  # runtime-safe: never imported when running GT-off
    from gt_engine.gt_session import GTSession
    from gt_engine.miniswe_integration import MiniSweAdapter

# minisweagent's __init__ prints a banner through rich; on Windows a cp1252
# stdout raises UnicodeEncodeError before main() can set PYTHONUTF8. Force UTF-8
# on the streams before any package import.
for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001 - stream reconfigure is best-effort
            pass
os.environ.setdefault("PYTHONUTF8", "1")
# Must precede the minisweagent import: LitellmModelConfig.cost_tracking's
# default is evaluated at class-definition time. Identical for both arms.
os.environ.setdefault("MSWEA_COST_TRACKING", "ignore_errors")

from minisweagent.agents.default import AgentConfig, DefaultAgent  # noqa: E402
from minisweagent.config import builtin_config_dir  # noqa: E402
from minisweagent.environments.local import LocalEnvironment, LocalEnvironmentConfig  # noqa: E402
from minisweagent.exceptions import Submitted  # noqa: E402
from minisweagent.models.litellm_model import LitellmModel  # noqa: E402

try:  # standalone remote runner first; package import for local tests second
    from miniswe_repro import (  # type: ignore[import-not-found]  # noqa: E402
        RunReceiptObserver,
        build_reproducibility_manifest,
        write_reproducibility_manifest,
    )
except ModuleNotFoundError:  # pragma: no cover - branch depends on invocation path
    from scripts.miniswe_repro import (  # noqa: E402
        RunReceiptObserver,
        build_reproducibility_manifest,
        write_reproducibility_manifest,
    )


_SENSITIVE_SHELL_ENV = {
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
}

# Match the bounded-output behavior of nano/tools.py. Mini-SWE otherwise keeps
# every byte of every command result and resends the complete history on each
# provider call; one 430k-character grep result caused 117k extra prompt tokens
# per subsequent call in paid run 33915825554.
MAX_TOOL_OUTPUT_CHARS = 16_000


def _truncate_tool_output(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    dropped = len(text) - limit
    return f"{text[:half]}\n... [truncated {dropped} chars] ...\n{text[-half:]}"


def _history_reference_marker(digest: str, size: int, tool_call_id: str) -> str:
    reference = {"sha256": digest, "utf8_bytes": size, "tool_call_id": tool_call_id}
    return "[GT_HISTORY_REF " + json.dumps(reference, sort_keys=True, separators=(",", ":")) + "]"


def _compact_miniswe_history(messages: list[dict]) -> None:
    """Reference byte-identical older tool results, never discard unique evidence.

    Each reference points directly to a full result in this request. The most
    recent action batch, assistant reasoning, and tool-call pairing stay intact.
    Original content stays under extra for exact recovery if an anchor leaves
    the history. Equality does not certify that an old observation is current.
    """
    last_assistant_index = max(
        (
            index
            for index, row in enumerate(messages)
            if row.get("role") == "assistant"
        ),
        default=len(messages),
    )
    results = Counter(
        row.get("tool_call_id") for row in messages
        if row.get("role") == "tool" and isinstance(row.get("tool_call_id"), str)
    )
    calls = Counter(
        call.get("id") for row in messages if row.get("role") == "assistant"
        if isinstance(row.get("tool_calls"), list)
        for call in (row.get("tool_calls") or [])
        if isinstance(call, dict) and isinstance(call.get("id"), str)
    )
    anchors: dict[str, str] = {}
    for index in range(len(messages) - 1, -1, -1):
        row = messages[index]
        if row.get("role") != "tool" or not isinstance(row.get("content"), str):
            continue
        extra = row.get("extra", {})
        if not isinstance(extra, dict):
            continue
        previous = extra.get("gt_history_reference")
        content = row["content"]
        if previous is not None:
            if not isinstance(previous, dict) or previous.get("schema") != "gt.history_reference.v1":
                raise ValueError("history_reference_invalid")
            content = previous.get("original_content")
            if not isinstance(content, str):
                raise ValueError("history_reference_original_missing")
        encoded = content.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        if previous is not None:
            if (
                previous.get("sha256") != digest
                or previous.get("utf8_bytes") != len(encoded)
                or not isinstance(previous.get("tool_call_id"), str)
                or row["content"] != _history_reference_marker(digest, len(encoded), previous["tool_call_id"])
            ):
                raise ValueError("history_reference_digest_mismatch")
            row["content"] = content
            extra.pop("gt_history_reference")
        tool_call_id = row.get("tool_call_id")
        if not isinstance(tool_call_id, str) or not tool_call_id or results[tool_call_id] != 1 or calls[tool_call_id] != 1:
            continue
        raw = extra.get("raw_output")
        if raw is not None and not isinstance(raw, str):
            continue
        try:
            identity = json.dumps({
                "content_sha256": digest,
                "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw is not None else None,
                "returncode": extra.get("returncode"),
                "exception_info": extra.get("exception_info"),
            }, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            continue
        anchor = anchors.setdefault(identity, tool_call_id)
        if index >= last_assistant_index or anchor == tool_call_id:
            continue
        marker = _history_reference_marker(digest, len(encoded), anchor)
        if len(marker.encode("utf-8")) >= len(encoded):
            continue
        row.setdefault("extra", {})["gt_history_reference"] = {
            "schema": "gt.history_reference.v1", "sha256": digest,
            "utf8_bytes": len(encoded), "tool_call_id": anchor,
            "original_content": content,
        }
        row["content"] = marker

class BoundedHistoryAgent(DefaultAgent):
    """Mini-SWE 2.4.6 with lossless repeated-output references and unchanged tools."""

    def query(self) -> dict:
        _compact_miniswe_history(self.messages)
        return super().query()


def _repository_head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def _write_model_patch(
    repo: Path, baseline: str, output: Path, *, excluded_roots: tuple[Path, ...] = ()
) -> None:
    """Use the same task-only patch export on normal and interrupted exits."""
    from scripts.miniswe_supervisor import export_patch

    export_patch(repo, baseline, output, excluded_roots=excluded_roots)


def _is_sensitive_env_name(name: str) -> bool:
    upper = name.upper()
    return upper in _SENSITIVE_SHELL_ENV or upper.endswith((
        "_API_KEY", "_ACCESS_TOKEN", "_AUTH_TOKEN", "_PASSWORD", "_SECRET",
    ))


def _scrub_sensitive_mapping(value):
    if isinstance(value, dict):
        return {
            key: _scrub_sensitive_mapping(item)
            for key, item in value.items()
            if not _is_sensitive_env_name(str(key))
        }
    if isinstance(value, list):
        return [_scrub_sensitive_mapping(item) for item in value]
    return value


class GTOffControlError(ValueError):
    """A provider-free GT-off control cannot claim a complete identity."""


_GT_OFF_IDENTITY_FIELDS = (
    "model_label",
    "served_model",
    "miniswe_agent_version",
    "task_set_hash",
    "source_revision",
    "scaffold_hash",
    "provider_config_hash",
    "temperature",
    "step_limit",
    "timeout",
    "environment_hash",
)


def validate_gt_off_control(
    *,
    identity: dict[str, object],
    events: tuple[dict[str, object], ...] | list[dict[str, object]],
    expected_identity: dict[str, object] | None = None,
) -> dict[str, object]:
    """Validate and freeze a GT-off control without executing a benchmark.

    This function is intentionally pure: it accepts an already-captured trace
    and emits a content-addressed receipt.  Model-provider events are allowed;
    GroundTruth runtime/provider hooks are forbidden.  A caller may provide a
    previously frozen identity, in which case every field must match exactly.
    """
    if not isinstance(identity, dict):
        raise GTOffControlError("identity_missing")
    missing = [name for name in _GT_OFF_IDENTITY_FIELDS if name not in identity]
    if missing:
        raise GTOffControlError(f"identity_missing:{','.join(missing)}")
    if expected_identity is not None:
        mismatches = [
            name
            for name in _GT_OFF_IDENTITY_FIELDS
            if identity.get(name) != expected_identity.get(name)
        ]
        if mismatches:
            raise GTOffControlError(f"identity_mismatch:{','.join(mismatches)}")
    if identity["model_label"] != "deepseek 0731 v4":
        raise GTOffControlError("model_label_mismatch")
    if identity["miniswe_agent_version"] != "2.4.6":
        raise GTOffControlError("miniswe_agent_version_mismatch")
    for name in _GT_OFF_IDENTITY_FIELDS:
        value = identity[name]
        if isinstance(value, str) and not value.strip():
            raise GTOffControlError(f"identity_empty:{name}")
    if float(identity["temperature"]) < 0:
        raise GTOffControlError("temperature_invalid")
    if int(identity["step_limit"]) < 1 or int(identity["timeout"]) < 1:
        raise GTOffControlError("budget_invalid")

    canonical_events: list[dict[str, object]] = []
    for event in events:
        if not isinstance(event, dict):
            raise GTOffControlError("trace_event_invalid")
        event_text = json.dumps(event, sort_keys=True, separators=(",", ":")).lower()
        if "gt_engine" in event_text or "groundtruth" in event_text or "gt." in event_text:
            raise GTOffControlError("gt_hook_in_trace")
        canonical_events.append(event)
    trace_bytes = json.dumps(
        canonical_events, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    trace_digest = hashlib.sha256(trace_bytes).hexdigest()
    provider_events = sum(
        1
        for event in canonical_events
        if "provider" in str(event.get("event", "")).lower()
        or "model.request" in str(event.get("event", "")).lower()
    )
    return {
        "schema": "gt.off_control_receipt.v1",
        "gt_enabled": False,
        "control_mode": "off",
        **{name: identity[name] for name in _GT_OFF_IDENTITY_FIELDS},
        "trace_event_count": len(canonical_events),
        "provider_event_count": provider_events,
        "trace_sha256": trace_digest,
        "gt_hook_event_count": 0,
        "research_valid": True,
    }


class CredentialIsolatedLocalEnvironment(LocalEnvironment):
    """Stock local execution semantics with host credentials removed.

    Harbor already supplies a disposable task container. This class closes the
    remaining boundary inside that container: provider/GCP/GitHub credentials
    stay available to the model client process but never enter model-executed
    shell commands or template variables. It is used identically in both arms.
    """

    def execution_env(self) -> dict[str, str]:
        combined = os.environ | self.config.env
        return {
            key: value
            for key, value in combined.items()
            if not _is_sensitive_env_name(key)
        }

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict:
        command = action.get("command", "")
        cwd = cwd or self.config.cwd or os.getcwd()
        try:
            result = subprocess.run(
                command,
                shell=True,
                text=True,
                cwd=cwd,
                env=self.execution_env(),
                timeout=timeout or self.config.timeout,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            output = {
                "output": result.stdout,
                "returncode": result.returncode,
                "exception_info": "",
            }
        except Exception as exc:  # identical recoverable environment contract
            raw_output = getattr(exc, "output", None)
            raw_output = (
                raw_output.decode("utf-8", errors="replace")
                if isinstance(raw_output, bytes)
                else (raw_output or "")
            )
            output = {
                "output": raw_output,
                "returncode": -1,
                "exception_info": f"An error occurred while executing the command: {exc}",
                "extra": {"exception_type": type(exc).__name__, "exception": str(exc)},
            }
        try:
            self._check_finished(output)
        except Submitted as exc:
            # Keep the native terminal message intact and retain the actual
            # environment result if the runtime refuses only submission.
            raw = str(output.get("output") or "")
            exc.gt_execution_result = {
                **output, "output": _truncate_tool_output(raw),
                "extra": {**output.get("extra", {}), "raw_output": raw},
            }
            raise
        raw_output = str(output.get("output") or "")
        output.setdefault("extra", {})["raw_output"] = raw_output
        output["output"] = _truncate_tool_output(raw_output)
        return output

    def get_template_vars(self, **kwargs):
        return _scrub_sensitive_mapping(super().get_template_vars(**kwargs))


def _templates() -> tuple[str, str]:
    import yaml

    config = yaml.safe_load((builtin_config_dir / "mini.yaml").read_text())
    agent = config["agent"]
    return str(agent["system_template"]), str(agent["instance_template"])


def _model_and_kwargs(model: str, temperature: float) -> tuple[str, dict]:
    """litellm-routable model id + kwargs for the configured gateway."""
    model_kwargs: dict = {"temperature": temperature, "num_retries": 0}
    if model.removeprefix("openai/") == "meta/muse-spark-1.2-contributor":
        # The retained DeepSWE baseline used xhigh reasoning. OpenRouter's
        # OpenAI-compatible contract accepts this structured field, and
        # leaving it implicit makes GT-on vs baseline outcome comparisons
        # invalid even when the visible model identifier is identical.
        model_kwargs["reasoning"] = {"effort": "xhigh"}
    reserved_output = int(
        os.environ.get("GT_PROVIDER_RESERVED_OUTPUT_TOKENS", "0") or 0
    )
    if reserved_output > 0:
        # Admission subtracts this exact value from the provider's live window;
        # the transport must request the same reservation.
        # Relace advertises ``max_tokens`` as its supported request parameter.
        # ``max_completion_tokens`` is the catalog metadata field, not a
        # request parameter accepted by this locked endpoint. Keeping
        # require_parameters=true means the wrong spelling fails with a 404.
        model_kwargs["max_tokens"] = reserved_output
    base_url = os.environ.get("OPENAI_BASE_URL")
    if base_url:
        # An OpenAI-compatible gateway owns the full catalog identifier.  A
        # provider-prefixed id such as minimax/minimax-m3:free must still be
        # forced through LiteLLM's OpenAI adapter, otherwise LiteLLM selects
        # its native MiniMax adapter and ignores OPENAI_API_KEY.
        if not model.startswith("openai/"):
            model = f"openai/{model}"
        model_kwargs["api_base"] = base_url
        if model == "openai/deepseek/deepseek-v4-flash-0731":
            raw_routing = os.environ.get("GT_PROVIDER_ROUTING_JSON", "")
            try:
                routing = json.loads(raw_routing)
            except json.JSONDecodeError as exc:
                raise ValueError("provider_routing_env_invalid") from exc
            expected_routing = {
                "only": ["relace"],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
            if routing != expected_routing:
                raise ValueError("provider_routing_env_not_allowed")
            model_kwargs["extra_body"] = {"provider": routing}
    return model, model_kwargs


def _render_gt_advisory_system(contract_text: str, localization: str) -> str:
    """Render optional GT evidence without narrowing Mini-SWE's capabilities."""
    parts = [
        "[GT_ADVISORY_POLICY]\n"
        "GroundTruth supplies optional deterministic evidence. You may ignore "
        "or disagree with it, inspect any repository file available in the "
        "sandbox, run any allowed shell/search/test command, edit any "
        "permissible file, and pursue any hypothesis. Candidate locations are "
        "starting points, never boundaries."
    ]
    if contract_text:
        parts.append(contract_text)
    if localization:
        parts.append(
            "[GT_LOCALIZATION_ADVISORY]\n"
            "Optional high-evidence starting points (non-exclusive):\n"
            f"{localization}"
        )
    return "\n\n".join(parts)


def resolve_run_task_identity(canonical_task_id: str, task: str) -> str:
    """Identify a run by its planned task rather than by a digest of its text.

    The diagnostics journal, the external state store, and the
    reproducibility manifest are all addressed by this value, and
    attestation matches them against the planned cohort.  A digest of the
    task text is not a planned identifier, so artifacts written under it
    read as belonging to an unplanned task while the planned task reads as
    having produced nothing -- both halves of the same run.

    The digest remains only for ad-hoc callers that bind no identity; every
    workflow passes --task-id.
    """

    canonical = str(canonical_task_id or "").strip()
    if canonical:
        return canonical
    return hashlib.sha256(task.encode("utf-8")).hexdigest()[:16]


def build_agent(
    *,
    task: str,
    canonical_task_id: str = "",
    model: str,
    cwd: str,
    state_dir: str,
    output: str | None,
    temperature: float,
    gt_off: bool,
    gt_mode: str = "advisory",
    capability_modes: dict[str, str] | None = None,
    disabled_capabilities: tuple[str, ...] = (),
    step_limit: int = 100,
    timeout: int = 30,
    wall_time_limit_seconds: int = 0,
) -> tuple[DefaultAgent, MiniSweAdapter | None, GTSession | None]:
    system_template, instance_template = _templates()
    model_name, model_kwargs = _model_and_kwargs(model, temperature)
    global_killed = os.environ.get("GT_KILL_SWITCH", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    gt_disabled = gt_off or gt_mode == "off" or global_killed
    if gt_disabled:
        # GT-off remains the stock Mini-SWE model, including its Bash-only
        # provider schema and parser.
        model_obj = LitellmModel(model_name=model_name, model_kwargs=model_kwargs)
    else:
        from gt_engine.miniswe_typed_actions import GroundTruthLitellmModel

        model_obj = GroundTruthLitellmModel(
            model_name=model_name, model_kwargs=model_kwargs
        )
    task_id = resolve_run_task_identity(canonical_task_id, task)
    env_obj = CredentialIsolatedLocalEnvironment(
        config_class=LocalEnvironmentConfig,
        cwd=cwd,
        timeout=timeout,
    )
    if gt_disabled:
        agent = BoundedHistoryAgent(
            model_obj, env_obj,
            config_class=AgentConfig,
            system_template=system_template,
            instance_template=instance_template,
            step_limit=step_limit,
            wall_time_limit_seconds=wall_time_limit_seconds,
            output_path=Path(output) if output else None,
        )
        observer = RunReceiptObserver(
            Path(state_dir) / task_id,
            requested_model=model,
            resolved_model=model_name,
        )
        observer.install(agent.model)
        return agent, None, None

    from gt_engine.bridge import apply_profile_env
    from gt_engine.gt_session import GTMode, GTSession, GTSessionConfig
    from gt_engine.indexer import BenchmarkGraphRequired, ensure_index_with_receipt
    from gt_engine.miniswe_controller import Predicate
    from gt_engine.miniswe_integration import MiniSweAdapter
    from gt_engine.miniswe_runtime import install_runtime_hooks
    from gt_engine.task_contract import extract_task_contract, render_task_contract
    from gt_engine.verification_contract import compile_obligation_predicates

    apply_profile_env()
    # Set the gateway producer flags INTERNALLY (never rely on container env:
    # round-11 the model read GT_* from `env` and audited the harness source).
    # _ensure_gateway_flags covers the 6 producer flags; submit suppression is
    # the enforcement arm the submit gate reads.
    try:
        from gt_engine.engine.runner import _ensure_gateway_flags

        _ensure_gateway_flags()
        os.environ.setdefault("GT_SUBMIT_SUPPRESSION_ENFORCE", "1")
    except Exception:  # noqa: BLE001 - flags default off; engine sets them lazily
        pass
    contract = extract_task_contract(task)
    compiled = compile_obligation_predicates(contract)
    predicates = tuple(
        Predicate(item.predicate_id, contract_obligation.text)
        for contract_obligation in contract.obligations
        for item in (compiled[contract_obligation.obligation_id],)
    )
    graph_db = None
    index_error: Exception | None = None
    try:
        index_receipt = ensure_index_with_receipt(cwd, state_dir=state_dir)
        graph_db = index_receipt.graph_db if index_receipt.success else None
        if not index_receipt.success and index_receipt.error_type:
            index_error = RuntimeError(
                f"{index_receipt.error_type}: {index_receipt.error_diagnostic}"
            )
    except BenchmarkGraphRequired:
        # Indexing is an optional observer everywhere except here. On a
        # benchmark-bound run the graph is the product under measurement, so a
        # missing one is not an observation to record and continue past -- it
        # stops the run before a single provider call is billed.
        raise
    except Exception as exc:  # noqa: BLE001 - indexing is an optional observer
        index_error = exc
    adapter = MiniSweAdapter(
        task_id=task_id,
        state_dir=state_dir,
        predicates=predicates,
        contract=contract,
        repo_root=cwd,
        graph_db=graph_db,
        issue_text=task,
        requested_model=model,
        resolved_model=model_name,
    )
    delivery_path = (
        "legacy" if os.environ.get("GT_LEGACY_MODEL_VISIBLE", "").strip() == "1"
        else "compiled"
    )
    session = GTSession(
        GTSessionConfig(
            task_id=adapter.task_id,
            repo_root=cwd,
            state_dir=state_dir,
            graph_db=graph_db,
            capabilities=(
                "exact_provider_payload",
                "provider_response_ids",
                "structured_actions",
                "structured_results",
                "workspace_deltas",
                "filesystem_snapshots",
                "tool_call_deferral",
                "parsed_test_results",
            ),
            issue_text=task,
            mode=GTMode(gt_mode),
            capability_modes=dict(capability_modes or {}),
            disabled_capabilities=tuple(disabled_capabilities),
            delivery_path=delivery_path,
        ),
        engine=adapter,
    )
    if index_error is not None:
        adapter.store.append(
            "index_unavailable",
            error_type=type(index_error).__name__,
            error=str(index_error)[:300],
        )

    # Advisory evidence is persistent so it need not be repeated per turn. In
    # SHADOW mode it is computed/logged but never enters model-visible bytes.
    if delivery_path == "legacy":
        contract_text, _ = render_task_contract(contract, max_chars=2400)
        localization = adapter.task_start_localization()
        rows = "\n".join(
            line for line in localization.splitlines()
            if line and not line.startswith("[GT_EVIDENCE")
        )
        safe_contract = (
            f"[GT_TASK_CONTRACT]\n{contract_text}"
            if contract_text and "{{" not in contract_text and "{%" not in contract_text
            else ""
        )
        safe_rows = rows if rows and "{{" not in rows and "{%" not in rows else ""
        if session.model_visible:
            system_template += "\n\n" + _render_gt_advisory_system(
                safe_contract, safe_rows
            )
            adapter._contract_shipped = True
            adapter._last_delta_signature = tuple(
                sorted((key, status.value) for key, status in adapter._status.items())
            )
    agent = BoundedHistoryAgent(
        model_obj, env_obj,
        config_class=AgentConfig,
        system_template=system_template,
        instance_template=instance_template,
        step_limit=step_limit,
        wall_time_limit_seconds=wall_time_limit_seconds,
        output_path=Path(output) if output else None,
    )
    install_runtime_hooks(agent, session)
    observer = RunReceiptObserver(
        Path(state_dir) / task_id,
        requested_model=model,
        resolved_model=model_name,
    )
    # Install after GT so this neutral observer hashes the final logical
    # request produced by the complete stack. The same observer is installed
    # in GT-off above.
    observer.install(agent.model)
    return agent, adapter, session


# T1.2: typed terminal outcomes with stable, non-ambiguous exit codes. A
# harness crash must never masquerade as success (the old unconditional
# `return 0` + `| tee ... || true` erased every failure).
TERMINAL_EXIT_CODES = {
    "submitted_verified": 0,   # agent submitted AND every GT obligation has evidence
    "submitted_unverified": 0, # agent submitted but some obligations have NO evidence (UNKNOWN)
    "stuck": 0,               # completed solver outcome; workspace remains gradable
    "budget_exhausted": 0,    # completed solver outcome; workspace remains gradable
    "timeout": 3,             # provider/command timeout
    "provider_failed": 4,     # provider refused/substituted the model
    "provider_model_mismatch": 4,
    "internal_error": 5,      # any other harness fault
    "task_failed": 0,         # completed solver outcome; workspace remains gradable
    "setup_error": 6,         # agent/environment/observer construction failed
}

# Terminal classes that must NEVER be reported as a clean pass.
_NON_SUBMITTED_TERMINALS = {"stuck", "budget_exhausted", "timeout",
                            "provider_failed", "provider_model_mismatch",
                            "internal_error", "setup_error", "task_failed"}

# Exception class name -> terminal outcome (mini-swe raises these through
# handle_uncaught_exception, which also writes an exit message).
_EXCEPTION_TERMINAL = {
    "Submitted": "submitted",
    "LifecycleError": "internal_error",
    "LimitsExceeded": "budget_exhausted",
    "TimeExceeded": "budget_exhausted",
    "AgentTimeoutError": "timeout",
    "APIError": "provider_failed",
    "APIConnectionError": "provider_failed",
    "AuthenticationError": "provider_failed",
    "BadRequestError": "provider_failed",
    "ProviderModelMismatch": "provider_model_mismatch",
    "ResearchModelMismatch": "provider_model_mismatch",
    "RunnerTerminationRequested": "timeout",
}


class RunnerTerminationRequested(RuntimeError):
    """A supervisor requested shutdown while the protected finalizer is pending."""


def _install_termination_guard():
    """Turn the first SIGTERM into a catchable timeout and protect finalization.

    The workflow retains an outer hard-kill reserve.  Ignoring subsequent
    SIGTERM after the first request gives patch/receipt publication that reserve
    instead of interrupting it a second time.
    """
    previous = signal.getsignal(signal.SIGTERM)

    def request_termination(signum, _frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise RunnerTerminationRequested(f"signal {signum}")

    signal.signal(signal.SIGTERM, request_termination)

    def restore() -> None:
        signal.signal(signal.SIGTERM, previous)

    return restore


def _classify_terminal(exception: BaseException | None, result: dict) -> str:
    """Map the run's ending into one typed terminal outcome."""
    if exception is not None:
        name = type(exception).__name__
        if name in _EXCEPTION_TERMINAL:
            return _EXCEPTION_TERMINAL[name]
        # litellm connection/status errors surface with provider-ish names.
        lowered = f"{name} {str(exception)}".lower()
        if any(t in lowered for t in (
            "connection", "timeout", "api", "auth", "provider",
            "rate limit", "status",
        )):
            return "provider_failed"
        if "tool action after stuck" in lowered or "lifecycleerror" in lowered:
            return "internal_error"
        if "limitsexceeded" in lowered:
            return "budget_exhausted"
        if "submitted" in lowered:
            return "submitted"
        return "internal_error"
    exit_status = str((result or {}).get("exit_status") or "")
    if "Submitted" in exit_status or (result or {}).get("submission"):
        return "submitted"
    if "LimitsExceeded" in exit_status or "TimeExceeded" in exit_status:
        return "budget_exhausted"
    if "Lifecycle" in exit_status or "STUCK" in str((result or {}).get("content") or "").upper():
        return "stuck"
    if (result or {}).get("submission") is False:
        return "task_failed"
    return "internal_error"


def main() -> int:
    harden_process_secret_boundary()
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--state-dir", default=".gt-state")
    parser.add_argument("--output")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--step-limit", type=int, default=100)
    parser.add_argument("--timeout", type=int, default=30,
                        help="per-command execution timeout in seconds")
    parser.add_argument("--metrics", help="write per-run metrics JSON to this path")
    parser.add_argument("--product-receipt")
    parser.add_argument("--adapter-receipt")
    parser.add_argument("--patch-output")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--product-source-sha", default="")
    parser.add_argument("--time-budget-seconds", type=int, default=1)
    parser.add_argument("--manifest", help="write reproducibility manifest here")
    parser.add_argument("--gt-off", action="store_true")
    parser.add_argument(
        "--gt-mode",
        choices=("off", "shadow", "advisory", "assistive", "enforced"),
        default="advisory",
    )
    parser.add_argument(
        "--gt-disable-capability",
        action="append",
        default=[],
        metavar="NAME",
        help="disable one deterministic GT capability (repeatable)",
    )
    parser.add_argument(
        "--gt-capability-mode",
        action="append",
        default=[],
        metavar="NAME=MODE",
        help="set one capability mode: off/shadow/advisory/assistive/enforced",
    )
    args = parser.parse_args()
    patch_baseline = ""
    capability_modes: dict[str, str] = {}
    for item in args.gt_capability_mode:
        name, separator, mode = item.partition("=")
        if not separator or mode not in {"off", "shadow", "advisory", "assistive", "enforced"}:
            parser.error("--gt-capability-mode requires NAME=MODE with a valid MODE")
        capability_modes[name] = mode
    try:
        if args.patch_output:
            # Baseline capture is setup work. A missing/non-Git workspace must
            # still emit the typed setup result instead of escaping before the
            # runner's failure-conservation path is installed.
            patch_baseline = _repository_head(Path(args.cwd))
        agent, adapter, session = build_agent(
            task=args.task,
            canonical_task_id=args.task_id,
            model=args.model,
            cwd=args.cwd,
            state_dir=args.state_dir,
            output=args.output,
            temperature=args.temperature,
            gt_off=args.gt_off,
            gt_mode=args.gt_mode,
            capability_modes=capability_modes,
            disabled_capabilities=tuple(args.gt_disable_capability),
            step_limit=args.step_limit,
            timeout=args.timeout,
            wall_time_limit_seconds=args.time_budget_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - setup must leave an audit artifact
        terminal = "setup_error"
        model_name, _model_kwargs = _model_and_kwargs(args.model, args.temperature)
        report = {
            "model": args.model,
            "terminal": terminal,
            "exit_code": TERMINAL_EXIT_CODES[terminal],
            "exception": f"{type(exc).__name__}: {exc}",
        }
        request_receipt = {
            "request_count": 0,
            "events_sha256": "",
            "provider_reported_model": "",
            "model_mismatch": False,
            "valid": False,
            "issues": ["agent setup failed before provider observation"],
        }
        manifest = build_reproducibility_manifest(
            task=args.task,
            requested_model=args.model,
            resolved_model=model_name,
            provider_reported_model="",
            fallback_model="",
            temperature=args.temperature,
            cwd=args.cwd,
            step_limit=args.step_limit,
            timeout=args.timeout,
            gt_mode="off" if args.gt_off else args.gt_mode,
            event_journal={},
            request_receipt=request_receipt,
            binary_paths=[sys.executable],
        )
        task_id = resolve_run_task_identity(args.task_id, args.task)
        manifest_path = Path(args.manifest) if args.manifest else (
            Path(args.state_dir) / task_id / "reproducibility_manifest.json"
        )
        write_reproducibility_manifest(manifest_path, manifest)
        report["model_identity"] = manifest["model"]
        report["research_valid"] = False
        report["reproducibility_manifest"] = str(manifest_path)
        if args.metrics:
            Path(args.metrics).parent.mkdir(parents=True, exist_ok=True)
            Path(args.metrics).write_text(
                json.dumps(report, sort_keys=True), encoding="utf-8"
            )
        print(json.dumps(report, sort_keys=True))
        return TERMINAL_EXIT_CODES[terminal]
    report: dict = {"model": args.model, "terminal": "internal_error"}
    result: dict = {}
    exception: BaseException | None = None
    gt_state: dict | None = None
    terminal = "internal_error"
    restore_termination_handler = _install_termination_guard()
    try:
        result = agent.run(args.task)
    except Exception as exc:  # noqa: BLE001 - Submitted propagates on accept
        exception = exc
        report["exception"] = f"{type(exc).__name__}: {exc}"
    if args.patch_output:
        try:
            _write_model_patch(
                Path(args.cwd), patch_baseline, Path(args.patch_output),
                excluded_roots=(Path(args.state_dir),),
            )
        except Exception as exc:  # noqa: BLE001 - missing patch invalidates grading
            report["patch_export_error"] = f"{type(exc).__name__}: {exc}"
            if exception is None:
                exception = exc
    # Treatment identity is the requested mode, not effective engine health.
    # A kill switch may preserve native execution but cannot relabel ON as OFF.
    gt_active = not args.gt_off and args.gt_mode != "off"
    if gt_active and session is not None:
        try:
            gt_state = session.completion_state()
            report["gt"] = gt_state
        except Exception:  # noqa: BLE001 - completion state must never mask the run
            pass
    terminal = _classify_terminal(exception, result)
    # T2.2: a submission where GT has no evidence for some obligation is
    # UNVERIFIED, not VERIFIED. UNKNOWN must never silently become success.
    if terminal == "submitted" and gt_active:
        verified = bool(gt_state and gt_state.get("verified"))
        terminal = "submitted_verified" if verified else "submitted_unverified"
    report["terminal"] = terminal
    report["gt_mode"] = "off" if not gt_active else args.gt_mode
    report["exit_code"] = TERMINAL_EXIT_CODES.get(
        terminal, TERMINAL_EXIT_CODES["internal_error"]
    )
    if not gt_active:
        report["stats"] = {"n_calls": getattr(agent, "n_calls", 0),
                           "cost": getattr(agent, "cost", 0)}
    if session is not None:
        try:
            session.close(terminal)
        except Exception as exc:  # noqa: BLE001 - invalidates, never masks
            terminal = "internal_error"
            report["terminal"] = terminal
            report["exit_code"] = TERMINAL_EXIT_CODES[terminal]
            report["session_close_error"] = f"{type(exc).__name__}: {exc}"
    observer = getattr(agent.model, "_research_receipt_observer", None)
    request_receipt = observer.receipt() if observer is not None else {
        "request_count": 0,
        "events_sha256": "",
        "provider_reported_model": "",
        "model_mismatch": False,
        "valid": False,
        "issues": ["neutral provider observer unavailable"],
    }
    model_name, _model_kwargs = _model_and_kwargs(args.model, args.temperature)
    event_journal = {}
    if adapter is not None:
        from gt_engine.event_journal import verify_event_journal

        journal_anchor = adapter.store.receipt()
        journal_check = verify_event_journal(
            adapter.store.path,
            event_count=int(journal_anchor["event_count"]),
            event_head=str(journal_anchor["event_head"]),
        )
        event_journal = {
            **journal_anchor,
            "path": adapter.store.path.name,
            "valid": journal_check.valid,
            "issues": list(journal_check.issues),
        }
    binary_paths = [sys.executable]
    source_paths = [str(Path(__file__).resolve())]
    repro_source = Path(__file__).with_name("miniswe_repro.py")
    if repro_source.is_file():
        source_paths.append(str(repro_source.resolve()))
    supervisor_source = Path(__file__).with_name("miniswe_supervisor.py")
    if supervisor_source.is_file():
        source_paths.append(str(supervisor_source.resolve()))
    uv_binary = shutil.which("uv")
    if uv_binary:
        binary_paths.append(uv_binary)
    if os.environ.get("GT_INDEX_BINARY"):
        binary_paths.append(os.environ["GT_INDEX_BINARY"])
    manifest = build_reproducibility_manifest(
        task=args.task,
        requested_model=args.model,
        resolved_model=model_name,
        provider_reported_model=str(
            request_receipt.get("provider_reported_model") or ""
        ),
        fallback_model="",
        temperature=args.temperature,
        cwd=args.cwd,
        step_limit=args.step_limit,
        timeout=args.timeout,
        gt_mode="off" if not gt_active else args.gt_mode,
        event_journal=event_journal,
        request_receipt=request_receipt,
        binary_paths=binary_paths,
        source_paths=source_paths,
        engine_integrity=(gt_state or {}).get("engine_integrity"),
    )
    manifest["research_valid"] = bool(
        manifest["research_valid"]
        and TERMINAL_EXIT_CODES.get(terminal, 5) == 0
    )
    task_id = resolve_run_task_identity(args.task_id, args.task)
    manifest_path = Path(args.manifest) if args.manifest else (
        Path(args.state_dir) / task_id / "reproducibility_manifest.json"
    )
    write_reproducibility_manifest(manifest_path, manifest)
    report["model_identity"] = manifest["model"]
    report["research_valid"] = manifest["research_valid"]
    report["reproducibility_manifest"] = str(manifest_path)
    if args.metrics:
        Path(args.metrics).parent.mkdir(parents=True, exist_ok=True)
        Path(args.metrics).write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    native_exit_code = TERMINAL_EXIT_CODES.get(
        terminal, TERMINAL_EXIT_CODES["internal_error"]
    )
    if args.product_receipt or args.adapter_receipt:
        from gt_harness.runtime_receipts import (
            issue_runtime_receipt_failure,
            issue_runtime_receipts,
        )

        receipt_error: BaseException | None = None
        try:
            if not args.product_receipt or not args.adapter_receipt:
                raise ValueError(
                    "product and adapter receipt paths must be configured together"
                )
            if not args.metrics or not args.output:
                raise ValueError(
                    "metrics and trajectory paths are required for runtime receipts"
                )
            issue_runtime_receipts(
                report_path=Path(args.metrics),
                trajectory_path=Path(args.output),
                state_dir=Path(args.state_dir),
                product_receipt_path=Path(args.product_receipt),
                adapter_receipt_path=Path(args.adapter_receipt),
                task_id=args.task_id,
                product_source_sha=args.product_source_sha,
                treatment="bare" if not gt_active else "groundtruth",
                requested_model=args.model,
                scaffold_version="2.4.6",
                time_budget_seconds=args.time_budget_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - receipts cannot mask task outcome
            receipt_error = exc
            report["receipt_issuance"] = {
                "status": "ERROR",
                "code": "runtime_receipt_issuance_failed",
                "type": type(exc).__name__,
                "message": str(exc),
            }
            if args.metrics:
                Path(args.metrics).write_text(
                    json.dumps(report, sort_keys=True), encoding="utf-8"
                )
            print(
                "runtime receipt issuance failed: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        if receipt_error is not None and args.product_receipt and args.adapter_receipt:
            try:
                issue_runtime_receipt_failure(
                    report_path=Path(args.metrics),
                    trajectory_path=Path(args.output),
                    product_receipt_path=Path(args.product_receipt),
                    adapter_receipt_path=Path(args.adapter_receipt),
                    task_id=args.task_id,
                    product_source_sha=args.product_source_sha,
                    treatment="bare" if not gt_active else "groundtruth",
                    requested_model=args.model,
                    scaffold_version="2.4.6",
                    time_budget_seconds=args.time_budget_seconds,
                    terminal=terminal,
                    exit_code=native_exit_code,
                    error=receipt_error,
                )
            except Exception as fallback_exc:  # noqa: BLE001 - preserve native exit
                print(
                    "runtime ERROR receipt publication failed: "
                    f"{type(fallback_exc).__name__}: {fallback_exc}",
                    file=sys.stderr,
                )
    restore_termination_handler()
    print(json.dumps(report, sort_keys=True))
    return native_exit_code


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
