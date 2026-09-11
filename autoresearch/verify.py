"""Autoresearch provider-free verify — emits one scalar: provider_free_score.

Offline only: no provider calls, no network, no benchmark dispatch.

Score = replay/utilization improvable metrics when every guard passes,
0.0 when any guard fails (loop auto-reverts).

Guards (binary):
  - central_readiness_audit: READY
  - central_integrity_audit: exit 0
  - central_feature_census: all verdicts green
  - pytest provider-free slice: 0 failures

Improvable axis (0..100):
  - efficiency replay on pinned TB2 v32 run: projected provider-view
    reduction ratio * 100 (outcome-preserving replay => free wins only)

Why sys.modules seeding: this dev box has foreign editable installs
(D:\\gt-cloud) whose regular `scripts` package shadows this repo's
namespace `scripts` dir. Pre-seeding sys.modules makes `scripts.*`
resolve to THIS tree for every child process without touching the tree.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "autoresearch"
REPLAY_ROOT = ROOT / "artifacts" / "verification" / "tb2-v32-run-33215218391"
GT_INDEX = ROOT / "vendor" / "gt-index-src" / "gt-index-current.exe"

# Provider-free slice: the architecture-sensitive subset of the CI gate.
PYTEST_FILES = [
    "tests/test_gt_central_runtime.py",
    "tests/test_gt_central_agent.py",
    "tests/test_gt_intelligence_layer.py",
    "tests/test_gt_repository_intelligence.py",
    "tests/test_gt_repository_mirror.py",
    "tests/test_gt_task_contract.py",
    "tests/test_central_readiness.py",
    "tests/test_central_replay.py",
    "tests/test_central_integrity_audit.py",
    "tests/test_gt_delivery_audit.py",
    "tests/test_gt_runtime_gate.py",
    "tests/test_repository_context.py",
    "tests/test_treatment_adapter.py",
    "tests/test_provider_view.py",
    "tests/test_gt_host_execution.py",
    "tests/test_central_efficiency_replay.py",
    "tests/test_gt_deep_metrics.py",
    "tests/test_gt_completion.py",
    "tests/test_convergence_controller.py",
    "tests/test_provider_evidence.py",
    "tests/test_hybrid_retrieval.py",
    "tests/test_diagnostics.py",
    "tests/test_decision_sufficiency.py",
    "tests/test_persistent_execution_state.py",
    "tests/test_hybrid_repository.py",
    "tests/test_miniswe_integration.py",
    "tests/test_miniswe_runtime.py",
]

def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["GT_INDEX_BINARY"] = str(GT_INDEX)
    # sitecustomize on PYTHONPATH: every descendant gets the scripts seed.
    pfenv = str(OUT_DIR / "pfenv")
    env["PYTHONPATH"] = pfenv + os.pathsep + env.get("PYTHONPATH", "")
    # Tests shell out to `git commit` in tmp repos; CI sets these, we must too.
    env.setdefault("GIT_AUTHOR_NAME", "GT Provider-Free Tests")
    env.setdefault("GIT_AUTHOR_EMAIL", "gt-tests@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "GT Provider-Free Tests")
    env.setdefault("GIT_COMMITTER_EMAIL", "gt-tests@example.invalid")
    return env


def _run_script(script: str, *args: str, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / script), *args],
        cwd=ROOT, env=_env(), capture_output=True, text=True, timeout=timeout,
    )


def _run_pytest(timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *PYTEST_FILES, "-q", "--no-header",
         "--color=no", "-p", "no:cacheprovider"],
        cwd=ROOT, env=_env(), capture_output=True, text=True, timeout=timeout,
    )


def _last_int(text: str, pattern: str) -> int:
    m = re.findall(pattern, text)
    return int(m[-1]) if m else -1


def main() -> int:
    t0 = time.time()
    breakdown: dict[str, object] = {}

    guards_ok = True

    # --- Guard 1: provider-free pytest slice -> 0 failures (runs first: the
    # most likely failure; lets failing iterations skip the slow replay) ---
    r = _run_pytest()
    (OUT_DIR / "pytest-latest.log").write_text(
        r.stdout + "\n=== STDERR ===\n" + r.stderr, encoding="utf-8")
    tail = (r.stdout + "\n" + r.stderr).strip().splitlines()[-8:]
    failed = _last_int(r.stdout + r.stderr, r"(\d+) failed")
    passed = _last_int(r.stdout + r.stderr, r"(\d+) passed")
    pytest_ok = r.returncode == 0 and failed == 0 and passed > 0
    breakdown["pytest"] = {"ok": pytest_ok, "passed": passed, "failed": failed, "tail": tail}
    guards_ok &= pytest_ok

    # --- Guard 2: readiness audit -> READY ---
    r = _run_script("scripts/central_readiness_audit.py")
    ready = "READY" in r.stdout and r.returncode == 0
    breakdown["readiness"] = {"ok": ready, "rc": r.returncode}
    guards_ok &= ready

    # --- Guard 3: integrity audit -> exit 0 (scoped to pinned run root) ---
    r = _run_script("scripts/central_integrity_audit.py", str(REPLAY_ROOT))
    breakdown["integrity"] = {"ok": r.returncode == 0, "rc": r.returncode}
    guards_ok &= r.returncode == 0

    # --- Guard 4: feature census -> every verdict line green ---
    census_json = OUT_DIR / "census-latest.json"
    r = _run_script("scripts/central_feature_census.py", "--output", str(census_json))
    bad = [ln for ln in r.stdout.splitlines()
           if ln.strip().isupper() and ln.strip().endswith(("_NOT_PROVEN", "BLOCKED", "FIRES", "CALLERS", "MISSES"))]
    bad = [ln for ln in bad if not ln.startswith(("NO_", "ALL_"))]
    census_ok = r.returncode == 0 and not bad
    breakdown["census"] = {"ok": census_ok, "rc": r.returncode, "bad_verdicts": bad}
    guards_ok &= census_ok

    # --- Improvable axis: efficiency replay reduction ratio (skipped when a
    # guard already failed — score is 0 either way) ---
    if not guards_ok:
        breakdown["efficiency_replay"] = {"skipped": "guards failed"}
        score = 0.0
        breakdown["guards_ok"] = False
        breakdown["elapsed_sec"] = round(time.time() - t0, 1)
        (OUT_DIR / "verify-latest.json").write_text(
            json.dumps(breakdown, indent=2, sort_keys=True), encoding="utf-8")
        print(f"provider_free_score={score}")
        print(json.dumps(breakdown, indent=1, default=str)[:1500])
        return 1
    # Script prints the result JSON then a verdict line; parse first JSON block.
    r = _run_script("scripts/central_efficiency_replay.py", str(REPLAY_ROOT),
                    timeout=3600)
    ratio = 0.0
    try:
        start = r.stdout.index("{")
        data = json.loads(r.stdout[start:r.stdout.rindex("}") + 1])
        ratio = float(data.get("projected_provider_view_reduction_ratio") or 0.0)
        (OUT_DIR / "efficiency-replay-latest.json").write_text(
            json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        breakdown["efficiency_replay"] = {
            "rc": r.returncode,
            "task_count": data.get("task_count"),
            "receipt_complete": data.get("receipt_complete"),
            "reduction_ratio": ratio,
            "chars_avoided": data.get("projected_provider_view_chars_avoided"),
            "compaction_deferrals": data.get("provider_view_compaction_deferrals"),
            "projected_epochs": data.get("provider_view_replay_compaction_epochs"),
        }
    except (ValueError, json.JSONDecodeError) as exc:
        breakdown["efficiency_replay"] = {
            "rc": r.returncode, "reduction_ratio": 0.0, "error": str(exc),
            "tail": r.stdout.strip().splitlines()[-3:],
        }

    score = round(ratio * 100.0, 4) if guards_ok else 0.0
    breakdown["guards_ok"] = guards_ok
    breakdown["elapsed_sec"] = round(time.time() - t0, 1)

    (OUT_DIR / "verify-latest.json").write_text(
        json.dumps(breakdown, indent=2, sort_keys=True), encoding="utf-8")
    print(f"provider_free_score={score}")
    print(json.dumps({k: v for k, v in breakdown.items() if k != "pytest" or True},
                     indent=1, default=str)[:1500])
    return 0 if guards_ok else 1


if __name__ == "__main__":
    sys.exit(main())
