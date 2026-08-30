"""Strict Go build-failure grammar for canonical RED evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
BANNER = re.compile(r"^# (?P<package>\S+)(?: \[(?P<test_package>[^\]]+)\])?$")
DIAGNOSTIC = re.compile(r"^(?P<path>.+?):(?P<line>\d+)(?::(?P<column>\d+))?: (?P<message>.+)$")
PACKAGE_FAILURE = re.compile(
    r"^FAIL\s+(?P<package>\S+)\s+\[build failed\](?:\s+(?P<timing>\d+(?:\.\d+)?s))?$"
)
CANONICAL_OUTCOME = "PACKAGE_OUTCOME=build_failed"


class GoGrammarError(ValueError):
    """Raw output cannot be represented by the closed Go RED grammar."""


@dataclass(frozen=True)
class CanonicalGoDiagnostic:
    body: bytes
    package_banner: str
    package_outcome: str
    matched_diagnostics: tuple[str, ...]


def _logical_text(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GoGrammarError("invalid_utf8:command") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return ANSI.sub("", text)


def _logical_path(raw_path: str, root: Path) -> str:
    if not isinstance(raw_path, str) or not raw_path or "\0" in raw_path:
        raise GoGrammarError("diagnostic_path_invalid")
    path = raw_path.replace("\\", "/")
    roots = {str(root.resolve()).replace("\\", "/"), root.resolve().as_posix()}
    for spelling in sorted(roots, key=len, reverse=True):
        if path == spelling:
            return "."
        if path.startswith(f"{spelling}/"):
            return f"./{path[len(spelling) + 1 :]}"
    if path.startswith("./"):
        logical = path
    elif path.startswith("/") or re.match(r"^[A-Za-z]:/", path):
        raise GoGrammarError("diagnostic_path_outside_root")
    else:
        logical = f"./{path}"
    if "/../" in f"/{logical}/":
        raise GoGrammarError("diagnostic_path_outside_root")
    return logical


def canonicalize_go_red(
    raw: bytes,
    *,
    root: str | Path,
    expected_source_path: str,
    expected_diagnostic: str,
    expected_match_count: int = 1,
) -> CanonicalGoDiagnostic:
    """Parse one failing Go package without erasing diagnostic content."""

    resolved_root = Path(root)
    if not isinstance(expected_source_path, str) or not expected_source_path:
        raise GoGrammarError("invalid_expected_source_path")
    if not isinstance(expected_diagnostic, str):
        raise GoGrammarError("invalid_expected_diagnostic")
    try:
        expected_path = _logical_path(expected_source_path, resolved_root)
    except GoGrammarError as exc:
        raise GoGrammarError("invalid_expected_source_path") from exc
    if (
        not expected_diagnostic
        or "\0" in expected_diagnostic
        or not isinstance(expected_match_count, int)
        or isinstance(expected_match_count, bool)
        or expected_match_count < 1
    ):
        raise GoGrammarError("invalid_expected_diagnostic")
    lines = _logical_text(raw).splitlines()
    canonical: list[str] = []
    matched: list[str] = []
    package_banner: str | None = None
    banner_package: str | None = None
    outcome_package: str | None = None
    terminal_fail = False

    for line_number, line in enumerate(lines, start=1):
        if not line:
            raise GoGrammarError(f"blank_line_not_permitted:{line_number}")
        if terminal_fail:
            raise GoGrammarError(f"output_after_terminal_fail:{line_number}")
        banner = BANNER.fullmatch(line)
        if banner:
            if package_banner is not None or canonical:
                raise GoGrammarError("multiple_package_banners")
            package_banner = line
            banner_package = banner.group("package")
            canonical.append(line)
            continue
        diagnostic = DIAGNOSTIC.fullmatch(line)
        if diagnostic:
            if package_banner is None or outcome_package is not None:
                raise GoGrammarError(f"diagnostic_out_of_order:{line_number}")
            try:
                logical_path = _logical_path(diagnostic.group("path"), resolved_root)
            except GoGrammarError as exc:
                raise GoGrammarError(f"diagnostic_path_outside_root:{line_number}") from exc
            column = diagnostic.group("column")
            location = f"{logical_path}:{diagnostic.group('line')}"
            if column:
                location += f":{column}"
            rendered = f"{location}: {diagnostic.group('message')}"
            canonical.append(rendered)
            if logical_path == expected_path and expected_diagnostic in rendered:
                matched.append(rendered)
            continue
        package_failure = PACKAGE_FAILURE.fullmatch(line)
        if package_failure:
            if package_banner is None or outcome_package is not None:
                raise GoGrammarError("multiple_package_outcomes")
            outcome_package = package_failure.group("package")
            canonical.append(CANONICAL_OUTCOME)
            continue
        if line == "FAIL":
            if outcome_package is None or terminal_fail:
                raise GoGrammarError(f"unexpected_terminal_fail:{line_number}")
            terminal_fail = True
            continue
        if line.startswith("FAIL"):
            raise GoGrammarError(f"unknown_failure_summary:{line_number}")
        raise GoGrammarError(f"unrecognized_output_line:{line_number}")

    if package_banner is None:
        raise GoGrammarError("missing_package_banner")
    if outcome_package is None:
        raise GoGrammarError("missing_package_outcome")
    if outcome_package != banner_package:
        raise GoGrammarError("package_identity_mismatch")
    if not terminal_fail:
        raise GoGrammarError("missing_terminal_fail")
    if len(matched) != expected_match_count:
        raise GoGrammarError(
            f"expected_diagnostic_match_count:{len(matched)}:{expected_match_count}"
        )
    body = ("\n".join(canonical) + "\n").encode("utf-8")
    return CanonicalGoDiagnostic(
        body=body,
        package_banner=package_banner,
        package_outcome="build_failed",
        matched_diagnostics=tuple(matched),
    )


def validate_canonical_body(
    body: bytes,
    *,
    expected_source_path: str,
    expected_diagnostic: str,
    expected_match_count: int,
) -> tuple[str, tuple[str, ...]]:
    """Validate the sidecar grammar without reconstructing discarded raw summaries."""

    if not isinstance(expected_source_path, str) or not expected_source_path:
        raise GoGrammarError("canonical:invalid_expected_source_path")
    if not isinstance(expected_diagnostic, str) or not expected_diagnostic:
        raise GoGrammarError("canonical:invalid_expected_diagnostic")
    if not isinstance(expected_match_count, int) or isinstance(expected_match_count, bool):
        raise GoGrammarError("canonical:invalid_expected_match_count")
    if not body.endswith(b"\n") or b"\r" in body:
        raise GoGrammarError("canonical:invalid_line_endings")
    try:
        lines = body.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise GoGrammarError("invalid_utf8:canonical") from exc
    if any(line == "" for line in lines):
        raise GoGrammarError("canonical:blank_line_not_permitted")
    if len(lines) < 3 or BANNER.fullmatch(lines[0]) is None:
        raise GoGrammarError("canonical:invalid_banner")
    if lines.count(CANONICAL_OUTCOME) != 1 or lines[-1] != CANONICAL_OUTCOME:
        raise GoGrammarError("canonical:invalid_outcome")
    diagnostics = tuple(line for line in lines[1:-1] if DIAGNOSTIC.fullmatch(line))
    if len(diagnostics) != len(lines[1:-1]):
        raise GoGrammarError("canonical:unrecognized_line")
    expected_path = expected_source_path.replace("\\", "/")
    if not expected_path.startswith("./"):
        expected_path = f"./{expected_path}"
    matched = tuple(
        line
        for line in diagnostics
        if line.startswith(f"{expected_path}:") and expected_diagnostic in line
    )
    if len(matched) != expected_match_count:
        raise GoGrammarError("canonical:expected_diagnostic_match_count")
    return lines[0], matched
