"""Execute immutable Groundtruth RED runners through canonical capture."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

try:
    from scripts.check_red_evidence_producers import validate
    from scripts.red_evidence import (
        EXACT_TEXT_GRAMMAR,
        OUTPUT_GRAMMAR,
        CaptureError,
        _receipt_sha256,
        capture,
        publish_evidence_directory,
        verify,
    )
except ModuleNotFoundError:
    from check_red_evidence_producers import validate
    from red_evidence import (
        EXACT_TEXT_GRAMMAR,
        OUTPUT_GRAMMAR,
        CaptureError,
        _receipt_sha256,
        capture,
        publish_evidence_directory,
        verify,
    )

GROUNDTRUTH_PARENT = "7674304191f9f53bee9a3e0ce42033da7973e665"
GROUNDTRUTH_CANDIDATE = "9e89322e09c330cc94eca663f7f87b32760c5583"
REPRESENTATIVES = {
    "cha_rta_boundary": {
        "commit": GROUNDTRUTH_PARENT,
        "path": ".githooks/tests/cha_rta_boundary_red.sh",
        "grammar": EXACT_TEXT_GRAMMAR,
        "diagnostic": "CHA/RTA implementation missing\n",
        "expected_source": ".githooks/tests/cha_rta_boundary_red.sh",
        "toolchain": ["sh", "--version"],
        "base": "0f09e993574bbeffe04bcb18401574b15c310bd9",
        "overlays": [
            ".githooks/tests/cha_rta_boundary_red.sh",
            ".githooks/red-artifacts/cha_rta_boundary_red.receipt",
        ],
        "prefixes": ["gt-index/internal/resolver", ".githooks/tests"],
    },
    "vta_step5_candidate_proof": {
        "commit": GROUNDTRUTH_CANDIDATE,
        "path": ".githooks/tests/vta_step5_candidate_proof_red.sh",
        "grammar": OUTPUT_GRAMMAR,
        "diagnostic": "undefined: VTAFlowProof",
        "expected_source": "gt-index/internal/resolver/vta_candidate_proof_red_test.go",
        "toolchain": ["go", "version"],
        "base": GROUNDTRUTH_PARENT,
        "overlays": [
            "gt-index/internal/resolver/vta_candidate_proof_red_test.go",
            ".githooks/tests/vta_step5_candidate_proof_red.sh",
            ".githooks/red-artifacts/vta_step5_candidate_proof_red.receipt",
        ],
        "prefixes": ["gt-index", ".githooks/tests", ".githooks/red-artifacts"],
    },
}


def _shell() -> str:
    if sys.platform != "win32":
        return "sh"
    for candidate in (
        Path("C:/Program Files/Git/bin/bash.exe"),
        Path("C:/Program Files/Git/usr/bin/bash.exe"),
        Path("C:/Program Files/Git/usr/bin/sh.exe"),
        Path("C:/Program Files/Git/bin/sh.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError("posix_shell_unavailable")


def _git_show(repo: Path, commit: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{path}"],
        check=True,
        capture_output=True,
    ).stdout


def _materialize(
    repo: Path,
    commit: str,
    destination: Path,
    *,
    overlay_commit: str | None = None,
    overlays: list[str] | None = None,
    prefixes: list[str] | None = None,
) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    valid_prefixes = [
        prefix
        for prefix in prefixes or []
        if subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{commit}:{prefix}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    ]
    archive_command = ["git", "-C", str(repo), "archive", "--format=tar", commit]
    archive_command.extend(valid_prefixes)
    archive = subprocess.run(
        archive_command,
        check=True,
        capture_output=True,
    )
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as stream:
        members = stream.getmembers()
        for member in members:
            target = (destination / member.name).resolve()
            try:
                target.relative_to(destination.resolve())
            except ValueError as exc:
                raise RuntimeError(f"archive_path_outside_root:{member.name}") from exc
        stream.extractall(destination)
    for path in overlays or []:
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_git_show(repo, overlay_commit or commit, path))
    subprocess.run(["git", "-C", str(destination), "init", "--quiet"], check=True)


def _all_files(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    )


def _prepare_vta(root: Path, cache: Path) -> None:
    """Warm content-addressed Go caches before the offline evidence phase."""

    for name in ("GOCACHE", "GOMODCACHE", "GOPATH"):
        (cache / name).mkdir(parents=True, exist_ok=True)
    environment = dict(__import__("os").environ)
    environment.update(
        {
            "CGO_ENABLED": "1",
            "GOCACHE": str(cache / "GOCACHE"),
            "GOMODCACHE": str(cache / "GOMODCACHE"),
            "GOPATH": str(cache / "GOPATH"),
            "GOSUMDB": "off",
            "GOWORK": "off",
        }
    )
    subprocess.run(["go", "mod", "download"], cwd=root, env=environment, check=True)
    preparation = subprocess.run(
        ["go", "test", "./internal/parser", "-run", "^$", "-count=1"],
        cwd=root,
        env=environment,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if preparation.returncode != 0:
        raise RuntimeError(f"dependency_preparation_failed:{preparation.returncode}")
    evidence_probe = subprocess.run(
        [
            "go",
            "test",
            "./internal/resolver",
            "-run",
            "^TestVTAPreservesCandidateSpecificProofPaths$",
            "-count=1",
        ],
        cwd=root,
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if evidence_probe.returncode == 0:
        raise RuntimeError("dependency_preparation_expected_red")


def _run_capture(
    *, root: Path, spec: dict[str, object], evidence: Path
) -> tuple[dict[str, object], dict[str, object], Path]:
    files = _all_files(root)
    source = str(spec["expected_source"])
    if source not in files:
        raise RuntimeError(f"expected_source_missing:{source}")
    shell = _shell()
    toolchain = list(spec["toolchain"])
    if toolchain[0] == "sh":
        toolchain[0] = shell
    capture_root = root
    capture_kwargs: dict[str, object] = {}
    command = [shell, str(spec["path"])]
    if spec["grammar"] == OUTPUT_GRAMMAR:
        capture_root = root / "gt-index"
        files = _all_files(capture_root)
        source = "internal/resolver/vta_candidate_proof_red_test.go"
        cache = evidence.parent / "prepared-cache"
        _prepare_vta(capture_root, cache)
        command = [
            "go",
            "test",
            "./internal/resolver",
            "-run",
            "^TestVTAPreservesCandidateSpecificProofPaths$",
            "-count=1",
        ]
        toolchain = ["go", "version"]
        capture_kwargs = {"cgo_enabled": "1", "cache_seed": cache}
    result = capture(
        root=capture_root,
        sources=[source],
        fixtures=[path for path in files if path != source],
        command=command,
        toolchain_command=toolchain,
        expected_source_path=source,
        expected_diagnostic=str(spec["diagnostic"]),
        output_grammar=str(spec["grammar"]),
        **capture_kwargs,
    )
    publish_evidence_directory(
        evidence_dir=evidence, root=capture_root, inputs=files, result=result
    )
    report = verify(
        root=capture_root,
        evidence_dir=evidence,
        expected_receipt_sha256=str(result.receipt["receipt_sha256"]),
    )
    if report["status"] != "pass":
        raise RuntimeError(json.dumps(report, sort_keys=True))
    return result.receipt, report, capture_root


def _mutation_matrix(
    root: Path, evidence: Path, receipt: dict[str, object], temporary: Path
) -> list[dict[str, object]]:
    mutations = {
        "source": "source",
        "fixture": "fixture",
        "command": "command",
        "diagnostic": "receipt",
        "exit": "receipt",
        "toolchain": "receipt",
        "raw": "raw",
        "external_digest": "receipt",
    }
    reports: list[dict[str, object]] = []
    for name, kind in mutations.items():
        case = temporary / f"mutation-{name}"
        case_root = temporary / f"root-{name}"
        shutil.copytree(root, case_root)
        shutil.copytree(evidence, case)
        if kind == "source":
            path = case_root / str(receipt["sources"][0]["path"])
            path.write_bytes(path.read_bytes() + b"\nmutation")
        elif kind == "fixture":
            path = case_root / str(receipt["fixtures"][0]["path"])
            path.write_bytes(path.read_bytes() + b"\nmutation")
        elif kind == "raw":
            (case / "raw.log").write_bytes((case / "raw.log").read_bytes() + b"mutation")
        else:
            payload = json.loads((case / "receipt.json").read_text(encoding="utf-8"))
            if name == "command":
                payload["command"]["argv"] = ["sh", "mutated.sh"]
            elif name == "diagnostic":
                payload["diagnostic"]["sha256"] = "0" * 64
            elif name == "exit":
                payload["command"]["exit_code"] = 7
            elif name == "toolchain":
                payload["toolchain"]["sha256"] = "0" * 64
            else:
                payload["receipt_sha256"] = "0" * 64
            if name in {"diagnostic", "exit", "toolchain"}:
                payload["receipt_sha256"] = _receipt_sha256(
                    {key: value for key, value in payload.items() if key != "receipt_sha256"}
                )
            (case / "receipt.json").write_text(json.dumps(payload), encoding="utf-8")
        checked = verify(
            root=case_root,
            evidence_dir=case,
            expected_receipt_sha256=str(receipt["receipt_sha256"]),
        )
        if checked["status"] == "pass":
            raise RuntimeError(f"mutation_accepted:{name}")
        reports.append({"mutation": name, "status": "rejected", "errors": checked["errors"]})
    return reports


def replay(*, harness_root: Path, groundtruth_root: Path, output: Path) -> dict[str, object]:
    inventory = validate(harness_root, groundtruth_root=groundtruth_root)
    if inventory["status"] != "pass":
        raise RuntimeError(json.dumps(inventory, sort_keys=True))
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence_root = output.parent / "evidence"
    evidence_root.mkdir(parents=True, exist_ok=True)
    results: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="gt-frozen-replay-") as temporary_name:
        temporary = Path(temporary_name)
        for name, spec in REPRESENTATIVES.items():
            root = temporary / name
            _materialize(
                groundtruth_root,
                str(spec["base"]),
                root,
                overlay_commit=str(spec["commit"]),
                overlays=list(spec["overlays"]),
                prefixes=list(spec["prefixes"]),
            )
            evidence = evidence_root / name
            receipt, verification, capture_root = _run_capture(
                root=root, spec=spec, evidence=evidence
            )
            mutation_workspace = temporary / f"mutations-{name}"
            mutation_workspace.mkdir()
            mutations = _mutation_matrix(capture_root, evidence, receipt, mutation_workspace)
            producer_bytes = _git_show(groundtruth_root, str(spec["commit"]), str(spec["path"]))
            results[name] = {
                "repository": "harneet2512/groundtruth",
                "commit": spec["commit"],
                "path": spec["path"],
                "producer_sha256": hashlib.sha256(producer_bytes).hexdigest(),
                "output_grammar": spec["grammar"],
                "command": receipt["command"],
                "toolchain": receipt["toolchain"],
                "raw_output_sha256": receipt["raw_output"]["sha256"],
                "canonical_sha256": receipt["diagnostic"]["sha256"],
                "receipt_sha256": receipt["receipt_sha256"],
                "verification": verification,
                "mutations": mutations,
            }
    report = {"schema": "gt.red_evidence.frozen_replay.v2", "status": "pass", "results": results}
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


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
    except (
        OSError,
        RuntimeError,
        CaptureError,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        tarfile.TarError,
    ) as exc:
        failure = {
            "schema": "gt.red_evidence.frozen_replay.v2",
            "status": "fail",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
        target = Path(args.output).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(failure, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(failure, sort_keys=True))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
