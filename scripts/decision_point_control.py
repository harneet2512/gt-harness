"""Run bounded control responses for exact GroundTruth decision-point captures.

The treatment request is captured before the first visible GT payload.  This
script sends the paired control messages (same model/tool schema/sampling) and
compares the first proposed Bash action mechanically.  It does not inspect or
claim hidden reasoning, and an action-level anchor match is reported only as a
behavioral proxy.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import litellm

from gt_engine.decision_point_eval import validate_decision_point_row
from gt_engine.replay_bundle import load_replay_bundle

_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])(?:/app/|app/)?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+")
_SYMBOL_RE = re.compile(
    r"(?:symbol|definition|caller|callee)\s*[=:]\s*([A-Za-z_][A-Za-z0-9_]*)",
    re.I,
)


def _bundle_paths(root: Path) -> list[Path]:
    if root.name == "gt_replay" and (root / "manifest.json").is_file():
        return [root]
    return sorted(
        path.parent.resolve()
        for path in root.rglob("manifest.json")
        if path.parent.name == "gt_replay"
    )


def _model_name(model: str, base_url: str | None) -> tuple[str, dict[str, Any]]:
    name = model
    kwargs: dict[str, Any] = {"temperature": 1.0}
    if base_url:
        if "/" not in name:
            name = f"openai/{name}"
        kwargs["api_base"] = base_url
    return name, kwargs


def _response_dict(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    for method in ("model_dump", "dict"):
        fn = getattr(response, method, None)
        if callable(fn):
            value = fn()
            if isinstance(value, dict):
                return value
    return {"repr": repr(response)}


def _commands(response: dict[str, Any]) -> list[str]:
    """Extract literal Bash commands from Mini-SWE normalized or provider output."""
    extra = response.get("extra")
    if isinstance(extra, dict):
        actions = extra.get("actions")
        if isinstance(actions, list):
            return [
                str(item.get("command"))
                for item in actions
                if isinstance(item, dict) and isinstance(item.get("command"), str)
            ]
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return []
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    if not isinstance(calls, list):
        return []
    commands: list[str] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        if function.get("name") != "bash":
            continue
        try:
            args = json.loads(function.get("arguments") or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(args, dict) and isinstance(args.get("command"), str):
            commands.append(args["command"])
    return commands


def _anchors(payload: str) -> tuple[str, ...]:
    paths = {match.group(0).lstrip("/") for match in _PATH_RE.finditer(payload)}
    symbols = set(_SYMBOL_RE.findall(payload))
    return tuple(sorted(paths | symbols))


def _anchor_in_commands(anchors: tuple[str, ...], commands: list[str]) -> bool:
    haystack = "\n".join(commands)
    return bool(anchors) and any(anchor in haystack for anchor in anchors)


def _valid_cases(root: Path) -> list[tuple[Path, Any]]:
    cases: list[tuple[Path, Any]] = []
    for bundle in _bundle_paths(root):
        loaded = load_replay_bundle(bundle)
        task_id = bundle.parent.parent.name.split("__", 1)[0]
        for row in loaded["calls"]:
            validation = validate_decision_point_row(row, task_id=task_id)
            if validation.case is not None:
                cases.append((bundle, validation.case))
    return cases


def run_controls(root: Path, *, model: str, base_url: str | None, limit: int) -> dict[str, Any]:
    model_name, model_kwargs = _model_name(model, base_url)
    cases = _valid_cases(root)[: max(0, limit)]
    rows: list[dict[str, Any]] = []
    for bundle, case in cases:
        control_response: dict[str, Any]
        error: str | None = None
        try:
            response = litellm.completion(
                model=model_name,
                messages=list(case.control_provider_messages),
                tools=list(case.provider_tools),
                **model_kwargs,
            )
            control_response = _response_dict(response)
        except Exception as exc:  # provider errors are recorded per case
            control_response = {}
            error = f"{type(exc).__name__}: {exc}"
        treatment_commands = _commands(case.treatment_response)
        control_commands = _commands(control_response)
        anchors = _anchors(case.payload)
        treatment_anchor = _anchor_in_commands(anchors, treatment_commands)
        control_anchor = _anchor_in_commands(anchors, control_commands)
        if error:
            comparison = "control_error"
        elif treatment_commands == control_commands:
            comparison = "equivalent_action"
        elif treatment_anchor and not control_anchor:
            comparison = "treatment_anchor_proxy"
        elif control_anchor and not treatment_anchor:
            comparison = "control_anchor_proxy"
        else:
            comparison = "different_action_indeterminate"
        rows.append(
            {
                "task_id": case.task_id,
                "call": case.call,
                "bundle": str(bundle),
                "model": model_name,
                "temperature": case.temperature,
                "payload": case.payload,
                "anchors": list(anchors),
                "treatment_commands": treatment_commands,
                "control_commands": control_commands,
                "treatment_anchor_proxy": treatment_anchor,
                "control_anchor_proxy": control_anchor,
                "comparison": comparison,
                "error": error,
                "control_response": control_response,
            }
        )
    counts: dict[str, int] = {}
    for row in rows:
        key = row["comparison"]
        counts[key] = counts.get(key, 0) + 1
    return {
        "schema": "gt.decision_point_control.v1",
        "model": model_name,
        "temperature": 1.0,
        "case_limit": limit,
        "case_count": len(rows),
        "comparison_counts": dict(sorted(counts.items())),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_controls(
        args.root, model=args.model, base_url=args.base_url or None, limit=args.limit
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("case_count", "comparison_counts")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
