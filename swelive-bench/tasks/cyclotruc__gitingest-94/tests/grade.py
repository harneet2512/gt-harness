#!/usr/bin/env python3
"""SWE-bench-Live grader for the pier verifier contract.

Parser + resolved logic mirror microsoft/SWE-bench-Live
evaluation/evaluation.py verbatim:

- parse_log_pytest(): scan for lines starting with PASSED/FAILED/SKIPPED/
  ERROR/XFAIL (the `-rA` short-test-summary format); FAILED lines have their
  " - " reason separator collapsed before splitting.
- default_pytest_parser(): collapse to pass/fail/skip (XFAIL -> pass).
- resolved: every FAIL_TO_PASS observed passing AND no FAIL_TO_PASS or
  PASS_TO_PASS observed failing. A FAIL_TO_PASS test missing from the log is
  not resolved; a PASS_TO_PASS test missing from the log is tolerated
  (same asymmetry as the official report logic).

Writes:
  /logs/verifier/reward.json       {"reward": 1.0 | 0.0}   (pier reward file)
  /logs/verifier/eval_report.json  official-style report (diagnostics)
"""

import json
import sys
from enum import Enum

VERIFIER_DIR = "/logs/verifier"
SPEC_PATH = "/tests/spec.json"


class TestStatus(Enum):
    FAILED = "FAILED"
    PASSED = "PASSED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"
    XFAIL = "XFAIL"


def parse_log_pytest(log: str) -> dict:
    """Copied from SWE-bench/swebench (as re-published by SWE-bench-Live)."""
    test_status_map = {}
    for line in log.split("\n"):
        if any([line.startswith(x.value) for x in TestStatus]):
            # Additional parsing for FAILED status
            if line.startswith(TestStatus.FAILED.value):
                line = line.replace(" - ", " ")
            test_case = line.split()
            if len(test_case) <= 1:
                continue
            test_status_map[test_case[1]] = test_case[0]
    return test_status_map


def default_pytest_parser(log: str) -> dict:
    mapping = parse_log_pytest(log)
    for test in mapping.keys():
        # XFAIL: expected failure remained expected -> treat as pass.
        if mapping[test].upper() == "XFAIL":
            mapping[test] = "pass"
        elif "pass" in mapping[test].lower():
            mapping[test] = "pass"
        elif "skip" in mapping[test].lower():
            mapping[test] = "skip"
        else:
            mapping[test] = "fail"
    return mapping


def main() -> int:
    log_path = sys.argv[1] if len(sys.argv) > 1 else f"{VERIFIER_DIR}/testlog.out"
    with open(SPEC_PATH, encoding="utf-8") as f:
        spec = json.load(f)
    f2p = spec["fail_to_pass"]
    p2p = spec["pass_to_pass"]

    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            log = f.read()
    except OSError:
        log = ""

    res = default_pytest_parser(log)
    suc = {t for t, s in res.items() if "pass" in s.lower()}
    fail = {t for t, s in res.items() if "fail" in s.lower()}

    report = {
        "instance_id": spec.get("instance_id"),
        "resolved": False,
        "PASS_TO_PASS": {
            "success": sorted(set(p2p) & suc),
            "failure": sorted(set(p2p) & fail),
        },
        "FAIL_TO_PASS": {
            "success": sorted(set(f2p) & suc),
            "failure": sorted(set(f2p) & fail),
        },
        "parsed_test_count": len(res),
    }
    f2p_ok = set(f2p).issubset(suc) or (len(set(f2p) & suc) == len(f2p))
    if (
        len(set(p2p) & fail) == 0
        and len(set(f2p) & fail) == 0
        and f2p_ok
    ):
        report["resolved"] = True

    reward = 1.0 if report["resolved"] else 0.0
    with open(f"{VERIFIER_DIR}/eval_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=True)
    with open(f"{VERIFIER_DIR}/reward.json", "w", encoding="utf-8") as f:
        json.dump({"reward": reward}, f)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
