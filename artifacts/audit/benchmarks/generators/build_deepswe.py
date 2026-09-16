"""Build tests/fixtures/benchmarks/deepswe_tasks.json + deepswe_test_configs.json.

The expected discovery triple is derived INDEPENDENTLY from the documented
CONFIDENCE LAW in groundtruth.runtime.verification_plan.discover_test_command's
docstring, applied to the root-only materialisation spec this script also
emits.  It never calls the production function.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verif  # noqa: E402

SRC = r"D:\tmp\claude\D--gt-harness\3c77641a-5f30-45de-8550-7ad3d0dcedca\scratchpad\bench_deepswe"
OUT = r"D:\gt-context-plan\tests\fixtures\benchmarks"

TASKS = json.load(open(os.path.join(SRC, "deepswe_tasks.json"), encoding="utf-8"))
CFG = json.load(open(os.path.join(SRC, "cfgfiles.json"), encoding="utf-8"))
PKG = json.load(open(os.path.join(SRC, "pkgjson.json"), encoding="utf-8"))
TREES = json.load(open(os.path.join(SRC, "root_trees.json"), encoding="utf-8"))

# Files the discovery probes read, by name, at the repository root.
CONTENT_FILES = ("pyproject.toml", "setup.cfg", "Makefile", "makefile", "GNUmakefile",
                 "pytest.ini", "tox.ini")
PRESENCE_FILES = ("pytest.ini", "tox.ini", "setup.py", "go.mod", "Cargo.toml",
                  "pnpm-lock.yaml", "yarn.lock", "tsconfig.json", "package.json",
                  "deno.json", "deno.jsonc", "go.work", "pnpm-workspace.yaml",
                  "package-lock.json", "Justfile", "justfile")

_JS_RUNNER_TOKEN_RE = re.compile(
    r"\b(jest|mocha|vitest|ava|tap|tape|jasmine|karma|cypress|playwright|uvu|qunit|nyc|c8|node|deno|bun)\b")
_MAKE_TEST_TARGET_RE = re.compile(r"^test[ \t]*:(?!=)")

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


def root_entries(tid):
    blobs, dirs = set(), set()
    for e in TREES.get(tid, {}).get("root", []):
        (dirs if e["t"] == "tree" else blobs).add(e["p"])
    return blobs, dirs


def materialisation(tid, row):
    blobs, dirs = root_entries(tid)
    files = {}
    for name in CONTENT_FILES:
        if name in blobs:
            text = CFG.get(tid, {}).get(name)
            if isinstance(text, str):
                files[name] = text
    present = []
    for name in PRESENCE_FILES:
        if name in blobs and name not in files:
            present.append(name)
    pkg = PKG.get(tid)
    pkg_min = None
    if isinstance(pkg, dict) and "error" not in pkg:
        scripts = pkg.get("scripts")
        pkg_min = {"name": str(pkg.get("name") or "pkg")}
        if isinstance(scripts, dict):
            pkg_min["scripts"] = {k: v for k, v in scripts.items()
                                  if isinstance(v, str) and k in ("test", "test:unit", "test:ci")}
    langs = row.get("repo_languages") or {}
    return {
        "files": files,
        "present": sorted(present),
        "package_json": pkg_min,
        "dirs": sorted(dirs),
        "has_python_sources": bool(langs.get("Python")),
    }


def derive(spec):
    """The CONFIDENCE LAW, re-implemented from its docstring, over `spec`."""
    files = spec["files"]
    present = set(spec["present"]) | set(files)
    dirs = set(spec["dirs"])
    pkg = spec["package_json"]

    # 1. pyproject [tool.pytest.ini_options]
    raw = files.get("pyproject.toml")
    if raw is not None and tomllib is not None:
        try:
            data = tomllib.loads(raw)
        except Exception:
            data = None
        if isinstance(data, dict):
            tool = data.get("tool")
            cfgp = tool.get("pytest") if isinstance(tool, dict) else None
            if isinstance(cfgp, dict) and isinstance(cfgp.get("ini_options"), dict):
                return ["pytest"], "config:pyproject.pytest", "medium", "pyproject[tool.pytest.ini_options]"
    # 2. pytest.ini
    if "pytest.ini" in present:
        return ["pytest"], "config:pytest_ini", "medium", "pytest.ini present"
    # 3. setup.cfg [tool:pytest]
    raw = files.get("setup.cfg")
    if raw is not None and "[tool:pytest]" in raw:
        return ["pytest"], "config:setup_cfg", "medium", "setup.cfg[tool:pytest]"
    # 4. package.json scripts.test
    if isinstance(pkg, dict):
        script = str((pkg.get("scripts") or {}).get("test") or "").strip()
        if script:
            low = script.lower()
            has_runner = bool(_JS_RUNNER_TOKEN_RE.search(low))
            if "no test specified" in low or (low.startswith("echo") and not has_runner):
                return None, "config:package_json_placeholder", "unknown", "package.json placeholder script"
            return ["npm", "test"], "config:package_json", ("medium" if has_runner else "low"), \
                "package.json scripts.test=%r" % script[:60]
    # 5. Makefile column-0 `test:` target
    for name in ("Makefile", "makefile", "GNUmakefile"):
        raw = files.get(name)
        if isinstance(raw, str) and any(_MAKE_TEST_TARGET_RE.match(line) for line in raw.splitlines()):
            return ["make", "test"], "config:makefile", "low", "%s column-0 test: target" % name
    # 6. go.mod
    if "go.mod" in present:
        return ["go", "test", "./..."], "config:go_mod", "low", "go.mod present"
    # 7. Cargo.toml
    if "Cargo.toml" in present:
        return ["cargo", "test"], "config:cargo", "low", "Cargo.toml present"
    # 8. extension fallback: adapters in registry order, first command wins
    py_manifests = {"pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg", "setup.py"} & present
    if py_manifests or spec["has_python_sources"]:
        if "pytest.ini" in present or "pyproject.toml" in present:
            return ["pytest"], "extension_fallback", "low", "python profile: pyproject/pytest.ini"
        if "tox.ini" in present:
            return ["tox"], "extension_fallback", "low", "python profile: tox.ini"
        if "tests" in dirs:
            return ["pytest"], "extension_fallback", "low", "python profile: tests/ directory"
    js_manifests = {"package.json", "pnpm-lock.yaml", "yarn.lock", "tsconfig.json"} & present
    if js_manifests:
        script = str(((pkg or {}).get("scripts") or {}).get("test") or "").strip()
        if script and "no test specified" not in script.lower():
            manager = ("pnpm" if "pnpm-lock.yaml" in present
                       else "yarn" if "yarn.lock" in present else "npm")
            return [manager, "test"], "extension_fallback", "low", "js profile: root scripts.test"
        return None, "UNPROVEN", "UNPROVEN", \
            "js profile falls through to sub-package manifests; sub-tree not captured"
    if "go.mod" in present:
        return ["go", "test", "./..."], "extension_fallback", "low", "go profile"
    if "Cargo.toml" in present:
        return ["cargo", "test"], "extension_fallback", "low", "rust profile"
    return None, "unknown", "unknown", "no config source and no profile command"


KEEP = ("task_id", "pinned_smoke20", "language", "github_primary_language",
        "equivalence_class", "repo", "base_commit", "test_framework", "grade_tool",
        "verifier_commands", "verifier_cwds", "f2p_total", "p2p_total",
        "repo_root_test_config", "gt_test_command_scope_parsable", "risk_flags",
        "pkg_package_manager")


def main():
    os.makedirs(OUT, exist_ok=True)
    tasks, configs = [], {}
    for row in TASKS:
        tid = row["task_id"]
        spec = materialisation(tid, row)
        cmd, basis, conf, why = derive(spec)
        slim = {k: row.get(k) for k in KEEP}
        slim["expected_discovery"] = {
            "command": cmd, "basis": basis, "confidence": conf, "rule": why,
        }
        slim["verifier_invocations"] = []
        for raw in row.get("verifier_commands") or []:
            cmd = verif.normalise(raw)
            if not cmd:
                continue
            kind, why = verif.label(cmd)
            slim["verifier_invocations"].append(
                {"command": cmd, "kind": kind, "reason": why})
        slim["analysis_discovery"] = {
            "command": row.get("gt_discover_command"),
            "basis": row.get("gt_discover_basis"),
            "confidence": row.get("gt_discover_confidence"),
        }
        tasks.append(slim)
        configs[tid] = spec
    json.dump(tasks, open(os.path.join(OUT, "deepswe_tasks.json"), "w", encoding="utf-8"),
              indent=1, sort_keys=False)
    json.dump(configs, open(os.path.join(OUT, "deepswe_test_configs.json"), "w", encoding="utf-8"),
              indent=1, sort_keys=True)
    print("tasks", len(tasks))
    for name in ("deepswe_tasks.json", "deepswe_test_configs.json"):
        print(name, os.path.getsize(os.path.join(OUT, name)))


if __name__ == "__main__":
    sys.exit(main())
