from __future__ import annotations

import argparse
import math
import os
import re
import sys
from collections.abc import Callable

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from .agent import Agent
from .prompts import GT_PROMPT_SUFFIX, SYSTEM_PROMPT
from .providers import AnthropicProvider, OpenAIProvider, Provider

_console = Console()


def build_provider(
    *,
    model: str,
    base_url: str | None,
    temperature: float | None = None,
) -> Provider:
    if base_url:
        # Local OpenAI-compatible servers (vLLM, ollama, llama.cpp) accept any
        # api_key. The openai SDK requires one to instantiate, so supply a
        # placeholder when none is set in the env.
        import openai
        key = os.environ.get("OPENAI_API_KEY") or "sk-local"
        client = openai.OpenAI(base_url=base_url, api_key=key)
        return OpenAIProvider(
            model=model,
            base_url=base_url,
            client=client,
            temperature=temperature,
        )
    if model.startswith(("claude", "anthropic")):
        return AnthropicProvider(model=model)
    return OpenAIProvider(model=model, temperature=temperature)


def _positive_int(text: str) -> int:
    """argparse ``type=`` for a whole number above 0.

    The message is fixed text on purpose: argparse already adds
    ``argument --flag:`` in front, and the value the person typed may be a
    pasted key, so it is never repeated back.
    """
    message = "must be a whole number greater than 0"
    if not (text.isascii() and text.isdigit()):
        raise argparse.ArgumentTypeError(message)
    try:
        value = int(text)
    except ValueError:  # Python refuses to convert more than 4300 digits
        raise argparse.ArgumentTypeError(message) from None
    if value <= 0:
        raise argparse.ArgumentTypeError(message)
    return value


_FLOAT_CHARS = frozenset("0123456789.eE+-")


def _finite_float(text: str) -> float | None:
    """Parse plain number text to a finite float, or return None.

    ``float()`` alone lets ``nan`` through (``nan <= 0`` is False) and turns
    ``1e999`` into ``inf`` without an error, so the text is limited to plain
    number characters first and the result must be finite.
    """
    if not text or not set(text) <= _FLOAT_CHARS:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _positive_float(text: str) -> float:
    """argparse ``type=`` for a finite number above 0.

    The message is fixed text for the same reason as ``_positive_int``: the
    value may be a pasted key and is never repeated back.
    """
    value = _finite_float(text)
    if value is None or value <= 0:
        raise argparse.ArgumentTypeError("must be a number greater than 0")
    return value


def _float_between(low: float, high: float) -> Callable[[str], float]:
    """Build an argparse ``type=`` for a finite number from low to high.

    Both ends are allowed. The message states the range and never repeats the
    typed value, as in ``_positive_float``.
    """
    message = f"must be between {low:g} and {high:g}"

    def check(text: str) -> float:
        value = _finite_float(text)
        if value is None or not (low <= value <= high):
            raise argparse.ArgumentTypeError(message)
        return value + 0.0  # turns -0.0 into 0.0

    return check


_REDACTED = "[redacted]"

# Variables the Anthropic and OpenAI SDKs read a credential from.
_CREDENTIAL_ENV_NAMES = frozenset({
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_WEBHOOK_SIGNING_KEY",
    "OPENAI_API_KEY",
    "OPENAI_ADMIN_KEY",
    "OPENAI_WEBHOOK_SECRET",
})
_CREDENTIAL_ENV_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")
# A shorter value would mask ordinary words and hide what went wrong.
_MIN_SECRET_LENGTH = 8

# "sk-" starting a word: sk-..., sk-ant-..., sk-proj-... and masked sk-abc***.
# The look-behind keeps words such as "risk-free" and "task-force" alone. A
# key glued to the letters before it is still caught when it is 32 or more
# characters with a digit, longer than any identifier seen in an error.
# Dot-joined segments belong to the token.
_SK_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_*-]+(?:[.][A-Za-z0-9_*-]+)*"
    r"|sk-(?=[A-Za-z0-9_-]*[0-9])[A-Za-z0-9_-]{32,}(?:[.][A-Za-z0-9_-]+)*"
)
_BEARER_TOKEN = re.compile(r"(\bBearer\s+)[A-Za-z0-9._~+/=*-]{8,}", re.IGNORECASE)


def _credential_values() -> list[str]:
    """Values of credential variables in the environment, longest first."""
    values = set()
    for name, value in os.environ.items():
        upper = name.upper()
        if upper in _CREDENTIAL_ENV_NAMES or upper.endswith(_CREDENTIAL_ENV_SUFFIXES):
            value = value.strip()
            if len(value) >= _MIN_SECRET_LENGTH:
                values.add(value)
    return sorted(values, key=len, reverse=True)


def _describe(error: BaseException) -> str:
    """``Name: message`` for an exception, even one whose ``__str__`` raises."""
    try:
        message = str(error)
    except Exception:
        message = "(message unavailable)"
    return f"{type(error).__name__}: {message}"


def _redact(text: str) -> str:
    """Replace anything key-shaped in ``text`` with ``[redacted]``.

    Covers the exact value of every credential variable (even without a
    ``sk-`` prefix), ``sk-`` tokens and ``Bearer`` tokens. Names the
    environment does not mark as credentials, and keys of other shapes, are
    not recognised.
    """
    # Shaped tokens go first, whole. If an env value that is only the start of
    # a longer token were replaced first, the marker would split the token and
    # its tail would no longer match.
    text = _SK_TOKEN.sub(_REDACTED, text)
    text = _BEARER_TOKEN.sub(lambda m: m.group(1) + _REDACTED, text)
    for value in _credential_values():
        text = text.replace(value, _REDACTED)
    return text


_TOOL_OUTPUT_LIMIT = 2000


def _tool_output_text(output: str) -> Text:
    """Tool output as literal text, cut at the limit with a note saying how much.

    ``Text`` is used so square brackets in the output (``[/foo]``) print as
    typed: a plain string is read as Rich markup, which eats the note and
    raises ``MarkupError`` on a stray closing tag.
    """
    text = Text(output[:_TOOL_OUTPUT_LIMIT])
    cut = len(output) - _TOOL_OUTPUT_LIMIT
    if cut > 0:
        unit = "character" if cut == 1 else "characters"
        text.append(f"\n[truncated, {cut} more {unit}]")
    return text


def _print_event(event: dict) -> None:
    et = event["type"]
    if et == "assistant":
        if event.get("text"):
            _console.print(Panel(event["text"], title="assistant", border_style="cyan"))
        # Log the tool calls themselves, not just their output. Without the
        # inputs a transcript is unreadable: you see what came back but never
        # what the model actually ran.
        for tc in event.get("tool_calls") or []:
            args = ", ".join(f"{k}={v!r}" for k, v in tc.arguments.items())
            _console.print(Panel(f"{tc.name}({args})", title="tool_call",
                                 border_style="yellow"))
    elif et == "tool_result":
        title = "tool_result" + (" (error)" if event.get("is_error") else "")
        _console.print(Panel(_tool_output_text(event["output"]), title=title,
                             border_style="red" if event.get("is_error") else "green"))
    elif et == "stats":
        _console.print(f"[dim]iter={event['iteration']} "
                       f"in={event['input_tokens']} out={event['output_tokens']}[/dim]")


def main(argv: list[str] | None = None) -> int:
    # Windows consoles and pipes default to cp1252; model output is full of
    # unicode. Never let the printer kill a finished run.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="nano")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="Run the agent on a task description.")
    run.add_argument("task", help="Plain-English task description.")
    run.add_argument("--model", default="claude-opus-4-8")
    run.add_argument("--base-url", default=None,
                     help="OpenAI-compatible base URL (Together, vLLM, etc.).")
    run.add_argument("--max-iterations", type=_positive_int, default=30)
    run.add_argument(
        "--temperature",
        type=_float_between(0, 2),
        default=None,
        help="Explicit OpenAI-compatible sampling temperature.",
    )
    run.add_argument("--gt-root", default=None,
                     help="Codebase root for GroundTruth evidence enrichment. "
                          "Omit to run stock nano (GT off).")
    run.add_argument(
        "--time-budget-seconds",
        type=_positive_float,
        default=None,
        help=(
            "Outer agent wall-clock budget. Bash timeouts are clamped to "
            "preserve time for the final verified response."
        ),
    )
    args = parser.parse_args(argv)

    # Construction failures (missing SDK/key, no usable shell) happen before
    # Agent.run()'s error boundary - turn them into a clean nonzero exit.
    try:
        provider = build_provider(
            model=args.model,
            base_url=args.base_url,
            temperature=args.temperature,
        )
        # The GT sentence is added ONLY when GT is requested: a GT-off run must
        # send byte-identical prompts to stock nano-harness.
        system = SYSTEM_PROMPT + (GT_PROMPT_SUFFIX if args.gt_root else "")
        agent = Agent(provider=provider, system=system,
                      max_iterations=args.max_iterations, on_event=_print_event,
                      gt_root=args.gt_root,
                      time_budget_seconds=args.time_budget_seconds)
    except Exception as e:
        # Exception text can carry a key (a bad-key reply quotes it back), and
        # it is data: printed as literal text, never read as Rich markup.
        message = _redact(_describe(e))
        _console.print(Text.assemble(("setup error:", "bold red"), " ", message))
        return 1
    result = agent.run(args.task)
    _console.print(f"\n[bold]stop:[/] {result.stop_reason}  "
                   f"iterations={result.iterations}  "
                   f"in={result.total_input_tokens}  "
                   f"out={result.total_output_tokens}  "
                   f"cache_read={result.total_cache_read_tokens}")
    if result.final_text:
        _console.print(Panel(result.final_text, title="final", border_style="bold"))
    return 0 if result.stop_reason == "end_turn" else 1


if __name__ == "__main__":
    sys.exit(main())
