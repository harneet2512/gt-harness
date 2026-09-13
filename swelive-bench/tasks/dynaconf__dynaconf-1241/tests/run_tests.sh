#!/bin/bash
# Instance test_cmds, verbatim from the SWE-bench-Live dataset row
# (equivalent to the run_test.sh the official eval writes via heredoc).
pytest -m "not integration" -v -l --tb=short --maxfail=1 -rA tests/
