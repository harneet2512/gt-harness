from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.red_evidence_go import GoGrammarError, canonicalize_go_red

SOURCE = "proof_red_test.go"
EXPECTED = "undefined: VTAFlowProof"
OBSERVED_RAW_SHA256 = "ee29265e7cf6ce4e409e7e3cff12461c27d96755841a7d65c4f1e7aa22ba3709"


def _raw(message: str = EXPECTED, *, separator: str = "\t", timing: str = "") -> bytes:
    return (
        "# example.invalid/redfixture [example.invalid/redfixture.test]\n"
        f"./{SOURCE}:6:6: {message}\n"
        f"FAIL{separator}example.invalid/redfixture [build failed]{timing}\n"
        "FAIL\n"
    ).encode()


def _parse(raw: bytes, root: Path, *, count: int = 1):
    return canonicalize_go_red(
        raw,
        root=root,
        expected_source_path=SOURCE,
        expected_diagnostic=EXPECTED,
        expected_match_count=count,
    )


def test_published_ubuntu_observation_is_exact() -> None:
    assert hashlib.sha256(_raw()).hexdigest() == OBSERVED_RAW_SHA256


def test_preserves_banner_column_tab_and_diagnostic_duration(tmp_path: Path) -> None:
    first = _parse(_raw("undefined: VTAFlowProof after 1s"), tmp_path)
    second = _parse(_raw("undefined: VTAFlowProof after 99s", separator=" "), tmp_path)
    assert first.body != second.body
    assert first.body.startswith(
        b"# example.invalid/redfixture [example.invalid/redfixture.test]\n./proof_red_test.go:6:6:"
    )


def test_recognized_package_timing_and_crlf_normalize(tmp_path: Path) -> None:
    plain = _parse(_raw(), tmp_path)
    timed = _parse(_raw(timing="\t0.123s").replace(b"\n", b"\r\n"), tmp_path)
    assert plain.body == timed.body


def test_root_metacharacters_are_normalized_only_in_path_token(tmp_path: Path) -> None:
    root = tmp_path / "root[1]+value"
    root.mkdir()
    absolute = str(root / SOURCE).replace("\\", "/")
    raw = _raw(f"undefined: VTAFlowProof at {absolute}").replace(
        f"./{SOURCE}:".encode(), f"{absolute}:".encode(), 1
    )
    result = _parse(raw, root)
    assert result.matched_diagnostics[0].startswith(f"./{SOURCE}:6:6:")
    assert absolute.encode() in result.body  # message content is never globally redacted


@pytest.mark.parametrize(
    "raw,error",
    [
        (_raw() + b"\xff", "invalid_utf8"),
        (_raw().replace(b"FAIL\n", b"", 1), "missing_terminal_fail"),
        (
            _raw().replace(b"FAIL\texample.invalid/redfixture [build failed]\n", b""),
            "unexpected_terminal_fail",
        ),
        (_raw().replace(b"FAIL\n", b"PASS\n"), "unrecognized_output_line"),
        (
            _raw().replace(b"FAIL\n", b"ok\texample.invalid/redfixture\n"),
            "unrecognized_output_line",
        ),
        (_raw().replace(b"FAIL\n", b"unknown output\nFAIL\n"), "unrecognized_output_line"),
        (
            _raw().replace(
                b"# example.invalid/redfixture [example.invalid/redfixture.test]\n",
                b"# example.invalid/redfixture [example.invalid/redfixture.test]\n# second\n",
            ),
            "multiple_package_banners",
        ),
        (
            _raw().replace(b"FAIL\n", b"FAIL\texample.invalid/redfixture [build failed]\nFAIL\n"),
            "multiple_package_outcomes",
        ),
    ],
)
def test_closed_grammar_rejects_unknown_success_and_duplicate_states(
    tmp_path: Path, raw: bytes, error: str
) -> None:
    with pytest.raises(GoGrammarError, match=error):
        _parse(raw, tmp_path)


def test_matcher_requires_declared_path_substring_and_exact_count(tmp_path: Path) -> None:
    wrong_path = _raw().replace(b"./proof_red_test.go", b"./other.go")
    with pytest.raises(GoGrammarError, match="expected_diagnostic_match_count:0:1"):
        _parse(wrong_path, tmp_path)
    duplicate = _raw().replace(
        b"FAIL\texample.invalid/redfixture",
        b"./proof_red_test.go:7:1: undefined: VTAFlowProof\nFAIL\texample.invalid/redfixture",
    )
    with pytest.raises(GoGrammarError, match="expected_diagnostic_match_count:2:1"):
        _parse(duplicate, tmp_path)
    assert len(_parse(duplicate, tmp_path, count=2).matched_diagnostics) == 2


@pytest.mark.parametrize("raw", [_raw().replace(b"\n", b"\n\n", 1), _raw() + b"\n"])
def test_blank_lines_are_not_silently_discarded(tmp_path: Path, raw: bytes) -> None:
    with pytest.raises(GoGrammarError, match="blank_line_not_permitted"):
        _parse(raw, tmp_path)
