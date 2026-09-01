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
    "credential_env",
    "credential_source_id",
    "availability",
}


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
        raise RuntimeError(code) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("provider_preflight_transport_failed") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("provider_preflight_response_invalid")
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
        raise RuntimeError(code) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("provider_canary_transport_failed") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("provider_canary_response_invalid")
    return payload


def probe(route: dict[str, Any], api_key: str) -> dict[str, bool]:
    if not api_key:
        raise RuntimeError("provider_credential_missing")
    base = str(route["base_url"]).rstrip("/")
    availability = route["availability"]
    key_payload = _get_json(base + availability["key_path"], api_key)
    key_data = key_payload.get("data")
    if not isinstance(key_data, dict):
        raise RuntimeError("provider_key_status_invalid")
    remaining = key_data.get("limit_remaining")
    if remaining is not None and (
        isinstance(remaining, bool)
        or not isinstance(remaining, (int, float))
        or remaining <= 0
    ):
        raise RuntimeError("provider_key_cannot_fund_run")
    models_payload = _get_json(base + availability["models_path"], api_key)
    models = models_payload.get("data")
    if not isinstance(models, list) or route["model"] not in {
        row.get("id") for row in models if isinstance(row, dict)
    }:
        raise RuntimeError("provider_model_unavailable")
    canary = _post_json(
        base + availability["inference_path"],
        api_key,
        {
            "model": route["model"],
            "messages": [{"role": "user", "content": "Reply OK."}],
            "max_tokens": 1,
            "temperature": 0,
        },
    )
    if not isinstance(canary.get("choices"), list) or not canary["choices"]:
        raise RuntimeError("provider_canary_response_invalid")
    return {
        "credential_valid": True,
        "key_limit_available": True,
        "model_visible": True,
        "model_canary_served": True,
    }


def run(*, manifest: Path, output: Path, source_sha: str, live: bool) -> dict[str, Any]:
    if not _SHA40.fullmatch(source_sha):
        raise ValueError("provider_preflight_source_sha_invalid")
    route, digest = load_route(manifest)
    error_code = None
    if live:
        try:
            checks = probe(route, os.environ.get(str(route["credential_env"]), ""))
        except RuntimeError as exc:
            error_code = str(exc)
            checks = {
                "credential_valid": False,
                "key_limit_available": False,
                "model_visible": False,
                "model_canary_served": False,
            }
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
        "provider_inference_calls": int(live and error_code is None),
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
