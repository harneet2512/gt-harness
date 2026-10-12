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


def test_print_event_shows_tool_call_inputs(capsys):
    from nano.cli import _print_event
    from nano.providers import ToolCall

    _print_event({"type": "assistant", "text": None,
                  "tool_calls": [ToolCall(id="t1", name="bash",
                                          arguments={"command": "ls -la"})]})
    out = capsys.readouterr().out
    assert "bash(" in out
    assert "ls -la" in out


def _write_transcript(path, events):
    import json
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path


def test_report_writes_page_beside_input_and_prints_where(tmp_path, capsys):
    src = _write_transcript(tmp_path / "r.transcript.jsonl",
                            [{"type": "user", "content": "do the thing"}])
    assert main(["report", str(src)]) == 0
    out = tmp_path / "r.transcript.report.html"
    assert capsys.readouterr().out.strip() == f"wrote {out}"
    assert "do the thing" in out.read_text(encoding="utf-8")


def test_report_output_flag_and_force(tmp_path, capsys):
    src = _write_transcript(tmp_path / "r.jsonl", [{"type": "user", "content": "t"}])
    dest = tmp_path / "chosen.html"
    dest.write_text("old")
    assert main(["report", str(src), "-o", str(dest)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("report error: ") and "--force" in err
    assert dest.read_text() == "old"
    assert main(["report", str(src), "--output", str(dest), "--force"]) == 0
    assert "<html" in dest.read_text(encoding="utf-8")


def test_report_error_is_one_line_on_stderr_and_exit_one(tmp_path, capsys):
    assert main(["report", str(tmp_path / "missing.jsonl")]) == 1
    cap = capsys.readouterr()
    assert cap.out == ""
    assert cap.err.count("\n") == 1 and cap.err.startswith("report error: ")


def test_report_os_error_is_reported_not_raised(tmp_path, capsys, monkeypatch):
    def boom(*a, **k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("nano.cli.write_report", boom)
    assert main(["report", "x.jsonl"]) == 1
    assert "Permission denied" in capsys.readouterr().err
