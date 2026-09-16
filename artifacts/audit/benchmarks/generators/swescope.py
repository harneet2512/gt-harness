"""Independent extent labels for the SWE-bench-Live command corpus."""
import collections
import json
import os
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, r"D:\gt-context-plan")
import verif  # noqa: E402
from gt_engine.runtime_observation import test_command_shape as S  # noqa: E402

# pytest options that consume the NEXT word as their value.  Everything here
# is read off `pytest --help`; a value read as a positional path turns a
# whole-suite run into a "scoped" run over a path that does not exist.
PYTEST_VALUE_OPTIONS = {
    "-k", "-m", "-p", "-n", "-o", "-c", "-r", "--deselect", "--ignore",
    "--ignore-glob", "--rootdir", "--junitxml", "--junit-xml", "--maxfail",
    "--durations", "--tb", "--log-level", "--log-file", "--basetemp",
    "--cov", "--cov-report", "--cov-config", "--cov-fail-under",
    "--override-ini", "--import-mode", "--dist", "--numprocesses",
    "--timeout", "--color", "--capture", "--confcutdir", "--last-failed-no-failures",
}
NARROWING_OPTIONS = {"-k", "-m", "--deselect", "--lf", "--last-failed", "--ff"}
EXCLUDING_OPTIONS = {"--ignore", "--ignore-glob", "--deselect"}


# `poetry run pytest` / `uv run --project x pytest` run the runner directly;
# the wrapper only chooses the interpreter, never the test selection.
PM_WRAPPERS = {"poetry", "pdm", "uv", "pipenv", "hatch", "rye"}
PM_VALUE_FLAGS = {"--project", "--directory", "--python", "-C", "--with"}


def _peel_manager(words):
    while words and verif._base(words[0]) in PM_WRAPPERS:
        index = 1
        while index < len(words) and words[index].startswith("-"):
            index += 2 if words[index] in PM_VALUE_FLAGS else 1
        if index < len(words) and words[index] == "run":
            index += 1
        else:
            break
        words = words[index:]
    return words


def extent(command):
    """suite | scoped | unknown, read off the command text by hand rules."""
    segs, ops = verif.segments(command)
    if segs is None:
        return "unknown", [], [], False, "unparseable"
    candidates = [_peel_manager(verif.program_words(s)) for s in segs]
    runners = [w for w in candidates if w and (
        verif._base(w[0]) in ("pytest", "py.test", "tox", "nox")
        or (verif._base(w[0]).startswith("python") and w[1:2] == ["-m"]))]
    if len(runners) != 1:
        return "unknown", [], [], False, "runner_count=%d" % len(runners)
    words = list(runners[0])
    words[0] = verif._base(words[0])
    head = words[0]
    rest = list(words[1:])
    if head in ("python", "python3", "python2"):
        if rest[:1] != ["-m"] or verif._base(rest[1]) != "pytest":
            return "unknown", [], [], False, "not a pytest invocation"
        rest = rest[2:]
    elif head in ("tox", "nox"):
        # `-e <env>` picks an environment, not a subset of the tests in it.
        return "suite", [], [], False, "%s runs its environment's whole suite" % head
    elif head not in ("pytest", "py.test"):
        return "unknown", [], [], False, "non-pytest runner head %r" % head
    positional, excluded, narrowed = [], [], False
    index = 0
    while index < len(rest):
        word = rest[index]
        if word == "--":
            positional.extend(rest[index + 1:])
            break
        if word.startswith("-"):
            name, sep, value = word.partition("=")
            if name in NARROWING_OPTIONS:
                narrowed = True
            if name in EXCLUDING_OPTIONS:
                excluded.append(value if sep else rest[index + 1]
                                if index + 1 < len(rest) else "")
            if not sep and name in PYTEST_VALUE_OPTIONS:
                index += 2
                continue
            index += 1
            continue
        positional.append(word)
        index += 1
    if positional:
        return "scoped", positional, excluded, narrowed, "positional selection"
    if narrowed or excluded:
        return "scoped", [], excluded, narrowed, "filter/exclusion narrows the run"
    return "suite", [], [], False, "no selection"


def main():
    tasks = json.load(open(
        r"D:\tmp\claude\D--gt-harness\3c77641a-5f30-45de-8550-7ad3d0dcedca"
        r"\scratchpad\bench_swelive\swelive_tasks.json", encoding="utf-8"))
    commands = collections.Counter(c for row in tasks for c in row["test_cmds"])
    agree, disagree = 0, []
    for command in sorted(commands):
        want, paths, excluded, narrowed, why = extent(command)
        shape = S(command)
        if shape.scope == want:
            agree += 1
        else:
            disagree.append((commands[command], command, want, why, shape.scope, list(shape.paths)))
    print("distinct commands", len(commands), "agree", agree, "disagree", len(disagree))
    for rows, command, want, why, got, paths in sorted(disagree, key=lambda r: -r[0]):
        print("%3d rows  want=%-7s got=%-7s paths=%s\n     %r\n     (%s)" % (
            rows, want, got, paths, command, why))
    # path-level disagreements among the agreeing rows
    print("\n--- same scope, different paths ---")
    for command in sorted(commands):
        want, paths, excluded, narrowed, _why = extent(command)
        shape = S(command)
        if shape.scope == want and list(shape.paths) != paths:
            print("%3d  want_paths=%s got=%s\n     %r" % (
                commands[command], paths, list(shape.paths), command))


if __name__ == "__main__":
    main()
