"""Co-change delivery: the engine half of final-hardening item 6.

The fixture graphs in this repository are depth-1 clones, so every one of them
has ``cochanges`` = 0 rows. These tests therefore build the table themselves,
from the producer's exact schema (``gt-index/internal/store/sqlite.go``,
commit ce5e0370):

    CREATE TABLE IF NOT EXISTS cochanges (
        file_a TEXT NOT NULL,
        file_b TEXT NOT NULL,
        count  INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(file_a, file_b)
    );

Three columns, no window column, no confidence column. ``count`` IS the pair
support: the producer's ``Persist`` writes ``Pair.Support`` into it and records
the dropped confidence fields as a KNOWN LOSS.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from gt_engine.cochange_evidence import (
    COCHANGE_DOSE_BYTE_LIMIT,
    COCHANGE_EVIDENCE_TYPE,
    COCHANGE_WINDOW_UNRECORDED,
    MAX_PARTNERS_PER_FILE,
    CochangePartner,
    cochange_partners,
    cochange_prior_dose,
    cochange_row_count,
    render_cochange_prior,
    run_cochange_prior,
)

# Verbatim from the producer schema. Copied rather than imported on purpose:
# a drift between GT's reader and the producer's writer must break a test.
_COCHANGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS cochanges (
    file_a TEXT NOT NULL,
    file_b TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    commits_a INTEGER NOT NULL DEFAULT 0,
    commits_b INTEGER NOT NULL DEFAULT 0,
    confidence_a_to_b REAL NOT NULL DEFAULT 0.0,
    confidence_b_to_a REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY(file_a, file_b)
);
CREATE INDEX IF NOT EXISTS idx_cochanges_a ON cochanges(file_a);
CREATE INDEX IF NOT EXISTS idx_cochanges_b ON cochanges(file_b);
"""


def _graph(path: Path, pairs: list[tuple[str, str, int]] | None = None) -> str:
    con = sqlite3.connect(path)
    try:
        con.executescript(_COCHANGE_SCHEMA)
        # A neighbouring table the emitter must never read.
        con.execute("CREATE TABLE IF NOT EXISTS edges (id INTEGER PRIMARY KEY)")
        con.executemany(
            "INSERT OR REPLACE INTO cochanges (file_a, file_b, count) VALUES (?, ?, ?)",
            pairs or [],
        )
        con.commit()
    finally:
        con.close()
    return str(path)


class _Adapter:
    """The three attributes the seam actually reads from MiniSweAdapter."""

    def __init__(self, graph_db: str, repo_root: str) -> None:
        self.graph_db = graph_db
        self.graph_fresh = True
        self.repo_root = repo_root
        self.repository_revision = "rev0"


# --- the empty table, which is what every local fixture actually has --------


def test_an_empty_cochanges_table_yields_no_evidence_and_no_error(tmp_path: Path):
    db = _graph(tmp_path / "g.db")

    assert cochange_row_count(db) == 0
    assert cochange_partners(db, "src/a.py") == ()
    assert run_cochange_prior(_Adapter(db, str(tmp_path)), ("src/a.py",)) == ""


def test_a_graph_without_the_table_is_quiet(tmp_path: Path):
    path = tmp_path / "g.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()

    assert cochange_row_count(str(path)) == 0
    assert cochange_partners(str(path), "src/a.py") == ()


def test_an_absent_graph_is_quiet(tmp_path: Path):
    absent = str(tmp_path / "nope.db")

    assert cochange_row_count(absent) == 0
    assert cochange_partners(absent, "src/a.py") == ()
    assert run_cochange_prior(_Adapter(absent, str(tmp_path)), ("src/a.py",)) == ""


def test_no_graph_configured_is_quiet(tmp_path: Path):
    assert cochange_row_count("") == 0
    assert cochange_partners("", "src/a.py") == ()
    assert run_cochange_prior(_Adapter("", str(tmp_path)), ("src/a.py",)) == ""


# --- reading the rows ------------------------------------------------------


def test_a_partner_is_found_from_either_column(tmp_path: Path):
    """The primary key is an ordered pair; the coupling relation is not."""

    db = _graph(tmp_path / "g.db", [("src/a.py", "src/b.py", 7)])

    from_a = cochange_partners(db, "src/a.py")
    from_b = cochange_partners(db, "src/b.py")

    assert [p.partner for p in from_a] == ["src/b.py"]
    assert [p.partner for p in from_b] == ["src/a.py"]


def test_count_is_the_support_because_the_schema_has_one_column(tmp_path: Path):
    db = _graph(tmp_path / "g.db", [("src/a.py", "src/b.py", 12)])

    partner = cochange_partners(db, "src/a.py")[0]

    assert partner.count == 12
    assert partner.support == 12


def test_the_window_is_unrecorded_because_the_table_does_not_store_it(tmp_path: Path):
    db = _graph(tmp_path / "g.db", [("src/a.py", "src/b.py", 3)])

    assert cochange_partners(db, "src/a.py")[0].window == COCHANGE_WINDOW_UNRECORDED


def test_a_recorded_window_is_reported_when_the_caller_supplies_one(tmp_path: Path):
    db = _graph(tmp_path / "g.db", [("src/a.py", "src/b.py", 3)])

    partner = cochange_partners(db, "src/a.py", window="commits<=500")[0]

    assert partner.window == "commits<=500"


def test_directional_confidence_and_published_window_are_read_from_graph(tmp_path: Path):
    path = tmp_path / "g.db"
    db = _graph(path)
    con = sqlite3.connect(db)
    try:
        con.execute("CREATE TABLE project_meta (key TEXT PRIMARY KEY, value TEXT)")
        con.executemany(
            "INSERT INTO project_meta(key,value) VALUES(?,?)",
            [
                ("derived_cochange_window_start", "oldest"),
                ("derived_cochange_window_end", "newest"),
            ],
        )
        con.execute(
            "INSERT INTO cochanges(file_a,file_b,count,commits_a,commits_b,confidence_a_to_b,confidence_b_to_a) VALUES(?,?,?,?,?,?,?)",
            ("src/a.py", "src/b.py", 4, 5, 4, 0.8, 1.0),
        )
        con.commit()
    finally:
        con.close()

    from_a = cochange_partners(db, "src/a.py")[0]
    from_b = cochange_partners(db, "src/b.py")[0]
    assert (from_a.commits_file, from_a.commits_partner, from_a.confidence) == (5, 4, 0.8)
    assert (from_b.commits_file, from_b.commits_partner, from_b.confidence) == (4, 5, 1.0)
    assert from_a.window == from_b.window == "oldest..newest"
    rendered = render_cochange_prior((from_a,))
    assert "confidence=0.80000000" in rendered
    assert "commits_file=5 commits_partner=4" in rendered


def test_legacy_nullable_directional_columns_do_not_fabricate_measurements(tmp_path: Path):
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    try:
        con.execute(
            "CREATE TABLE cochanges ("
            "file_a TEXT NOT NULL, file_b TEXT NOT NULL, count INTEGER NOT NULL, "
            "commits_a INTEGER, commits_b INTEGER, "
            "confidence_a_to_b REAL, confidence_b_to_a REAL)"
        )
        con.execute(
            "INSERT INTO cochanges(file_a,file_b,count) VALUES(?,?,?)",
            ("src/a.py", "src/b.py", 4),
        )
        con.commit()
    finally:
        con.close()

    partner = cochange_partners(path, "src/a.py")[0]
    assert partner.support == 4
    assert partner.commits_file is None
    assert partner.commits_partner is None
    assert partner.confidence is None
    rendered = render_cochange_prior((partner,))
    assert "confidence=unrecorded" in rendered
    assert "commits_file=" not in rendered


def test_partners_rank_by_support_then_path(tmp_path: Path):
    db = _graph(
        tmp_path / "g.db",
        [
            ("src/a.py", "src/low.py", 1),
            ("src/a.py", "src/high.py", 9),
            ("src/mid_b.py", "src/a.py", 5),
            ("src/mid_a.py", "src/a.py", 5),
        ],
    )

    partners = [p.partner for p in cochange_partners(db, "src/a.py", limit=4)]

    assert partners == ["src/high.py", "src/mid_a.py", "src/mid_b.py", "src/low.py"]


def test_partners_are_bounded(tmp_path: Path):
    db = _graph(
        tmp_path / "g.db",
        [("src/a.py", f"src/p{i}.py", 10 - i) for i in range(8)],
    )

    assert len(cochange_partners(db, "src/a.py")) == MAX_PARTNERS_PER_FILE
    assert len(cochange_partners(db, "src/a.py", limit=2)) == 2


def test_a_self_pair_is_never_a_partner(tmp_path: Path):
    db = _graph(tmp_path / "g.db", [("src/a.py", "src/a.py", 40)])

    assert cochange_partners(db, "src/a.py") == ()


def test_provenance_points_at_the_stored_row_not_at_the_query(tmp_path: Path):
    db = _graph(tmp_path / "g.db", [("src/a.py", "src/b.py", 7)])

    # Same row, reached from either end: provenance keeps the stored key order.
    assert cochange_partners(db, "src/a.py")[0].provenance == (
        "cochanges(file_a=src/a.py,file_b=src/b.py)"
    )
    assert cochange_partners(db, "src/b.py")[0].provenance == (
        "cochanges(file_a=src/a.py,file_b=src/b.py)"
    )


def test_only_the_cochanges_table_is_read(tmp_path: Path):
    """A prior may not touch the edge tables, so it cannot promote an edge."""

    db = _graph(tmp_path / "g.db", [("src/a.py", "src/b.py", 2)])
    statements: list[str] = []
    real_connect = sqlite3.connect

    def tracing_connect(*args, **kwargs):
        con = real_connect(*args, **kwargs)
        con.set_trace_callback(statements.append)
        return con

    sqlite3.connect = tracing_connect  # type: ignore[assignment]
    try:
        cochange_partners(db, "src/a.py")
    finally:
        sqlite3.connect = real_connect  # type: ignore[assignment]

    assert statements
    joined = " ".join(statements).lower()
    assert "cochanges" in joined
    for forbidden in ("edges", "nodes", "properties", "closure", "assertions"):
        assert forbidden not in joined


def test_row_count_reports_the_real_number(tmp_path: Path):
    db = _graph(
        tmp_path / "g.db",
        [("src/a.py", "src/b.py", 1), ("src/a.py", "src/c.py", 1)],
    )

    assert cochange_row_count(db) == 2


# --- rendering -------------------------------------------------------------


def _partner(**kwargs) -> CochangePartner:
    base = {
        "file_path": "src/a.py",
        "partner": "src/b.py",
        "count": 7,
        "support": 7,
        "window": COCHANGE_WINDOW_UNRECORDED,
        "provenance": "cochanges(file_a=src/a.py,file_b=src/b.py)",
    }
    return CochangePartner(**{**base, **kwargs})


def test_the_rendered_line_carries_partner_count_window_and_support():
    rendered = render_cochange_prior((_partner(),))

    assert "partner=src/b.py" in rendered
    assert "count=7" in rendered
    assert "support=7" in rendered
    assert f"window={COCHANGE_WINDOW_UNRECORDED}" in rendered
    assert "provenance=cochanges(file_a=src/a.py,file_b=src/b.py)" in rendered


def test_the_rendered_line_declares_a_prior_not_a_resolution():
    rendered = render_cochange_prior((_partner(),))

    assert "status=prior_not_resolution" in rendered
    assert "resolved" not in rendered
    assert "verified" not in rendered


def test_rendering_nothing_renders_nothing():
    assert render_cochange_prior(()) == ""


# --- the seam --------------------------------------------------------------


def test_the_seam_renders_a_dose_for_an_edited_or_viewed_file(tmp_path: Path):
    db = _graph(tmp_path / "g.db", [("src/a.py", "src/b.py", 4)])

    rendered = run_cochange_prior(_Adapter(db, str(tmp_path)), ("src/a.py",))

    assert "src/a.py" in rendered
    assert "partner=src/b.py" in rendered
    assert "revision=rev0" in rendered


def test_the_seam_never_reads_a_stale_graph(tmp_path: Path):
    db = _graph(tmp_path / "g.db", [("src/a.py", "src/b.py", 4)])
    adapter = _Adapter(db, str(tmp_path))
    adapter.graph_fresh = False

    assert run_cochange_prior(adapter, ("src/a.py",)) == ""


def test_the_seam_ignores_files_outside_the_repository(tmp_path: Path):
    db = _graph(tmp_path / "g.db", [("src/a.py", "src/b.py", 4)])
    outside = str(tmp_path.parent / "elsewhere" / "a.py")

    assert run_cochange_prior(_Adapter(db, str(tmp_path)), (outside,)) == ""


def test_the_seam_is_bounded_across_files(tmp_path: Path):
    db = _graph(
        tmp_path / "g.db",
        [(f"src/f{i}.py", f"src/p{i}.py", 3) for i in range(6)],
    )

    rendered = run_cochange_prior(
        _Adapter(db, str(tmp_path)), tuple(f"src/f{i}.py" for i in range(6))
    )

    assert 0 < len(rendered.splitlines()) <= 6


def test_the_evidence_type_is_the_one_attribution_already_knows():
    from gt_engine.attribution import feature_for_evidence

    assert COCHANGE_EVIDENCE_TYPE == "cochange_partner"
    assert feature_for_evidence(COCHANGE_EVIDENCE_TYPE) == "cochange_prior"


# --- the dose --------------------------------------------------------------


class _StagingAdapter(_Adapter):
    def __init__(self, graph_db: str, repo_root: str) -> None:
        super().__init__(graph_db, repo_root)
        self.staged: list[dict] = []

    def stage_model_visible_delivery(self, **kwargs) -> None:
        self.staged.append(kwargs)


def test_the_dose_is_tagged_staged_advisory_and_attributable(tmp_path: Path):
    from gt_engine.attribution import feature_for_evidence

    db = _graph(tmp_path / "g.db", [("src/a.py", "src/b.py", 4)])
    adapter = _StagingAdapter(db, str(tmp_path))

    dose = cochange_prior_dose(adapter, ("src/a.py",))

    assert dose.startswith("[GT_EVIDENCE:cochange_partner]\n")
    assert len(adapter.staged) == 1
    staged = adapter.staged[0]
    assert staged["kind"] == COCHANGE_EVIDENCE_TYPE
    assert staged["semantics"] == "advisory"
    assert staged["target"] == "src/a.py"
    assert feature_for_evidence(staged["kind"]) == "cochange_prior"


def test_an_empty_table_stages_nothing_at_all(tmp_path: Path):
    db = _graph(tmp_path / "g.db")
    adapter = _StagingAdapter(db, str(tmp_path))

    assert cochange_prior_dose(adapter, ("src/a.py",)) == ""
    assert adapter.staged == []


def test_the_dose_stays_inside_its_byte_ceiling(tmp_path: Path):
    db = _graph(
        tmp_path / "g.db",
        [("src/a.py", f"src/very_long_partner_name_{i}.py", 9) for i in range(3)]
        + [("src/c.py", f"src/another_long_partner_{i}.py", 8) for i in range(3)],
    )
    adapter = _StagingAdapter(db, str(tmp_path))

    dose = cochange_prior_dose(adapter, ("src/a.py", "src/c.py"))
    body = dose.split("\n", 1)[1]

    assert len(body.encode("utf-8")) <= COCHANGE_DOSE_BYTE_LIMIT
    # Dropped, never cut: a half-line is a half-claim.
    assert body.splitlines()
    for line in body.splitlines():
        assert line.endswith("status=prior_not_resolution"), line


def test_a_line_that_cannot_fit_at_all_stages_nothing(tmp_path: Path):
    """No dose is better than a dose whose provenance is cut in half."""

    long_a = "src/" + ("a" * 400) + ".py"
    long_b = "src/" + ("b" * 400) + ".py"
    db = _graph(tmp_path / "g.db", [(long_a, long_b, 5)])
    adapter = _StagingAdapter(db, str(tmp_path))

    assert cochange_prior_dose(adapter, (long_a,)) == ""
    assert adapter.staged == []


# --- the seam never lets a prior outrank a resolution ----------------------


def test_the_seam_collects_the_prior_but_ranks_it_below_current_evidence(
    tmp_path: Path,
):
    """Candidate collection must precede one deterministic ranked selection."""

    import inspect

    from gt_engine import miniswe_runtime

    source = inspect.getsource(miniswe_runtime._run_evidence)
    pipeline_at = source.index("result = run_evidence_pipeline(")
    prior_at = source.index("_cochange_prior(adapter")
    selection_at = source.index("winner = sorted(candidates")

    assert pipeline_at < prior_at < selection_at
    assert '10, cochange_metadata.get("kind", "cochange_partner")' in source
    assert "100 if event.test_outcome" in source


def test_the_seam_hook_is_quiet_when_the_action_touched_no_file(tmp_path: Path):
    from gt_engine.miniswe_runtime import _cochange_prior

    db = _graph(tmp_path / "g.db", [("src/a.py", "src/b.py", 4)])
    adapter = _StagingAdapter(db, str(tmp_path))

    assert _cochange_prior(adapter, "ls -la", ()) == ""
    assert adapter.staged == []


def test_the_seam_hook_covers_an_edited_file(tmp_path: Path):
    from gt_engine.miniswe_runtime import _cochange_prior

    db = _graph(tmp_path / "g.db", [("src/a.py", "src/b.py", 4)])
    adapter = _StagingAdapter(db, str(tmp_path))

    dose = _cochange_prior(adapter, "python -c pass", ("src/a.py",))

    assert "partner=src/b.py" in dose
    assert adapter.staged[0]["kind"] == COCHANGE_EVIDENCE_TYPE


def test_the_seam_hook_changes_nothing_on_a_graph_with_no_rows(tmp_path: Path):
    """The state every depth-1 fixture ships in: the runtime must be unchanged.

    `_run_evidence` previously ended in `return cap_evidence(result.rendered)`,
    which is `""` when the pipeline said nothing. The hook is reached only in
    that case and returns `""` here, so an empty `cochanges` table leaves the
    delivered bytes byte-identical to the pre-item-6 runtime.
    """

    from gt_engine.miniswe_runtime import _cochange_prior

    db = _graph(tmp_path / "g.db")
    adapter = _StagingAdapter(db, str(tmp_path))

    assert _cochange_prior(adapter, "cat src/a.py", ("src/a.py",)) == ""
    assert adapter.staged == []


def test_the_seam_hook_never_raises_on_a_broken_adapter(tmp_path: Path):
    """Correct-or-quiet: a prior may not be the thing that kills a turn."""

    from gt_engine.miniswe_runtime import _cochange_prior

    class _Broken:
        repo_root = str(tmp_path)
        repository_revision = "rev0"

        @property
        def graph_db(self):
            raise RuntimeError("graph handle exploded")

    assert _cochange_prior(_Broken(), "cat src/a.py", ("src/a.py",)) == ""
