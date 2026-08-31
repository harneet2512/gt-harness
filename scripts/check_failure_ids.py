"""Fail-closed construction gate for failure-ID evidence.

This is the executable boundary used by ``.githooks/pre-commit``.  It keeps
the older failure-ID scanner as one check, but adds exact lineage, fixture,
manifest, and canonical-trailer checks.  Every refusal is a stable JSON
object so callers can persist and compare it byte-for-byte.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# Direct hook execution places ``scripts/`` on sys.path instead of the
# repository root.  Resolve the package from the checked-out tree explicitly;
# otherwise the production hook can fail before it evaluates any evidence.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from scripts import validate_failure_ids  # noqa: E402

RESULT_SCHEMA = "gt.failure_gate.result.v1"
MANIFEST_SCHEMA = "gt.failure_gate.manifest.v1"
SNAPSHOT = "config/failure_id_ledger_snapshot.json"
SNAPSHOT_REVISION = (
    "sha256:86fe88f99aef31db9214a9c82364936fb1523a465043aee0c3b325fa823c6eb6"
)
SNAPSHOT_PAYLOAD = "3aefe224c70975266cdc777d231bc4ecb8b3fa38e98d7921d4ffcb5f6a2c7592"
EXPECTED_REMOTE = "https://github.com/harneet2512/gt-harness.git"
EXPECTED_BRANCH = "main"

CANONICAL_TRAILER_LINES = (
    "GT-Spec-Comment: 90bb9618-dbd7-4a0c-bb5d-cfc87b1c043f",
    "GT-Spec-Body-SHA256: c68668f16a34d8da98bc34ee6de3ae21f73a9caf74e318b083ddb83e1393042c",
    "GT-Spec-Comment: dd508394-ba1d-4bee-9036-c0ffb5c466b6",
    "GT-Spec-Body-SHA256: 6054c1cfdf4d51ebc00a49bf4a61507051435f339dfcf17d19a91532fb730b45",
    "GT-Spec-Comment: 22b3aefe-e9c8-43fe-b218-12916babafaf",
    "GT-Spec-Body-SHA256: 13d8fe1bb56ef8546f54069ba4734fc6a9e7af83de618b71de0a83805b380669",
    "GT-Spec-Comment: e1387116-6022-413c-8828-5ce3bf5b589f",
    "GT-Spec-Body-SHA256: 6268eb0fc15a77681b52a928303205b3a5fc0d128f99da54169b46ec312ccb82",
    "GT-Spec-Comment: 242b64fd-ba03-453c-85d0-71dab16da56f",
    "GT-Spec-Body-SHA256: 95edf2d49011627099de33d1b2d7a849b720cf0365a573ba1e794e170bc808de",
    "GT-Spec-Comment: 6f34c102-0752-49de-be76-6eefbdfec8d9",
    "GT-Spec-Body-SHA256: 2503fe53e0d9b27e046e6490e2403252853802ee7acde4e54a7a5ba80eec7149",
    "GT-Spec-Comment: 469326e1-bc62-4909-b691-c8c77c3f78f0",
    "GT-Spec-Body-SHA256: caf35990a6d811f2b60b62a9c5748b2da3d3bad8ddf4d996a893052b45ff5bcf",
    "GT-Spec-Comment: 565b41de-388a-45f3-bfad-7d4bb359aaf2",
    "GT-Spec-Body-SHA256: d35d86e60116e488222521c6b8c4caf772e2e155d4e24656f0eb19955b827a53",
    "GT-Spec-Comment: fcf83ad9-699e-41f3-b524-71a801252f13",
    "GT-Spec-Body-SHA256: 833f8f06655009e75b42b231d9a4844078668f50945bae68e45a6321dbb5023e",
    "GT-Spec-Comment: 9e335bab-1ab7-4434-98fa-b0134ed2da11",
    "GT-Spec-Body-SHA256: 2f7dff3a6f1d4bb110ba4bd2aa2d427b9506d5b8db34f26505a46f3f4696e05d",
    "GT-Spec-Comment: bcda74e9-a30e-4732-9f8b-4b17861d8a93",
    "GT-Spec-Body-SHA256: 5ccd71c564b25467627166e0578abc20c315fb823bcf37d210a5821cefacc38b",
    "GT-Spec-Comment: c9867652-8403-4a8d-aef4-00aa5e3a7b95",
    "GT-Spec-Body-SHA256: 4193ab1da4dafc826bfd21d6da91b173398a1ec7e0715883986ce0f91ba0258a",
    "GT-Spec-Comment: f21537c8-5bce-402f-8bc2-03458dad80eb",
    "GT-Spec-Body-SHA256: b985579a65decf133a0b59b76992d94901464f5b7c70aa03cd5349562acb4fd3",
    "GT-Spec-Comment: 5f001124-8628-4966-b9ad-87754088bf25",
    "GT-Spec-Body-SHA256: d1ab08cd594bdd3ea42d5532de4efbfeeafd39f83dd9b09ea58525293c832cb2",
    "GT-Spec-Comment: bfcb26a5-acf8-4590-a1af-2b00da1b6d4c",
    "GT-Spec-Body-SHA256: 13efe658a54de310b6846b0ffec9f6ca30bb5a4d76599a72893edb3254b54f53",
)

EXPECTED_REFUSALS = {
    "manifest_missing": "MANIFEST_MISSING",
    "manifest_invalid": "MANIFEST_INVALID",
    "lineage_mismatch": "LINEAGE_MISMATCH",
    "fixture_mismatch": "FIXTURE_MISMATCH",
    "trailer_invalid": "TRAILER_BLOCK_INVALID",
    "failure_ids_invalid": "FAILURE_IDS_INVALID",
}


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _is_commit_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(
        character in "0123456789abcdef" for character in value
    )


def _refusal(code: str, detail: str) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "status": "REFUSED",
        "code": code,
        "detail": detail,
    }


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _load_manifest(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not path.is_file():
        return None, _refusal("MANIFEST_MISSING", "manifest_missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, _refusal("MANIFEST_INVALID", "manifest_unreadable")
    if not isinstance(payload, dict):
        return None, _refusal("MANIFEST_INVALID", "manifest_not_object")
    if payload.get("schema") != MANIFEST_SCHEMA:
        return None, _refusal("MANIFEST_INVALID", "manifest_schema")
    if payload.get("repository") != "gt-harness":
        return None, _refusal("MANIFEST_INVALID", "manifest_repository")
    required = {
        "lineage",
        "fixture_hashes",
        "expected_refusals",
        "trailer_lines",
        "repository_locator",
        "spec_bindings",
        "required_evidence",
    }
    if not required <= set(payload):
        return None, _refusal("MANIFEST_INVALID", "manifest_required_field")
    if payload["expected_refusals"] != EXPECTED_REFUSALS:
        return None, _refusal("MANIFEST_INVALID", "manifest_refusal_map")
    if tuple(payload["trailer_lines"]) != CANONICAL_TRAILER_LINES:
        return None, _refusal("MANIFEST_INVALID", "manifest_trailer_block")
    locator = payload["repository_locator"]
    if not isinstance(locator, dict):
        return None, _refusal("MANIFEST_INVALID", "manifest_repository_locator")
    if (
        locator.get("remote") != EXPECTED_REMOTE
        or locator.get("default_branch") != EXPECTED_BRANCH
    ):
        return None, _refusal("MANIFEST_INVALID", "manifest_repository_locator")
    bindings = payload["spec_bindings"]
    expected_bindings = [
        {
            "comment": comment.removeprefix("GT-Spec-Comment: "),
            "body_sha256": body.removeprefix("GT-Spec-Body-SHA256: "),
        }
        for comment, body in zip(
            CANONICAL_TRAILER_LINES[::2], CANONICAL_TRAILER_LINES[1::2], strict=True
        )
    ]
    if bindings != expected_bindings:
        return None, _refusal("MANIFEST_INVALID", "manifest_spec_bindings")
    evidence = payload["required_evidence"]
    expected_evidence = {
        "failure_scan": {
            "expected_next": "FD-031",
            "ledger_snapshot": SNAPSHOT,
            "ledger_revision": SNAPSHOT_REVISION,
            "ledger_payload_sha256": SNAPSHOT_PAYLOAD,
        }
    }
    if evidence != expected_evidence:
        return None, _refusal("MANIFEST_INVALID", "manifest_required_evidence")
    lineage = payload["lineage"]
    if not isinstance(lineage, dict):
        return None, _refusal("MANIFEST_INVALID", "manifest_lineage")
    for key in ("head_sha", "parent_sha", "source_revision"):
        if not isinstance(lineage.get(key), str) or not _is_commit_sha(lineage[key]):
            return None, _refusal("MANIFEST_INVALID", f"manifest_lineage_{key}")
    fixtures = payload["fixture_hashes"]
    if not isinstance(fixtures, dict) or not fixtures:
        return None, _refusal("MANIFEST_INVALID", "manifest_fixtures")
    for path, digest in fixtures.items():
        if not isinstance(path, str) or Path(path).is_absolute() or ".." in Path(path).parts:
            return None, _refusal("MANIFEST_INVALID", "manifest_fixture_path")
        if not isinstance(digest, str) or not _is_digest(digest):
            return None, _refusal("MANIFEST_INVALID", "manifest_fixture_hash")
    return payload, None


def _check_lineage(
    root: Path,
    lineage: dict[str, str],
    locator: dict[str, str],
    commit: str,
) -> dict[str, Any] | None:
    head = _git(root, "rev-parse", "HEAD")
    selected = _git(root, "rev-parse", commit)
    if head != lineage["head_sha"] or selected != head or lineage["source_revision"] != head:
        return _refusal("LINEAGE_MISMATCH", "head_or_source_revision")
    remote = _git(root, "remote", "get-url", "origin")
    if remote != locator["remote"]:
        return _refusal("LINEAGE_MISMATCH", "origin_repository")
    parents = _git(root, "rev-list", "--parents", "-n", "1", head)
    if not parents or lineage["parent_sha"] not in parents.split()[1:]:
        return _refusal("LINEAGE_MISMATCH", "immediate_parent")
    try:
        ancestry = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", lineage["parent_sha"], head],
            check=False, capture_output=True, text=True, encoding="utf-8",
        )
    except OSError:
        ancestry = None
    if ancestry is None or ancestry.returncode != 0:
        return _refusal("LINEAGE_MISMATCH", "ancestor_check")
    return None


def _check_fixtures(root: Path, fixture_hashes: dict[str, str]) -> dict[str, Any] | None:
    for relative, expected in sorted(fixture_hashes.items()):
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return _refusal("FIXTURE_MISMATCH", relative)
        try:
            actual = _sha256(path)
        except OSError:
            return _refusal("FIXTURE_MISMATCH", relative)
        if actual != expected:
            return _refusal("FIXTURE_MISMATCH", relative)
    return None


def _check_trailers(message: str) -> dict[str, Any] | None:
    lines = message.rstrip("\r\n").splitlines()
    if len(lines) < len(CANONICAL_TRAILER_LINES):
        return _refusal("TRAILER_BLOCK_INVALID", "trailer_block_missing")
    if tuple(lines[-len(CANONICAL_TRAILER_LINES) :]) != CANONICAL_TRAILER_LINES:
        return _refusal("TRAILER_BLOCK_INVALID", "trailer_block_not_final_or_mutated")
    return None


def evaluate(
    root: str | Path,
    *,
    manifest: str | Path,
    commit: str = "HEAD",
) -> dict[str, Any]:
    repo = Path(root).resolve()
    manifest_path = Path(manifest).resolve()
    expected_manifest = (repo / ".githooks" / "failure-gate.json").resolve()
    if manifest_path != expected_manifest:
        return _refusal("MANIFEST_INVALID", "manifest_path")
    payload, error = _load_manifest(manifest_path)
    if error:
        return error
    lineage = payload["lineage"]
    error = _check_lineage(repo, lineage, payload["repository_locator"], commit)
    if error:
        return error
    error = _check_fixtures(repo, payload["fixture_hashes"])
    if error:
        return error
    message = _git(repo, "show", "-s", "--format=%B", commit)
    if message is None:
        return _refusal("LINEAGE_MISMATCH", "commit_unreadable")
    error = _check_trailers(message)
    if error:
        return error
    evidence = payload["required_evidence"]["failure_scan"]
    report = validate_failure_ids.validate(
        [repo],
        expected_next=evidence["expected_next"],
        ledger_snapshot=repo / evidence["ledger_snapshot"],
        expected_ledger_revision=evidence["ledger_revision"],
        expected_ledger_payload_sha256=evidence["ledger_payload_sha256"],
    )
    if report["status"] != "pass":
        return _refusal("FAILURE_IDS_INVALID", "repository_scan")
    return {
        "schema": RESULT_SCHEMA,
        "status": "PASS",
        "code": "ACCEPTED",
        "head_sha": lineage["head_sha"],
        "parent_sha": lineage["parent_sha"],
        "source_revision": lineage["source_revision"],
        "fixture_hashes": dict(sorted(payload["fixture_hashes"].items())),
        "trailer_lines": len(CANONICAL_TRAILER_LINES),
        "spec_bindings": payload["spec_bindings"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the fail-closed GT construction gate.")
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--manifest", default=".githooks/failure-gate.json")
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--receipt")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = evaluate(args.root, manifest=args.manifest, commit=args.commit)
    output = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    print(output, end="")
    if args.receipt:
        target = Path(args.receipt).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(output)
            handle.flush()
        try:
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
