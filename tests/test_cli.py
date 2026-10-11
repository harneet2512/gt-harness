from unittest.mock import MagicMock, patch

from nano.cli import build_provider, main


def test_build_provider_anthropic_by_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p = build_provider(model="claude-opus-4-7", base_url=None)
    assert p.__class__.__name__ == "AnthropicProvider"


def test_build_provider_openai_for_gpt_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    p = build_provider(model="gpt-5", base_url=None)
    assert p.__class__.__name__ == "OpenAIProvider"


def test_build_provider_openai_with_base_url():
    p = build_provider(
        model="local/llama3",
        base_url="http://localhost:8000/v1",
        temperature=1.0,
    )
    assert p.__class__.__name__ == "OpenAIProvider"
    assert p.base_url == "http://localhost:8000/v1"
    assert p.temperature == 1.0


def test_main_runs_agent_and_prints(monkeypatch, capsys):
    fake_agent = MagicMock()
    fake_agent.run.return_value = MagicMock(
        final_text="DONE",
        stop_reason="end_turn",
        iterations=2,
        total_input_tokens=100,
        total_output_tokens=20,
        total_cache_read_tokens=80,
    )
    with patch("nano.cli.Agent", return_value=fake_agent), \
         patch("nano.cli.build_provider", return_value=MagicMock(model="m")):
        rc = main(["run", "do the thing", "--model", "m"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DONE" in out


def test_main_setup_failure_is_clean_exit_not_traceback(capsys):
    # Provider/agent construction failures (missing SDK, no bash, bad key
    # config) happen before Agent.run()'s error boundary. The CLI must turn
    # them into a printed error + exit 1, never an uncaught traceback.
    with patch("nano.cli.build_provider",
               side_effect=RuntimeError("no api key configured")):
        rc = main(["run", "task", "--model", "m"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "no api key configured" in out


def _refused_run(argv, capsys):
    """Run main() expecting an argparse refusal; return (exit code, out, err)."""
    with patch("nano.cli.Agent") as agent_cls, \
         patch("nano.cli.build_provider") as build:
        try:
            main(argv)
        except SystemExit as e:
            code = e.code
        else:
            raise AssertionError("main() returned instead of refusing")
        agent_cls.assert_not_called()
        build.assert_not_called()
    captured = capsys.readouterr()
    return code, captured.out, captured.err


BAD_MAX_ITERATIONS = [
    "0", "-0", "-1", "-3", "abc", "nan", "inf", "-inf", "3.0", "1e3", "",
    "1" * 5000,
]


def test_at1_max_iterations_bad_values_refused_naming_the_flag(capsys):
    for bad in BAD_MAX_ITERATIONS:
        code, out, err = _refused_run(
            ["run", "task", "--model", "m", "--max-iterations", bad], capsys)
        assert code == 2, bad[:20]
        assert "--max-iterations" in err, bad[:20]
        assert "Traceback" not in err and "Traceback" not in out


def test_at1_max_iterations_equals_form_is_refused_too(capsys):
    code, _out, err = _refused_run(
        ["run", "task", "--model", "m", "--max-iterations=-3"], capsys)
    assert code == 2
    assert "--max-iterations" in err


def test_at1_max_iterations_error_does_not_echo_the_bad_value(capsys):
    # The bad value may be a pasted key, and eval callers tee stderr into a
    # persisted CI log. Built from pieces so no key-shaped literal is committed.
    secret = "sk-" + "ab12" * 8
    for bad in (secret, "nope-" + "x" * 12, "-77", "1" * 5000):
        code, out, err = _refused_run(
            ["run", "task", "--model", "m", "--max-iterations", bad], capsys)
        assert code == 2
        assert bad not in err and bad not in out


def test_at1_max_iterations_good_values_reach_the_agent():
    for argv_extra, expected in (
        ([], 30),
        (["--max-iterations", "1"], 1),
        (["--max-iterations", "30"], 30),
        (["--max-iterations", "100"], 100),
    ):
        fake_agent = MagicMock()
        fake_agent.run.return_value = MagicMock(
            final_text="", stop_reason="end_turn", iterations=1,
            total_input_tokens=0, total_output_tokens=0,
            total_cache_read_tokens=0)
        with patch("nano.cli.Agent", return_value=fake_agent) as agent_cls, \
             patch("nano.cli.build_provider", return_value=MagicMock(model="m")):
            rc = main(["run", "task", "--model", "m", *argv_extra])
        assert rc == 0
        assert agent_cls.call_args.kwargs["max_iterations"] == expected


def test_at1_positive_int_validator_is_reusable_and_message_is_plain():
    import argparse

    import pytest

    from nano.cli import _positive_int

    assert _positive_int("7") == 7
    for bad in ("0", "-1", "abc", "nan", "", "1" * 5000):
        with pytest.raises(argparse.ArgumentTypeError) as exc:
            _positive_int(bad)
        message = str(exc.value)
        assert "—" not in message
        assert len(bad) < 4 or bad not in message


def test_print_event_shows_tool_call_inputs(capsys):
    from nano.cli import _print_event
    from nano.providers import ToolCall

    _print_event({"type": "assistant", "text": None,
                  "tool_calls": [ToolCall(id="t1", name="bash",
                                          arguments={"command": "ls -la"})]})
    out = capsys.readouterr().out
    assert "bash(" in out
    assert "ls -la" in out


def test_at1_max_iterations_only_plain_ascii_digits_are_accepted(capsys):
    # int() alone would accept these and quietly change what the limit means.
    for bad in (" 5 ", "+5", "1_0", "٣"):
        code, _out, err = _refused_run(
            ["run", "task", "--model", "m", f"--max-iterations={bad}"], capsys)
        assert code == 2, repr(bad)
        assert "--max-iterations" in err
        assert len(err) < 500  # usage line plus one short message
