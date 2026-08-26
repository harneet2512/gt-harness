"""Drive the released CLI through a real graph lifecycle and retain proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _run(arguments: list[str], *, cwd: Path, env: dict[str, str]) -> tuple[int, str, str]:
    result = subprocess.run(
        [sys.executable, "-m", "gt_harness.cli", *arguments],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


def _json(stdout: str, *, operation: str) -> dict[str, Any]:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{operation} emitted non-JSON output: {stdout[-1000:]}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{operation} output is not an object")
    return value


def _write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def verify(output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    operations: list[dict[str, Any]] = []

    doctor_code, doctor_stdout, doctor_stderr = _run(["doctor", "--no-build"], cwd=ROOT, env=env)
    (output / "doctor.stdout.json").write_text(doctor_stdout, encoding="utf-8")
    (output / "doctor.stderr.log").write_text(doctor_stderr, encoding="utf-8")
    if doctor_code != 0:
        raise RuntimeError("gt-harness doctor failed")
    _json(doctor_stdout, operation="doctor")
    operations.append({"operation": "doctor", "exit_code": doctor_code})

    with tempfile.TemporaryDirectory(prefix="gt-harness-verification-") as temporary:
        fixture = Path(temporary) / "repository"
        state = Path(temporary) / "state"
        fixture.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=fixture, check=True)
        subprocess.run(
            ["git", "config", "user.email", "verification@example.invalid"],
            cwd=fixture,
            check=True,
        )
        subprocess.run(["git", "config", "user.name", "GT Verification"], cwd=fixture, check=True)
        (fixture / "app.py").write_text(
            "def answer():\n    return 42\n\ndef invoke():\n    return answer()\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "app.py"], cwd=fixture, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=fixture, check=True)

        build_code, build_stdout, build_stderr = _run(
            [
                "graph",
                "build",
                "--root",
                str(fixture),
                "--state-dir",
                str(state),
                "--force",
            ],
            cwd=ROOT,
            env=env,
        )
        (output / "cold-build.stderr.log").write_text(build_stderr, encoding="utf-8")
        build = _json(build_stdout, operation="cold graph build")
        _write(output / "cold-build.json", build)
        if build_code != 0 or build.get("query_ready") is not True:
            raise RuntimeError("cold graph build did not become query-ready")
        operations.append({"operation": "cold_build", "exit_code": build_code})

        query_code, query_stdout, query_stderr = _run(
            [
                "graph",
                "query",
                "--root",
                str(fixture),
                "--state-dir",
                str(state),
                "definition",
                "answer",
            ],
            cwd=ROOT,
            env=env,
        )
        query = _json(query_stdout, operation="definition query")
        _write(output / "definition-query.json", query)
        (output / "definition-query.stderr.log").write_text(query_stderr, encoding="utf-8")
        if query_code != 0 or not any(
            row.get("name") == "answer" and row.get("file_path") == "app.py"
            for row in query.get("evidence", [])
        ):
            raise RuntimeError("definition query did not return repository truth")
        operations.append({"operation": "definition_query", "exit_code": query_code})

        (fixture / "app.py").write_text(
            "def updated_answer():\n    return 43\n\ndef invoke():\n    return updated_answer()\n",
            encoding="utf-8",
        )
        stale_code, stale_stdout, stale_stderr = _run(
            ["graph", "status", "--root", str(fixture), "--state-dir", str(state)],
            cwd=ROOT,
            env=env,
        )
        stale = _json(stale_stdout, operation="stale status")
        _write(output / "stale-status.json", stale)
        (output / "stale-status.stderr.log").write_text(stale_stderr, encoding="utf-8")
        if stale_code == 0 or stale.get("build_status") != "STALE" or stale.get("query_ready"):
            raise RuntimeError("repository mutation was not fail-closed as STALE")
        operations.append({"operation": "stale_detection", "exit_code": stale_code})

        rebuild_code, rebuild_stdout, rebuild_stderr = _run(
            ["graph", "build", "--root", str(fixture), "--state-dir", str(state)],
            cwd=ROOT,
            env=env,
        )
        rebuild = _json(rebuild_stdout, operation="graph rebuild")
        _write(output / "rebuild.json", rebuild)
        (output / "rebuild.stderr.log").write_text(rebuild_stderr, encoding="utf-8")
        if rebuild_code != 0 or rebuild.get("query_ready") is not True:
            raise RuntimeError("graph rebuild did not restore readiness")
        if rebuild.get("generation_id") == build.get("generation_id"):
            raise RuntimeError("changed repository reused the old graph generation")
        operations.append({"operation": "rebuild", "exit_code": rebuild_code})

    evidence = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.iterdir())
        if path.is_file()
    }
    result = {
        "schema": "gt.cli_verification.v1",
        "status": "PASS",
        "surface": "gt-harness CLI",
        "operations": operations,
        "immutable_generation_changed": True,
        "temporary_state_cleaned": True,
        "evidence_sha256": evidence,
        "provider_calls": 0,
        "provider_credentials_inspected": False,
    }
    _write(output / "verification-summary.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts" / "verification" / "latest"
    )
    args = parser.parse_args()
    result = verify(args.output.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
