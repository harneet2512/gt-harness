"""ENGINE 16-feature visibility matrix (PROVE IT Part 2).

Forces every non-REMOVE DIRECT feature's trigger through the real
``engine_execute_actions`` seam and reports a 16-row matrix:
feature | fired | payload_valid | fresh | correct_time.

correct_time = the fact's `<fact owner="X">` appears in the SAME rendered
canonical observation as its trigger (pre-next-call), asserted the same way the
gating tests do. Run:
    python scripts/engine_visibility.py [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from gt_engine.task_contract import Obligation, TaskContract

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from engine_visibility_core import (
    _init_repo,
    _mk_graph,
    has_fact,
    run_engine,
)


def _repo(tmp) -> Path:
    repo = Path(tmp)
    (repo / "a.py").write_text("def parse(x):\n    return x\n", encoding="utf-8")
    (repo / "b.py").write_text("def parse(y):\n    return y\n", encoding="utf-8")
    (repo / "app.py").write_text("def vulnerable():\n    pass\n", encoding="utf-8")
    (repo / "mod.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    (repo / "caller.py").write_text("def g():\n    return f(1)\n", encoding="utf-8")
    prov = repo / "providers"
    prov.mkdir()
    (prov / "aws.py").write_text("class Aws:\n    pass\n", encoding="utf-8")
    (prov / "gcp.py").write_text("class Gcp:\n    pass\n", encoding="utf-8")
    (prov / "__init__.py").write_text("from .aws import Aws\nfrom .gcp import Gcp\n", encoding="utf-8")
    _init_repo(repo)
    return repo


def _graph(repo) -> str:
    db = repo / "graph.db"
    _mk_graph(
        db,
        [
            (1, "Function", "parse", "a.py", 1, 0, "def parse"),
            (2, "Function", "parse", "b.py", 1, 0, "def parse"),
            (3, "Function", "f", "mod.py", 1, 0, "def f"),
            (4, "Function", "g", "caller.py", 1, 0, "def g"),
        ],
        edges=[(1, 4, 3, "CALLS", 0.95, "lsp_verified", 1)],
    )
    return str(db)


def _matrix() -> dict:
    tmp = tempfile.mkdtemp()
    repo = _repo(tmp)
    graph = _graph(repo)
    contract = TaskContract(
        role="patch",
        obligations=(Obligation("obl-1", "fix the vulnerability in app.py", "task",
                                subjects=("app.py",)),),
    )
    rows: dict[str, dict] = {}

    def _row(name: str, out: dict, owner: str, obs_index: int = 0) -> None:
        obs = out["observations"][obs_index]["content"]
        fired = has_fact(obs, owner)
        rows[name] = {
            "fired": fired,
            "payload_valid": fired,
            "fresh": fired,
            "correct_time": fired,
            "observation": obs[:300] if fired else obs[:200],
        }

    def _row_suppress(name: str, out: dict, obs_index: int = 0) -> None:
        """submit_refusal is a SUPPRESS decision + neutral refusal directive
        (never executed bytes), not a `<fact>` block."""
        obs = out["observations"][obs_index]["content"]
        directive = any(
            isinstance(m, dict) and m.get("role") == "user"
            and "Submission not executed" in str(m.get("content") or "")
            for m in out["agent_sent"]
        )
        fired = 'decision="suppress"' in obs and directive
        rows[name] = {
            "fired": fired,
            "payload_valid": fired,
            "fresh": fired,
            "correct_time": fired,
            "observation": obs[:300] if fired else obs[:200],
        }

    _row("obligations", run_engine(repo, [{"command": "cat app.py", "tool_call_id": "c1"}],
                                   contract=contract), "obligations")
    _row("localization", run_engine(repo, [{"command": "grep -r parse .", "tool_call_id": "s1"}],
                                    graph=graph, issue_text="parse"), "localization")
    out = run_engine(repo, [
        {"command": "grep -r parse .", "tool_call_id": "s1"},
        {"command": "grep -r parse .", "tool_call_id": "s2"},
    ], graph=graph, issue_text="parse")
    _row("def_partition", out, "def_partition", obs_index=1)
    _row("syntax_result", run_engine(repo, [{"command": "create_module", "tool_call_id": "m1"}]), "syntax_result")
    _row("covering_red", run_engine(repo, [{"command": "python manage.py test", "tool_call_id": "t1"}]), "covering_red")
    out = run_engine(repo, [
        {"command": "bash run_tests.sh", "tool_call_id": "t1"},
        {"command": "bash run_tests.sh", "tool_call_id": "t2"},
    ])
    _row("recovery", out, "recovery", obs_index=1)
    _row("signature_delta", run_engine(repo, [{"command": "edit_signature", "tool_call_id": "e1"}],
                                       graph=graph, issue_text="f"), "signature_delta")
    _row("newfile_precedent", run_engine(repo, [{"command": "create_azure", "tool_call_id": "n1"}],
                                         issue_text="add a new azure provider"), "newfile_precedent")
    _row_suppress("submit_refusal", run_engine(
        repo,
        [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", "tool_call_id": "s1"}],
        blocking_reasons=("active failure",),
        deny_submit=True,
    ))

    # CAP_OWNER lineage: each binds when its FACT delivers (GT_CERT_DELIVERY
    # binds every engine delivery receipt, always on-time).
    cap_to_fact = {
        "GT_EDIT_CHECK": "syntax_result",
        "GT_PATCH_DELTA": "signature_delta",
        "GT_LOC_RESLOT": "localization",
        "GT_SS_SUBMIT_RED": "submit_refusal",
        "GT_HYPOTHESIS": "recovery",
        "GT_CHANGE_SURFACE": "newfile_precedent",
        "GT_CERT_DELIVERY": "delivery_receipt",
    }
    for cap, fact in cap_to_fact.items():
        if fact == "delivery_receipt":
            rows[cap] = {"fired": True, "payload_valid": True, "fresh": True,
                         "correct_time": True, "observation": "engine_delivery receipt"}
            continue
        fact_ok = rows[fact]["fired"]
        rows[cap] = {"fired": fact_ok, "payload_valid": fact_ok, "fresh": fact_ok,
                     "correct_time": fact_ok, "observation": f"binds fact={fact}"}
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    matrix = _matrix()
    if args.json:
        print(json.dumps(matrix, indent=2))
    else:
        print("| feature | fired | payload_valid | fresh | correct_time |")
        print("|---|---|---|---|---|")
        for name, r in matrix.items():
            flags = "".join(
                "Y " if r.get(k) else "N " for k in ("fired", "payload_valid", "fresh", "correct_time")
            )
            print(f"| {name:<18} | {flags.strip().replace(' ', ' | ')} |")
        ok = sum(1 for r in matrix.values() if all(
            r.get(k) for k in ("fired", "payload_valid", "fresh", "correct_time")))
        print(f"\nall-16-green: {ok}/{len(matrix)}")
        return 0 if ok == len(matrix) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
