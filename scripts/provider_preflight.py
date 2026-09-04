"""Validate and probe the single versioned provider route without leaking account data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_SCHEMA = "gt.provider_route.v1"
_SHA40 = re.compile(r"[0-9a-f]{40}")
_ALLOWED_KEYS = {
    "schema",
    "route_id",
    "provider",
    "base_url",
    "model",
    "requested_output_tokens",
    "credential_env",
    "credential_source_id",
    "availability",
}


class ProviderPreflightError(RuntimeError):
    """A closed provider failure carrying only non-sensitive progress metadata."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.checks: dict[str, bool] | None = None
        self.provider_inference_attempts = 0
        self.context_window_tokens: int | None = None
        self.context_window_source: str | None = None


def load_route(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    route = json.loads(raw)
    if not isinstance(route, dict) or set(route) != _ALLOWED_KEYS:
        raise ValueError("provider_route_shape_invalid")
    if route["schema"] != _SCHEMA or route["provider"] != "openrouter":
        raise ValueError("provider_route_identity_invalid")
    if route["base_url"] != "https://openrouter.ai/api/v1":
        raise ValueError("provider_base_url_not_allowed")
    if route["model"] != "meta/muse-spark-1.2-contributor":
        raise ValueError("provider_model_not_allowed")
    if route["credential_env"] != "OPENROUTER_API_KEY":
        raise ValueError("provider_credential_env_not_allowed")
    requested_output = route["requested_output_tokens"]
    if (
        isinstance(requested_output, bool)
        or not isinstance(requested_output, int)
        or requested_output < 1
    ):
        raise ValueError("provider_output_reservation_invalid")
    availability = route["availability"]
    if availability != {
        "key_path": "/key",
        "models_path": "/models",
        "inference_path": "/chat/completions",
        "require_positive_key_limit_when_present": True,
    }:
        raise ValueError("provider_availability_contract_invalid")
    return route, hashlib.sha256(raw).hexdigest()


def _get_json(url: str, api_key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        code = {
            401: "provider_credential_rejected",
            402: "provider_billing_failure",
            429: "provider_rate_limited",
        }.get(exc.code, "provider_preflight_http_failed")
        if code == "provider_preflight_http_failed":
            code = f"provider_preflight_http_{exc.code}"
        raise ProviderPreflightError(code) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProviderPreflightError("provider_preflight_transport_failed") from exc
    if not isinstance(payload, dict):
        raise ProviderPreflightError("provider_preflight_response_invalid")
    return payload


def _post_json(url: str, api_key: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, separators=(",", ":")).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        code = {
            401: "provider_credential_rejected",
            402: "provider_billing_failure",
            429: "provider_rate_limited",
        }.get(exc.code, "provider_canary_http_failed")
        if code == "provider_canary_http_failed":
            code = f"provider_canary_http_{exc.code}"
        raise ProviderPreflightError(code) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProviderPreflightError("provider_canary_transport_failed") from exc
    if not isinstance(payload, dict):
        raise ProviderPreflightError("provider_canary_response_invalid")
    return payload


def probe(route: dict[str, Any], api_key: str) -> tuple[dict[str, bool], int, str]:
    checks = {
        "credential_valid": False,
        "key_limit_available": False,
        "model_visible": False,
        "model_canary_served": False,
    }
    if not api_key:
        raise ProviderPreflightError("provider_credential_missing")
    base = str(route["base_url"]).rstrip("/")
    availability = route["availability"]
    attempts = 0
    context_window: int | None = None
    context_source: str | None = None
    try:
        key_payload = _get_json(base + availability["key_path"], api_key)
        key_data = key_payload.get("data")
        if not isinstance(key_data, dict):
            raise ProviderPreflightError("provider_key_status_invalid")
        checks["credential_valid"] = True
        remaining = key_data.get("limit_remaining")
        if remaining is not None and (
            isinstance(remaining, bool)
            or not isinstance(remaining, (int, float))
            or remaining <= 0
        ):
            raise ProviderPreflightError("provider_key_cannot_fund_run")
        checks["key_limit_available"] = True
        models_payload = _get_json(base + availability["models_path"], api_key)
        models = models_payload.get("data")
        matching = [
            row
            for row in models if isinstance(row, dict) and row.get("id") == route["model"]
        ] if isinstance(models, list) else []
        if len(matching) != 1:
            raise ProviderPreflightError("provider_model_unavailable")
        checks["model_visible"] = True
        model_row = matching[0]
        candidate_window = model_row.get("context_length")
        top_provider = model_row.get("top_provider")
        top_provider = top_provider if isinstance(top_provider, dict) else {}
        max_completion = top_provider.get("max_completion_tokens")
        requested_output = int(route["requested_output_tokens"])
        if (
            isinstance(candidate_window, bool)
            or not isinstance(candidate_window, int)
            or candidate_window <= requested_output
        ):
            raise ProviderPreflightError("provider_context_window_unavailable")
        if max_completion is not None and (
            isinstance(max_completion, bool)
            or not isinstance(max_completion, int)
            or max_completion < requested_output
        ):
            raise ProviderPreflightError("provider_output_reservation_unsupported")
        context_window = candidate_window
        context_source = "openrouter:/models"
        attempts = 1
        canary = _post_json(
            base + availability["inference_path"],
            api_key,
            {
                "model": route["model"],
                "messages": [{"role": "user", "content": "Reply OK."}],
                "max_completion_tokens": 16,
                "temperature": 0,
            },
        )
        if not isinstance(canary.get("choices"), list) or not canary["choices"]:
            raise ProviderPreflightError("provider_canary_response_invalid")
        checks["model_canary_served"] = True
    except ProviderPreflightError as exc:
        exc.checks = dict(checks)
        exc.provider_inference_attempts = attempts
        exc.context_window_tokens = context_window
        exc.context_window_source = context_source
        raise
    assert context_window is not None and context_source is not None
    return checks, context_window, context_source


def run(*, manifest: Path, output: Path, source_sha: str, live: bool) -> dict[str, Any]:
    if not _SHA40.fullmatch(source_sha):
        raise ValueError("provider_preflight_source_sha_invalid")
    route, digest = load_route(manifest)
    error_code = None
    provider_inference_attempts = 0
    context_window_tokens: int | None = None
    context_window_source: str | None = None
    if live:
        try:
            checks, context_window_tokens, context_window_source = probe(
                route, os.environ.get(str(route["credential_env"]), "")
            )
            provider_inference_attempts = 1
        except ProviderPreflightError as exc:
            error_code = str(exc)
            checks = exc.checks or {
                "credential_valid": False,
                "key_limit_available": False,
                "model_visible": False,
                "model_canary_served": False,
            }
            provider_inference_attempts = exc.provider_inference_attempts
            context_window_tokens = exc.context_window_tokens
            context_window_source = exc.context_window_source
    else:
        checks = {
            "credential_valid": False,
            "key_limit_available": False,
            "model_visible": False,
            "model_canary_served": False,
        }
    receipt = {
        "schema": "gt.provider_preflight.v1",
        "status": "FAIL" if error_code else "PASS",
        "error_code": error_code,
        "mode": "live" if live else "provider_free",
        "source_sha": source_sha,
        "route_id": route["route_id"],
        "provider": route["provider"],
        "base_url": route["base_url"],
        "model": route["model"],
        "route_sha256": digest,
        "checks": checks,
        "provider_ready": live and error_code is None,
        "paid_run_approved": live,
        "account_amounts_recorded": False,
        "provider_inference_attempts": provider_inference_attempts,
        "provider_inference_calls": int(live and error_code is None),
        "context_window_tokens": context_window_tokens,
        "reserved_output_tokens": int(route["requested_output_tokens"]),
        "context_window_source": context_window_source,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    receipt = run(
        manifest=args.manifest,
        output=args.output,
        source_sha=args.source_sha,
        live=args.live,
    )
    print(json.dumps(receipt, sort_keys=True))
    return int(receipt["status"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
