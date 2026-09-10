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

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
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

    with tempfile.TemporaryDirectory(prefix="gt-baseline-evidence-") as evidence_root:
        environment = BaselineEnvironment(cwd=repo_root, timeout=max(1, budget_seconds),
                                           evidence_root=evidence_root)
        result = environment.execute({"command": shlex.join(command), "argv": list(command)},
                                     timeout=max(1, budget_seconds))
        extra = result["extra"]
        if extra.get("timed_out"):
            raise subprocess.TimeoutExpired(list(command), budget_seconds)
        if extra.get("surviving_descendants"):
            raise RuntimeError("baseline has surviving descendants")
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
) -> BaselineResult:
    """Run the repository's suite once and record what was already green."""
    import hashlib

    from scripts.miniswe_gt_run import _is_sensitive_env_name

    child_env = {key: value for key, value in (execution_env if execution_env is not None else os.environ).items()
                 if not _is_sensitive_env_name(key)}

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
        proc = _execute_baseline(tuple(command), repo_root, float(budget_seconds), child_env)
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
        status="captured",
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
    )
    if not after.captured:
        return RegressionReport(status="unknown", detail=after.status, after=after)
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
        )
    if after.exit_code not in (None, 0) and after.failed == 0 and after.errored == 0:
        return RegressionReport(
            status="unknown", passed_delta=passed_delta, failed_delta=failed_delta,
            detail="Passing summary with nonzero exit cannot establish an intact baseline", after=after,
        )
    return RegressionReport(
        status="intact", passed_delta=passed_delta, failed_delta=failed_delta,
        after=after,
    )
