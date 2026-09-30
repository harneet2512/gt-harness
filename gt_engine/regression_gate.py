"""Regression gate: existing tests that passed at the task's start commit and fail now.

At the agent's first submit, GT runs the repository's OWN tests - tests present and
unchanged since the start commit, related to the files the agent changed - in the agent's
environment. A test that fails now is re-run on a clean copy of the start commit
(`git archive`; the agent's tree is never touched). Only tests that pass there and fail now
are regressions; they hold the submit once, by name.

Only what a developer has is used: the repository, its history and its tests. Nothing the
benchmark's verifier adds (see docs/benchmarks/benchmark_integrity.md). Every uncertain case
fails open: no runner, a collection error, a timeout, a start copy that cannot run.

Related tests, most specific first: tests GT's graph reaches from the changed code
(test-level ids); test files that reference a changed module at the start commit
(`git grep`); test files named after it. Runners: pytest (test ids), go test (per package),
vitest / jest (per file). v1 (run 36677745292) found related tests on 3 of 8 DeepSWE tasks:
JS/TS was unsupported, Go only looked for <file>_test.go, and one Python run spent 150 s on
whole test files; this version addresses all three.

Evidence (DeepSWE GT runs 1-2, error analysis only): 16% of failed runs broke existing
tests; vulture (both runs) and sqlfmt (run 2) passed every hidden requirement test and
failed only on repository tests the agent could have run.
"""
from __future__ import annotations

import json
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Callable

# execute(command) -> (output, returncode); runs a shell command in the agent's environment.
Execute = Callable[[str], "tuple[str, int]"]
# referencing(stems) -> test files at the start commit that mention any of the stems.
Referencing = Callable[[list[str]], "list[str]"]

MAX_TEST_FILES = 8
MAX_TEST_IDS = 40
RUN_TIMEOUT_SECONDS = 90
BASE_COPY = "/tmp/gt-regression-base"
TIMED_OUT = 124  # coreutils `timeout`

_PY_TEST = re.compile(r"(^|/)(test_[^/]+|[^/]+_test)\.py$")
_GO_TEST = re.compile(r"_test\.go$")
_JS_TEST = re.compile(r"\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs|mts|cts)$")
_PY_FAILED = re.compile(r"^(?:FAILED|ERROR) (\S+?::\S+?)(?: - |$)", re.M)
# A test file that no longer imports or collects: "ERROR tests/test_x.py - ImportError ..."
_PY_FILE_ERROR = re.compile(r"^ERROR (\S+?\.py)(?: - |$)", re.M)
_PY_SUMMARY = re.compile(r"=+ .*?\b(\d+ (?:passed|failed|errors?)|no tests ran)\b.*=+|^\d+ (?:passed|failed|errors?)", re.M)
_GO_FAILED = re.compile(r"^\s*--- FAIL: (\S+)", re.M)
_GO_BUILD_FAILED = re.compile(r"^FAIL\s+(\S+)\s+\[(?:build|setup) failed\]", re.M)
_GO_RAN = re.compile(r"^(?:ok|FAIL|---)\s", re.M)
_GO_TIMED_OUT = re.compile(r"panic: test timed out after")
GO_TEST_TIMEOUT = "80s"  # go's own timeout, inside the outer one: failures found so far are still printed
NOTE_CHARS = 600
_JS_FAILED_FILE = re.compile(r"^\s*(?:FAIL|❯|×)\s+(\S+\.(?:test|spec)\.\w+)", re.M)
_JS_RAN = re.compile(r"(Test Files|Tests:|Test Suites:)\s", re.M)


def is_test_file(path: str) -> bool:
    return bool(_PY_TEST.search(path) or _GO_TEST.search(path) or _JS_TEST.search(path))


@dataclass
class RegressionResult:
    regressions: list[str] = field(default_factory=list)
    failing_now: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    runners: list[str] = field(default_factory=list)
    skipped: str = ""
    seconds: float = 0.0
    note: str = ""  # tail of the test output when no verdict was reached (journaled for diagnosis)

    def message(self) -> str:
        if not self.regressions:
            return ""
        lines = "\n".join(f"  - {name}" for name in self.regressions[:12])
        return ("[GT] regression check: these existing tests passed at the task's starting commit and fail "
                f"with your changes:\n{lines}\n"
                "They describe behaviour the repository already had. Fix the code so they pass again "
                "(do not edit or delete these tests), rerun them, then submit again.")


def _q(value: str) -> str:
    return shlex.quote(value)


def _bounded(cwd: str, command: str) -> str:
    """POSIX-sh safe (the agent's executor runs /bin/sh, which has no PIPESTATUS): the
    command's own exit code is echoed as GT_RC, then the tail of its output."""
    log = "/tmp/gt-regression.log"
    return f"cd {_q(cwd)} && {{ {command} ; }} > {log} 2>&1; echo GT_RC=$?; tail -n 400 {log}"


def _stems(changed: list[str]) -> list[str]:
    out = []
    for path in changed:
        if is_test_file(path):
            continue
        stem = PurePosixPath(path).stem
        if stem in ("__init__", "index", "mod", "main", "lib", "types", "utils", "util") or len(stem) < 3:
            stem = PurePosixPath(path).parent.name or stem  # a generic file name says little: use its package
        if len(stem) >= 3 and stem not in out:
            out.append(stem)
    return out[:12]


def related_tests(changed: list[str], reachable: list[str], exists_at_start: Callable[[str], bool],
                  referencing: Referencing | None = None) -> tuple[list[str], dict[str, list[str]]]:
    """Existing, untouched test files related to the change, and for each the test ids GT's
    graph reached (empty = run the whole file / package)."""
    changed_set = set(changed)
    ids: dict[str, list[str]] = {}
    order: list[str] = []

    def add(path: str, test_id: str = "") -> None:
        path = path.lstrip("./")
        if path in changed_set or not is_test_file(path):
            return
        if path not in ids:
            if not exists_at_start(path):
                return
            ids[path] = []
            order.append(path)
        if test_id and test_id not in ids[path]:
            ids[path].append(test_id)

    for item in reachable:
        path, _, name = item.partition("::")
        add(path, name)
    if referencing is not None:
        try:
            for path in referencing(_stems(changed)):
                add(path)
        except Exception:  # noqa: BLE001 - discovery is best effort
            pass
    for src in changed:
        if is_test_file(src):
            continue
        p = PurePosixPath(src)
        stem, parent = p.stem, str(p.parent)
        if p.suffix == ".py":
            for guess in (f"tests/test_{stem}.py", f"test/test_{stem}.py", f"{parent}/tests/test_{stem}.py",
                          f"{parent}/test_{stem}.py"):
                add(guess)
        elif p.suffix == ".go":
            add(f"{parent}/{stem}_test.go")
        elif p.suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".mts"):
            for ext in (p.suffix, ".ts", ".js"):
                add(f"{parent}/{stem}.test{ext}")
                add(f"{parent}/{stem}.spec{ext}")
    files = order[:MAX_TEST_FILES]
    return files, {f: ids[f] for f in files}


def go_packages(changed: list[str], has_tests_at_start: Callable[[str], bool]) -> list[str]:
    """Go runs tests per package: every package with a changed .go file and tests at the start."""
    pkgs = []
    for path in changed:
        if path.endswith(".go"):
            pkg = str(PurePosixPath(path).parent)
            if pkg not in pkgs and has_tests_at_start(pkg):
                pkgs.append(pkg)
    return pkgs[:MAX_TEST_FILES]


def _pytest(execute: Execute, cwd: str, targets: list[str], pythonpath: str = "") -> tuple[set[str], bool, bool]:
    # PYTHONDONTWRITEBYTECODE: the agent's tree must end exactly as the gate found it (no
    # __pycache__ an agent's `git add -A` could commit); the pytest cache is off below.
    env = "PYTHONDONTWRITEBYTECODE=1 "
    env += f"PYTHONPATH={_q(pythonpath)}${{PYTHONPATH:+:$PYTHONPATH}} " if pythonpath else ""
    cmd = _bounded(cwd, f"{env}timeout {RUN_TIMEOUT_SECONDS} python -m pytest -q -rfE -p no:cacheprovider "
                        f"--no-header {' '.join(_q(t) for t in targets)}")
    output, _ = execute(cmd)
    timed_out = f"GT_RC={TIMED_OUT}" in output
    ran = bool(_PY_SUMMARY.search(output)) and "No module named pytest" not in output and not timed_out
    failed = set(_PY_FAILED.findall(output)) | {f"{f} (does not import)" for f in _PY_FILE_ERROR.findall(output)}
    return failed, ran, timed_out, output


def _gotest(execute: Execute, cwd: str, packages: list[str], run: str = "") -> tuple[set[str], bool, bool]:
    flt = f" -run {_q(run)}" if run else ""
    cmd = _bounded(cwd, f"timeout {RUN_TIMEOUT_SECONDS} go test -count=1 -short -timeout {GO_TEST_TIMEOUT}{flt} "
                        f"{' '.join(_q('./' + p) for p in packages)}")
    output, _ = execute(cmd)
    failed = set(_GO_FAILED.findall(output)) | {f"{p} (does not build)" for p in _GO_BUILD_FAILED.findall(output)}
    # A timed-out run with failures found before the timeout still has something to check.
    timed_out = (f"GT_RC={TIMED_OUT}" in output or bool(_GO_TIMED_OUT.search(output))) and not failed
    return failed, bool(_GO_RAN.search(output)) and not timed_out, timed_out, output


def js_runner(package_json: str) -> str:
    """vitest or jest from the repository's package.json (scripts or dependencies), else ''."""
    try:
        data = json.loads(package_json)
    except ValueError:
        return ""
    blob = json.dumps({k: data.get(k) for k in ("scripts", "devDependencies", "dependencies")})
    if "vitest" in blob:
        return "vitest"
    if "jest" in blob:
        return "jest"
    return ""


def _jstest(execute: Execute, cwd: str, runner: str, files: list[str]) -> tuple[set[str], bool, bool]:
    """Run `files` (paths relative to `cwd`, the package directory) with vitest or jest."""
    args = " ".join(_q(f) for f in files)
    run = (f"npx --no-install vitest run {args}" if runner == "vitest"
           else f"npx --no-install jest --ci {args}")
    cmd = _bounded(cwd, f"CI=1 timeout {RUN_TIMEOUT_SECONDS} {run}")
    output, _ = execute(cmd)
    timed_out = f"GT_RC={TIMED_OUT}" in output
    failed = {m.lstrip("./") for m in _JS_FAILED_FILE.findall(output)}
    return {f for f in files if any(f.endswith(x) or x.endswith(f) for x in failed)}, \
        bool(_JS_RAN.search(output)) and not timed_out, timed_out, output


def js_packages(js_files: list[str], package_json_at: Callable[[str], str]) -> dict[str, tuple[str, list[str]]]:
    """Group JS/TS test files by their nearest package.json (monorepos keep the runner in the
    package, not the root): {package dir: (runner, [files relative to it])}; no runner = left out."""
    groups: dict[str, tuple[str, list[str]]] = {}
    for f in js_files:
        parts = PurePosixPath(f).parent.parts
        for depth in range(len(parts), -1, -1):
            pkg = "/".join(parts[:depth])
            text = package_json_at(f"{pkg}/package.json" if pkg else "package.json")
            if not text:
                continue
            runner = js_runner(text)
            if runner:
                rel = f[len(pkg) + 1:] if pkg else f
                groups.setdefault(pkg, (runner, []))[1].append(rel)
            break  # the nearest package.json decides, runner or not
    return groups


def check(root: str, baseline: str, changed: list[str], reachable: list[str], execute: Execute,
          exists_at_start: Callable[[str], bool], referencing: Referencing | None = None,
          package_json: str | Callable[[str], str] = "",
          go_has_tests: Callable[[str], bool] | None = None) -> RegressionResult:
    started = time.perf_counter()
    result = RegressionResult()
    try:
        if not baseline:
            result.skipped = "no_start_commit"
            return result
        files, ids = related_tests(changed, reachable, exists_at_start, referencing)
        py = [f for f in files if f.endswith(".py")]
        js = [f for f in files if _JS_TEST.search(f)]
        pkgs = go_packages(changed, go_has_tests) if go_has_tests else []
        pkgs += [p for p in sorted({str(PurePosixPath(f).parent) for f in files if f.endswith(".go")}) if p not in pkgs]
        package_json_at = package_json if callable(package_json) else (
            lambda path, text=package_json: text if path == "package.json" else "")
        js_groups = js_packages(js, package_json_at) if js else {}
        result.candidates = py + js + [f"{p}/ (go)" for p in pkgs]
        if not (py or pkgs or js_groups):
            result.skipped = "no_related_existing_tests" if not files else "no_supported_runner"
            return result
        py_targets = [f"{f}::{t}" for f in py for t in ids.get(f, [])][:MAX_TEST_IDS] or py
        now: dict[str, set[str]] = {}
        ran_any = False
        last_output = ""
        def js_now():
            failed_all, ran_all, timed, outputs = set(), False, False, []
            for pkg, (runner, rel) in js_groups.items():
                failed, ran, timed_out, output = _jstest(execute, f"{root}/{pkg}" if pkg else root, runner, rel)
                failed_all |= {f"{pkg}/{x}" if pkg else x for x in failed}
                ran_all |= ran
                timed |= timed_out
                outputs.append(output)
                if timed_out:
                    break
            return failed_all, ran_all, timed, "\n".join(outputs)

        for kind, run in (("pytest", lambda: _pytest(execute, root, py_targets) if py else None),
                          ("go", lambda: _gotest(execute, root, pkgs) if pkgs else None),
                          ("js", lambda: js_now() if js_groups else None)):
            got = run()
            if got is None:
                continue
            failed, ran, timed_out, output = got
            last_output = output
            result.runners.append(kind + (":timeout" if timed_out else ""))
            if timed_out:
                result.skipped = "timeout"
                result.note = output[-NOTE_CHARS:]
                return result  # a slow suite costs the agent once, never twice
            ran_any |= ran
            if failed:
                now[kind] = failed
        result.failing_now = sorted(n for s in now.values() for n in s)
        if not result.failing_now:
            result.skipped = "all_pass" if ran_any else "tests_did_not_run"
            if not ran_any:
                result.note = last_output[-NOTE_CHARS:]
            return result
        # Same tests on a clean copy of the start commit; the agent's tree is not touched.
        link_modules = "".join(
            f" && ln -sfn {_q(root)}/{pkg + '/' if pkg else ''}node_modules {BASE_COPY}/{pkg + '/' if pkg else ''}node_modules"
            for pkg in js_groups) if "js" in now else ""
        out, _ = execute(f"rm -rf {BASE_COPY} && mkdir -p {BASE_COPY} && "
                         f"git -c safe.directory='*' -C {_q(root)} archive {_q(baseline)} | tar -x -C {BASE_COPY}"
                         f"{link_modules} && echo GT_BASE_READY")
        if "GT_BASE_READY" not in out:
            result.skipped = "start_copy_failed"
            return result
        regressions: list[str] = []
        if "pytest" in now:
            targets = sorted({n.split(" (")[0] for n in now["pytest"]})
            base_failed, base_ran, _t, _o = _pytest(execute, BASE_COPY, targets,
                                                    pythonpath=f"{BASE_COPY}:{BASE_COPY}/src")
            if base_ran:
                regressions += sorted(now["pytest"] - base_failed)
        if "go" in now:
            tests = sorted(n for n in now["go"] if not n.endswith("(does not build)"))
            run_filter = "^(" + "|".join(re.escape(n.split("/")[0]) for n in tests) + ")$" if tests else "^$"
            base_failed, base_ran, _t, _o = _gotest(execute, BASE_COPY, pkgs, run=run_filter)
            if base_ran:
                regressions += sorted(now["go"] - base_failed)
        if "js" in now:
            for pkg, (runner, _rel) in js_groups.items():
                mine = sorted(f for f in now["js"] if (f.startswith(pkg + "/") if pkg else True))
                if not mine:
                    continue
                rel = [f[len(pkg) + 1:] if pkg else f for f in mine]
                base_dir = f"{BASE_COPY}/{pkg}" if pkg else BASE_COPY
                base_failed, base_ran, _t, _o = _jstest(execute, base_dir, runner, rel)
                if base_ran:
                    regressions += sorted(f for f, r in zip(mine, rel) if r not in base_failed)
        result.regressions = regressions
        if not regressions:
            result.skipped = "failures_predate_the_change"
        return result
    except Exception as exc:  # noqa: BLE001 - the gate fails open, never costs the submit
        result.skipped = f"error:{type(exc).__name__}"
        return result
    finally:
        result.seconds = round(time.perf_counter() - started, 2)


# Kept for callers of the v1 name.
def candidate_test_files(changed: list[str], reachable: list[str], exists_at_start: Callable[[str], bool]) -> list[str]:
    return related_tests(changed, reachable, exists_at_start)[0]


__all__ = ["RegressionResult", "candidate_test_files", "check", "go_packages", "is_test_file", "js_packages",
           "js_runner", "related_tests"]
