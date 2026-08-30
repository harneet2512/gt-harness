"""Replay representative frozen Groundtruth RED producers through the canonical CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from scripts.check_red_evidence_producers import validate
except ModuleNotFoundError:  # Direct `python scripts/replay_frozen_red_producers.py` execution.
    from check_red_evidence_producers import validate

GROUNDTRUTH_PARENT = "7674304191f9f53bee9a3e0ce42033da7973e665"
GROUNDTRUTH_CANDIDATE = "9e89322e09c330cc94eca663f7f87b32760c5583"
REPRESENTATIVES = {
    "cha_rta_boundary": (GROUNDTRUTH_PARENT, ".githooks/tests/cha_rta_boundary_red.sh"),
    "vta_step5_candidate_proof": (
        GROUNDTRUTH_CANDIDATE,
        ".githooks/tests/vta_step5_candidate_proof_red.sh",
    ),
}
RAW = (
    "# example.invalid/redfixture [example.invalid/redfixture.test]\n"
    "./proof_red_test.go:6:6: undefined: VTAFlowProof\n"
    "FAIL\texample.invalid/redfixture [build failed]\n"
    "FAIL\n"
)


def _git_show(repo: Path, commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{path}"],
        check=True,
        capture_output=True,
    )
    return result.stdout


def replay(*, harness_root: Path, groundtruth_root: Path, output: Path) -> dict[str, object]:
    inventory = validate(harness_root, groundtruth_root=groundtruth_root)
    if inventory["status"] != "pass":
        raise RuntimeError(json.dumps(inventory, sort_keys=True))
    cli = harness_root / "scripts" / "red_evidence.py"
    results: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="gt-frozen-replay-") as temporary:
        root = Path(temporary) / "fixture"
        root.mkdir()
        (root / "proof_red_test.go").write_text("package redfixture\n", encoding="utf-8")
        for name, (commit, producer_path) in REPRESENTATIVES.items():
            producer_bytes = _git_show(groundtruth_root, commit, producer_path)
            producer_file = root / f"{name}.producer"
            producer_file.write_bytes(producer_bytes)
            replay_file = root / f"{name}.py"
            replay_file.write_text(
                f"import sys\nsys.stdout.write({RAW!r})\nraise SystemExit(1)\n",
                encoding="utf-8",
            )
            evidence = Path(temporary) / name
            command = [sys.executable, replay_file.name]
            capture = subprocess.run(
                [
                    sys.executable,
                    str(cli),
                    "capture",
                    "--root",
                    str(root),
                    "--source",
                    "proof_red_test.go",
                    "--fixture",
                    producer_file.name,
                    "--fixture",
                    replay_file.name,
                    "--command-json",
                    json.dumps(command),
                    "--toolchain-command-json",
                    json.dumps([sys.executable, "--version"]),
                    "--expected-source-path",
                    "proof_red_test.go",
                    "--expected-diagnostic",
                    "undefined: VTAFlowProof",
                    "--evidence-dir",
                    str(evidence),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if capture.returncode != 0:
                raise RuntimeError(capture.stderr or capture.stdout)
            receipt = json.loads(capture.stdout)
            verified = subprocess.run(
                [
                    sys.executable,
                    str(cli),
                    "verify",
                    "--root",
                    str(root),
                    "--evidence-dir",
                    str(evidence),
                    "--expected-receipt-sha256",
                    receipt["receipt_sha256"],
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if verified.returncode != 0:
                raise RuntimeError(verified.stdout)
            producer_file.write_bytes(producer_bytes + b"\nmutation")
            mutated = subprocess.run(
                [
                    sys.executable,
                    str(cli),
                    "verify",
                    "--root",
                    str(root),
                    "--evidence-dir",
                    str(evidence),
                    "--expected-receipt-sha256",
                    receipt["receipt_sha256"],
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            mutation_report = json.loads(mutated.stdout)
            if mutated.returncode == 0 or "fixture_hash_mismatch" not in json.dumps(
                mutation_report
            ):
                raise RuntimeError("historical fixture mutation was accepted")
            results[name] = {
                "commit": commit,
                "path": producer_path,
                "producer_sha256": hashlib.sha256(producer_bytes).hexdigest(),
                "receipt_sha256": receipt["receipt_sha256"],
                "canonical_sha256": receipt["canonical_sha256"],
                "capture_exit": capture.returncode,
                "verify_exit": verified.returncode,
                "mutation_rejected": True,
            }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"schema": "gt.red_evidence.frozen_replay.v1", "results": results}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return {"schema": "gt.red_evidence.frozen_replay.v1", "results": results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", default=".")
    parser.add_argument("--groundtruth-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        report = replay(
            harness_root=Path(args.harness_root).resolve(),
            groundtruth_root=Path(args.groundtruth_root).resolve(),
            output=Path(args.output).resolve(),
        )
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {"schema": "gt.red_evidence.frozen_replay.v1", "status": "fail", "error": str(exc)}
            )
        )
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
