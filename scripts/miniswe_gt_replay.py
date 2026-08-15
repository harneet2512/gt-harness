"""Offline integrity replay for a Mini-SWE + GT run artifact bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from gt_engine.event_journal import verify_event_journal


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inside(base: Path, relative: str) -> Path:
    target = (base / relative).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"artifact path escapes bundle: {relative}")
    return target


def audit_replay_bundle(manifest_path: str | Path) -> dict[str, Any]:
    manifest_file = Path(manifest_path).resolve()
    base = manifest_file.parent
    issues: list[str] = []
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"valid": False, "issues": [f"manifest unreadable: {exc}"]}
    if manifest.get("schema") != "gt.repro.v1":
        issues.append("unsupported manifest schema")

    event_count = 0
    journal = manifest.get("event_journal") or {}
    if journal:
        try:
            event_path = _inside(base, str(journal.get("path") or "events.jsonl"))
            verified = verify_event_journal(
                event_path,
                event_count=int(journal.get("event_count") or 0),
                event_head=str(journal.get("event_head") or ""),
            )
            event_count = verified.event_count
            issues.extend(f"event journal: {item}" for item in verified.issues)
        except Exception as exc:
            issues.append(f"event journal unreadable: {exc}")

    provider_request_count = 0
    receipts = manifest.get("provider_receipts") or {}
    if not receipts.get("valid"):
        issues.append("provider receipt manifest marks the log invalid")
    provider_path_text = str(receipts.get("events_path") or "")
    if provider_path_text:
        try:
            provider_path = _inside(base, provider_path_text)
            expected = str(receipts.get("events_sha256") or "")
            if not provider_path.is_file():
                issues.append("provider receipt log missing")
                rows = []
            else:
                if expected and _sha(provider_path) != expected:
                    issues.append("provider receipt log hash mismatch")
                rows = [
                    json.loads(line)
                    for line in provider_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            requests: dict[str, dict] = {}
            terminals: set[str] = set()
            for row in rows:
                event = row.get("event")
                request_id = str(row.get("request_id") or "")
                if event == "provider_request":
                    provider_request_count += 1
                    requests[request_id] = row
                    blob_rel = str(row.get("request_blob") or "")
                    if not blob_rel:
                        issues.append(f"provider request {request_id}: blob missing")
                        continue
                    blob = _inside(base, blob_rel)
                    if not blob.is_file():
                        issues.append(f"provider request {request_id}: blob missing")
                    elif _sha(blob) != str(row.get("request_blob_sha256") or ""):
                        issues.append(
                            f"provider request blob hash mismatch: {request_id}"
                        )
                elif event in {"provider_response", "provider_failure"}:
                    terminals.add(request_id)
            expected_count = int(receipts.get("request_count") or 0)
            if provider_request_count != expected_count:
                issues.append(
                    "provider request count mismatch: "
                    f"expected {expected_count}, got {provider_request_count}"
                )
            for request_id in requests:
                if request_id not in terminals:
                    issues.append(f"provider request lacks terminal receipt: {request_id}")
        except Exception as exc:
            issues.append(f"provider receipts unreadable: {exc}")

    return {
        "schema": "gt.bundle-replay.v1",
        "valid": not issues,
        "manifest_research_valid": bool(manifest.get("research_valid")),
        "event_count": event_count,
        "provider_request_count": provider_request_count,
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    args = parser.parse_args()
    report = audit_replay_bundle(args.manifest)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
