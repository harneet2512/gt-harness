"""Collect executed pytest phases, including non-strict XPASS, for proof gates."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


class ExecutionReceipt:
    def __init__(self):
        self.collected = []
        self.reports = []

    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]

    def pytest_runtest_logreport(self, report):
        self.reports.append({
            "node_id": report.nodeid,
            "phase": report.when,
            "outcome": report.outcome,
            "wasxfail": hasattr(report, "wasxfail"),
        })


def main():
    target = Path(sys.argv[1])
    receipt = ExecutionReceipt()
    # Pin rootdir to the invocation directory with an empty inifile. Without
    # this, an ancestor pyproject.toml/pytest.ini above the temp tree (e.g. a
    # stray one under %TEMP%) hijacks rootdir, prefixes every collected nodeid
    # with its path, and the witness matcher reports not_run for a suite that
    # actually ran. The empty ini also drops foreign ini_options like addopts.
    inifile = target.parent / "gt-evidence-pytest.ini"
    inifile.write_text("[pytest]\n", encoding="utf-8")
    result = pytest.main(
        ["-c", str(inifile), "--rootdir", str(Path.cwd()), *sys.argv[2:]],
        plugins=[receipt],
    )
    target.write_text(json.dumps({
        "collected": receipt.collected,
        "reports": receipt.reports,
    }), encoding="utf-8")
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
