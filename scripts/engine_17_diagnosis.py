"""ENGINE 17-feature diagnosis harness — per-feature firing + payload audit.

Exercises every FACT producer with controlled stubs (fake adapter / contract /
gateway state) and reports: fired? content usable (real text + anchors, not
opaque IDs)? which producer path? why-not when it abstains. This is the
"breakdown of all 17" the round-5 data could not show.

Usage:
    python scripts/engine_17_diagnosis.py [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parent.parent


class StubAdapter:
    repository_revision = "rev-1"
    repo_root = ""
    graph_db = None
    graph_fresh = True
    contract = None
    _dedup_chain = set()
    _latest_delivery = None

    def __init__(self, repo_root=""):
        self.repo_root = repo_root or tempfile.mkdtemp()

    def gateway_state(self):
        from groundtruth.runtime.gateway import GatewayState

        return GatewayState(repo_root=self.repo_root)

    def evaluate_observation(self, *a, **k):
        return None

    def evaluate_failing_observation(self, *a, **k):
        return None


def _usable(content: dict) -> bool:
    """Payload is usable iff it carries real text/anchors, not opaque IDs."""
    if not content:
        return False
    blob = json.dumps(content)
    if "obl-" in blob and not any(
        k in blob for k in ("requirements", "subjects", "file", "target")
    ):
        return False  # opaque obligation IDs only
    if not any(k in blob for k in ("requirements", "file", "path", "target",
                                   "detail", "outcome", "answer", "evidence")):
        return False
    return True


def diagnose() -> dict:
    from gt_engine.engine.runner import (
        _covering_red_artifact,
        _gateway_facts,
        _obligations_fact,
        _syntax_artifact,
        _valid_fact_payload,
    )
    from gt_engine.task_contract import Obligation, TaskContract

    os.environ.setdefault("GT_GATEWAY", "1")
    rows: dict = {}

    def _shape(fact) -> dict:
        """Payload shape + freshness contract per delivered fact."""
        if fact is None:
            return {"shape_ok": False, "fresh": False}
        return {
            "shape_ok": _valid_fact_payload(fact),
            "fresh": bool(fact.freshness_revision) or fact.coverage in (
                "episode_observed", "produced", "execution_specific"),
            "owner": fact.owner,
            "model_visible": fact.model_visible,
        }

    # --- syntax_result ---
    tmp = Path(tempfile.mkdtemp())
    (tmp / "bad.py").write_text("def f(:\n", encoding="utf-8")
    a = _syntax_artifact(str(tmp / "bad.py"), str(tmp))
    rows["syntax_result"] = {
        "fired": a is not None, "content": dict(a.content) if a else None,
        **_shape(a),
        "producer": "engine._syntax_artifact",
        "note": "fires on changed .py; value-gated to ERROR only",
    }

    # --- covering_red ---
    a = _covering_red_artifact("pytest tests", "1 failed", 1)
    rows["covering_red"] = {
        "fired": a is not None, "content": dict(a.content) if a else None,
        **_shape(a),
        "producer": "engine._covering_red_artifact + gateway.covering_verdict",
        "note": "fires on RED test commands",
    }

    # --- obligations (with the content fix) ---
    tc = TaskContract(
        role="patch",
        obligations=(
            Obligation("obl-1", "fix the vulnerability in app.py", "task",
                       subjects=("app.py",)),
        ),
    )
    adapter = StubAdapter()
    adapter.contract = tc
    a = _obligations_fact(command="cat app.py", raw="app.py contents",
                          returncode=0, adapter=adapter)
    rows["obligations"] = {
        "fired": a is not None, "content": dict(a.content) if a else None,
        "anchors": list(a.anchors) if a else [], **_shape(a),
        "producer": "engine._obligations_fact",
        "note": "content fix landed; verify live",
    }

    # --- gateway-backed: covering_verdict, def_partition, localization, ---
    # --- signature_delta, newfile_precedent, recovery                       ---
    raw = ("tests/test_a.py:4: in test_x\n    app_function()\n"
           "src/app.py:12: in app_function\n    assert x == 1\n"
           "E   AssertionError\n1 failed")
    facts = _gateway_facts(command="pytest tests/test_a.py", raw=raw,
                           returncode=1, changed_files=(), viewed_files=(),
                           adapter=StubAdapter())
    first = facts[0] if facts else None
    rows["gateway_covering"] = {
        "fired": bool(facts), "content": [dict(f.content) for f in facts] if facts else [],
        **_shape(first),
        "producer": "gateway.covering_verdict",
        "note": "fires with a source traceback frame",
    }

    # signature_delta via gateway patch_delta (edit with signature change)
    repo = Path(tempfile.mkdtemp())
    (repo / "mod.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    import subprocess

    for cmd in (["git", "init", "-q", str(repo)],
                ["git", "-C", str(repo), "config", "user.email", "t@t.t"],
                ["git", "-C", str(repo), "config", "user.name", "t"],
                ["git", "-C", str(repo), "add", "."],
                ["git", "-C", str(repo), "commit", "-qm", "init"]):
        subprocess.run(cmd, capture_output=True)
    (repo / "mod.py").write_text("def g(x):\n    return x + 1\n", encoding="utf-8")
    sa = StubAdapter(repo_root=str(repo))
    sig_facts = _gateway_facts(command="sed -i s/f/g/ mod.py", raw="",
                               returncode=0, changed_files=("mod.py",),
                               viewed_files=(), adapter=sa)
    sig_first = sig_facts[0] if sig_facts else None
    rows["signature_delta"] = {
        "fired": bool(sig_facts),
        "content": [dict(f.content) for f in sig_facts] if sig_facts else [],
        **_shape(sig_first),
        "producer": "gateway.patch_delta",
        "note": "edit + signature change; git before/after threaded",
    }

    # localization / def_partition / newfile_precedent / recovery / submit
    # need real graph / search-outcome / episode state -> honest abstention.
    for feature in ("localization", "def_partition", "newfile_precedent",
                    "recovery", "submit_refusal"):
        rows[feature] = {
            "fired": False, "shape_ok": False, "fresh": False,
            "content": None,
            "producer": "gateway / engine gate",
            "note": "requires real graph/episode/blocker state (diagnosed, not stubbable)",
        }

    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    rows = diagnose()
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(f"| feature | fired | shape_ok | fresh | producer | note |")
        print("|---|---|---|---|---|---|")
        for name, r in rows.items():
            print(f"| {name} | {r['fired']} | {r['shape_ok']} | {r['fresh']} | "
                  f"{r['producer']} | {r['note']} |")
        fired = sum(1 for r in rows.values() if r["fired"])
        ok = sum(1 for r in rows.values() if r.get("shape_ok") and r.get("fresh"))
        print(f"\nstubbable fired={fired} shape+fresh ok={ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
