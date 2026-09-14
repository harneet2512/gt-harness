"""Installed-wheel L1 ranking parity: the beets-5495 witness case.

Wheel-side defect recorded in the capability matrix
(``l1_brief_witnessless_outranks_witnessed``): ``generate_v1r_brief``'s
``result.files`` order is HASHSEED-SENSITIVE - the same fixture + graph
ranks beets/importer.py (VERIFIED, ``set_fields -> set_parse`` witness)
below beets/library.py (no witness, WARNING) under some PYTHONHASHSEED
values and correctly under others (PYTHONHASHSEED=1 passes). A set/dict
iteration leak survives somewhere upstream of the final ordering - the
B5-4 ``sorted(_ein_n)`` determinism patch did not close the last hole.
GT's contract is deterministic context: brief order must not vary
run-to-run on identical input.

xfail(strict=False) - the defect is nondeterministic, so this test
documents it without flaking the suite; tighten to strict=True once the
wheel's pretask ranking is repaired upstream.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_BEETS_ISSUE = (
    "set_fields does not parse values correctly. When calling set_fields on an "
    "item, the field string is stored verbatim instead of being parsed by "
    "set_parse. Expected set_parse to coerce the field value."
)


def _make_beets_db(tmp_path):
    repo = tmp_path / "repo"
    (repo / "beets" / "dbcore").mkdir(parents=True)
    (repo / "beets" / "util").mkdir(parents=True)
    (repo / "beets" / "importer.py").write_text(
        "def set_fields(self, fields):\n"
        "    for key, val in fields.items():\n"
        "        self.set_parse(key, val)\n",
        encoding="utf-8",
    )
    (repo / "beets" / "dbcore" / "db.py").write_text(
        "def set_parse(self, key, string):\n    return _parse(string)\n",
        encoding="utf-8",
    )
    (repo / "beets" / "util" / "pipeline.py").write_text(
        "def parse_stage(values):\n"
        "    # parse the field values in the pipeline\n"
        "    return values\n",
        encoding="utf-8",
    )
    (repo / "beets" / "library.py").write_text(
        "def store(self, fields):\n    # library stores parsed field values\n    return fields\n",
        encoding="utf-8",
    )
    db = str(tmp_path / "graph.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE nodes (
            id INTEGER PRIMARY KEY, label TEXT, name TEXT, qualified_name TEXT,
            file_path TEXT, start_line INTEGER, end_line INTEGER, signature TEXT,
            return_type TEXT, is_exported INTEGER, is_test INTEGER, language TEXT,
            parent_id INTEGER
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER, type TEXT,
            source_line INTEGER, source_file TEXT, resolution_method TEXT,
            confidence REAL, metadata TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO nodes (id,label,name,file_path,start_line,end_line,signature,"
        "is_test,language) VALUES (?,?,?,?,?,?,?,0,'python')",
        [
            (1, "Method", "set_fields", "beets/importer.py", 1, 3,
             "def set_fields(self, fields):"),
            (2, "Method", "set_parse", "beets/dbcore/db.py", 1, 2,
             "def set_parse(self, key, string):"),
            (3, "Function", "parse_stage", "beets/util/pipeline.py", 1, 3,
             "def parse_stage(values):"),
            (4, "Method", "store", "beets/library.py", 1, 3,
             "def store(self, fields):"),
        ],
    )
    conn.execute(
        "INSERT INTO edges (id,source_id,target_id,type,source_line,source_file,"
        "resolution_method,confidence) VALUES "
        "(1,1,2,'CALLS',3,'beets/importer.py','import',1.0)"
    )
    conn.commit()
    conn.close()
    return str(repo), db


@pytest.mark.xfail(
    strict=False,
    reason=(
        "installed wheel pretask ranking is hashseed-nondeterministic "
        "(matrix: l1_brief_witnessless_outranks_witnessed): witness-less "
        "library.py can outrank witnessed importer.py in result.files"
    ),
)
def test_installed_wheel_ranks_witnessed_file_above_witnessless(tmp_path):
    from groundtruth.pretask.v1r_brief import generate_v1r_brief

    repo, db = _make_beets_db(tmp_path)
    result = generate_v1r_brief(
        _BEETS_ISSUE, repo, db, bug_id="beets-5495-synth"
    )
    paths = [entry.path for entry in result.files]
    assert "beets/importer.py" in paths
    imp = paths.index("beets/importer.py")
    for witnessless in ("beets/util/pipeline.py", "beets/library.py"):
        if witnessless in paths:
            assert imp < paths.index(witnessless), (
                f"{witnessless} (witness-less) ranked above importer.py "
                f"(witnessed): {paths}"
            )
