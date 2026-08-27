"""Build and certify the GroundTruth Go indexer from checked-in source."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

GT_INDEX_SOURCE_SHA256 = "f66932b655486667bf8434fcb2d21e0c2448ea46a06c7e2236f30a8e74ae9d11"
GT_INDEX_SOURCE_OBJECT = GT_INDEX_SOURCE_SHA256
GT_INDEX_SOURCE_FILES = 84
GT_INDEX_BUILD_ID = f"source-{GT_INDEX_SOURCE_SHA256[:16]}"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
INDEXER_SOURCE = REPOSITORY_ROOT / "vendor" / "gt-index-src"


@dataclass(frozen=True, slots=True)
class IndexerSetupReceipt:
    status: str
    source_path: str
    source_git_object: str
    go_path: str
    go_version: str
    binary_path: str
    binary_sha256: str
    build_duration_ms: float
    cached: bool
    diagnostic: str = ""
    observed_source_tree_sha256: str = ""
    observed_source_files: int = 0

    def as_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["source_tree_sha256"] = GT_INDEX_SOURCE_SHA256
        value["build_id"] = GT_INDEX_BUILD_ID
        return value


def _binary_name() -> str:
    return "gt-index.exe" if os.name == "nt" else "gt-index"


def _go_candidates() -> tuple[Path, ...]:
    found = shutil.which("go")
    rows: list[Path] = [Path(found)] if found else []
    if os.name == "nt":
        rows.extend(
            [
                Path(r"C:\Go\bin\go.exe"),
                Path(r"C:\Program Files\Go\bin\go.exe"),
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Go" / "bin" / "go.exe",
            ]
        )
    return tuple(dict.fromkeys(path for path in rows if str(path)))


def find_go() -> Path | None:
    return next((path.resolve() for path in _go_candidates() if path.is_file()), None)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_source_payload(path: Path) -> bytes:
    """Return platform-independent bytes for a checked-in source file.

    Git may materialize text files with CRLF on Windows even though the blob is
    stored with LF.  The indexer source tree is entirely text, so its release
    identity must describe repository content rather than checkout policy.
    """

    return path.read_bytes().replace(b"\r\n", b"\n")


def _source_tree_identity() -> tuple[str, int]:
    digest = hashlib.sha256()
    paths = sorted(
        (
            (
                path.relative_to(REPOSITORY_ROOT).as_posix(),
                path,
            )
            for path in INDEXER_SOURCE.rglob("*")
            if path.is_file() and path.name not in {"gt-index", "gt-index.exe"}
        ),
        key=lambda row: row[0],
    )
    for relative_text, path in paths:
        relative = relative_text.encode("utf-8")
        payload = _canonical_source_payload(path)
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest(), len(paths)


def _cache_directory() -> Path:
    override = str(os.environ.get("GT_HARNESS_CACHE_DIR") or "").strip()
    base = Path(override).expanduser() if override else Path.home() / ".groundtruth"
    return base / "bin" / GT_INDEX_BUILD_ID


def ensure_source_indexer(*, force: bool = False, timeout: float = 900.0) -> IndexerSetupReceipt:
    """Return a verified indexer, compiling the pinned source when necessary."""

    started = time.perf_counter()
    source = INDEXER_SOURCE.resolve()
    go = find_go()
    target_dir = _cache_directory()
    binary = target_dir / _binary_name()
    receipt_path = target_dir / "build-receipt.json"

    if not (source / "go.mod").is_file() or not (source / "cmd" / "gt-index" / "main.go").is_file():
        return IndexerSetupReceipt(
            "FAILED",
            str(source),
            GT_INDEX_SOURCE_OBJECT,
            str(go or ""),
            "",
            "",
            "",
            0.0,
            False,
            "vendored_indexer_source_missing",
        )
    source_digest, source_files = _source_tree_identity()
    if source_digest != GT_INDEX_SOURCE_SHA256 or source_files != GT_INDEX_SOURCE_FILES:
        return IndexerSetupReceipt(
            "FAILED",
            str(source),
            GT_INDEX_SOURCE_OBJECT,
            str(go or ""),
            "",
            "",
            "",
            0.0,
            False,
            "vendored_indexer_source_identity_mismatch",
            observed_source_tree_sha256=source_digest,
            observed_source_files=source_files,
        )
    if go is None:
        return IndexerSetupReceipt(
            "FAILED",
            str(source),
            GT_INDEX_SOURCE_OBJECT,
            "",
            "",
            "",
            "",
            0.0,
            False,
            "go_not_found",
            observed_source_tree_sha256=source_digest,
            observed_source_files=source_files,
        )

    version_result = subprocess.run(
        [str(go), "version"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    go_version = " ".join(version_result.stdout.split())
    if not force and binary.is_file() and receipt_path.is_file():
        try:
            prior = json.loads(receipt_path.read_text(encoding="utf-8"))
            digest = _sha256(binary)
            if (
                prior.get("source_git_object") == GT_INDEX_SOURCE_OBJECT
                and prior.get("source_tree_sha256") == GT_INDEX_SOURCE_SHA256
                and prior.get("binary_sha256") == digest
            ):
                return IndexerSetupReceipt(
                    "READY",
                    str(source),
                    GT_INDEX_SOURCE_OBJECT,
                    str(go),
                    go_version,
                    str(binary),
                    digest,
                    round((time.perf_counter() - started) * 1000.0, 3),
                    True,
                    observed_source_tree_sha256=source_digest,
                    observed_source_files=source_files,
                )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if os.name == "nt" else ""
    with tempfile.NamedTemporaryFile(
        dir=target_dir, prefix=".gt-index-build-", suffix=suffix, delete=False
    ) as handle:
        candidate = Path(handle.name)
    candidate.unlink(missing_ok=True)
    environment = dict(os.environ)
    environment["CGO_ENABLED"] = "1"
    try:
        result = subprocess.run(
            [
                str(go),
                "build",
                "-tags",
                "sqlite_fts5",
                "-trimpath",
                "-o",
                str(candidate),
                "./cmd/gt-index/",
            ],
            cwd=source,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, float(timeout)),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        candidate.unlink(missing_ok=True)
        return IndexerSetupReceipt(
            "FAILED",
            str(source),
            GT_INDEX_SOURCE_OBJECT,
            str(go),
            go_version,
            "",
            "",
            round((time.perf_counter() - started) * 1000.0, 3),
            False,
            type(exc).__name__,
            observed_source_tree_sha256=source_digest,
            observed_source_files=source_files,
        )
    if result.returncode != 0 or not candidate.is_file():
        candidate.unlink(missing_ok=True)
        diagnostic = " ".join((result.stderr or result.stdout).split())[-2000:]
        return IndexerSetupReceipt(
            "FAILED",
            str(source),
            GT_INDEX_SOURCE_OBJECT,
            str(go),
            go_version,
            "",
            "",
            round((time.perf_counter() - started) * 1000.0, 3),
            False,
            diagnostic or f"go_build_exit_{result.returncode}",
            observed_source_tree_sha256=source_digest,
            observed_source_files=source_files,
        )

    if os.name != "nt":
        candidate.chmod(candidate.stat().st_mode | stat.S_IXUSR)
    os.replace(candidate, binary)
    digest = _sha256(binary)
    receipt = IndexerSetupReceipt(
        "READY",
        str(source),
        GT_INDEX_SOURCE_OBJECT,
        str(go),
        go_version,
        str(binary),
        digest,
        round((time.perf_counter() - started) * 1000.0, 3),
        False,
        observed_source_tree_sha256=source_digest,
        observed_source_files=source_files,
    )
    temporary_receipt = receipt_path.with_suffix(".tmp")
    temporary_receipt.write_text(
        json.dumps(receipt.as_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary_receipt, receipt_path)
    return receipt


__all__ = [
    "GT_INDEX_BUILD_ID",
    "GT_INDEX_SOURCE_OBJECT",
    "GT_INDEX_SOURCE_FILES",
    "GT_INDEX_SOURCE_SHA256",
    "IndexerSetupReceipt",
    "ensure_source_indexer",
    "find_go",
]
