from __future__ import annotations

import argparse
import math
import os
import re
import sys
from collections.abc import Callable

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from .agent import Agent
from .prompts import GT_PROMPT_SUFFIX, SYSTEM_PROMPT
from .providers import AnthropicProvider, OpenAIProvider, Provider

_console = Console()
_TOOL_OUTPUT_LIMIT = 2000


def _number_arg[N: (int, float)](
    parse: Callable[[str], N],
    accepts: Callable[[N], bool],
    message: str,
) -> Callable[[str], N]:
    """Build an argparse type= function.

    `parse` turns text into a number, `accepts` says whether the number is
    allowed. Any failure raises ArgumentTypeError(message); the rejected text
    is never echoed, because it could be a pasted key.
    """

    def convert(text: str) -> N:
        try:
            value = parse(text)
        except (ValueError, OverflowError):
            raise argparse.ArgumentTypeError(message) from None
        if not accepts(value):
            raise argparse.ArgumentTypeError(message)
        return value

    return convert


_positive_int = _number_arg(int, lambda v: v >= 1, "must be a positive whole number")
# `inf > 0` is True and `nan > 0` is False, so finiteness is checked first.
_positive_float = _number_arg(
    float, lambda v: math.isfinite(v) and v > 0, "must be a positive number"
)
# Range checks already reject nan (every comparison with it is False) and inf.
_temperature = _number_arg(float, lambda v: 0 <= v <= 2, "must be between 0 and 2")


_REDACTED = "[redacted]"

# Key shapes that must never reach a terminal or CI log. A closed list: a shape
# that is not here is not caught. Each pattern is anchored so ordinary words
# ("task-force", "disk-backed") are left alone.
_KEY_SHAPES = tuple(
    re.compile(pattern)
    for pattern in (
        # OpenAI and Anthropic keys, including sk-ant-, sk-proj- and the masked
        # form providers echo back ("sk-proj-****abcd").
        r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_*-]{8,}",
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/=*-]{8,}",
        # The shapes below have no leading word boundary on purpose: their long
        # fixed bodies make a false match unlikely, and a boundary would let a
        # key glued to a word character ("key_AKIA...") through.
        r"gh[pos]_[A-Za-z0-9]{20,}",
        r"github_pat_[A-Za-z0-9_]{20,}",
        r"AKIA[0-9A-Z]{16}",
        r"AIza[0-9A-Za-z_-]{35}",
        r"xox[bp]-[A-Za-z0-9-]{10,}",
    )
)
# `name=value` and `name: value` (also `"name": "value"`). Only the value is
# replaced, so the message still says which setting was involved.
_NAMED_SECRET = re.compile(
    r"""(?ix)
    (?P<name>[\w-]*(?:api[_-]?key|token|secret|password)["']?\s*[=:]\s*)
    (?P<value>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*|'(?:[^'\\]|\\.)*|[^\s,;&]+)
    """
)


def _redact_secrets(text: str) -> str:
    """Replace every key-shaped piece of `text` with `[redacted]`."""
    for shape in _KEY_SHAPES:
        text = shape.sub(_REDACTED, text)
    return _NAMED_SECRET.sub(lambda m: m.group("name") + _REDACTED, text)


def _setup_error_line(error: Exception) -> str:
    """The `setup error:` line for `error`, safe to print.

    Redact first, then escape for Rich: the marker and any tag-like piece of the
    message (a stray `[/foo]` raises MarkupError) are then shown literally.
    """
    return "[bold red]setup error:[/] " + escape(
        _redact_secrets(f"{type(error).__name__}: {error}")
    )


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


def _tool_output_body(output: str) -> Text:
    """The panel body for a tool result: the first 2000 characters, then a note
    saying how many were cut.

    Built as Text so the output is shown literally: tool output can contain
    things like `[red]` or a stray `[/foo]`, which Rich would otherwise treat as
    markup (swallowing the text, or raising MarkupError). The slice happens on
    the raw string first, so a tag cut at the limit cannot break anything.
    """
    body = Text(output[:_TOOL_OUTPUT_LIMIT])
    left = len(output) - _TOOL_OUTPUT_LIMIT
    if left > 0:
        unit = "character" if left == 1 else "characters"
        body.append(f"\n[truncated, {left} more {unit}]", style="dim")
    return body


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
        _console.print(Panel(_tool_output_body(event["output"]), title=title,
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
        type=_temperature,
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
        _console.print(_setup_error_line(e))
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
