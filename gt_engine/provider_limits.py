"""Provider transport admission limits shared by Mini-SWE runtime seams."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

PROVIDER_REQUEST_MAX_BYTES = 6 * 1024 * 1024


class ProviderRequestTooLarge(RuntimeError):
    """Deterministic local refusal; this exception must never be retried."""

    code = "GT_PROVIDER_REQUEST_TOO_LARGE"
    retryable = False

    def __init__(self, request_bytes: int, limit_bytes: int = PROVIDER_REQUEST_MAX_BYTES):
        self.request_bytes = int(request_bytes)
        self.limit_bytes = int(limit_bytes)
        super().__init__(
            f"provider request refused locally: {self.request_bytes} bytes exceeds "
            f"{self.limit_bytes} bytes"
        )


def provider_request_bytes(payload: Mapping[str, Any]) -> int:
    """Return the exact canonical UTF-8 envelope size used for admission."""

    return len(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    )


def enforce_provider_request_limit(
    payload: Mapping[str, Any], *, limit_bytes: int = PROVIDER_REQUEST_MAX_BYTES
) -> int:
    """Refuse an oversized complete request before any transport is invoked."""

    size = provider_request_bytes(payload)
    if size > int(limit_bytes):
        raise ProviderRequestTooLarge(size, int(limit_bytes))
    return size


__all__ = [
    "PROVIDER_REQUEST_MAX_BYTES",
    "ProviderRequestTooLarge",
    "enforce_provider_request_limit",
    "provider_request_bytes",
]
