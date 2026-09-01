"""Bounded, body-free provider connectivity probe for task diagnostics."""

from __future__ import annotations

import os
import urllib.request


def main() -> None:
    base_url = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    if not base_url:
        print("GT_PROVIDER_PROBE_ERROR=ProviderConfigurationError:base_url_missing")
        return
    request = urllib.request.Request(
        base_url + "/models",
        headers={"Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", "")},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            print(f"GT_PROVIDER_PROBE_STATUS={response.status}")
    except Exception as exc:  # noqa: BLE001 - diagnostic must never abort the agent
        print(f"GT_PROVIDER_PROBE_ERROR={type(exc).__name__}")


if __name__ == "__main__":
    main()
