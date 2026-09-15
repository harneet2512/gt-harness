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

Resolution (wheel commit 835c680f, D:\\gt-product-source): the ordering
defect does not reproduce on the pinned wheel across a 30+ PYTHONHASHSEED
sweep on both this synthetic fixture and the real beets-5495 graph, and
run 34985500743 proved the localizer ranks importer.py first in the
acceptance container itself. The verified-witness ranking repair plus the
B5-3/B5-4/DET-CAP determinism patches closed the ordering leak.

The run-34985500743 empty ``.files`` was NOT a delivery defect: the serial
suite imports ``gt_engine`` early, whose ``apply_profile_env()`` fans out
the Profile-2 flag set (``GT_BRIEF_MINIMAL/GT_LOC_RESLOT/GT_BRIEF_NATIVE``)
into ``os.environ``. Under that production posture the wheel honestly
retires step-0 localization narration (reactive ``search_result``
delivery instead), so ``files == []`` and ``delivered_candidate_count``
is ``None`` - the C15 "reduction emptied delivery" NOT_EVALUABLE marker,
not a zero-acquired ranking. This test pins the env posture explicitly
per phase instead of inheriting ambient suite state, and locks all three
behaviors: localizer ranking, delivered-order join under the delivery
posture, and the honest empty set under Profile-2.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

_BEETS_ISSUE = (
    "set_fields does not parse values correctly. When calling set_fields on an "
    "item, the field string is stored verbatim instead of being parsed by "
    "set_parse. Expected set_parse to coerce the field value."
)

# The Profile-2 brief-posture flags ``apply_profile_env()`` sets on
# ``import gt_engine`` - the serial suite leaks them into this test's
# ambient environment. Phase B/C pin them explicitly instead.
_PROFILE_BRIEF_FLAGS = ("GT_BRIEF_MINIMAL", "GT_LOC_RESLOT", "GT_BRIEF_NATIVE")


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


def test_installed_wheel_ranks_witnessed_file_above_witnessless(tmp_path, monkeypatch):
    """Two-layer contract: the localizer must rank the witnessed file above
    witnessless hard negatives, and the delivered brief order must agree.

    The strict arm lives at ``localize()`` - the layer that owns the
    ranking defect. The delivered arm pins the legacy step-0 posture
    (Profile-2 brief flags scrubbed) so ``.files`` exercises the
    render-join where the defect was recorded. An empty delivered set
    dumps full diagnostics instead of silently passing or masking the
    join failure.
    """
    from groundtruth.pretask.graph_localizer import localize
    from groundtruth.pretask.v1r_brief import generate_v1r_brief

    repo, db = _make_beets_db(tmp_path)

    loc = localize(_BEETS_ISSUE, db, repo_root=repo)
    loc_order = [c.file_path.replace("\\", "/") for c in loc.candidates]
    assert "beets/importer.py" in loc_order, (
        f"witnessed importer.py absent from localizer candidates: {loc_order}"
    )
    imp = loc_order.index("beets/importer.py")
    for witnessless in ("beets/util/pipeline.py", "beets/library.py"):
        if witnessless in loc_order:
            assert imp < loc_order.index(witnessless), (
                f"localizer ranked witness-less {witnessless} above "
                f"witnessed importer.py: {loc_order}"
            )

    # Delivery posture: retire the Profile-2 brief flags the serial suite
    # leaks via ``import gt_engine`` -> ``apply_profile_env()``. Without
    # the scrub the wheel honestly emits the minimal brief and the join
    # arm is vacuous (run 34985500743: entries=4, files==[], delivered=None).
    for flag in _PROFILE_BRIEF_FLAGS:
        monkeypatch.delenv(flag, raising=False)

    result = generate_v1r_brief(
        _BEETS_ISSUE, repo, db, bug_id="beets-5495-synth"
    )
    paths = [entry.path for entry in result.files]
    assert paths, (
        "installed wheel delivered zero file candidates under the "
        f"delivery posture (localizer order was {loc_order}). brief_text:\n"
        f"{result.brief_text}\n"
        f"delivered_candidate_count={result.delivered_candidate_count} "
        f"rendered_candidate_count={result.rendered_candidate_count} "
        f"budget_suppressed={result.budget_suppressed} "
        f"confidence_tier={result.confidence_tier} "
        f"localizer_confident={loc.confident} "
        f"localizer_gate={loc.gate_reason}"
    )
    assert "beets/importer.py" in paths
    imp = paths.index("beets/importer.py")
    for witnessless in ("beets/util/pipeline.py", "beets/library.py"):
        if witnessless in paths:
            assert imp < paths.index(witnessless), (
                f"{witnessless} (witness-less) ranked above importer.py "
                f"(witnessed): {paths}"
            )


def test_profile2_posture_delivers_no_step0_files(tmp_path, monkeypatch):
    """Under the production Profile-2 posture the step-0 brief honestly
    ships zero file candidates - localization rides the reactive
    ``search_result`` delivery boundary, and ``delivered_candidate_count``
    reports ``None`` (C15: the reduction emptied delivery, NOT_EVALUABLE)
    rather than a false zero. Locks the run-34985500743 observation as
    designed behavior so it can never masquerade as a delivery defect.
    """
    from groundtruth.pretask.graph_localizer import localize
    from groundtruth.pretask.v1r_brief import generate_v1r_brief

    repo, db = _make_beets_db(tmp_path)
    for flag in _PROFILE_BRIEF_FLAGS:
        monkeypatch.setenv(flag, "1")

    loc = localize(_BEETS_ISSUE, db, repo_root=repo)
    loc_order = [c.file_path.replace("\\", "/") for c in loc.candidates]
    assert "beets/importer.py" in loc_order  # ranking unaffected by posture

    result = generate_v1r_brief(
        _BEETS_ISSUE, repo, db, bug_id="beets-5495-synth-profile2"
    )
    assert result.files == []
    assert result.rendered_candidate_count == 0
    assert result.delivered_candidate_count is None, (
        "Profile-2 reduction emptied delivery -> NOT_EVALUABLE (None); a "
        f"zero or positive count misreports the posture: "
        f"{result.delivered_candidate_count} "
        f"budget_suppressed={result.budget_suppressed}"
    )
