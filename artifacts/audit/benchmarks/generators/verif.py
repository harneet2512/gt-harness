"""Normalise DeepSWE verifier shell fragments and label them independently.

The label is derived from the DOCUMENTED contract of `test_command_shape`
("at most one test-runner invocation surrounded by benign segments"), written
here from that docstring rather than by calling the classifier.
"""
import collections
import json
import re
import shlex
import sys

sys.path.insert(0, r"D:\gt-context-plan")
from gt_engine.runtime_observation import test_command_shape as S  # noqa: E402

BENIGN = {
    "cd", "echo", "printf", "true", ":", "pwd", "export", "ls", "cat", "nl",
    "head", "tail", "wc", "sed", "grep", "egrep", "fgrep", "rg", "test", "[",
    "sort", "uniq", "cut", "tr", "awk", "column", "date", "basename",
    "dirname", "readlink", "file", "stat", "md5sum", "sha256sum", "cmp",
    "tee", "less", "more", "rev", "fold", "expand", "strings", "which",
    "type", "hostname", "whoami", "env",
}
BENIGN_GIT = {
    "status", "rev-parse", "branch", "log", "diff", "show", "merge-base",
    "add", "commit", "rev-list", "ls-files", "describe", "shortlog",
    "remote", "tag", "blame", "cat-file", "symbolic-ref",
}
STATIC_CHECKS = {
    "ruff", "mypy", "flake8", "pylint", "pyright", "eslint", "tsc", "biome",
    "shellcheck",
}
# Words that keep the program position open for the NEXT word.
WRAPPERS = {
    "env", "sudo", "time", "nice", "nohup", "stdbuf", "exec", "command",
    "timeout", "xvfb-run",
}
WRAPPER_VALUE_FLAGS = {"-k", "--kill-after", "-s", "--signal"}
RUNNER_NAMES = {"pytest", "py.test", "tox", "nox", "stestr", "vitest", "jest",
                "mocha", "ava", "tap", "uvu", "karma", "jasmine"}

_REDIR_RE = re.compile(r"(?:\d?>>?|\d?<|&>)\s*(?:&\d|\S+)")


def normalise(raw):
    """Join the shell-fragment artifacts of one recorded `test.sh` line.

    A recorded entry is a slice of the verifier script, so it can end in a
    line continuation and can open or close a ``{ ... ; }`` group.  Neither is
    part of the invocation's own semantics; redirections, operators and
    quoting are left exactly as recorded.
    """
    text = (raw or "").strip()
    text = re.sub(r"\\\s*$", "", text).strip()
    if text.startswith("{"):
        text = text[1:].strip()
    text = re.sub(r";?\s*\}\s*$", "", text).strip()
    text = re.sub(r";\s*$", "", text).strip()
    return text


def _unquoted_spans(cmd):
    """(index, char) pairs for characters outside single/double quotes."""
    quote = None
    for i, ch in enumerate(cmd):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            continue
        yield i, ch


def paren_balance(cmd):
    depth = 0
    for _i, ch in _unquoted_spans(cmd):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
    return depth


def strip_redirections(cmd):
    out, i, n = [], 0, len(cmd)
    quote = None
    while i < n:
        ch = cmd[i]
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if cmd[i:i + 2] != "<<":
            m = _REDIR_RE.match(cmd, i)
            if m:
                i = m.end()
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def segments(cmd):
    cmd = strip_redirections(cmd)
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        return None, None
    segs, cur, ops = [], [], []
    for t in toks:
        if t and all(ch in ";&|<>()" for ch in t):
            if t in ("(", ")"):
                continue
            ops.append(t)
            if cur:
                segs.append(cur)
                cur = []
            continue
        cur.append(t)
    if cur:
        segs.append(cur)
    return segs, ops


def _base(w):
    return w.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".exe")


def program_words(seg):
    """The segment's words with env assignments and exec wrappers peeled off."""
    i = 0
    while i < len(seg):
        w = seg[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w):
            i += 1
            continue
        if _base(w) in WRAPPERS:
            i += 1
            while i < len(seg) and seg[i].startswith("-"):
                i += 2 if seg[i] in WRAPPER_VALUE_FLAGS else 1
            if i < len(seg) and re.fullmatch(r"\d+(\.\d+)?[smhd]?", seg[i]):
                i += 1  # `timeout 600`
            continue
        break
    return [_base(seg[i])] + list(seg[i + 1:]) if i < len(seg) else []


def is_runner(seg):
    words = program_words(seg)
    if not words:
        return False
    head, rest = words[0], words[1:]
    positional = [w for w in rest if not w.startswith("-")]
    if head in RUNNER_NAMES:
        return True
    if head in ("python", "python3", "python2"):
        return bool(rest[:2] and rest[0] == "-m" and _base(rest[1]) in RUNNER_NAMES | {"unittest"})
    if head == "go":
        return positional[:1] == ["test"]
    if head == "cargo":
        return positional[:1] in (["test"], ["nextest"])
    if head == "deno":
        return positional[:1] == ["test"]
    if head == "make":
        return any(p.startswith("test") or p in ("check", "ci") for p in positional)
    if head in ("npm", "pnpm", "yarn", "bun"):
        if not positional:
            return False
        if positional[0] in ("test", "t"):
            return True
        if positional[0] in ("run", "run-script", "exec") and len(positional) > 1:
            return positional[1] in RUNNER_NAMES or positional[1].startswith("test")
        return _base(positional[0]) in RUNNER_NAMES
    if head in ("npx", "bunx", "pnpx"):
        return bool(positional and _base(positional[0]) in RUNNER_NAMES)
    if head == "node":
        return "--test" in rest
    return False


def head_name(seg):
    words = program_words(seg)
    return words[0] if words else "?"


def label(cmd):
    """One of plain | compound | fragment, with the reason that decided it."""
    if re.search(r"<<-?\s*['\"]?\w", cmd):
        return "compound", "heredoc"
    if paren_balance(cmd) != 0:
        return "fragment", "unbalanced_parentheses"
    segs, ops = segments(cmd)
    if segs is None:
        return "fragment", "unparseable_quoting"
    runners = [s for s in segs if is_runner(s)]
    if len(runners) == 0:
        return "compound", "no_runner_invocation"
    if len(runners) > 1:
        return "compound", "multiple_runner_invocations"
    if any(op == "&" for op in ops):
        return "compound", "backgrounded"
    if any(op == "||" for op in ops):
        return "compound", "ambiguous_operator"
    for seg in segs:
        if seg is runners[0]:
            continue
        words = program_words(seg)
        head = words[0] if words else "?"
        if head in BENIGN:
            continue
        if head == "git" and words[1:2] and words[1] in BENIGN_GIT:
            continue
        if head in STATIC_CHECKS:
            continue
        return "compound", "non_benign_segment:%s" % head
    return "plain", "single runner invocation surrounded by benign segments"


def main():
    T = json.load(open(r"D:\gt-context-plan\tests\fixtures\benchmarks\deepswe_tasks.json", encoding="utf-8"))
    counts = collections.Counter()
    disagree = collections.defaultdict(list)
    for r in T:
        for raw in r["verifier_commands"]:
            cmd = normalise(raw)
            if not cmd:
                continue
            kind, why = label(cmd)
            scope = S(cmd).scope
            counts[(kind, scope)] += 1
            if kind == "plain" and scope == "unknown":
                disagree["plain_but_unknown"].append((r["task_id"], r["language"], cmd[:170], why))
            if kind == "compound" and scope != "unknown":
                disagree["compound_but_classified"].append((r["task_id"], r["language"], cmd[:170], why, scope))
    print(counts)
    for k, v in disagree.items():
        print("\n===", k, len(v))
        for item in v[:30]:
            print("  ", item)


if __name__ == "__main__":
    main()
