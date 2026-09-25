"""Treatment-knob consumption for the DeepSWE central benchmark scaffold.

HAR-90 C6 (option A): the ``--ak`` workflow knobs were silently dropped at
Harbor's ``BaseInstalledAgent`` boundary because only four ``CliFlag`` entries
existed. Every knob the workflow passes is now declared in
``eval/miniswe_agent.py`` and consumed here — by the supervisor (deadline,
treatment label) and by ``miniswe_gt_run`` (full resolution).

Consumption rules, per knob, after the run mode resolves:

* ``wired`` — the knob drives a real canonical mechanism.
* ``satisfiable`` — the asserted value matches canonical behavior.
* ``inert`` — the knob asserts ``false`` for a mechanism this build lacks;
  the requested state and the actual state agree.
* ``record_only`` — observational knob; recorded verbatim, declared
  unapplied.
* ``moot_gt_off`` — GT is off; feature knobs cannot apply.
* refused — the asserted value requires a mechanism canonical does not
  carry. Refusal is by name (``TreatmentFlagRefusal``), journaled through
  the normal setup-error artifact path. Never a silent drop.

A knob value at its declared argparse default counts as unset; a treatment
contract may supply it as a default below the explicit command line.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


class TreatmentFlagRefusal(ValueError):
    """A declared treatment knob could not be honored on this build."""


# Names the workflow may pass that have no GT-session effect when GT is off.
# Under mode=off they are recorded moot rather than evaluated.
_GT_FEATURE_KNOBS = (
    "enable_all_features",
    "enable_submit_readiness",
    "enable_repository_intelligence",
    "require_graph_ready",
    "enable_lint",
    "enable_feature_guidance",
    "enable_context_frontier",
    "enable_preemptive_retrieval",
    "enable_persistent_execution_state",
    "enable_context_compaction",
    "enable_completion_controller",
    "enable_progress_control",
    "enable_adaptive_validation_timeout",
    "enable_shadow_submit_gate",
    "enable_decision_sufficiency",
    "enable_task_start_advisory",
    "enable_replay_capture",
    "policy_mode",
    "preflight_mode",
    "treatment_profile",
    "gt_request_token_budget",
    "persistent_state_bootstrap_timeout_sec",
    "persistent_state_bootstrap_input_tokens",
    "persistent_state_bootstrap_output_tokens",
    "persistent_state_context_tokens",
    "repository_initial_index_timeout_sec",
    "repository_refresh_timeout_sec",
    "preemptive_retrieval_model_dir",
)

# Mechanisms this canonical branch demonstrably carries. ``true`` against an
# entry here is satisfiable; ``false`` refuses because the runtime cannot
# selectively disable a mechanism it is built from.
_SATISFIABLE_TRUE = {
    "enable_all_features": "profile-2 runtime set",
    "enable_repository_intelligence": "index/graph machinery",
    "enable_submit_readiness": "plan_submit_gate",
    "enable_decision_sufficiency": "admission sufficiency machinery",
    "enable_context_compaction": "compact_provider_view",
    "require_graph_ready": "BenchmarkGraphRequired startup gate",
}

# Observational knobs: recorded verbatim and declared unapplied rather than
# refused — the receipt carries the honest non-application.
_RECORD_ONLY = {"enable_replay_capture"}

# String-valued mode knobs with a satisfiable vocabulary.
_OFF_SATISFIABLE_MODES = {"policy_mode", "preflight_mode"}

# Knobs that may only appear at their default on this build (mechanism absent).
_ABSENT_MECHANISM_NUMERIC = {
    "gt_request_token_budget",
    "persistent_state_bootstrap_timeout_sec",
    "persistent_state_bootstrap_input_tokens",
    "persistent_state_bootstrap_output_tokens",
    "persistent_state_context_tokens",
    "repository_refresh_timeout_sec",
}

_BOOL = {"true": True, "false": False}


def _truthy(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    return _BOOL.get(str(value).strip().lower())


def _unset(value: Any) -> bool:
    return value is None or value == "" or value == 0


def _load_contract(path: str) -> dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8")
    doc = json.loads(raw)
    if not isinstance(doc, dict):
        raise TreatmentFlagRefusal("treatment_contract_shape:agent_kwargs_missing")
    digest = doc.get("contract_sha256")
    if digest:
        body = dict(doc)
        body.pop("contract_sha256", None)
        recomputed = hashlib.sha256(
            json.dumps(body, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode("utf-8", "surrogatepass")
        ).hexdigest()
        if recomputed != digest:
            raise TreatmentFlagRefusal("treatment_contract_digest_mismatch")
    kwargs = doc.get("agent_kwargs")
    if not isinstance(kwargs, dict):
        raise TreatmentFlagRefusal("treatment_contract_shape:agent_kwargs_missing")
    return kwargs


def resolve_treatment_flags(args: Any) -> dict[str, dict[str, str]]:
    """Consume every declared treatment knob; mutate ``args`` where wired.

    Returns the per-knob resolution table for the run manifest. Raises
    ``TreatmentFlagRefusal`` with a named reason when an asserted knob has no
    canonical mechanism.
    """
    get = lambda name, default="": getattr(args, name, default)

    resolution: dict[str, dict[str, str]] = {}
    contract_path = str(get("treatment_runtime_contract_path") or "").strip()
    contract_defaults: dict[str, Any] = {}
    if contract_path:
        try:
            contract_defaults = _load_contract(contract_path)
        except TreatmentFlagRefusal:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise TreatmentFlagRefusal(
                f"treatment_contract_unreadable:{type(exc).__name__}"
            ) from exc
        resolution["treatment_runtime_contract_path"] = {
            "value": contract_path,
            "disposition": "wired:contract_merged",
        }

    def effective(name: str) -> Any:
        cli = get(name)
        if not _unset(cli):
            return cli
        if name in contract_defaults:
            return contract_defaults[name]
        return cli

    # --- mode resolution -------------------------------------------------
    integration = str(effective("integration_mode") or "").strip().lower()
    gt_off = bool(get("gt_off")) or str(get("gt_mode") or "") == "off"
    if integration == "off":
        # ``advisory`` is the runner's default mode, not an explicit choice;
        # only a deliberately different --gt-mode conflicts with off.
        if not gt_off and str(get("gt_mode") or "") not in ("", "advisory", "off"):
            raise TreatmentFlagRefusal(
                "treatment_knob_conflict:integration_mode=off_vs_gt_mode"
            )
        args.gt_off = True
        args.gt_mode = "off"
        gt_off = True
        resolution["integration_mode"] = {
            "value": "off", "disposition": "wired:gt_off"}
    elif integration == "active":
        if gt_off:
            raise TreatmentFlagRefusal(
                "treatment_knob_conflict:integration_mode=active_vs_gt_off"
            )
        resolution["integration_mode"] = {
            "value": "active", "disposition": "wired:advisory"}
    elif integration == "":
        resolution["integration_mode"] = {
            "value": "default", "disposition": "unset"}
    else:
        raise TreatmentFlagRefusal(
            f"treatment_integration_mode_invalid:{integration}"
        )

    # --- budget reconciliation -------------------------------------------
    # The contract-supplied value must pass through ``effective()`` AND be
    # written back onto args: the runner consumes args.time_budget_seconds /
    # args.step_limit directly, so a contract-only knob recorded as "wired"
    # but never set would be a silent drop.
    budget = resolve_deadline_seconds(
        args, execution_budget=effective("execution_budget_sec"))
    args.time_budget_seconds = budget
    resolution["execution_budget_sec"] = {
        "value": str(effective("execution_budget_sec") or "unset"),
        "disposition": "wired:deadline" if effective("execution_budget_sec")
        else "unset",
    }
    step_limit = effective("step_limit")
    if not _unset(step_limit):
        try:
            args.step_limit = int(step_limit)
        except (TypeError, ValueError) as exc:
            raise TreatmentFlagRefusal(
                f"treatment_knob_value_invalid:step_limit={step_limit}"
            ) from exc
    resolution["step_limit"] = {
        "value": str(step_limit if not _unset(step_limit) else "unset"),
        "disposition": "wired:runner_arg" if not _unset(step_limit) else "unset",
    }
    resolution["time_budget_seconds"] = {
        "value": str(budget),
        "disposition": "wired:deadline",
    }
    resolution["task_id"] = {
        "value": str(get("task_id") or "unset"),
        "disposition": "wired:receipt_identity",
    }
    resolution["product_source_sha"] = {
        "value": str(get("product_source_sha") or "unset"),
        "disposition": "wired:receipt_identity",
    }

    # --- env-mapped knobs ---------------------------------------------------
    model_dir = effective("preemptive_retrieval_model_dir")
    if gt_off:
        resolution["preemptive_retrieval_model_dir"] = {
            "value": str(model_dir or "unset"), "disposition": "moot_gt_off"}
    elif not _unset(model_dir):
        if Path(str(model_dir)).is_dir():
            os.environ["GT_DENSE_MODEL_DIR"] = str(model_dir)
            disposition = "wired:GT_DENSE_MODEL_DIR"
        else:
            disposition = ("inert:path_not_visible_in_task_env;"
                           "staged_dense_dir_used")
        resolution["preemptive_retrieval_model_dir"] = {
            "value": str(model_dir), "disposition": disposition}
    else:
        resolution["preemptive_retrieval_model_dir"] = {
            "value": "unset", "disposition": "unset"}

    index_timeout = effective("repository_initial_index_timeout_sec")
    if gt_off:
        resolution["repository_initial_index_timeout_sec"] = {
            "value": str(index_timeout or "unset"), "disposition": "moot_gt_off"}
    elif not _unset(index_timeout):
        os.environ["GT_INDEX_TIMEOUT_SECONDS"] = str(int(index_timeout))
        resolution["repository_initial_index_timeout_sec"] = {
            "value": str(index_timeout),
            "disposition": "wired:GT_INDEX_TIMEOUT_SECONDS",
        }
    else:
        resolution["repository_initial_index_timeout_sec"] = {
            "value": "unset", "disposition": "unset"}

    # --- feature / mode knob table -----------------------------------------
    for name in _GT_FEATURE_KNOBS:
        if name in resolution:
            continue
        value = effective(name)
        if gt_off:
            if name == "require_graph_ready" and _truthy(value) is True:
                raise TreatmentFlagRefusal(
                    "treatment_knob_conflict:require_graph_ready=true_under_gt_off"
                )
            resolution[name] = {
                "value": str(value if not _unset(value) else "unset"),
                "disposition": "moot_gt_off",
            }
            continue
        if name in _RECORD_ONLY:
            resolution[name] = {
                "value": str(value if not _unset(value) else "unset"),
                "disposition": "record_only:declared_unapplied",
            }
            continue
        if name in _OFF_SATISFIABLE_MODES:
            text = str(value or "").strip().lower()
            if _unset(value) or text == "off":
                resolution[name] = {
                    "value": text or "unset",
                    "disposition": "satisfiable" if text == "off" else "unset",
                }
            else:
                raise TreatmentFlagRefusal(
                    f"treatment_knob_unsupported:{name}={text}"
                )
            continue
        if name == "treatment_profile":
            if _unset(value):
                resolution[name] = {"value": "unset", "disposition": "unset"}
            else:
                raise TreatmentFlagRefusal(
                    f"treatment_knob_unsupported:{name}={value}"
                )
            continue
        if name in _ABSENT_MECHANISM_NUMERIC:
            if _unset(value):
                resolution[name] = {"value": "unset", "disposition": "unset"}
            else:
                raise TreatmentFlagRefusal(
                    f"treatment_knob_unsupported:{name}={value}"
                )
            continue
        flag = _truthy(value)
        if flag is True:
            if name in _SATISFIABLE_TRUE:
                resolution[name] = {
                    "value": "true",
                    "disposition": f"satisfiable:{_SATISFIABLE_TRUE[name]}",
                }
            else:
                raise TreatmentFlagRefusal(
                    f"treatment_knob_unsupported:{name}=true"
                )
        elif flag is False:
            if name in _SATISFIABLE_TRUE:
                raise TreatmentFlagRefusal(
                    f"treatment_knob_unsupported:{name}=false"
                )
            resolution[name] = {
                "value": "false", "disposition": "inert:mechanism_absent"}
        elif _unset(value):
            resolution[name] = {"value": "unset", "disposition": "unset"}
        else:
            raise TreatmentFlagRefusal(
                f"treatment_knob_value_invalid:{name}={value}"
            )
    return resolution


def resolve_deadline_seconds(args: Any, *, execution_budget: Any = None) -> int:
    """Reconcile ``--time-budget-seconds`` with ``--execution-budget-seconds``.

    ``time_budget_seconds`` carries the historical sentinel default of 1;
    ``execution_budget_sec`` is the workflow's resolved task deadline. When
    both carry explicit differing values the run refuses rather than silently
    preferring one. ``execution_budget`` lets the resolver pass the
    contract-effective value: the argparse default alone would report a
    contract-supplied deadline "wired" while never applying it.
    """
    tbs = int(getattr(args, "time_budget_seconds", 0) or 0)
    raw_ebs = (
        execution_budget if execution_budget is not None
        else getattr(args, "execution_budget_sec", 0)
    )
    try:
        ebs = int(raw_ebs or 0)
    except (TypeError, ValueError) as exc:
        raise TreatmentFlagRefusal(
            f"treatment_knob_value_invalid:execution_budget_sec={raw_ebs}"
        ) from exc
    explicit_tbs = tbs != 1
    if ebs and explicit_tbs and ebs != tbs:
        raise TreatmentFlagRefusal(
            f"treatment_budget_conflict:time_budget_seconds={tbs},"
            f"execution_budget_sec={ebs}"
        )
    return ebs or tbs


__all__ = [
    "TreatmentFlagRefusal",
    "resolve_deadline_seconds",
    "resolve_treatment_flags",
]
