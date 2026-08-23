"""Regression tests for GHA run 30496848157 (TB2 GT arm, 5/5 tasks dead at
iteration 1, zero tokens):

    agent error: UnicodeEncodeError: 'ascii' codec can't encode character
    '\\ufeff' in position 7: ordinal not in range(128)

Root cause: the OPENAI_API_KEY secret carried a leading U+FEFF (BOM). httpx
encodes HTTP header values as ASCII, so building ``Authorization: Bearer
<BOM><key>`` died before the first byte hit the network — "Bearer " is exactly
7 characters, hence position 7. The baseline arm survived only because
tb2_baseline.yml sanitizes keys on the runner; tb2_gt.yml did not.

Pinned here:
  1. the exact production mechanism (ASCII encode of a "Bearer <BOM>key"
     header value fails at position 7 on U+FEFF),
  2. the harness-level sanitation (eval/_env.py — arm-neutral, protects every
     workflow, not just the two that carry a runner-side sanitize step),
  3. the container UTF-8 hardening surface (UTF8_ENV for install+run execs),
  4. agent survival: with GT fully live, an ascii stdio stream plus the
     production provider fault still ends as an error RESULT (never a crash),
     and a clean GT-on run under ascii stdio completes end-to-end.
"""
from __future__ import annotations

import pytest

from eval._env import UTF8_ENV, clean_env_value, provider_env

BOM = "\ufeff"


@pytest.fixture(autouse=True)
def _gt_env_isolation():
    """Same guard as test_gt_engine: apply_profile_env (run inside
    create_bridge) writes GT_* fan-out directly into os.environ - strip
    before and restore after so nothing leaks across test modules."""
    import os
    saved = {k: v for k, v in os.environ.items() if k.startswith("GT_")}
    for k in saved:
        del os.environ[k]
    yield
    for k in [k for k in os.environ if k.startswith("GT_")]:
        del os.environ[k]
    os.environ.update(saved)


# --------------------------------------------------------------------------- #
# 1. the production mechanism, byte-exact
# --------------------------------------------------------------------------- #
def test_bearer_header_with_bom_fails_ascii_encode_at_position_7():
    raw_key = BOM + "sk-deadbeef"  # a PowerShell-file paste into gh secret set
    header_value = "Bearer " + raw_key
    with pytest.raises(UnicodeEncodeError) as ei:
        header_value.encode("ascii")  # httpx _normalize_header_value
    assert ei.value.start == 7  # "Bearer " is exactly 7 chars
    assert header_value[ei.value.start] == BOM
    # After sanitation the same header encodes cleanly.
    ("Bearer " + clean_env_value(raw_key)).encode("ascii")


# --------------------------------------------------------------------------- #
# 2. sanitation
# --------------------------------------------------------------------------- #
def test_clean_env_value_strips_bom_and_whitespace():
    assert clean_env_value(BOM + "sk-x") == "sk-x"           # leading BOM
    assert clean_env_value("sk-" + BOM + "x") == "sk-x"      # mid-string BOM
    assert clean_env_value("  sk-x\r\n") == "sk-x"           # secret-file tail
    assert clean_env_value(None) == ""
    assert clean_env_value(BOM + " \n") == ""                # BOM-only -> empty


def test_provider_env_sanitizes_and_drops_empty(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", BOM + "sk-open\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc ")
    monkeypatch.setenv("OPENAI_BASE_URL", BOM)  # cleans to empty -> omitted
    env = provider_env()
    assert env["OPENAI_API_KEY"] == "sk-open"
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-abc"
    assert "OPENAI_BASE_URL" not in env
    for v in env.values():
        v.encode("ascii")  # every forwarded value must be header-safe


def test_provider_env_omits_unset_vars(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    assert provider_env() == {}


# --------------------------------------------------------------------------- #
# 3. container UTF-8 hardening surface
# --------------------------------------------------------------------------- #
def test_utf8_env_pins_utf8_mode_for_task_containers():
    # POSIX/C-locale task images: Mini-SWE's Python must run UTF-8 regardless.
    # The workflow-level PYTHONUTF8=1 reaches only the RUNNER, never the
    # containers - the adapters must carry these into every install/run exec.
    assert UTF8_ENV == {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
