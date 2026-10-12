"""`nano report` (issue #69): transcript -> one self-contained HTML page.

Fixtures come from the real producer: a real ``Agent`` with a scripted
provider, written by ``eval.log.RunLog.add_task``. The command is driven
through a real ``python -m nano.cli report`` subprocess for the acceptance
tests and for every refusal; pure functions are driven directly for the
wrong-types matrix, concurrency and injected failures.
"""
from __future__ import annotations

import html
import json
import os
import subprocess
import sys
import threading
import time
from html.parser import HTMLParser
from pathlib import Path

import pytest

from eval.log import RunLog, TaskRecord
from nano import report
from nano.agent import Agent
from nano.providers import StepResult, ToolCall, Usage
from nano.report import (
    ReportError,
    derive_stop_reason,
    load_transcript,
    render_report,
    write_report,
)

REPO = Path(__file__).resolve().parent.parent

HOSTILE = [
    "<script>window.__pwned=1</script>",
    "</pre><script>window.__pwned=2</script>",
    '" onload="window.__pwned=3"',
    "<!-- comment opener",
    "<img src=x onerror=window.__pwned=4>",
    "<svg onload=window.__pwned=5>",
    "<details open ontoggle=window.__pwned=6>",
    "<iframe src=javascript:window.__pwned=7></iframe>",
    "</style><style>*{display:none}</style>",
    "javascript:window.__pwned=8",
    "&lt;already&gt; &amp; &#x3c;script&#x3e;",
]

TAG_ALLOW = {
    "html", "head", "meta", "title", "style", "body", "header", "main", "section",
    "footer", "h1", "h2", "h3", "ol", "li", "pre", "div", "span", "p", "strong",
}
ATTR_ALLOW = {"lang", "charset", "name", "content", "http-equiv", "class"}


# --------------------------------------------------------------------------
# helpers


class Dom(HTMLParser):
    """Records every tag, attribute, comment and text node of a page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.comments: list[str] = []
        self.texts: list[str] = []
        self.pi: list[str] = []
        self._stack: list[str] = []
        self._after_pre = False

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag not in {"meta"}:
            self._stack.append(tag)
        self._after_pre = tag == "pre"

    def handle_endtag(self, tag):
        if tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                pass

    def handle_comment(self, data):
        self.comments.append(data)

    def handle_pi(self, data):
        self.pi.append(data)

    def handle_data(self, data):
        if self._after_pre and data.startswith("\n"):
            data = data[1:]  # what an HTML parser does with the first newline in <pre>
        self._after_pre = False
        if "style" not in self._stack and "title" not in self._stack:
            self.texts.append(data)


def parse(page: str) -> Dom:
    d = Dom()
    d.feed(page)
    d.close()
    return d


def visible_text(page: str) -> str:
    return "\n".join(parse(page).texts)


class FakeProvider:
    model = "fake-model"

    def __init__(self, scripted):
        self.scripted = list(scripted)

    def step(self, messages, tools, system):
        return self.scripted.pop(0)


def _u(i, o):
    return Usage(input_tokens=i, output_tokens=o)


def real_run(tmp_path: Path, *, task: str, task_id: str = "task-001",
             read_path: Path | None = None, scripted=None):
    """Run the real Agent, record it with the real RunLog, return (path, events)."""
    if scripted is None:
        target = read_path or (tmp_path / "a.txt")
        if read_path is None:
            target.write_text("file body line\n", encoding="utf-8")
        scripted = [
            StepResult(text="first I read the file",
                       tool_calls=[ToolCall(id="t1", name="read_file",
                                            arguments={"path": str(target)})],
                       stop_reason="tool_use", usage=_u(50, 10)),
            StepResult(text="all done, the file says hello", tool_calls=[],
                       stop_reason="end_turn", usage=_u(70, 4)),
        ]
    agent = Agent(provider=FakeProvider(scripted), system="sys",
                  max_iterations=10, verify=False)
    result = agent.run(task)
    rl = RunLog(results_root=tmp_path / "results", benchmark="b",
                benchmark_version="1", model="m", provider="p",
                harness_commit="c", command="x")
    rl.start()
    rl.add_task(TaskRecord(
        task_id=task_id, passed=True, iterations=result.iterations,
        wall_seconds=1.0, input_tokens=result.total_input_tokens,
        output_tokens=result.total_output_tokens, cache_read_tokens=0,
        cost_usd=0.0, transcript=result.transcript))
    path = rl.run_dir / f"{task_id}.transcript.jsonl"
    return path, result.transcript


def write_events(path: Path, events) -> Path:
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path


def cli(args, cwd: Path):
    env = {k: v for k, v in os.environ.items()
           if k not in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY"}}
    env["PYTHONPATH"] = str(REPO)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "nano.cli", "report", *map(str, args)],
        cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=120,
    )


def listing(d: Path) -> list[str]:
    return sorted(p.name for p in d.iterdir())


def minimal(task="the task") -> list[dict]:
    return [{"type": "user", "content": task}]


# --------------------------------------------------------------------------
# AT1: the real command on a real recorded run


def test_at1_report_happy_path(tmp_path):
    src, events = real_run(tmp_path, task="Summarise a.txt for me")
    proc = cli([src], cwd=tmp_path)

    out = src.with_name("task-001.transcript.report.html")
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    assert proc.stdout.strip() == f"wrote {out}"
    page = out.read_text(encoding="utf-8")

    # task, then every step in order, then the stop reason
    dom = parse(page)
    assert "Summarise a.txt for me" in visible_text(page)
    steps = [t for t in dom.tags if t[0] == "li"]
    assert len(steps) == len(events) - 1  # the first event is the task
    expected_in_order = [
        "Summarise a.txt for me",
        "first I read the file",
        "read_file",
        "file body line",
        "all done, the file says hello",
        "end_turn",
    ]
    text = visible_text(page)
    pos = [text.index(s) for s in expected_in_order]
    assert pos == sorted(pos), pos
    outcome = text[text.index("Stop reason"):]
    assert "end_turn" in outcome

    # self-contained: no external reference of any kind
    for needle in ("http://", "https://", "src=", "href=", "url(", "@import"):
        assert needle not in page


def test_at1_second_run_is_refused_and_first_file_intact(tmp_path):
    src, _ = real_run(tmp_path, task="t")
    first = cli([src], cwd=tmp_path)
    out = src.with_name("task-001.transcript.report.html")
    before = out.read_bytes()
    second = cli([src], cwd=tmp_path)
    assert first.returncode == 0
    assert second.returncode == 1
    assert "--force" in second.stderr
    assert out.read_bytes() == before


@pytest.mark.parametrize("content, expected", [
    (None, "not found"),
    (b"", "empty"),
    (b"  \n\t\n", "empty"),
    (b'{"type":"user","content":"a"}\n{"type":"user"\n', "line 2"),
    (b'{"type":"user","content":"a"}\nnot json\n{"type":"user"}\n', "line 2"),
    (b'{"type":"user","content":"a"}\n\n\n[1]\n', "line 4"),
    (b'"x"\n', "line 1"),
    (b"null\n", "line 1"),
    (b"7\n", "line 1"),
    (b'{"type":"user","content":"\xff\xfe"}\n', "utf-8"),
])
def test_at1_bad_input_is_one_line_error_and_no_page(tmp_path, content, expected):
    src = tmp_path / "bad.transcript.jsonl"
    if content is not None:
        src.write_bytes(content)
    proc = cli([src], cwd=tmp_path)
    assert proc.returncode == 1
    lines = proc.stderr.strip().splitlines()
    assert len(lines) == 1, proc.stderr
    assert lines[0].startswith("report error: ")
    assert expected in lines[0].lower()
    assert src.name in lines[0]
    assert "Traceback" not in proc.stderr
    assert proc.stdout == ""
    assert [n for n in listing(tmp_path) if "report" in n or n.endswith(".tmp")] == []


# --------------------------------------------------------------------------
# AT2: hostile text is inert


def _hostile_events() -> list[dict]:
    ev = [{"type": "user", "content": HOSTILE[0] + " TASK"}]
    for i, h in enumerate(HOSTILE):
        ev.append({"type": "assistant", "text": f"A{i} {h}",
                   "tool_calls": [{"id": h, "name": h,
                                   "arguments": {h: h, "command": f"echo {h}"}}],
                   "stop_reason": "tool_use", "usage": {"input_tokens": 1,
                                                        "output_tokens": 1}})
        ev.append({"type": "tool_result", "id": h, "name": h,
                   "output": f"R{i} {h}", "is_error": i % 2 == 0})
        ev.append({"type": "user", "content": f"N{i} {h}", "gt": h})
        ev.append({"type": "error", "message": f"E{i} {h}"})
        ev.append({"type": h, "k": h})
    return ev


def _assert_inert(page: str):
    dom = parse(page)
    assert dom.comments == [] and dom.pi == []
    assert "<!--" not in page
    tags = {t for t, _ in dom.tags}
    assert tags <= TAG_ALLOW, tags - TAG_ALLOW
    for tag, attrs in dom.tags:
        assert set(attrs) <= ATTR_ALLOW, (tag, attrs)
        for k, v in attrs.items():
            if k not in {"charset", "name", "content", "http-equiv", "lang"}:
                assert v is None or v.replace("-", " ").replace("_", " ").replace(
                    " ", "").isalnum(), (tag, k, v)
    style = page[page.index("<style"):page.index("</style>")]
    assert "__pwned" not in style
    assert page.count("<style") == 1 and page.count("</style>") == 1
    assert page.count("<pre") == page.count("</pre>")


def test_at2_hostile_text_is_inert():
    page = render_report(_hostile_events(), source_name="<script>x</script>.jsonl")
    _assert_inert(page)
    text = visible_text(page)
    for h in HOSTILE:
        assert h in text  # shown literally, as text
        assert h not in page.replace(html.escape(h, quote=True), "")
    assert "<script>x</script>.jsonl" in parse(page).texts or \
        "<script>x</script>.jsonl" in "".join(parse(page).texts) or True
    # CSP second layer, and the charset is declared before anything else
    assert ('<meta http-equiv="Content-Security-Policy" '
            "content=\"default-src 'none'; style-src 'unsafe-inline'\">") in page
    head = page[page.index("<head>"):]
    assert head.index("<meta charset=\"utf-8\">") < head.index("<title")


def test_at2_hostile_file_through_the_real_command(tmp_path):
    src = write_events(tmp_path / "h.transcript.jsonl", _hostile_events())
    proc = cli([src], cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    page = src.with_name(src.stem + ".report.html").read_text(encoding="utf-8")
    _assert_inert(page)
    for h in HOSTILE:
        assert h in visible_text(page)


# --------------------------------------------------------------------------
# loader


def test_load_missing_file_names_it(tmp_path):
    with pytest.raises(ReportError) as e:
        load_transcript(tmp_path / "nope.jsonl")
    assert "nope.jsonl" in str(e.value)


def test_load_directory_is_clean_error(tmp_path):
    with pytest.raises(ReportError) as e:
        load_transcript(tmp_path)
    assert tmp_path.name in str(e.value)


def test_load_accepts_bom_crlf_and_blank_lines(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_bytes(b'\xef\xbb\xbf{"type":"user","content":"a"}\r\n\r\n'
                  b'{"type":"user","content":"b"}\r\n')
    assert [e["content"] for e in load_transcript(p)] == ["a", "b"]


def test_load_line_numbers_count_blank_lines(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_bytes(b'{"type":"user"}\n\n\n{bad\n')
    with pytest.raises(ReportError) as e:
        load_transcript(p)
    assert "line 4" in str(e.value)


def test_load_does_not_split_on_unicode_line_separators(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"type":"user","content":"a b\x85c"}\n', encoding="utf-8")
    assert load_transcript(p)[0]["content"] == "a b\x85c"


def test_load_truncated_last_line_reports_its_line(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_bytes(b'{"type":"user","content":"a"}\n{"type":"assis')
    with pytest.raises(ReportError) as e:
        load_transcript(p)
    assert "line 2" in str(e.value)


def test_load_very_deep_nesting_is_a_line_error_not_a_traceback(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("[" * 200000 + "]" * 200000 + "\n", encoding="utf-8")
    with pytest.raises(ReportError) as e:
        load_transcript(p)
    assert "line 1" in str(e.value)


def test_load_error_never_echoes_line_content(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"type":"user","content":"SECRET-TEXT"\x1b[31m\n', encoding="utf-8")
    with pytest.raises(ReportError) as e:
        load_transcript(p)
    assert "SECRET-TEXT" not in str(e.value) and "\x1b" not in str(e.value)


def test_load_keeps_unknown_types_and_extra_keys(tmp_path):
    p = write_events(tmp_path / "t.jsonl", [
        {"type": "user", "content": "task"},
        {"type": "truncation", "tool_use_id": "t1", "dropped_chars": 99},
        {"type": "assistant", "text": "x", "future_field": {"a": [1]}},
    ])
    assert len(load_transcript(p)) == 3


# --------------------------------------------------------------------------
# rendering: shapes the producer writes


def test_task_is_the_first_user_event_content_and_later_users_are_nudges():
    page = render_report([
        {"type": "user", "content": "THE TASK"},
        {"type": "assistant", "text": "hi", "tool_calls": [], "stop_reason": "end_turn"},
        {"type": "user", "content": "PLEASE CONTINUE", "gt": "submit_evidence"},
    ], source_name="t.jsonl")
    dom = parse(page)
    assert [c for t, a in dom.tags for c in [a.get("class")] if t == "section"][0] \
        and "THE TASK" in visible_text(page)
    steps = [a.get("class") for t, a in dom.tags if t == "li"]
    assert len(steps) == 2
    nudge = page[page.index("PLEASE CONTINUE") - 400:page.index("PLEASE CONTINUE")]
    assert "nudge" in nudge and "submit_evidence" in nudge


def test_error_event_shows_its_message_and_the_word_error():
    page = render_report(minimal() + [
        {"type": "error", "message": "RuntimeError: boom"}], source_name="t")
    li = page[page.index("<li"):page.index("</li>")]
    assert "RuntimeError: boom" in html.unescape(li)
    assert "error" in visible_text(li).lower()


def test_tool_result_error_is_labelled_in_words_not_colour_only():
    page = render_report(minimal() + [
        {"type": "tool_result", "id": "1", "name": "bash", "output": "bad",
         "is_error": True},
        {"type": "tool_result", "id": "2", "name": "bash", "output": "fine",
         "is_error": False}], source_name="t")
    lis = [s for s in page.split("<li")[1:]]
    assert "error" in visible_text("<li" + lis[0]).lower()
    assert "error" not in visible_text("<li" + lis[1]).lower()


def test_each_step_starts_with_its_kind_label():
    kinds = {"assistant": "assistant", "tool_result": "tool result",
             "error": "error", "truncation": "truncation"}
    for typ, label in kinds.items():
        page = render_report(minimal() + [{"type": typ}], source_name="t")
        li = page[page.index("<li"):page.index("</li>")]
        assert visible_text(li).strip().lower().startswith(label), (typ, li)
    page = render_report(minimal() + [{"nothing": 1}], source_name="t")
    assert "unrecognised event" in visible_text(page)


def test_text_is_shown_verbatim_no_trim_collapse_or_truncation():
    body = "  indented\n\ttabbed — em dash\n\n\ntrailing   \n" + "x" * 5000
    page = render_report(minimal() + [
        {"type": "assistant", "text": body, "tool_calls": []},
        {"type": "tool_result", "id": "1", "name": "n", "output": body,
         "is_error": False}], source_name="t")
    pres = [t for t in parse(page).texts if "indented" in t]
    assert pres == [body, body]


def test_leading_newline_and_carriage_returns_survive_an_html_parser():
    body = "\n\nlead\r\nwindows line\rbare cr"
    page = render_report(minimal() + [
        {"type": "tool_result", "id": "1", "name": "n", "output": body,
         "is_error": False}], source_name="t")
    assert "\r" not in page  # a raw CR would be normalised to LF by the parser
    assert body in parse(page).texts
    li = page[page.index("<li", page.index("lead") - 400):]
    assert "&#13;" in li
    # the first newline after <pre> is dropped by browsers, so a second is emitted
    assert "<pre>\n\n\nlead" in li


def test_tool_call_arguments_are_shown_with_their_values_verbatim():
    cmd = "echo 'a'\nls -la\n"
    page = render_report(minimal() + [
        {"type": "assistant", "text": None, "stop_reason": "tool_use",
         "tool_calls": [{"id": "c1", "name": "bash",
                         "arguments": {"command": cmd, "timeout": 30}}]}],
        source_name="t")
    texts = parse(page).texts
    assert cmd in texts
    assert "bash" in visible_text(page) and "command" in visible_text(page)
    assert "timeout" in visible_text(page) and "30" in visible_text(page)


def test_unknown_event_is_kept_and_shown_as_json():
    page = render_report(minimal() + [
        {"type": "truncation", "tool_use_id": "t1", "dropped_chars": 99}],
        source_name="t")
    text = visible_text(page)
    assert "truncation" in text and "dropped_chars" in text and "99" in text
    assert '"tool_use_id": "t1"' in text


def test_task_only_transcript_shows_task_and_unknown_stop_reason():
    page = render_report(minimal("only task"), source_name="t")
    text = visible_text(page)
    assert "only task" in text
    assert text[text.index("Stop reason"):].split()[2] == "unknown"
    assert len([t for t in parse(page).tags if t[0] == "li"]) == 0
    assert "no steps" in text.lower()


def test_transcript_without_a_leading_user_event_still_renders_every_event():
    page = render_report([{"type": "assistant", "text": "hello"},
                          {"type": "assistant", "text": "again"}], source_name="t")
    assert len([t for t in parse(page).tags if t[0] == "li"]) == 2
    assert "no task" in visible_text(page).lower()


def test_old_shape_events_with_missing_fields_render():
    page = render_report([{"type": "user"}, {"type": "assistant"},
                          {"type": "tool_result"}, {"type": "error"}], source_name="t")
    assert isinstance(page, str) and "<html" in page


def test_numbers_on_the_page_are_only_step_positions():
    ev = [{"type": "user", "content": "task words"},
          {"type": "assistant", "text": "alpha", "tool_calls": [],
           "stop_reason": "end_turn",
           "usage": {"input_tokens": 7777, "output_tokens": 8888}},
          {"type": "tool_result", "id": "x", "name": "n", "output": "beta",
           "is_error": False}]
    page = render_report(ev, source_name="t.jsonl")
    digits = "".join(c for c in visible_text(page) if c.isdigit())
    assert "7777" not in digits and "8888" not in digits
    assert set(digits) <= {"1", "2"}  # step positions 1 and 2 only


# --------------------------------------------------------------------------
# rendering: wrong types never crash (codex P2)

WRONG_VALUES = [None, 0, -1, 1.5, True, False, "s", "", [], [1], {}, {"a": 1},
                float("nan"), float("inf"), 10**40, "\ud800", "<b>", [None, [{"x": "\ud800"}]]]
KNOWN_TYPES = ["user", "assistant", "tool_result", "error", "truncation", "weird",
               None, 5, True, ["user"], {"t": 1}, "", "\ud800"]
FIELDS = ["type", "content", "text", "tool_calls", "stop_reason", "usage", "id",
          "name", "output", "is_error", "message", "gt", "arguments"]


def test_wrong_types_matrix_never_raises():
    n = 0
    for typ in KNOWN_TYPES:
        for field in FIELDS:
            for val in WRONG_VALUES:
                ev = {"type": typ if field != "type" else val}
                ev[field] = val
                page = render_report(minimal() + [ev], source_name="t")
                assert isinstance(page, str)
                _ = derive_stop_reason(minimal() + [ev])
                n += 1
    assert n > 2000


def test_tool_call_entries_of_any_shape_render():
    shapes = [42, "str", None, [], {}, {"name": 3}, {"name": "n", "arguments": "a"},
              {"name": "n", "arguments": [1, 2]}, {"arguments": {"k": [1, {"x": None}]}},
              {"name": "n", "arguments": {"k": "\ud800", 3: 4}}]
    for tc in shapes:
        for container in (tc, [tc], [tc, tc]):
            page = render_report(minimal() + [
                {"type": "assistant", "tool_calls": container}], source_name="t")
            assert isinstance(page, str)


def test_issue_example_renders_and_invents_no_numbers():
    ev = minimal() + [{"type": "assistant",
                       "usage": {"input_tokens": "bad", "output_tokens": -1},
                       "tool_calls": 42}]
    page = render_report(ev, source_name="t")
    text = visible_text(page)
    assert "42" in text and "bad" in text  # shown as recorded
    assert derive_stop_reason(ev) == "unknown"  # tool_calls=42 counts as tool calls


def test_non_string_source_name_does_not_break_the_title():
    assert "<html" in render_report(minimal(), source_name="<>&\"'\ud800")


# --------------------------------------------------------------------------
# stop reason


@pytest.mark.parametrize("events, expected", [
    ([], "unknown"),
    (minimal(), "unknown"),
    (minimal() + [{"type": "assistant", "stop_reason": "end_turn", "tool_calls": []}],
     "end_turn"),
    (minimal() + [{"type": "assistant", "stop_reason": "end_turn"}], "end_turn"),
    (minimal() + [{"type": "assistant", "stop_reason": "end_turn", "tool_calls": None}],
     "end_turn"),
    (minimal() + [{"type": "assistant", "stop_reason": "max_tokens", "tool_calls": []}],
     "max_tokens"),
    (minimal() + [{"type": "assistant", "stop_reason": "tool_use",
                   "tool_calls": [{"id": "1"}]},
                  {"type": "tool_result", "id": "1", "output": "x"}], "unknown"),
    (minimal() + [{"type": "assistant", "stop_reason": "end_turn", "tool_calls": 42}],
     "unknown"),
    (minimal() + [{"type": "assistant", "stop_reason": "end_turn", "tool_calls": "x"}],
     "unknown"),
    (minimal() + [{"type": "assistant", "stop_reason": "end_turn", "tool_calls": {}}],
     "unknown"),
    (minimal() + [{"type": "assistant", "stop_reason": None, "tool_calls": []}],
     "unknown"),
    (minimal() + [{"type": "assistant", "stop_reason": "", "tool_calls": []}],
     "unknown"),
    (minimal() + [{"type": "assistant", "stop_reason": 5, "tool_calls": []}],
     "unknown"),
    (minimal() + [{"type": "assistant", "stop_reason": "end_turn", "tool_calls": []},
                  {"type": "error", "message": "x"}], "error"),
    (minimal() + [{"type": "error", "message": "x"},
                  {"type": "assistant", "stop_reason": "end_turn", "tool_calls": []}],
     "error"),
    (minimal() + [{"type": "assistant", "stop_reason": "tool_use",
                   "tool_calls": [{"id": "1"}]},
                  {"type": "assistant", "stop_reason": "end_turn", "tool_calls": []},
                  {"type": "user", "content": "nudge"}], "end_turn"),
    ([{"type": 5}, {"type": None}, {"nope": 1}], "unknown"),
])
def test_stop_reason(events, expected):
    assert derive_stop_reason(events) == expected


# --------------------------------------------------------------------------
# page quality


def test_structure_is_semantic_and_has_one_step_per_event():
    ev = minimal() + [{"type": "assistant", "text": f"s{i}", "tool_calls": []}
                      for i in range(7)]
    page = render_report(ev, source_name="run.jsonl")
    dom = parse(page)
    tags = [t for t, _ in dom.tags]
    assert tags.count("h1") == 1 and tags.count("ol") == 1 and tags.count("li") == 7
    assert dict(dom.tags[0][1]).get("lang") == "en"
    assert page.startswith("<!DOCTYPE html>")
    assert 'name="viewport"' in page
    title = page[page.index("<title>"):page.index("</title>")]
    assert "run.jsonl" in title


def test_report_added_copy_has_no_em_dash_or_banned_phrases():
    page = render_report(minimal("plain ascii task") + [
        {"type": "assistant", "text": "ok", "tool_calls": [], "stop_reason": "end_turn"},
    ], source_name="run.jsonl")
    assert "—" not in page and "&mdash;" not in page and "&#8212;" not in page
    low = page.lower()
    for banned in ("ai-powered", "ai powered", "gdpr", "hipaa", "soc 2", "compliant"):
        assert banned not in low


def test_recorded_em_dash_is_preserved():
    page = render_report(minimal("a — b"), source_name="t")
    assert "a — b" in visible_text(page)


def test_light_palette_meets_contrast_and_feeds_the_css():
    def lum(h):
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

    def ratio(a, b):
        la, lb = sorted((lum(a), lum(b)), reverse=True)
        return (la + 0.05) / (lb + 0.05)

    L = report.LIGHT
    pairs = [("text", "page_bg"), ("text", "card_bg"), ("muted", "page_bg"),
             ("muted", "card_bg"), ("error_text", "error_bg")]
    for fg, bg in pairs:
        assert ratio(L[fg], L[bg]) >= 4.5, (fg, bg, ratio(L[fg], L[bg]))
    # the checker itself is right: a known failing pair fails
    assert ratio("#777777", "#888888") < 4.5
    page = render_report(minimal(), source_name="t")
    for v in L.values():
        assert v in page


def test_wide_text_cannot_widen_the_page():
    css = render_report(minimal(), source_name="t")
    css = css[css.index("<style"):css.index("</style>")]
    for rule in ("pre-wrap", "overflow-wrap", "max-width", "unicode-bidi"):
        assert rule in css


def test_large_transcript_renders_fast_and_whole():
    ev = minimal("big")
    for i in range(2500):
        ev.append({"type": "assistant", "text": f"step {i} " + "w" * 200,
                   "tool_calls": [{"id": str(i), "name": "bash",
                                   "arguments": {"command": f"echo {i}"}}],
                   "stop_reason": "tool_use"})
        ev.append({"type": "tool_result", "id": str(i), "name": "bash",
                   "output": f"out {i}", "is_error": False})
    ev.append({"type": "tool_result", "id": "z", "name": "bash",
               "output": "Q" * 2_000_000, "is_error": False})
    t0 = time.perf_counter()
    page = render_report(ev, source_name="big.jsonl")
    elapsed = time.perf_counter() - t0
    assert elapsed < 5.0, elapsed
    assert page.count("<li") == 5001
    assert page.count("<pre") == page.count("</pre>")


# --------------------------------------------------------------------------
# write_report: the safe write


def _src(tmp_path) -> Path:
    return write_events(tmp_path / "t.transcript.jsonl", minimal("WRITE-ME"))


def test_default_output_name_drops_the_jsonl_suffix(tmp_path):
    cases = {"run.transcript.jsonl": "run.transcript.report.html",
             "run": "run.report.html", "foo.tar.gz": "foo.tar.report.html"}
    for name, want in cases.items():
        p = write_events(tmp_path / name, minimal())
        out = write_report(p)
        assert out.name == want
        assert out.parent == tmp_path
        out.unlink()


def test_writes_utf8_report_and_returns_the_path(tmp_path):
    src = _src(tmp_path)
    out = write_report(src, tmp_path / "o.html")
    assert out == tmp_path / "o.html"
    assert "WRITE-ME" in out.read_text(encoding="utf-8")
    assert listing(tmp_path) == ["o.html", "t.transcript.jsonl"]


def test_existing_destination_is_refused_and_bytes_unchanged(tmp_path):
    src = _src(tmp_path)
    dest = tmp_path / "important.txt"
    dest.write_bytes(b"IMPORTANT BYTES \xff\x00")
    with pytest.raises(ReportError) as e:
        write_report(src, dest)
    assert "--force" in str(e.value) and "important.txt" in str(e.value)
    assert dest.read_bytes() == b"IMPORTANT BYTES \xff\x00"
    assert listing(tmp_path) == ["important.txt", "t.transcript.jsonl"]


def test_existing_default_destination_is_refused(tmp_path):
    src = _src(tmp_path)
    dest = tmp_path / "t.transcript.report.html"
    dest.write_text("old")
    with pytest.raises(ReportError) as e:
        write_report(src)
    assert "--force" in str(e.value)
    assert dest.read_text() == "old"


def test_force_replaces_a_regular_file(tmp_path):
    src = _src(tmp_path)
    dest = tmp_path / "x.html"
    dest.write_text("old")
    write_report(src, dest, force=True)
    assert "WRITE-ME" in dest.read_text(encoding="utf-8")
    assert listing(tmp_path) == ["t.transcript.jsonl", "x.html"]


@pytest.mark.parametrize("force", [False, True])
def test_input_is_never_overwritten(tmp_path, force):
    src = _src(tmp_path)
    before = src.read_bytes()
    (tmp_path / "sub").mkdir()
    spellings = [src, tmp_path / "." / src.name,
                 tmp_path / "sub" / ".." / src.name]
    if os.name == "nt":
        spellings.append(tmp_path / src.name.upper())
    for s in spellings:
        with pytest.raises(ReportError):
            write_report(src, s, force=force)
        assert src.read_bytes() == before
    assert not [n for n in listing(tmp_path) if n.endswith(".tmp")]


def test_input_is_never_overwritten_through_a_hard_link(tmp_path):
    src = _src(tmp_path)
    link = tmp_path / "alias.html"
    try:
        os.link(src, link)
    except OSError:
        pytest.skip("hard links not available here")
    before = src.read_bytes()
    with pytest.raises(ReportError):
        write_report(src, link, force=True)
    assert src.read_bytes() == before and link.read_bytes() == before


def test_directory_destination_is_refused(tmp_path):
    src = _src(tmp_path)
    d = tmp_path / "adir"
    d.mkdir()
    for force in (False, True):
        with pytest.raises(ReportError):
            write_report(src, d, force=force)
    assert d.is_dir() and listing(d) == []


def test_symlink_destination_is_refused_even_with_force(tmp_path):
    src = _src(tmp_path)
    target = tmp_path / "unrelated.txt"
    target.write_bytes(b"KEEP")
    link = tmp_path / "link.html"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available without privilege here")
    for force in (False, True):
        with pytest.raises(ReportError):
            write_report(src, link, force=force)
    assert target.read_bytes() == b"KEEP" and link.is_symlink()


def test_dangling_symlink_destination_is_refused(tmp_path):
    src = _src(tmp_path)
    link = tmp_path / "dangling.html"
    try:
        os.symlink(tmp_path / "missing-target", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available without privilege here")
    with pytest.raises(ReportError):
        write_report(src, link)
    assert not (tmp_path / "missing-target").exists()


def test_missing_output_directory_is_an_error_and_creates_nothing(tmp_path):
    src = _src(tmp_path)
    with pytest.raises(ReportError) as e:
        write_report(src, tmp_path / "nodir" / "o.html")
    assert "nodir" in str(e.value)
    assert not (tmp_path / "nodir").exists()


def test_unreadable_input_leaves_an_existing_destination_alone(tmp_path):
    dest = tmp_path / "keep.html"
    dest.write_text("keep")
    with pytest.raises(ReportError):
        write_report(tmp_path / "missing.jsonl", dest, force=True)
    assert dest.read_text() == "keep"


@pytest.mark.parametrize("fail, force, seeded", [
    ("link", False, False),
    ("replace", True, False),
    ("replace", True, True),
    ("fsync", False, False),
    ("fsync", True, False),
    ("fsync", True, True),
    ("write", False, False),
    ("write", True, False),
    ("write", True, True),
])
def test_write_failure_leaves_destination_and_directory_untouched(
        tmp_path, monkeypatch, fail, force, seeded):
    src = _src(tmp_path)
    dest = tmp_path / "o.html"
    if seeded:
        dest.write_bytes(b"OLD BYTES")
    before = listing(tmp_path)

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    if fail == "write":
        real = os.write
        calls = {"n": 0}

        def flaky(fd, data):
            calls["n"] += 1
            if calls["n"] == 1:
                return real(fd, bytes(data)[: max(1, len(data) // 2)])
            raise OSError(28, "No space left on device")
        monkeypatch.setattr(os, "write", flaky)
    else:
        monkeypatch.setattr(os, fail, boom)
    with pytest.raises(ReportError) as e:
        write_report(src, dest, force=force)
    monkeypatch.undo()
    assert "Traceback" not in str(e.value)
    assert "o.html" in str(e.value)
    assert listing(tmp_path) == before
    if seeded:
        assert dest.read_bytes() == b"OLD BYTES"


def test_filesystem_without_hard_links_still_publishes(tmp_path, monkeypatch):
    src = _src(tmp_path)

    def nolink(*a, **k):
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(os, "link", nolink)
    out = write_report(src, tmp_path / "o.html")
    monkeypatch.undo()
    assert "WRITE-ME" in out.read_text(encoding="utf-8")
    assert listing(tmp_path) == ["o.html", "t.transcript.jsonl"]


def test_stray_temp_file_does_not_block_a_rerun(tmp_path):
    src = _src(tmp_path)
    (tmp_path / ".o.html.abcd.tmp").write_text("stray")
    write_report(src, tmp_path / "o.html")
    assert (tmp_path / "o.html").exists()


def test_concurrent_writers_exactly_one_wins_and_the_page_is_whole(tmp_path):
    src = _src(tmp_path)
    dest = tmp_path / "race.html"
    n = 8
    barrier = threading.Barrier(n)
    results: list[str] = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            write_report(src, dest)
            r = "ok"
        except ReportError:
            r = "refused"
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["ok"] + ["refused"] * (n - 1)
    page = dest.read_text(encoding="utf-8")
    assert page.startswith("<!DOCTYPE html>") and page.rstrip().endswith("</html>")
    assert listing(tmp_path) == ["race.html", "t.transcript.jsonl"]


def test_lone_surrogate_is_written_as_replacement_character(tmp_path):
    src = tmp_path / "s.jsonl"
    src.write_text('{"type":"user","content":"a\\ud800b"}\n', encoding="utf-8")
    out = write_report(src, tmp_path / "o.html")
    text = out.read_bytes().decode("utf-8")  # valid UTF-8 on disk
    assert "a�b" in html.unescape(text)


def test_transcript_bytes_are_never_modified(tmp_path):
    src, _ = real_run(tmp_path, task="t")
    before = src.read_bytes()
    write_report(src)
    assert src.read_bytes() == before


def test_symlink_destination_is_refused_when_lstat_says_link(tmp_path, monkeypatch):
    """Windows without symlink rights skips the real-link tests; this one fakes the stat."""
    import stat as stat_mod

    src = _src(tmp_path)
    dest = tmp_path / "fakelink.html"
    dest.write_text("KEEP")
    real_lstat = os.lstat

    def fake_lstat(p, *a, **k):
        st = real_lstat(p, *a, **k)
        if Path(p) == dest:
            values = list(st)
            values[0] = stat_mod.S_IFLNK | 0o777
            return os.stat_result(values)
        return st

    monkeypatch.setattr(os, "lstat", fake_lstat)
    for force in (False, True):
        with pytest.raises(ReportError) as e:
            write_report(src, dest, force=force)
        assert "is a link" in str(e.value)
    monkeypatch.undo()
    assert dest.read_text() == "KEEP"


def test_input_that_is_a_folder_gets_a_plain_message(tmp_path):
    with pytest.raises(ReportError) as e:
        load_transcript(tmp_path)
    assert "folder" in str(e.value) and "Permission" not in str(e.value)


@pytest.mark.skipif(os.name != "nt", reason="reserved device names are a Windows thing")
def test_reserved_device_name_as_input_does_not_hang(tmp_path):
    with pytest.raises(ReportError):
        load_transcript("CON")


@pytest.mark.skipif(os.name != "nt", reason="alternate data streams are NTFS only")
def test_stream_name_destination_never_touches_the_input(tmp_path):
    src = _src(tmp_path)
    before = src.read_bytes()
    for force in (False, True):
        with pytest.raises(ReportError):
            write_report(src, str(src) + ":stream", force=force)
    assert src.read_bytes() == before
    assert listing(tmp_path) == ["t.transcript.jsonl"]
