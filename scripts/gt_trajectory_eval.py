#!/usr/bin/env python3
"""gt_trajectory_eval - offline per-task trajectory scorer for GT-on runs.

Scores a single coding-agent trial (or a batch of task dirs) entirely from
artifacts already on disk: the Mini-SWE trajectory (OpenAI-style messages),
the chained GT event journal (``events.jsonl``), delivery payload blobs, the
certified ``graph.db`` neighborhood, the gold reference patch, and the
official verifier outcome.  No network, no provider calls, no repo checkout.

The point of the metrics is to separate "GroundTruth led the agent somewhere
new" from "GroundTruth confirmed what the agent had already touched".  A
prior audit (run 34625781346, aiomonitor-task-snapshots-diff) showed
212/212 deliveries SENT+VISIBLE+SERVED but only ~3 consumed_fair - the
deliveries mostly confirmed already-viewed files.  ``gt_utilization``
therefore always reports ``led_new`` and ``confirmed_only`` as separate
counts; a single "consumed" number is never emitted.

Anchoring rule (gold, not trajectory voting): ``G_i`` is built ONLY from the
gold patch file set, expanded by 1-hop graph.db edges when a graph exists.
The reference set never contains a file merely because the agent touched it.

Action indexing: an "action" is one assistant tool_call (1-based, in message
order).  Journal events use ``action_index``/``action_id`` = index of the
action that produced/preceded the event, so a delivery with action_index k
is first visible to action k+1.  Some producers stub ``action_index`` on
deliveries (all 1 in run 33646776586) while ``iteration`` equals the
carrying action's index - detected per-run and compensated via a
trajectory scan for the ``[GT_EVIDENCE:*]`` marker in tool observations.

Delivery funnel (the same chain scripts/gt_audit.py grades): per delivery,
SENT (a provider request exists at the serving boundary) -> VISIBLE
(delivered content located inside the request's model-visible messages;
``bytes`` when the sealed blob survives, ``marker``/``marker_new`` when
the [GT_*] block is tied to the delivery's kind+target) -> SERVED
(provider_response journaled for that request) -> AGENT-DID (the
trajectory's assistant turn carries the same provider_response_id) ->
CONSUMED (a word-boundary token match on the delivery's content inside
the serving action or the two following - ``prior_touches`` counts
earlier touches so GT-LED (consumed_fair, prior_touches == 0) stays
distinct from GT-CONFIRMED.  Journal anchor fields drift across
producers, so the serving request is resolved from request bytes
(earliest request past a per-kind cursor whose LAST tool message carries
the tied block - markers persist in older observations, so anywhere-
matching is only a labeled fallback) and the effective consumption anchor
is the serving response's action index, not the journaled anchor.
Everything here is descriptive: no causal claim is made from delivery
alone.

Held-out discipline: ``--splits``/``--tuning-tasks``/``--eval-tasks`` assign
each task to the ``tuning`` or ``evaluation`` split; the split is recorded
per task and aggregated separately.  Metrics on the evaluation split must
never become tuning targets.

If a field cannot be computed from the artifacts it is emitted as null and
named in ``missing_inputs`` - never fabricated.

Usage:
    python -m scripts.gt_trajectory_eval --trial <trial-dir> [--tasks-root <dir>]
    python -m scripts.gt_trajectory_eval --batch <root> [--tasks-root <dir>]
        [--splits splits.json] [--baseline <path>] [--baseline-trajs <dir>]
        [--out aggregate.json] [--md table.md] [--outdir out/traj_eval]

Local repo only for imports (the consumption token policy comes from
scripts/gt_audit.py); zero network/provider calls.  Deterministic output
ordering (tasks sorted by task_id, lists sorted).
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sqlite3
import statistics
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Word-boundary delivery-token policy is owned by scripts/gt_audit.py
# (the SENT/VISIBLE/SERVED/CONSUMED join); reuse it, never re-derive it.
from scripts.gt_audit import (  # noqa: E402
    delivery_content_tokens,
    token_word_hit,
)

SCHEMA = "gt.traj_eval.v1"
K_WINDOW = 5  # deliveries: acted within K subsequent actions
CONSUME_TURNS = 3  # gt_audit convention: serving turn + two following

# --------------------------------------------------------------------------
# path normalization
# --------------------------------------------------------------------------
# Repo files inside the container are referenced both as repo-relative
# (``aiomonitor/monitor.py``) and mount-absolute (``/app/aiomonitor/monitor.py``)
# forms; both were observed in delivery targets of run 34625781346.
_PATH_EXT = (
    "py|pyi|go|rs|ts|tsx|js|jsx|mjs|cjs|java|c|h|cc|cpp|cxx|hh|hpp|rb|php|"
    "sh|bash|zsh|fish|md|rst|txt|toml|yaml|yml|json|jsonl|cfg|ini|xml|html|"
    "css|scss|sql|proto|thrift|vue|svelte|hs|ml|mli|fs|fsx|cs|dart|r|jl|"
    "groovy|gradle|kt|kts|scala|ex|exs|erl|hrl|clj|cljs|lua|pl|pm|abs|mk|"
    "cmake|dockerfile|tf|hcl|nix|patch|diff|lock|mod|sum|work|csv|tsv|"
    "gitignore|gitattributes|editorconfig|env|properties|plist|storyboard"
)
_PATH_RE = re.compile(r"[A-Za-z0-9_@.+\-]+(?:/[A-Za-z0-9_@.+\-]+)+"
                      r"\.(?:" + _PATH_EXT + r")(?::\d+)*(?:::\S*)?",
                      re.IGNORECASE)
_BARE_FILE_RE = re.compile(r"[A-Za-z0-9_@.+\-]+\.(?:" + _PATH_EXT + r")",
                           re.IGNORECASE)


def _norm_path(p: str) -> str:
    """Normalize a path token to repo-relative POSIX form."""
    p = p.strip().strip("\"'`").rstrip(":;,.)]")
    p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = re.sub(r"/+", "/", p)
    return p


def path_variants(p: str) -> set[str]:
    """Repo-relative candidate forms of a raw path token.

    ``/app/x/y.py`` -> {``app/x/y.py``, ``x/y.py``}; ``./a/b`` -> ``a/b``.
    Only absolute-originated tokens drop their first component - a real
    repo ``app/`` directory is never stripped.
    """
    was_abs = p.strip().startswith("/")
    n = _norm_path(p).lstrip("/")
    out = {n}
    if was_abs and "/" in n:
        out.add(n.split("/", 1)[1])
    if n.startswith("app/"):
        out.add(n[4:])
    return {v for v in out if v and v not in (".", "..")}


def in_fileset(path_token: str, files: set[str]) -> bool:
    return bool(path_variants(path_token) & files)


def _targets_core(path_token: str, files: set[str]) -> bool:
    """File target in G, or a directory target whose subtree holds G files
    (``ls tests/`` IS core-directed when tests/test_x.py is in G)."""
    if in_fileset(path_token, files):
        return True
    for v in path_variants(path_token):
        base = v.rsplit("/", 1)[-1]
        if "." in base:  # looks like a file, not a directory
            continue
        d = v.rstrip("/") + "/"
        if any(g.startswith(d) for g in files):
            return True
    return False


def match_variant(path_token: str, files: set[str]) -> str | None:
    inter = path_variants(path_token) & files
    return sorted(inter)[0] if inter else None


def extract_mention_paths(text: str) -> set[str]:
    """Conservative file-path tokens from free text (delivery bodies,
    raw commands).  Requires a '/' in the token or a known extension."""
    out: set[str] = set()
    for m in _PATH_RE.finditer(text):
        out.add(_norm_path(m.group(0).split("::")[0].rstrip(":").rsplit(":", 1)[0]
                     if re.search(r":\d+$", m.group(0)) else m.group(0).split("::")[0]))
    for m in _BARE_FILE_RE.finditer(text):
        tok = m.group(0)
        # bare names like "CHANGES.rst" or "test_x.py" (no slash) are kept,
        # decimals/versions are excluded by the extension whitelist
        if "/" in tok or "." in tok:
            out.add(_norm_path(tok))
    return {o for o in out if o}


# --------------------------------------------------------------------------
# command parsing
# --------------------------------------------------------------------------
_VIEW_PROGS = {
    "cat", "head", "tail", "less", "more", "nl", "bat", "wc", "file", "stat",
    "diff", "xxd", "od", "tailf", "view", "column",
}
_AWK_PROGS = {"awk", "gawk", "mawk", "perl"}
_GREP_PROGS = {"grep", "egrep", "fgrep", "rg", "ag", "ack"}
_FIND_PROGS = {"find", "ls", "dir", "tree", "fd", "fdfind", "locate"}
_EDIT_PROGS = {"apply_patch", "patch", "git", "tee", "truncate", "patch_apply"}
_EDITOR_PROGS = {"nano", "vim", "vi", "nvim", "emacs", "code", "subl", "atom"}
_TEST_RUNNERS = {
    "pytest", "py.test", "tox", "ctest", "phpunit", "jest", "vitest", "mocha",
    "bats", "nose", "nosetests", "unittest", "rspec", "minitest", "testthat",
    "gtest", "catch2", "doctest", "quickcheck", "spec",
}
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_REDIRECT_RE = re.compile(r"^(\d*|&)(>{1,2})(.*)$")
_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")
_PY_WRITE_RE = re.compile(
    r"(?:open\(\s*[\"']([^\"']+)[\"']\s*,\s*[\"'][wax+]"
    r"|Path\(\s*[\"']([^\"']+)[\"']\s*\)\s*\.\s*write_(?:text|bytes)"
    r"|write_text\(|write_bytes\(|writelines\()")
_PY_OPEN_PATH_RE = re.compile(
    r"open\(\s*[\"']([^\"']+)[\"']\s*,\s*[\"'][wax+]"
    r"|Path\(\s*[\"']([^\"']+)[\"']\s*\)\s*\.\s*write_(?:text|bytes)")
_PATCH_TARGET_RE = re.compile(
    r"^\+\+\+\s+b/(\S+)|^\*\*\*\s+(?:Update|Add|Delete)\s+File:\s*(\S+)"
    r"|---\s+a/(\S+)", re.MULTILINE)
_SED_SCRIPT_RE = re.compile(r"^\d*(,\d+|\$)?[pdqsxy]?$|^\d+,\d+[pd]$|^s[|/#].*")

_CHECK_SEGMENT_RE = re.compile(
    r"\b(pytest|py\.test|tox|ctest|phpunit|jest|vitest|mocha|bats|rspec|"
    r"deno\s+test|cargo\s+(?:test|build|check|clippy)|go\s+(?:test|build|vet)|"
    r"npm\s+(?:test|run\s+\S*(?:test|check|build|lint)\S*)|"
    r"yarn\s+(?:test|build)|pnpm\s+(?:test|build)|"
    r"make\s+(?:test|check|build|verify)|"
    r"python[\d.]*\s+-m\s+(?:pytest|unittest|nose|mypy|ruff)|"
    r"mypy\b|ruff\b|eslint\b|golangci-lint\b|flake8\b|pylint\b|"
    r"mvn\s+(?:test|verify|package)|gradle\w*\s+(?:test|build)|"
    r"swift\s+test|dotnet\s+test|composer\s+test)\b",
    re.IGNORECASE)
_SCRIPT_TEST_RE = re.compile(
    r"\b(?:bash|sh|zsh|\./)\s*\S*(?:test|check|verify|ci)\S*\.(?:sh|bash)\b"
    r"|\b\S*(?:test|check|verify)\S*\.sh\b", re.IGNORECASE)


class Action:
    """One assistant tool_call, flattened to 1-based order."""

    __slots__ = ("index", "command", "returncode", "view_files", "edit_files",
                 "search_targets", "mention_files", "is_view", "is_search",
                 "is_edit", "is_check", "is_submit", "check_sigs")

    def __init__(self, index: int, command: str) -> None:
        self.index = index
        self.command = command
        self.returncode: int | None = None
        self.view_files: set[str] = set()
        self.edit_files: set[str] = set()
        self.search_targets: set[str] = set()
        self.mention_files: set[str] = set()
        self.is_view = False
        self.is_search = False
        self.is_edit = False
        self.is_check = False
        self.is_submit = False
        self.check_sigs: list[str] = []

    @property
    def touched(self) -> set[str]:
        return self.view_files | self.edit_files | self.mention_files

    @property
    def is_exploration(self) -> bool:
        return self.is_view or self.is_search


def _tokenize_segments(line: str) -> list[list[str]]:
    """Split one command line into pipeline/sequence segments of tokens."""
    lexer = shlex.shlex(line, posix=True, punctuation_chars="|&;")
    lexer.whitespace_split = True
    segments: list[list[str]] = []
    current: list[str] = []
    try:
        for tok in lexer:
            if tok and set(tok) <= set("|&;"):
                if current:
                    segments.append(current)
                current = []
            else:
                current.append(tok)
    except ValueError:
        if current:
            segments.append(current)
        return segments
    if current:
        segments.append(current)
    return segments


def _strip_prefixes(tokens: list[str]) -> list[str]:
    out = list(tokens)
    while out and (_ENV_ASSIGN_RE.match(out[0]) or out[0] in
                   ("sudo", "env", "time", "nice", "ionice", "\\")):
        out = out[1:]
    return out


def _pos_args(tokens: list[str], skip_opts_with_arg: set[str] | None = None,
              drop_first: int = 0) -> list[str]:
    """Positional (non-flag) args, skipping options that consume a value
    and redirection operators plus their targets."""
    skip_opts_with_arg = skip_opts_with_arg or set()
    args: list[str] = []
    skip_next = False
    for t in tokens[1:]:
        if skip_next:
            skip_next = False
            continue
        if t == "--":
            continue
        if t in skip_opts_with_arg:
            skip_next = True
            continue
        r = _REDIRECT_RE.match(t)
        if r:
            if not r.group(3):  # bare '>' consumes the next token
                skip_next = True
            continue
        if t.startswith("-") or t.startswith("<"):
            continue
        args.append(t)
    return args[drop_first:]


def _py_write_targets(code: str) -> set[str]:
    out = set()
    for m in _PY_OPEN_PATH_RE.finditer(code):
        p = m.group(1) or m.group(2)
        if p:
            out.add(_norm_path(p))
    return out


def _patch_targets(body: str) -> set[str]:
    out = set()
    for m in _PATCH_TARGET_RE.finditer(body):
        for g in m.groups():
            if g:
                out.add(_norm_path(g))
    return out


def _check_signature(tokens: list[str]) -> str | None:
    """Stable signature for a check/test/build segment, or None."""
    if not tokens:
        return None
    prog = tokens[0].split("/")[-1]
    joined = " ".join(tokens[:6])
    is_check = (
        prog in _TEST_RUNNERS
        or _CHECK_SEGMENT_RE.search(joined)
        or (prog in ("python", "python3", "python3.12") and len(tokens) > 2
            and tokens[1] == "-m")
        or _SCRIPT_TEST_RE.search(joined)
        or (prog == "make")
        or (prog in ("go", "cargo", "npm", "yarn", "pnpm", "deno", "mvn",
                     "gradle", "dotnet", "swift") and len(tokens) > 1
            and tokens[1] in ("test", "build", "check", "vet", "clippy",
                              "verify", "package", "run"))
    )
    if not is_check:
        return None
    if prog in ("python", "python3", "python3.12") and len(tokens) > 2 \
            and tokens[1] == "-m":
        runner = f"python -m {tokens[2]}"
    elif prog in ("go", "cargo", "deno", "mvn", "gradle", "dotnet", "swift") \
            and len(tokens) > 1:
        runner = f"{prog} {tokens[1]}"
    elif prog in ("npm", "yarn", "pnpm"):
        runner = f"{prog} {tokens[1]}" if len(tokens) > 1 else prog
    else:
        runner = prog
    path_args = sorted(
        a for a in tokens[1:]
        if not a.startswith("-") and ("/" in a or "." in a or a == "."))
    return f"{runner}|{','.join(path_args)}"


def parse_command(action: "Action", heredoc_scripts: dict[str, set[str]]) -> None:
    """Classify one command string into view/edit/search/check targets."""
    cmd = action.command
    if "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in cmd:
        action.is_submit = True

    lines = cmd.split("\n")
    heredoc_delim: str | None = None
    heredoc_body: list[str] = []
    pending_bodies: list[str] = []

    for line in lines:
        if heredoc_delim is not None:
            if line.strip() == heredoc_delim:
                pending_bodies.append("\n".join(heredoc_body))
                heredoc_body = []
                heredoc_delim = None
            else:
                heredoc_body.append(line)
            continue
        for tokens in _tokenize_segments(line):
            _classify_segment(action, tokens, heredoc_scripts)
        m = _HEREDOC_RE.search(line)
        if m:
            heredoc_delim = m.group(1)
            heredoc_body = []

    if heredoc_body:
        pending_bodies.append("\n".join(heredoc_body))

    for body in pending_bodies:
        # python heredoc that writes files; apply_patch/patch heredoc diffs
        pw = _py_write_targets(body)
        if pw:
            action.is_edit = True
            action.edit_files |= pw
        pt = _patch_targets(body)
        if pt:
            action.is_edit = True
            action.edit_files |= pt


def _classify_segment(action: "Action", raw_tokens: list[str],
                      heredoc_scripts: dict[str, set[str]]) -> None:
    if not raw_tokens:
        return
    tokens = _strip_prefixes(raw_tokens)
    if not tokens:
        return

    # redirection writes: > file, >> file, 1>file, &>file (2> is stderr-only)
    for i, t in enumerate(tokens):
        m = _REDIRECT_RE.match(t)
        if m and m.group(1) != "2":
            tgt = m.group(3)
            if not tgt and i + 1 < len(tokens):
                tgt = tokens[i + 1]
            if tgt:
                action.is_edit = True
                action.edit_files.add(_norm_path(tgt))

    prog = tokens[0].split("/")[-1]

    if prog in ("cd", "pushd", "popd", "export", "source", ".", "set",
                "unset", "echo", "printf", "true", "false", "exit", "pwd",
                "which", "type", "history", "alias", "umask"):
        sig = _check_signature(tokens)
        if sig:
            action.is_check = True
            action.check_sigs.append(sig)
        return

    if prog == "str_replace_editor":
        if len(tokens) > 1 and tokens[1] in ("view", "open"):
            action.is_view = True
            for a in _pos_args(tokens, drop_first=1):
                action.view_files.add(_norm_path(a))
    elif prog in _EDITOR_PROGS:
        action.is_view = True
        for a in _pos_args(tokens):
            action.view_files.add(_norm_path(a))
    elif prog in _VIEW_PROGS:
        action.is_view = True
        opt_args = {"-n", "-c", "-v", "--lines", "--bytes", "--verbose"} \
            if prog in ("head", "tail", "tailf") else set()
        for a in _pos_args(tokens, skip_opts_with_arg=opt_args):
            action.view_files.add(_norm_path(a))
    elif prog in _AWK_PROGS:
        # awk '<program>' file...  -> skip first positional (the program)
        files = _pos_args(tokens, skip_opts_with_arg={"-f", "-v", "-F"})
        # if -f used, every positional is a file; else drop the program arg
        files = files if "-f" in tokens else files[1:]
        if files:
            action.is_view = True
            for a in files:
                action.view_files.add(_norm_path(a))
    elif prog == "sed":
        files = _pos_args(tokens, skip_opts_with_arg={"-e", "-f", "-i", "-r"})
        if "-i" in tokens or any(t.startswith("-i") for t in tokens):
            action.is_edit = True
            for a in files:
                action.edit_files.add(_norm_path(a))
        else:
            # drop the script arg (first positional) when it looks like one
            if files and (_SED_SCRIPT_RE.match(files[0])
                          or "-e" in tokens
                          or any(t.startswith("-e") for t in tokens)):
                files = files[1:]
            if files:
                action.is_view = True
                for a in files:
                    action.view_files.add(_norm_path(a))
    elif prog in _GREP_PROGS or (prog == "git" and len(tokens) > 1
                                 and tokens[1] == "grep"):
        action.is_search = True
        files = _pos_args(tokens, skip_opts_with_arg={"-e", "-f", "-m"})
        if "-e" not in tokens and files:
            files = files[1:]  # first positional is the pattern
        recursive = any(t.startswith("-") and ("r" in t or "R" in t)
                        for t in tokens[1:] if t.startswith("-"))
        if files and not recursive:
            action.is_view = True  # grep -n <file> prints file lines
            for a in files:
                action.view_files.add(_norm_path(a))
        else:
            for a in files:
                action.search_targets.add(_norm_path(a))
    elif prog in _FIND_PROGS:
        action.is_search = True
        if prog == "find":
            for a in _pos_args(tokens):
                if a.startswith("-"):
                    break
                action.search_targets.add(_norm_path(a))
        else:
            for a in _pos_args(tokens):
                action.search_targets.add(_norm_path(a))
    elif prog in ("python", "python3", "python3.12", "python2", "pypy") or \
            prog.startswith("python"):
        if "-c" in tokens:
            i = tokens.index("-c")
            if i + 1 < len(tokens):
                pw = _py_write_targets(tokens[i + 1])
                if pw:
                    action.is_edit = True
                    action.edit_files |= pw
        else:
            script_args = _pos_args(tokens, skip_opts_with_arg={"-m", "-c"})
            for a in script_args:
                if a in heredoc_scripts:
                    action.is_edit = True
                    action.edit_files |= heredoc_scripts[a]
    elif prog == "tee":
        action.is_edit = True
        for a in _pos_args(tokens):
            action.edit_files.add(_norm_path(a))
    elif prog == "apply_patch" or prog == "patch" or \
            (prog == "git" and len(tokens) > 1 and tokens[1] == "apply"):
        action.is_edit = True
        # targets live in the diff body (heredoc) or a diff file arg
        for a in _pos_args(tokens):
            action.mention_files.add(_norm_path(a))
    elif prog == "git":
        # git show/diff/log/blame - read-only exploration of repo state
        if len(tokens) > 1 and tokens[1] in ("show", "diff", "log", "blame",
                                             "grep", "ls-files"):
            action.is_search = True
            for a in _pos_args(tokens[1:]):
                action.search_targets.add(_norm_path(a))
    elif prog in ("bash", "sh", "zsh", "dash"):
        for a in _pos_args(tokens):
            if a in heredoc_scripts:
                action.is_edit = True
                action.edit_files |= heredoc_scripts[a]
    elif prog == "gt-evidence":
        pass  # recovery plumbing read - not a repo view

    sig = _check_signature(tokens)
    if sig:
        action.is_check = True
        action.check_sigs.append(sig)

    # broad mention extraction (delivery "acted on" matching)
    for m in _PATH_RE.finditer(" ".join(tokens)):
        action.mention_files.add(_norm_path(m.group(0).split("::")[0]))


def parse_trajectory(traj: dict) -> list[Action]:
    messages = traj.get("messages") or []
    actions: list[Action] = []
    pending: list[Action] = []  # actions awaiting their tool result
    heredoc_scripts: dict[str, set[str]] = {}

    # first pass: record heredoc-written helper scripts so a later
    # `python /tmp/fix.py` action is still recognized as the edit
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            cmd = _command_of(tc)
            if cmd is None:
                continue
            _record_heredoc_scripts(cmd, heredoc_scripts)

    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            for tc in m.get("tool_calls") or []:
                cmd = _command_of(tc) or ""
                a = Action(len(actions) + 1, cmd)
                parse_command(a, heredoc_scripts)
                actions.append(a)
                pending.append(a)
        elif role == "tool":
            if pending:
                a = pending.pop(0)
                extra = m.get("extra") or {}
                rc = extra.get("returncode")
                if rc is None:
                    rc = _returncode_from_content(m.get("content") or "")
                a.returncode = rc
    return actions


def _command_of(tool_call: dict) -> str | None:
    fn = (tool_call or {}).get("function") or {}
    args = fn.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, ValueError):
            return args if args.strip() else ""
    if isinstance(args, dict):
        c = args.get("command")
        return c if isinstance(c, str) else json.dumps(args)
    return None


def _record_heredoc_scripts(cmd: str, out: dict[str, set[str]]) -> None:
    """Remember files whose heredoc bodies contain write calls, so that a
    later `python <file>` execution counts as the actual edit."""
    delim = None
    target = None
    body: list[str] = []
    for line in cmd.split("\n"):
        if delim is not None:
            if line.strip() == delim:
                if target and _py_write_targets("\n".join(body)):
                    out[_norm_path(target)] = _py_write_targets("\n".join(body))
                delim, target, body = None, None, []
            else:
                body.append(line)
            continue
        m = _HEREDOC_RE.search(line)
        if m:
            toks = line[:m.start()].split()
            tgt = None
            for i, t in enumerate(toks):
                r = _REDIRECT_RE.match(t)
                if r and r.group(1) != "2":
                    tgt = r.group(3) or (toks[i + 1] if i + 1 < len(toks) else None)
            delim, target, body = m.group(1), tgt, []


def _returncode_from_content(content) -> int | None:
    if not isinstance(content, str):
        content = json.dumps(content)
    m = re.search(r"<returncode>(-?\d+)</returncode>", content)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------
# event journal
# --------------------------------------------------------------------------
def load_events(state_dir: Path) -> list[dict]:
    p = state_dir / "events.jsonl"
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def find_state_dirs(trial: Path) -> list[Path]:
    """All ``agent/gt-state/<id>`` dirs (layout varies across producers)."""
    roots = [trial / "agent" / "gt-state", trial / "gt-state"]
    out = []
    for r in roots:
        if r.is_dir():
            for child in sorted(r.iterdir()):
                if child.is_dir():
                    out.append(child)
            if (r / "events.jsonl").is_file():
                out.append(r)
    return out


def find_trial_dirs(task_dir: Path) -> list[Path]:
    """Dirs containing a trajectory (``*__XXXXX`` level)."""
    hits = set()
    for name in ("miniswe_trajectory.json", "gt-run.trajectory.json",
                 "nano.txt"):
        for p in task_dir.rglob(name):
            hits.add(p.parent.parent if p.parent.name == "agent"
                     else p.parent)
    return sorted(hits)


# --------------------------------------------------------------------------
# gold + graph
# --------------------------------------------------------------------------
def gold_files_from_patch(patch_text: str) -> set[str]:
    out = set()
    for line in patch_text.splitlines():
        m = re.match(r"^\+\+\+\s+b/(\S+)", line)
        if m:
            out.add(_norm_path(m.group(1)))
        m2 = re.match(r"^diff --git a/(\S+) b/(\S+)", line)
        if m2:
            out.add(_norm_path(m2.group(2)))
    return {o for o in out if o and o != "dev/null"}


def load_gold_files(task_dir: Path) -> tuple[set[str], bool]:
    """Gold files from tasks/<id>/solution/*.patch|*.diff (or dir of diffs)."""
    sol = task_dir / "solution"
    if not sol.is_dir():
        return set(), False
    files: set[str] = set()
    for p in sorted(sol.rglob("*")):
        if p.is_file() and p.suffix in (".patch", ".diff"):
            files |= gold_files_from_patch(p.read_text(encoding="utf-8",
                                                       errors="replace"))
    return files, bool(files)


_HUNK_RE = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$")
_IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
_IDENT_STOP = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "return", "self",
    "none", "null", "nil", "true", "false", "def", "class", "func", "import",
    "from", "elif", "else", "while", "break", "continue", "pass", "raise",
    "assert", "print", "echo", "int", "str", "string", "bool", "float",
    "var", "let", "const", "new", "delete", "if", "then", "do", "done",
    "case", "switch", "default", "try", "catch", "finally", "throw",
    "public", "private", "static", "void", "package", "struct", "enum",
    "interface", "impl", "type", "map", "range", "make", "error", "err",
    "args", "kwargs", "stdin", "stdout", "stderr", "todo", "fixme", "xxx",
})
_LINE_TOLERANCE = 6  # graph rows describe the BASE file; hunk numbers drift


def parse_gold_patch(patch_text: str) -> dict[str, dict]:
    """Per-file gold detail: new-side changed ranges + added identifiers.

    ``{file: {"new_ranges": [(start, end)], "old_ranges": [...],
              "added_idents": {name}}}`` — hunk granularity (the whole
    ``+c,d`` span counts as changed), plus identifier tokens seen only on
    added lines (candidate NEW symbol names the base-commit graph never
    contains).
    """
    files: dict[str, dict] = {}
    cur: str | None = None
    for line in patch_text.splitlines():
        m = re.match(r"^diff --git a/(\S+) b/(\S+)", line)
        if m:
            cur = _norm_path(m.group(2))
            files.setdefault(cur, {"new_ranges": [], "old_ranges": [],
                                   "added_idents": set()})
            continue
        m = re.match(r"^\+\+\+\s+b/(\S+)", line)
        if m:
            cur = _norm_path(m.group(1))
            files.setdefault(cur, {"new_ranges": [], "old_ranges": [],
                                   "added_idents": set()})
            continue
        m = re.match(r"^---\s+a/(\S+)", line)
        if m and cur is None:
            cur = _norm_path(m.group(1))
            files.setdefault(cur, {"new_ranges": [], "old_ranges": [],
                                   "added_idents": set()})
            continue
        m = _HUNK_RE.match(line)
        if m and cur is not None:
            o_s, o_l = int(m.group(1)), int(m.group(2) or 1)
            n_s, n_l = int(m.group(3)), int(m.group(4) or 1)
            files[cur]["old_ranges"].append((o_s, o_s + max(o_l, 1) - 1))
            files[cur]["new_ranges"].append((n_s, n_s + max(n_l, 1) - 1))
            continue
        if cur is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            for tok in _IDENT_RE.findall(line[1:]):
                if tok.lower() not in _IDENT_STOP:
                    files[cur]["added_idents"].add(tok)
    return {k: v for k, v in files.items() if k and k != "dev/null"}


def load_gold_detail(task_dir: Path) -> tuple[dict[str, dict], bool]:
    """All solution patches merged into one per-file detail map."""
    sol = task_dir / "solution"
    if not sol.is_dir():
        return {}, False
    detail: dict[str, dict] = {}
    for p in sorted(sol.rglob("*")):
        if not (p.is_file() and p.suffix in (".patch", ".diff")):
            continue
        for f, d in parse_gold_patch(
                p.read_text(encoding="utf-8", errors="replace")).items():
            slot = detail.setdefault(f, {"new_ranges": [], "old_ranges": [],
                                         "added_idents": set()})
            slot["new_ranges"] += d["new_ranges"]
            slot["old_ranges"] += d["old_ranges"]
            slot["added_idents"] |= d["added_idents"]
    return detail, bool(detail)


# --------------------------------------------------------------------------
# gold-closure builder (relevance oracle: gold patch + graph closure ONLY)
# --------------------------------------------------------------------------
# Semantic edge types worth a 1-hop expansion.  Resolution bookkeeping
# (CANDIDATE*, HAS_*_FACT, DerivationFact rows) is excluded on purpose: those
# nodes re-tag the same file and would inflate the closure without adding a
# caller/callee/test the agent could actually open.
_CLOSURE_EDGE_TYPES = (
    "CALLS", "IMPORTS", "READS", "WRITES", "RAISES", "IMPLEMENTS",
    "EXTENDS", "SELECTED_TARGET",
)
_SYMBOL_LABELS = {
    "Function", "Method", "Class", "Interface", "Struct", "Enum",
    "TypeAlias", "Typedef", "Module", "Variable", "Constant", "Macro",
    "Procedure", "Trait", "Impl",
}
_TEST_PATH_RE = re.compile(
    r"(?:^|/)(?:tests?|testing|__tests__|specs?)/"
    r"|(?:^|/)(?:test_[^/]+|[^/]+_test|[^/]+\.spec|[^/]+\.test|"
    r"[^/]+_spec)\.[a-z0-9]+$",
    re.IGNORECASE)


def _is_test_file(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path))


def _graph_schema(con: sqlite3.Connection) -> tuple[set[str], set[str]]:
    cur = con.cursor()
    ncols = {r[1] for r in cur.execute("PRAGMA table_info(nodes)")}
    ecols = {r[1] for r in cur.execute("PRAGMA table_info(edges)")}
    return ncols, ecols


def _nodes_for_files(con: sqlite3.Connection, files: list[str],
                     ncols: set[str]) -> list[dict]:
    """All nodes living in ``files``; symbol fields when the schema has them."""
    if not files:
        return []
    cols = ["id", "file_path"]
    for c in ("name", "qualified_name", "label", "start_line", "end_line",
              "is_test"):
        cols.append(c if c in ncols else f"NULL AS {c}")
    marks = ",".join("?" * len(files))
    rows = con.execute(
        f"SELECT {', '.join(cols)} FROM nodes WHERE file_path IN ({marks})",
        files).fetchall()
    out = []
    for r in rows:
        out.append({"id": r[0], "file_path": r[1], "name": r[2],
                    "qualified_name": r[3], "label": r[4],
                    "start_line": r[5], "end_line": r[6], "is_test": r[7]})
    return out


def _edge_files(con: sqlite3.Connection, anchor_ids: list[int],
                edge_types: tuple[str, ...] | None,
                tests_only: bool) -> set[str]:
    """File paths of nodes 1-hop from anchors, both directions."""
    if not anchor_ids:
        return set()
    amarks = ",".join("?" * len(anchor_ids))
    type_clause = ""
    params: list = list(anchor_ids)
    if edge_types is not None:
        tmarks = ",".join("?" * len(edge_types))
        type_clause = f" AND e.type IN ({tmarks})"
        params += list(edge_types)
    test_clause = ""
    if tests_only:
        test_clause = " AND n2.is_test = 1"
    q = (f"SELECT DISTINCT n2.file_path FROM edges e "
         f"JOIN nodes n2 ON e.target_id = n2.id "
         f"WHERE e.source_id IN ({amarks}){type_clause}{test_clause}"
         f" AND n2.file_path IS NOT NULL")
    out = {_norm_path(r[0]) for r in con.execute(q, params) if r[0]}
    q2 = (f"SELECT DISTINCT n1.file_path FROM edges e "
          f"JOIN nodes n1 ON e.source_id = n1.id "
          f"WHERE e.target_id IN ({amarks}){type_clause}"
          + (" AND n1.is_test = 1" if tests_only else "")
          + " AND n1.file_path IS NOT NULL")
    out |= {_norm_path(r[0]) for r in con.execute(q2, params) if r[0]}
    if tests_only:
        # producers without is_test: filename heuristic on 1-hop neighbours
        q3 = (f"SELECT DISTINCT n2.file_path FROM edges e "
              f"JOIN nodes n2 ON e.target_id = n2.id "
              f"WHERE e.source_id IN ({amarks}) AND n2.file_path IS NOT NULL")
        out |= {_norm_path(r[0]) for r in con.execute(q3, anchor_ids)
                if r[0] and _is_test_file(_norm_path(r[0]))}
        q4 = (f"SELECT DISTINCT n1.file_path FROM edges e "
              f"JOIN nodes n1 ON e.source_id = n1.id "
              f"WHERE e.target_id IN ({amarks}) AND n1.file_path IS NOT NULL")
        out |= {_norm_path(r[0]) for r in con.execute(q4, anchor_ids)
                if r[0] and _is_test_file(_norm_path(r[0]))}
    return out


def build_gold_closure(task_dir: Path, graph_path: Path | None) -> dict:
    """Relevance oracle: gold patch files + changed symbols + 1-hop graph.

    Levels, strictest first:
      * symbol  - gold file has graph nodes overlapping the patch's changed
        ranges (or named by added identifiers); expand via CALLS / IMPORTS /
        READS / WRITES / RAISES / IMPLEMENTS / EXTENDS / SELECTED_TARGET both
        directions plus any-edge neighbours that are tests.
      * file    - no symbol match for the file (new file, drifted lines, or a
        graph without symbol columns): every node in the file anchors the
        same 1-hop file expansion.  This is also the whole-graph mode when
        the schema has no symbol columns at all.
      * none    - no graph.db: closure degrades to the gold file set.

    The closure NEVER contains a file merely because a trajectory touched
    it; only the gold patch and graph edges decide membership.
    """
    detail, gold_ok = load_gold_detail(task_dir)
    gold = set(detail)
    out: dict = {"gold_files": sorted(gold), "gold_ok": gold_ok,
                 "graph_path": str(graph_path) if graph_path else None,
                 "symbol_level": False, "files_detail": {},
                 "closure_files": [], "changed_symbols": {},
                 "closure_edge_types": list(_CLOSURE_EDGE_TYPES)}
    if not gold_ok:
        return out
    if graph_path is None:
        out["mode"] = "no_graph"
        return out
    try:
        con = sqlite3.connect(f"file:{graph_path}?mode=ro", uri=True)
    except sqlite3.Error:
        out["mode"] = "graph_unreadable"
        return out
    try:
        ncols, ecols = _graph_schema(con)
        if "file_path" not in ncols or not {"source_id", "target_id"} <= ecols:
            out["mode"] = "graph_schema_minimal"
            return out
        symbol_cols = {"start_line", "end_line"} <= ncols
        typed_edges = "type" in ecols
        out["symbol_level"] = symbol_cols
        edge_types = _CLOSURE_EDGE_TYPES if typed_edges else None
        closure: set[str] = set()
        for gf in sorted(gold):
            nodes = _nodes_for_files(con, [gf], ncols)
            info = detail.get(gf) or {}
            anchors: list[dict] = []
            matched_names: list[str] = []
            if symbol_cols:
                ranges = [(max(1, s - _LINE_TOLERANCE), e + _LINE_TOLERANCE)
                          for s, e in info.get("new_ranges", [])]
                added = {a.lower() for a in info.get("added_idents", set())}
                for n in nodes:
                    if n["label"] and n["label"] not in _SYMBOL_LABELS:
                        continue
                    hit = False
                    if n["start_line"] is not None and ranges:
                        lo = n["start_line"]
                        hi = n["end_line"] if n["end_line"] else lo
                        hit = any(lo <= e2 and hi >= s2
                                  for s2, e2 in ranges)
                    if not hit and added:
                        nm = (n["name"] or "").lower()
                        qn = (n["qualified_name"] or "").lower()
                        hit = bool(nm and nm in added) or any(
                            a and a in qn for a in added if len(a) >= 4)
                    if hit:
                        anchors.append(n)
                        matched_names.append(
                            n["qualified_name"] or n["name"] or str(n["id"]))
            mode = "symbol" if anchors else "file"
            if not anchors:
                anchors = [n for n in nodes
                           if not n["label"] or n["label"] in _SYMBOL_LABELS
                           or n["label"] == "File"]
                if not anchors:
                    anchors = nodes  # last resort: facts carry file_path too
            anchor_ids = [n["id"] for n in anchors if n["id"] is not None]
            nbr = _edge_files(con, anchor_ids, edge_types,
                              tests_only=False)
            if typed_edges:
                nbr |= _edge_files(con, anchor_ids, None, tests_only=True)
            nbr -= gold
            closure |= nbr
            out["files_detail"][gf] = {
                "mode": mode, "anchors": len(anchor_ids),
                "symbols": sorted(set(matched_names)),
                "neighbor_files": sorted(nbr)}
            if matched_names:
                out["changed_symbols"][gf] = sorted(set(matched_names))
        out["closure_files"] = sorted(closure)
        out["mode"] = "symbol" if out["changed_symbols"] else "file"
        return out
    except sqlite3.Error:
        out["mode"] = "graph_query_failed"
        return out
    finally:
        con.close()


def graph_neighborhood(graph_path: Path, gold: set[str]) -> set[str] | None:
    """1-hop file neighborhood of gold files via nodes/edges tables."""
    try:
        con = sqlite3.connect(f"file:{graph_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        cur = con.cursor()
        tables = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "nodes" not in tables or "edges" not in tables:
            return set()
        ncols = {r[1] for r in cur.execute("PRAGMA table_info(nodes)")}
        ecols = {r[1] for r in cur.execute("PRAGMA table_info(edges)")}
        if "file_path" not in ncols or not {"source_id", "target_id"} <= ecols:
            return set()
        marks = ",".join("?" * len(gold))
        if not gold:
            return set()
        rows = cur.execute(
            f"SELECT DISTINCT n2.file_path FROM edges e "
            f"JOIN nodes n1 ON e.source_id = n1.id "
            f"JOIN nodes n2 ON e.target_id = n2.id "
            f"WHERE n1.file_path IN ({marks}) AND n2.file_path IS NOT NULL",
            sorted(gold)).fetchall()
        rows += cur.execute(
            f"SELECT DISTINCT n1.file_path FROM edges e "
            f"JOIN nodes n1 ON e.source_id = n1.id "
            f"JOIN nodes n2 ON e.target_id = n2.id "
            f"WHERE n2.file_path IN ({marks}) AND n1.file_path IS NOT NULL",
            sorted(gold)).fetchall()
        return {_norm_path(r[0]) for r in rows if r[0]} - set(gold)
    except sqlite3.Error:
        return None
    finally:
        con.close()


def find_graph(state_dirs: list[Path], trial: Path) -> Path | None:
    for d in state_dirs:
        for name in ("graph.db", "graph.sqlite", "graph.sqlite3"):
            p = d / name
            if p.is_file():
                return p
    for p in trial.rglob("graph.db"):
        return p
    for p in trial.rglob("graph.sqlite*"):
        if p.is_file():
            return p
    return None


# --------------------------------------------------------------------------
# deliveries
# --------------------------------------------------------------------------
_DELIVERY_EVENTS = {"evidence_delivery", "context_addition_delivery",
                    "delivery"}
_VIEW_EVENTS = {"file_view", "viewed_files", "file_opened", "file_read",
               "view"}
# execution_evidence is what run 33646776586 journals; check_execution /
# plan_check are the typed names newer producers use for the same idea.
_CHECK_EVENTS = {"execution_evidence", "check", "execution_check",
                 "check_execution", "plan_check", "plan_check_execution"}
_REQUEST_EVENTS = {"provider_delivery", "provider_request"}
_RESPONSE_EVENTS = {"provider_response"}
_GT_MARKER_RE = re.compile(r"\[GT_[A-Z_]+(?::[a-z_]+)?\]")
# markers that only ever ride sealed (tool-observation) deliveries; they
# can never tie a prompt-lane delivery
_SEALED_MARKER_RE = re.compile(r"^\[GT_(?:EVIDENCE:|EXECUTION_EVIDENCE)")
# kind spellings that deviate from the [GT_EVIDENCE:<kind>] convention
# (observed across run 33646776586 request blobs)
_GT_KIND_MARKER = {
    "context_contract": "[GT_TASK_CONTRACT]",
    "context_delta": "[GT_OBLIGATION_DELTA]",
    "execution_evidence": "[GT_EXECUTION_EVIDENCE]",
}
_NONFILE_TARGETS = {"provider_prompt", "prompt", "system_prompt", "", "task"}


def _event_paths(e: dict) -> set[str]:
    out = set()
    for k in ("path", "file", "file_path", "target", "filename"):
        v = e.get(k)
        if isinstance(v, str) and v and v not in _NONFILE_TARGETS:
            for var in path_variants(v):
                out.add(var)
    for k in ("paths", "files", "viewed_files", "changed_paths"):
        v = e.get(k)
        if isinstance(v, list):
            for it in v:
                if isinstance(it, str):
                    for var in path_variants(it):
                        out.add(var)
    return out


def _parse_delivery_blob(path: Path) -> dict:
    """A deliveries/*.json blob is rendered text, not JSON:
    ``[GT_CONTEXT_UNIT] {json}`` header line, then ``[GT_EVIDENCE:kind]``
    (or another ``[GT_*]`` marker) plus the shipped text."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    out: dict = {"unit_id": path.stem, "action_index": None, "kind": None,
                 "text": raw}
    lines = raw.split("\n", 1)
    m = re.match(r"\[GT_CONTEXT_UNIT\]\s*(\{.*\})", lines[0].strip())
    if m:
        try:
            hdr = json.loads(m.group(1))
            out["action_index"] = hdr.get("action_index")
            out["unit_id"] = hdr.get("unit_id") or path.stem
            out["supersession_key"] = hdr.get("supersession_key")
        except json.JSONDecodeError:
            pass
        body = lines[1] if len(lines) > 1 else ""
    else:
        body = raw
    mk = re.search(r"\[GT_EVIDENCE:([a-z_]+)\]", body)
    if mk:
        out["kind"] = mk.group(1)
    else:
        mk2 = re.search(r"\[GT_[A-Z_]+\]", body)
        out["kind"] = mk2.group(0) if mk2 else None
    out["files"] = extract_mention_paths(body)
    return out


def collect_deliveries(events: list[dict], state_dirs: list[Path],
                       traj: dict) -> tuple[list[dict], int, int]:
    """Delivered GT units, deduplicated by identity, with anchor metadata."""
    deliveries: dict[str, dict] = {}
    prepared: set[str] = set()
    refused = 0
    for e in events:
        ev = e.get("event")
        if ev == "delivery_prepared":
            did = e.get("delivery_identity") or e.get("dedup_key") \
                or e.get("payload_sha256") or e.get("event_hash")
            if did:
                prepared.add(did)
            continue
        if ev == "delivery_refused":
            refused += 1
            continue
        if ev not in _DELIVERY_EVENTS:
            continue
        did = e.get("delivery_identity") or e.get("dedup_key") \
            or e.get("payload_sha256") or e.get("event_hash") \
            or f"anon-{e.get('sequence')}"
        d = deliveries.setdefault(did, {
            "delivery_id": did, "kind": e.get("kind") or e.get("evidence_type"),
            "event": ev, "target": e.get("target"),
            "action_index": e.get("action_index"),
            "iteration": e.get("iteration"),
            "delivery_ordinal": e.get("delivery_ordinal"),
            "request_id": e.get("request_id"),
            "lane": e.get("lane"), "blob": None, "blob_action_index": None,
            "rendered": None,
            "target_files": set(), "anchor": None, "anchor_source": None,
        })
        d["kind"] = d["kind"] or e.get("kind") or e.get("evidence_type")
        if d["action_index"] is None:
            d["action_index"] = e.get("action_index")
        if d["iteration"] is None:
            d["iteration"] = e.get("iteration")
        blob_rel = e.get("delivery_blob")
        if blob_rel and d["blob"] is None:
            d["blob"] = blob_rel

    # attach blob payloads (paths inside the shipped text count as targets)
    for d in deliveries.values():
        blob_rel = d.get("blob")
        if blob_rel:
            for sd in state_dirs:
                p = sd / blob_rel
                if p.is_file():
                    b = _parse_delivery_blob(p)
                    d["blob_action_index"] = b["action_index"]
                    d["kind"] = d["kind"] or b["kind"]
                    d["rendered"] = b["text"]
                    d["target_files"] |= b["files"]
                    break
        t = d.get("target")
        if isinstance(t, str) and t and t not in _NONFILE_TARGETS:
            d["target_files"] |= path_variants(t)

    # Blobs without a delivered event prove preparation, not delivery
    # (the deliveries dir is populated at prepare time; e.g. run
    # 34625781346 journals prepared_deliveries_discarded).  They are
    # counted as prepared_not_delivered, never as delivered.
    known = set(deliveries)
    for sd in state_dirs:
        ddir = sd / "deliveries"
        if not ddir.is_dir():
            continue
        for p in sorted(ddir.glob("*.json")):
            if p.stem in known:
                continue
            b = _parse_delivery_blob(p)
            if b["unit_id"] in known:
                continue
            known.add(b["unit_id"])
            prepared.add(b["unit_id"])

    # degenerate action_index detection: several delivered events with
    # distinct iterations but every action_index pinned to {0,1} -> the
    # producer stubbed the field (observed on run 33646776586).
    evt = [d for d in deliveries.values() if d["event"]]
    iters = {d["iteration"] for d in evt if d["iteration"] is not None}
    ais = {d["action_index"] for d in evt if d["action_index"] is not None}
    degenerate = len(iters) > 1 and ais and max(ais) <= 1

    for d in sorted(deliveries.values(),
                    key=lambda x: (x["delivery_ordinal"] or 10**6,
                                   str(x["delivery_id"]))):
        anchor, src = _resolve_anchor(d, degenerate, traj)
        d["anchor"] = anchor
        d["anchor_source"] = src
        d["target_files"] = sorted(d["target_files"])
    res = sorted(deliveries.values(),
                 key=lambda x: (x["anchor"] if x["anchor"] is not None else 10**9,
                                str(x["delivery_id"])))
    return res, len(prepared - set(deliveries)), refused


def _scan_tool_kinds(traj: dict) -> dict[str, list[int]]:
    """kind -> ordered action indexes whose tool RESULT contained the marker."""
    out: dict[str, list[int]] = {}
    action_no = 0
    pending_tool = 0
    for m in traj.get("messages") or []:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            n = len(m["tool_calls"])
            action_no += n
            pending_tool = n
        elif m.get("role") == "tool" and pending_tool:
            idx = action_no - pending_tool + 1
            pending_tool -= 1
            c = m.get("content") or ""
            if not isinstance(c, str):
                c = json.dumps(c)
            extra = m.get("extra") or {}
            raw = extra.get("raw_output")
            hay = c + ("\n" + raw if isinstance(raw, str) else "")
            for kind in set(re.findall(r"\[GT_EVIDENCE:([a-z_]+)\]", hay)):
                out.setdefault(kind, []).append(idx)
    return out


def _resolve_anchor(d: dict, degenerate: bool, traj: dict
                    ) -> tuple[int | None, str | None]:
    """First action index that could observe the delivery.

    action_index k (event or blob header) means "delivered after action k",
    so the first action that can see it is k+1.  When the producer stubbed
    action_index we locate the ``[GT_EVIDENCE:kind]`` marker in the tool
    observations themselves (trajectory_scan) - that marker rides the
    observation of action k, again first visible at k+1.  Last resort is
    ``iteration + 1``: on producers where the field is stubbed, iteration
    equals the carrying action's index (verified on run 33646776586).
    """
    if d.get("blob_action_index") is not None:
        return d["blob_action_index"] + 1, "blob_header"
    ai = d.get("action_index")
    it = d.get("iteration")
    if ai is not None and not degenerate and not (ai <= 1 and it and it >= 3):
        return ai + 1, "event_action_index"
    if d.get("kind"):
        pos = _scan_positions_for(traj, d["kind"])
        if pos is not None:
            return pos + 1, "trajectory_scan"
    if it is not None:
        return it + 1, "iteration_proxy"
    if ai is not None:
        return ai + 1, "event_action_index"
    return None, None


def _scan_positions_for(traj: dict, kind: str) -> int | None:
    kinds = traj.get("_gt_evidence_positions")
    if kinds is None:
        kinds = _scan_tool_kinds(traj)
        traj["_gt_evidence_positions"] = kinds
    positions = kinds.get(kind) or []
    for i, pos in enumerate(positions):
        if pos >= 0:
            positions[i] = -1  # consume; deliveries keep journal order
            return pos
    return None


# --------------------------------------------------------------------------
# provider funnel: SENT / VISIBLE / SERVED / AGENT-DID / CONSUMED
# (same chain gt_audit grades; offline evidence = journals + request blobs)
# --------------------------------------------------------------------------
_REQUEST_ITER_RE = re.compile(r"request-(\d+)-")


def _provider_maps(events: list[dict]) -> tuple[dict[int, dict],
                                              dict[str, dict],
                                              dict[int, dict]]:
    """iteration -> request row, request_id -> response row,
    iteration -> response row.  ``provider_delivery`` rows carry the
    iteration directly; ``provider_request`` receipts embed it in
    ``request-N-<sha>`` request ids."""
    reqs: dict[int, dict] = {}
    resps: dict[str, dict] = {}
    resps_it: dict[int, dict] = {}
    for e in events:
        ev = e.get("event")
        if ev in _REQUEST_EVENTS:
            it = e.get("iteration")
            if not isinstance(it, int) or isinstance(it, bool):
                m = _REQUEST_ITER_RE.match(str(e.get("request_id") or ""))
                it = int(m.group(1)) if m else None
            if it is not None:
                reqs.setdefault(it, e)
        elif ev in _RESPONSE_EVENTS:
            rid = str(e.get("request_id") or "")
            if rid:
                resps.setdefault(rid, e)
            it = e.get("iteration")
            if not isinstance(it, int) or isinstance(it, bool):
                m = _REQUEST_ITER_RE.match(rid)
                it = int(m.group(1)) if m else None
            if it is not None:
                resps_it.setdefault(it, e)
    return reqs, resps, resps_it


def _response_action_map(traj: dict) -> dict[str, int]:
    """provider response id -> first action index of the assistant turn."""
    out: dict[str, int] = {}
    idx = 0
    for m in traj.get("messages") or []:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            first = idx + 1
            idx += len(m["tool_calls"])
            rid = ((m.get("extra") or {}).get("response") or {})
            rid = rid.get("id") if isinstance(rid, dict) else None
            if rid:
                out.setdefault(str(rid), first)
        elif m.get("role") == "assistant":
            rid = ((m.get("extra") or {}).get("response") or {})
            rid = rid.get("id") if isinstance(rid, dict) else None
            if rid:
                out.setdefault(str(rid), idx + 1)
    return out


def _request_blob_messages(state_dirs: list[Path], row: dict | None,
                           cache: dict) -> list | None:
    """Model-visible ``messages`` of one provider request blob."""
    if not row:
        return None
    key = row.get("request_blob")
    if not key:
        return None
    if key in cache:
        return cache[key]
    msgs = None
    for sd in state_dirs:
        p = sd / str(key)
        if not p.is_file():
            continue
        try:
            j = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError, UnicodeError):
            continue
        cand = j.get("messages") if isinstance(j, dict) else None
        if isinstance(cand, list):
            msgs = [m for m in cand if isinstance(m, dict)]
            break
    cache[key] = msgs
    return msgs


def _contains_text(value: object, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value
    if isinstance(value, dict):
        return any(_contains_text(v, needle) for v in value.values())
    if isinstance(value, list):
        return any(_contains_text(v, needle) for v in value)
    return False


def _gt_blocks_in(text: str) -> list[tuple[str, str]]:
    """Every ``[GT_*]``-headed block in one message: (marker, block_text)."""
    out: list[tuple[str, str]] = []
    for m in _GT_MARKER_RE.finditer(text):
        start = m.start()
        tail = text[start:start + 6000]
        endm = re.search(r"</gt-facts>", tail)
        end = endm.end() if endm else len(tail)
        out.append((m.group(0), tail[:end]))
    return out


def _extract_gt_block(messages: list, kind: str | None,
                      target_tokens: set[str],
                      prompt_lane: bool = False
                      ) -> tuple[str | None, str | None, str | None]:
    """Locate the shipped GT block inside a request's messages.

    Returns (evidence_label, block_text, marker_text):
      * ``marker`` - a [GT_*] block tied to THIS delivery: right
        ``[GT_EVIDENCE:kind]`` section containing a target token, or any GT
        block for prompt-lane / kindless deliveries.
      * ``marker_any`` - GT markers exist in the request but no block could
        be tied to this delivery's target (marker persists in history; only
        sealed bytes would attribute exactly).
      * None - no GT marker in the request at all.
    """
    # Marker spellings differ across producers: most sealed kinds ship
    # ``[GT_EVIDENCE:<kind>]``; prompt/contract kinds ship their own heads
    # ([GT_TASK_CONTRACT], [GT_OBLIGATION_DELTA]) and execution_evidence
    # ships [GT_EXECUTION_EVIDENCE].  Accept both spellings per kind.
    wanted: set[str] | None = None
    if kind:
        wanted = {f"[GT_EVIDENCE:{kind}]"}
        alt = _GT_KIND_MARKER.get(kind)
        if alt:
            wanted.add(alt)
    generic = False
    for m in messages:
        c = m.get("content")
        if not isinstance(c, str):
            c = json.dumps(c)
        blocks = _gt_blocks_in(c)
        if not blocks:
            continue
        for marker_text, block in blocks:
            if wanted and marker_text not in wanted:
                continue
            if (wanted is None and prompt_lane
                    and _SEALED_MARKER_RE.match(marker_text)):
                continue  # sealed-evidence markers can't tie prompt lane
            if not target_tokens:
                return "marker", block, marker_text
            if any(token_word_hit(block, {t}) for t in target_tokens):
                return "marker", block, marker_text
            generic = True  # right kind / other block, wrong target
        if wanted:
            # file-target delivery: a block of a DIFFERENT kind naming the
            # target is weak evidence only
            for marker_text, block in blocks:
                if any(token_word_hit(block, {t}) for t in target_tokens):
                    generic = True
        else:
            generic = generic or bool(blocks)
    return (("marker_any", None, None) if generic else (None, None, None))


def _count_kind_blocks(messages: list, kind: str | None,
                       prompt_lane: bool) -> int:
    """How many marker blocks of this delivery's kind a request carries.

    Same-kind GT blocks accumulate in observation history, so for
    indistinguishable repeated kinds (context_delta etc.) the request that
    served instance N is the first whose block count reaches N."""
    wanted: set[str] | None = None
    if kind:
        wanted = {f"[GT_EVIDENCE:{kind}]"}
        alt = _GT_KIND_MARKER.get(kind)
        if alt:
            wanted.add(alt)
    n = 0
    for m in messages:
        c = m.get("content")
        if not isinstance(c, str):
            c = json.dumps(c)
        for marker_text, _block in _gt_blocks_in(c):
            if wanted and marker_text not in wanted:
                continue
            if prompt_lane and _SEALED_MARKER_RE.match(marker_text):
                continue
            n += 1
    return n


def _delivery_funnel(d: dict, anchor: int | None, actions: list,
                     reqs: dict, resps: dict, resps_it: dict,
                     resp_action: dict, state_dirs: list[Path],
                     blob_cache: dict, req_cursor: dict,
                     req_seq: dict) -> dict:
    """SENT->VISIBLE->SERVED->AGENT-DID->CONSUMED for one delivery.

    anchor = first action index that could observe the delivery per the
    journal fields (same convention as _resolve_anchor).  Those fields
    drift +/-1 across producers (carrying-observation index on some tasks,
    serving-request index on others - observed on run 33646776586), so the
    request blobs are ground truth for VISIBLE: when the anchored request
    cannot be tied to this delivery, requests are scanned in order past a
    per-kind cursor until a request containing the tied marker/bytes is
    found.  The serving request is the one carrying the content; its
    provider response's action is the effective anchor.
    """
    f: dict = {"anchor": anchor, "sent": False, "visible": False,
               "visible_evidence": None, "served": False,
               "agent_did": False, "response_action": None,
               "consumed": False, "match": "", "prior_touches": 0,
               "verdict": "unanchored"}
    if anchor is None:
        return f
    target = d.get("target") if isinstance(d.get("target"), str) else ""
    # empty target means "no target recorded", not prompt lane - sealed
    # kinds like execution_evidence journal target="" while riding obs
    prompt_lane = (d.get("lane") == "prompt"
                   or (target and target in _NONFILE_TARGETS))
    target_tokens = set(d.get("target_files") or [])
    if not target_tokens and target and not prompt_lane:
        target_tokens = set(path_variants(target))
    kind = d.get("kind")
    dkey = kind or "_any"
    # only the sealed payload counts as ``bytes`` evidence; a marker block
    # extracted from one request must never re-match as bytes on another
    sealed = d.get("rendered")

    def tie(msgs) -> tuple[str | None, str | None, str | None]:
        if sealed and _contains_text(msgs, sealed):
            mts = _gt_blocks_in(sealed)
            return "bytes", None, (mts[0][0] if mts else None)
        return _extract_gt_block(msgs, kind, target_tokens,
                                 prompt_lane=prompt_lane)

    serving_it: int | None = None
    marker_text = None
    anchored_ev: str | None = None
    req0 = reqs.get(anchor)
    if req0 is not None:
        f["sent"] = True
        f["request_id"] = str(req0.get("request_id") or "")
        f["sent_evidence"] = ("delivery_ids"
                              if isinstance(req0.get("delivery_ids"), list)
                              else "boundary_request")
        msgs0 = _request_blob_messages(state_dirs, req0, blob_cache)
        if msgs0 is None:
            f["visible_evidence"] = "request_blob_unreadable"
        else:
            ev, block, marker_text = tie(msgs0)
            if ev:
                anchored_ev = ev
                if block and not d.get("rendered"):
                    d["rendered"] = block
    # Serving request = earliest request past the per-kind cursor that
    # carries this delivery's block.  Field anchors only hint; the request
    # bytes are ground truth and drift goes both directions across
    # producers (carrying-obs vs serving-request iteration conventions).
    #
    # Pass 1 requires the tied block in the request's LAST tool message:
    # marker blocks persist inside older observations for the rest of the
    # trajectory, so for a repeated kind an anywhere-match would tie
    # instance N to a request still carrying instance N-1's stale block.
    # The carrying observation is the newest message exactly once - in the
    # serving request.  Pass 2 is the anywhere-fallback: for kinds that
    # never ride observations ([GT_TASK_CONTRACT] in the user message) and
    # for first-of-kind deliveries the earliest anywhere-match IS the
    # arrival; for repeated indistinguishable kinds (no target tokens) it
    # additionally requires the request to contain >= seq same-kind blocks
    # (instances only ever add blocks until compaction).  A repeated
    # file-targeted kind resolved this way is labeled marker_persisted -
    # its target's block persists, so instance attribution is approximate.
    start = req_cursor.get(dkey)
    seq = req_seq.get(dkey, 1)
    ordered = [it2 for it2 in sorted(reqs)
               if start is None or it2 > start]

    def _resolve(it2: int, msgs2, ev2: str, block2, mt2,
                 label: str) -> None:
        nonlocal serving_it, marker_text
        serving_it = it2
        f["visible"] = True
        f["visible_evidence"] = label
        marker_text = mt2
        if it2 != anchor:
            f["visible_anchor_offset"] = it2 - anchor
        if block2 and not d.get("rendered"):
            d["rendered"] = block2

    for it2 in ordered:  # pass 1: tied block in the last tool message
        msgs2 = _request_blob_messages(state_dirs, reqs[it2],
                                       blob_cache)
        if msgs2 is None:
            continue
        lt = next((m for m in reversed(msgs2)
                   if m.get("role") == "tool"), None)
        if lt is None:
            continue
        ev2, block2, mt2 = tie([lt])
        if ev2 in ("marker", "bytes"):
            _resolve(it2, msgs2, ev2, block2, mt2, ev2)
            break
    if serving_it is None:
        for it2 in ordered:  # pass 2: anywhere in the request
            msgs2 = _request_blob_messages(state_dirs, reqs[it2],
                                           blob_cache)
            if msgs2 is None:
                continue
            ev2, block2, mt2 = tie(msgs2)
            if ev2 not in ("marker", "bytes"):
                continue
            label = ev2
            if target_tokens:
                if seq > 1:
                    label = "marker_persisted"
            elif seq > 1:
                # indistinguishable same-kind markers: the request serving
                # instance N is where the count of same-kind blocks
                # reaches N
                if _count_kind_blocks(msgs2, kind,
                                      prompt_lane) < seq:
                    continue
            _resolve(it2, msgs2, ev2, block2, mt2, label)
            break
    if serving_it is None and anchored_ev is not None:
        # anchored request tied but sits at/behind the kind cursor (a prior
        # same-kind delivery overshot) - still the best evidence we have
        serving_it = anchor
        f["visible"] = True
        f["visible_evidence"] = anchored_ev
    if serving_it is not None:
        prev = req_cursor.get(dkey)
        if prev is None or serving_it > prev:
            req_cursor[dkey] = serving_it
        sreq = reqs.get(serving_it)
        if sreq is not None:
            f["serving_iteration"] = serving_it
            f["request_id"] = str(sreq.get("request_id")
                                  or f.get("request_id") or "")
            if not f["sent"]:
                f["sent"] = True
                f["sent_evidence"] = "marker_scan"
        # stronger evidence: marker text absent from the previous request -
        # the delivery genuinely arrived at serving_it
        if marker_text:
            prev_msgs = _request_blob_messages(
                state_dirs, reqs.get(serving_it - 1), blob_cache) \
                if reqs.get(serving_it - 1) is not None else None
            if prev_msgs is not None and not _contains_text(
                    prev_msgs, marker_text):
                f["visible_evidence"] = "marker_new"
    if not f["sent"]:
        if actions and anchor > max(a.index for a in actions):
            f["verdict"] = "delivered_after_last_action"
        else:
            f["verdict"] = "not_sent"
        return f
    if not f["visible"]:
        f["verdict"] = "sent_not_visible"
        # still allow served/consumed grading below for diagnosis
    resp = resps.get(f.get("request_id") or "") \
        or resps_it.get(serving_it or anchor)
    if resp is None:
        f["verdict"] = ("delivered_not_served" if f["visible"]
                        else "sent_not_visible")
        return f
    pid = str(resp.get("provider_response_id")
              or resp.get("response_id") or "")
    f["served"] = True
    act_idx = resp_action.get(pid) if pid else None
    if act_idx is None:
        f["verdict"] = "served_response_not_in_trajectory"
        return f
    f["agent_did"] = True
    f["response_action"] = act_idx

    # Effective anchor: the action produced by the serving request is the
    # first action that could observe the delivered content.  The journal's
    # ``iteration``/``action_index`` fields are inconsistent across
    # producers (carrying-observation index on some tasks, serving-request
    # index on others - observed +/-1 drift on run 33646776586), so the
    # provider join, not the field arithmetic, sets the real boundary.
    eff_anchor = act_idx if act_idx is not None else anchor
    f["effective_anchor"] = eff_anchor

    tokens = delivery_content_tokens(
        str(d.get("delivery_id") or ""), target, d.get("rendered"))
    tokens |= {t for t in target_tokens if len(t) >= 4}
    touch_tokens = set()
    if target and not prompt_lane:
        touch_tokens = {target, target.rsplit("/", 1)[-1]}
    elif target_tokens:
        touch_tokens = {t.rsplit("/", 1)[-1] for t in target_tokens} | set(
            target_tokens)
    if touch_tokens:
        for a in actions:
            if a.index < eff_anchor \
                    and token_word_hit(a.command, touch_tokens):
                f["prior_touches"] += 1
    matched = ""
    for a in actions:
        if eff_anchor <= a.index < eff_anchor + CONSUME_TURNS:
            hit = token_word_hit(a.command, tokens)
            if hit:
                matched = hit
                f["consumed_at"] = a.index
                break
    f["consumed"] = bool(matched)
    f["match"] = matched
    if not f["visible"]:
        f["verdict"] = "sent_not_visible"
    elif matched:
        f["verdict"] = "consumed"
    else:
        f["verdict"] = "seen_no_action_on_content"
    return f


# --------------------------------------------------------------------------
# per-step timeline
# --------------------------------------------------------------------------
def _relevance_label(a: "Action", gold: set[str], G: set[str]) -> str:
    targets = a.view_files | a.edit_files | a.search_targets
    if any(_targets_core(t, set(gold)) for t in targets):
        return "gold"
    if any(_targets_core(t, G) for t in targets):
        return "closure"
    if targets:
        return "non_core"
    if a.is_exploration:
        return "unscoped"
    return "none"


def _build_timeline(actions: list, gold: set[str], G: set[str],
                    deliveries: list[dict]) -> list[dict]:
    """step -> action -> relevance label -> GT attribution."""
    delivered_at: dict[int, list[str]] = {}
    consumed_at: dict[int, list[dict]] = {}
    for d in deliveries:
        f = d.get("funnel") or {}
        eff = f.get("effective_anchor") or f.get("anchor")
        if eff:
            delivered_at.setdefault(eff, []).append(
                str(d.get("delivery_id"))[:16])
        if f.get("consumed_at"):
            consumed_at.setdefault(f["consumed_at"], []).append({
                "delivery": str(d.get("delivery_id"))[:16],
                "class": d.get("consumption_class"),
                "match": f.get("match")})
    rows = []
    for a in actions:
        kinds = [k for k, fl in (("view", a.is_view), ("search", a.is_search),
                                 ("edit", a.is_edit), ("check", a.is_check),
                                 ("submit", a.is_submit)) if fl]
        rows.append({
            "step": a.index,
            "kinds": kinds or ["other"],
            "cmd": a.command[:160],
            "targets": sorted(a.view_files | a.edit_files
                              | a.search_targets)[:20],
            "relevance": _relevance_label(a, gold, G),
            "returncode": a.returncode,
            "gt_delivered": delivered_at.get(a.index) or None,
            "gt_consumed": consumed_at.get(a.index) or None,
        })
    return rows


# --------------------------------------------------------------------------
# held-out split + baseline (descriptive comparison only)
# --------------------------------------------------------------------------
def load_splits(spec: str | None, tuning_csv: str | None,
                eval_csv: str | None) -> tuple[dict[str, set[str]], list[str]]:
    """Explicit task split: {"tuning": {...}, "evaluation": {...}}.

    Sources: --splits <json file> with those keys, and/or --tuning-tasks /
    --eval-tasks comma lists.  A task in both is a conflict (reported, never
    silently resolved)."""
    splits = {"tuning": set(), "evaluation": set()}
    issues: list[str] = []
    if spec:
        p = Path(spec)
        if p.is_file():
            try:
                j = json.loads(p.read_text(encoding="utf-8",
                                           errors="replace"))
                for k in ("tuning", "evaluation"):
                    v = j.get(k)
                    if isinstance(v, list):
                        splits[k] |= {str(x) for x in v}
                    elif v is not None:
                        issues.append(f"splits file: '{k}' is not a list")
            except (json.JSONDecodeError, OSError) as ex:
                issues.append(f"splits file unreadable: {ex!r}")
        else:
            issues.append(f"splits file not found: {spec}")
    for csv_val, key in ((tuning_csv, "tuning"), (eval_csv, "evaluation")):
        if csv_val:
            splits[key] |= {t.strip() for t in csv_val.split(",")
                            if t.strip()}
    conflict = splits["tuning"] & splits["evaluation"]
    for t in sorted(conflict):
        issues.append(f"task in BOTH splits: {t}")
    return splits, issues


def split_for(task_id: str | None, splits: dict[str, set[str]]) -> str:
    if task_id and task_id in splits["tuning"] & splits["evaluation"]:
        return "conflict"
    if task_id and task_id in splits["tuning"]:
        return "tuning"
    if task_id and task_id in splits["evaluation"]:
        return "evaluation"
    return "unassigned"


def typical_path_similarity(viewed: set[str],
                            baseline_viewed: set[str]) -> dict | None:
    """DISTINCT-LABEL metric: similarity of this trial's viewed-file path to
    what baseline trajectories happened to inspect.  NEVER merged into
    recall/precision - those are anchored on the gold closure, while this is
    descriptive overlap with a survivorship-biased reference."""
    if not baseline_viewed:
        return None
    inter = viewed & baseline_viewed
    union = viewed | baseline_viewed
    return {"jaccard": (len(inter) / len(union)) if union else None,
            "overlap_files": len(inter),
            "trial_viewed": len(viewed),
            "baseline_viewed": len(baseline_viewed)}


def baseline_viewed_files(traj: dict) -> set[str]:
    """Files a baseline trajectory touched (views/edits), normalized."""
    files: set[str] = set()
    for a in parse_trajectory(traj):
        for f in a.view_files | a.edit_files:
            files |= path_variants(f)
    return {f for f in files if f}


def load_baseline_trajectories(root: Path) -> dict[str, set[str]]:
    """task_id -> union of files viewed across that task's baseline trials.

    Accepts a dir of ``<task>__*`` trial dirs (each holding
    agent/miniswe_trajectory.json) or ``<task>.json`` files carrying
    {"viewed_files": [...]} or a raw trajectory object."""
    out: dict[str, set[str]] = {}
    root = Path(root)
    if not root.is_dir():
        return out
    for child in sorted(root.iterdir()):
        task_id = child.name.split("__", 1)[0]
        if child.is_dir():
            for name in ("agent/miniswe_trajectory.json",
                         "miniswe_trajectory.json",
                         "agent/gt-run.trajectory.json"):
                p = child / name
                if p.is_file():
                    try:
                        tj = json.loads(p.read_text(
                            encoding="utf-8", errors="replace"))
                    except (json.JSONDecodeError, OSError):
                        continue
                    out.setdefault(task_id, set())
                    out[task_id] |= baseline_viewed_files(tj)
                    break
        elif child.suffix == ".json":
            try:
                j = json.loads(child.read_text(encoding="utf-8",
                                               errors="replace"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(j, dict) and isinstance(j.get("viewed_files"),
                                                list):
                out.setdefault(child.stem, set())
                out[child.stem] |= {_norm_path(str(v))
                                    for v in j["viewed_files"]}
            elif isinstance(j, dict) and isinstance(j.get("messages"), list):
                out.setdefault(child.stem, set())
                out[child.stem] |= baseline_viewed_files(j)
    return {k: {v for v in vs if v} for k, vs in out.items()}


def load_baseline_outcomes(path: Path) -> tuple[dict[str, dict],
                                              dict, str | None]:
    """GT-off outcome reference (descriptive only, never causal).

    Accepted shapes:
      * smoke20 comparison JSON (``per_task`` rows with baseline_passes /
        baseline_trials / baseline_four_trial_pass_rate)
      * DEEPSWE_EVALUATION_RESULTS-style list or {"results": [...]} of
        {task|task_name|task_id, solved|passed|reward}
      * Muse bundle dir (SUMMARY.json + *per-task-comparison*.json)
    Returns (per_task {task_id: {...}}, summary_meta, error)."""
    path = Path(path)
    if not path.exists():
        return {}, {}, f"baseline path not found: {path}"
    docs: list[dict] = []
    if path.is_dir():
        for p in sorted(path.glob("*per-task-comparison*.json")) + \
                sorted(path.glob("*per_task*.json")):
            try:
                docs.append(json.loads(p.read_text(
                    encoding="utf-8", errors="replace")))
            except (json.JSONDecodeError, OSError):
                continue
        if not docs and (path / "SUMMARY.json").is_file():
            try:
                docs.append(json.loads(
                    (path / "SUMMARY.json").read_text(encoding="utf-8",
                                                      errors="replace")))
            except (json.JSONDecodeError, OSError):
                pass
    else:
        try:
            docs.append(json.loads(path.read_text(encoding="utf-8",
                                                  errors="replace")))
        except (json.JSONDecodeError, OSError) as ex:
            return {}, {}, f"baseline unreadable: {ex!r}"
    per_task: dict[str, dict] = {}
    meta: dict = {"source": str(path)}
    for j in docs:
        if not isinstance(j, (dict, list)):
            continue
        if isinstance(j, dict) and isinstance(j.get("baseline"), dict):
            meta["baseline_summary"] = {
                k: j["baseline"].get(k) for k in
                ("pass_rate", "passes", "failures", "task_count",
                 "trial_count", "schema", "source_locator")}
        rows = []
        if isinstance(j, dict):
            for key in ("per_task", "results", "tasks", "trials"):
                if isinstance(j.get(key), list):
                    rows = j[key]
                    break
            else:
                if isinstance(j.get("per_task"), dict):
                    rows = [{"task": k, **v} if isinstance(v, dict)
                            else {"task": k}
                            for k, v in j["per_task"].items()]
        elif isinstance(j, list):
            rows = j
        for r in rows:
            if not isinstance(r, dict):
                continue
            tid = (r.get("task") or r.get("task_name") or r.get("task_id")
                   or r.get("name"))
            if isinstance(tid, dict):
                tid = tid.get("path")
            if not isinstance(tid, str) or not tid:
                continue
            tid = tid.rstrip("/").split("/")[-1]
            rec = per_task.setdefault(tid, {})
            for src, dst in (("baseline_passes", "gt_off_passes"),
                             ("baseline_trials", "gt_off_trials"),
                             ("baseline_four_trial_pass_rate",
                              "gt_off_rate"),
                             ("passes", "gt_off_passes"),
                             ("trials", "gt_off_trials"),
                             ("pass_rate", "gt_off_rate"),
                             ("solved", "gt_off_solved"),
                             ("passed", "gt_off_passes"),
                             ("reward", "gt_off_reward")):
                if dst not in rec and isinstance(r.get(src),
                                               (int, float, bool)):
                    rec[dst] = r[src]
    return per_task, meta, None


# --------------------------------------------------------------------------
# metric computation
# --------------------------------------------------------------------------
def evaluate_trial(trial: Path, tasks_root: Path,
                   split: str = "unassigned",
                   baseline_viewed: set[str] | None = None,
                   baseline_outcome: dict | None = None) -> dict:
    trial = Path(trial)
    missing: list[str] = []
    out: dict = {"schema": SCHEMA, "trial_dir": str(trial),
                 "trial_name": trial.name}

    # ---- task id ----------------------------------------------------------
    task_id = _resolve_task_id(trial)
    out["task_id"] = task_id
    out["split"] = split  # held-out discipline: tuning vs evaluation

    # ---- gold + graph (relevance oracle: patch + closure ONLY) -------------
    state_dirs = find_state_dirs(trial)
    graph_path = find_graph(state_dirs, trial)
    tasks_root = Path(tasks_root)
    task_dir = tasks_root / task_id if task_id else tasks_root / "__missing__"
    closure = build_gold_closure(task_dir, graph_path)
    gold = set(closure["gold_files"])
    gold_ok = bool(closure["gold_ok"])
    out["gold_files"] = sorted(gold)
    out["gold_closure"] = {
        "mode": closure.get("mode"),
        "symbol_level": closure.get("symbol_level"),
        "closure_edge_types": closure.get("closure_edge_types"),
        "changed_symbols": closure.get("changed_symbols"),
        "files_detail": closure.get("files_detail"),
    }
    if not gold_ok:
        missing.append("gold_solution_patch" if task_id else "task_id")

    # keep the legacy field name for continuity: it is the closure file set
    neighborhood: set[str] | None = (
        set(closure["closure_files"])
        if closure.get("mode") in ("symbol", "file") else None)
    graph_ok = neighborhood is not None
    out["graph_neighborhood_available"] = bool(graph_ok)
    out["graph_neighborhood"] = sorted(neighborhood) if graph_ok else (
        [] if graph_path else None)
    if not graph_path:
        missing.append("graph_db")
    elif closure.get("mode") not in ("symbol", "file"):
        missing.append("graph_closure_" + str(closure.get("mode")))
    G = set(gold) | (neighborhood or set())
    out["G_i"] = sorted(G)
    out["G_i_size"] = len(G)

    # ---- trajectory -------------------------------------------------------
    traj_path = None
    for name in ("miniswe_trajectory.json", "gt-run.trajectory.json"):
        p = trial / "agent" / name
        if p.is_file():
            traj_path = p
            break
    traj = None
    if traj_path:
        try:
            traj = json.loads(traj_path.read_text(encoding="utf-8",
                                                  errors="replace"))
        except json.JSONDecodeError:
            missing.append("trajectory_unparseable")
    else:
        missing.append("trajectory")
    actions = parse_trajectory(traj) if isinstance(traj, dict) else []
    out["steps_total"] = len(actions) if actions else (0 if traj else None)

    # ---- events -----------------------------------------------------------
    # a trial may carry several gt-state dirs (one holds events.jsonl,
    # another graph.db); use the largest journal found.  The provider
    # receipt journal (provider_events.jsonl) is merged in - it carries the
    # same request/response chain under a different request_id namespace.
    events: list[dict] = []
    provider_events: list[dict] = []
    for sd in state_dirs:
        ev = load_events(sd)
        if len(ev) > len(events):
            events = ev
        pe = sd / "provider_events.jsonl"
        if pe.is_file():
            for line in pe.read_text(encoding="utf-8",
                                     errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(e, dict):
                    provider_events.append(e)
    if not events:
        missing.append("events_jsonl")

    # typed file views (none in the runs audited so far; honored if present)
    typed_views: list[tuple[int, set[str]]] = []
    for e in events:
        if e.get("event") in _VIEW_EVENTS:
            idx = e.get("action_index") or e.get("action_id")
            paths = _event_paths(e)
            try:
                idx = int(idx) if idx is not None else None
            except (TypeError, ValueError):
                idx = None
            if idx is not None and paths:
                typed_views.append((idx, paths))
    use_typed_views = bool(typed_views)

    # typed edits -> merge into actions (authoritative file sets)
    first_edit_typed: tuple[int, set[str]] | None = None
    for e in events:
        if e.get("event") == "edit_transaction":
            idx = e.get("action_index") or e.get("action_id")
            changed = {_norm_path(c) for c in (e.get("changed_paths") or [])}
            try:
                idx = int(idx) if idx is not None else None
            except (TypeError, ValueError):
                idx = None
            if idx is None:
                continue
            if first_edit_typed is None or idx < first_edit_typed[0]:
                first_edit_typed = (idx, changed)
            if 1 <= idx <= len(actions):
                actions[idx - 1].is_edit = True
                actions[idx - 1].edit_files |= changed

    if use_typed_views:
        for idx, paths in typed_views:
            if 1 <= idx <= len(actions):
                actions[idx - 1].is_view = True
                actions[idx - 1].view_files |= paths

    # ---- view sequence ----------------------------------------------------
    viewed_before_edit: set[str] = set()
    seen: set[str] = set()
    revisits = 0
    view_events = 0
    ttfc = None
    first_core_file = None
    exploration_pre_core = 0
    non_core_pre_core = 0
    unscoped_searches = 0

    first_edit_shell = next(
        ((a.index, set(a.edit_files)) for a in actions if a.is_edit), None)
    if first_edit_typed and first_edit_shell:
        first_edit_step = min(first_edit_typed[0], first_edit_shell[0])
        first_edit_files = (first_edit_typed[1]
                            if first_edit_typed[0] <= first_edit_shell[0]
                            else first_edit_shell[1])
        first_edit_source = ("edit_transaction"
                             if first_edit_typed[0] <= first_edit_shell[0]
                             else "shell_parse")
    elif first_edit_typed:
        first_edit_step, first_edit_files = first_edit_typed
        first_edit_source = "edit_transaction"
    elif first_edit_shell:
        first_edit_step, first_edit_files = first_edit_shell
        first_edit_source = "shell_parse"
    else:
        first_edit_step, first_edit_files, first_edit_source = None, set(), None

    for a in actions:
        # TTFC = first action touching the gold closure by ANY means:
        # viewing a file, searching a scoped target, or editing it.  A dir
        # target counts when its subtree holds G files (ls tests/).
        hits = {match_variant(v, G) for v in a.view_files | a.edit_files}
        hits.discard(None)
        for t in a.search_targets:
            mv = match_variant(t, G)
            if mv:
                hits.add(mv)
            elif _targets_core(t, G):
                pv = sorted(path_variants(t))
                if pv:
                    hits.add(pv[0])
        if a.is_view:
            for v in a.view_files:
                view_events += 1
                pv = sorted(path_variants(v))
                mv = match_variant(v, G) or (pv[0] if pv else v)
                if mv in seen:
                    revisits += 1
                seen.add(mv)
        if first_edit_step is None or a.index < first_edit_step:
            viewed_before_edit |= {
                match_variant(v, G)
                or (sorted(path_variants(v)) or [v])[0]
                for v in a.view_files}
        if ttfc is None and hits:
            ttfc = a.index
            first_core_file = sorted(hits)[0]
        if a.is_exploration and (ttfc is None or a.index < ttfc):
            exploration_pre_core += 1
            targets = a.view_files | a.search_targets
            if targets:
                if not any(_targets_core(t, G) for t in targets):
                    non_core_pre_core += 1
            else:
                unscoped_searches += 1

    out["ttfc"] = ttfc
    out["first_core_file"] = first_core_file
    if actions:
        vb = viewed_before_edit
        out["preedit_viewed"] = len(vb)
        out["preedit_recall"] = (len({v for v in vb if v in G}) / len(G)
                                 if G else None)
        out["preedit_precision"] = (len({v for v in vb if v in G}) / len(vb)
                                    if vb else None)
        out["viewed_files_preedit"] = sorted(vb)
        out["revisit_rate"] = revisits / view_events if view_events else None
        out["revisits"] = revisits
        out["view_file_events"] = view_events
    else:
        out["preedit_viewed"] = None
        out["preedit_recall"] = None
        out["preedit_precision"] = None
        out["viewed_files_preedit"] = None
        out["revisit_rate"] = None
        out["revisits"] = None
        out["view_file_events"] = None
        if traj is not None:
            missing.append("actions_in_trajectory")

    out["first_edit_step"] = first_edit_step
    out["first_edit_files"] = sorted(first_edit_files)
    out["first_edit_source"] = first_edit_source
    if first_edit_step is None:
        out["first_edit_in_gold"] = None
        out["first_edit_in_G"] = None
        if actions:
            missing.append("edit_action")
    elif not first_edit_files:
        out["first_edit_in_gold"] = None
        out["first_edit_in_G"] = None
        missing.append("first_edit_file_unresolved")
    else:
        out["first_edit_in_gold"] = any(
            in_fileset(f, set(gold)) for f in first_edit_files) \
            if gold else None
        out["first_edit_in_G"] = any(
            in_fileset(f, G) for f in first_edit_files) if G else None

    out["non_core_exploration"] = {
        "label": "non_core",
        "exploration_actions_before_core": exploration_pre_core if actions
        else None,
        "non_core": non_core_pre_core if actions else None,
        "ratio": (non_core_pre_core / exploration_pre_core
                  if actions and exploration_pre_core else None),
        "unscoped_searches": unscoped_searches if actions else None,
        "note": "outside-G_i is not strictly useless; honest naming",
    }

    # ---- recovery ---------------------------------------------------------
    out["recovery"] = _compute_recovery(actions, events)

    # ---- gt_utilization ----------------------------------------------------
    deliveries, prepared_only, refused_n = collect_deliveries(
        events, state_dirs, traj if isinstance(traj, dict) else {"messages": []})
    reqs, resps, resps_it = _provider_maps(events + provider_events)
    resp_action = _response_action_map(
        traj if isinstance(traj, dict) else {"messages": []})
    blob_cache: dict = {}
    req_cursor: dict = {}  # kind -> last request iteration that served it
    req_seq: dict = {}     # kind -> per-task delivery instance counter
    util = {
        "deliveries_total": len(deliveries),
        "file_targeted": 0, "acted_within_5": 0, "led_new": 0,
        "confirmed_only": 0, "not_used": 0, "no_file_target": 0,
        "unanchored": 0, "prepared_not_delivered": prepared_only,
        "refused": refused_n, "k_window": K_WINDOW,
        # funnel (gt_audit join): SENT/VISIBLE/SERVED/AGENT-DID/CONSUMED
        "consume_turns": CONSUME_TURNS,
        "sent": 0, "visible": 0, "served": 0, "agent_did": 0,
        "consumed": 0, "consumed_fair": 0, "confirmed_consumed": 0,
        "deliveries": [],
    }
    for d in deliveries:
        targets = set(d["target_files"])
        anchor = d["anchor"]
        rec = {"delivery_id": d["delivery_id"], "kind": d["kind"],
               "event": d["event"], "lane": d["lane"], "anchor": anchor,
               "anchor_source": d["anchor_source"],
               "target_files": sorted(targets)}
        dkey = d.get("kind") or "_any"
        req_seq[dkey] = req_seq.get(dkey, 0) + 1
        funnel = _delivery_funnel(d, anchor, actions, reqs, resps,
                                  resps_it, resp_action, state_dirs,
                                  blob_cache, req_cursor, req_seq)
        rec["funnel"] = {k: v for k, v in funnel.items()}
        rec["effective_anchor"] = funnel.get("effective_anchor")
        for k in ("sent", "visible", "served", "agent_did", "consumed"):
            if funnel.get(k):
                util[k] += 1
        if funnel.get("visible_evidence"):
            util.setdefault("visible_evidence", {})
            vev = util["visible_evidence"]
            vev[funnel["visible_evidence"]] = \
                vev.get(funnel["visible_evidence"], 0) + 1
        if funnel["consumed"] and funnel["prior_touches"] == 0:
            rec["consumption_class"] = "led"
            util["consumed_fair"] += 1
        elif funnel["consumed"]:
            rec["consumption_class"] = "confirmed"
            util["confirmed_consumed"] += 1
        elif funnel["verdict"] in ("consumed",):
            rec["consumption_class"] = None
        else:
            rec["consumption_class"] = funnel["verdict"]
        if not targets:
            rec["bucket"] = "no_file_target"
            util["no_file_target"] += 1
            rec["acted_within_5"] = None
            rec["previously_touched"] = None
        elif anchor is None:
            rec["bucket"] = "unanchored"
            util["unanchored"] += 1
            rec["acted_within_5"] = None
            rec["previously_touched"] = None
        else:
            util["file_targeted"] += 1
            # K-window measured from the effective (provider-join) anchor,
            # falling back to the field-derived anchor when unserved
            eff = funnel.get("effective_anchor") or anchor
            prev = any(in_fileset(t, a.touched)
                       for a in actions if a.index < eff
                       for t in targets)
            acted = any(in_fileset(t, a.touched)
                        for a in actions
                        if eff <= a.index < eff + K_WINDOW
                        for t in targets)
            rec["previously_touched"] = prev
            rec["acted_within_5"] = acted
            if acted:
                util["acted_within_5"] += 1
            if prev:
                rec["bucket"] = "confirmed_only"
                util["confirmed_only"] += 1
            elif acted:
                rec["bucket"] = "led_new"
                util["led_new"] += 1
            else:
                rec["bucket"] = "not_used"
                util["not_used"] += 1
        util["deliveries"].append(rec)
    out["gt_utilization"] = util

    # ---- verifier + model patch -------------------------------------------
    reward, partial, solved = _verifier_outcome(trial)
    out["verifier_reward"] = reward
    out["verifier_partial"] = partial
    if reward is None:
        missing.append("verifier_reward")
    out["model_patch_files"] = _model_patch_files(trial, missing)

    # ---- baseline (descriptive only, never causal) -------------------------
    if baseline_viewed:
        viewed: set[str] = set()
        for a in actions:
            for fp in a.view_files | a.edit_files:
                mv = match_variant(fp, G)
                if mv:
                    viewed.add(mv)
                else:
                    cand = sorted(path_variants(fp))
                    if cand:
                        viewed.add(cand[0])
        viewed.discard("")
        sim = typical_path_similarity(viewed, baseline_viewed)
        if sim is not None:
            sim["_label"] = (
                "typical_path_similarity - descriptive overlap with "
                "baseline trajectories; NOT part of recall/precision")
        out["typical_path_similarity"] = sim
    else:
        out["typical_path_similarity"] = None
    if baseline_outcome is not None:
        out["baseline_outcome"] = {
            "gt_off": baseline_outcome,
            "gt_on_reward": reward,
            "note": "descriptive comparison only; not a causal claim",
        }

    # ---- causal timeline ---------------------------------------------------
    out["timeline"] = {
        "first_core_step": ttfc,
        "first_edit_step": first_edit_step,
        "solved": solved,
        "steps_total": out["steps_total"],
    }
    out["steps"] = _build_timeline(actions, gold, G, util["deliveries"])
    out["missing_inputs"] = sorted(set(missing))
    return out


def _resolve_task_id(trial: Path) -> str | None:
    for cfg in (trial / "config.json", trial.parent / "config.json"):
        if cfg.is_file():
            try:
                j = json.loads(cfg.read_text(encoding="utf-8",
                                             errors="replace"))
            except json.JSONDecodeError:
                continue
            p = ((j.get("task") or {}).get("path")
                 or ((j.get("agent") or {}).get("kwargs") or {}).get("task_id"))
            if isinstance(p, str) and p:
                return p.rstrip("/").split("/")[-1]
    res = trial / "result.json"
    if res.is_file():
        try:
            j = json.loads(res.read_text(encoding="utf-8", errors="replace"))
            p = (j.get("task_id") or {}).get("path")
            if isinstance(p, str) and p:
                return p.rstrip("/").split("/")[-1]
        except json.JSONDecodeError:
            pass
    name = trial.name
    if "__" in name:
        return name.split("__")[0]
    return None


def _verifier_outcome(trial: Path) -> tuple[float | None, float | None,
                                          bool | None]:
    p = trial / "verifier" / "reward.json"
    if p.is_file():
        try:
            j = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            r = j.get("reward")
            return (float(r) if r is not None else None,
                    float(j["partial"]) if j.get("partial") is not None
                    else None,
                    (r == 1) if r is not None else None)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    res = trial / "result.json"
    if res.is_file():
        try:
            j = json.loads(res.read_text(encoding="utf-8", errors="replace"))
            r = (((j.get("verifier_result") or {}).get("rewards") or {})
                 .get("reward"))
            return (float(r) if r is not None else None, None,
                    (r == 1) if r is not None else None)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return None, None, None


def _model_patch_files(trial: Path, missing: list[str]) -> list[str] | None:
    p = trial / "artifacts" / "model.patch"
    if not p.is_file():
        missing.append("model_patch")
        return None
    return sorted(gold_files_from_patch(
        p.read_text(encoding="utf-8", errors="replace")))


def _compute_recovery(actions: list[Action], events: list[dict]) -> dict:
    """Failing check/test executions later passing on the same target."""
    observations: list[dict] = []
    typed_actions: set[int] = set()
    for e in events:
        if e.get("event") in _CHECK_EVENTS:
            idx = e.get("action_id") or e.get("action_index")
            outcome = e.get("outcome")
            rc = e.get("returncode")
            if idx is None:
                continue
            passed = (outcome == "pass") or (outcome is None and rc == 0)
            failed = (outcome == "fail") or (outcome is None
                                             and isinstance(rc, int)
                                             and rc != 0)
            if not (passed or failed):
                continue
            sig = "sha:" + str(e.get("command_sha256") or e.get("event_hash"))
            observations.append({"action": int(idx), "signature": sig,
                                 "passed": bool(passed), "source": "typed"})
            typed_actions.add(int(idx))
    for a in actions:
        if a.index in typed_actions or a.returncode is None:
            continue
        for sig in a.check_sigs:
            observations.append({"action": a.index, "signature": sig,
                                 "passed": a.returncode == 0,
                                 "source": "shell"})

    by_sig: dict[str, list[dict]] = {}
    for o in sorted(observations, key=lambda x: x["action"]):
        by_sig.setdefault(o["signature"], []).append(o)
    failed_targets = 0
    recovered = 0
    gaps: list[int] = []
    for sig, obs in by_sig.items():
        first_fail = next((o for o in obs if not o["passed"]), None)
        if not first_fail:
            continue
        failed_targets += 1
        later_pass = next((o for o in obs
                           if o["passed"] and o["action"] > first_fail["action"]),
                          None)
        if later_pass:
            recovered += 1
            gaps.append(later_pass["action"] - first_fail["action"])
    fails = sum(1 for o in observations if not o["passed"])
    passes = sum(1 for o in observations if o["passed"])
    return {
        "check_executions": len(observations),
        "checks_failed": fails,
        "checks_passed": passes,
        "failed_targets": failed_targets,
        "recovered_targets": recovered,
        "recovery_rate": (recovered / failed_targets
                          if failed_targets else None),
        "steps_to_recovery_median": (statistics.median(gaps)
                                    if gaps else None),
        "steps_to_recovery": gaps,
    }


# --------------------------------------------------------------------------
# batch
# --------------------------------------------------------------------------
def _fmt(v, nd=2):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def markdown_table(rows: list[dict]) -> str:
    hdr = ("task", "split", "steps", "reward", "ttfc", "edit_step",
           "edit_in_gold", "preR", "preP", "revisit", "dlv", "sent",
           "vis", "served", "cons", "led_fair", "conf", "act<=5",
           "noncore", "recov")
    lines = ["| " + " | ".join(hdr) + " |",
             "|" + "---|" * len(hdr)]
    for r in rows:
        u = r.get("gt_utilization") or {}
        nc = r.get("non_core_exploration") or {}
        rec = r.get("recovery") or {}
        vals = [
            r.get("task_id"), r.get("split"), r.get("steps_total"),
            r.get("verifier_reward"), r.get("ttfc"), r.get("first_edit_step"),
            r.get("first_edit_in_gold"), r.get("preedit_recall"),
            r.get("preedit_precision"), r.get("revisit_rate"),
            u.get("deliveries_total"), u.get("sent"), u.get("visible"),
            u.get("served"), u.get("consumed"), u.get("consumed_fair"),
            u.get("confirmed_only"), u.get("acted_within_5"),
            nc.get("non_core"), rec.get("recovery_rate"),
        ]
        lines.append("| " + " | ".join(_fmt(v) for v in vals) + " |")
    return "\n".join(lines)


def aggregate(results: list[dict]) -> dict:
    keys = ["steps_total", "ttfc", "first_edit_step", "preedit_recall",
            "preedit_precision", "revisit_rate", "verifier_reward",
            "verifier_partial"]
    agg: dict = {"n_tasks": len(results), "metrics": {}}
    for k in keys:
        vals = [r[k] for r in results if isinstance(r.get(k), (int, float))]
        agg["metrics"][k] = {
            "n": len(vals),
            "mean": statistics.mean(vals) if vals else None,
            "median": statistics.median(vals) if vals else None,
        }
    feg = [r["first_edit_in_gold"] for r in results
           if r.get("first_edit_in_gold") is not None]
    agg["metrics"]["first_edit_accuracy"] = {
        "n": len(feg),
        "mean": statistics.mean(1.0 if v else 0.0 for v in feg)
        if feg else None}
    util_keys = ["deliveries_total", "file_targeted", "acted_within_5",
                 "led_new", "confirmed_only", "not_used", "no_file_target",
                 "unanchored", "refused", "sent", "visible", "served",
                 "agent_did", "consumed", "consumed_fair",
                 "confirmed_consumed", "prepared_not_delivered"]
    uagg = {}
    for k in util_keys:
        vals = [(r.get("gt_utilization") or {}).get(k) for r in results]
        vals = [v for v in vals if isinstance(v, (int, float))]
        uagg[k] = {"n": len(vals), "sum": sum(vals) if vals else 0,
                   "mean": statistics.mean(vals) if vals else None,
                   "median": statistics.median(vals) if vals else None}
    agg["gt_utilization"] = uagg
    for k in ("check_executions", "checks_failed", "checks_passed",
              "failed_targets", "recovered_targets", "recovery_rate",
              "steps_to_recovery_median"):
        vals = [(r.get("recovery") or {}).get(k) for r in results]
        vals = [v for v in vals if isinstance(v, (int, float))]
        agg["metrics"]["recovery_" + k] = {
            "n": len(vals), "sum": sum(vals) if k != "recovery_rate" else None,
            "mean": statistics.mean(vals) if vals else None,
            "median": statistics.median(vals) if vals else None}
    n_graph = sum(1 for r in results if r.get("graph_neighborhood_available"))
    n_solved = sum(1 for r in results
                   if r.get("timeline", {}).get("solved") is True)
    agg["graph_neighborhood_tasks"] = n_graph
    agg["solved_tasks"] = n_solved
    agg["solved_n"] = sum(1 for r in results
                          if r.get("timeline", {}).get("solved") is not None)
    # held-out discipline: identical aggregates per split so eval-split
    # numbers can never silently mix with tuning tasks
    per_split: dict[str, list[dict]] = {}
    for r in results:
        per_split.setdefault(str(r.get("split") or "unassigned"),
                             []).append(r)
    agg["splits"] = {name: {"n_tasks": len(rs)}
                     for name, rs in sorted(per_split.items())}
    for name, rs in sorted(per_split.items()):
        s = agg["splits"][name]
        for k in keys:
            vals = [r[k] for r in rs if isinstance(r.get(k), (int, float))]
            s.setdefault("metrics", {})[k] = {
                "n": len(vals),
                "mean": statistics.mean(vals) if vals else None,
                "median": statistics.median(vals) if vals else None}
        feg_s = [r["first_edit_in_gold"] for r in rs
                 if r.get("first_edit_in_gold") is not None]
        s["metrics"]["first_edit_accuracy"] = {
            "n": len(feg_s),
            "mean": (statistics.mean(1.0 if v else 0.0 for v in feg_s)
                     if feg_s else None)}
        for k in util_keys:
            vals = [(r.get("gt_utilization") or {}).get(k) for r in rs]
            vals = [v for v in vals if isinstance(v, (int, float))]
            s.setdefault("gt_utilization", {})[k] = {
                "n": len(vals), "sum": sum(vals) if vals else 0}
        s["solved_tasks"] = sum(
            1 for r in rs if r.get("timeline", {}).get("solved") is True)
    # typical-path similarity stays its own aggregate - never merged with
    # recall/precision which are gold-closure-anchored
    tps = [(r.get("typical_path_similarity") or {}).get("jaccard")
           for r in results]
    tps = [v for v in tps if isinstance(v, (int, float))]
    agg["typical_path_similarity"] = {
        "_label": "descriptive overlap vs baseline trajectories",
        "n": len(tps),
        "mean": statistics.mean(tps) if tps else None}
    return agg


def run_batch(root: Path, tasks_root: Path,
              splits: dict[str, set[str]] | None = None,
              baseline_outcomes: dict[str, dict] | None = None,
              baseline_trajs: dict[str, set[str]] | None = None,
              baseline_meta: dict | None = None) -> dict:
    splits = splits or {"tuning": set(), "evaluation": set()}
    baseline_outcomes = baseline_outcomes or {}
    baseline_trajs = baseline_trajs or {}
    task_dirs = sorted(p for p in Path(root).iterdir()
                       if p.is_dir() and "task" in p.name)
    results: list[dict] = []
    for td in task_dirs:
        trials = find_trial_dirs(td)
        if not trials:
            results.append({"schema": SCHEMA, "task_dir": str(td),
                            "task_id": td.name, "error": "no_trial_dir",
                            "missing_inputs": ["trial_dir"]})
            continue
        for trial in trials:
            try:
                tid = _resolve_task_id(trial)
                res = evaluate_trial(
                    trial, tasks_root, split=split_for(tid, splits),
                    baseline_viewed=(baseline_trajs.get(tid)
                                     if tid else None),
                    baseline_outcome=(baseline_outcomes.get(tid)
                                      if tid else None))
                results.append(res)
            except Exception as ex:  # one bad trial must not sink the batch
                results.append({"schema": SCHEMA, "task_dir": str(td),
                                "trial_dir": str(trial),
                                "task_id": trial.name, "error": repr(ex)})
    results.sort(key=lambda r: (str(r.get("task_id")),
                                str(r.get("trial_name") or "")))
    agg = aggregate(results)
    out = {"schema": SCHEMA + ".batch", "root": str(root),
           "tasks_root": str(tasks_root), "tasks": results,
           "aggregate": agg, "markdown": markdown_table(results)}
    out["split_counts"] = {
        name: len([r for r in results if r.get("split") == name])
        for name in ("tuning", "evaluation", "unassigned", "conflict")}
    out["held_out_note"] = (
        "evaluation-split metrics must never become tuning targets; "
        "all baseline comparisons are descriptive, not causal")
    if baseline_meta is not None:
        out["baseline"] = baseline_meta
        matched = sorted(t for t in baseline_outcomes
                         if any(r.get("task_id") == t for r in results))
        out["baseline"]["matched_tasks"] = matched
        tp = [baseline_outcomes[t].get("gt_off_passes") for t in matched]
        tt = [baseline_outcomes[t].get("gt_off_trials") for t in matched]
        out["baseline"]["gt_off_passes"] = sum(
            v for v in tp if isinstance(v, (int, float))) or None
        out["baseline"]["gt_off_trials"] = sum(
            v for v in tt if isinstance(v, (int, float))) or None
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gt_trajectory_eval",
        description="Offline per-task trajectory metrics for GT-on runs.")
    ap.add_argument("--trial", help="single trial dir (<task>__<suffix>)")
    ap.add_argument("--batch", help="root containing gt-harness-*-task-* dirs")
    ap.add_argument("--tasks-root",
                    default=r"D:\deepswe-bench-435ee89\tasks",
                    help="dir containing tasks/<task_id>/solution gold refs")
    ap.add_argument("--splits",
                    help="JSON file {\"tuning\": [...], \"evaluation\": "
                         "[...]} assigning each task to a held-out split")
    ap.add_argument("--tuning-tasks", help="comma-separated task ids "
                                           "(tuning split)")
    ap.add_argument("--eval-tasks", help="comma-separated task ids "
                                         "(evaluation split)")
    ap.add_argument("--baseline",
                    help="GT-off reference: DEEPSWE_EVALUATION_RESULTS.json, "
                         "a smoke20 comparison JSON, or a Muse bundle dir "
                         "(descriptive comparison only)")
    ap.add_argument("--baseline-trajs",
                    help="dir of baseline trial dirs or <task>.json viewed-"
                         "file lists for typical_path_similarity")
    ap.add_argument("--out", help="write JSON here (default stdout)")
    ap.add_argument("--md", help="write the markdown table here (batch only)")
    ap.add_argument("--outdir",
                    help="batch: write trajectory_eval.json + report.md + "
                         "timelines.jsonl into this repo-local dir")
    args = ap.parse_args(argv)

    tasks_root = Path(args.tasks_root)
    splits, split_issues = load_splits(args.splits, args.tuning_tasks,
                                       args.eval_tasks)
    baseline_outcomes: dict[str, dict] = {}
    baseline_meta: dict | None = None
    if args.baseline:
        baseline_outcomes, baseline_meta, berr = load_baseline_outcomes(
            Path(args.baseline))
        if berr:
            baseline_meta = baseline_meta or {}
            baseline_meta["error"] = berr
    baseline_trajs = (load_baseline_trajectories(Path(args.baseline_trajs))
                      if args.baseline_trajs else {})

    if args.trial:
        tid = Path(args.trial).name.split("__", 1)[0]
        res = evaluate_trial(Path(args.trial), tasks_root,
                             split=split_for(tid, splits),
                             baseline_viewed=baseline_trajs.get(tid),
                             baseline_outcome=baseline_outcomes.get(tid))
        if split_issues:
            res["split_issues"] = split_issues
        text = json.dumps(res, indent=2, sort_keys=True)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
        return 0
    if args.batch:
        res = run_batch(Path(args.batch), tasks_root, splits=splits,
                        baseline_outcomes=baseline_outcomes,
                        baseline_trajs=baseline_trajs,
                        baseline_meta=baseline_meta)
        if split_issues:
            res["split_issues"] = split_issues
        text = json.dumps(res, indent=2, sort_keys=True)
        if args.outdir:
            od = Path(args.outdir)
            od.mkdir(parents=True, exist_ok=True)
            (od / "trajectory_eval.json").write_text(
                text + "\n", encoding="utf-8")
            (od / "report.md").write_text(res["markdown"] + "\n",
                                          encoding="utf-8")
            with (od / "timelines.jsonl").open("w", encoding="utf-8") as fh:
                for t in res["tasks"]:
                    fh.write(json.dumps({
                        "task_id": t.get("task_id"),
                        "trial_name": t.get("trial_name"),
                        "split": t.get("split"),
                        "steps": t.get("steps")},
                        sort_keys=True) + "\n")
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        elif not args.outdir:
            print(text)
        md = res["markdown"]
        if args.md:
            Path(args.md).write_text(md + "\n", encoding="utf-8")
        elif not args.outdir:
            print(md)
        return 0
    ap.error("one of --trial or --batch is required")
    return 2


if __name__ == "__main__":
    sys.exit(main())
