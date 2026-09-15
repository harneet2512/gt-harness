"""Model-facing delivery payload and plan-check binding regression tests.

Smoke-20 (run 34715686102) proved the delivery layer stripped payload to
pointers - localization 7/19 consumed, select_catalog 0/19 - and that the
persistent-plan check channel could never execute: validation_source_digest
returned "" for suite commands, so every check was discarded
``test_source_not_bound`` and no predicate ever proved.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gt_engine.delivery_budget import (
    MAX_LOCALIZATION_DELIVERIES,
    compact_localization,
)
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.persistent_plan.checks import (
    CheckSpec,
    classify_bound_check,
    validation_source_digest,
)
from gt_engine.retrieval import render_semantic_localization


def _adapter(tmp_path) -> MiniSweAdapter:
    return MiniSweAdapter(task_id="payload", state_dir=tmp_path, predicates=[])


def _events(adapter: MiniSweAdapter) -> list[dict]:
    return [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# Localization render: payload, not pointers
# ---------------------------------------------------------------------------


def test_localization_render_carries_symbol_snippet_and_plain_reasons() -> None:
    items = [
        {
            "anchor": "crates/oxvg_optimiser/src/jobs/inline_styles.rs:421",
            "qualified_name": "CollectMatchingSelectors.is_selector_removable",
            "label": "Method",
            "snippet": "fn is_selector_removable(selector: &Selector) -> bool",
            "reasons": ["retrieval:lexical", "retrieval:dense"],
            "score": 0.029,
        },
        {
            "anchor": "crates/oxvg_ast/src/arena.rs:49",
            "name": "Arena",
            "label": "Struct",
            "snippet": "",
            "reasons": ["retrieval:lexical"],
            "score": 0.016,
        },
    ]
    rendered = render_semantic_localization(items)
    lines = rendered.splitlines()
    assert lines[0] == "[GT_EVIDENCE:localization]"
    assert len(lines) == 3
    # Payload present where the old render had only anchor+score.
    assert "is_selector_removable" in lines[1]
    assert "Method" in lines[1]
    assert "fn is_selector_removable" in lines[1]
    assert "name/text match" in lines[1]
    assert "semantic match" in lines[1]
    assert "score=" not in rendered
    assert "retrieval:" not in rendered
    # Every item line keeps the path:line prefix compact_localization needs.
    for line in lines[1:]:
        head = line.split(None, 1)[0]
        assert head.rsplit(":", 1)[1].isdigit()


def test_localization_render_survives_lane_compaction() -> None:
    items = [
        {
            "anchor": f"src/module_{index}.py:{index + 1}",
            "qualified_name": f"module_{index}.handler_{index}",
            "label": "Function",
            "snippet": "x" * 140,
            "reasons": ["retrieval:lexical"],
        }
        for index in range(20)
    ]
    compacted = compact_localization(render_semantic_localization(items))
    lines = compacted.splitlines()
    assert lines[0] == "[GT_EVIDENCE:localization]"
    # Whole items dropped, never a sliced factual statement.
    assert 1 < len(lines) < 21
    assert len(compacted.encode("utf-8")) <= 1_400
    items_shown = [line for line in lines[1:] if "withheld by byte budget" not in line]
    assert all("handler_" in line for line in items_shown)
    # Dropped items are declared model-visibly, not silently absent.
    assert lines[-1].endswith("withheld by byte budget")


def test_localization_render_without_optional_fields_still_anchors() -> None:
    rendered = render_semantic_localization(
        [{"anchor": "src/mod.py:12", "reasons": [], "score": 0.5}]
    )
    assert rendered.splitlines()[1].startswith("src/mod.py:12")


# ---------------------------------------------------------------------------
# Check binding: suite commands bind the repo's test surface
# ---------------------------------------------------------------------------


class _File:
    def __init__(self, path: str, kind: str = "file", sha256: str = "0" * 64,
                 captured: bytes | None = None):
        self.path = path
        self.kind = kind
        self.sha256 = sha256
        self.captured = captured


class _Snapshot:
    def __init__(self, root: Path, files: list[_File], complete: bool = True):
        self.root = str(root)
        self.files = files
        self.complete = complete


def _spec(tmp_path: Path, argv: list[str], **extra) -> CheckSpec:
    return CheckSpec.from_dict(
        {"argv": argv, "cwd": ".", "requirement_ids": ["req-1"], **extra},
        str(tmp_path),
    )


def test_suite_command_binds_workspace_test_surface(tmp_path: Path) -> None:
    files = [
        _File("src/lib.rs"),
        _File("tests/integration_test.rs", sha256="a" * 64),
        _File("crates/core/tests/unit.rs", sha256="b" * 64),
        _File("package.json", sha256="c" * 64),
    ]
    spec = _spec(tmp_path, ["cargo", "test"])
    digest = validation_source_digest(spec, _Snapshot(tmp_path, files))
    assert digest  # previously "" -> test_source_not_bound -> never ran


def test_suite_command_without_test_surface_stays_unbound(tmp_path: Path) -> None:
    files = [_File("src/lib.rs"), _File("README.md")]
    spec = _spec(tmp_path, ["cargo", "test"])
    assert validation_source_digest(spec, _Snapshot(tmp_path, files)) == ""


def test_argv_path_binding_still_wins_over_surface_fallback(tmp_path: Path) -> None:
    files = [
        _File("tests/a_test.py", sha256="a" * 64),
        _File("tests/b_test.py", sha256="b" * 64),
    ]
    spec = _spec(tmp_path, ["pytest", "tests/a_test.py"])
    digest = validation_source_digest(spec, _Snapshot(tmp_path, files))
    assert digest


def test_incomplete_snapshot_binds_nothing(tmp_path: Path) -> None:
    files = [_File("tests/x_test.rs")]
    spec = _spec(tmp_path, ["cargo", "test"])
    assert validation_source_digest(
        spec, _Snapshot(tmp_path, files, complete=False)
    ) == ""


WORKSPACE_MANIFEST = b'[workspace]\nmembers = ["core/*"]\n'
ENGINE_MANIFEST = b'[package]\nname = "boa_engine"\nversion = "0.1.0"\n'


def _cargo_repo(other_test_sha: str, engine_test_sha: str = "e" * 64) -> list:
    return [
        _File("Cargo.toml", captured=WORKSPACE_MANIFEST),
        _File("core/engine/Cargo.toml", captured=ENGINE_MANIFEST),
        _File("core/engine/tests/engine_test.rs", sha256=engine_test_sha),
        _File("crates/other/tests/other_test.rs", sha256=other_test_sha),
    ]


def test_cargo_package_flag_binds_the_member_manifest_dir(tmp_path: Path) -> None:
    # ``cargo test -p boa_engine`` selects by package name, not path: the
    # manifest declaring boa_engine lives at core/engine/. Resolving the flag
    # value as a filesystem path misses the package and degrades the binding
    # to the whole test surface - which then invalidates on unrelated edits.
    spec = _spec(tmp_path, ["cargo", "test", "-p", "boa_engine"])
    digest = validation_source_digest(
        spec, _Snapshot(tmp_path, _cargo_repo("f" * 64))
    )
    assert digest
    # An unrelated crate's test file changing must NOT invalidate the bound
    # identity: the digest is scoped to core/engine plus runner config.
    same = validation_source_digest(
        spec, _Snapshot(tmp_path, _cargo_repo("9" * 64))
    )
    assert same == digest
    # ...but the member's own test tree is bound - changing it rebinds.
    changed = validation_source_digest(
        spec, _Snapshot(tmp_path, _cargo_repo("f" * 64, engine_test_sha="z" * 64))
    )
    assert changed != digest


def test_cargo_package_flag_falls_back_when_manifest_missing(tmp_path: Path) -> None:
    files = [_File("tests/x_test.rs", sha256="a" * 64)]
    spec = _spec(tmp_path, ["cargo", "test", "-p", "not_a_package"])
    # No manifest declares the package: the honest fallback is the whole test
    # surface (over-broad but bound), never a silent empty digest.
    assert validation_source_digest(spec, _Snapshot(tmp_path, files))


# ---------------------------------------------------------------------------
# classify_bound_check: a passing bound check is reachable evidence
# ---------------------------------------------------------------------------


class _Execution:
    outcome = "pass"
    returncode = 0
    repository_revision = "rev"
    environment_sha256 = "env"
    timed_out = False
    command_sha256 = ""
    protocol = ""


def _execution(spec: CheckSpec) -> _Execution:
    import hashlib

    execution = _Execution()
    execution.command_sha256 = hashlib.sha256(spec.command.encode()).hexdigest()
    return execution


def test_passing_suite_check_is_check_passed_without_selected_ids(tmp_path: Path) -> None:
    spec = _spec(tmp_path, ["cargo", "test"], test_source_digest="d" * 64)
    observation = classify_bound_check(
        spec, _execution(spec), before_revision="rev", after_revision="rev",
        capture_complete=True, test_ids=(), test_source_digest="d" * 64,
    )
    assert observation.state == "CHECK_PASSED"


def test_selected_test_ids_must_appear_when_selected(tmp_path: Path) -> None:
    spec = _spec(
        tmp_path, ["pytest", "tests/a.py"],
        selected_test_ids=["tests/a.py::test_one"],
        test_source_digest="d" * 64,
    )
    observation = classify_bound_check(
        spec, _execution(spec), before_revision="rev", after_revision="rev",
        capture_complete=True, test_ids=("tests/a.py::test_one",),
        test_source_digest="d" * 64,
    )
    assert observation.state == "CHECK_PASSED"
    missing = classify_bound_check(
        spec, _execution(spec), before_revision="rev", after_revision="rev",
        capture_complete=True, test_ids=("tests/a.py::test_other",),
        test_source_digest="d" * 64,
    )
    assert missing.state == "UNVERIFIED"


def test_failed_check_reports_check_failed(tmp_path: Path) -> None:
    spec = _spec(tmp_path, ["cargo", "test"], test_source_digest="d" * 64)
    execution = _execution(spec)
    execution.outcome = "fail"
    execution.returncode = 1
    observation = classify_bound_check(
        spec, execution, before_revision="rev", after_revision="rev",
        capture_complete=True, test_ids=(), test_source_digest="d" * 64,
    )
    assert observation.state == "CHECK_FAILED"


# ---------------------------------------------------------------------------
# Check rebind: drifted test source gets a new honest identity
# ---------------------------------------------------------------------------


def test_rebind_check_source_preserves_requirements_and_swaps_pending(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    spec = _spec(tmp_path, ["cargo", "test"], test_source_digest="a" * 64)
    adapter._check_specs = {spec.check_id: spec}
    adapter._pending_check_ids = {spec.check_id}
    rebound = adapter._rebind_check_source(spec, "b" * 64)
    assert rebound.check_id != spec.check_id
    assert rebound.requirement_ids == spec.requirement_ids
    assert rebound.test_source_digest == "b" * 64
    assert spec.check_id not in adapter._check_specs
    assert rebound.check_id in adapter._check_specs
    assert adapter._pending_check_ids == {rebound.check_id}
    events = _events(adapter)
    rebound_events = [row for row in events if row.get("event") == "plan_check_rebound"]
    assert rebound_events and rebound_events[0]["previous_check_id"] == spec.check_id


# ---------------------------------------------------------------------------
# Localization re-delivery: content-keyed, capped
# ---------------------------------------------------------------------------


def _offer_localization(adapter: MiniSweAdapter, iteration: int, text: str) -> bool:
    return adapter.admit_model_visible_delivery(
        lane="sealed", kind="localization", rendered=text,
        action_index=iteration, iteration=iteration, dedup_key=text,
    )


def _bind(adapter: MiniSweAdapter, *texts: str) -> None:
    adapter.bind_provider_payload(
        {"messages": [{"role": "tool", "content": " ".join(texts)}]}
    )


def test_changed_localization_content_may_redeliver(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    assert _offer_localization(adapter, 0, "ranked rows v1")
    _bind(adapter, "ranked rows v1")
    # Identical bytes already fired: still refused.
    assert not _offer_localization(adapter, 1, "ranked rows v1")
    # New ranked content (the information need moved) is admitted.
    assert _offer_localization(adapter, 1, "ranked rows v2")
    _bind(adapter, "ranked rows v2")
    assert _offer_localization(adapter, 2, "ranked rows v3")
    _bind(adapter, "ranked rows v3")
    # The per-task ceiling still binds - re-localization cannot loop.
    assert not _offer_localization(adapter, 3, "ranked rows v4")
    refusals = [
        row for row in _events(adapter)
        if row.get("event") == "delivery_refused"
    ]
    assert any(row["reason"] == "localization_fire_once" for row in refusals)
    assert any(row["reason"] == "localization_task_ceiling" for row in refusals)
    delivered = [
        row for row in _events(adapter)
        if row.get("event") == "evidence_delivery"
        and row.get("evidence_type") == "localization"
    ]
    assert len(delivered) == MAX_LOCALIZATION_DELIVERIES


def test_pending_localization_still_serialized_per_decision(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    assert _offer_localization(adapter, 0, "v1")
    assert not _offer_localization(adapter, 0, "v2")


def _offer_localization_targeted(
    adapter: MiniSweAdapter, iteration: int, text: str, target: str
) -> bool:
    return adapter.admit_model_visible_delivery(
        lane="sealed", kind="localization", rendered=text,
        action_index=iteration, iteration=iteration, dedup_key=text,
        target=target,
    )


def test_novel_top_target_bypasses_soft_ceiling_until_hard_cap(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    tops = ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py"]
    for iteration, top in enumerate(tops):
        assert _offer_localization_targeted(
            adapter, iteration, f"ranked rows for {top}", top
        )
        _bind(adapter, f"ranked rows for {top}")
    # Past the soft cap, a re-rank under an already-delivered top is churn and
    # is refused - the anti-spam property the ceiling exists for.
    assert not _offer_localization_targeted(
        adapter, len(tops), "re-ranked rows for a.py", "a.py"
    )
    # Even a novel top is refused once the hard bound is spent.
    assert not _offer_localization_targeted(
        adapter, len(tops) + 1, "ranked rows for g.py", "g.py"
    )
    refusals = [
        row for row in _events(adapter)
        if row.get("event") == "delivery_refused"
        and row.get("reason") == "localization_task_ceiling"
    ]
    assert len(refusals) == 2
    assert {row.get("target") for row in refusals} == {"a.py", "g.py"}
    delivered = [
        row for row in _events(adapter)
        if row.get("event") == "evidence_delivery"
        and row.get("evidence_type") == "localization"
    ]
    assert len(delivered) == len(tops)


def test_same_top_churn_refused_at_soft_ceiling_while_novel_top_admits(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    for iteration, top in enumerate(["a.py", "b.py", "c.py"]):
        assert _offer_localization_targeted(
            adapter, iteration, f"ranked rows for {top}", top
        )
        _bind(adapter, f"ranked rows for {top}")
    # Soft cap spent: same-top churn refused, novel top admitted.
    assert not _offer_localization_targeted(
        adapter, 3, "re-ranked rows for a.py", "a.py"
    )
    assert _offer_localization_targeted(adapter, 3, "ranked rows for d.py", "d.py")


# ---------------------------------------------------------------------------
# Check observation -> obligation predicate evidence (the smoke-20 gap:
# commands_run=0 everywhere, nothing ever proved, submitted_unverified always)
# ---------------------------------------------------------------------------


class _ExecSnapshot:
    def __init__(self, root: Path, files: list[_File], revision: str = "rev"):
        self.root = str(root)
        self.files = files
        self.complete = True
        self.revision = revision


class _Env:
    class _Store:
        @staticmethod
        def bytes(_sha: str) -> bytes:
            return b""

    evidence_store = _Store()


def test_agent_run_check_observes_and_proves_mapped_predicates(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from gt_engine.miniswe_controller import Predicate, PredicateStatus

    adapter = _adapter(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter.repo_root = str(repo)
    adapter.repository_revision = "rev"

    row = SimpleNamespace(row_id="req-1", verification_command="cargo test")
    adapter.persistent_plan = SimpleNamespace(rows=[row])
    adapter.plan_row_predicates = {"req-1": ("pred-1",)}
    adapter.predicates["pred-1"] = Predicate("pred-1", "obligation text")
    adapter._status["pred-1"] = PredicateStatus.UNKNOWN
    adapter.publish_plan_state = lambda: None

    spec = _spec(repo, ["cargo", "test"])
    adapter._check_specs = {spec.check_id: spec}
    adapter._pending_check_ids = {spec.check_id}

    files = [_File("src/lib.rs"), _File("tests/t.rs", sha256="a" * 64)]
    before = _ExecSnapshot(repo, files)
    after = _ExecSnapshot(repo, files)
    adapter.observe_plan_checks(
        "cargo test",
        {"returncode": 0, "output": "test result: ok",
         "extra": {"environment_sha256": "env", "cwd": str(repo),
                   "capture_complete": True}},
        before, after, _Env(),
    )

    # The unbound spec is rebound to the workspace test surface on first
    # observation, so the observation is keyed by the rebound check id.
    observation = next(iter(adapter._plan_check_observations.values()))
    assert observation.state == "CHECK_PASSED"
    assert adapter.plan_row_state("req-1") == "CHECK_PASSED"
    assert adapter._status["pred-1"] is PredicateStatus.GREEN
    assert adapter.unmet_plan_rows() == ()
    events = _events(adapter)
    assert any(
        row.get("event") == "plan_check_observed"
        and row.get("state") == "CHECK_PASSED" for row in events
    )


def test_compiled_predicates_are_journaled(tmp_path: Path) -> None:
    """Smoke-20 audit showed predicate_compiled_count=0 on every task: the
    Mini-SWE path compiled predicates in memory but only the legacy bridge
    journaled them."""
    from gt_engine.task_contract import Obligation, TaskContract

    contract = TaskContract(
        "ARTIFACT",
        (Obligation("obl-1", "Create output.json artifact.", "test"),),
    )
    adapter = MiniSweAdapter(
        task_id="payload",
        state_dir=tmp_path / "journal",
        predicates=[],
        contract=contract,
    )
    rows = [
        row for row in _events(adapter)
        if row.get("event") == "contract.predicate_compiled"
    ]
    assert len(rows) == 1
    assert rows[0]["phase"] == "task_start"
    assert rows[0]["obligation_id"] == "obl-1"
    assert rows[0]["predicate_id"]
    assert rows[0]["kind"]


def test_failing_agent_run_check_does_not_fabricate_green(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from gt_engine.miniswe_controller import Predicate, PredicateStatus

    adapter = _adapter(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter.repo_root = str(repo)
    adapter.repository_revision = "rev"
    row = SimpleNamespace(row_id="req-1", verification_command="cargo test")
    adapter.persistent_plan = SimpleNamespace(rows=[row])
    adapter.plan_row_predicates = {"req-1": ("pred-1",)}
    adapter.predicates["pred-1"] = Predicate("pred-1", "obligation text")
    adapter._status["pred-1"] = PredicateStatus.UNKNOWN
    adapter.publish_plan_state = lambda: None

    spec = _spec(repo, ["cargo", "test"])
    adapter._check_specs = {spec.check_id: spec}
    adapter._pending_check_ids = {spec.check_id}
    files = [_File("tests/t.rs", sha256="a" * 64)]
    adapter.observe_plan_checks(
        "cargo test",
        {"returncode": 1, "output": "test result: FAILED",
         "extra": {"environment_sha256": "env", "cwd": str(repo),
                   "capture_complete": True}},
        _ExecSnapshot(repo, files), _ExecSnapshot(repo, files), _Env(),
    )
    assert adapter._status["pred-1"] is not PredicateStatus.GREEN
    assert adapter.unmet_plan_rows() == ("req-1",)


def test_changelog_fragment_edit_keeps_a_bound_check_proof(tmp_path: Path) -> None:
    """F7 (run 34766499875, epoch 36): the towncrier fragment
    ``changes/460.enhancement`` discarded all eleven GREEN bound-check
    predicates because every receipt carried the conservative workspace-wide
    footprint. A receipt bound to declared test sources survives the edit:
    the path is outside every declared scope root and has no import channel.
    A code edit inside or outside the scope still invalidates it."""
    from types import SimpleNamespace

    from gt_engine.miniswe_controller import Predicate, PredicateStatus

    adapter = _adapter(tmp_path)
    adapter.start_task()
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter.repo_root = str(repo)
    adapter.repository_revision = "rev"

    row = SimpleNamespace(row_id="req-1",
                          verification_command="pytest tests/test_snapshots.py")
    adapter.persistent_plan = SimpleNamespace(rows=[row])
    adapter.plan_row_predicates = {"req-1": ("pred-1",)}
    adapter.predicates["pred-1"] = Predicate("pred-1", "obligation text")
    adapter._status["pred-1"] = PredicateStatus.UNKNOWN
    adapter.publish_plan_state = lambda: None

    spec = _spec(repo, ["pytest", "tests/test_snapshots.py"],
                 test_source_paths=["tests/test_snapshots.py"])
    adapter._check_specs = {spec.check_id: spec}
    adapter._pending_check_ids = {spec.check_id}
    files = [_File("tests/test_snapshots.py", sha256="a" * 64)]
    adapter.observe_plan_checks(
        "pytest tests/test_snapshots.py",
        {"returncode": 0, "output": "1 passed",
         "extra": {"environment_sha256": "env", "cwd": str(repo),
                   "capture_complete": True}},
        _ExecSnapshot(repo, files), _ExecSnapshot(repo, files), _Env(),
    )
    assert adapter._status["pred-1"] is PredicateStatus.GREEN

    adapter.note_edit(["changes/460.enhancement"])
    assert adapter._status["pred-1"] is PredicateStatus.GREEN

    adapter.note_edit(["src/mod.py"])
    assert adapter._status["pred-1"] is PredicateStatus.UNKNOWN


# ---------------------------------------------------------------------------
# dense_rank runtime-embed ceiling: cold corpus degrades, never starves
# ---------------------------------------------------------------------------


def test_dense_rank_abstains_when_runtime_embed_exceeds_budget(tmp_path: Path, monkeypatch) -> None:
    import gt_engine.dense_runtime as dense_runtime
    from gt_engine import retrieval

    embedded_calls: list[list[str]] = []

    def fake_embed_queries(model_root, queries):
        return [tuple([0.1] * 768) for _ in queries]

    def fake_embed_texts(model_root, texts):
        embedded_calls.append(list(texts))
        return [tuple([0.1] * 768) for _ in texts]

    monkeypatch.setattr(dense_runtime, "embed_queries", fake_embed_queries)
    monkeypatch.setattr(dense_runtime, "embed_texts", fake_embed_texts)

    class _Lookup:
        vectors: dict = {}
        hits = 0
        misses = 0
        missing_stable_ids: list = []
        dimension = 768
        reason = "cold_store"

    class _DocumentLookup:
        vectors: dict = {}
        hits = 0
        misses = 0
        reason = "cold_store"
        recipe_id = "test"

    monkeypatch.setattr(
        retrieval.contract_embeddings, "lookup_document_vectors",
        lambda store_path, documents, missing: _DocumentLookup(),
    )

    node_stable_ids = {index: f"stable-{index}" for index in range(600)}
    documents = {stable: f"doc {stable}" for stable in node_stable_ids.values()}
    ranking = retrieval._rank_from_store(
        query="q", model_root=tmp_path, lookup=_Lookup(),
        documents=documents, node_stable_ids=node_stable_ids, k=4,
        store_path=tmp_path / "store.sqlite", source_revision="rev",
        graph_identity="graph",
    )
    assert ranking.available is False
    assert ranking.reason == "dense_index_not_ready"
    assert ranking.detail["runtime_embed_missing"] == 600
    # No corpus-scale embedding ran on the query path.
    assert embedded_calls == []
