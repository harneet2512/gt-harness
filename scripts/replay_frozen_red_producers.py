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
        _executable_identity,
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
        _executable_identity,
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
        "toolchain": ["sh", "-c", "printf '%s\\n' frozen-posix-shell"],
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
EXPECTED_BLOBS = {
    "cha_rta_boundary": {
        "base": "0f09e993574bbeffe04bcb18401574b15c310bd9",
        "overlay": GROUNDTRUTH_PARENT,
        "blobs": {
            ".githooks/tests/cha_rta_boundary_red.sh": (
                "9db5ed7c789d5eb9f614ced9e3c31283710992b97abd7e124e21cc5233ff507f"
            ),
            ".githooks/red-artifacts/cha_rta_boundary_red.receipt": (
                "f779c85ed06c670512a792beb5425545ff8ec59fbb32e1306dc045247cc0a0e6"
            ),
        },
        "expected_output": "4e5a94d86ff35d088c69a038023fd47665174c7417fcde5795e97b91a27fb00b",
    },
    "vta_step5_candidate_proof": {
        "base": GROUNDTRUTH_PARENT,
        "overlay": GROUNDTRUTH_CANDIDATE,
        "blobs": {
            "gt-index/internal/resolver/vta_candidate_proof_red_test.go": (
                "2f862f21dbc3ff18b05ca36fb7b19559f463c21f7719d58d2c86e2c0b50c5656"
            ),
            ".githooks/tests/vta_step5_candidate_proof_red.sh": (
                "99fe24379c6a0ad92f61be8ee71faf21fdb81086446a994a8b559e6807827234"
            ),
            ".githooks/red-artifacts/vta_step5_candidate_proof_red.receipt": (
                "7232164cb8602ae0790bc977d00a69b7c8bfe8b38c5ef34a16a24928b34096c7"
            ),
        },
    },
}
VTA_DIAGNOSTICS = [
    "./internal/resolver/vta_candidate_proof_red_test.go:29:27: undefined: VTAFlowProof",
    (
        "./internal/resolver/vta_candidate_proof_red_test.go:29:56: results[0].FlowProofs "
        "undefined (type VTAResult has no field or method FlowProofs)"
    ),
    (
        "./internal/resolver/vta_candidate_proof_red_test.go:30:35: results[0].FlowProofs "
        "undefined (type VTAResult has no field or method FlowProofs)"
    ),
]


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


def _resolve_tool(name: str) -> str:
    selected = shutil.which(name)
    if selected is None:
        raise RuntimeError(f"tool_unavailable:{name}")
    return str(Path(selected).resolve())


def _git_show(repo: Path, commit: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{path}"],
        check=True,
        capture_output=True,
    ).stdout


def _assert_manifest(repo: Path, name: str) -> None:
    manifest = EXPECTED_BLOBS[name]
    for commit_key in ("base", "overlay"):
        commit = str(manifest[commit_key])
        if subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{commit}^{{commit}}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode:
            raise RuntimeError(f"manifest_commit_missing:{name}:{commit_key}:{commit}")
    for path, expected in manifest["blobs"].items():
        actual = hashlib.sha256(_git_show(repo, str(manifest["overlay"]), path)).hexdigest()
        if actual != expected:
            raise RuntimeError(f"manifest_hash_mismatch:{name}:{path}:{actual}:{expected}")


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


def _prepare_vta(root: Path, cache: Path, evidence_parent: Path) -> dict[str, object]:
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
            "GOPROXY": "https://proxy.golang.org,direct",
            "GOSUMDB": "off",
            "GOWORK": "off",
        }
    )
    preparation_log = evidence_parent / "preparation.log"
    preparation_log.parent.mkdir(parents=True, exist_ok=True)
    log_parts: list[bytes] = []
    download = subprocess.run(
        ["go", "mod", "download"], cwd=root, env=environment, check=False, capture_output=True
    )
    log_parts.append(download.stdout + download.stderr)
    if download.returncode != 0:
        preparation_log.write_bytes(b"".join(log_parts))
        raise RuntimeError(f"dependency_preparation_failed:{download.returncode}")
    preparation = subprocess.run(
        ["go", "test", "./internal/parser", "-run", "^$", "-count=1"],
        cwd=root,
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    log_parts.append(preparation.stdout)
    preparation_log.write_bytes(b"".join(log_parts))
    go_path = Path(_resolve_tool("go"))
    gcc_path = Path(_resolve_tool("gcc"))
    record = {
        "phase": "dependency_preparation",
        "commands": [
            {"argv": ["go", "mod", "download"], "exit_code": download.returncode},
            {
                "argv": ["go", "test", "./internal/parser", "-run", "^$", "-count=1"],
                "exit_code": preparation.returncode,
            },
        ],
        "cwd": str(root),
        "environment": {
            "CGO_ENABLED": "1",
            "GOPROXY": environment["GOPROXY"],
            "GOMODCACHE": str(cache / "GOMODCACHE"),
            "GOCACHE": str(cache / "GOCACHE"),
            "GOPATH": str(cache / "GOPATH"),
        },
        "exit_code": download.returncode,
        "test_exit_code": preparation.returncode,
        "log_sha256": hashlib.sha256(preparation_log.read_bytes()).hexdigest(),
        "log_size": preparation_log.stat().st_size,
        "go_executable": str(go_path),
        "go_identity": _executable_identity(go_path),
        "gcc_executable": str(gcc_path),
        "gcc_identity": _executable_identity(gcc_path),
    }
    (evidence_parent / "preparation.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if download.returncode != 0:
        raise RuntimeError(f"dependency_preparation_failed:{download.returncode}")
    if preparation.returncode != 0:
        raise RuntimeError(f"dependency_preparation_failed:{preparation.returncode}")
    return record


def _cache_manifest(cache: Path, destination: Path) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    paths = [p for p in cache.rglob("*") if p.is_file()]
    if any(path.is_symlink() for path in cache.rglob("*")):
        raise RuntimeError("prepared_cache_symlink_present")
    for path in sorted(paths, key=lambda item: item.relative_to(cache).as_posix()):
        data = path.read_bytes()
        entries.append(
            {
                "path": path.relative_to(cache).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        )
    manifest = {"schema": "gt.red_evidence.prepared_cache.v1", "entries": entries}
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    result = {
        **manifest,
        "entry_count": len(entries),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "size": len(encoded),
    }
    destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "schema": result["schema"],
        "entry_count": result["entry_count"],
        "sha256": result["sha256"],
        "size": result["size"],
    }


def _verify_cache_manifest(cache: Path, manifest_path: Path) -> dict[str, object]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"prepared_cache_manifest_unreadable:{type(exc).__name__}") from exc
    entries = manifest.get("entries") if isinstance(manifest, dict) else None
    if (
        not isinstance(entries, list)
        or manifest.get("schema") != "gt.red_evidence.prepared_cache.v1"
    ):
        raise RuntimeError("prepared_cache_manifest_invalid")
    paths: list[str] = []
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "sha256", "size"}
            or not isinstance(entry.get("path"), str)
            or not isinstance(entry.get("sha256"), str)
            or not isinstance(entry.get("size"), int)
        ):
            raise RuntimeError("prepared_cache_manifest_entry_invalid")
        paths.append(entry["path"])
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise RuntimeError("prepared_cache_manifest_order")
    for entry in entries:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != entry["path"]:
            raise RuntimeError("prepared_cache_manifest_path_invalid")
        path = (cache / entry["path"]).resolve()
        try:
            path.relative_to(cache.resolve())
        except ValueError as exc:
            raise RuntimeError("prepared_cache_manifest_path_escape") from exc
        if not path.is_file():
            raise RuntimeError(f"prepared_cache_file_missing:{entry['path']}")
        if path.is_symlink():
            raise RuntimeError(f"prepared_cache_symlink:{entry['path']}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"] or len(data) != entry["size"]:
            raise RuntimeError(f"prepared_cache_file_mismatch:{entry['path']}")
    payload = {"schema": manifest["schema"], "entries": entries}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    if manifest.get("sha256") != hashlib.sha256(encoded).hexdigest():
        raise RuntimeError("prepared_cache_manifest_digest_mismatch")
    if manifest.get("entry_count") != len(entries) or manifest.get("size") != len(encoded):
        raise RuntimeError("prepared_cache_manifest_metadata_mismatch")
    actual_paths = sorted(
        path.relative_to(cache).as_posix()
        for path in cache.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    if actual_paths != paths:
        raise RuntimeError("prepared_cache_manifest_file_set_mismatch")
    return {
        "schema": manifest["schema"],
        "entry_count": len(entries),
        "sha256": manifest["sha256"],
        "size": manifest["size"],
    }


def _composite_manifest(root: Path, spec: dict[str, object]) -> dict[str, object]:
    entries = []
    for path in _all_files(root):
        data = (root / path).read_bytes()
        entries.append(
            {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        )
    payload = {
        "schema": "gt.red_evidence.composite_tree.v1",
        "base": spec["base"],
        "overlay": spec["commit"],
        "prefixes": spec["prefixes"],
        "overlays": spec["overlays"],
        "entries": entries,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return {
        **payload,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "entry_count": len(entries),
        "size": len(encoded),
    }


def _write_environment_failure(*, evidence_parent: Path, name: str, error: CaptureError) -> None:
    """Keep decisive command bytes when canonical publication is refused."""

    evidence_parent.mkdir(parents=True, exist_ok=True)
    raw_path: Path | None = None
    raw = error.raw_bytes
    if raw is not None:
        raw_path = evidence_parent / f"{name}-environment-failure.raw.log"
        raw_path.write_bytes(raw)
    record: dict[str, object] = {
        "schema": "gt.red_evidence.replay_environment_failure.v1",
        "status": "fail",
        "type": "REPLAY_ENVIRONMENT_UNVERIFIED",
        "message": str(error),
        "command": error.command,
        "toolchain": error.toolchain,
        "environment": error.environment,
    }
    if raw_path is not None and raw is not None:
        record["raw_output"] = {
            "path": raw_path.name,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
        }
    (evidence_parent / f"{name}-environment-failure.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _run_capture(
    *,
    root: Path,
    spec: dict[str, object],
    evidence: Path,
    composite: dict[str, object],
    composite_name: str,
    runner_image: str | None = None,
    runner_image_version: str | None = None,
    runner_architecture: str | None = None,
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
        preparation = _prepare_vta(capture_root, cache, evidence.parent)
        preparation["cache_manifest"] = _cache_manifest(
            cache, evidence.parent / "cache-manifest.json"
        )
        (evidence.parent / "preparation.json").write_text(
            json.dumps(preparation, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
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
        preparation_path = evidence.parent / "preparation.json"
        preparation_provenance = {
            "schema": "gt.red_evidence.prepared_provenance.v1",
            "preparation_sha256": hashlib.sha256(preparation_path.read_bytes()).hexdigest(),
            "cache_manifest_sha256": str(preparation["cache_manifest"]["sha256"]),
            "composite_manifest_sha256": str(composite["sha256"]),
            "composite_manifest_name": composite_name,
            "go_identity": preparation["go_identity"],
            "gcc_identity": preparation["gcc_identity"],
        }
        preparation_provenance["sha256"] = hashlib.sha256(
            json.dumps(
                {key: value for key, value in preparation_provenance.items() if key != "sha256"},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        capture_kwargs["prepared_provenance"] = preparation_provenance
    try:
        result = capture(
            root=capture_root,
            sources=[source],
            fixtures=[path for path in files if path != source],
            command=command,
            toolchain_command=toolchain,
            expected_source_path=source,
            expected_diagnostic=str(spec["diagnostic"]),
            output_grammar=str(spec["grammar"]),
            runner_image=runner_image,
            runner_image_version=runner_image_version,
            runner_architecture=runner_architecture,
            **capture_kwargs,
        )
    except CaptureError as exc:
        _write_environment_failure(evidence_parent=evidence.parent, name=evidence.name, error=exc)
        raise RuntimeError(f"REPLAY_ENVIRONMENT_UNVERIFIED:{exc}") from exc
    if spec["grammar"] == OUTPUT_GRAMMAR:
        lines = result.canonical_bytes.decode("utf-8").splitlines()
        if (
            len(lines) != 5
            or lines[1:4] != VTA_DIAGNOSTICS
            or lines[-1] != "PACKAGE_OUTCOME=build_failed"
        ):
            raise RuntimeError("vta_diagnostic_conservation_failed")
    publish_evidence_directory(
        evidence_dir=evidence, root=capture_root, inputs=files, result=result
    )
    prepared_cache = (
        evidence.parent / "prepared-cache" if spec["grammar"] == OUTPUT_GRAMMAR else None
    )
    if prepared_cache is not None:
        _verify_cache_manifest(prepared_cache, evidence.parent / "cache-manifest.json")
    report = verify(
        root=capture_root,
        evidence_dir=evidence,
        expected_receipt_sha256=str(result.receipt["receipt_sha256"]),
        replay=True,
        prepared_cache=prepared_cache,
        prepared_provenance_dir=evidence.parent if prepared_cache is not None else None,
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
        "normalizer": "receipt",
        "canonical": "canonical",
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
        elif kind == "canonical":
            (case / "canonical.txt").write_bytes(
                (case / "canonical.txt").read_bytes() + b"mutation"
            )
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
            elif name == "normalizer":
                payload["normalizer_version"] = "mutated-normalizer"
            else:
                payload["receipt_sha256"] = "0" * 64
            if name in {"diagnostic", "exit", "toolchain", "normalizer"}:
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


def replay(
    *,
    harness_root: Path,
    groundtruth_root: Path,
    output: Path,
    runner_image: str | None = None,
    runner_image_version: str | None = None,
    runner_architecture: str | None = None,
) -> dict[str, object]:
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
            _assert_manifest(groundtruth_root, name)
            root = temporary / name
            _materialize(
                groundtruth_root,
                str(spec["base"]),
                root,
                overlay_commit=str(spec["commit"]),
                overlays=list(spec["overlays"]),
                prefixes=list(spec["prefixes"]),
            )
            composite = _composite_manifest(root, spec)
            (evidence_root / f"{name}-composite.json").write_text(
                json.dumps(composite, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            evidence = evidence_root / name
            receipt, verification, capture_root = _run_capture(
                root=root,
                spec=spec,
                evidence=evidence,
                composite=composite,
                composite_name=f"{name}-composite.json",
                runner_image=runner_image,
                runner_image_version=runner_image_version,
                runner_architecture=runner_architecture,
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
                "diagnostics": (
                    receipt["diagnostic"]["matched_diagnostics"]
                    if spec["grammar"] == EXACT_TEXT_GRAMMAR
                    else (evidence / "canonical.txt").read_text(encoding="utf-8").splitlines()[1:-1]
                ),
                "environment_policy": receipt["environment_policy"],
                "receipt_sha256": receipt["receipt_sha256"],
                "verification": verification,
                "mutations": mutations,
                "composite_manifest": composite,
            }
            if spec["grammar"] == OUTPUT_GRAMMAR:
                preparation_path = evidence.parent / "preparation.json"
                results[name]["preparation"] = json.loads(
                    preparation_path.read_text(encoding="utf-8")
                )
            expected_output = EXPECTED_BLOBS[name].get("expected_output")
            if expected_output is not None and receipt["diagnostic"]["sha256"] != expected_output:
                raise RuntimeError(
                    f"observed_output_hash_mismatch:{name}:{receipt['diagnostic']['sha256']}:{expected_output}"
                )
    report = {"schema": "gt.red_evidence.frozen_replay.v2", "status": "pass", "results": results}
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", default=".")
    parser.add_argument("--groundtruth-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--runner-image")
    parser.add_argument("--runner-image-version")
    parser.add_argument("--runner-architecture")
    args = parser.parse_args()
    try:
        report = replay(
            harness_root=Path(args.harness_root).resolve(),
            groundtruth_root=Path(args.groundtruth_root).resolve(),
            output=Path(args.output).resolve(),
            runner_image=args.runner_image,
            runner_image_version=args.runner_image_version,
            runner_architecture=args.runner_architecture,
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
