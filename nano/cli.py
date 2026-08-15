from __future__ import annotations

import argparse
import os
import sys

from rich.console import Console
from rich.panel import Panel

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
        _console.print(Panel(event["output"][:2000], title=title,
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
    run.add_argument("--max-iterations", type=int, default=30)
    run.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Explicit OpenAI-compatible sampling temperature.",
    )
    run.add_argument("--gt-root", default=None,
                     help="Codebase root for GroundTruth evidence enrichment. "
                          "Omit to run stock nano (GT off).")
    run.add_argument(
        "--time-budget-seconds",
        type=float,
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
        _console.print(f"[bold red]setup error:[/] {type(e).__name__}: {e}")
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
