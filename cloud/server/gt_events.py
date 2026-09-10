"""GroundTruth typed actions as first-class frames on the session stream.

A GT **typed action** (``tool_name: "groundtruth"``) is not a shell command.
``gt_engine.miniswe_runtime.install_runtime_hooks`` replaces
``agent.execute_actions`` with a version that dispatches the typed branch
through ``execute_typed_action_fail_open`` and **never** touches
``env.execute`` (``miniswe_runtime.py`` lines 596-745: every typed path ends in
``continue`` before the shell). That is why ``_EmittingEnvironment`` — the seam
that produces ``tool_call``/``tool_result`` — sees nothing for a typed action,
and why the UI showed a model call with no work under it.

The seam used here is ``model.format_observation_messages(message, outputs,
template_vars)``, which GT's ``execute_actions`` calls once, at the end, with

* ``message["extra"]["actions"]`` — the *normalised* action requests (already
  through :mod:`cloud.server.typed_scopes`), and
* ``outputs`` — the result mapping of each action, positionally aligned,

and which runs **after every action of that model call has executed and before
the next model call**. It is the narrowest cloud-side point that sees both
halves, and unlike wrapping ``execute_typed_action_fail_open`` it also covers
the three synthetic short-circuits GT answers without calling the router
(``query_fanout_refused``, ``capability_disabled``,
``query_turn_budget_exceeded``).

Nothing under ``gt_engine/`` is modified: the wrappers are installed onto the
agent and model *instances* the cloud runner built.
"""
from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from types import MethodType
from typing import Any

__all__ = [
    "GT_ACTION_EVENT",
    "MAX_ARGUMENT_CHARS",
    "MAX_OMISSIONS",
    "build_gt_action_event",
    "install_gt_action_events",
    "is_typed_action",
]

#: the event type the UI renders as a GroundTruth line
GT_ACTION_EVENT = "gt_action"
#: how much of any string inside ``arguments`` survives onto the wire
MAX_ARGUMENT_CHARS = 200
#: omissions are a diagnosis, not a dump
MAX_OMISSIONS = 10
#: the tool name GT's own ``is_typed_action`` matches on
_GROUNDTRUTH_TOOL = "groundtruth"
#: marks an agent whose typed actions are already wired
_INSTALLED_FLAG = "_gt_cloud_action_events"


def is_typed_action(action: Any) -> bool:
    """Mirror of ``gt_engine.miniswe_typed_actions.is_typed_action``.

    Reimplemented rather than imported so this module stays importable in a
    checkout without ``litellm`` — the same reason ``typed_scopes`` defers its
    own GT import.
    """
    return isinstance(action, Mapping) and action.get("tool_name") == _GROUNDTRUTH_TOOL


def _truncated(value: Any) -> Any:
    """``value`` with every string bounded to :data:`MAX_ARGUMENT_CHARS`."""
    if isinstance(value, str):
        return value[:MAX_ARGUMENT_CHARS]
    if isinstance(value, Mapping):
        return {str(k): _truncated(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_truncated(item) for item in value]
    return value


def _enum_name(value: Any) -> str:
    """``"EvidenceSemantics.EXACT"`` / ``"exact"`` -> ``"exact"``; else ``""``."""
    if not isinstance(value, str) or not value:
        return ""
    return value.rsplit(".", 1)[-1].lower()


def _payload_of(output: Any) -> dict[str, Any]:
    """The ``gt.compiled_observation.v1`` mapping carried in ``output``."""
    if not isinstance(output, Mapping):
        return {}
    text = output.get("output")
    if not isinstance(text, str) or not text:
        return {}
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _strings(value: Any, limit: int | None = None) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    items = [str(item) for item in value]
    return items if limit is None else items[:limit]


def match_count(direct_answer: Any) -> int:
    """How many rows an answer carries.

    Deliberately the same rule GT uses for its own ``returned_count``
    (``gt_engine/miniswe_typed_actions.py``): a list counts its rows, a mapping
    counts the first row-list it carries, ``None`` is zero, anything else is
    one answer.
    """
    if isinstance(direct_answer, list):
        return len(direct_answer)
    if isinstance(direct_answer, Mapping):
        for key in ("matches", "results", "candidates", "items"):
            rows = direct_answer.get(key)
            if isinstance(rows, list):
                return len(rows)
        return 1
    if direct_answer is None:
        return 0
    return 1


def producer_scope(evidence: Any, direct_answer: Any) -> list[str]:
    """The scope the producer says it really searched, or ``[]``.

    ``exact_literal_search`` echoes it under ``answer["scope"]``; the
    compatibility fallback puts it under ``coverage["scope"]`` instead. When
    neither is present the producer did not say what it covered, and this
    returns nothing rather than repeating back the *requested* scope — a
    request is not evidence of coverage.
    """
    candidates: list[Any] = []
    if isinstance(evidence, Mapping):
        candidates.extend((evidence.get("answer"), evidence.get("coverage")))
    candidates.append(direct_answer)
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        scope = candidate.get("scope")
        if isinstance(scope, str) and scope:
            return [scope]
        found = _strings(scope)
        if found:
            return found
    return []


def build_gt_action_event(
    action: Mapping[str, Any],
    output: Any,
    *,
    step: int,
    duration_ms: float,
) -> dict[str, Any]:
    """The ``gt_action`` payload for one typed action and its result."""
    gt_action = action.get("gt_action")
    gt_action = gt_action if isinstance(gt_action, Mapping) else {}
    arguments = gt_action.get("arguments")
    arguments = dict(arguments) if isinstance(arguments, Mapping) else {}

    payload = _payload_of(output)
    evidence = payload.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    decision = payload.get("decision")
    decision = decision if isinstance(decision, Mapping) else {}
    direct_answer = payload.get("direct_answer")
    if direct_answer is None:
        direct_answer = evidence.get("answer")
    extra = output.get("extra") if isinstance(output, Mapping) else None
    extra = extra if isinstance(extra, Mapping) else {}

    event: dict[str, Any] = {
        "step": int(step),
        "kind": str(gt_action.get("kind") or ""),
        "arguments": _truncated(arguments),
        "scope": producer_scope(evidence, direct_answer),
        "returncode": int(output.get("returncode", -1))
        if isinstance(output, Mapping) and isinstance(output.get("returncode"), int)
        else -1,
        "semantics": _enum_name(evidence.get("semantics")),
        "coverage": _enum_name(evidence.get("coverage")),
        "match_count": match_count(direct_answer),
        "omissions": _strings(evidence.get("omissions"), MAX_OMISSIONS),
        "reason_codes": _strings(decision.get("reason_codes")),
        "duration_ms": round(float(duration_ms), 3),
    }
    artifact_id = str(
        evidence.get("action_id") or extra.get("compiled_observation_sha256") or ""
    )
    if artifact_id:
        event["evidence_artifact_id"] = artifact_id
    return event


def gt_action_events(
    message: Any, outputs: Sequence[Any], *, step: int, duration_ms: float
) -> list[dict[str, Any]]:
    """One payload per typed action in ``message``, in the order they ran.

    ``outputs`` is positionally aligned with ``message["extra"]["actions"]``:
    GT's ``execute_actions`` appends exactly one output per action, in order.
    An output that is missing (a batch cut short by ``Submitted``) is skipped
    rather than guessed at.
    """
    extra = message.get("extra") if isinstance(message, Mapping) else None
    actions = (extra or {}).get("actions") if isinstance(extra, Mapping) else None
    if not isinstance(actions, (list, tuple)):
        return []
    events: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        if index >= len(outputs) or not is_typed_action(action):
            continue
        events.append(
            build_gt_action_event(
                action, outputs[index], step=step, duration_ms=duration_ms
            )
        )
    return events


def install_gt_action_events(agent: Any) -> bool:
    """Emit a ``gt_action`` frame for every typed action ``agent`` runs.

    Call this **after** ``install_runtime_hooks``: the timer wraps whatever
    ``agent.execute_actions`` is by then (GT's replacement), and the emitter
    wraps ``agent.model.format_observation_messages``, which that replacement
    calls once the batch has run and before the next model call. Returns
    ``False`` when the model has no such method — GT then returns raw outputs
    and there is nothing to hang the frames on.

    ``duration_ms`` is the wall clock of the action batch the typed action
    belonged to. A model call almost always carries a single action, in which
    case it is that action's own time.
    """
    model = getattr(agent, "model", None)
    formatter = getattr(model, "format_observation_messages", None)
    execute_actions = getattr(agent, "execute_actions", None)
    if not callable(formatter) or not callable(execute_actions):
        return False
    if getattr(agent, _INSTALLED_FLAG, False):
        return True

    state: dict[str, float] = {"started": 0.0}

    def timed_execute_actions(_self: Any, message: dict) -> Any:
        state["started"] = time.monotonic()
        return execute_actions(message)

    def emitting_formatter(
        _self: Any, message: Any, outputs: Sequence[Any], *args: Any, **kwargs: Any
    ) -> Any:
        formatted = formatter(message, outputs, *args, **kwargs)
        started = state["started"]
        elapsed_ms = (time.monotonic() - started) * 1000.0 if started else 0.0
        try:
            events = gt_action_events(
                message,
                outputs,
                step=int(getattr(agent, "current_step", 0) or 0),
                duration_ms=elapsed_ms,
            )
            for event in events:
                agent.note_gt_action(event)
                agent.emit_event(GT_ACTION_EVENT, event)
        except Exception:  # noqa: BLE001 - a frame must never break a turn
            pass
        return formatted

    agent.execute_actions = MethodType(timed_execute_actions, agent)
    model.format_observation_messages = MethodType(emitting_formatter, model)
    setattr(agent, _INSTALLED_FLAG, True)
    return True
