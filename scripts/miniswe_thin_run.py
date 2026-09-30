"""The baseline Mini-SWE runner with GT attached the GitNexus way.

Derived from the runner that produced the space-bunny GT-off baselines
(embed-bakeoff scripts/miniswe_gt_run.py at 8e94abf9, identical at 086fb8e5):
``--gt-off`` is that runner's GT-off path verbatim - DefaultAgent, stock
LitellmModel with ``{temperature, api_base}``, CredentialIsolatedLocalEnvironment,
mini.yaml templates, no inner clock, no supervisor. Without ``--gt-off`` the
ONLY differences are gt_engine.thin_agent.GTAttachedAgent (DefaultAgent plus an
``execute_actions`` override that appends GT blocks) and GT's prompt sections.

Scripts/miniswe_repro_baseline.py is the baseline's own receipt observer. The
baseline's run_receipt_v2/runtime_attestation writers are not in this lineage;
they wrote audit artifacts only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

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
from minisweagent.models.litellm_model import LitellmModel  # noqa: E402

try:  # standalone remote runner first; package import for local tests second
    from miniswe_repro_baseline import (  # type: ignore[import-not-found]  # noqa: E402
        RunReceiptObserver,
        build_reproducibility_manifest,
        write_reproducibility_manifest,
    )
except ModuleNotFoundError:  # pragma: no cover - branch depends on invocation path
    from scripts.miniswe_repro_baseline import (  # noqa: E402
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
        self._check_finished(output)
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
    model_kwargs: dict = {"temperature": temperature}
    base_url = os.environ.get("OPENAI_BASE_URL")
    if base_url:
        # An OpenAI-compatible gateway owns the full catalog identifier.  A
        # provider-prefixed id such as minimax/minimax-m3:free must still be
        # forced through LiteLLM's OpenAI adapter, otherwise LiteLLM selects
        # its native MiniMax adapter and ignores OPENAI_API_KEY.
        if not model.startswith("openai/"):
            model = f"openai/{model}"
        model_kwargs["api_base"] = base_url
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


def build_agent(
    *,
    task: str,
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
) -> tuple[DefaultAgent, MiniSweAdapter | None, GTSession | None]:
    system_template, instance_template = _templates()
    model_name, model_kwargs = _model_and_kwargs(model, temperature)
    global_killed = os.environ.get("GT_KILL_SWITCH", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    gt_disabled = gt_off or gt_mode == "off" or global_killed
    # Both arms: the stock Mini-SWE model, its Bash-only provider schema and
    # parser, and identical model kwargs.
    model_obj = LitellmModel(model_name=model_name, model_kwargs=model_kwargs)
    task_id = hashlib.sha256(task.encode("utf-8")).hexdigest()[:16]
    env_obj = CredentialIsolatedLocalEnvironment(
        config_class=LocalEnvironmentConfig,
        cwd=cwd,
        timeout=timeout,
    )
    if gt_disabled:
        agent = DefaultAgent(
            model_obj, env_obj,
            config_class=AgentConfig,
            system_template=system_template,
            instance_template=instance_template,
            step_limit=step_limit,
            output_path=Path(output) if output else None,
        )
        observer = RunReceiptObserver(
            Path(state_dir) / task_id,
            requested_model=model,
            resolved_model=model_name,
        )
        observer.install(agent.model)
        return agent, None, None

    from gt_engine.attached_delivery import attached_system_section, instance_template_for
    from gt_engine.thin_agent import GTAttachedAgent, build_attached_session

    adapter, session, delivery, layout = build_attached_session(
        task=task, cwd=cwd, state_dir=state_dir, task_id=task_id,
        model=model, resolved_model=model_name,
    )
    delivery.start(layout.task_root / "bin", env_obj.config.env)
    from gt_engine.workspace_gate import gate_report

    gate = gate_report(cwd)  # once: the journaled decision is the applied one
    store = getattr(getattr(session, "_engine", None), "store", None)
    if store is not None:
        try:
            store.append("gt_workspace_gate", **gate)
        except Exception as exc:  # noqa: BLE001 - journaling never costs the run
            print(f"gt_workspace_gate not journaled: {type(exc).__name__}: {exc}", file=sys.stderr)
    agent = GTAttachedAgent(
        model_obj, env_obj,
        delivery=delivery,
        adapter=adapter,
        config_class=AgentConfig,
        system_template=f"{system_template}\n\n{attached_system_section()}",
        instance_template=instance_template_for(instance_template, cwd, report=gate),
        step_limit=step_limit,
        output_path=Path(output) if output else None,
    )
    observer = RunReceiptObserver(
        Path(state_dir) / task_id,
        requested_model=model,
        resolved_model=model_name,
    )
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
    "AgentTimeoutError": "timeout",
    "APIError": "provider_failed",
    "APIConnectionError": "provider_failed",
    "AuthenticationError": "provider_failed",
    "BadRequestError": "provider_failed",
    "ProviderModelMismatch": "provider_model_mismatch",
    "ResearchModelMismatch": "provider_model_mismatch",
}


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
    if "LimitsExceeded" in exit_status:
        return "budget_exhausted"
    if "Lifecycle" in exit_status or "STUCK" in str((result or {}).get("content") or "").upper():
        return "stuck"
    if (result or {}).get("submission") is False:
        return "task_failed"
    return "internal_error"


def _infrastructure_classification(terminal: str) -> str:
    if terminal == "setup_error":
        return "SETUP_ERROR"
    if terminal in {"provider_failed", "provider_model_mismatch"}:
        return "PROVIDER_ERROR"
    if terminal == "timeout":
        return "INTERRUPTED"
    if terminal == "internal_error":
        return "HARNESS_ERROR"
    return "COMPLETED"


def _provider_usage(observer) -> dict[str, int | float]:
    usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "duration_ms": 0.0}
    if observer is None:
        return usage
    usage["calls"] = int(getattr(observer, "request_count", 0) or 0)
    path = Path(getattr(observer, "events_path", ""))
    if not path.is_file():
        return usage
    try:
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeError, json.JSONDecodeError):
        return usage
    for row in rows:
        if row.get("event") not in {"provider_response", "provider_failure"}:
            continue
        provider = row.get("usage") or {}
        usage["input_tokens"] += int(
            provider.get("prompt_tokens") or provider.get("input_tokens") or 0
        )
        usage["output_tokens"] += int(
            provider.get("completion_tokens") or provider.get("output_tokens") or 0
        )
        usage["duration_ms"] += float(row.get("latency_ms") or 0.0)
    return usage


def main() -> int:
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
    parser.add_argument("--manifest", help="write reproducibility manifest here")
    parser.add_argument("--gt-off", action="store_true")
    parser.add_argument(
        "--gt-mode",
        choices=("off", "shadow", "advisory", "assistive", "enforced", "engine"),
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
    task_id = hashlib.sha256(args.task.encode("utf-8")).hexdigest()[:16]
    capability_modes: dict[str, str] = {}
    for item in args.gt_capability_mode:
        name, separator, mode = item.partition("=")
        if not separator or mode not in {"off", "shadow", "advisory", "assistive", "enforced"}:
            parser.error("--gt-capability-mode requires NAME=MODE with a valid MODE")
        capability_modes[name] = mode
    try:
        agent, adapter, session = build_agent(
            task=args.task,
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
    terminal = "internal_error"
    try:
        result = agent.run(args.task)
    except Exception as exc:  # noqa: BLE001 - Submitted propagates on accept
        exception = exc
        report["exception"] = f"{type(exc).__name__}: {exc}"
    gt_active = (
        not args.gt_off
        and args.gt_mode != "off"
        and os.environ.get("GT_KILL_SWITCH", "").strip().lower()
        not in {"1", "true", "yes", "on"}
    )
    if gt_active and adapter is not None:
        delivery = getattr(adapter, "attached_delivery", None)
        try:
            report["gt"] = agent.serialize()["info"]["gt"]
        except Exception:  # noqa: BLE001 - GT metrics must never mask the run
            pass
        if delivery is not None:
            delivery.stop()
    terminal = _classify_terminal(exception, result)
    report["terminal"] = terminal
    report["gt_mode"] = "off" if not gt_active else "attached_thin"
    report["exit_code"] = TERMINAL_EXIT_CODES.get(
        terminal, TERMINAL_EXIT_CODES["internal_error"]
    )
    if not gt_active:
        report["stats"] = {"n_calls": getattr(agent, "n_calls", 0),
                           "cost": getattr(agent, "cost", 0)}
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
        gt_mode="off" if not gt_active else "attached_thin",
        event_journal=event_journal,
        request_receipt=request_receipt,
        binary_paths=binary_paths,
        source_paths=source_paths,
    )
    manifest["research_valid"] = bool(
        manifest["research_valid"]
        and TERMINAL_EXIT_CODES.get(terminal, 5) == 0
    )
    manifest_path = Path(args.manifest) if args.manifest else (
        Path(args.state_dir) / task_id / "reproducibility_manifest.json"
    )
    write_reproducibility_manifest(manifest_path, manifest)
    report["model_identity"] = manifest["model"]
    report["research_valid"] = manifest["research_valid"]
    report["reproducibility_manifest"] = str(manifest_path)
    report["provider_usage"] = _provider_usage(observer)
    if args.metrics:
        Path(args.metrics).parent.mkdir(parents=True, exist_ok=True)
        Path(args.metrics).write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return TERMINAL_EXIT_CODES.get(terminal, TERMINAL_EXIT_CODES["internal_error"])


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
