"""Explicit check specifications and revision-bound plan evidence.

Shell transcripts are audit artifacts, never executable specifications. Passing
a bound check is CHECK_PASSED, not a proof of arbitrary requested behavior.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class CheckSpec:
    check_id: str
    argv: tuple[str, ...]
    cwd: str
    protocol: str
    requirement_ids: tuple[str, ...]
    selected_test_ids: tuple[str, ...] = ()
    test_source_digest: str = ""
    environment_sha256: str = ""
    test_source_paths: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: dict, repo_root: str) -> CheckSpec:
        argv = value.get("argv")
        if not isinstance(argv, (list, tuple)) or not argv or any(
            not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv
        ):
            raise ValueError("check argv must be a nonempty string array")
        executable = Path(argv[0]).name.lower().removesuffix(".exe")
        if executable in {"sh", "bash", "zsh", "cmd", "powershell", "pwsh", "env"}:
            raise ValueError("shell/wrapper check executables are not admissible")
        if (executable.startswith(("python", "node", "ruby", "perl")) and
                any(arg in {"-c", "--eval", "-e", "-Command", "-EncodedCommand"} for arg in argv[1:])):
            raise ValueError("inline program execution is not an automatic check")
        from groundtruth.runtime.patterns import TEST_RUNNER_RE

        if not TEST_RUNNER_RE.match(shlex.join((executable, *argv[1:]))):
            raise ValueError("automatic check requires a canonical test-runner invocation")
        root = Path(repo_root).resolve()
        cwd = (root / str(value.get("cwd", "."))).resolve()
        if cwd != root and root not in cwd.parents:
            raise ValueError("check cwd escapes repository")
        rows = value.get("requirement_ids", ())
        tests = value.get("selected_test_ids", ())
        source_paths = value.get("test_source_paths", ())
        for values in (rows, tests, source_paths):
            if not isinstance(values, (list, tuple)) or any(not isinstance(x, str) or not x for x in values):
                raise ValueError("check identities must be string arrays")
        if not rows:
            raise ValueError("check has no requirement binding")
        material = {
            "argv": list(argv), "cwd": cwd.relative_to(root).as_posix(),
            "protocol": str(value.get("protocol", "")),
            "requirement_ids": sorted(set(rows)), "selected_test_ids": sorted(set(tests)),
            "test_source_digest": str(value.get("test_source_digest", "")),
            "environment_sha256": str(value.get("environment_sha256", "")),
            "test_source_paths": sorted(set(source_paths)),
        }
        # One execution can support several explicit requirement bindings.
        # Binding another row must not schedule the identical command twice.
        identity = {key: item for key, item in material.items() if key != "requirement_ids"}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        if value.get("check_id") not in (None, "", digest):
            raise ValueError("check identity mismatch")
        return cls(digest, tuple(argv), material["cwd"], material["protocol"],
                   tuple(material["requirement_ids"]), tuple(material["selected_test_ids"]),
                   material["test_source_digest"], material["environment_sha256"],
                   tuple(material["test_source_paths"]))

    @property
    def command(self) -> str:
        return shlex.join(self.argv)

    def as_dict(self) -> dict:
        return asdict(self)


# Heads whose exit status IS the assertion (match/difference verdicts).
_ASSERTION_HEADS = {
    "test", "[", "[[",
    "diff", "diff3", "sdiff", "zdiff", "cmp", "zcmp", "comm", "vimdiff",
    "grep", "egrep", "fgrep", "rgrep", "zgrep", "pgrep",
}
# Heads that assert only under a flag; without it they print, not verdict.
_FLAG_GATED_HEADS = {
    "jq": {"-e", "--exit-status"},
    "git": {"--exit-code", "--quiet"},
    "sha1sum": {"-c", "--check"}, "sha224sum": {"-c", "--check"},
    "sha256sum": {"-c", "--check"}, "sha384sum": {"-c", "--check"},
    "sha512sum": {"-c", "--check"}, "shasum": {"-c", "--check"},
    "md5sum": {"-c", "--check"}, "b2sum": {"-c", "--check"},
    "cksum": {"-c", "--check"},
}
# Operators whose rc attribution is ambiguous no matter what follows them.
_AMBIGUOUS_OPERATORS = {"||", "&"}


def _program_last_command(command: str) -> list[str]:
    """Words of the program's final command - the exit-status owner."""
    try:
        lexer = shlex.shlex(
            _escape_word_parens(command), posix=True, punctuation_chars=True
        )
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    last: list[str] = []
    current: list[str] = []
    saw_ambiguous = False
    for token in tokens:
        if token and all(char in ";&|()" for char in token):
            if token in _AMBIGUOUS_OPERATORS:
                saw_ambiguous = True
            if current:
                last, current = current, []
            continue
        current.append(token)
    if current:
        last = current
    if saw_ambiguous:
        return []
    while last and "=" in last[0] and not last[0].startswith(("=", "-")) \
            and last[0].split("=", 1)[0].isidentifier():
        last = last[1:]
    return last


def program_command_is_verdict(command: str) -> bool:
    """True when the program's final exit status is an assertion verdict.

    ``test "$(fd --sort)" = "a"`` ends in an assertion head - its rc is the
    check. ``pytest x | tail -1`` ends in ``tail``: rc 0 regardless of the
    suite, so binding it would manufacture CHECK_PASSED. The last command's
    head must be an assertion verb (or flag-gated assertion form), or itself
    match the canonical test-runner pattern.
    """
    words = _program_last_command(command)
    if not words:
        return False
    head = Path(words[0]).name.lower().removesuffix(".exe")
    if head in _ASSERTION_HEADS:
        return True
    gated = _FLAG_GATED_HEADS.get(head)
    if gated and any(word in gated for word in words[1:]):
        return True
    from groundtruth.runtime.patterns import TEST_RUNNER_RE

    return bool(TEST_RUNNER_RE.match(shlex.join(words)))


@dataclass(frozen=True)
class ProgramCheckSpec:
    """A behavioral check: a shell program whose exit status is the verdict.

    Some plan rows verify behavior no test runner can express - the DeepSWE
    fd task's 39 acceptance checks were programs like
    ``tmp=$(mktemp -d); cd "$tmp"; touch a; test "$(fd --sort)" = "a"``.
    They failed every CheckSpec gate (shell expansion, non-runner argv heads)
    and sat pending until the gate conceded. The program text is the spec:
    binding it verbatim and replaying it through the task environment gives
    the same verdict the official verifier gets, bound to revision and
    environment. ``test``/``diff -q``/``grep -q``/``cmp`` all encode their
    assertion in the exit status, which is what this contract binds.
    """

    check_id: str
    program: str
    requirement_ids: tuple[str, ...]
    environment_sha256: str = ""

    @classmethod
    def from_command(
        cls, command: str, requirement_ids, *, environment_sha256: str = ""
    ) -> ProgramCheckSpec:
        program = str(command or "").strip()
        if not program or "\x00" in program:
            raise ValueError("program check requires nonempty program text")
        rows = tuple(sorted({str(row) for row in (requirement_ids or ()) if row}))
        if not rows:
            raise ValueError("check has no requirement binding")
        material = {
            "program": program,
            "environment_sha256": str(environment_sha256 or ""),
        }
        digest = hashlib.sha256(
            json.dumps(material, sort_keys=True).encode()
        ).hexdigest()
        return cls(digest, program, rows, material["environment_sha256"])

    @property
    def command(self) -> str:
        return self.program

    def as_dict(self) -> dict:
        return asdict(self)


def classify_program_check(
    spec: ProgramCheckSpec, execution, *, before_revision: str,
    after_revision: str, capture_complete: bool,
) -> CheckObservation:
    """Classify a program execution against its bound spec.

    The verdict is the exit status under the same revision+environment
    bindings CheckSpec requires; there is no test-source digest because the
    program text itself is the bound source.
    """
    state = "UNVERIFIED"
    if (execution is not None and capture_complete and before_revision
            and before_revision == after_revision == execution.repository_revision
            and execution.command_sha256
            == hashlib.sha256(spec.program.encode()).hexdigest()
            and (not spec.environment_sha256
                 or execution.environment_sha256 == spec.environment_sha256)
            and not execution.timed_out):
        if execution.outcome == "fail":
            state = "CHECK_FAILED"
        elif execution.outcome == "pass" and execution.returncode == 0:
            state = "CHECK_PASSED"
    return CheckObservation(
        spec.check_id, state, after_revision,
        getattr(execution, "environment_sha256", "") or "",
        capture_complete, (), binding_basis="program_spec",
    )


@dataclass(frozen=True)
class CheckObservation:
    check_id: str
    state: str
    source_revision: str
    environment_sha256: str
    capture_complete: bool
    test_ids: tuple[str, ...] = ()
    binding_basis: str = "explicit_check_spec"
    test_source_digest: str = ""


def _escape_word_parens(command: str) -> str:
    """Backslash-escape parens that are argument text, not subshell operators.

    Posix shlex with ``punctuation_chars`` splits every unquoted ``(``/``)``
    into a standalone operator token — including parens glued to a word, like
    the test ids in ``cargo test -- sorting(asc)`` or ``foo()``. Glued parens
    are argument content: escaping them keeps them inside the word token. A
    ``(`` that directly continues a word (the previous raw character is word
    content, not whitespace or another operator) is literal; a ``)`` is
    literal whenever no operator paren is open — inside a subshell it stays
    the closing operator even glued to the last word (``(cargo test)``).
    Quoting and backslash escapes follow posix shlex rules, so ``"a(b)"`` and
    ``a\\(b`` pass through untouched.
    """
    out: list[str] = []
    quote: "str | None" = None
    escaped = False
    word_char = False
    open_parens = 0
    for ch in command:
        if escaped:
            out.append(ch)
            escaped = False
            word_char = True
            continue
        if quote is not None:
            out.append(ch)
            if ch == quote:
                quote = None
            elif quote == '"' and ch == "\\":
                escaped = True
            word_char = True
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            word_char = True
            continue
        if ch in "'\"":
            out.append(ch)
            quote = ch
            word_char = True
            continue
        if ch in "();<>|&":
            if ch == "(":
                if word_char:
                    out.append("\\")
                    word_char = True
                else:
                    open_parens += 1
                    word_char = False
            elif ch == ")":
                if open_parens:
                    open_parens -= 1
                    word_char = False
                else:
                    out.append("\\")
                    word_char = True
            else:
                word_char = False
            out.append(ch)
            continue
        out.append(ch)
        word_char = ch not in " \t\r\n"
    return "".join(out)


def decompose_check_command(command: str):
    """Split a simple sequential shell command into check segments.

    Returns ``(segments, separators, last_raw_is_check, reason)``.
    ``segments`` is a list of ``(argv_list, cwd_suffix_or_None)`` where
    ``cwd_suffix`` folds leading ``cd`` segments; callers resolve it against
    their own base directory. ``separators`` lists the operators
    (``&&``/``;``) between raw segments; ``last_raw_is_check`` is True when
    the final raw segment was emitted as a check segment (not a folded
    ``cd``). Together they let callers attribute an exit status honestly: an
    all-``&&`` chain with rc==0 proves every segment passed; a ``;``-chain's
    rc belongs only to the last raw segment.

    Parens glued to word characters (``sorting(asc)``) are argv text, not
    operators. A subshell wrapping the whole command — ``( cd x && cargo
    test )`` — runs the inner chain with the chain's own exit status, so it
    unwraps cleanly and attribution is unchanged.

    ``reason`` is set (and segments empty) when the command uses operators
    whose semantics cannot be preserved by independent argv checks: pipes and
    ``||`` change which exit status matters, ``&``/mid-command subshells
    change the process model, and redirections change the observable.
    """
    try:
        lexer = shlex.shlex(
            _escape_word_parens(command), posix=True, punctuation_chars=True
        )
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None, (), False, "unparseable_shell_command"
    # Unwrap subshells that enclose the entire command. Parens surviving in
    # any other position stay operator tokens and are rejected below.
    while len(tokens) > 1 and tokens[0] == "(":
        depth = 0
        close = None
        for index, token in enumerate(tokens):
            if token == "(":
                depth += 1
            elif token == ")":
                depth -= 1
                if depth == 0:
                    close = index
                    break
        if close != len(tokens) - 1:
            break
        tokens = tokens[1:close]
    raw_segments: list[list[str]] = []
    separators: list[str] = []
    current: list[str] = []
    for token in tokens:
        if token and all(char in ";&|<>()" for char in token):
            if token not in {"&&", ";"}:
                return None, (), False, f"unsupported_shell_operator:{token}"
            if current:
                raw_segments.append(current)
                separators.append(token)
                current = []
            continue
        current.append(token)
    if current:
        raw_segments.append(current)
    if not raw_segments:
        return None, (), False, "empty_command"
    if "$" in command or "`" in command:
        return None, (), False, "shell_expansion"
    segments: list[tuple[list[str], "str | None"]] = []
    cwd: "str | None" = None
    last_raw_is_check = False
    for seg in raw_segments:
        head = Path(seg[0]).name.lower().removesuffix(".exe") if seg else ""
        if head == "cd" and len(seg) == 2:
            cwd = seg[1]
            last_raw_is_check = False
            continue
        segments.append((list(seg), cwd))
        last_raw_is_check = True
    if not segments:
        return None, (), False, "no_executable_segment"
    return segments, tuple(separators), last_raw_is_check, None


def _looks_like_test_source(path: str) -> bool:
    """Workspace-relative path that is plausibly test source or test data."""
    parts = [part.lower() for part in Path(path).parts]
    name = parts[-1] if parts else ""
    if any(part in {"test", "tests", "testing", "__tests__", "spec", "specs",
                    "testdata", "fixtures", "conftest"} for part in parts[:-1]):
        return True
    return (
        name.startswith("test_") or name.startswith("conftest")
        or "_test." in name or name.endswith((".test.ts", ".test.tsx",
                    ".test.js", ".test.jsx", ".spec.ts", ".spec.js",
                    "_spec.rb", "_test.go", "tests.rs", "_tests.rs"))
    )


def _cargo_package_scopes(spec: CheckSpec, snapshot) -> list[str]:
    """Map ``cargo test -p <name>`` package arguments to workspace member dirs.

    A cargo package name is a manifest identity, not a path: ``cargo -p
    boa_engine`` selects whichever Cargo.toml declares ``package.name =
    "boa_engine"``, and that manifest may live at ``core/engine/``. Resolving
    the flag value as a filesystem path misses the package entirely and falls
    through to the whole-test-surface binding - an over-broad digest that
    invalidates the check on unrelated test edits.
    """
    executable = Path(spec.argv[0]).name.lower().removesuffix(".exe")
    if executable != "cargo" or not any(
        arg in {"test", "nextest"} for arg in spec.argv[1:]
    ):
        return []
    names: list[str] = []
    args = list(spec.argv[1:])
    for index, arg in enumerate(args):
        if arg in {"-p", "--package"} and index + 1 < len(args):
            names.append(args[index + 1])
        elif arg.startswith(("-p=", "--package=")):
            names.append(arg.split("=", 1)[1])
    if not names:
        return []
    import tomllib

    wanted = set(names)
    scopes = []
    for item in snapshot.files:
        captured = getattr(item, "captured", None)
        if Path(item.path).name != "Cargo.toml" or captured is None:
            continue
        try:
            doc = tomllib.loads(captured.decode("utf-8", "replace"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError):
            continue
        package = doc.get("package", {})
        if isinstance(package, dict) and package.get("name") in wanted:
            scopes.append(str(Path(item.path).parent).replace("\\", "/") or ".")
    return scopes


def validation_source_digest(spec: CheckSpec, snapshot) -> str:
    """Bind declared test source/configuration; result dependencies remain workspace-wide."""
    if not snapshot.complete:
        return ""
    root = Path(snapshot.root)
    scopes = list(spec.test_source_paths)
    if not scopes:
        scopes.extend(_cargo_package_scopes(spec, snapshot))
    if not scopes:
        for arg in spec.argv[1:]:
            candidate = (root / spec.cwd / arg.split("::", 1)[0]).resolve()
            if root in candidate.parents:
                relative = candidate.relative_to(root).as_posix()
                if any(f.path == relative or f.path.startswith(relative + "/") for f in snapshot.files):
                    scopes.append(relative)
    if not scopes:
        # A suite invocation (``cargo test``, ``npm test``, bare ``pytest``)
        # names no path: its honest source identity is the repository's whole
        # test surface, plus the runner configuration the digest always adds.
        # Without this fallback the spec binds with an empty digest and the
        # drain loop discards it as ``test_source_not_bound`` - which is why
        # every plan check in the smoke cohort went pending and no predicate
        # ever proved.
        scopes = [f.path for f in snapshot.files
                  if f.kind == "file" and _looks_like_test_source(f.path)]
    if not scopes:
        return ""
    selected = []
    for scope in scopes:
        path = (root / scope).resolve()
        if path != root and root not in path.parents:
            return ""
        relative = path.relative_to(root).as_posix()
        files = [f for f in snapshot.files if relative == "." or f.path == relative
                 or f.path.startswith(relative + "/")]
        if not files or any(f.kind != "file" for f in files):
            return ""
        selected.extend((f.path, f.sha256) for f in files)
    selected.extend((f.path, f.sha256) for f in snapshot.files
                    if Path(f.path).suffix.lower() in {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".lock"})
    return hashlib.sha256(json.dumps(sorted(set(selected)), separators=(",", ":")).encode()).hexdigest()


_PYTEST_MISMATCH_RE = re.compile(
    r"import file mismatch|has this __file__ attribute"
    r"|not the same as the test file",
    re.IGNORECASE,
)


def pytest_collection_mismatch(output: str) -> bool:
    """True when pytest could not collect the declared targets at all.

    ``pytest a/app_test.py b/app_test.py`` (or a bare suite run on a tree
    with duplicate test basenames) dies in collection: two same-named
    modules cannot coexist under one rootdir import. That is an instrument
    defect in the invocation, not evidence about the tree - the check never
    ran a single assertion.
    """
    return bool(_PYTEST_MISMATCH_RE.search(output or ""))


def pytest_importlib_argv(argv) -> list[str]:
    """Argv with ``--import-mode=importlib`` after the pytest token.

    Importlib mode names each test module by its rootdir-relative path, so
    duplicate basenames collect cleanly. Inserting after the ``pytest``
    token keeps ``python -m pytest ...`` well-formed.
    """
    args = list(argv)
    if any(arg.startswith("--import-mode") for arg in args):
        return args
    for index, arg in enumerate(args):
        if Path(arg).name.lower().removesuffix(".exe") == "pytest":
            return [*args[: index + 1], "--import-mode=importlib", *args[index + 1:]]
    return [args[0], "--import-mode=importlib", *args[1:]]


_PYTEST_INTERRUPTED_RE = re.compile(
    r"Interrupted: \d+ errors? during collection",
)


def argv_runs_pytest(argv) -> bool:
    """True when some token in ``argv`` is the pytest runner itself.

    Both ``pytest`` and ``python -m pytest`` name it; ``cargo test`` does
    not. Pytest-only accommodations are gated on this, because handing a
    pytest flag to another runner converts a readable observation into a
    spawn failure - a worse answer than the one being repaired.
    """
    return any(Path(arg).name.lower().removesuffix(".exe") == "pytest"
               for arg in argv)


def pytest_collection_interrupted(output: str) -> bool:
    """True when pytest aborted the session because collection errored.

    Pytest's default is all-or-nothing: one module that raises on import
    ends the WHOLE session before a single assertion runs. Run 34996816912
    (dynaconf__dynaconf-1241) measured it - discovered command ``pytest -v``
    (basis config:pytest_ini) exited 2 in 4.4s with errored=4, passed=0,
    failed=0 and zero names - while ``pytest --collect-only`` on the same
    checkout printed "743 tests collected, 10 errors" then "Interrupted: 10
    errors during collection". The 743 collectable tests are a fact about
    the tree; the abort is a property of the invocation.
    """
    return bool(_PYTEST_INTERRUPTED_RE.search(output or ""))


def pytest_continue_on_collection_errors_argv(argv) -> list[str]:
    """Argv with ``--continue-on-collection-errors`` after the pytest token.

    The flag keeps the uncollectable modules as errors and runs everything
    that did collect, so the capture records the names that were already
    green instead of nothing at all. Inserting after the ``pytest`` token
    keeps ``python -m pytest ...`` well-formed; the transform is idempotent,
    so an argv that already carries the flag comes back unchanged and the
    caller can tell there is no second run worth spending.
    """
    args = list(argv)
    if "--continue-on-collection-errors" in args:
        return args
    for index, arg in enumerate(args):
        if Path(arg).name.lower().removesuffix(".exe") == "pytest":
            return [*args[: index + 1], "--continue-on-collection-errors",
                    *args[index + 1:]]
    return [args[0], "--continue-on-collection-errors", *args[1:]]


def classify_bound_check(spec: CheckSpec, execution, *, before_revision: str,
                         after_revision: str, capture_complete: bool,
                         test_ids: tuple[str, ...] = (),
                         test_source_digest: str = "") -> CheckObservation:
    state = "UNVERIFIED"
    environment = getattr(execution, "environment_sha256", "")
    if (execution is not None and capture_complete and before_revision
            and spec.test_source_digest and test_source_digest == spec.test_source_digest
            and before_revision == after_revision == execution.repository_revision
            and environment and (not spec.environment_sha256 or environment == spec.environment_sha256)
            and execution.command_sha256 == hashlib.sha256(spec.command.encode()).hexdigest()
            and (not spec.protocol or execution.protocol == spec.protocol)
            and not execution.timed_out):
        if execution.outcome in {"fail", "env_fail"}:
            state = "CHECK_FAILED"
        elif (execution.outcome == "pass" and execution.returncode == 0
              and (not spec.selected_test_ids
                   or (test_ids and set(spec.selected_test_ids).issubset(test_ids)))):
            # A bound check that ran and passed is CHECK_PASSED. When specific
            # test ids were selected they must all appear in the run; a suite
            # invocation with no selection proves itself by its exit status.
            state = "CHECK_PASSED"
    return CheckObservation(spec.check_id, state, after_revision, environment,
                            capture_complete, test_ids, test_source_digest=test_source_digest)
