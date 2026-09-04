"""Container exec-env hardening shared by every Harbor adapter (BOTH arms).

Two harness-level, arm-neutral guarantees (kept out of nano/ itself so the
agent stays byte-identical; kept out of any single workflow so no future
workflow can silently lose them):

1. ``clean_env_value`` / ``provider_env``: credentials forwarded into task
   containers are stripped of BOM / stray whitespace. Root cause of GHA run
   30496848157 (all 5 GT-arm tasks dead at iteration 1, zero tokens): the
   OPENAI_API_KEY secret was set from a PowerShell-written file and carried a
   leading U+FEFF. httpx encodes header values as ASCII, so the very first
   request died building ``Authorization: Bearer <U+FEFF><key>`` ("Bearer " is 7
   chars — hence "'ascii' codec can't encode character '\\ufeff' in position
   7"). The baseline workflow already sanitized its keys on the runner
   (tb2_baseline.yml "Sanitize API keys"); tb2_gt.yml did not, which is why
   only the GT arm crashed. This mirror of that step at the adapter seam
   protects every arm and every workflow. Same normalization as the workflow
   step: drop every U+FEFF, then strip whitespace.

2. ``UTF8_ENV``: task images run POSIX/C locales; the workflow-level
   PYTHONUTF8=1 applies only to the RUNNER, never inside task containers.
   Pinning UTF-8 mode in the exec env for install+run keeps nano's Python
   UTF-8 regardless of image locale (subprocess text decoding, file I/O
   defaults). Applied identically to baseline and GT arms — the one
   deliberate shared environment change.
"""
from __future__ import annotations

import os

# UTF-8 regardless of the task image's locale. PYTHONUTF8=1 flips CPython's
# UTF-8 mode (stdio, fs encoding, open() default); PYTHONIOENCODING pins stdio
# even where UTF-8 mode is overridden. Note: HTTP header values are ASCII by
# spec — these flags harden I/O but can NOT absorb a non-ASCII credential,
# which is why provider_env() sanitizes keys as well.
UTF8_ENV: dict[str, str] = {
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
}

_PROVIDER_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "GT_PROVIDER_CONTEXT_WINDOW_TOKENS",
    "GT_PROVIDER_RESERVED_OUTPUT_TOKENS",
    "GT_PROVIDER_CONTEXT_WINDOW_SOURCE",
)


def clean_env_value(value: str | None) -> str:
    """BOM- and whitespace-stripped env value ('' when unset/empty).

    Exactly the tb2_baseline.yml sanitize normalization: remove every U+FEFF
    (a BOM pasted mid-string is as fatal in a header as a leading one), then
    strip surrounding whitespace/newlines.
    """
    return (value or "").replace("\ufeff", "").strip()


def provider_env() -> dict[str, str]:
    """Sanitized provider credentials/gateway from the host env.

    Values that clean to empty are omitted entirely (an empty var and an
    absent var must behave the same inside the container).
    """
    out: dict[str, str] = {}
    for name in _PROVIDER_VARS:
        v = clean_env_value(os.environ.get(name))
        if v:
            out[name] = v
    return out
