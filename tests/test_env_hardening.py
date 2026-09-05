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

import io
import sys

import pytest

from eval._env import UTF8_ENV, clean_env_value, provider_env
from nano.agent import Agent
from nano.providers import StepResult, ToolCall, Usage

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
    monkeypatch.setenv(
        "GT_PROVIDER_ROUTING_JSON",
        ' {"only":["relace"],"allow_fallbacks":false,"require_parameters":true}\n',
    )
    env = provider_env()
    assert env["OPENAI_API_KEY"] == "sk-open"
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-abc"
    assert env["GT_PROVIDER_ROUTING_JSON"] == (
        '{"only":["relace"],"allow_fallbacks":false,"require_parameters":true}'
    )
    assert "OPENAI_BASE_URL" not in env
    for v in env.values():
        v.encode("ascii")  # every forwarded value must be header-safe


def test_provider_env_omits_unset_vars(monkeypatch):
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "GT_PROVIDER_ROUTING_JSON",
    ):
        monkeypatch.delenv(name, raising=False)
    assert provider_env() == {}


# --------------------------------------------------------------------------- #
# 3. container UTF-8 hardening surface
# --------------------------------------------------------------------------- #
def test_utf8_env_pins_utf8_mode_for_task_containers():
    # POSIX/C-locale task images: nano's Python must run UTF-8 regardless.
    # The workflow-level PYTHONUTF8=1 reaches only the RUNNER, never the
    # containers - the adapters must carry these into every install/run exec.
    assert UTF8_ENV == {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


# --------------------------------------------------------------------------- #
# 4. agent survival with GT live under ascii stdio
# --------------------------------------------------------------------------- #
class _ScriptedProvider:
    def __init__(self, steps):
        self._steps = iter(steps)

    def step(self, messages, tools, system):
        return next(self._steps)


class _BomHeaderProvider:
    """Raises exactly what httpx raised in production (before any network)."""

    def step(self, messages, tools, system):
        ("Bearer " + BOM + "sk-deadbeef").encode("ascii")
        raise AssertionError("unreachable")


def _usage():
    return Usage(input_tokens=10, output_tokens=5, cache_read_tokens=0)


@pytest.fixture
def gt_repo(tmp_path, monkeypatch):
    """A real indexed repo with GT on (mirrors test_gt_engine.indexed_repo)."""
    pytest.importorskip("groundtruth")
    monkeypatch.setenv("GT_GATEWAY", "1")
    monkeypatch.setenv("GT_GATEWAY_NATIVE", "1")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "alpha.py").write_text(
        "def helper(x, y):\n    return x + y\n\n\n"
        "def caller_a(v):\n    return helper(v, 1)\n", encoding="utf-8")
    (pkg / "beta.py").write_text(
        "def helper(a, b, c):\n    return a * b * c\n\n\n"
        "def caller_b(v):\n    return helper(v, 2, 3)\n", encoding="utf-8")
    from gt_engine.indexer import ensure_index
    if ensure_index(str(tmp_path)) is None:
        pytest.skip("gt-index binary unavailable")
    return tmp_path


@pytest.fixture
def ascii_stdio(monkeypatch):
    """Swap stdout/stderr for strict-ascii streams: the C-locale container
    posture (any non-ASCII write raises UnicodeEncodeError)."""
    out = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    err = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    return out


def test_agent_gt_on_bom_provider_dies_as_result_not_crash(gt_repo, ascii_stdio):
    """The exact production failure, end to end: GT bridge live, ascii stdio,
    provider faults at the header encode -> a clean error AgentResult with the
    byte-identical panel text and zero token spend. Never an escaped raise."""
    agent = Agent(provider=_BomHeaderProvider(), system="s",
                  gt_root=str(gt_repo))
    assert agent._gt is not None  # GT genuinely live, not dormant
    result = agent.run("Fix the bug in pkg/alpha.py")
    assert result.stop_reason == "error"
    assert result.final_text == (
        "agent error: UnicodeEncodeError: 'ascii' codec can't encode "
        "character '\\ufeff' in position 7: ordinal not in range(128)")
    assert result.iterations == 1
    assert result.total_input_tokens == 0


def test_agent_gt_on_completes_under_ascii_stdio(gt_repo, ascii_stdio):
    """A healthy GT-on run must survive ascii stdio: index, task_start (v1r
    brief telemetry is redirected), the loop, and the enrich delivery path."""
    steps = [
        StepResult(text=None, tool_calls=[ToolCall(
            id="c1", name="bash", arguments={"command": "echo gt-ascii-smoke"})],
            stop_reason="tool_use", usage=_usage()),
        StepResult(text="done", tool_calls=[], stop_reason="end_turn",
                   usage=_usage()),
    ]
    agent = Agent(provider=_ScriptedProvider(steps), system="s",
                  gt_root=str(gt_repo), max_pushbacks=0)
    assert agent._gt is not None
    result = agent.run("helper in pkg/alpha.py returns the wrong sum")
    assert result.stop_reason in ("end_turn", "unverified")
    assert result.final_text == "done"
    # The delivery path itself under ascii stdio: an ambiguous-symbol grep
    # observation must come back enriched (pure suffix), not raise.
    grep_out = ("pkg/alpha.py:1:def helper(x, y):\n"
                "pkg/beta.py:1:def helper(a, b, c):\n")
    enriched = agent._gt.enrich(
        "bash", {"command": "grep -rn helper ."}, grep_out, False)
    assert enriched.startswith(grep_out)
    assert len(enriched) > len(grep_out)  # a dose was appended, quietly
