import argparse
from unittest.mock import MagicMock, patch

import pytest

from nano.cli import (
    _float_between,
    _positive_float,
    _positive_int,
    build_provider,
    main,
)


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
    assert _positive_int("7") == 7
    for bad in ("0", "-1", "abc", "nan", "", "1" * 5000):
        with pytest.raises(argparse.ArgumentTypeError) as exc:
            _positive_int(bad)
        message = str(exc.value)
        assert "—" not in message
        assert len(bad) < 4 or bad not in message


def test_at1_max_iterations_only_plain_ascii_digits_are_accepted(capsys):
    # int() alone would accept these and quietly change what the limit means.
    for bad in (" 5 ", "+5", "1_0", "٣"):
        code, _out, err = _refused_run(
            ["run", "task", "--model", "m", f"--max-iterations={bad}"], capsys)
        assert code == 2, repr(bad)
        assert "--max-iterations" in err
        assert len(err) < 500  # usage line plus one short message


BAD_TIME_BUDGET = [
    "0", "0.0", "-0", "-5", "-0.5", "abc", "nan", "NaN", "inf", "-inf",
    "infinity", "1e999", "", "1" * 5000, " 5 ", "1_0", "+inf", "٣", "0x10",
    "1e", ".", "--5", "5s",
]


def test_at1_time_budget_bad_values_refused_naming_the_flag(capsys):
    for bad in BAD_TIME_BUDGET:
        code, out, err = _refused_run(
            ["run", "task", "--model", "m", f"--time-budget-seconds={bad}"],
            capsys)
        assert code == 2, repr(bad[:20])
        assert "--time-budget-seconds" in err, repr(bad[:20])
        assert "Traceback" not in err and "Traceback" not in out


def test_at1_time_budget_separate_value_form_is_refused_too(capsys):
    for bad in ("0", "-5", "nan", "inf"):
        code, _out, err = _refused_run(
            ["run", "task", "--model", "m", "--time-budget-seconds", bad],
            capsys)
        assert code == 2, bad
        assert "--time-budget-seconds" in err


def test_at1_time_budget_error_does_not_echo_the_bad_value(capsys):
    # Same reasoning as --max-iterations: the value may be a pasted key.
    secret = "sk-" + "ab12" * 8
    for bad in (secret, "nope-" + "x" * 12, "-77", "1" * 5000):
        code, out, err = _refused_run(
            ["run", "task", "--model", "m", f"--time-budget-seconds={bad}"],
            capsys)
        assert code == 2
        assert bad not in err and bad not in out


def test_at1_time_budget_good_values_reach_the_agent():
    for argv_extra, expected in (
        ([], None),
        (["--time-budget-seconds", "1800"], 1800.0),
        (["--time-budget-seconds", "0.5"], 0.5),
        (["--time-budget-seconds", "1e3"], 1000.0),
        (["--time-budget-seconds", "1800.0"], 1800.0),
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
        got = agent_cls.call_args.kwargs["time_budget_seconds"]
        assert got == expected and (expected is None or isinstance(got, float))


def test_at1_positive_float_validator_is_reusable_and_message_is_plain():
    assert _positive_float("7") == 7.0
    assert _positive_float("0.25") == 0.25
    for bad in ("0", "-1", "abc", "nan", "inf", "1e999", "", "1" * 5000):
        with pytest.raises(argparse.ArgumentTypeError) as exc:
            _positive_float(bad)
        message = str(exc.value)
        assert "—" not in message
        assert len(bad) < 4 or bad not in message


BAD_TEMPERATURE = [
    "2.5", "2.0000001", "-0.1", "-1", "nan", "inf", "-inf", "infinity", "1e999",
    "abc", "", "3", "20",
]


def test_at2_temperature_bad_values_refused_naming_flag_and_range(capsys):
    for bad in BAD_TEMPERATURE:
        code, out, err = _refused_run(
            ["run", "task", "--model", "m", f"--temperature={bad}"], capsys)
        assert code == 2, bad
        assert "--temperature" in err, bad
        assert "between 0 and 2" in err, bad
        assert "Traceback" not in err and "Traceback" not in out


def test_at2_temperature_separate_value_form_is_refused_too(capsys):
    code, _out, err = _refused_run(
        ["run", "task", "--model", "m", "--temperature", "2.5"], capsys)
    assert code == 2
    assert "--temperature" in err


def test_at2_temperature_only_plain_number_text_is_accepted(capsys):
    for bad in (" 1 ", "1_0", "٣", "0x1", "1,5"):
        code, _out, err = _refused_run(
            ["run", "task", "--model", "m", f"--temperature={bad}"], capsys)
        assert code == 2, repr(bad)
        assert "--temperature" in err
        assert len(err) < 500  # usage line plus one short message


def test_at2_temperature_error_does_not_echo_the_bad_value(capsys):
    # The value may be a pasted key, and eval callers tee stderr into a CI log.
    secret = "sk-" + "ab12" * 8
    for bad in (secret, "nope-" + "x" * 12, "-77", "9" * 5000):
        code, out, err = _refused_run(
            ["run", "task", "--model", "m", f"--temperature={bad}"], capsys)
        assert code == 2
        assert bad not in err and bad not in out


def test_at2_temperature_good_values_reach_build_provider():
    for argv_extra, expected in (
        ([], None),
        (["--temperature", "0"], 0.0),
        (["--temperature=-0"], 0.0),
        (["--temperature", "1"], 1.0),
        (["--temperature", "2"], 2.0),
        (["--temperature", "0.7"], 0.7),
        (["--temperature", "1e0"], 1.0),
    ):
        fake_agent = MagicMock()
        fake_agent.run.return_value = MagicMock(
            final_text="", stop_reason="end_turn", iterations=1,
            total_input_tokens=0, total_output_tokens=0,
            total_cache_read_tokens=0)
        with patch("nano.cli.Agent", return_value=fake_agent), \
             patch("nano.cli.build_provider",
                   return_value=MagicMock(model="m")) as build:
            rc = main(["run", "task", "--model", "m", *argv_extra])
        assert rc == 0
        got = build.call_args.kwargs["temperature"]
        assert got == expected, argv_extra
        assert expected is None or isinstance(got, float)
        # 0 must stay 0.0, not be mistaken for "unset".
        assert (got is None) == (expected is None)


def test_at2_float_between_validator_is_reusable_and_message_is_plain():
    check = _float_between(0, 2)
    assert check("0") == 0.0 and check("2") == 2.0 and check("1.5") == 1.5
    for bad in ("2.5", "-0.1", "nan", "inf", "1e999", "abc", "", "9" * 5000):
        with pytest.raises(argparse.ArgumentTypeError) as exc:
            check(bad)
        message = str(exc.value)
        assert message == "must be between 0 and 2"
        assert "—" not in message
    assert _float_between(1, 10)("10") == 10.0


def test_print_event_shows_tool_call_inputs(capsys):
    from nano.cli import _print_event
    from nano.providers import ToolCall

    _print_event({"type": "assistant", "text": None,
                  "tool_calls": [ToolCall(id="t1", name="bash",
                                          arguments={"command": "ls -la"})]})
    out = capsys.readouterr().out
    assert "bash(" in out
    assert "ls -la" in out



def _letters(n):
    """n characters that repeat a-z, so a cut at the wrong place is visible."""
    return "".join(chr(97 + i % 26) for i in range(n))


def _printed_text(event):
    """Run _print_event with the console mocked; return the Text inside the Panel."""
    from rich.text import Text

    from nano.cli import _print_event

    with patch("nano.cli._console") as console:
        _print_event(event)
    panel = console.print.call_args[0][0]
    assert isinstance(panel.renderable, Text), "tool output must be literal Text"
    return panel.renderable.plain


def _rendered(event, width=2300):
    """Run _print_event on a wide real Rich console; return everything it wrote."""
    import io

    from rich.console import Console

    from nano.cli import _print_event

    buffer = io.StringIO()
    with patch("nano.cli._console", Console(file=buffer, width=width, color_system=None)):
        _print_event(event)
    return buffer.getvalue()


def _tool_result(output, is_error=False):
    return {"type": "tool_result", "output": output, "is_error": is_error}


def test_at3_exactly_2000_characters_prints_in_full_with_no_note():
    output = _letters(2000)
    assert _printed_text(_tool_result(output)) == output


def test_at3_short_and_empty_output_have_no_note():
    assert _printed_text(_tool_result("")) == ""
    assert _printed_text(_tool_result(_letters(1999))) == _letters(1999)


def test_at3_one_character_over_says_one_more_character():
    output = _letters(2001)
    text = _printed_text(_tool_result(output))
    assert text.startswith(output[:2000])
    assert text.endswith("[truncated, 1 more character]")
    assert text.split("\n")[0] == output[:2000]


def test_at3_longer_output_says_the_exact_number_cut():
    output = _letters(3234)
    text = _printed_text(_tool_result(output))
    assert text == output[:2000] + "\n[truncated, 1234 more characters]"


def test_at3_note_is_visible_in_the_real_terminal_output():
    out = _rendered(_tool_result(_letters(3234)))
    assert "[truncated, 1234 more characters]" in out
    assert _letters(2000) in out
    assert _letters(2001) not in out


def test_at3_square_brackets_in_output_print_as_typed(capsys):
    from nano.cli import _print_event

    _print_event(_tool_result("before [/foo] after [bold]x[/bold] [red]y"))
    out = capsys.readouterr().out
    assert "before [/foo] after [bold]x[/bold] [red]y" in out


def test_at3_brackets_near_the_cut_do_not_crash_or_swallow_the_note():
    output = _letters(1990) + "[/foo]" + _letters(10) + "tail"
    out = _rendered(_tool_result(output))
    assert "[/foo]" in out
    assert "[truncated, 10 more characters]" in out


def test_at3_error_panel_gets_the_same_note_and_literal_text():
    event = _tool_result("[/foo] " + _letters(2500), is_error=True)
    out = _rendered(event)
    assert "tool_result (error)" in out
    assert "[/foo]" in out
    assert "[truncated, 507 more characters]" in out


def test_at3_note_uses_plain_words():
    text = _printed_text(_tool_result(_letters(2100)))
    assert "—" not in text and "–" not in text


def test_at3_count_is_in_characters_not_bytes():
    output = "A" + "\U0001F600" * 2100  # 2101 characters, 8401 bytes
    text = _printed_text(_tool_result(output))
    assert text == output[:2000] + "\n[truncated, 101 more characters]"
