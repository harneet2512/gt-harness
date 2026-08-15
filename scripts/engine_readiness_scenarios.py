"""Per-feature REAL-seam forcing scenarios (P1) for the readiness audit.

Each scenario returns (agent, adapter, graph_db, root) after building a git
workspace + optional synthetic graph + scripted trajectory that forces ONE
feature's trigger through the REAL DefaultAgent/MiniSweAdapter/install_runtime
hooks path. The audit then evaluates the observed bytes.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from engine_smoke_e2e import (  # noqa: E402
    TASK,
    SRC,
    FIXED,
    EDIT,
    build_scenario_engine,
)

SRC_MOD = "def compute(values):\n    total = sum(values)\n    return total / len(values)\n"
FIXED_MOD = "def compute(values):\n    return (total / len(values)) if values else 0.0\n"
EDIT_MOD = (
    "printf \"def compute(values):\\n    return (total / len(values)) "
    "if values else 0.0\\n\" > src/mod.py"
)
TEST_MOD = "from mod import compute\n\n\ndef test_empty():\n    assert compute([]) == 0.0\n"


def _msg(content, command, tool_id):
    return {
        "role": "assistant", "content": content,
        "extra": {"actions": [{"command": command, "tool_call_id": tool_id}],
                  "response": {"model": "m", "usage": {}}},
    }


def scenario_obligations():
    return build_scenario_engine(
        files={
            "src/mod.py": SRC_MOD,
            "tests/test_mod.py": TEST_MOD,
            "app.py": "def vulnerable():\n    pass\n",
        },
        script={
            "cat app.py": {"output": "def vulnerable():\n    pass\n", "returncode": 0},
        },
        trajectory=[
            _msg("read the vulnerable file", "cat app.py", "c1"),
            _msg("read again", "cat app.py", "c2"),
        ],
        task="fix the vulnerability in app.py",
    )


def scenario_covering_red():
    return build_scenario_engine(
        files={"src/mod.py": SRC_MOD, "tests/test_mod.py": TEST_MOD},
        script={
            "python -m pytest tests/test_mod.py -q": {
                "output": (
                    "tests/test_mod.py::test_compute FAILED - compute([]) "
                    "raised ZeroDivisionError\n1 failed\n"
                ),
                "returncode": 1,
            },
            "bash run_tests.sh": {
                "output": (
                    "Traceback (most recent call last):\n"
                    '  File "src/mod.py", line 3, in compute\n'
                    "    return total / len(values)\n"
                    "ZeroDivisionError\nFAILED\n1 failed\n"
                ),
                "returncode": 1,
            },
        },
        trajectory=[
            _msg("run tests", "python -m pytest tests/test_mod.py -q", "c3"),
            _msg("run tests again", "bash run_tests.sh", "c4"),
        ],
    )


def scenario_localization():
    return build_scenario_engine(
        files={
            "a.py": "def parse(x):\n    return x\n",
            "b.py": "def parse(y):\n    return y\n",
        },
        graph=(
            [(1, "Function", "parse", "a.py", 1, 0, "def parse"),
             (2, "Function", "parse", "b.py", 1, 0, "def parse")],
            [],
        ),
        script={"grep -r parse .": {"output": "a.py:1: def parse\nb.py:1: def parse",
                                   "returncode": 0}},
        trajectory=[
            _msg("find parse", "grep -r parse .", "s1"),
            _msg("find parse again", "grep -r parse .", "s2"),
        ],
        issue_text="fix the parse function",
    )


def scenario_def_partition():
    return build_scenario_engine(
        files={
            "a.py": "def parse(x):\n    return x\n",
            "b.py": "def parse(y):\n    return y\n",
        },
        graph=(
            [(1, "Function", "parse", "a.py", 1, 0, "def parse"),
             (2, "Function", "parse", "b.py", 1, 0, "def parse")],
            [],
        ),
        script={"grep -r parse .": {"output": "a.py:1: def parse\nb.py:1: def parse",
                                   "returncode": 0}},
        trajectory=[
            _msg("find parse", "grep -r parse .", "s1"),
            _msg("find parse again", "grep -r parse .", "s2"),
            _msg("find parse again", "grep -r parse .", "s3"),
        ],
        issue_text="fix the parse function",
    )


def scenario_syntax_result():
    return build_scenario_engine(
        files={"src/mod.py": SRC_MOD, "tests/test_mod.py": TEST_MOD},
        script={
            "printf 'def new():\n    return 1\n' > new_module.py": {
                "output": "", "returncode": 0,
            },
        },
        writes={
            "> new_module.py": ("new_module.py", "def new():\n    return 1\n"),
        },
        trajectory=[
            _msg("create module", "printf 'def new():\n    return 1\n' > new_module.py", "m1"),
        ],
    )


def scenario_recovery():
    return build_scenario_engine(
        files={"src/mod.py": SRC_MOD, "tests/test_mod.py": TEST_MOD},
        script={
            "python -m pytest tests/test_mod.py -q": {
                "output": (
                    "Traceback (most recent call last):\n"
                    '  File "src/mod.py", line 3, in compute\n'
                    "    return total / len(values)\n"
                    "ZeroDivisionError\nE   AssertionError\n1 failed\n"
                ),
                "returncode": 1,
            },
        },
        trajectory=[
            _msg("run tests", "python -m pytest tests/test_mod.py -q", "r1"),
            _msg("run same tests", "python -m pytest tests/test_mod.py -q", "r2"),
        ],
    )


def scenario_signature_delta():
    # caller graph: g() in caller.py calls f() in mod.py; the edit changes f's
    # signature so caller_break must fire (cross-language CALLS caller impact).
    return build_scenario_engine(
        files={
            "mod.py": "def f(x):\n    return x\n",
            "caller.py": "def g():\n    return f(1)\n",
        },
        graph=(
            [(1, "Function", "f", "mod.py", 1, 0, "def f"),
             (2, "Function", "g", "caller.py", 1, 0, "def g")],
            [(1, 2, 1, "CALLS", 0.95, "lsp_verified", 1)],
        ),
        script={
            "printf 'def f(x, y):\n    return x + y\n' > mod.py": {
                "output": "", "returncode": 0,
            },
        },
        writes={" > mod.py": ("mod.py", "def f(x, y):\n    return x + y\n")},
        trajectory=[
            _msg("edit signature", "printf 'def f(x, y):\n    return x + y\n' > mod.py", "e1"),
        ],
        issue_text="f has callers",
    )


def scenario_newfile_precedent():
    return build_scenario_engine(
        files={
            "providers/aws.py": "class Aws:\n    pass\n",
            "providers/gcp.py": "class Gcp:\n    pass\n",
            "providers/__init__.py": "from .aws import Aws\nfrom .gcp import Gcp\n",
        },
        script={
            "printf 'class Azure:\n    pass\n' > providers/azure.py": {
                "output": "", "returncode": 0,
            },
        },
        writes={
            "> providers/azure.py": ("providers/azure.py", "class Azure:\n    pass\n"),
        },
        trajectory=[
            _msg("create azure provider",
                 "printf 'class Azure:\n    pass\n' > providers/azure.py", "n1"),
        ],
        issue_text="add a new azure provider",
    )


def scenario_submit_refusal():
    return build_scenario_engine(
        files={"src/mod.py": SRC_MOD, "tests/test_mod.py": TEST_MOD},
        script={
            "python -m pytest tests/test_mod.py -q": {
                "output": (
                    "tests/test_mod.py::test_compute FAILED - compute([]) "
                    "raised ZeroDivisionError\n1 failed\n"
                ),
                "returncode": 1,
            },
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT": {
                "output": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nfixed\n", "returncode": 0,
            },
        },
        trajectory=[
            _msg("run tests", "python -m pytest tests/test_mod.py -q", "r1"),
            _msg("submit while red", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", "s1"),
            _msg("after refusal", "cat src/mod.py", "c8"),
        ],
    )


def scenario_typed_search():
    """Typed exact_literal_search (model-selected tool) must deliver an answer
    through the real seam when a graph is present (production)."""
    return build_scenario_engine(
        files={"app.py": "def bottle():\n    return 1\n"},
        graph=([(1, "Function", "bottle", "app.py", 1, 0, "def bottle")], []),
        script={
            "grep -R -F -- bottle .": {
                "output": "app.py:1: def bottle():\n", "returncode": 0,
            },
        },
        trajectory=[
            {"role": "assistant", "content": "search bottle",
             "extra": {"actions": [
                 {"gt_action": {"kind": "exact_literal_search",
                                "arguments": {"literal": "bottle", "paths": ["."]}},
                  "tool_call_id": "g1"}],
                 "response": {"model": "m", "usage": {}}}},
        ],
        issue_text="find bottle",
    )


SCENARIOS = {
    "obligations": (scenario_obligations, ("obligations",)),
    "covering_red": (scenario_covering_red, ("covering_red",)),
    "localization": (scenario_localization, ("localization",)),
    "def_partition": (scenario_def_partition, ("def_partition",)),
    "syntax_result": (scenario_syntax_result, ("syntax_result",)),
    "recovery": (scenario_recovery, ("recovery",)),
    "signature_delta": (scenario_signature_delta, ("signature_delta",)),
    "newfile_precedent": (scenario_newfile_precedent, ("newfile_precedent",)),
    "submit_refusal": (scenario_submit_refusal, ("submit_refusal",)),
    "typed_search": (scenario_typed_search, ("localization",)),
}
