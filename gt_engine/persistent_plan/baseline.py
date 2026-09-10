"""Capture the repository's green test set BEFORE the first edit.

Nothing in the engine did this. Every test execution on the live path is
triggered by an edit, so the run never learned which tests were already passing
and could not tell "I broke this" from "this was broken when I arrived". Two
tasks in the measured run submitted patches that broke previously-passing tests.

This is a GT-owned subprocess in the shape of ``miniswe_covering.run_syntax_probe``:
its own explicit timeout (the agent's shell is capped at 30 s and this is not
the agent's shell), a bounded scope, and correct-or-quiet on every fault. It
runs the repository's OWN declared test command, discovered from its config. It
never reads the benchmark's tests, its fail-to-pass list, or its verifier.
"""
from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# The capture is a fraction of the run, never a phase of it. Measured runtimes
# were 16-72 minutes against an 85-minute budget, so a few minutes buys the
# regression signal the run has never had -- but a suite that wants longer than
# this is one this feature declines to wait for.
# Measured cost matters more than coverage here. The capture buys a regression
# signal, but it is spent from the benchmark's own budget, and on run
# 34312022821 seven tasks died at the deadline carrying it. Half the previous
# cap still catches a fast suite; a slow one abstains and says so, which is a
# better trade than four minutes off every task's clock.
BASELINE_FRACTION = 0.03
BASELINE_MAX_SECONDS = 120.0
BASELINE_MIN_SECONDS = 20.0
RECHECK_MAX_SECONDS = 240.0
MAX_OUTPUT_CHARS = 20_000


@dataclass(frozen=True)
class BaselineResult:
    status: str
    command: tuple[str, ...] = ()
    basis: str = ""
    confidence: str = ""
    passed: int = 0
    failed: int = 0
    errored: int = 0
    passing_names: tuple[str, ...] = ()
    failing_names: tuple[str, ...] = ()
    duration_seconds: float = 0.0
    exit_code: int | None = None
    output_sha256: str = ""
    restored_paths: tuple[str, ...] = ()
    detail: str = ""
    environment_sha256: str = ""
    source_revision: str = ""
    after_source_revision: str = ""
    # The three things "the same command still passes the same tests" depends on
    # and the agent can change. Test sources are kept per file, because the
    # question is per test: only the files the observed tests live in are
    # recorded, so the map stays a handful of rows rather than the whole tree.
    # An empty digest means the file was not present at capture time.
    test_file_digests: tuple[tuple[str, str], ...] = ()
    config_sha256: str = ""
    dependency_sha256: str = ""

    @property
    def captured(self) -> bool:
        return self.status == "captured"

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "command": list(self.command),
            "basis": self.basis,
            "confidence": self.confidence,
            "passed": self.passed,
            "failed": self.failed,
            "errored": self.errored,
            "passing_names": list(self.passing_names),
            "failing_names": list(self.failing_names),
            "duration_seconds": round(self.duration_seconds, 3),
            "exit_code": self.exit_code,
            "output_sha256": self.output_sha256,
            "restored_paths": list(self.restored_paths),
            "detail": self.detail,
            "environment_sha256": self.environment_sha256,
            "source_revision": self.source_revision,
            "after_source_revision": self.after_source_revision,
            "test_file_digests": [list(item) for item in self.test_file_digests],
            "config_sha256": self.config_sha256,
            "dependency_sha256": self.dependency_sha256,
        }

    def summary(self) -> str:
        if not self.captured:
            return f"baseline: {self.status}"
        return (
            f"baseline: {self.passed} passing, {self.failed} failing "
            f"via `{' '.join(self.command)}`"
        )


@dataclass
class RegressionReport:
    status: str = "unknown"
    newly_failing: tuple[str, ...] = ()
    passed_delta: int = 0
    failed_delta: int = 0
    detail: str = ""
    after: BaselineResult | None = field(default=None, repr=False)
    # Files a previously passing test lived in whose content moved. The name
    # survived; the test did not, so its pass is not carried.
    changed_test_files: tuple[str, ...] = ()
    # Identities that moved without invalidating the name comparison --
    # "config", "dependency". Reported rather than blocking: adding a fixture
    # or a package is ordinary work, and a report that silently did not check
    # is worse than one that says what it saw.
    changed_identities: tuple[str, ...] = ()

    @property
    def regressed(self) -> bool:
        return self.status == "regressed"

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "newly_failing": list(self.newly_failing),
            "passed_delta": self.passed_delta,
            "failed_delta": self.failed_delta,
            "detail": self.detail,
            "after_environment_sha256": self.after.environment_sha256 if self.after else "",
            "changed_test_files": list(self.changed_test_files),
            "changed_identities": list(self.changed_identities),
        }


def baseline_budget_seconds(wall_time_limit_seconds: float | None) -> float:
    """Bounded slice of the run's own budget, never of the receipt reserve."""
    if not wall_time_limit_seconds or wall_time_limit_seconds <= 0:
        return BASELINE_MIN_SECONDS
    share = float(wall_time_limit_seconds) * BASELINE_FRACTION
    return max(BASELINE_MIN_SECONDS, min(BASELINE_MAX_SECONDS, share))


def discover_command(repo_root: str) -> tuple[tuple[str, ...] | None, str, str]:
    """The repository's own whole-suite command, from its own config."""
    try:
        from groundtruth.runtime.verification_plan import discover_test_command

        command, basis, confidence = discover_test_command(repo_root)
    except Exception:  # noqa: BLE001 - discovery is correct-or-quiet
        return None, "unknown", "unknown"
    return (tuple(command) if command else None), str(basis), str(confidence)


def _parse(output: str, command: tuple[str, ...]) -> tuple[dict[str, int], list[str], list[str]]:
    try:
        from groundtruth.runtime.test_runner import (
            _parse_failing_test_names,
            _parse_passing_test_names,
            _parse_test_output,
        )

        counts = _parse_test_output(output, list(command))
        passing = _parse_passing_test_names(output)
        failing = _parse_failing_test_names(output)
        return counts, passing, failing
    except Exception:  # noqa: BLE001 - parsing is correct-or-quiet
        return {"passed": 0, "failed": 0, "errored": 0}, [], []


# What the discovered command reads to decide which tests exist and how they
# run. Matched by basename anywhere in the tree, because conftest.py is
# per-directory by design and a repository may carry several.
_CONFIG_BASENAMES = frozenset({
    "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", "conftest.py",
    ".pytest.ini", "jest.config.js", "jest.config.ts", "vitest.config.ts",
    "vitest.config.js", "karma.conf.js", "phpunit.xml", "phpunit.xml.dist",
})
# What the repository DECLARES it depends on. Installed packages live outside
# the tree and are deliberately not covered here; this is the half that is a
# fact about the repository rather than about the container.
_DEPENDENCY_BASENAMES = frozenset({
    "requirements.txt", "requirements-dev.txt", "constraints.txt", "pyproject.toml",
    "poetry.lock", "Pipfile", "Pipfile.lock", "setup.py", "package.json",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.mod", "go.sum",
    "Cargo.toml", "Cargo.lock", "Gemfile", "Gemfile.lock", "pom.xml",
    "build.gradle", "build.gradle.kts", "composer.json", "composer.lock",
})


def identity_paths_for(names: Iterable[str], known_paths: Iterable[str] = ()) -> tuple[str, ...]:
    """The repository files the observed test names live in.

    A runner that prints `tests/test_widget.py::test_a` is telling us which
    file holds that test. unittest prints a dotted identity instead
    (`tests.test_widget.TestCase.test_a`), which names a module rather than a
    path; that is resolved against ``known_paths`` by trying successively
    shorter prefixes, and a prefix that resolves to nothing is discarded rather
    than guessed at.

    A name that yields no file leaves no entry, and the absence has to stay
    visible downstream: an empty result means no identity was established, not
    that nothing changed.
    """
    known = {str(path).replace("\\", "/") for path in known_paths}
    found: set[str] = set()
    for name in names:
        candidate = str(name or "").split("::", 1)[0].replace("\\", "/").strip()
        while candidate.startswith("./"):
            candidate = candidate[2:]
        if not candidate:
            continue
        if "/" in candidate or candidate.endswith((".py", ".js", ".ts", ".go", ".rb", ".php")):
            found.add(candidate)
            continue
        parts = candidate.split(".")
        for stop in range(len(parts), 0, -1):
            module = "/".join(parts[:stop]) + ".py"
            if module in known:
                found.add(module)
                break
    return tuple(sorted(found))


def _snapshot_digests(snapshot, paths: Iterable[str]) -> tuple[tuple[str, str], ...]:
    """Digest each named path from a snapshot; absent files digest to ''."""
    known = {item.path.replace("\\", "/"): item.sha256 for item in getattr(snapshot, "files", ())}
    return tuple(sorted((path, known.get(path, "")) for path in dict.fromkeys(paths)))


def _grouped_digest(snapshot, basenames: frozenset[str]) -> str:
    """One digest over every file in the tree whose basename is of interest."""
    rows = sorted(
        (item.path.replace("\\", "/"), item.sha256)
        for item in getattr(snapshot, "files", ())
        if item.path.replace("\\", "/").rsplit("/", 1)[-1] in basenames
    )
    if not rows:
        return ""
    payload = "\n".join(f"{path}:{digest}" for path, digest in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _tracked_dirty(repo_root: str) -> tuple[str, ...]:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=repo_root, capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
    except Exception:  # noqa: BLE001
        return ()
    if proc.returncode != 0:
        return ()
    paths: list[str] = []
    for line in (proc.stdout or "").splitlines():
        path = line[3:].strip()
        if path:
            paths.append(path.split(" -> ")[-1])
    return tuple(sorted(paths))


def _restore(repo_root: str, paths: tuple[str, ...]) -> tuple[str, ...]:
    """Compatibility shim: automatic checks never undo repository mutations."""
    return ()


def _execute_baseline(command: tuple[str, ...], repo_root: str,
                      budget_seconds: float, child_env: dict[str, str]):
    """Use the same descendant containment and complete capture as task actions."""
    from scripts.miniswe_gt_run import CredentialIsolatedLocalEnvironment

    executable = command[0]
    if not shutil.which(executable, path=child_env.get("PATH")):
        candidate = Path(repo_root) / executable
        if not candidate.is_file():
            raise FileNotFoundError(executable)

    class BaselineEnvironment(CredentialIsolatedLocalEnvironment):
        def execution_env(self):
            # Do not merge a caller's isolated environment back with host secrets
            # or host configuration. Only add the external capture destination.
            return child_env | {"GT_EVIDENCE_ROOT": str(self.evidence_store.root)}

    # WHOLE SECONDS. minisweagent's environment config declares `timeout: int`,
    # so Pydantic accepts an integral float such as 120.0 by coercion and REJECTS
    # a fractional one. Subtracting the source-capture time from the allowance
    # made this value fractional for the first time, and every affected baseline
    # then failed validation before the command ran -- reported as spawn_failed
    # with detail ValidationError, which reads like a missing runner rather than a
    # rejected argument. Floor rather than round: the allowance is a ceiling the
    # baseline may not exceed, and a floor of one second keeps a tiny remainder
    # from becoming a zero-second timeout.
    allowance = max(1, int(budget_seconds))
    with tempfile.TemporaryDirectory(prefix="gt-baseline-evidence-") as evidence_root:
        environment = BaselineEnvironment(cwd=repo_root, timeout=allowance,
                                           evidence_root=evidence_root)
        result = environment.execute({"command": shlex.join(command), "argv": list(command)},
                                     timeout=allowance)
        extra = result["extra"]
        if extra.get("timed_out"):
            raise subprocess.TimeoutExpired(list(command), budget_seconds)
        if extra.get("surviving_descendants"):
            raise RuntimeError("baseline has surviving descendants")
        if extra.get("capture_complete") is not True:
            raise RuntimeError("baseline output capture is not complete")
        output = environment.evidence_store.bytes(extra["output_artifact"]["sha256"])
        return subprocess.CompletedProcess(list(command), result["returncode"],
                                           output.decode("utf-8", "replace"), "")


def run_baseline(
    repo_root: str,
    *,
    budget_seconds: float,
    command: tuple[str, ...] | None = None,
    basis: str = "",
    confidence: str = "",
    execution_env: dict[str, str] | None = None,
    identity_paths: tuple[str, ...] = (),
) -> BaselineResult:
    """Run the repository's suite once and record what was already green.

    ``identity_paths`` are files an EARLIER capture recorded and this run must
    digest whether or not its own names mention them. Without it a comparison
    could not tell a test file that vanished from one this run never observed.
    """
    import hashlib

    from gt_harness.canonical_io import canonical_json_bytes
    from scripts.miniswe_gt_run import _is_sensitive_env_name

    child_env = {key: value for key, value in (execution_env if execution_env is not None else os.environ).items()
                 if not _is_sensitive_env_name(key)}
    environment_sha256 = hashlib.sha256(canonical_json_bytes(child_env)).hexdigest()

    if not repo_root or not os.path.isdir(repo_root):
        return BaselineResult(status="no_repository")
    if command is None:
        command, basis, confidence = discover_command(repo_root)
    if not command:
        return BaselineResult(
            status="no_test_command", basis=basis or "unknown",
            confidence=confidence or "unknown",
        )

    started = time.monotonic()
    try:
        from gt_engine.runtime_observation import capture_workspace

        before = capture_workspace(repo_root)
        if not before.complete:
            raise RuntimeError("baseline source capture incomplete")
        remaining = float(budget_seconds) - (time.monotonic() - started)
        if remaining <= 0:
            raise subprocess.TimeoutExpired(list(command), budget_seconds)
        proc = _execute_baseline(tuple(command), repo_root, remaining, child_env)
        after = capture_workspace(repo_root)
        if not after.complete:
            raise RuntimeError("baseline final source capture incomplete")
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - started
        return BaselineResult(
            status="timeout", command=tuple(command), basis=basis,
            confidence=confidence, duration_seconds=elapsed,
            detail=f"suite exceeded {budget_seconds:.0f}s",
        )
    except FileNotFoundError:
        # The discovered command names a runner the container does not have.
        # Measured: a repository whose only signal was a tox.ini resolved to
        # `tox`, which was not installed, so the baseline died and every row
        # that had no covering test silently lost its check as well. A
        # low-confidence guess that cannot even spawn is worth one retry
        # through this interpreter before giving up.
        fallback = (sys.executable, "-m", "pytest")
        if tuple(command) != fallback and basis != "interpreter_fallback":
            return run_baseline(
                repo_root, budget_seconds=budget_seconds, command=fallback,
                basis="interpreter_fallback", confidence="low",
                execution_env=child_env,
            )
        return BaselineResult(
            status="spawn_failed", command=tuple(command), basis=basis,
            confidence=confidence, detail="FileNotFoundError",
            duration_seconds=time.monotonic() - started,
        )
    except Exception as exc:  # noqa: BLE001 - a probe fault is correct-or-quiet
        return BaselineResult(
            status="spawn_failed", command=tuple(command), basis=basis,
            confidence=confidence, detail=type(exc).__name__,
            duration_seconds=time.monotonic() - started,
        )

    elapsed = time.monotonic() - started
    # Preview limits belong to rendering, never canonical semantic analysis.
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    counts, passing, failing = _parse(output, tuple(command))
    total = counts["passed"] + counts["failed"] + counts["errored"]
    if total == 0:
        return BaselineResult(
            status="no_tests_observed", command=tuple(command), basis=basis,
            confidence=confidence, duration_seconds=elapsed,
            exit_code=proc.returncode,
            output_sha256=hashlib.sha256(output.encode("utf-8", "replace")).hexdigest(),
            detail="runner produced no parseable result",
        )
    return BaselineResult(
        status="captured" if before.revision == after.revision else "source_changed_during_baseline",
        source_revision=before.revision,
        after_source_revision=after.revision,
        environment_sha256=environment_sha256,
        command=tuple(command),
        basis=basis,
        confidence=confidence,
        passed=int(counts["passed"]),
        failed=int(counts["failed"]),
        errored=int(counts["errored"]),
        passing_names=tuple(passing),
        failing_names=tuple(failing),
        duration_seconds=elapsed,
        exit_code=proc.returncode,
        output_sha256=hashlib.sha256(output.encode("utf-8", "replace")).hexdigest(),
        # Digested from the snapshot taken AFTER the command, so a suite that
        # rewrites its own fixtures is recorded as it ended, not as it began.
        test_file_digests=_snapshot_digests(
            after,
            (*identity_paths, *identity_paths_for(
                (*passing, *failing),
                (item.path for item in getattr(after, "files", ())),
            )),
        ),
        config_sha256=_grouped_digest(after, _CONFIG_BASENAMES),
        dependency_sha256=_grouped_digest(after, _DEPENDENCY_BASENAMES),
    )


def compare_to_baseline(
    baseline: BaselineResult, repo_root: str, *, budget_seconds: float,
    execution_env: dict[str, str] | None = None,
) -> RegressionReport:
    """Re-run the captured command and report what stopped passing.

    Only a test that was passing before and is failing now counts as a
    regression. A suite that was already red stays the agent's context, not its
    fault, and a re-run that cannot be parsed is UNKNOWN -- never a blocker,
    because a probe fault must not refuse a submission.
    """
    if not baseline.captured:
        return RegressionReport(status="no_baseline", detail=baseline.status)
    after = run_baseline(
        repo_root,
        budget_seconds=min(float(budget_seconds), RECHECK_MAX_SECONDS),
        command=baseline.command,
        basis=baseline.basis,
        confidence=baseline.confidence,
        execution_env=execution_env,
        identity_paths=tuple(path for path, _digest in baseline.test_file_digests),
    )
    return compare_results(baseline, after)


def compare_results(baseline: BaselineResult, after: BaselineResult) -> RegressionReport:
    """Decide what the two observations do and do not establish.

    Split from the run so the decision is testable without spawning a suite,
    and so every rule below is a statement about two recorded observations
    rather than about a subprocess.
    """
    if not baseline.captured:
        return RegressionReport(status="no_baseline", detail=baseline.status)
    if not after.captured:
        return RegressionReport(status="unknown", detail=after.status, after=after)
    if not baseline.environment_sha256 or not after.environment_sha256:
        return RegressionReport(status="unknown", detail="baseline environment identity missing", after=after)
    if baseline.environment_sha256 != after.environment_sha256:
        return RegressionReport(status="unknown", detail="baseline environment identity changed", after=after)
    newly_failing = tuple(
        name for name in after.failing_names if name in baseline.passing_names
    )
    passed_delta = after.passed - baseline.passed
    failed_delta = after.failed - baseline.failed
    if newly_failing:
        return RegressionReport(
            status="regressed",
            newly_failing=newly_failing,
            passed_delta=passed_delta,
            failed_delta=failed_delta,
            detail=(
                f"{after.passed} passing now against {baseline.passed} before"
            ),
            after=after,
        )
    missing = set(baseline.passing_names) - set(after.passing_names) - set(after.failing_names)
    if missing or passed_delta < 0:
        return RegressionReport(
            status="incomplete", passed_delta=passed_delta, failed_delta=failed_delta,
            detail="Previously passing tests not observed passing: " + ", ".join(sorted(missing)),
            after=after,
        )
    # A NAME is not an identity. `test_widget.py::test_rejects_bad_input`
    # passing before and passing after is conservation only if it is the same
    # test, and the file holding it is a file the agent can edit. Rewriting an
    # assertion into `assert True` conserves the name perfectly, which is
    # exactly the substitution the bound-check path already refuses via
    # test_source_digest. The baseline has to refuse it too.
    #
    # Only the baseline's OWN files are compared, so adding new test files --
    # the work itself, on most tasks -- is never a conservation failure.
    changed_identities = tuple(
        name for name, before_value, after_value in (
            ("config", baseline.config_sha256, after.config_sha256),
            ("dependency", baseline.dependency_sha256, after.dependency_sha256),
        )
        if before_value != after_value
    )
    if not baseline.test_file_digests:
        return RegressionReport(
            status="unknown", passed_delta=passed_delta, failed_delta=failed_delta,
            detail="No per-test source identity was recorded, so test identity "
                   "conservation was never established",
            after=after, changed_identities=changed_identities,
        )
    observed = dict(after.test_file_digests)
    changed_test_files = tuple(
        path for path, digest in baseline.test_file_digests
        if observed.get(path, "") != digest
    )
    if changed_test_files:
        affected = sorted(
            name for name in baseline.passing_names
            if str(name).split("::", 1)[0].replace("\\", "/").lstrip("./") in set(changed_test_files)
        )
        return RegressionReport(
            status="incomplete", passed_delta=passed_delta, failed_delta=failed_delta,
            changed_test_files=changed_test_files,
            changed_identities=changed_identities,
            detail="Previously passing tests whose own source changed, so their "
                   "pass is not carried: " + ", ".join(affected),
            after=after,
        )
    unattributed = set(after.failing_names) - set(baseline.failing_names)
    if unattributed or failed_delta > 0 or after.errored > baseline.errored:
        return RegressionReport(
            status="new_failures_unattributed", passed_delta=passed_delta,
            failed_delta=failed_delta,
            detail="New failures were observed, but were not identified as passing in the baseline: "
                   + ", ".join(sorted(unattributed)),
            after=after,
        )
    if baseline.passed > len(set(baseline.passing_names)):
        return RegressionReport(
            status="unknown", passed_delta=passed_delta, failed_delta=failed_delta,
            detail="Aggregate counts cannot establish conservation of test identities", after=after,
            changed_identities=changed_identities,
        )
    if after.exit_code not in (None, 0) and after.failed == 0 and after.errored == 0:
        return RegressionReport(
            status="unknown", passed_delta=passed_delta, failed_delta=failed_delta,
            detail="Passing summary with nonzero exit cannot establish an intact baseline", after=after,
            changed_identities=changed_identities,
        )
    # Intact, and honest about what that did and did not cover. A changed
    # configuration or dependency set does not falsify the name-and-source
    # comparison above, but the same command may now select a different suite,
    # and a report that quietly did not look is worse than one that says so.
    detail = ""
    if changed_identities:
        labels = {"config": "test configuration", "dependency": "declared dependencies"}
        detail = ("Baseline conserved, but the following changed since capture "
                  "and were not accounted for: "
                  + ", ".join(labels[name] for name in changed_identities))
    return RegressionReport(
        status="intact", passed_delta=passed_delta, failed_delta=failed_delta,
        after=after, changed_identities=changed_identities, detail=detail,
    )
