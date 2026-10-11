from unittest.mock import MagicMock, patch

import pytest

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


def test_print_event_shows_tool_call_inputs(capsys):
    from nano.cli import _print_event
    from nano.providers import ToolCall

    _print_event({"type": "assistant", "text": None,
                  "tool_calls": [ToolCall(id="t1", name="bash",
                                          arguments={"command": "ls -la"})]})
    out = capsys.readouterr().out
    assert "bash(" in out
    assert "ls -la" in out


def _fake_agent_result():
    fake_agent = MagicMock()
    fake_agent.run.return_value = MagicMock(
        final_text="DONE",
        stop_reason="end_turn",
        iterations=2,
        total_input_tokens=100,
        total_output_tokens=20,
        total_cache_read_tokens=80,
    )
    return fake_agent


@pytest.mark.parametrize("bad", ["0", "-3", "1.5", "1e3", "abc", "", "True"])
def test_at1_max_iterations_rejects_non_positive(bad, capsys):
    with patch("nano.cli.Agent") as agent_cls, \
         patch("nano.cli.build_provider") as build:
        with pytest.raises(SystemExit) as exc:
            main(["run", "x", "--max-iterations", bad])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--max-iterations" in err
    assert "must be a positive whole number" in err
    assert chr(0x2014) not in err
    agent_cls.assert_not_called()
    build.assert_not_called()


def test_at1_max_iterations_error_does_not_echo_value(capsys):
    secret_shaped = "sk-" + "a" * 24
    with patch("nano.cli.Agent") as agent_cls, \
         patch("nano.cli.build_provider") as build:
        with pytest.raises(SystemExit) as exc:
            main(["run", "x", "--max-iterations", secret_shaped])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert secret_shaped not in captured.err
    assert secret_shaped not in captured.out
    agent_cls.assert_not_called()
    build.assert_not_called()


@pytest.mark.parametrize(("text", "expected"), [("1", 1), ("100", 100), (" 5 ", 5)])
def test_at1_max_iterations_accepts_positive(text, expected):
    with patch("nano.cli.Agent", return_value=_fake_agent_result()) as agent_cls, \
         patch("nano.cli.build_provider", return_value=MagicMock(model="m")):
        rc = main(["run", "x", "--model", "m", "--max-iterations", text])
    assert rc == 0
    assert agent_cls.call_args.kwargs["max_iterations"] == expected


def test_at1_max_iterations_default_is_30():
    with patch("nano.cli.Agent", return_value=_fake_agent_result()) as agent_cls, \
         patch("nano.cli.build_provider", return_value=MagicMock(model="m")):
        rc = main(["run", "x", "--model", "m"])
    assert rc == 0
    assert agent_cls.call_args.kwargs["max_iterations"] == 30


@pytest.mark.parametrize("bad", ["0", "-5", "-0.5", "nan", "inf", "1e999", "abc", "", "0.0"])
def test_at1_time_budget_rejects_non_positive(bad, capsys):
    with patch("nano.cli.Agent") as agent_cls, \
         patch("nano.cli.build_provider") as build:
        with pytest.raises(SystemExit) as exc:
            main(["run", "x", "--time-budget-seconds", bad])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--time-budget-seconds" in err
    assert "must be a positive number" in err
    assert chr(0x2014) not in err
    agent_cls.assert_not_called()
    build.assert_not_called()


def test_at1_time_budget_error_does_not_echo_value(capsys):
    secret_shaped = "sk-" + "b" * 24
    with patch("nano.cli.Agent") as agent_cls, \
         patch("nano.cli.build_provider") as build:
        with pytest.raises(SystemExit) as exc:
            main(["run", "x", "--time-budget-seconds", secret_shaped])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert secret_shaped not in captured.err
    assert secret_shaped not in captured.out
    agent_cls.assert_not_called()
    build.assert_not_called()


@pytest.mark.parametrize(("text", "expected"), [("0.5", 0.5), ("1800", 1800.0), (" 3 ", 3.0)])
def test_at1_time_budget_accepts_positive(text, expected):
    with patch("nano.cli.Agent", return_value=_fake_agent_result()) as agent_cls, \
         patch("nano.cli.build_provider", return_value=MagicMock(model="m")):
        rc = main(["run", "x", "--model", "m", "--time-budget-seconds", text])
    assert rc == 0
    assert agent_cls.call_args.kwargs["time_budget_seconds"] == expected


def test_at1_time_budget_default_is_none_and_not_validated():
    with patch("nano.cli.Agent", return_value=_fake_agent_result()) as agent_cls, \
         patch("nano.cli.build_provider", return_value=MagicMock(model="m")):
        rc = main(["run", "x", "--model", "m"])
    assert rc == 0
    assert agent_cls.call_args.kwargs["time_budget_seconds"] is None


def test_at1_time_budget_negative_inf_with_equals_is_rejected(capsys):
    with patch("nano.cli.Agent") as agent_cls, \
         patch("nano.cli.build_provider") as build:
        with pytest.raises(SystemExit) as exc:
            main(["run", "x", "--time-budget-seconds=-inf"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--time-budget-seconds" in err
    assert "must be a positive number" in err
    agent_cls.assert_not_called()
    build.assert_not_called()


def test_at1_time_budget_negative_inf_as_separate_word_still_exits_2(capsys):
    # argparse reads "-inf" as a flag, not a value, so the validator never runs.
    with patch("nano.cli.Agent") as agent_cls, \
         patch("nano.cli.build_provider") as build:
        with pytest.raises(SystemExit) as exc:
            main(["run", "x", "--time-budget-seconds", "-inf"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--time-budget-seconds" in err
    assert "must be a positive number" not in err  # the validator did not run
    agent_cls.assert_not_called()
    build.assert_not_called()


@pytest.mark.parametrize("bad", ["2.1", "-0.1", "3", "-1", "nan", "inf", "1e999", "abc", ""])
def test_at2_temperature_out_of_range_rejected(bad, capsys):
    with patch("nano.cli.Agent") as agent_cls, \
         patch("nano.cli.build_provider") as build:
        with pytest.raises(SystemExit) as exc:
            main(["run", "x", "--temperature", bad])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--temperature" in err
    assert "must be between 0 and 2" in err
    assert chr(0x2014) not in err
    agent_cls.assert_not_called()
    build.assert_not_called()


def test_at2_temperature_error_does_not_echo_value(capsys):
    secret_shaped = "sk-" + "c" * 24
    with patch("nano.cli.Agent") as agent_cls, \
         patch("nano.cli.build_provider") as build:
        with pytest.raises(SystemExit) as exc:
            main(["run", "x", "--temperature", secret_shaped])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert secret_shaped not in captured.err
    assert secret_shaped not in captured.out
    agent_cls.assert_not_called()
    build.assert_not_called()


@pytest.mark.parametrize(("text", "expected"), [("0", 0.0), ("2", 2.0), ("1.0", 1.0), ("0.7", 0.7)])
def test_at2_temperature_bounds_accepted(text, expected):
    with patch("nano.cli.Agent", return_value=_fake_agent_result()), \
         patch("nano.cli.build_provider", return_value=MagicMock(model="m")) as build:
        rc = main(["run", "x", "--model", "m", "--temperature", text])
    assert rc == 0
    assert build.call_args.kwargs["temperature"] == expected


def test_at2_temperature_default_is_none_and_not_validated():
    with patch("nano.cli.Agent", return_value=_fake_agent_result()), \
         patch("nano.cli.build_provider", return_value=MagicMock(model="m")) as build:
        rc = main(["run", "x", "--model", "m"])
    assert rc == 0
    assert build.call_args.kwargs["temperature"] is None


def test_at2_temperature_negative_inf_exits_2_naming_flag(capsys):
    # The "=" form hands "-inf" to the validator. As a separate word argparse
    # would read it as a flag; "-0.1" as a separate word is covered above.
    with patch("nano.cli.Agent") as agent_cls, \
         patch("nano.cli.build_provider") as build:
        with pytest.raises(SystemExit) as exc:
            main(["run", "x", "--temperature=-inf"])
    assert exc.value.code == 2
    assert "must be between 0 and 2" in capsys.readouterr().err
    agent_cls.assert_not_called()
    build.assert_not_called()


def _tool_result(output, *, is_error=False):
    return {"type": "tool_result", "id": "t1", "output": output, "is_error": is_error}


def _strip_panel(out):
    """Printed text with panel borders, spaces and line breaks removed, to count characters."""
    return "".join(ch for ch in out if ch not in "\n\r│─┌┐└┘ ")


def _shown(capsys):
    return _strip_panel(capsys.readouterr().out)


@pytest.mark.parametrize(("length", "left"), [(2001, 1), (3234, 1234), (2500, 500)])
def test_at3_tool_result_over_limit_shows_truncation_note(length, left, capsys):
    from nano.cli import _print_event

    _print_event(_tool_result("a" * length))
    shown = _shown(capsys)
    note = f"[truncated,{left}more{'character' if left == 1 else 'characters'}]"
    assert note in shown
    # Count only the output part: the note itself contains the letter "a".
    assert shown.replace(note, "").count("a") == 2000


def test_at3_error_result_also_shows_truncation_note(capsys):
    from nano.cli import _print_event

    _print_event(_tool_result("b" * 2600, is_error=True))
    out = capsys.readouterr().out
    assert "tool_result (error)" in out
    assert "[truncated,600morecharacters]" in _strip_panel(out)


@pytest.mark.parametrize("length", [0, 1, 1999, 2000])
def test_at3_at_or_under_limit_has_no_note(length, capsys):
    from nano.cli import _print_event

    _print_event(_tool_result("c" * length))
    out = capsys.readouterr().out
    assert "truncated" not in out
    assert out.count("c") == length


def test_at3_tool_output_with_markup_is_shown_literally(capsys):
    from nano.cli import _print_event

    _print_event(_tool_result("[red]hot[/red] and [/foo] and [bold"))
    shown = _shown(capsys)
    assert "[red]hot[/red]and[/foo]and[bold" in shown


def test_at3_tag_crossing_the_limit_is_shown_literally_and_counted(capsys):
    from nano.cli import _print_event

    text = "x" * 1996 + "[red]tail[/red]" + "y" * 10
    _print_event(_tool_result(text))
    shown = _shown(capsys)
    assert "[red" in shown  # first 2000 characters end inside the tag: "[red"
    assert f"[truncated,{len(text) - 2000}morecharacters]" in shown
    assert "tail" not in shown
