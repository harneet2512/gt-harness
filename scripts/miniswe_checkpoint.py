"""Persist available work independently of the model/agent process.

A checkpoint is an integrity-bound patch captured during execution, never a
verification result, a trusted-writer attestation, or a claim that later edits
survived abrupt container loss. The agent and checkpoint helper run as the same
OS identity, so these receipts establish byte integrity and attempt identity
only. The official verifier must still consume the exact exported patch.
Full-container-loss durability remains an installed-runtime proof gate.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

from gt_harness.canonical_io import atomic_json, atomic_write

CHECKPOINT_SCHEMA = "gt.recovery_checkpoint.v2"
INTEGRITY_SCOPE = "agent_candidate_patch_bytes_only"


def workspace_identity(workspace: str | Path) -> str:
    resolved = str(Path(workspace).resolve())
    return hashlib.sha256(resolved.encode("utf-8", "surrogatepass")).hexdigest()


def _request_identity(request: dict) -> tuple[str, str]:
    run_nonce = str(request.get("run_nonce") or "")
    workspace_sha256 = str(request.get("workspace_sha256") or "")
    if (len(run_nonce) != 32 or any(c not in "0123456789abcdef" for c in run_nonce)
            or workspace_sha256 != workspace_identity(request["workspace"])):
        raise ValueError("checkpoint_request_identity_invalid")
    directory = Path(request["directory"]).resolve()
    if directory.name != run_nonce:
        raise ValueError("checkpoint_attempt_namespace_invalid")
    return run_nonce, workspace_sha256


def capture_checkpoint(request: dict) -> dict:
    from scripts.miniswe_supervisor import export_patch

    run_nonce, workspace_sha256 = _request_identity(request)
    directory = Path(request["directory"]).resolve()
    staging = directory / "capture.patch"
    export_patch(Path(request["workspace"]), request["baseline"], staging,
                 excluded_roots=tuple(Path(path) for path in request["excluded_roots"]))
    payload = staging.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    blob = directory / "patches" / digest
    if not blob.exists():
        atomic_write(blob, payload)
    elif blob.read_bytes() != payload:
        raise ValueError("checkpoint_blob_corrupt")
    receipt = {
        "schema": CHECKPOINT_SCHEMA, "baseline": request["baseline"],
        "run_nonce": run_nonce, "workspace_sha256": workspace_sha256,
        "patch_sha256": digest, "patch_bytes": len(payload),
        "captured_at_unix_ns": time.time_ns(), "capture_consistency": "live_worktree",
        "integrity_scope": INTEGRITY_SCOPE,
        "verified": False, "code_current": False, "official_score": False,
        "terminal": False,
        "synthetic_transport": bool(request.get("synthetic_transport", False)),
    }
    # Blob publication/fsync precedes the pointer. Interrupted publication leaves
    # the previous pointer intact, and does not overwrite its immutable blob.
    atomic_json(directory / "latest.json", receipt)
    return receipt


def read_checkpoint(
    directory: Path,
    baseline: str,
    *,
    run_nonce: str,
    workspace_sha256: str,
) -> tuple[dict, bytes]:
    """Read one attempt's integrity-bound, unverified candidate patch."""
    directory = directory.resolve()
    receipt = json.loads((directory / "latest.json").read_bytes())
    digest = receipt.get("patch_sha256", "")
    if (receipt.get("schema") != CHECKPOINT_SCHEMA
            or receipt.get("baseline") != baseline
            or receipt.get("run_nonce") != run_nonce
            or directory.name != run_nonce
            or receipt.get("workspace_sha256") != workspace_sha256
            or receipt.get("integrity_scope") != INTEGRITY_SCOPE
            or receipt.get("verified") is not False
            or receipt.get("code_current") is not False
            or receipt.get("official_score") is not False
            or receipt.get("terminal") is not False
            or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
        raise ValueError("checkpoint_identity_invalid")
    payload = (directory / "patches" / digest).read_bytes()
    if (hashlib.sha256(payload).hexdigest() != digest
            or len(payload) != receipt.get("patch_bytes")):
        raise ValueError("checkpoint_integrity_invalid")
    return receipt, payload


def main() -> int:
    request = json.loads(Path(sys.argv[1]).read_bytes())
    _request_identity(request)
    deadline = float(request["deadline"])
    interval = float(request.get("interval_seconds", 5))
    if not 0.1 <= interval <= 60:
        raise ValueError("invalid_checkpoint_interval")
    while time.monotonic() < deadline:
        try:
            capture_checkpoint(request)
        except Exception as exc:
            # Keep the last durable checkpoint, recording failure independently.
            atomic_json(Path(request["directory"]) / "capture_error.json", {
                "schema": "gt.checkpoint_error.v1", "error_type": type(exc).__name__,
                "observed_at_unix_ns": time.time_ns(),
            })
        time.sleep(max(0, min(interval, deadline - time.monotonic())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
