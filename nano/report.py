"""`nano report`: turn a recorded run (``*.transcript.jsonl``) into one HTML page.

The page shows the task, each step the agent took, in order, and how the run
ended. Three rules shape the code:

* Recorded text is data. Every string from the transcript goes through
  ``_esc`` and lands in a text node; the page has no script, no event handler,
  no link and no external resource, and carries a Content-Security-Policy as a
  second layer. Markup is built only by ``_el`` and ``_pre``, which escape
  their arguments, so a value cannot reach the page unescaped by accident.
* The report shows what the file says and nothing else. It never trims or
  rewrites recorded text and never prints a number the file does not give.
* The writer never destroys a file: an existing destination is refused unless
  ``force`` is set, the transcript itself is never overwritten, and the page is
  published whole or not at all.

Loading is strict (a bad line is a line-numbered ``ReportError``); rendering is
tolerant (any JSON object renders, wrong-typed fields are shown as JSON).
"""
from __future__ import annotations

import errno
import html
import json
import os
import re
import secrets
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DARK",
    "LIGHT",
    "ReportError",
    "Summary",
    "derive_stop_reason",
    "load_transcript",
    "render_report",
    "summarize",
    "write_report",
]

# Colours of the page. The CSS is generated from this one dict.
LIGHT = {
    "page_bg": "#ffffff",
    "card_bg": "#f6f8fa",
    "text": "#1f2328",
    "muted": "#59636e",
    "border": "#d1d9e0",
    "error_text": "#82071e",
    "error_bg": "#ffebe9",
}

# The same names for a system set to dark. Used only inside the dark media query.
DARK = {
    "page_bg": "#0d1117",
    "card_bg": "#161b22",
    "text": "#e6edf3",
    "muted": "#9da7b3",
    "border": "#3d444d",
    "error_text": "#ffb3ad",
    "error_bg": "#3b1219",
}

# The one sentence this module adds at the foot of the page. Plain words, no digits.
_FOOTER = "Made by nano report from the recorded run. The text above is shown as it was recorded."

# A constant with no markup characters other than quotes, which are safe inside a
# double-quoted attribute, so it is written as is.
_CSP = "default-src 'none'; style-src 'unsafe-inline'"
_UNKNOWN = "unknown"


class ReportError(Exception):
    """A problem the user can act on; the message is one line."""


# --------------------------------------------------------------------------
# escaping and element building


class Markup(str):
    """Text that is already safe HTML. Only ``_el`` and ``_pre`` make it."""

    __slots__ = ()


class _Trusted(Markup):
    """Markup made inside this module. A Markup object arriving as recorded data
    is a different type and is escaped like any other text."""

    __slots__ = ()


_UNSAFE_CHARS = re.compile("[\ud800-\udfff\x00]")


def _esc(s: str) -> str:
    # Lone surrogates (from a JSON "\ud800") and NUL cannot be written as
    # UTF-8 or kept by a browser; they become U+FFFD. A raw CR would be turned
    # into LF by the HTML parser, so it is written as a character reference.
    s = _UNSAFE_CHARS.sub("\ufffd", s)
    return html.escape(s, quote=True).replace("\r", "&#13;")


def _frag(x: str) -> str:
    return x if type(x) is _Trusted else _esc(x)


def _el(tag: str, cls: str | None, *children: str) -> Markup:
    attr = f' class="{_esc(cls)}"' if cls else ""
    return _Trusted(f"<{tag}{attr}>{''.join(_frag(c) for c in children)}</{tag}>")


def _pre(text: str) -> Markup:
    # Browsers drop one newline right after <pre>, so a text that starts with a
    # newline gets an extra one to keep its own.
    lead = "\n" if text.startswith("\n") else ""
    return _Trusted(f"<pre>{lead}{_esc(text)}</pre>")


def _text(value: object) -> str:
    """The one way a recorded value becomes text: a string as is, else JSON."""
    if isinstance(value, str):
        # str() of a subclass (Markup) gives a plain str, so it gets escaped.
        return str(value)
    try:
        indent = 2 if isinstance(value, (dict, list)) else None
        return json.dumps(value, ensure_ascii=False, indent=indent, default=repr)
    except (TypeError, ValueError, RecursionError):
        try:
            return repr(value)
        except Exception:
            return "<value that cannot be shown>"


# --------------------------------------------------------------------------
# loading


def load_transcript(path: str | os.PathLike[str]) -> list[dict]:
    """Read a JSONL transcript. Every non-blank line must be a JSON object."""
    path = Path(path)
    try:
        mode = os.stat(path).st_mode
    except FileNotFoundError:
        raise ReportError(f"{path}: file not found") from None
    except OSError as e:
        raise ReportError(f"{path}: cannot read the file ({e.strerror or e})") from None
    if stat.S_ISDIR(mode):
        raise ReportError(f"{path}: is a folder, not a transcript file")
    if not stat.S_ISREG(mode):
        # A device such as CON would block forever on read.
        raise ReportError(f"{path}: is not a regular file")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise ReportError(f"{path}: file not found") from None
    except OSError as e:
        raise ReportError(f"{path}: cannot read the file ({e.strerror or e})") from None
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    events: list[dict] = []
    for number, chunk in enumerate(raw.split(b"\n"), start=1):
        if not chunk.strip():
            continue
        try:
            line = chunk.decode("utf-8")
        except UnicodeDecodeError:
            raise ReportError(f"{path}: line {number}: not valid UTF-8") from None
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            raise ReportError(f"{path}: line {number}: not valid JSON") from None
        if not isinstance(event, dict):
            raise ReportError(
                f"{path}: line {number}: expected a JSON object, found "
                f"{type(event).__name__}"
            )
        events.append(event)
    if not events:
        raise ReportError(f"{path}: the file is empty")
    return events


# --------------------------------------------------------------------------
# what the file proves


def _has_tool_calls(tool_calls: object) -> bool:
    # Missing, null or an empty list mean "no tool calls". Anything else, even
    # a value of the wrong type, counts as having them: the file does not
    # prove the model was finished.
    return not (tool_calls is None or (isinstance(tool_calls, list) and not tool_calls))


def _is_event(ev: object, kind: str) -> bool:
    return isinstance(ev, dict) and isinstance(ev.get("type"), str) and ev["type"] == kind


def derive_stop_reason(events: Iterable[object]) -> str:
    """``error`` if an error event exists; else the last assistant turn's own
    stop reason when it made no tool calls; else ``unknown``."""
    last_assistant: dict | None = None
    for ev in events:
        if _is_event(ev, "error"):
            return "error"
        if _is_event(ev, "assistant"):
            last_assistant = ev  # type: ignore[assignment]
    if last_assistant is None or _has_tool_calls(last_assistant.get("tool_calls")):
        return _UNKNOWN
    reason = last_assistant.get("stop_reason")
    return reason if isinstance(reason, str) and reason else _UNKNOWN


@dataclass(frozen=True)
class Summary:
    """The numbers in the summary bar. ``None`` means the file does not prove it.

    Built only by ``summarize``, which guarantees the types; not validated here.

    ``total_time`` is always ``None``: the transcript has no timestamps and the
    report does not guess a duration."""

    iterations: int
    input_tokens: int | None
    output_tokens: int | None
    stop_reason: str
    total_time: None = None


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _token_total(assistants: list[dict], field: str) -> int | None:
    """The sum of ``usage[field]`` over every assistant event, only when there is
    at least one and every one of them records a real count >= 0."""
    if not assistants:
        return None
    total = 0
    for ev in assistants:
        usage = ev.get("usage")
        value = usage.get(field) if isinstance(usage, dict) else None
        if not _is_count(value):
            return None
        total += value
    return total


def summarize(events: Iterable[object]) -> Summary:
    events = list(events)
    assistants = [ev for ev in events if _is_event(ev, "assistant")]
    return Summary(
        iterations=len(assistants),
        input_tokens=_token_total(assistants, "input_tokens"),  # type: ignore[arg-type]
        output_tokens=_token_total(assistants, "output_tokens"),  # type: ignore[arg-type]
        stop_reason=derive_stop_reason(events),
    )


# --------------------------------------------------------------------------
# rendering


def _kv(label: str, value: object) -> Markup:
    return _el("span", "kv", _el("span", "k", label), " ", _el("span", "v", _text(value)))


def _recorded(ev: dict, key: str, empty: str) -> Markup:
    """A <pre> with the recorded text of ``ev[key]``, or a note when absent."""
    if key not in ev or ev[key] is None:
        return _el("p", "muted", empty)
    text = _text(ev[key])
    if text == "":
        return _el("p", "muted", "(empty)")
    return _pre(text)


def _usage_is_plain(usage: object) -> bool:
    return isinstance(usage, dict) and all(
        isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in usage.values()
    )


def _call(entry: object) -> Markup:
    if not isinstance(entry, dict):
        return _el("div", "call", _el("div", "kind", "tool call"), _pre(_text(entry)))
    head = [_el("span", "kind", "tool call"), " "]
    head.append(_el("strong", None, _text(entry["name"]) if "name" in entry else "(no name)"))
    if entry.get("id") is not None:
        head += [" ", _kv("id", entry["id"])]
    parts: list[str] = [_el("div", "head", *head)]
    args = entry.get("arguments")
    if isinstance(args, dict):
        for key, value in args.items():
            parts.append(_el("div", "arg", _el("div", "k", _text(key)), _pre(_text(value))))
    elif args is not None:
        parts.append(_el("div", "arg", _el("div", "k", "arguments"), _pre(_text(args))))
    return _el("div", "call", *parts)


def _assistant(ev: dict) -> Markup:
    parts: list[str] = [_el("div", "kind", "assistant")]
    text = ev.get("text")
    if text is not None and text != "":
        parts.append(_pre(_text(text)))
    calls = ev.get("tool_calls")
    if isinstance(calls, list):
        parts += [_call(c) for c in calls]
    elif calls is not None:
        parts.append(_el("div", "call", _el("div", "kind", "tool calls (not a list)"),
                         _pre(_text(calls))))
    if ev.get("stop_reason") is not None:
        parts.append(_el("div", "meta", _kv("stop reason", ev["stop_reason"])))
    usage = ev.get("usage")
    if usage is not None and not _usage_is_plain(usage):
        parts.append(_el("div", "call", _el("div", "kind", "usage (not readable as counts)"),
                         _pre(_text(usage))))
    return _el("li", "step assistant", *parts)


def _tool_result(ev: dict) -> Markup:
    head = [_el("span", "kind", "tool result")]
    if ev.get("is_error") is True:
        head += [" ", _el("span", "flag", "error")]
    meta: list[str] = []
    if ev.get("name") is not None:
        meta.append(_el("strong", None, _text(ev["name"])))
    if ev.get("id") is not None:
        meta += [" ", _kv("id", ev["id"])]
    if ev.get("is_error") not in (None, True, False):
        meta += [" ", _kv("is_error", ev["is_error"])]
    parts: list[str] = [_el("div", "head", *head)]
    if meta:
        parts.append(_el("div", "meta", *meta))
    parts.append(_recorded(ev, "output", "(no output recorded)"))
    cls = "step result" + (" failed" if ev.get("is_error") is True else "")
    return _el("li", cls, *parts)


def _error(ev: dict) -> Markup:
    return _el("li", "step failed", _el("div", "kind", "error"),
               _recorded(ev, "message", "(no message recorded)"))


def _nudge(ev: dict) -> Markup:
    parts: list[str] = [_el("div", "kind", "nudge (message to the agent)")]
    if ev.get("gt") is not None:
        parts.append(_el("div", "meta", _kv("gt", ev["gt"])))
    parts.append(_recorded(ev, "content", "(no text recorded)"))
    return _el("li", "step nudge", *parts)


def _other(ev: object) -> Markup:
    label = "unrecognised event"
    if isinstance(ev, dict) and isinstance(ev.get("type"), str) and ev["type"] != "":
        label = ev["type"]
    return _el("li", "step other", _el("div", "kind", label), _pre(_text(ev)))


def _step(ev: object) -> Markup:
    if isinstance(ev, dict):
        kind = ev.get("type")
        if kind == "assistant":
            return _assistant(ev)
        if kind == "tool_result":
            return _tool_result(ev)
        if kind == "error":
            return _error(ev)
        if kind == "user":
            return _nudge(ev)
    return _other(ev)


def _stat(cls: str, label: str, value: int | str | None) -> Markup:
    shown = _UNKNOWN if value is None else str(value)
    return _el("div", f"stat {cls}", _el("span", "k", label), _el("span", "v", shown))


def _summary_bar(s: Summary) -> Markup:
    return _el(
        "section", "summary",
        _stat("iterations", "Iterations", s.iterations),
        _stat("input_tokens", "Input tokens", s.input_tokens),
        _stat("output_tokens", "Output tokens", s.output_tokens),
        _stat("stop_reason", "Stop reason (last model turn)", s.stop_reason),
        _stat("total_time", "Total time", s.total_time),
    )


def _vars(palette: dict[str, str]) -> str:
    return ";".join(f"--{k.replace('_', '-')}:{c}" for k, c in palette.items())


def _css() -> str:
    return (
        f":root{{color-scheme:light dark;{_vars(LIGHT)}}}\n"
        f"@media (prefers-color-scheme: dark){{:root{{{_vars(DARK)}}}}}\n"
        "*{box-sizing:border-box}\n"
        "body{margin:0;background:var(--page-bg);color:var(--text);"
        "font:16px/1.5 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}\n"
        "header,main{max-width:60rem;margin:0 auto;padding:1rem}\n"
        "h1{font-size:1.5rem;margin:0 0 .25rem}\n"
        "h2{font-size:1.15rem;margin:1.5rem 0 .5rem}\n"
        ".muted,.k,.kind{color:var(--muted)}\n"
        ".kind{font-size:.85rem;text-transform:uppercase;letter-spacing:.04em}\n"
        "ol{padding-left:1.75rem;margin:0}\n"
        ".step{margin:0 0 1rem;padding:.5rem .75rem;border:1px solid var(--border);"
        "border-radius:6px;max-width:100%;overflow-wrap:anywhere}\n"
        ".step.failed{background:var(--error-bg);color:var(--error-text);"
        "border-color:var(--error-text)}\n"
        ".step.failed .kind,.step.failed .k{color:var(--error-text)}\n"
        ".flag{font-weight:700;color:var(--error-text);background:var(--error-bg);"
        "padding:0 .35rem;border-radius:4px}\n"
        ".call{margin:.5rem 0;padding-left:.75rem;border-left:3px solid var(--border)}\n"
        ".meta,.head{margin:.25rem 0}\n"
        ".kv{margin-right:.75rem}\n"
        ".kv .k{margin-right:.25rem}\n"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;"
        "unicode-bidi:isolate;max-width:100%;overflow-x:auto;margin:.25rem 0;"
        "padding:.5rem;background:var(--card-bg);color:var(--text);"
        "border:1px solid var(--border);border-radius:4px;"
        "font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}\n"
        ".summary{display:flex;flex-wrap:wrap;gap:.5rem;margin:0 0 .5rem}\n"
        ".stat{flex:1 1 9rem;min-width:0;padding:.5rem .75rem;background:var(--card-bg);"
        "border:1px solid var(--border);border-radius:6px;overflow-wrap:anywhere}\n"
        ".stat .k{display:block;font-size:.85rem}\n"
        ".stat .v{display:block;font-size:1.25rem;font-weight:600;max-height:6rem;overflow:auto}\n"
        ".stat.stop_reason{flex-basis:100%}\n"
        ".outcome{margin-top:1.5rem;padding:.75rem;background:var(--card-bg);"
        "border:1px solid var(--border);border-radius:6px}\n"
        ".outcome p{margin:0;overflow-wrap:anywhere}\n"
        "header p{margin:0}\n"
        "footer{max-width:60rem;margin:0 auto;padding:1rem;color:var(--muted);"
        "font-size:.85rem}\n"
        "footer p{margin:0}\n"
    )


def render_report(events: Iterable[object], *, source_name: object) -> str:
    """The whole page as one string. Never raises on any list of JSON values."""
    events = list(events)
    if events and _is_event(events[0], "user"):
        task_event, steps = events[0], events[1:]
    else:
        task_event, steps = None, events

    if isinstance(task_event, dict):
        task = _recorded(task_event, "content", "(no text recorded)")
    else:
        task = _el("p", "muted", "No task recorded in this transcript.")

    if steps:
        body = _el("ol", None, *[_step(ev) for ev in steps])
    else:
        body = _el("p", "muted", "No steps recorded.")

    name = _text(source_name)
    summary = summarize(events)
    stop = summary.stop_reason
    return "".join([
        "<!DOCTYPE html>\n",
        '<html lang="en">\n<head>\n',
        '<meta charset="utf-8">\n',
        f'<meta http-equiv="Content-Security-Policy" content="{_CSP}">\n',
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n',
        _el("title", None, f"Run report: {name}"), "\n",
        "<style>\n", _css(), "</style>\n",
        "</head>\n<body>\n",
        _el("header", None, _el("h1", None, "Run report"),
            _el("p", "muted", _kv("transcript", name))), "\n",
        "<main>\n",
        _summary_bar(summary), "\n",
        _el("section", "task", _el("h2", None, "Task"), task), "\n",
        _el("section", "steps", _el("h2", None, "Steps"), body), "\n",
        _el("section", "outcome", _el("h2", None, "How it ended"),
            _el("p", None, _el("strong", None, "Stop reason"), " ",
                _el("span", "v", stop))), "\n",
        "</main>\n",
        _el("footer", None, _el("p", None, _FOOTER)), "\n",
        "</body>\n</html>\n",
    ])


# --------------------------------------------------------------------------
# the safe write

# Errors from os.link that mean "this filesystem cannot make hard links", where
# falling back to an exclusive create is right. Anything else (no space, no
# permission) is a real failure and is reported, not retried another way.
_NO_HARD_LINKS = {
    errno.EPERM, errno.EXDEV, errno.EINVAL,
    getattr(errno, "ENOTSUP", errno.EPERM),
    getattr(errno, "EOPNOTSUPP", errno.EPERM),
    getattr(errno, "ENOSYS", errno.EPERM),
}
_BINARY = getattr(os, "O_BINARY", 0)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _os_reason(e: OSError) -> str:
    return e.strerror or str(e)


def _refuse_destination(src: Path, dest: Path, force: bool) -> None:
    parent = dest.parent
    if os.name == "nt" and ":" in dest.name:
        # "file:stream" would attach a hidden data stream to an existing file.
        raise ReportError(f"{dest}: a colon in the file name is not allowed")
    if not parent.is_dir():
        raise ReportError(f"{dest}: the folder {parent} does not exist")
    try:
        st = os.lstat(dest)
    except FileNotFoundError:
        st = None
    except OSError as e:
        raise ReportError(f"{dest}: cannot check the output path ({_os_reason(e)})") from None
    if st is None:
        # Not there yet, so it cannot be the transcript; compare spellings anyway.
        if os.path.normcase(os.path.realpath(dest)) == os.path.normcase(os.path.realpath(src)):
            raise ReportError(f"{dest}: that is the transcript itself; it is never overwritten")
        return
    if stat.S_ISLNK(st.st_mode) or (hasattr(os.path, "isjunction") and os.path.isjunction(dest)):
        raise ReportError(f"{dest}: is a link; give a plain file path")
    if not stat.S_ISREG(st.st_mode):
        raise ReportError(f"{dest}: is not a regular file")
    try:
        same = os.path.samefile(src, dest)
    except OSError:
        same = False
    if same:
        raise ReportError(f"{dest}: that is the transcript itself; it is never overwritten")
    if not force:
        raise ReportError(f"{dest}: already exists; use --force to replace it")


def _publish_without_link(tmp: Path, dest: Path) -> None:
    """For filesystems with no hard links: reserve the name exclusively with an
    empty file, then move the finished temp file over it. A reader sees an empty
    file or the whole page, never half a page."""
    os.close(os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, 0o666))
    try:
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(dest)
        except OSError:
            pass
        raise


def write_report(
    src: str | os.PathLike[str],
    out: str | os.PathLike[str] | None = None,
    *,
    force: bool = False,
) -> Path:
    """Write the report for ``src`` and return where it went.

    The default destination is ``<src stem>.report.html`` beside ``src``. An
    existing destination is refused unless ``force``; ``src`` itself, links and
    non-regular files are always refused. The page is written to a temporary
    file in the destination folder and then published whole: with ``force`` by
    ``os.replace``, without it by a hard link that fails if the name is taken,
    so two writers can never both win and nothing is half written.
    """
    src = Path(src)
    events = load_transcript(src)
    dest = Path(out) if out is not None else src.with_name(src.stem + ".report.html")
    _refuse_destination(src, dest, force)
    data = render_report(events, source_name=src.name).encode("utf-8", errors="replace")

    tmp = dest.with_name(f".{dest.name}.{secrets.token_hex(6)}.tmp")
    try:
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, 0o666)
        except OSError as e:
            raise ReportError(f"{dest}: cannot write here ({_os_reason(e)})") from None
        try:
            _write_all(fd, data)
            os.fsync(fd)
        except OSError as e:
            raise ReportError(f"{dest}: could not write the report ({_os_reason(e)})") from None
        finally:
            os.close(fd)
        try:
            if force:
                os.replace(tmp, dest)
            else:
                try:
                    os.link(tmp, dest)
                except FileExistsError:
                    raise ReportError(f"{dest}: already exists; use --force to replace it") from None
                except OSError as e:
                    if e.errno not in _NO_HARD_LINKS:
                        raise
                    try:
                        _publish_without_link(tmp, dest)
                    except FileExistsError:
                        raise ReportError(
                            f"{dest}: already exists; use --force to replace it") from None
        except OSError as e:
            raise ReportError(f"{dest}: could not write the report ({_os_reason(e)})") from None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return dest
