"""ENGINE deliverability probe — does each feature's producer actually produce?

Enables the gateway feature flags, builds a synthetic ToolEvent + GatewayState
for each feature's trigger, runs gateway.produce_raw, and reports which
evidence_types come out. Engine-direct producers (syntax_result, covering_red)
are exercised directly. Features that need a real graph/episode will honestly
report 'requires-conditions' — those are verified by the round-3 smoke ladder.

Usage:
    python scripts/engine_deliverability_probe.py
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Enable the gateway producers that default OFF.
os.environ.setdefault("GT_GATEWAY", "1")
os.environ.setdefault("GT_LOC_RESLOT", "1")
os.environ.setdefault("GT_PATCH_DELTA", "1")
os.environ.setdefault("GT_CHANGE_SURFACE", "1")


def _tool_event(**kw) -> "object":
    from groundtruth.runtime.gateway import ToolEvent

    return ToolEvent(kind=kw.get("kind", "bash"), **{k: v for k, v in kw.items() if k != "kind"})


def _state(**kw) -> "object":
    from groundtruth.runtime.gateway import GatewayState

    return GatewayState(**kw)


def _probe(event, state) -> list[str]:
    from groundtruth.runtime.gateway import produce_raw

    try:
        envelopes = produce_raw(event, state)
    except Exception as exc:  # noqa: BLE001 - probe must not crash
        return [f"error:{type(exc).__name__}"]
    return sorted({str(getattr(e, "evidence_type", "")) for e in envelopes or ()})


def main() -> int:
    from groundtruth.runtime.adapters.miniswe import CoveringResult  # type: ignore

    tmp = tempfile.mkdtemp()
    results: list[tuple[str, str, list[str]]] = []

    # covering_red (test_result)
    results.append(("covering_red", "test_result fail",
                    _probe(_tool_event(kind="bash", command="pytest", output="1 failed",
                                       exit_status=1, semantic_events=("test_result",),
                                       test_outcome="fail", primary_boundary="test_result",
                                       covering=CoveringResult(
                                           target="tests/test_a.py", verdict="FAIL",
                                           body_lines=["    assert x == 1",
                                                       "E   AssertionError",
                                                       "tests/test_a.py:4 in test_a"],
                                           evidence=[("tests/test_a.py", 4)], tier="ERROR",
                                           test_files=("tests/test_a.py",))),
                           _state(repo_root=tmp))))

    # signature_delta (edit_result, python before/after on disk)
    py = Path(tmp) / "m.py"
    py.write_text("def g(x):\n    return x + 1\n", encoding="utf-8")  # AFTER content on disk
    results.append(("signature_delta", "edit_result py",
                    _probe(_tool_event(command="sed -i s/f/g/ m.py", changed_files=("m.py",),
                                       semantic_events=("edit_result",),
                                       primary_boundary="edit_result",
                                       edit_before_after={"m.py": ("def f(x):\n    return x\n",
                                                                   "def g(x):\n    return x + 1\n")}),
                           _state(repo_root=tmp, graph_db=None))))

    # localization (search_result) - likely requires a graph
    results.append(("localization", "search_result (no graph)",
                    _probe(_tool_event(command="grep foo .", output="x:1\n",
                                       exit_status=0, semantic_events=("search_result",),
                                       primary_boundary="search_result"),
                           _state(repo_root=tmp, issue_text="task asks about foo"))))

    # def_partition (search outcome AMBIGUOUS_HIT/FLOOD)
    results.append(("def_partition", "search ambiguous",
                    _probe(_tool_event(command="grep bar .", output="a.py:1\na.py:1\nb.py:1\n",
                                       exit_status=0, semantic_events=("search_result",),
                                       primary_boundary="search_result"),
                           _state(repo_root=tmp))))

    # submit_refusal (submit) - engine submit gate
    from gt_engine.engine.runner import _covering_red_artifact, _syntax_artifact

    syntax = _syntax_artifact(str(py), str(tmp))
    covering_fail = _covering_red_artifact("pytest tests", "1 failed", 1)
    covering_pass = _covering_red_artifact("pytest tests", "1 passed", 0)

    print("| feature | trigger | produced evidence_types |")
    print("|---|---|---|")
    for feature, trigger, produced in results:
        print(f"| {feature} | {trigger} | {produced} |")
    print(f"| syntax_result | changed .py (engine direct) | "
          f"{[syntax.owner] if syntax else 'NONE'} (ok={syntax.content['ok'] if syntax else '-'}) |")
    print(f"| covering_red | pytest fail (engine direct) | {[covering_fail.owner] if covering_fail else 'NONE'} |")
    print(f"| covering_red | pytest pass (value-gated) | {'DROPPED (zero-gain)' if not covering_pass else 'EMITTED'} |")

    print()
    print("Features whose producer needs real conditions (graph/episode/history) are verified")
    print("by the round-3 smoke ladder; the probe reports their abstention honestly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
