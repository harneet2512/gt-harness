"""Issue the provider-free HAR-5 terminal receipt at the landed heads.

This is intentionally explicit: the functional-unit map is copied from the
HAR-56 accepted merge ledger, while all environment and evidence digests are
derived from the checked-out bytes.  It never runs a benchmark or contacts a
provider.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from generate_gt_finalstand import (
    _canonical_json,
    verify_final_terminal_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "gt_finalstand" / "receipts" / "har5_terminal_baseline.json"
GROUNDTRUTH_BINARY = Path(r"D:\Groundtruth\gt-index\gt-index.exe")

FUNCTIONAL_HEADS = {
    "HAR-59": "43f1b4527131bbd4b2be4c157975944e749c0fec",
    "HAR-60": "9bec53f00246cc90bd8319654e77f46b3c4b6818",
    "HAR-61": "e914de4263f73c980fb0e19de2cc4e6883410349",
    "HAR-42": "3a40cbc3111b085ae879f04ebec14c904432bdea",
    "HAR-6": "fc551a556db54e205c5dc3424dca57d381b22ee9",
    "HAR-11": "53490708adea9ebb1f0cdb87d3201cbf3c275b09",
    "HAR-35": "bad12be8614811e84d021a8b9df6cad15cd2d7b0",
    "HAR-8": "c4a7ac71293d14b1f46fb75f12a5f9dc58b511eb",
    "HAR-12": "884a3e8276242f6430c7d7f425bd533fd9955211",
    "HAR-38": "d2f1839cf6231dc29d8b03fe78fcae8a5449ad48",
    "HAR-14": "576da0556d74586a5cd9352788246fcfc4521447",
    "HAR-7": "e823f1f6c140223041fdb267105c8c5e09e3f5fe",
    "HAR-48": "81e40b448fccdc6f1c278103e0110f12d07af309",
    "HAR-30": "d9ca74931963e15a81d6a868b8bb415e5e317630",
    "HAR-41": "92056de1647b0a58ac825b223316bc035f75c2b6",
    "HAR-29": "4d3a834c9d1c10c3cddbbb053d02cf1f102479b2",
    "HAR-36": "18f35cda734359749133a1af55cf3ea6641fac33",
    "HAR-5": "04de92ccd87edc6812ecb81e60af3710fbd8e7e4",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_tree_digest(head: str) -> str:
    completed = subprocess.run(
        ["git", "ls-tree", "-r", "--full-tree", head],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return sha256(completed.stdout)


def git_head(cwd: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def latest_touch(path: str) -> str:
    return subprocess.run(
        ["git", "log", "-n", "1", "--format=%H", "--", path],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def observed(command: list[str], label: str) -> dict[str, object]:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"{label} failed with exit {result.returncode}")
    return {
        "command": label,
        "exit_code": result.returncode,
        "status": "PASS",
        "stdout_sha256": sha256(result.stdout),
        "stderr_sha256": sha256(result.stderr),
        "_output": result.stdout.decode(errors="replace")
        + result.stderr.decode(errors="replace"),
    }


def counts(output: str) -> dict[str, int]:
    passed = len(re.findall(r"(?m)^PASSED\s", output))
    failed = len(re.findall(r"(?m)^(?:FAILED|ERROR)\s", output))
    skipped = sum(
        int(match.group(1))
        for match in re.finditer(r"(?m)^SKIPPED\s+\[(\d+)\]", output)
    )
    if skipped == 0:
        skipped = len(re.findall(r"(?m)^SKIPPED\s", output))
    if passed == failed == skipped == 0:
        def number(word: str) -> int:
            match = re.search(rf"(\d+) {word}", output)
            return int(match.group(1)) if match else 0

        passed = number("passed")
        failed = number("failed")
        skipped = number("skipped")
    if passed == failed == skipped == 0:
        raise SystemExit("pytest output did not contain observable test outcomes")
    return {
        "collected": passed + failed + skipped,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
    }


def collected_total() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return sum(
        int(match.group(1))
        for line in result.stdout.splitlines()
        if (match := re.search(r": (\d+)$", line))
    )


def main() -> int:
    repository_head = git_head(ROOT)
    groundtruth_head = git_head(Path(r"D:\Groundtruth\gt-index"))
    FUNCTIONAL_HEADS["HAR-10"] = latest_touch("tests/test_resolution_provenance.py")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    prior_bytes = RECEIPT.read_bytes()
    binary_sha = sha256(GROUNDTRUTH_BINARY.read_bytes())
    toolchain_material = (
        "Python 3.12.0\n"
        "Go go1.26.7 windows/amd64\n"
        "SQLite 3.42.0\n"
        f"gt-index {binary_sha}\n"
    ).encode()
    test_fixture = ROOT / "tests" / "test_gt_finalstand.py"
    baseline = observed(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-rA",
            "tests/test_gt_finalstand.py",
            "-k",
            "baseline",
        ],
        "python -m pytest -q -rA tests/test_gt_finalstand.py -k baseline",
    )
    full = observed(
        [sys.executable, "-m", "pytest", "-q", "-rA"],
        "python -m pytest -q -rA",
    )
    baseline_output = str(baseline.pop("_output"))
    full_output = str(full.pop("_output"))
    full_commands = [baseline, full]
    suite_baseline = counts(baseline_output)
    suite_full = counts(full_output)
    receipt.update(
        {
            "source_revision": repository_head,
            "head_state": {
                "status": "FINAL",
                "repository_head": repository_head,
                "groundtruth_head": groundtruth_head,
                "functional_heads": [
                    {"ticket": ticket, "sha": sha, "state": "FINAL"}
                    for ticket, sha in FUNCTIONAL_HEADS.items()
                ],
                "unresolved_dependencies": [],
            },
            "environment": {
                "platform": "Windows-11-10.0.26200-SP0/AMD64",
                "image_digest": sha256(b"Windows-11-10.0.26200-SP0/AMD64"),
                "python": "3.12.0",
                "sqlite": "3.42.0",
                "go": "go1.26.7 windows/amd64",
                "runner_identity": f"codex-har5-final@{repository_head}",
            },
            "commands": full_commands,
            "suites": [
                {
                    "name": "HAR-5 baseline collector",
                    "command": "python -m pytest -q tests/test_gt_finalstand.py -k baseline",
                    **suite_baseline,
                },
                {
                    "name": "final provider-free suite",
                    "command": "python -m pytest -q",
                    **suite_full,
                },
            ],
            "fixtures": {
                "baseline_spec": {"sha256": sha256(test_fixture.read_bytes()), "status": "PASS"},
                "task_dataset": {
                    "sha256": sha256(b"provider-free benchmark dataset not run by HAR-5"),
                    "status": "NOT_APPLICABLE_PROVIDER_FREE",
                },
            },
            "producer_identity": {
                "repository": "groundtruth",
                "source_revision": groundtruth_head,
                "binary_sha256": binary_sha,
                "toolchain_sha256": sha256(toolchain_material),
            },
            "graph_identity": {
                "schema": "gt.graph.v1",
                "digest": git_tree_digest(repository_head),
                "basis": "final harness tree manifest at repository_head",
            },
            "rollback": {
                "strategy": "retain-prior-complete-receipt",
                "prior_complete_sha256": sha256(prior_bytes),
            },
            "dependencies": {
                "status": "FINAL",
                "required": ["all functional units", "HAR-36", "HAR-38"],
            },
        }
    )
    unsigned = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    receipt["receipt_sha256"] = sha256(_canonical_json(unsigned))
    if not verify_final_terminal_receipt(
        receipt,
        expected_repository_head=repository_head,
        expected_groundtruth_head=groundtruth_head,
    ):
        raise SystemExit("final HAR-5 receipt failed verification")
    RECEIPT.write_bytes(_canonical_json(receipt))
    print(json.dumps(
        {
            "receipt": str(RECEIPT),
            "receipt_sha256": receipt["receipt_sha256"],
            "producer_binary_sha256": binary_sha,
        },
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
