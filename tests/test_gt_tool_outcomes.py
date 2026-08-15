from gt_engine.tool_outcomes import classify_tool_outcome


def test_classifies_useful_red():
    result = classify_tool_outcome(
        "python -m pytest -q",
        "1 failed, 3 passed\n[exit code 1]",
        is_error=True,
        returncode=1,
    )
    assert result.classification == "useful_red"
    assert result.harmful is False


def test_classifies_expected_negative_search():
    result = classify_tool_outcome(
        "grep -R SECRET .",
        "[exit code 1]",
        is_error=True,
        returncode=1,
    )
    assert result.classification == "expected_negative_probe"
    assert result.harmful is False


def test_classifies_dependency_tool_contract_timeout_and_shell():
    cases = [
        (
            "python check.py",
            "ModuleNotFoundError: No module named 'x'",
            "dependency_or_environment",
        ),
        ("", "old string not found in x.py", "tool_contract_error"),
        ("sleep 5", "Command exceeded timeout of 1s", "timeout_or_resource"),
        ("exit 0", "Shell process exited unexpectedly.", "shell_lifecycle"),
    ]
    for command, output, expected in cases:
        result = classify_tool_outcome(
            command, output, is_error=True, returncode=1
        )
        assert result.classification == expected
        assert result.harmful is True


def test_same_failure_has_stable_information_signature():
    one = classify_tool_outcome(
        "python check.py",
        "AssertionError: expected 3",
        is_error=True,
        returncode=1,
    )
    two = classify_tool_outcome(
        "python check.py",
        "AssertionError: expected 3",
        is_error=True,
        returncode=1,
    )
    assert one.information_signature == two.information_signature
