"""Deterministic taxonomy for coding-agent tool observations."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_TEST_RE = re.compile(
    r"(?i)\b(?:pytest|unittest|npm\s+test|cargo\s+test|go\s+test|"
    r"assert|check|verify|validate|build|compile|import)\b"
)
_SEARCH_RE = re.compile(r"(?i)(?:^|[;&|]\s*)(?:rg|grep|find)\b")
_COMMAND_ERROR_RE = re.compile(
    r"(?i)(?:syntaxerror|syntax error|unexpected token|invalid syntax|"
    r"unterminated|no such file|cannot access|cd: .*: no such)"
)
_TOOL_CONTRACT_RE = re.compile(
    r"(?i)(?:old string not found|non-unique|does not exist for (?:view|edit)|"
    r"invalid tool arguments|missing required argument|unknown tool)"
)
_DEPENDENCY_RE = re.compile(
    r"(?i)(?:command not found|modulenotfounderror|no module named|"
    r"importerror|cannot find package|not installed)"
)
_TIMEOUT_RE = re.compile(
    r"(?i)(?:exceeded timeout|timed out|timeout|out of memory|oom|exit 137)"
)
_SHELL_RE = re.compile(r"(?i)shell process exited unexpectedly")


@dataclass(frozen=True)
class ToolOutcome:
    classification: str
    harmful: bool
    information_signature: str
    reason: str


def classify_tool_outcome(
    command: str,
    output: str,
    *,
    is_error: bool,
    returncode: int | None,
) -> ToolOutcome:
    """Classify an observation without treating every nonzero as agent damage."""
    command = command or ""
    output = output or ""
    combined = f"{command}\n{output}"
    if not is_error and returncode in (None, 0):
        classification, harmful, reason = "success", False, "zero_exit_or_tool_success"
    elif _SHELL_RE.search(output):
        classification, harmful, reason = (
            "shell_lifecycle",
            True,
            "persistent_shell_terminated",
        )
    elif _TIMEOUT_RE.search(output):
        classification, harmful, reason = (
            "timeout_or_resource",
            True,
            "timeout_or_resource_marker",
        )
    elif _TOOL_CONTRACT_RE.search(output):
        classification, harmful, reason = (
            "tool_contract_error",
            True,
            "tool_api_or_precondition_miss",
        )
    elif _DEPENDENCY_RE.search(output):
        classification, harmful, reason = (
            "dependency_or_environment",
            True,
            "dependency_marker",
        )
    elif (
        returncode == 1
        and _SEARCH_RE.search(command)
        and not output.strip().replace("[exit code 1]", "").strip()
    ):
        classification, harmful, reason = (
            "expected_negative_probe",
            False,
            "search_no_match",
        )
    elif _TEST_RE.search(command):
        classification, harmful, reason = (
            "useful_red",
            False,
            "executed_check_exposed_failure",
        )
    elif _COMMAND_ERROR_RE.search(combined):
        classification, harmful, reason = (
            "agent_command_error",
            True,
            "command_or_path_marker",
        )
    elif is_error or (returncode not in (None, 0)):
        classification, harmful, reason = (
            "product_failure",
            False,
            "executed_product_command_failed",
        )
    else:
        classification, harmful, reason = "unknown", True, "unclassified_observation"
    normalized = re.sub(r"\s+", " ", output.strip().lower())[:1000]
    signature = hashlib.sha256(
        f"{classification}\0{returncode}\0{normalized}".encode(
            "utf-8", "surrogatepass"
        )
    ).hexdigest()
    return ToolOutcome(classification, harmful, signature, reason)
