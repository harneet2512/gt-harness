"""Neutral Mini-SWE request receipts and per-run reproducibility manifests.

This module intentionally imports no GroundTruth package. The same observer is
installed in GT-off and GT-on arms so research instrumentation is symmetric.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import MethodType
from typing import Any

_SENSITIVE_FRAGMENTS = ("api_key", "apikey", "authorization", "password", "secret", "token")
_PROVIDER_PREFIXES = {
    "openai", "anthropic", "azure", "vertex_ai", "bedrock", "deepseek",
    "together_ai", "groq", "mistral",
}


class ResearchModelMismatch(RuntimeError):
    """Provider-reported model is not an accepted requested/resolved alias."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _normalize_model(model: str) -> str:
    value = (model or "").strip().lower()
    if "/" in value:
        prefix, remainder = value.split("/", 1)
        if prefix in _PROVIDER_PREFIXES:
            return remainder
    return value


def _sanitize(value: Any, *, key: str = "") -> Any:
    lowered = key.lower()
    if any(fragment in lowered for fragment in _SENSITIVE_FRAGMENTS):
        return {"redacted": True, "sha256": _sha(str(value).encode("utf-8"))}
    if isinstance(value, Mapping):
        return {str(k): _sanitize(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class RunReceiptObserver:
    """Pass-through receipt seam installed identically in both A/B arms."""

    def __init__(
        self,
        root: str | Path,
        *,
        requested_model: str,
        resolved_model: str,
        fallback_model: str = "",
    ):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "provider_events.jsonl"
        self.requests_dir = self.root / "provider_requests"
        self.requests_dir.mkdir(exist_ok=True)
        self.requested_model = requested_model
        self.resolved_model = resolved_model
        self.fallback_model = fallback_model
        self.provider_reported_model = ""
        self.model_mismatch = False
        self.request_count = 0
        self._latest_request_id = ""
        self._request_started: dict[str, float] = {}
        self._installed_model: Any | None = None
        self._original_prepare: Any | None = None
        self._original_query: Any | None = None

    def _append(self, event: str, **payload: Any) -> None:
        row = {
            "schema": "gt.provider-receipt.v1",
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "event": event,
            **payload,
        }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(row).decode("utf-8") + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _latency_ms(self, request_id: str) -> float:
        started = self._request_started.get(request_id)
        return round(1000 * (time.monotonic() - started), 3) if started else 0.0

    def _record_request(self, model: Any, prepared: list[dict]) -> None:
        self.request_count += 1
        logical = {
            "model": self.resolved_model,
            "model_kwargs": getattr(model, "model_kwargs", {}) or {},
            "tools": getattr(model, "tools", None),
            "messages": prepared,
        }
        exact_bytes = _canonical(logical)
        exact_hash = _sha(exact_bytes)
        messages_hash = _sha(_canonical(prepared))
        stored_bytes = _canonical(_sanitize(logical))
        blob_hash = _sha(stored_bytes)
        blob = self.requests_dir / f"{blob_hash}.json"
        if blob.exists():
            if _sha(blob.read_bytes()) != blob_hash:
                raise RuntimeError(f"content-address collision at {blob}")
        else:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.requests_dir,
                prefix=f".{blob_hash}.",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(stored_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.replace(temporary, blob)
            finally:
                if temporary.exists():
                    temporary.unlink()
        request_id = f"request-{self.request_count}-{exact_hash[:16]}"
        self._latest_request_id = request_id
        self._request_started[request_id] = time.monotonic()
        self._append(
            "provider_request",
            request_id=request_id,
            request_id_kind="local_correlation",
            request_sha256=exact_hash,
            messages_sha256=messages_hash,
            request_blob=f"provider_requests/{blob.name}",
            request_blob_sha256=blob_hash,
            requested_model=self.requested_model,
            resolved_model=self.resolved_model,
            fallback_model=self.fallback_model,
        )

    def _record_response(self, message: Mapping[str, Any]) -> None:
        extra = message.get("extra") or {}
        response = extra.get("response") if isinstance(extra, Mapping) else None
        reported = str(response.get("model") or "") if isinstance(response, Mapping) else ""
        self.provider_reported_model = reported
        expected = {
            _normalize_model(item)
            for item in (self.requested_model, self.resolved_model, self.fallback_model)
            if item
        }
        mismatch = bool(reported and expected and _normalize_model(reported) not in expected)
        self.model_mismatch = mismatch
        self._append(
            "provider_response",
            request_id=self._latest_request_id,
            request_id_kind="local_correlation",
            provider_response_id=(
                str(response.get("id") or "") if isinstance(response, Mapping) else ""
            ),
            response_sha256=_sha(_canonical(response)) if response is not None else "",
            provider_reported_model=reported,
            model_mismatch=mismatch,
            usage=dict(response.get("usage") or {}) if isinstance(response, Mapping) else {},
            latency_ms=self._latency_ms(self._latest_request_id),
        )
        if mismatch:
            raise ResearchModelMismatch(
                "provider model mismatch: requested="
                f"{self.requested_model!r}, resolved={self.resolved_model!r}, "
                f"reported={reported!r}"
            )

    def install(self, model: Any) -> None:
        if getattr(model, "_research_receipt_observer", None) is not None:
            return
        prepare = getattr(model, "_prepare_messages_for_api", None)
        query = getattr(model, "query", None)
        if not callable(prepare) or not callable(query):
            raise TypeError("model lacks request preparation/query seams")
        self._installed_model = model
        self._original_prepare = prepare
        self._original_query = query

        def prepare_messages(_model: Any, messages: list[dict]) -> list[dict]:
            prepared = prepare(messages)
            self._record_request(_model, prepared)
            return prepared

        def query_messages(_model: Any, messages: list[dict], **kwargs: Any) -> dict:
            try:
                result = query(messages, **kwargs)
            except Exception as exc:
                self._append(
                    "provider_failure",
                    request_id=self._latest_request_id,
                    request_id_kind="local_correlation",
                    error_type=type(exc).__name__,
                    error=str(exc)[:500],
                    latency_ms=self._latency_ms(self._latest_request_id),
                )
                raise
            self._record_response(result)
            return result

        model._prepare_messages_for_api = MethodType(prepare_messages, model)
        model.query = MethodType(query_messages, model)
        model._research_receipt_observer = self

    def receipt(self) -> dict[str, Any]:
        payload = self.events_path.read_bytes() if self.events_path.exists() else b""
        issues: list[str] = []
        requests: dict[str, dict[str, Any]] = {}
        terminals: set[str] = set()
        try:
            rows = [
                json.loads(line)
                for line in payload.decode("utf-8").splitlines()
                if line.strip()
            ]
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            rows = []
            issues.append(f"provider receipt log is not valid JSONL: {exc}")
        for row in rows:
            request_id = str(row.get("request_id") or "")
            event = row.get("event")
            if event == "provider_request":
                if not request_id or request_id in requests:
                    issues.append(f"invalid or duplicate provider request id: {request_id!r}")
                    continue
                requests[request_id] = row
                relative = str(row.get("request_blob") or "")
                blob = (self.root / relative).resolve()
                if not relative or self.root.resolve() not in blob.parents:
                    issues.append(f"provider request blob escapes receipt root: {request_id}")
                elif not blob.is_file():
                    issues.append(f"provider request blob missing: {request_id}")
                elif _sha(blob.read_bytes()) != str(row.get("request_blob_sha256") or ""):
                    issues.append(f"provider request blob hash mismatch: {request_id}")
            elif event in {"provider_response", "provider_failure"}:
                if not request_id:
                    issues.append(f"{event} lacks request id")
                elif request_id in terminals:
                    issues.append(f"multiple provider terminals: {request_id}")
                terminals.add(request_id)
        if len(requests) != self.request_count:
            issues.append(
                "provider request count mismatch: "
                f"expected {self.request_count}, got {len(requests)}"
            )
        for request_id in requests:
            if request_id not in terminals:
                issues.append(f"provider request lacks terminal receipt: {request_id}")
        for request_id in terminals:
            if request_id not in requests:
                issues.append(f"provider terminal lacks request receipt: {request_id}")
        return {
            "request_count": self.request_count,
            "events_sha256": _sha(payload),
            "events_path": self.events_path.name,
            "provider_reported_model": self.provider_reported_model,
            "model_mismatch": self.model_mismatch,
            "valid": not issues,
            "issues": issues,
        }


def _file_receipt(path: str | Path) -> dict[str, Any]:
    item = Path(path)
    if not item.is_file():
        return {"path": str(item), "present": False, "sha256": "", "bytes": 0}
    payload = item.read_bytes()
    return {
        "path": str(item.resolve()),
        "present": True,
        "sha256": _sha(payload),
        "bytes": len(payload),
    }


def _git_identity(cwd: str) -> dict[str, str]:
    def run(*args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", cwd, *args], capture_output=True, text=True, timeout=10
            )
        except Exception:
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""

    return {
        "head": run("rev-parse", "HEAD"),
        "tree": run("rev-parse", "HEAD^{tree}"),
    }


def _installed_packages() -> list[str]:
    packages = {
        f"{dist.metadata.get('Name', 'unknown')}=={dist.version}"
        for dist in importlib.metadata.distributions()
    }
    return sorted(packages, key=str.lower)


def build_reproducibility_manifest(
    *,
    task: str,
    requested_model: str,
    resolved_model: str,
    provider_reported_model: str,
    fallback_model: str,
    temperature: float,
    cwd: str,
    step_limit: int,
    timeout: int,
    gt_mode: str,
    event_journal: Mapping[str, Any] | None,
    request_receipt: Mapping[str, Any],
    binary_paths: list[str] | tuple[str, ...] = (),
    source_paths: list[str] | tuple[str, ...] = (),
    engine_integrity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        miniswe_version = importlib.metadata.version("mini-swe-agent")
    except importlib.metadata.PackageNotFoundError:
        miniswe_version = "unavailable"
    workspace = _git_identity(cwd)
    installed_packages = _installed_packages()
    comparison_core = {
        "task_sha256": _sha(task.encode("utf-8")),
        "requested_model": requested_model,
        "resolved_model": resolved_model,
        "temperature": temperature,
        "step_limit": step_limit,
        "timeout": timeout,
        "python": platform.python_version(),
        "mini_swe_agent": miniswe_version,
        "installed_packages": installed_packages,
        "workspace_head": workspace["head"],
        "workspace_tree": workspace["tree"],
    }
    normalized_reported = _normalize_model(provider_reported_model)
    normalized_expected = {
        _normalize_model(item)
        for item in (requested_model, resolved_model, fallback_model)
        if item
    }
    model_match = bool(
        normalized_reported and normalized_reported in normalized_expected
    )
    provider_receipts_valid = bool(request_receipt.get("valid", False))
    event_journal_valid = bool(
        not event_journal or event_journal.get("valid", False)
    )
    endpoint = os.environ.get("OPENAI_BASE_URL", "")
    engine_valid = gt_mode == "off" or (
        isinstance(engine_integrity, Mapping)
        and engine_integrity.get("schema") == "gt.engine_integrity.v1"
        and engine_integrity.get("valid") is True
        and engine_integrity.get("mode") == gt_mode
        and engine_integrity.get("issues") == []
        and engine_integrity.get("disabled_stage") == ""
    )
    return {
        "schema": "gt.repro.v1",
        "created_utc": datetime.now(UTC).isoformat(),
        "task_sha256": comparison_core["task_sha256"],
        "gt_mode": gt_mode,
        "model": {
            "requested": requested_model,
            "resolved": resolved_model,
            "provider_reported": provider_reported_model,
            "fallback": fallback_model,
            "match": model_match,
        },
        "request_config": {
            "temperature": temperature,
            "step_limit": step_limit,
            "command_timeout_seconds": timeout,
        },
        "workspace": {"cwd": str(Path(cwd).resolve()), **workspace},
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "mini_swe_agent": miniswe_version,
            "packages": installed_packages,
        },
        "provider_environment": {
            "endpoint_sha256": _sha(endpoint.encode("utf-8")) if endpoint else "",
            "credential_present": bool(
                os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
            ),
        },
        "event_journal": dict(event_journal or {}),
        "engine_integrity": dict(engine_integrity or {}),
        "provider_receipts": dict(request_receipt),
        "binaries": [_file_receipt(path) for path in binary_paths],
        "runner_sources": [_file_receipt(path) for path in source_paths],
        "comparison_fingerprint": _sha(_canonical(comparison_core)),
        "research_valid": bool(
            model_match
            and not bool(request_receipt.get("model_mismatch"))
            and provider_receipts_valid
            and event_journal_valid
            and engine_valid
        ),
    }


def write_reproducibility_manifest(
    path: str | Path, manifest: Mapping[str, Any]
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical(dict(manifest))
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=target.parent, prefix=f".{target.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target
