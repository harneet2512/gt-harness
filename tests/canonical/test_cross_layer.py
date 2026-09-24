"""F.3 — mandatory cross-layer chains on the real producer path.

Each chain runs the shape the runtime contract mandates:

    index -> capability query -> real edit -> record_edit_transaction
    (synchronous amend) -> same query -> real pytest -> runtime evidence

The post-edit query must either reflect the edit's semantic delta or
honestly abstain/partial with named omissions when the amend is refused —
and the refusal must be journaled, never silently stale. The chain closes
with a real ``python -m pytest`` run over the edited copy whose recorded
``ExecutionEvidence`` must cite the transaction's post-edit revision.

Convergence: the amended graph is compared against a clean rebuild of the
exact edited tree. ``assert_graphs_isomorphic`` proves two clean builds
equal modulo location-scoped hex ids; an *amended* graph needs one more
layer — the batch amend retains parent rows verbatim and re-inserts the
edited file's rows at the tail of the rowid space, so integer rowids and
the foreign keys naming them legitimately differ even where content is
identical. ``_assert_semantically_isomorphic`` adds that layer: rows align
on content columns, foreign keys rewrite through the learned id-space
bijections, and any divergent column outside the declared id/digest set
fails by name.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("groundtruth.runtime.deterministic_queries")

from gt_engine import indexer
from gt_engine.capabilities import analysis, change, runtime, structure
from gt_engine.engine_state import RuntimeLayout
from gt_engine.miniswe_typed_actions import execute_typed_action_fail_open
from gt_engine.runtime_observation import (
    capture_workspace,
    compile_execution_evidence,
    diff_workspace,
)
from tests.canonical.conftest import (
    CanonWorkspace,
    assert_graphs_isomorphic,
    canon_json,
    dump_table,
    producer_candidates,
    scrub_payload,
)

_PRODUCER_MAY_EXIST = any(
    Path(candidate).is_file() for candidate in producer_candidates()
) or os.environ.get("GT_INDEX_BINARY")

pytestmark = pytest.mark.skipif(
    not _PRODUCER_MAY_EXIST,
    reason="no gt-index producer candidate exists on this host",
)

SERVER = "pyapp/server.py"
FIXTURE_TEST = "pyapp/test_server.py"

# Content tables whose rows must be isomorphic between the amended graph
# and the clean rebuild — the same selection the determinism suite pins,
# plus file_hashes (compared minus the per-run indexed_at clock).
_CONTENT_TABLES = (
    "nodes",
    "edges",
    "assertions",
    "closure",
    "cochanges",
    "communities",
    "community_members",
    "processes",
    "process_steps",
    "cfg_blocks",
    "cfg_defs",
    "cfg_edges",
    "cfg_uses",
    "properties",
    "resolution_callsites",
    "resolution_candidates",
    "resolution_symbols",
    "parser_assertion_inventory",
    "parser_edge_inventory",
    "parser_node_inventory",
    "parser_property_inventory",
)


# ---------------------------------------------------------------------------
# Real-path helpers (mirrors of the runtime suite's helpers — the chain
# boundary is the same production machinery)
# ---------------------------------------------------------------------------


def _move_sanitize_call(root: Path) -> None:
    """Move the ``sanitize(raw)`` call from ``list_items`` into
    ``render_item`` — a one-file semantic edit, line count preserved."""
    path = root / SERVER
    before = path.read_text(encoding="utf-8")
    after = before.replace(
        "    cleaned = sanitize(raw)\n", "    cleaned = raw\n"
    )
    assert after != before, "fixture drifted: list_items sanitize call gone"
    moved = after.replace(
        '    payload = store.fetch(request.args.get("key"))\n',
        '    payload = store.fetch(sanitize(request.args.get("key")))\n',
    )
    assert moved != after, "fixture drifted: render_item fetch call changed"
    path.write_text(moved, encoding="utf-8")


def _transact_edit(workspace, *, action_id: int = 1):
    """capture -> real edit -> capture -> diff -> record_edit_transaction."""
    before = capture_workspace(workspace.root)
    _move_sanitize_call(workspace.root)
    after = capture_workspace(workspace.root)
    txn = diff_workspace(
        before, after, action_id=action_id, command="apply_patch"
    )
    workspace.adapter.record_edit_transaction(txn)
    return txn


def _amend_adopted_or_none(workspace) -> dict | None:
    """The adoption row, or None after asserting the journaled refusal."""
    adopted = workspace.journal_event("graph_sync_amend")
    refused = workspace.journal_event("graph_sync_amend_refused")
    assert adopted is not None or refused is not None, (
        "transaction-boundary amend left no journal row at all"
    )
    if adopted is not None:
        assert adopted["adopted"] is True
        return adopted
    assert refused.get("reason"), "amend refusal must name its reason"
    assert workspace.adapter.engine_state.graph_current is False, (
        "amend refused yet graph claims current — silent staleness"
    )
    return None


def _run_fixture_pytest(workspace, *, action_id: int, revision: str):
    """Real pytest over the edited copy -> compile -> record evidence."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(workspace.root) + os.pathsep + env.get(
        "PYTHONPATH", ""
    )
    # argv[0]'s basename: an absolute Windows path mangles the certified
    # classifier's invocation-surface parse.
    python = Path(sys.executable).name
    command = f"{python} -m pytest {FIXTURE_TEST} -x -q"
    run = subprocess.run(
        [sys.executable, "-m", "pytest", FIXTURE_TEST, "-x", "-q"],
        cwd=workspace.root,
        env=env,
        capture_output=True,
        timeout=180,
    )
    evidence = compile_execution_evidence(
        command=command,
        output=(run.stdout + run.stderr).decode("utf-8", "replace"),
        returncode=run.returncode,
        action_id=action_id,
        repository_revision=revision,
        raw_output=run.stdout + run.stderr,
    )
    assert evidence is not None
    workspace.adapter.record_execution_evidence(evidence, command)
    return evidence


def _caller_names(result) -> set[str]:
    answer = result.answer or {}
    return {
        row["name"]
        for rows in answer.get("callers_by_depth", {}).values()
        for row in rows
    }


def _slice_call_names(result) -> set[str]:
    answer = result.answer or {}
    return {
        site["name"]
        for piece in answer.get("slices", [])
        for site in piece.get("call_sites", [])
    }


def _assert_runtime_cites_revision(workspace, revision: str) -> None:
    """The recorded execution evidence must name the post-edit revision."""
    state = runtime.verification_state(workspace.session)
    assert state.status == "ok", state.omissions
    assert state.answer["state"] == "recorded"
    assert state.answer["repository_revision"] == revision
    last = runtime.last_test_result(workspace.session)
    assert last.status == "ok", last.omissions
    assert last.answer["repository_revision"] == revision


# ---------------------------------------------------------------------------
# Chain: callers
# ---------------------------------------------------------------------------


def test_callers_chain_reflects_moved_call(runtime_workspace):
    """index -> callers(sanitize) -> move the call -> amend -> callers
    shows the old location gone and the new one present -> pytest ->
    evidence cites the post-edit revision."""
    ws = runtime_workspace("chain-callers")

    pre = structure.callers(ws.session, "sanitize")
    pre_names = _caller_names(pre)
    assert "list_items" in pre_names, pre_names
    assert "render_item" not in pre_names, pre_names

    txn = _transact_edit(ws)
    adopted = _amend_adopted_or_none(ws)

    post = structure.callers(ws.session, "sanitize")
    if adopted is None:
        refused = ws.journal_event("graph_sync_amend_refused")
        # Stale graph: honest read-back means fresh=False and the refusal
        # journaled — the answer must never silently serve post-edit
        # semantics. The pytest+evidence half of the chain still runs.
        assert post.fresh is False
        assert post.omissions or post.status in {
            "partial", "abstain", "unavailable",
        }, post.status
        _run_fixture_pytest(ws, action_id=2, revision=txn.post_revision)
        _assert_runtime_cites_revision(ws, txn.post_revision)
        pytest.skip(f"sync amend refused on this host: {refused['reason']}")

    assert post.fresh is True
    assert post.status in {"ok", "partial"}
    post_names = _caller_names(post)
    assert "list_items" not in post_names, post_names
    assert "render_item" in post_names, post_names
    # The untouched call sites survive the amend.
    assert {"format_greeting", "test_sanitize"} <= post_names

    _run_fixture_pytest(ws, action_id=2, revision=txn.post_revision)
    _assert_runtime_cites_revision(ws, txn.post_revision)


# ---------------------------------------------------------------------------
# Chain: slice
# ---------------------------------------------------------------------------


def test_slice_chain_reflects_moved_call(runtime_workspace):
    """The backward slice from ``list_items`` line 83 must lose the
    ``sanitize`` call site once the call moves — or honestly abstain."""
    ws = runtime_workspace("chain-slice")

    pre = analysis.slice(ws.session, "list_items", 83)
    pre_sites = _slice_call_names(pre)
    assert "sanitize" in pre_sites, (
        f"baseline slice lost the sanitize call site: {pre.answer}"
    )

    txn = _transact_edit(ws)
    adopted = _amend_adopted_or_none(ws)

    post = analysis.slice(ws.session, "list_items", 83)
    if adopted is None:
        refused = ws.journal_event("graph_sync_amend_refused")
        assert post.fresh is False
        _run_fixture_pytest(ws, action_id=2, revision=txn.post_revision)
        _assert_runtime_cites_revision(ws, txn.post_revision)
        pytest.skip(f"sync amend refused on this host: {refused['reason']}")

    assert post.fresh is True
    post_sites = _slice_call_names(post)
    assert "sanitize" not in post_sites, post_sites
    assert "run_query" in post_sites, post_sites

    _run_fixture_pytest(ws, action_id=2, revision=txn.post_revision)
    _assert_runtime_cites_revision(ws, txn.post_revision)


# ---------------------------------------------------------------------------
# Chain: patch_impact
# ---------------------------------------------------------------------------


def test_patch_impact_chain(runtime_workspace):
    """patch_impact over the real before/after bytes answers on both sides
    of the amend with an honest envelope — the after_sha256 the facade
    echoes must be the digest of the real edited bytes."""
    ws = runtime_workspace("chain-patch")

    before_text = (ws.root / SERVER).read_text(encoding="utf-8")
    after_text = before_text.replace(
        "    cleaned = sanitize(raw)\n", "    cleaned = raw\n"
    ).replace(
        '    payload = store.fetch(request.args.get("key"))\n',
        '    payload = store.fetch(sanitize(request.args.get("key")))\n',
    )
    proposal = {SERVER: {"before": before_text, "after": after_text}}

    pre = change.patch_impact(ws.session, proposal)
    assert pre.status in {"ok", "partial", "abstain"}, pre.status
    if pre.status != "ok":
        assert pre.omissions, "non-ok patch_impact with no named omission"
    files = (pre.answer or {}).get("files") or []
    assert files and files[0]["path"] == SERVER
    assert files[0]["before_sha256"] == hashlib.sha256(
        before_text.encode("utf-8")
    ).hexdigest()
    assert files[0]["after_sha256"] == hashlib.sha256(
        after_text.encode("utf-8")
    ).hexdigest()

    txn = _transact_edit(ws)
    adopted = _amend_adopted_or_none(ws)

    post = change.patch_impact(ws.session, proposal)
    if adopted is None:
        refused = ws.journal_event("graph_sync_amend_refused")
        assert post.fresh is False
        _run_fixture_pytest(ws, action_id=2, revision=txn.post_revision)
        _assert_runtime_cites_revision(ws, txn.post_revision)
        pytest.skip(f"sync amend refused on this host: {refused['reason']}")

    assert post.fresh is True
    assert post.status in {"ok", "partial", "abstain"}
    if post.status != "ok":
        assert post.omissions
    post_files = (post.answer or {}).get("files") or []
    assert post_files and post_files[0]["after_sha256"] == hashlib.sha256(
        after_text.encode("utf-8")
    ).hexdigest()

    _run_fixture_pytest(ws, action_id=2, revision=txn.post_revision)
    _assert_runtime_cites_revision(ws, txn.post_revision)


# ---------------------------------------------------------------------------
# Chain: route_map / api_impact
# ---------------------------------------------------------------------------


def _route_flows(result, route_name: str) -> set[str]:
    """The flow symbols of one route entry — the handler's direct outgoing
    CALLS targets. The route_map surface names the list ``downstream_calls``
    and the route key ``route``; api_impact passes the raw route row
    through with ``flows`` under key ``name``."""
    answer = result.answer or {}
    for route in answer.get("routes") or []:
        if route.get("route") == route_name or route.get("name") == route_name:
            calls = route.get("downstream_calls") or route.get("flows") or []
            return {str(f.get("symbol", "")) for f in calls if isinstance(f, dict)}
    return set()


def test_route_map_and_api_impact_chain(runtime_workspace):
    """Moving ``sanitize`` into the /api/render handler must surface in
    that route's downstream flows after the amend — or the chain honestly
    abstains. (The file-level route surface already names sanitize via
    /api/items; the route-scoped flows are the delta.)"""
    ws = runtime_workspace("chain-route")

    pre_map = structure.framework_relationships(ws.session, SERVER)
    assert pre_map.status in {"ok", "partial"}, (
        f"route_map cannot answer: {pre_map.status} {pre_map.omissions}"
    )
    pre_flows = _route_flows(pre_map, "/api/render")
    assert pre_flows, f"no /api/render route in map: {pre_map.answer}"
    assert "sanitize" not in pre_flows, pre_flows

    pre_impact = change.route_impact(ws.session, route="/api/render")
    pre_impact_flows = _route_flows(pre_impact, "/api/render")
    if pre_impact_flows:
        assert "sanitize" not in pre_impact_flows

    txn = _transact_edit(ws)
    adopted = _amend_adopted_or_none(ws)

    post_map = structure.framework_relationships(ws.session, SERVER)
    post_impact = change.route_impact(ws.session, route="/api/render")
    if adopted is None:
        refused = ws.journal_event("graph_sync_amend_refused")
        assert post_map.fresh is False
        _run_fixture_pytest(ws, action_id=2, revision=txn.post_revision)
        _assert_runtime_cites_revision(ws, txn.post_revision)
        pytest.skip(f"sync amend refused on this host: {refused['reason']}")

    assert post_map.fresh is True
    assert post_impact.fresh is True
    post_flows = _route_flows(post_map, "/api/render")
    post_impact_flows = _route_flows(post_impact, "/api/render")
    # The handler now calls sanitize directly: the amended graph must
    # surface it in at least one certified route surface.
    assert "sanitize" in (post_flows | post_impact_flows), (
        "amended graph lost render_item -> sanitize from the route "
        f"surfaces: route_map flows={post_flows} "
        f"api_impact flows={post_impact_flows}"
    )

    _run_fixture_pytest(ws, action_id=2, revision=txn.post_revision)
    _assert_runtime_cites_revision(ws, txn.post_revision)


# ---------------------------------------------------------------------------
# Convergence: amended graph vs clean rebuild of the edited tree
# ---------------------------------------------------------------------------


def _clean_rebuild(workspace, revision: str) -> Path:
    """A second, independent ``ensure_index`` build over the same edited
    tree — the clean baseline the amended graph must converge to."""
    layout = RuntimeLayout.resolve(
        workspace=workspace.root,
        state_root=workspace.state.parent / "state-clean",
        task_id=f"{workspace.task_id}-clean",
    )
    graph = indexer.ensure_index(
        str(workspace.root), layout=layout, source_revision=revision
    )
    assert graph is not None, "clean rebuild over the edited tree failed"
    return Path(graph)


def _typed_answer(workspace, graph: Path, revision: str,
                  kind: str, arguments: dict) -> str:
    """The certified typed answer over an explicit graph, scrubbed to a
    canonical byte string — the determinism suite's comparison shape."""
    _request, result = execute_typed_action_fail_open(
        {
            "tool_name": "groundtruth",
            "tool_call_id": f"canon-cross:{kind}",
            "gt_action": {"kind": kind, "arguments": dict(arguments)},
        },
        repo_root=workspace.root,
        configuration={
            "configuration_id": "canon-cross",
            "graph_db": str(graph),
            "graph_source_revision": revision,
        },
    )
    scrub_ws = CanonWorkspace(
        root=workspace.root, graph=graph,
        binary=workspace.binary, build_info=workspace.build_info,
    )
    return canon_json(scrub_payload(json.loads(result["output"]), scrub_ws))


# -- semantic isomorphism ---------------------------------------------------

# Column roles for the amended-vs-clean comparison. Every column that may
# legitimately differ is declared here; anything else that differs is a
# real content divergence and fails the audit by name.
#
#   ("int", space)   integer FK / own rowid into a renumbered id space
#   ("text", space)  same FK rendered as a numeric string
#   ("list", space)  JSON list of integer FKs
#   ("digest",)      sha256 over the referenced row's stored form
#   ("volatile",)    per-build wall-clock or insertion artifact
#
# The batch amend retains parent rows verbatim and re-mints the edited
# file's rows at the tail of the rowid space — verified on the real
# producer: the amended file's nodes get ids 650+ where a clean build
# numbers them 46+. Rowid space is an insertion artifact, not content.
_COLUMN_ROLES: dict[str, dict[str, tuple]] = {
    "nodes": {
        "id": ("int", "nodes"),
        "parent_id": ("int", "nodes"),
        "file_node_id": ("int", "nodes"),
    },
    "edges": {
        "id": ("int", "edges"),
        "source_id": ("int", "nodes"),
        "target_id": ("int", "nodes"),
    },
    "assertions": {
        "id": ("int", "assertions"),
        "test_node_id": ("int", "nodes"),
        "target_node_id": ("int", "nodes"),
    },
    "closure": {
        "source_id": ("int", "nodes"),
        "target_id": ("int", "nodes"),
    },
    "properties": {
        "id": ("int", "properties"),
        "node_id": ("int", "nodes"),
    },
    "resolution_callsites": {
        "source_id": ("int", "nodes"),
        "source_native_id": ("text", "nodes"),
        "selected_target_native_id": ("text", "nodes"),
    },
    "resolution_symbols": {
        "native_id": ("text", "nodes"),
    },
    "parser_node_inventory": {
        "node_id": ("int", "nodes"),
        "content_sha256": ("digest",),
    },
    "parser_edge_inventory": {
        "edge_id": ("int", "edges"),
        "content_sha256": ("digest",),
    },
    "parser_assertion_inventory": {
        "assertion_id": ("int", "assertions"),
        "content_sha256": ("digest",),
    },
    "parser_property_inventory": {
        "property_id": ("int", "properties"),
        "content_sha256": ("digest",),
    },
    "processes": {
        "id": ("int", "processes"),
        "witness_assertion_id": ("int", "assertions"),
    },
    "process_steps": {
        "process_id": ("int", "processes"),
    },
    "communities": {
        "id": ("int", "communities"),
        "evidence_edge_ids": ("list", "edges"),
    },
    "community_members": {
        "community_id": ("int", "communities"),
        "node_id": ("int", "nodes"),
    },
    "cfg_blocks": {"id": ("int", "cfg_blocks")},
    "cfg_defs": {"id": ("int", "cfg_defs")},
    "cfg_edges": {"id": ("int", "cfg_edges")},
    "cfg_uses": {"id": ("int", "cfg_uses")},
    "file_hashes": {"indexed_at": ("volatile",)},
}

_HEX_TOKEN = re.compile(r"[0-9a-f]{32,}")
_UNRESOLVED = "<unresolved>"


class _SpaceMaps:
    """Learned bijections: integer rowid spaces + the hex-token map the
    conftest comparator uses for producer-minted identities.

    The maps are global across tables: ``edges.source_id`` must resolve
    through the same ``nodes`` bijection the node rows established. Every
    learned pair is one-to-one — a minted id names exactly one identity —
    and trial-learning rolls back so a row-pairing search can backtrack.
    """

    def __init__(self) -> None:
        self.int_fwd: dict[str, dict[int, int]] = {}
        self.int_rev: dict[str, dict[int, int]] = {}
        self.hex_fwd: dict[str, str] = {}
        self.hex_rev: dict[str, str] = {}

    def learn_int(self, space: str, av: int, bv: int,
                  learned: list) -> bool:
        """Trial-learn ``av -> bv`` in one integer id space.

        Returns False on a bijection conflict; on success appends an undo
        record to ``learned`` only when the pair is new (an already-known
        consistent pair learns nothing and needs no rollback)."""
        if av is None or bv is None:
            # NULL is content, not an id: only NULL<->NULL pairs.
            return av is None and bv is None
        fwd = self.int_fwd.setdefault(space, {})
        rev = self.int_rev.setdefault(space, {})
        if fwd.get(av, bv) != bv or rev.get(bv, av) != av:
            return False
        if av not in fwd:
            fwd[av] = bv
            rev[bv] = av
            learned.append(("int", space, av, bv))
        return True

    def learn_hex(self, av: str, bv: str, learned: list) -> bool:
        """Trial-learn ``av -> bv`` in the producer-minted hex space."""
        if self.hex_fwd.get(av, bv) != bv or self.hex_rev.get(bv, av) != av:
            return False
        if av not in self.hex_fwd:
            self.hex_fwd[av] = bv
            self.hex_rev[bv] = av
            learned.append(("hex", av, bv))
        return True

    def rollback(self, learned: list) -> None:
        """Undo trial-learned pairs in reverse order."""
        for entry in reversed(learned):
            if entry[0] == "int":
                _, space, av, bv = entry
                del self.int_fwd[space][av]
                del self.int_rev[space][bv]
            else:
                _, av, bv = entry
                del self.hex_fwd[av]
                del self.hex_rev[bv]


def _resolve_cell(
    role: tuple, value, maps: _SpaceMaps, *, side: str
):
    """Resolve one cell into the counterpart's id space.

    Returns ``(resolved_repr, unresolved_values)`` — unresolved cells are
    what the alignment pass learns; an unresolved cell surviving into the
    verify pass is a real divergence."""
    kind = role[0]
    if kind == "digest":
        return "<digest>", ()
    int_maps = maps.int_fwd if side == "a" else maps.int_rev
    if kind == "int":
        if value is None:
            # NULL is content, not an id — never an unresolved token.
            return repr(value), ()
        space = role[1]
        mapped = int_maps.get(space, {}).get(value)
        if mapped is None:
            return _UNRESOLVED, (value,)
        return mapped if side == "a" else value, ()
    if kind == "text":
        space = role[1]
        try:
            iv = int(value)
        except (TypeError, ValueError):
            return repr(value), ()
        mapped = int_maps.get(space, {}).get(iv)
        if mapped is None:
            return _UNRESOLVED, (iv,)
        return repr(str(mapped if side == "a" else iv)), ()
    if kind == "list":
        space = role[1]
        try:
            items = json.loads(value)
        except (TypeError, ValueError):
            return repr(value), ()
        out: list[str] = []
        unknown: list[int] = []
        for item in items:
            try:
                iv = int(item)
            except (TypeError, ValueError):
                out.append(repr(item))
                continue
            mapped = int_maps.get(space, {}).get(iv)
            if mapped is None:
                unknown.append(iv)
                out.append(_UNRESOLVED)
            else:
                out.append(str(mapped if side == "a" else iv))
        return "[" + ",".join(out) + "]", tuple(unknown)
    if kind == "hex":
        if not isinstance(value, str):
            return repr(value), ()
        mapping = (
            maps.hex_fwd if side == "a"
            else {b: b for b in maps.hex_rev}
        )
        unknown: list[str] = []

        def swap(match):
            token = match.group(0)
            mapped = mapping.get(token)
            if mapped is None:
                unknown.append(token)
                return _UNRESOLVED
            return mapped

        return _HEX_TOKEN.sub(swap, value), tuple(unknown)
    if kind == "volatile":
        return "<volatile>", ()
    raise AssertionError(f"unknown column role {role!r}")


def _classify_diffcol(table: str, col: str, rows_a, rows_b) -> tuple:
    """The role a differing column plays. Undeclared non-id differences
    fail — this is the audit that keeps the comparison honest."""
    roles = _COLUMN_ROLES.get(table, {})
    if col in roles:
        return roles[col]
    from collections import Counter

    counts_a = Counter(r[col] for r in rows_a)
    counts_b = Counter(r[col] for r in rows_b)
    differing = list((counts_a - counts_b).elements()) + list(
        (counts_b - counts_a).elements()
    )
    # An undeclared column may differ only if every differing value is a
    # producer-minted id (a >=32-hex token string); those join the same
    # global bijection the determinism comparator maintains. Anything
    # else is semantic divergence and fails by name.
    hexish = bool(differing) and all(
        isinstance(v, str) and _HEX_TOKEN.search(v) for v in differing
    )
    assert hexish, (
        f"{table}.{col}: content column diverged between amended graph "
        f"and clean rebuild (undeclared, non-id values) — sample "
        f"{[repr(v) for v in differing[:4]]}"
    )
    return ("hex",)


def _compare_semantic_table(table: str, graph_a: Path, graph_b: Path,
                            maps: _SpaceMaps) -> None:
    rows_a = dump_table(graph_a, table)
    rows_b = dump_table(graph_b, table)
    assert len(rows_a) == len(rows_b), (
        f"{table}: {len(rows_a)} rows vs {len(rows_b)}"
    )
    if not rows_a:
        return
    cols = list(rows_a[0].keys())
    diffcols = [
        c for c in cols
        if sorted(map(repr, (r[c] for r in rows_a)))
        != sorted(map(repr, (r[c] for r in rows_b)))
    ]
    if not diffcols:
        return
    spec = {c: _classify_diffcol(table, c, rows_a, rows_b) for c in diffcols}

    def signature(row, *, side: str):
        parts: list[str] = []
        unresolved: list[tuple[str, tuple]] = []
        for c in sorted(cols):
            role = spec.get(c)
            if role is None:
                parts.append(repr(row[c]))
                continue
            resolved, unknown = _resolve_cell(role, row[c], maps, side=side)
            if unknown:
                unresolved.append((c, unknown))
                parts.append(_UNRESOLVED)
            else:
                parts.append(str(resolved))
        return tuple(parts), tuple(unresolved)

    groups_a: dict[tuple, list[tuple[dict, tuple]]] = {}
    groups_b: dict[tuple, list[tuple[dict, tuple]]] = {}
    for row in rows_a:
        sig, unresolved = signature(row, side="a")
        groups_a.setdefault(sig, []).append((row, unresolved))
    for row in rows_b:
        sig, unresolved = signature(row, side="b")
        groups_b.setdefault(sig, []).append((row, unresolved))
    assert set(groups_a) == set(groups_b), (
        f"{table}: resolved row sets diverge\n"
        f"  only in amended: {sorted(set(groups_a) - set(groups_b))[:3]}\n"
        f"  only in clean:   {sorted(set(groups_b) - set(groups_a))[:3]}"
    )

    # Learn the bijections inside every aligned group. A zip-by-sort-order
    # cannot work here: minted ids are not per-row unique — callsite_id
    # names a whole resolution-fact group — so the pairing must be
    # *searched* under the constraint that every learned map stays
    # one-to-one globally.
    for sig in groups_a:
        ga, gb = groups_a[sig], groups_b[sig]
        assert len(ga) == len(gb), (
            f"{table}: aligned group has {len(ga)} vs {len(gb)} rows"
        )
        _align_group(
            table, spec,
            [row for row, _un in ga], [row for row, _un in gb],
            maps,
        )

    # Verify pass: no unresolved cell may survive; rewritten A rows must
    # equal B rows verbatim, multiset-compared. Both sides normalize
    # through ``_resolve_cell`` — A tokens rewrite into B-space, B cells
    # render their own (now fully claimed) ids through the same path —
    # so equal content compares equal regardless of cell type.
    rewritten_a: list[str] = []
    for row in rows_a:
        parts: list[str] = []
        for c in sorted(cols):
            role = spec.get(c)
            if role is None:
                parts.append(repr(row[c]))
                continue
            resolved, unknown = _resolve_cell(
                role, row[c], maps, side="a"
            )
            assert not unknown, (
                f"{table}.{c}: unmapped id(s) {unknown} survived "
                f"alignment in row {row[c]!r}"
            )
            parts.append(str(resolved))
        rewritten_a.append(tuple(parts).__repr__())
    verbatim_b: list[str] = []
    for row in rows_b:
        parts = []
        for c in sorted(cols):
            role = spec.get(c)
            if role is None:
                parts.append(repr(row[c]))
                continue
            parts.append(
                str(_resolve_cell(role, row[c], maps, side="b")[0])
            )
        verbatim_b.append(tuple(parts).__repr__())
    assert sorted(rewritten_a) == sorted(verbatim_b), (
        f"{table}: rewritten rows diverge after id remapping\n"
        f"  only in amended: "
        f"{sorted(set(rewritten_a) - set(verbatim_b))[:2]}\n"
        f"  only in clean:   "
        f"{sorted(set(verbatim_b) - set(rewritten_a))[:2]}"
    )


def _learn_cell(role: tuple, va, vb, maps: _SpaceMaps,
                learned: list) -> bool:
    """Trial-extend every id-space map with one cell pair.

    True when the pair is consistent with everything learned so far —
    already-mapped cells must map to exactly this counterpart, unmapped
    cells learn. Non-id roles never constrain. Within one aligned group
    the resolved (non-id) cell structure is already proven equal, so a
    non-parseable text/list cell only needs literal equality."""
    kind = role[0]
    if kind in {"digest", "volatile"}:
        return True
    if kind == "int":
        return maps.learn_int(role[1], va, vb, learned)
    if kind == "text":
        try:
            ia, ib = int(va), int(vb)
        except (TypeError, ValueError):
            return va == vb
        return maps.learn_int(role[1], ia, ib, learned)
    if kind == "list":
        try:
            items_a, items_b = json.loads(va), json.loads(vb)
        except (TypeError, ValueError):
            return va == vb
        if (
            not isinstance(items_a, list)
            or not isinstance(items_b, list)
            or len(items_a) != len(items_b)
        ):
            return False
        for xa, xb in zip(items_a, items_b):
            try:
                ia, ib = int(xa), int(xb)
            except (TypeError, ValueError):
                if xa != xb:
                    return False
                continue
            if not maps.learn_int(role[1], ia, ib, learned):
                return False
        return True
    if kind == "hex":
        toks_a = _HEX_TOKEN.findall(str(va))
        toks_b = _HEX_TOKEN.findall(str(vb))
        if len(toks_a) != len(toks_b):
            return False
        for xa, xb in zip(toks_a, toks_b):
            if not maps.learn_hex(xa, xb, learned):
                return False
        return True
    raise AssertionError(f"unknown column role {role!r}")


def _align_group(table: str, spec: dict, rows_a, rows_b,
                 maps: _SpaceMaps) -> None:
    """Commit one id-consistent bijection between two aligned row sets.

    Rows in one signature group are identical on every resolved column;
    the only freedom is which A row pairs with which B row. The choice is
    load-bearing because minted ids are shared across rows — e.g. one
    ``callsite_id`` names a whole resolution-fact group — and because
    downstream tables resolve foreign keys through the maps this pairing
    learns. A depth-first search tries candidates in ``dump_table``
    rowid order (the producer's deterministic emission order pairs the
    right rows first) and rolls back trial mappings on conflict; failure
    means the equal-content rows carry genuinely different id partitions
    — a real divergence, reported by name.
    """
    n = len(rows_a)
    used = [False] * n

    def dfs(i: int) -> bool:
        if i == n:
            return True
        ra = rows_a[i]
        for j, rb in enumerate(rows_b):
            if used[j]:
                continue
            learned: list = []
            for c in spec:
                if not _learn_cell(spec[c], ra[c], rb[c], maps, learned):
                    maps.rollback(learned)
                    break
            else:
                used[j] = True
                if dfs(i + 1):
                    return True
                used[j] = False
                maps.rollback(learned)
        return False

    assert dfs(0), (
        f"{table}: no id-consistent pairing inside an equal-content row "
        f"group of {n} rows — the amended graph's minted-id partitions "
        "differ from the clean rebuild's"
    )


def _assert_semantically_isomorphic(
    amended: Path, clean: Path, tables: tuple[str, ...]
) -> _SpaceMaps:
    """Amended graph == clean rebuild, modulo renumbered rowid space.

    Order matters: nodes must align first — they mint the id space every
    other table's foreign keys point into — then edges/assertions/
    properties before the inventory and community tables that reference
    them. Hex-minted ids (stable_id, repo_id, *_set_id, qualified_name)
    join the same global bijection the determinism comparator maintains.
    """
    maps = _SpaceMaps()
    ordered = [t for t in (
        "nodes", "edges", "assertions", "properties",
        "closure", "cochanges", "communities", "community_members",
        "processes", "process_steps",
        "cfg_blocks", "cfg_defs", "cfg_edges", "cfg_uses",
        "resolution_callsites", "resolution_candidates",
        "resolution_symbols",
        "parser_node_inventory", "parser_edge_inventory",
        "parser_assertion_inventory", "parser_property_inventory",
        "file_hashes",
    ) if t in tables or t == "file_hashes"]
    for table in ordered:
        _compare_semantic_table(table, amended, clean, maps)
    assert maps.int_fwd.get("nodes"), (
        "no node id pairs learned — graphs did not align at all"
    )
    return maps


# -- parser-fact inventory digests -------------------------------------------
#
# ``parser_*_inventory.content_sha256`` is factValuesDigest — sha256 over a
# Go ``json.Marshal`` of the referenced row's covered columns (see
# vendor/gt-index-src/internal/store/parsed_facts.go). The digest covers
# rowid-space foreign keys by construction, so it legitimately differs
# across the renumbering; what must hold in BOTH graphs is internal
# consistency: every inventoried row's digest equals the digest of the
# row it currently names. ``_go_json`` reproduces Go's encoding/json for
# the value domain these columns use (ints, floats, strings, nulls) —
# HTML-escaped <>&, \uXXXX control escapes, shortest-form floats.

_GO_ESCAPES = {
    '"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t",
    "<": "\\u003c", ">": "\\u003e", "&": "\\u0026",
    "\u2028": "\\u2028", "\u2029": "\\u2029",
}


def _go_json_value(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return repr(value)
    if isinstance(value, str):
        out = ['"']
        for ch in value:
            esc = _GO_ESCAPES.get(ch)
            if esc is not None:
                out.append(esc)
            elif ord(ch) < 0x20:
                out.append(f"\\u{ord(ch):04x}")
            else:
                out.append(ch)
        out.append('"')
        return "".join(out)
    raise TypeError(f"unsupported value for go-json digest: {value!r}")


_INVENTORY_COVERED = {
    "parser_property_inventory": (
        "properties", "property_id", "id",
        ("node_id", "kind", "value", "line", "confidence"),
    ),
    "parser_assertion_inventory": (
        "assertions", "assertion_id", "id",
        ("test_node_id", "target_node_id", "resolution_score", "kind",
         "expression", "expected", "line"),
    ),
    "parser_edge_inventory": (
        "edges", "edge_id", "id",
        ("source_id", "target_id", "type", "source_line", "source_file",
         "resolution_method", "confidence", "metadata", "trust_tier",
         "candidate_count", "evidence_type", "verification_status"),
    ),
}


def _verify_inventory_digests(graph: Path) -> None:
    """Every inventory digest must describe the row it currently names —
    the producer's own staleness invariant, checked on both graphs."""
    for inventory, (table, fk, pk, covered) in _INVENTORY_COVERED.items():
        facts = {r[pk]: r for r in dump_table(graph, table)}
        for entry in dump_table(graph, inventory):
            row = facts[entry[fk]]
            material = "[" + ",".join(
                _go_json_value(row[c]) for c in covered
            ) + "]"
            digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
            assert digest == entry["content_sha256"], (
                f"{inventory}: digest for {fk}={entry[fk]} does not "
                f"describe the {table} row it names — stale inventory"
            )


# ---------------------------------------------------------------------------
# Convergence test
# ---------------------------------------------------------------------------


def test_amended_graph_converges_with_clean_rebuild(runtime_workspace):
    """After a real amend, the adopted graph's semantic content must equal
    a clean rebuild over the same edited tree: identical rows modulo the
    renumbered id space, internally consistent fact digests, and
    byte-identical typed answers."""
    ws = runtime_workspace("converge")
    txn = _transact_edit(ws)
    adopted = _amend_adopted_or_none(ws)

    if adopted is None:
        refused = ws.journal_event("graph_sync_amend_refused")
        # Fail-closed: the serving boundary must publish the clean rebuild
        # — the only legitimate base once the amend cannot land.
        ws.adapter.refresh_graph(phase="canonical_convergence")
        recovery = ws.journal_event("graph_recovery")
        if not (recovery and recovery.get("adopted") is True):
            pytest.skip(
                "amend refused "
                f"({refused['reason']}) and boundary recovery did not "
                "publish — convergence-vs-amend has no amended graph"
            )
        published = Path(ws.adapter.engine_state.graph_path)
        manifest = json.loads(
            published.with_suffix(".manifest.json").read_text()
        )
        assert manifest["build_mode"] == "full", (
            "boundary recovery published a non-clean graph after "
            "amend refusal — the fail-closed path must rebuild"
        )
        fresh = _clean_rebuild(ws, txn.post_revision)
        # Two full builds of the same tree share the rowid space, so the
        # stronger determinism comparator applies unmodified.
        assert_graphs_isomorphic(published, fresh, _CONTENT_TABLES)
        return

    amended = Path(ws.adapter.engine_state.graph_path)
    assert amended != ws.graph and amended.is_file()

    # The adopted graph's manifest must prove incremental lineage: it
    # names the certified parent it amended and the paths it covered.
    manifest = json.loads(
        amended.with_suffix(".manifest.json").read_text()
    )
    assert manifest["build_mode"] == "incremental"
    parent_sha = hashlib.sha256(ws.graph.read_bytes()).hexdigest()
    assert manifest["parent_graph_sha256"] == parent_sha
    assert SERVER in manifest["amended_paths"]

    fresh = _clean_rebuild(ws, txn.post_revision)

    # 1. Table-level semantic equivalence: every content column equal,
    #    every differing column a declared id/digest rewrite through one
    #    consistent bijection.
    maps = _assert_semantically_isomorphic(amended, fresh, _CONTENT_TABLES)

    # 2. file_hashes: identical file identities; only the write clock may
    #    differ (indexed_at is declared volatile — never silently dropped).
    fh_a = {
        tuple((k, v) for k, v in r.items() if k != "indexed_at")
        for r in dump_table(amended, "file_hashes")
    }
    fh_b = {
        tuple((k, v) for k, v in r.items() if k != "indexed_at")
        for r in dump_table(fresh, "file_hashes")
    }
    assert fh_a == fh_b

    # 3. Parser-fact inventories: digests internally consistent in BOTH
    #    graphs — the producer's own anti-stale invariant.
    _verify_inventory_digests(amended)
    _verify_inventory_digests(fresh)

    # 4. The consumer-visible semantic snapshot: certified typed answers
    #    over each graph must be byte-identical after location scrubbing.
    for kind, arguments in (
        ("callers", {"symbol": "sanitize", "depth": 3}),
        ("references", {"symbol": "sanitize"}),
        ("slice", {"symbol": "list_items", "line": 83}),
        ("api_impact", {"route": "/api/render"}),
        ("route_map", {"path": SERVER}),
    ):
        answer_a = _typed_answer(ws, amended, txn.post_revision, kind, arguments)
        answer_b = _typed_answer(ws, fresh, txn.post_revision, kind, arguments)
        assert answer_a == answer_b, (
            f"typed answer for {kind} diverged between amended graph "
            "and clean rebuild"
        )
    assert maps.hex_fwd, "no producer-minted ids were aligned at all"


# ---------------------------------------------------------------------------
# Fail-closed: a broken amend base refuses by name and the boundary
# publishes the clean rebuild
# ---------------------------------------------------------------------------


def test_amend_refusal_boundary_publishes_clean_rebuild(runtime_workspace):
    """Remove the parent's certification manifest and the transaction
    amend must refuse ``parent_manifest_missing`` — journaled, named. The
    serving boundary then publishes the only legitimate base: a clean
    whole-tree build, isomorphic to an independent clean rebuild."""
    ws = runtime_workspace("refuse")
    adapter = ws.adapter
    parent = ws.graph
    manifest = parent.with_suffix(".manifest.json")
    assert manifest.is_file(), "ensure_index did not certify the parent"
    manifest.unlink()

    txn = _transact_edit(ws)

    refused = ws.journal_event("graph_sync_amend_refused")
    assert refused is not None, "broken parent did not refuse the amend"
    assert refused["reason"] == "parent_manifest_missing"
    assert adapter.engine_state.graph_current is False, (
        "refused amend left graph_current true — silent staleness"
    )

    ok = adapter.refresh_graph(phase="canonical_refusal")
    assert ok is True, "boundary did not complete the fail-closed resync"
    recovery = ws.journal_event("graph_recovery")
    assert recovery is not None and recovery["adopted"] is True
    assert adapter.engine_state.graph_current is True

    published = Path(adapter.engine_state.graph_path)
    assert published != parent and published.is_file()
    rec_manifest = json.loads(
        published.with_suffix(".manifest.json").read_text()
    )
    assert rec_manifest["build_mode"] == "full"
    assert rec_manifest["amended_paths"] == []
    assert adapter.graph_db == str(published)

    fresh = _clean_rebuild(ws, txn.post_revision)
    assert_graphs_isomorphic(published, fresh, _CONTENT_TABLES)
