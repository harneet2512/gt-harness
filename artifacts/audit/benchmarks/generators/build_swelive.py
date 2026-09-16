"""Build tests/fixtures/benchmarks/swelive_tasks.json (300 rows).

Discovery expectations are derived independently from the recorded config
FACTS (which pytest config section the repo carries, which root manifests
exist), not by calling discover_test_command.  The recorded real-call result
from the per-task analysis is carried alongside as a cross-check.
"""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swescope  # noqa: E402

SRC = r"D:\tmp\claude\D--gt-harness\3c77641a-5f30-45de-8550-7ad3d0dcedca\scratchpad\bench_swelive"
OUT = r"D:\gt-context-plan\tests\fixtures\benchmarks"

TASKS = json.load(open(os.path.join(SRC, "swelive_tasks.json"), encoding="utf-8"))
CFG = json.load(open(os.path.join(SRC, "cache", "cfg_feats.json"), encoding="utf-8"))
TREE = json.load(open(os.path.join(SRC, "cache", "tree_feats.json"), encoding="utf-8"))
DISC = json.load(open(os.path.join(SRC, "cache", "discover_real.json"), encoding="utf-8"))
COLLIDE = json.load(open(os.path.join(SRC, "cache", "collide.json"), encoding="utf-8"))
SCOPE = json.load(open(os.path.join(SRC, "cache", "scope_real.json"), encoding="utf-8"))

# Root manifests whose mere PRESENCE decides a discovery step.  `Makefile` is
# deliberately absent: only its column-0 `test:` target matters and that is
# carried as its own recorded flag.
ROOT_MANIFESTS = ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini", "setup.py",
                  "package.json", "go.mod", "Cargo.toml")


# The ten commands the dynaconf agent actually ran in recorded run
# 34996816912 - the agent's shapes, not the verifier's.
RECOVERED_AGENT_COMMANDS = [
    (25, "cd /testbed && python -m pytest tests/test_base.py -q 2>&1 | tail -20"),
    (26, "cd /testbed && git stash && python -m pytest tests/test_base.py::test_get_item"
         " -q 2>&1 | tail -10; git stash pop"),
    (27, "cd /testbed && python -m pytest tests/test_utils.py tests/test_env_loader.py"
         " tests/test_yaml_loader.py tests/test_py_loader.py tests/test_hooking.py"
         " tests/test_inspect.py -q 2>&1 | tail -15"),
    (28, "cd /testbed && python -m pytest tests/test_cli.py -q 2>&1 | tail -15"),
    (36, "cd /testbed && python -m pytest tests/test_base.py tests/test_utils.py"
         " -q 2>&1 | tail -8"),
    (39, "cd /testbed && python -m pytest"
         " tests/test_base.py::test_set_explicit_merge_token -v 2>&1 | tail -8"),
    (40, "cd /testbed && python -m pytest"
         " tests/test_base.py::test_set_explicit_merge_token -vv 2>&1"
         " | sed -n '/AssertionError/,/short test summary/p'"),
    (43, "cd /testbed && python -m pytest"
         " tests/test_base.py::test_set_explicit_merge_token -v 2>&1 | tail -5"),
    (44, "cd /testbed && python -m pytest tests/test_base.py tests/test_utils.py"
         " -q 2>&1 | tail -5"),
    (45, "cd /testbed && python -m pytest tests/ -q -x --ignore=tests/test_base.py"
         " --ignore=tests/test_cli.py --ignore=tests/test_vault.py"
         " --ignore=tests/test_redis.py 2>&1 | tail -5"),
]


def key_of(row):
    return "%s@%s" % (row["repo"], row["base_commit"])


def derive(spec):
    """The CONFIDENCE LAW over the materialisation spec (see build_deepswe)."""
    present = set(spec["root_manifests"])
    if spec["pyproject_has_ini_options"]:
        return ["pytest"], "config:pyproject.pytest", "medium", "pyproject[tool.pytest.ini_options]"
    if "pytest.ini" in present:
        return ["pytest"], "config:pytest_ini", "medium", "pytest.ini present"
    if spec["setup_cfg_has_tool_pytest"]:
        return ["pytest"], "config:setup_cfg", "medium", "setup.cfg[tool:pytest]"
    if spec["makefile_test_target"]:
        return ["make", "test"], "config:makefile", "low", "Makefile column-0 test: target"
    if "go.mod" in present:
        return ["go", "test", "./..."], "config:go_mod", "low", "go.mod present"
    if "Cargo.toml" in present:
        return ["cargo", "test"], "config:cargo", "low", "Cargo.toml present"
    # extension fallback: python profile (a .py source always exists here)
    if "pytest.ini" in present or "pyproject.toml" in present:
        return ["pytest"], "extension_fallback", "low", "python profile: pyproject/pytest.ini"
    if "tox.ini" in present:
        return ["tox"], "extension_fallback", "low", "python profile: tox.ini"
    if spec["test_roots"]:
        return ["pytest"], "extension_fallback", "low", "python profile: tests/ directory"
    return None, "unknown", "unknown", "no config source and no profile command"


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for row in TASKS:
        key = key_of(row)
        cfg = CFG.get(key, {})
        tree = TREE.get(key, {})
        coll = COLLIDE.get(key, {})
        src = cfg.get("pytest_cfg_source") or ""
        present = sorted(set(tree.get("cfg_files") or []) & set(ROOT_MANIFESTS))
        rec = DISC.get(key)
        test_roots = [r for r in (tree.get("test_roots") or []) if r in ("tests", "test")]
        spec = {
            "root_manifests": present,
            "pyproject_has_ini_options": src.startswith("pyproject.toml["),
            "setup_cfg_has_tool_pytest": src.startswith("setup.cfg["),
            "makefile_test_target": bool(rec and rec.get("basis") == "config:makefile"),
            "test_roots": test_roots,
        }
        cmd, basis, conf, why = derive(spec)
        scope_rows = []
        for c in row["test_cmds"]:
            s = SCOPE.get(c) or {}
            scope_rows.append({
                "command": c,
                "scope": s.get("scope"),
                "paths": s.get("paths", []),
                "excluded": s.get("excluded", []),
                "narrowed": s.get("narrowed"),
                "protocol": s.get("protocol"),
            })
        rows.append({
            "instance_id": row["instance_id"],
            "repo": row["repo"],
            "base_commit": row["base_commit"],
            "equivalence_class": row["equivalence_class"],
            "suite_size_class": row["suite_size_class"],
            "test_cmds": row["test_cmds"],
            "log_parser": row["log_parser"],
            "f2p_count": row["f2p_count"],
            "p2p_count": row["p2p_count"],
            "f2p_files_new_in_test_patch": row["f2p_files_new_in_test_patch"],
            "f2p_truncated_ids": row["f2p_truncated_ids"],
            "p2p_truncated_ids": row["p2p_truncated_ids"],
            "p2p_non_python_items": row["p2p_non_python_items"],
            "src_layout": row["src_layout"],
            "import_basename_collisions": row["import_basename_collisions"],
            "dup_test_basenames": row["dup_test_basenames"],
            "multi_test_root": row["multi_test_root"],
            "n_top_roots_with_tests": row["n_top_roots_with_tests"],
            "test_roots": row["test_roots"],
            "risk_flags": row["risk_flags"],
            "materialisation": spec,
            "expected_discovery": {"command": cmd, "basis": basis,
                                   "confidence": conf, "rule": why},
            "recorded_discovery": (
                {"command": rec["command"], "basis": rec["basis"],
                 "confidence": rec["confidence"],
                 "name_emitting_argv": rec["name_emitting_argv"]}
                if rec else None),
            "expected_baseline_argv": row["baseline_argv"],
            "scope_rows": scope_rows,
        })
    # --- distinct verifier commands + the 10 commands recovered from the
    # recorded dynaconf run 34996816912 (canary_replay/replay_report.md) ---
    counts = collections.Counter(c for row in TASKS for c in row["test_cmds"])
    corpus = []
    for command in sorted(counts):
        want, paths, excluded, narrowed, why = swescope.extent(command)
        corpus.append({"command": command, "origin": "dataset_test_cmds",
                       "rows": counts[command], "expected_scope": want,
                       "expected_paths": paths, "expected_excluded": excluded,
                       "expected_narrowed": narrowed, "rationale": why})
    for action_id, command in RECOVERED_AGENT_COMMANDS:
        want, paths, excluded, narrowed, why = swescope.extent(command)
        corpus.append({"command": command, "origin": "run_34996816912",
                       "action_id": action_id, "rows": 1,
                       "expected_scope": want, "expected_paths": paths,
                       "expected_excluded": excluded,
                       "expected_narrowed": narrowed, "rationale": why})
    path = os.path.join(OUT, "swelive_tasks.json")
    json.dump({"tasks": rows, "command_corpus": corpus},
              open(path, "w", encoding="utf-8"), indent=1)
    print("rows", len(rows), "corpus", len(corpus), "bytes", os.path.getsize(path))
    miss = [r["instance_id"] for r in rows if r["recorded_discovery"] is None]
    print("no recorded discovery:", len(miss), miss[:10])
    bad = [(r["instance_id"], r["expected_discovery"], r["recorded_discovery"])
           for r in rows if r["recorded_discovery"] and
           [r["expected_discovery"]["command"], r["expected_discovery"]["basis"],
            r["expected_discovery"]["confidence"]] !=
           [r["recorded_discovery"]["command"], r["recorded_discovery"]["basis"],
            r["recorded_discovery"]["confidence"]]]
    print("expectation vs recorded mismatches:", len(bad))
    for b in bad[:15]:
        print("   ", b)


if __name__ == "__main__":
    main()
