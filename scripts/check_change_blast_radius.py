"""Prove a staged change did not break what depends on it.

Three defects in three consecutive commits shared one shape: the change's effect
was wider than the function edited.

  args.state_dir inside build_agent   wider than the LINE  - no args in scope
  successful_receipt fixture          wider than the TEST  - shared by 8 tests
  os.environ.setdefault in build_agent wider than the RUN  - retrieval.py reads
                                       the same variable, process-global

Each was caught, twice by executing the line and once by CI, and the rate rose
while moving faster toward a dispatch. Rules addressed to oneself fail exactly
when attention is elsewhere; this runs whether or not anyone remembers it.

Three checks, matched to the three shapes:

1. RUFF over staged Python. F821 undefined-name is already selected in
   pyproject; it catches the NameError class outright and costs a second.

2. THE DERIVED SUITES, IN ONE PYTEST PROCESS. Collect the tests that reference
   any symbol whose definition the diff touches, and run them TOGETHER. Running
   them per-file is what HIDES a process-global mutation: the env leak passed in
   isolation and failed only in aggregate. Isolation is the failure mode here,
   not the safety net.

3. A PROMPT on process-global mutation. Warn, never block - an operator override
   written through os.environ is legitimate, and a hard block on a legitimate
   pattern is how hooks get bypassed.

What BLOCKS and what REPORTS, and why the line is where it is
------------------------------------------------------------
Ruff blocks: it is deterministic, environment-independent, and F821 is exactly
the NameError class.

The derived suites block WHEN THE INTERPRETER IS PROVABLY THE RIGHT ONE, and
report otherwise. That condition is machine-checked, not remembered: the suites
run under `.venv` when this repository has one, and the root `conftest.py`
refuses collection unless the installed `groundtruth-mcp` is byte-for-byte the
pinned wheel. A refusal comes back as pytest's usage exit and is reported rather
than blocking, because it means the environment could not answer - not that the
change is bad. Anything else from a verified interpreter is a real failure and
stops the commit.

This version of the file reported unconditionally. That was a workaround for an
interpreter resolving `groundtruth` to an editable checkout at a revision the
benchmark never installs, and a gate that only works if someone reads it has the
exact property that made these three defects necessary in the first place. The
environment was fixed instead; the report path survives only for a machine that
has not been set up yet.

The authority for green remains the CI gate, which already runs everything. What
this adds is the derived set IN ONE PROCESS, printed before the push rather than
six minutes into CI - and that single-process property is the part that matters,
because the env leak passed in isolation and failed only in aggregate.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# pytest's usage exit. The root conftest refuses collection when the installed
# producer is not the pinned wheel, and that is a statement about the machine,
# not about the diff, so it must not block.
PYTEST_USAGE_ERROR = 4


def _verified_interpreter() -> tuple[str, bool]:
    """The interpreter carrying the pinned producer, and whether it is one.

    A repository-local `.venv` is the environment this gate can make claims
    about; the ambient interpreter is whatever the machine happens to have, and
    on this machine that was an editable checkout of a different revision.
    """
    for candidate in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"):
        if candidate.is_file():
            return str(candidate), True
    return sys.executable, False

GLOBAL_MUTATION = re.compile(
    r"^\+\s*(?:os\.environ\[[^\]]+\]\s*=|os\.environ\.setdefault\(|os\.environ\.update\(|"
    r"sys\.path\.insert\(|logging\.basicConfig\(|warnings\.filterwarnings\()"
)
DEFINITION = re.compile(r"^\+?\s*(?:def|class)\s+([A-Za-z_]\w*)")


def _is_python(path: str) -> bool:
    """.py, or a file whose shebang says python.

    `.githooks/pre-commit` is Python with no extension, so an extension filter
    never linted it - and a syntax error went in that only git found, by trying
    to execute it. The hook is the one file where "ruff would have caught it"
    is not a hypothetical.
    """
    if path.endswith(".py"):
        return True
    try:
        with (ROOT / path).open("rb") as handle:
            first = handle.readline(200)
    except OSError:
        return False
    return first.startswith(b"#!") and b"python" in first


def _staged() -> list[str]:
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                         capture_output=True, text=True, cwd=ROOT).stdout
    return [line for line in out.splitlines() if _is_python(line)]


def main() -> int:
    # Say what could NOT be derived from. Symbol-derived selection cannot reach a
    # file that has no symbols, and this repository has four such classes, all
    # load-bearing: .githooks/*, workflow YAML, config JSON, the bundle manifest.
    # Twelve commits were reported as "derived set green" while the diff that
    # broke CI touched .githooks/pre-commit - true statement, narrower than what
    # the reader took from it. A witness must cover the claim's scope, and the
    # claim must state the witness's scope; printing the gap is how the commit
    # message gets written honestly without anyone remembering to.
    all_staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True, cwd=ROOT).stdout.splitlines()
    undecidable = [path for path in all_staged if path.strip() and not _is_python(path)]
    if undecidable:
        print(f"    NOT DERIVABLE ({len(undecidable)}): no symbols to select tests by "
              f"- CI is the only authority for these", file=sys.stderr)
        for path in undecidable:
            print(f"        {path}", file=sys.stderr)
    staged = _staged()
    if not staged:
        # Not "nothing to check" - nothing this gate CAN check. A diff of only
        # config, hooks or YAML lands here, and e559c1c3 - the hook rewrite that
        # broke CI - was exactly that: .githooks/pre-commit had no .py extension
        # until be8d2d67, so this returned 0 before printing anything at all.
        return 0
    diff = subprocess.run(["git", "diff", "--cached", "-U0", "--"] + staged,
                          capture_output=True, text=True, cwd=ROOT).stdout

    # (1) undefined names and the rest of the configured ruff selection
    ruff = subprocess.run([sys.executable, "-m", "ruff", "check", *staged],
                          capture_output=True, text=True, cwd=ROOT)
    if ruff.returncode != 0:
        print(ruff.stdout or ruff.stderr, file=sys.stderr)
        print("blast-radius gate: ruff refused the staged files", file=sys.stderr)
        return 1

    # (3) prompt, never block
    for line in diff.splitlines():
        if GLOBAL_MUTATION.match(line):
            print(f"blast-radius NOTE: {line.strip()}", file=sys.stderr)
            print("  this mutates process-global state. Is the blast radius the "
                  "process or the function? retrieval.py reads os.environ too.",
                  file=sys.stderr)

    # (2) every test file referencing a touched definition, in ONE process
    touched = {m.group(1) for line in diff.splitlines() if (m := DEFINITION.match(line))}
    touched |= {Path(p).stem for p in staged if not p.startswith("tests/")}
    suites = {p for p in staged if p.startswith("tests/")}
    if touched:
        pattern = r"\b(" + "|".join(sorted(re.escape(t) for t in touched if len(t) > 3)) + r")\b"
        for test in (ROOT / "tests").glob("test_*.py"):
            try:
                if re.search(pattern, test.read_text(encoding="utf-8", errors="ignore")):
                    suites.add(test.relative_to(ROOT).as_posix())
            except OSError:
                continue
    if not suites:
        print("blast-radius gate: no dependent suite derived; nothing to prove", file=sys.stderr)
        return 0
    ordered = sorted(suites)
    print(f"blast-radius gate: {len(ordered)} suite(s), one process:", file=sys.stderr)
    for s in ordered:
        print(f"    {s}", file=sys.stderr)
    interpreter, verified = _verified_interpreter()
    # No -q here: pyproject already sets `-ra -q`, and a second -q is -qq, which
    # suppresses the count line outright. The first run of this gate printed a
    # suite list and then nothing at all, which is precisely the failure this
    # file exists to prevent - a check whose output nobody can read.
    # git exports GIT_DIR, GIT_INDEX_FILE, GIT_PREFIX and friends into every
    # hook it runs, and this gate runs from a hook. Inherited, they follow
    # pytest into the fixtures: a test that builds a throwaway repository and
    # commits to it then writes to the REAL one instead, because its `git` calls
    # resolve to the committing repository rather than its tmp_path. Measured:
    # seven suites failing here that pass standalone, fixture commits landing on
    # the live branch, and core.bare left set in the shared config so that every
    # later commit in either worktree refused with "must be run in a work tree".
    # The suite must decide its own repository, so the gate hands it none.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GT_PRECOMMIT_ACTIVE"] = "1"
    proc = subprocess.run([interpreter, "-m", "pytest", "--no-header",
                           "-p", "no:cacheprovider", *ordered],
                          capture_output=True, text=True, cwd=ROOT, env=env)
    tail = [ln for ln in proc.stdout.splitlines() if ln.startswith("FAILED") or " passed" in ln
            or " failed" in ln or " error" in ln]
    for line in tail[-12:]:
        print(f"    {line}", file=sys.stderr)
    if proc.returncode == 0:
        return 0
    # Some suites assert a property of HEAD, not of the working tree:
    # gt_harness.product refuses to bundle when the source closure differs from
    # HEAD, so test_product_acceptance CANNOT pass with a staged-but-uncommitted
    # change to any closure file. Controlled rather than assumed - appending a
    # bare comment to gt_harness/product.py reproduces it exactly, so the
    # failure is caused by the tree being dirty and not by any diff's content.
    #
    # Blocking on that would refuse every commit that touches the closure, and a
    # gate that is always red is a gate that gets bypassed. Reported, with the
    # blocking decision taken on the remaining failures - which is how the one
    # REAL failure in this set (a parity test pinning the old patch path) still
    # stopped the commit.
    head_only = "source_closure_differs_from_head" in proc.stdout
    failures = [ln for ln in proc.stdout.splitlines() if ln.startswith("FAILED")]
    attributable = [ln for ln in failures
                    if not (head_only and "test_product_acceptance" in ln)]
    if head_only:
        print(f"    NOTE: {len(failures) - len(attributable)} failure(s) assert a "
              f"property of HEAD and cannot pass before the commit exists "
              f"(source_closure_differs_from_head); not counted against the diff",
              file=sys.stderr)
    if failures and not attributable:
        print("blast-radius gate: every failure is a HEAD-asserting suite; nothing "
              "is attributable to the staged change", file=sys.stderr)
        return 0
    if proc.returncode == PYTEST_USAGE_ERROR:
        print(proc.stdout.strip() or proc.stderr.strip(), file=sys.stderr)
        print("blast-radius gate: the derived set could not be collected, so this "
              "reports rather than blocks - the message above is about the machine, "
              "not about the diff. Fix it and the derived set starts blocking.",
              file=sys.stderr)
        return 0
    if not verified:
        print("blast-radius gate: the derived set is NOT green, and this interpreter "
              "is the ambient one rather than a repository .venv carrying the pinned "
              "producer, so the failures are not attributable and this does not block. "
              "Create the .venv and they will be.", file=sys.stderr)
        return 0
    print(f"blast-radius gate: the derived set is NOT green under {interpreter}, which "
          f"carries the pinned producer. These failures are attributable to the staged "
          f"change. Fix them or unstage.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
