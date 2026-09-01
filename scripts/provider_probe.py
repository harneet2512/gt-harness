"""Bounded, body-free provider connectivity probe for task diagnostics."""

from __future__ import annotations

import os
import urllib.request


def main() -> None:
    request = urllib.request.Request(
        "https://api.deepseek.com/models",
        headers={"Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", "")},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            print(f"GT_PROVIDER_PROBE_STATUS={response.status}")
    except Exception as exc:  # noqa: BLE001 - diagnostic must never abort the agent
        print(
            "GT_PROVIDER_PROBE_ERROR="
            f"{type(exc).__name__}:{str(exc)[:160]}"
        )


if __name__ == "__main__":
    main()
