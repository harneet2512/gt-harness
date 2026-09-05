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
    result = pytest.main(sys.argv[2:], plugins=[receipt])
    target.write_text(json.dumps({
        "collected": receipt.collected,
        "reports": receipt.reports,
    }), encoding="utf-8")
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
