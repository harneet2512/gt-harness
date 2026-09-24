"""Canonical static-intelligence suite over the real produced graph.

Every case dispatches a certified typed kind through the same
``build_action_request`` -> ``execute_typed_action`` pipeline the
model-facing ``groundtruth`` tool uses (``run_typed`` is exactly what the
``gt_engine.capabilities`` facades call), then pins the scrubbed compiled
observation against a golden JSON under ``goldens/static/``.

Goldens are location-free: ``scrub_payload`` removes absolute paths and
hash fields derived from them, so a golden pins the *answer* — routes,
edges, omissions, decisions — never the tmp dir the graph happened to be
built in. Regenerate only with ``GT_UPDATE_GOLDENS=1``.

The suite skips cleanly when no runnable producer exists (see conftest for
the resolution policy); the skipif below is a file-existence pre-filter so
collection reports the skip without paying for a subprocess probe.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("groundtruth.runtime.deterministic_queries")

from gt_engine.capabilities._query import run_typed  # noqa: E402
from gt_engine.miniswe_typed_actions import (  # noqa: E402
    QUERY_RESULT_MAX_BYTES,
    build_action_request,
    execute_typed_action,
)
from gt_engine.typed_output_bounds import bound_compiled_observation  # noqa: E402
from tests.canonical.conftest import (  # noqa: E402
    FIXTURE_REVISION,
    golden_assert,
    producer_candidates,
)

# File-existence pre-filter; the session fixture does the authoritative
# `-build-info` probe and skips with the precise reason when every
# candidate is missing or unrunnable.
_PRODUCER_MAY_EXIST = any(
    Path(candidate).is_file() for candidate in producer_candidates()
) or os.environ.get("GT_INDEX_BINARY")

pytestmark = pytest.mark.skipif(
    not _PRODUCER_MAY_EXIST,
    reason="no gt-index producer candidate exists on this host",
)


def _execute(
    session,
    conf: dict,
    kind: str,
    arguments: dict[str, Any],
    case: str,
) -> tuple[dict, dict]:
    """Run one certified typed kind; return (raw result, decoded payload).

    Uses the session-bound engine's repo_root/graph_db exactly like
    ``capabilities._query.run_typed``, keeping the raw ``output`` string so
    the byte bound is measured on what the model would actually receive.
    """
    engine = session._engine
    request = build_action_request(
        {
            "gt_action": {"kind": kind, "arguments": dict(arguments)},
            "tool_call_id": f"canon:{kind}:{case}",
        },
        repo_root=engine.repo_root,
        configuration=conf,
    )
    result = execute_typed_action(
        request, repo_root=engine.repo_root, graph_db=engine.graph_db or None
    )
    payload = json.loads(result["output"])
    assert len(result["output"].encode("utf-8")) <= QUERY_RESULT_MAX_BYTES
    return result, payload


# ── golden-pinned typed kinds over the produced graph ───────────────────


def test_definition_sanitize(polyglot_session, polyglot_conf, polyglot_repo):
    session, _adapter = polyglot_session
    _, payload = _execute(
        session, polyglot_conf,
        "definition", {"symbol": "sanitize"}, "sanitize",
    )
    golden_assert("static/definition_sanitize", payload, polyglot_repo)
    answer = payload["direct_answer"]
    assert answer is not None
    locations = json.dumps(answer)
    assert "pyapp/server.py" in locations


def test_references_sanitize(polyglot_session, polyglot_conf, polyglot_repo):
    session, _adapter = polyglot_session
    _, payload = _execute(
        session, polyglot_conf,
        "references", {"symbol": "sanitize"}, "sanitize",
    )
    golden_assert("static/references_sanitize", payload, polyglot_repo)
    answer = payload["direct_answer"]
    assert answer is not None
    # The production helper is referenced by the handler and by the test.
    assert "pyapp/server.py" in json.dumps(answer)


def test_callers_execute(polyglot_session, polyglot_conf, polyglot_repo):
    session, _adapter = polyglot_session
    _, payload = _execute(
        session, polyglot_conf,
        "callers", {"symbol": "execute", "depth": 2}, "execute",
    )
    golden_assert("static/callers_execute", payload, polyglot_repo)
    answer = payload["direct_answer"]
    assert answer is not None
    callers = json.dumps(answer)
    assert "run_query" in callers


def test_symbol_context_friendly(polyglot_session, polyglot_conf, polyglot_repo):
    session, _adapter = polyglot_session
    _, payload = _execute(
        session, polyglot_conf,
        "symbol_context", {"symbol": "Friendly", "language": "typescript"},
        "friendly_ts",
    )
    golden_assert("static/symbol_context_friendly", payload, polyglot_repo)
    answer = payload["direct_answer"]
    assert answer is not None
    assert "tsapp/server.ts" in json.dumps(answer)


def test_route_map(polyglot_session, polyglot_conf, polyglot_repo):
    session, _adapter = polyglot_session
    _, payload = _execute(
        session, polyglot_conf, "route_map", {}, "all"
    )
    golden_assert("static/route_map", payload, polyglot_repo)
    rows = payload["direct_answer"]["routes"]
    assert {
        "/api/items",
        "/api/render",
        "/api/orders",
        "/api/orders/total",
    } <= {row["route"] for row in rows}
    # /api/items is declared in three languages; each gets its own row.
    items = [row for row in rows if row["route"] == "/api/items"]
    items_py = next(row for row in items if row["handler_file"] == "pyapp/server.py")
    assert items_py["handler"] == "list_items"
    assert items_py["method"] == "GET"
    # Depends injection is reported on the Flask handler.
    assert items_py["injections"][0]["provider"] == "get_store"
    # The Express twin carries the app.use middleware fact.
    items_ts = next(row for row in items if row["handler_file"] == "tsapp/server.ts")
    assert items_ts["middleware"][0]["name"] == "audit"
    # The JS consumer calls the colliding /api/items routes at file level.
    assert any(
        consumer["file"] == "client/web.js"
        for row in items
        for consumer in row["consumers"]
    )


def test_api_impact_items(polyglot_session, polyglot_conf, polyglot_repo):
    session, _adapter = polyglot_session
    _, payload = _execute(
        session, polyglot_conf,
        "api_impact", {"route": "/api/items"}, "items",
    )
    golden_assert("static/api_impact_items", payload, polyglot_repo)
    routes = payload["direct_answer"]["routes"]
    assert any(route["handler"] == "list_items" for route in routes)


def test_taint_items_to_execute(polyglot_session, polyglot_conf, polyglot_repo):
    session, _adapter = polyglot_session
    _, payload = _execute(
        session, polyglot_conf,
        "taint",
        {"source": "list_items", "sink": "execute", "depth": 4},
        "items_execute",
    )
    golden_assert("static/taint_items_execute", payload, polyglot_repo)
    paths = [path["path"] for path in payload["direct_answer"]["paths"]]
    assert ["list_items", "run_query", "execute"] in paths
    omissions = set(payload["evidence"]["omissions"])
    assert "symbol_level_reachability_only" in omissions


def test_processes(polyglot_session, polyglot_conf, polyglot_repo):
    session, _adapter = polyglot_session
    _, payload = _execute(
        session, polyglot_conf, "processes", {}, "all"
    )
    golden_assert("static/processes", payload, polyglot_repo)
    processes = payload["direct_answer"]["processes"]
    assert processes, "test-witnessed flows must exist in this graph"
    labels = {proc["label"] for proc in processes}
    # Assertion-witnessed chains from the Python, Go and Java test files.
    assert "format_total -> round_price" in labels
    assert "Compute -> helper" in labels
    assert "greet -> prefix" in labels
    assert all(proc["witnessed"] for proc in processes)


def test_slice_python_list_items(polyglot_session, polyglot_conf, polyglot_repo):
    session, _adapter = polyglot_session
    _, payload = _execute(
        session, polyglot_conf,
        "slice", {"symbol": "list_items", "line": 83}, "py_items",
    )
    golden_assert("static/slice_list_items", payload, polyglot_repo)
    (record,) = payload["direct_answer"]["slices"]
    assert record["file_path"] == "pyapp/server.py"
    # raw -> cleaned -> out chain: all three assignments feed line 83.
    assert {80, 82, 83} <= set(record["slice_lines"])


def test_slice_go_compute(polyglot_session, polyglot_conf, polyglot_repo):
    session, _adapter = polyglot_session
    _, payload = _execute(
        session, polyglot_conf,
        "slice",
        {"symbol": "Compute", "line": 36, "language": "go"},
        "go_compute",
    )
    golden_assert("static/slice_compute", payload, polyglot_repo)
    (record,) = payload["direct_answer"]["slices"]
    assert record["file_path"] == "gosvc/main.go"
    assert record["substrate"] == "persisted_cfg"
    assert 36 in record["slice_lines"]


def test_shape_check_friendly(polyglot_session, polyglot_conf, polyglot_repo):
    session, _adapter = polyglot_session
    _, payload = _execute(
        session, polyglot_conf,
        "shape_check", {"symbol": "Friendly", "language": "typescript"},
        "friendly_ts",
    )
    golden_assert("static/shape_check_friendly", payload, polyglot_repo)
    answer = payload["direct_answer"]
    assert answer["check_count"] >= 1


def test_rename_sanitize(polyglot_session, polyglot_conf, polyglot_repo):
    session, _adapter = polyglot_session
    _, payload = _execute(
        session, polyglot_conf,
        "rename",
        {"symbol": "sanitize", "new_name": "clean_input"},
        "sanitize",
    )
    golden_assert("static/rename_sanitize", payload, polyglot_repo)
    answer = payload["direct_answer"]
    assert "pyapp/server.py" in answer["files_to_touch"]


def test_tool_map(polyglot_session, polyglot_conf, polyglot_repo):
    session, _adapter = polyglot_session
    _, payload = _execute(
        session, polyglot_conf, "tool_map", {}, "all"
    )
    golden_assert("static/tool_map", payload, polyglot_repo)
    assert isinstance(payload["direct_answer"]["tools"], list)


def test_exact_literal_search(polyglot_session, polyglot_conf, polyglot_repo):
    session, _adapter = polyglot_session
    _, payload = _execute(
        session, polyglot_conf,
        "exact_literal_search",
        {"literal": "Depends(get_store)", "paths": ["pyapp"]},
        "depends",
    )
    golden_assert("static/exact_literal_search_depends", payload, polyglot_repo)
    matches = payload["direct_answer"]["matches"]
    assert {row["path"] for row in matches} == {"pyapp/server.py"}
    assert {row["line"] for row in matches} == {79, 88}


def test_syntax_server_py(polyglot_session, polyglot_conf, polyglot_repo):
    session, _adapter = polyglot_session
    _, payload = _execute(
        session, polyglot_conf,
        "syntax", {"path": "pyapp/server.py"}, "server_py",
    )
    golden_assert("static/syntax_server_py", payload, polyglot_repo)
    assert payload["direct_answer"] is not None


# ── run_typed parity: the facade pipeline is the same pipeline ──────────


def test_run_typed_facade_pipeline_agrees(polyglot_session, polyglot_conf):
    session, _adapter = polyglot_session
    via_facade = run_typed(
        session, "definition", {"symbol": "sanitize"},
        configuration=polyglot_conf,
    )
    engine = session._engine
    request = build_action_request(
        {
            "gt_action": {"kind": "definition", "arguments": {"symbol": "sanitize"}},
            "tool_call_id": "capability:definition",
        },
        repo_root=engine.repo_root,
        configuration=polyglot_conf,
    )
    direct = json.loads(
        execute_typed_action(
            request, repo_root=engine.repo_root, graph_db=engine.graph_db
        )["output"]
    )
    # run_typed folds the same compiled observation into CapabilityResult:
    # identical answer, and the facade status maps the same decision mode.
    assert via_facade.answer == direct["direct_answer"]
    expected_status = {
        "REPLACE": "ok",
        "AUGMENT": "partial",
        "PASS_THROUGH": "abstain",
    }[direct["decision"]["mode"]]
    assert via_facade.status == expected_status


# ── volatile binding fields: asserted by form, never by golden value ────


def test_snapshot_binding_fields(polyglot_session, polyglot_conf, polyglot_repo):
    session, _adapter = polyglot_session
    _, payload = _execute(
        session, polyglot_conf,
        "definition", {"symbol": "sanitize"}, "binding",
    )
    snapshot = payload["action_request"]["repository_snapshot"]
    root_sha = hashlib.sha256(
        str(polyglot_repo.root).encode("utf-8")
    ).hexdigest()
    assert snapshot["repository_id"] == root_sha
    assert snapshot["root_sha256"] == root_sha
    assert snapshot["revisions"]["graph"] == FIXTURE_REVISION
    # No git repo inside the fixture copy: the honest fallback, or a real
    # 40-hex revision if the tmp dir happens to sit inside one.
    git_rev = snapshot["git_revision"]
    assert git_rev == "WORKTREE" or (
        len(git_rev) == 40
        and all(c in "0123456789abcdef" for c in git_rev)
    )
    assert payload["evidence"]["producer"] == "deterministic_query.definition"


# ── bounded output ──────────────────────────────────────────────────────

_BOUND_CASES = [
    ("definition", {"symbol": "sanitize"}),
    ("references", {"symbol": "sanitize"}),
    ("callers", {"symbol": "execute"}),
    ("symbol_context", {"symbol": "Friendly"}),
    ("route_map", {}),
    ("api_impact", {"route": "/api/items"}),
    ("taint", {"source": "list_items", "sink": "execute"}),
    ("processes", {}),
    ("slice", {"symbol": "list_items", "line": 83}),
    ("shape_check", {"symbol": "Friendly"}),
    ("tool_map", {}),
    ("exact_literal_search", {"literal": "return", "paths": ["."]}),
]


def test_every_kind_answer_fits_the_wire_bound(
    polyglot_session, polyglot_conf, polyglot_repo
):
    session, _adapter = polyglot_session
    for index, (kind, arguments) in enumerate(_BOUND_CASES):
        result, payload = _execute(
            session, polyglot_conf, kind, arguments, f"bound{index}"
        )
        encoded = len(result["output"].encode("utf-8"))
        assert encoded <= QUERY_RESULT_MAX_BYTES, (kind, encoded)
        # Nothing was silently dropped: no truncation markers without an
        # explicit omissions record.
        omissions = payload["evidence"].get("omissions") or []
        truncated = [o for o in omissions if "query_result" in str(o)]
        if encoded == QUERY_RESULT_MAX_BYTES:
            assert truncated, f"{kind} hit the bound with no truncation record"


def test_truncation_records_omissions():
    """The bound itself, exercised on a synthetic oversized observation:
    shrinking must be recorded, not silent.

    The payload stays small (a few hundred rows over a few-hundred-byte
    ceiling) because the bound halve-loop re-serializes per pass — a big
    fixture would make the test quadratic for no extra coverage.
    """
    oversized = {
        "schema": "gt.compiled_observation.v1",
        "action_request": {"kind": "processes"},
        "evidence": {
            "schema": "gt.evidence_artifact.v1",
            "omissions": [],
            "anchors": [f"anchor-{i:03d}" for i in range(120)],
            "witnesses": [],
        },
        "direct_answer": {
            "processes": [
                {"label": f"flow-{i:03d}", "steps": [f"s{j}" for j in range(6)]}
                for i in range(120)
            ]
        },
        "decision": {"mode": "AUGMENT"},
    }
    bounded, added = bound_compiled_observation(
        oversized, max_bytes=4096, kind="processes"
    )
    encoded = json.dumps(
        bounded, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert len(encoded) <= 4096
    assert "query_result_byte_limit" in added
    assert any(item.startswith("query_result_truncated:") for item in added)
