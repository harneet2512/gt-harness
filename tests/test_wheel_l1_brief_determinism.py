"""Installed-wheel L1 brief determinism: PYTHONHASHSEED sweep.

Covering test for capability-matrix failure mode
``graph_pipeline/l1_brief_witnessless_outranks_witnessed``.

``generate_v1r_brief``'s ``result.files`` order must be identical across
PYTHONHASHSEED values — GT's contract is deterministic context. Hash order is
fixed at interpreter start, so each seed runs a real subprocess; a set/dict
iteration leak anywhere in the pretask ranking path shows up as an order diff
between runs. Asserts both the cross-seed invariant and the witnessed-above-
witness-less ranking on the beets-5495 synthetic fixture.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_CHILD = r'''
import json, sqlite3, sys, tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
repo = tmp / "repo"
(repo / "beets" / "dbcore").mkdir(parents=True)
(repo / "beets" / "util").mkdir(parents=True)
(repo / "beets" / "importer.py").write_text(
    "def set_fields(self, fields):\n"
    "    for key, val in fields.items():\n"
    "        self.set_parse(key, val)\n", encoding="utf-8")
(repo / "beets" / "dbcore" / "db.py").write_text(
    "def set_parse(self, key, string):\n    return _parse(string)\n", encoding="utf-8")
(repo / "beets" / "util" / "pipeline.py").write_text(
    "def parse_stage(values):\n    return values\n", encoding="utf-8")
(repo / "beets" / "library.py").write_text(
    "def store(self, fields):\n    return fields\n", encoding="utf-8")
db = str(tmp / "graph.db")
conn = sqlite3.connect(db)
conn.executescript("""
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
""")
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
    "(1,1,2,'CALLS',3,'beets/importer.py','import',1.0)")
conn.commit(); conn.close()

from groundtruth.pretask.v1r_brief import generate_v1r_brief
issue = (
    "set_fields does not parse values correctly. When calling set_fields on an "
    "item, the field string is stored verbatim instead of being parsed by "
    "set_parse. Expected set_parse to coerce the field value."
)
r = generate_v1r_brief(issue, str(repo), db, bug_id="beets-5495-synth")
print(json.dumps([e.path for e in r.files]))
'''


def test_installed_wheel_brief_order_is_hashseed_invariant():
    orders: dict[str, list] = {}
    for seed in ("0", "1", "7", "42"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        # The certified container has no sentence-transformers; force the shared
        # ONNX surface so the witness path engages identically to production.
        env.setdefault("GT_FORCE_ONNX_EMBEDDER", "1")
        # The serial suite leaks the Profile-2 brief-posture flags into
        # os.environ via ``import gt_engine`` -> ``apply_profile_env()``; the
        # child inherits them and the wheel then honestly ships the minimal
        # brief (files == [] on EVERY seed -> a vacuous "deterministic" pass,
        # exactly what run 34985500743 recorded). Pin the delivery posture so
        # the sweep exercises the render-join it claims to cover.
        for flag in ("GT_BRIEF_MINIMAL", "GT_LOC_RESLOT", "GT_BRIEF_NATIVE"):
            env.pop(flag, None)
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD],
            capture_output=True,
            text=True,
            env=env,
            timeout=300,
        )
        assert proc.returncode == 0, f"seed={seed}: {proc.stderr[-2000:]}"
        order = json.loads(proc.stdout.strip().splitlines()[-1])
        orders[seed] = order
        assert order, (
            f"seed={seed}: wheel delivered zero file candidates under the "
            "delivery posture - an empty order makes every assertion below "
            f"vacuous. stderr: {proc.stderr[-1500:]}"
        )
        assert "beets/importer.py" in order, (
            f"seed={seed}: witnessed importer.py absent from delivered "
            f"files: {order}"
        )
        imp = order.index("beets/importer.py")
        for wl in ("beets/library.py", "beets/util/pipeline.py"):
            if wl in order:
                assert imp < order.index(wl), (
                    f"seed={seed}: witness-less {wl} outranks witnessed "
                    f"importer.py: {order}"
                )
    unique = {tuple(o) for o in orders.values()}
    assert len(unique) == 1, (
        "PYTHONHASHSEED-dependent brief order on the installed wheel: "
        + "; ".join(f"seed {k}: {v}" for k, v in orders.items())
    )
