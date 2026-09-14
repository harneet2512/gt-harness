"""Vendor a recorded Mini-SWE trial into a compact audit-replay fixture.

``gt_audit.audit_task`` needs only a slice of a recorded trial directory:
``result.json``, ``agent/miniswe_trajectory.json``,
``agent/miniswe_report.json``, the single ``agent/gt-state/<state>/events.jsonl``
journal, and every blob that journal references through the keys the audit
verifies (``delivery_blob``, ``request_blob``, ``request_manifest``,
``response_blob``, ``rendered_blob``) plus the ``provider_messages`` CAS
objects those manifests point at. The heavy content stores the audit never
opens - monolithic ``provider_requests/`` in ``message_cas`` runs,
``contract-embeddings.sqlite``, graph stores - stay behind, and anything the
journal references that cannot be copied is reported rather than dropped
silently, because a fixture that cannot prove its own rows is worse than no
fixture at all.

Usage::

    python scripts/extract_replay_fixture.py \\
        --src /path/to/<task>__<trial> --dst tests/fixtures/.../<trial>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

# Blob fields the audit path resolves under the state directory. ``*_blob``
# rows outside this set (transaction_artifacts, raw execution payloads, ...)
# are run evidence, not audit inputs, so they are copied only when they sit
# inside a directory already selected for extraction.
AUDIT_BLOB_KEYS = (
    "delivery_blob",
    "request_blob",
    "request_manifest",
    "response_blob",
    "rendered_blob",
)

# Top-level state files matching these suffixes are graph/index content, not
# journal evidence; the audit derives everything it needs from events.jsonl
# and the referenced blobs.
DENY_FILE_SUFFIXES = (".db", ".db.gz", ".sqlite", ".sqlite3", ".ndb", ".pdb")

# Small whole-directory classes vendored for fidelity when they stay under
# the per-directory cap. They are not audit inputs; they exist so the fixture
# still describes the run's own plan/LSP surface to future readers.
FIDELITY_DIRS = (
    "persistent_plans",
    "plan",
    "plan_checkpoints",
    "verification_plans",
    "lsp_receipts",
    "execution_evidence",
)

AGENT_FILES = ("miniswe_trajectory.json", "miniswe_report.json")


def _dir_size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _copy(src: Path, dst: Path, copied: list[str]) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    copied.append(str(dst))
    return src.stat().st_size


def _journal_references(journal: Path) -> tuple[list[str], list[str]]:
    """Return (audit blob paths, provider_message digests) from the journal."""
    blob_paths: list[str] = []
    message_digests: list[str] = []
    for line in journal.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            continue
        for key in AUDIT_BLOB_KEYS:
            value = row.get(key)
            if isinstance(value, str) and value:
                blob_paths.append(value)
    return blob_paths, message_digests


def extract_fixture(
    src: Path,
    dst: Path,
    *,
    max_file_mb: float = 8.0,
    max_fidelity_dir_mb: float = 2.0,
) -> dict:
    src = src.resolve()
    dst = dst.resolve()
    report: dict = {
        "source": str(src),
        "destination": str(dst),
        "copied_bytes": 0,
        "copied_files": 0,
        "skipped": [],
        "warnings": [],
    }
    copied: list[str] = []

    def _note(message: str) -> None:
        report["warnings"].append(message)

    def _skip(relative: str, reason: str) -> None:
        report["skipped"].append({"path": relative, "reason": reason})

    result_json = src / "result.json"
    if not result_json.is_file():
        raise ValueError(f"{src}: missing result.json (not a trial directory)")
    report["copied_bytes"] += _copy(result_json, dst / "result.json", copied)

    agent_src = src / "agent"
    for name in AGENT_FILES:
        candidate = agent_src / name
        if candidate.is_file():
            report["copied_bytes"] += _copy(
                candidate, dst / "agent" / name, copied
            )
        elif name == "miniswe_trajectory.json":
            raise ValueError(f"{src}: missing agent/{name}")
        else:
            _note(f"agent/{name} absent; audit will score bootstrap calls as 0")

    journals = sorted((agent_src / "gt-state").glob("*/events.jsonl"))
    if len(journals) != 1:
        raise ValueError(
            f"{src}: expected exactly one agent/gt-state/*/events.jsonl, "
            f"found {len(journals)}"
        )
    journal = journals[0]
    state_src = journal.parent
    state_dst = dst / "agent" / "gt-state" / state_src.name

    # Top-level files: everything small that is not a denied content class.
    for path in sorted(state_src.iterdir()):
        if not path.is_file():
            continue
        relative = path.name
        if path.name.endswith(DENY_FILE_SUFFIXES):
            _skip(f"{state_src.name}/{relative}", "denied content class")
            continue
        size = path.stat().st_size
        if size > max_file_mb * 1024 * 1024:
            _skip(f"{state_src.name}/{relative}", f"{size} bytes over file cap")
            continue
        report["copied_bytes"] += _copy(path, state_dst / relative, copied)

    # Fidelity directories, copied whole while small.
    for name in FIDELITY_DIRS:
        directory = state_src / name
        if not directory.is_dir():
            continue
        size = _dir_size(directory)
        if size > max_fidelity_dir_mb * 1024 * 1024:
            _skip(f"{state_src.name}/{name}", f"{size} bytes over dir cap")
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                report["copied_bytes"] += _copy(
                    path, state_dst / path.relative_to(state_src), copied
                )

    # Journal-referenced audit blobs: the authoritative inclusion set. Any
    # referenced file that is missing lands as a warning, since the audit
    # verifies these byte-for-byte and the fixture must not silently lose one.
    blob_paths, _ = _journal_references(journal)
    manifests: list[Path] = []
    for relative in sorted(set(blob_paths)):
        source = state_src / relative
        if not source.is_file():
            _note(f"journal references missing blob: {relative}")
            continue
        report["copied_bytes"] += _copy(
            source, state_dst / relative, copied
        )
        if relative.startswith("provider_request_manifests/"):
            manifests.append(source)

    # Provider message CAS objects referenced by the copied manifests.
    for manifest in manifests:
        try:
            body = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            _note(f"unparseable manifest: {manifest.name}")
            continue
        for reference in body.get("messages") or []:
            digest = str((reference or {}).get("sha256") or "")
            if not digest:
                continue
            message_src = state_src / "provider_messages" / f"{digest}.json"
            message_dst = state_dst / "provider_messages" / f"{digest}.json"
            if message_dst.is_file():
                continue
            if not message_src.is_file():
                _note(f"manifest {manifest.name}: missing message {digest}")
                continue
            report["copied_bytes"] += _copy(message_src, message_dst, copied)

    report["copied_files"] = len(copied)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", required=True, type=Path,
                        help="recorded trial directory (contains result.json + agent/)")
    parser.add_argument("--dst", required=True, type=Path,
                        help="fixture destination directory")
    parser.add_argument("--max-file-mb", type=float, default=8.0)
    parser.add_argument("--max-fidelity-dir-mb", type=float, default=2.0)
    args = parser.parse_args(argv)
    report = extract_fixture(
        args.src, args.dst,
        max_file_mb=args.max_file_mb,
        max_fidelity_dir_mb=args.max_fidelity_dir_mb,
    )
    vendored = sorted((args.dst / "agent" / "gt-state").glob("*/events.jsonl"))
    report["sha256_events"] = (
        hashlib.sha256(vendored[0].read_bytes()).hexdigest()
        if vendored else ""
    )
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
