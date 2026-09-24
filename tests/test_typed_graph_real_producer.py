"""The advanced typed kinds, executed against a graph the real gt-index builds.

Until now these kinds were covered only by ActionRequest construction in the
harness and by hand-built SQLite fixtures in the producer, so nothing showed
that route_map, api_impact, taint, rename, shape_check, tool_map or slice run
through ``execute_typed_action`` over a producer graph. This module builds a
small polyglot repository (Flask routes, a JS client, a TS interface, a Go
service, an MCP tool), indexes it with the real producer and dispatches every
advanced kind through the fail-open entry the Mini-SWE environment uses.

Producer resolution, first match wins:

1. ``GT_INDEX_BINARY`` (any platform, if it runs here);
2. the vendored ``vendor/gt-index-linux-amd64`` on Linux x86-64;
3. ``go build -tags sqlite_fts5`` of ``vendor/gt-index-src`` (the vendored
   producer's source, 0becde10), stamped so the analysis phase runs.

The module skips cleanly only when none of these yields a runnable binary.
Known producer/query defects are pinned as strict xfails so a fix upstream
turns them into failures that demand the xfail be removed.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("groundtruth.runtime.deterministic_queries")

from gt_engine.miniswe_typed_actions import (  # noqa: E402
    QUERY_RESULT_MAX_BYTES,
    execute_typed_action_fail_open,
)

HARNESS_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_REVISION = "fixture-workspace-revision-1"
SOURCE_REVISION_CAPABILITY = "source_revision_meta_v1"

POLYGLOT_FILES = {
    "app/server.py": '''import subprocess
from flask import Flask, request

app = Flask(__name__)


class Store:
    def __init__(self):
        self.cmd = ""

    def save(self, value):
        self.cmd = value

    def run(self):
        return subprocess.run(self.cmd, shell=True)


def clean(value):
    if value.startswith("-"):
        value = value[1:]
    return value


def execute(cmd):
    return subprocess.run(cmd, shell=True)


@app.get("/items")
def list_items():
    raw = request.args.get("q")
    safe = clean(raw)
    total = 0
    if safe:
        total = len(safe)
    out = execute(safe)
    return {"total": total, "out": str(out)}


@app.route("/save")
def save_item():
    s = Store()
    s.save(request.args.get("c"))
    return s.run()
''',
    "app/tools.py": '''from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fixture")


@mcp.tool()
def lookup(query):
    return query.strip()
''',
    "svc/client.js": '''async function load() {
  const r = await fetch("/items");
  return r.json();
}
module.exports = { load };
''',
    "svc/shapes.ts": '''export interface Greeter {
  greet(name: string): string;
  wave(): void;
}

export class Friendly implements Greeter {
  greet(name: string): string {
    return "hi " + name;
  }
}

export class Polite implements Greeter {
  greet(name: string): string {
    return "hi " + name;
  }
  wave(): void {}
}
''',
    "svc/main.go": '''package svc

import "net/http"

func helper(n int) int {
\tx := n * 2
\tif x > 10 {
\t\tx = x - 1
\t}
\treturn x
}

func Compute(a int, b int) int {
\ty := a + b
\tz := helper(y)
\tw := 0
\tfor i := 0; i < z; i++ {
\t\tw += i
\t}
\treturn w + z
}

func Handler(w http.ResponseWriter, r *http.Request) {
\tw.Write([]byte("ok"))
}

func Register(mux *http.ServeMux) {
\tmux.HandleFunc("/compute", Handler)
}
''',
}


def _build_info(binary: str) -> dict | None:
    try:
        probe = subprocess.run([binary, "-build-info"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if probe.returncode != 0:
        return None
    try:
        return json.loads(probe.stdout.decode("utf-8", "replace"))
    except ValueError:
        return None


def _go_build(out_dir: Path) -> str | None:
    go = shutil.which("go")
    source = HARNESS_ROOT / "vendor" / "gt-index-src"
    if go is None or not (source / "cmd" / "gt-index").is_dir():
        return None
    commit = (source / "SOURCE-COMMIT").read_text(encoding="utf-8").strip() or "vendored"
    binary = out_dir / ("gt-index.exe" if os.name == "nt" else "gt-index")
    # Every stamp is required: an unstamped build rolls the analysis phase
    # back (incomplete producer identity) and the graph loses its derived
    # edges.
    ldflags = " ".join(
        f"-X main.{name}={value}"
        for name, value in (
            ("commitSHA", commit),
            ("buildTimeUTC", "2026-01-01T00:00:00Z"),
            ("goToolchain", "local-test"),
            ("sourceFingerprint", "harness-test-build"),
            ("compiledBuildTags", "sqlite_fts5"),
        )
    )
    env = dict(os.environ, CGO_ENABLED="1")
    try:
        build = subprocess.run(
            [go, "build", "-tags", "sqlite_fts5", "-ldflags", ldflags, "-o", str(binary),
             "./cmd/gt-index"],
            cwd=source, capture_output=True, timeout=900, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return str(binary) if build.returncode == 0 and binary.is_file() else None


@pytest.fixture(scope="session")
def gt_index(tmp_path_factory) -> tuple[str, dict]:
    candidates: list[str] = []
    if os.environ.get("GT_INDEX_BINARY"):
        candidates.append(os.environ["GT_INDEX_BINARY"])
    vendored = HARNESS_ROOT / "vendor" / "gt-index-linux-amd64"
    if sys.platform.startswith("linux") and platform.machine() in {"x86_64", "AMD64"}:
        candidates.append(str(vendored))
    for candidate in candidates:
        info = _build_info(candidate)
        if info is not None:
            return candidate, info
    built = _go_build(tmp_path_factory.mktemp("gt-index-build"))
    info = _build_info(built) if built else None
    if built is None or info is None:
        pytest.skip("no runnable gt-index: set GT_INDEX_BINARY or install go with CGO")
    return built, info


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=fixture@example.invalid", "-c", "user.name=fixture",
         "-c", "core.autocrlf=false", *args],
        cwd=root, check=True, capture_output=True,
    )


def _index(binary: str, info: dict, files: dict[str, str], root: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is required to give the fixture a repository revision")
    root.mkdir(parents=True)
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "fixture")
    graph = root.parent / "graph.db"
    argv = [binary, "-root", str(root), "-output", str(graph)]
    if SOURCE_REVISION_CAPABILITY in (info.get("capabilities") or ()):
        argv += ["-source-revision", FIXTURE_REVISION]
    build = subprocess.run(argv, capture_output=True, timeout=300)
    assert build.returncode == 0, build.stderr.decode("utf-8", "replace")[-2000:]
    return graph


@pytest.fixture(scope="module")
def polyglot(gt_index, tmp_path_factory) -> tuple[Path, Path, dict]:
    binary, info = gt_index
    root = tmp_path_factory.mktemp("polyglot") / "repo"
    graph = _index(binary, info, POLYGLOT_FILES, root)
    return root, graph, info


def _run(
    root: Path,
    graph: Path,
    kind: str,
    arguments: dict,
    *,
    graph_source_revision: str = FIXTURE_REVISION,
) -> tuple[dict, dict]:
    _, result = execute_typed_action_fail_open(
        {"tool_name": "groundtruth", "tool_call_id": f"real-{kind}",
         "gt_action": {"kind": kind, "arguments": arguments}},
        repo_root=root,
        configuration={"graph_db": str(graph),
                       "graph_source_revision": graph_source_revision},
    )
    assert len(result["output"].encode("utf-8")) <= QUERY_RESULT_MAX_BYTES
    payload = json.loads(result["output"])
    # Every advanced kind is certified partial: whatever the producer says,
    # the answer augments the model's inspection and never replaces it.
    assert payload["decision"]["mode"] == "AUGMENT", payload["decision"]
    assert result["returncode"] == 2
    assert payload["evidence"]["producer"] == f"deterministic_query.{kind}"
    return payload, payload["direct_answer"]


def _routes(answer: dict) -> dict[str, dict]:
    return {row["route"]: row for row in answer["routes"]}


def test_producer_graph_revision_is_verified_or_honestly_flagged(polyglot):
    root, graph, info = polyglot
    payload, _ = _run(root, graph, "definition", {"symbol": "clean"})
    omissions = set(payload["evidence"]["omissions"])
    revision_omissions = omissions & {"graph_revision_mismatch", "graph_revision_unavailable"}
    with sqlite3.connect(f"file:{graph.as_posix()}?mode=ro", uri=True) as conn:
        meta = dict(conn.execute("SELECT key, value FROM project_meta").fetchall())
    if SOURCE_REVISION_CAPABILITY in (info.get("capabilities") or ()):
        assert meta.get("source_revision") == FIXTURE_REVISION
        assert not revision_omissions, omissions
    else:
        # A producer without the flag records no workspace revision; its
        # git_commit is the producer's own build commit, never the fixture's.
        assert "source_revision" not in meta
        assert meta.get("git_commit") != FIXTURE_REVISION
        assert revision_omissions, omissions


def test_producer_graph_revision_mismatch_is_flagged(polyglot):
    root, graph, info = polyglot
    if SOURCE_REVISION_CAPABILITY not in (info.get("capabilities") or ()):
        pytest.skip("producer lacks source_revision_meta_v1; mismatch cannot be verified")
    payload, _ = _run(
        root, graph, "definition", {"symbol": "clean"},
        graph_source_revision="different-workspace-revision",
    )
    assert "graph_revision_mismatch" in payload["evidence"]["omissions"]


def test_route_map_reads_producer_route_edges(polyglot):
    root, graph, _ = polyglot
    payload, answer = _run(root, graph, "route_map", {})
    routes = _routes(answer)
    assert {"/items", "/save", "/compute"} <= set(routes)
    items = routes["/items"]
    assert (items["method"], items["handler"], items["handler_file"]) == (
        "GET", "list_items", "app/server.py")
    assert [c["file"] for c in items["consumers"]] == ["svc/client.js"]
    assert {call["symbol"] for call in items["downstream_calls"]} >= {"clean", "execute"}
    assert routes["/compute"]["handler_file"] == "svc/main.go"


def test_api_impact_attributes_the_client_consumer(polyglot):
    root, graph, _ = polyglot
    _, answer = _run(root, graph, "api_impact", {"route": "/items"})
    (route,) = answer["routes"]
    assert route["handler"] == "list_items"
    assert route["affected_files"] == ["svc/client.js"]
    assert route["consumers"][0]["attribution"] == "route_level"


def test_taint_is_symbol_reachability_with_its_limits_named(polyglot):
    root, graph, _ = polyglot
    payload, answer = _run(root, graph, "taint", {"source": "list_items", "sink": "execute"})
    assert ["list_items", "execute"] in [path["path"] for path in answer["paths"]]
    assert {"symbol_level_reachability_only", "statement_level_dataflow_unavailable"} <= set(
        payload["evidence"]["omissions"])


def test_rename_previews_the_graph_edit_sites(polyglot):
    root, graph, _ = polyglot
    payload, answer = _run(root, graph, "rename", {"symbol": "clean", "new_name": "sanitize"})
    assert answer["files_to_touch"] == ["app/server.py"]
    calls = answer["edit_sites_by_type"]["CALLS"]
    assert [(row["referencing_symbol"], row["line"]) for row in calls] == [("list_items", 31)]
    assert "text_references_not_enumerated" in payload["evidence"]["omissions"]


def test_shape_check_runs_the_interface_conformance_check(polyglot):
    root, graph, _ = polyglot
    _, answer = _run(root, graph, "shape_check", {"symbol": "Friendly"})
    assert answer["check_count"] >= 1
    assert {check["interface"] for check in answer["checks"]} == {"Greeter"}


def test_shape_check_reports_the_missing_interface_method(polyglot):
    root, graph, _ = polyglot
    _, answer = _run(root, graph, "shape_check", {"symbol": "Friendly"})
    assert any("wave" in check.get("missing_methods", []) for check in answer["checks"])


def test_shape_check_passes_a_conforming_class_with_an_empty_method(polyglot):
    """`Polite` implements both members; `wave(): void {}` has an empty body.

    Pins the class-side detection leg: an empty-bodied method_definition must
    count as implemented, so a conforming class earns `status: pass` — and a
    fully-passing check takes the exact-verdict path (returncode 0) rather
    than the partial AUGMENT path the other shape_check legs exercise."""
    root, graph, _ = polyglot
    _, result = execute_typed_action_fail_open(
        {"tool_name": "groundtruth", "tool_call_id": "real-shape-pass",
         "gt_action": {"kind": "shape_check", "arguments": {"symbol": "Polite"}}},
        repo_root=root,
        configuration={"graph_db": str(graph),
                       "graph_source_revision": FIXTURE_REVISION},
    )
    payload = json.loads(result["output"])
    assert result["returncode"] == 0, payload
    answer = payload["direct_answer"]
    assert answer["checks"], answer
    for check in answer["checks"]:
        assert check["interface"] == "Greeter"
        assert check["missing_methods"] == [], check
        assert check["status"] == "pass"
        assert check["implemented_count"] == check["required_count"] == 2


def test_tool_map_executes_and_names_what_it_cannot_see(polyglot):
    root, graph, _ = polyglot
    payload, answer = _run(root, graph, "tool_map", {})
    assert "registration_sites_untracked" in payload["evidence"]["omissions"]
    assert isinstance(answer["tools"], list)


def test_tool_map_detects_a_function_registered_as_an_mcp_tool(polyglot):
    """An external decorator still gets a DECORATES edge via the `Decorator`
    occurrence node (mechanism `syntactic_decorator_applied`), so `@mcp.tool()`
    on `lookup` is visible to tool_map. Rows key the symbol under `tool`."""
    root, graph, _ = polyglot
    _, answer = _run(root, graph, "tool_map", {})
    rows = {tool.get("tool"): tool for tool in answer["tools"]}
    assert "lookup" in rows
    assert rows["lookup"]["decorator"] == "tool"
    assert rows["lookup"]["file_path"] == "app/tools.py"


def test_slice_backward_over_the_python_source_substrate(polyglot):
    root, graph, _ = polyglot
    payload, answer = _run(root, graph, "slice", {"symbol": "list_items", "line": 35})
    (record,) = answer["slices"]
    assert record["file_path"] == "app/server.py"
    assert record["slice_lines"] == [30, 31, 35]
    assert answer["interprocedural"] is False
    assert "slice_limitations_present" in payload["evidence"]["omissions"]


def test_slice_over_the_persisted_go_cfg(polyglot):
    root, graph, _ = polyglot
    _, answer = _run(root, graph, "slice", {"symbol": "Compute", "line": 20, "language": "go"})
    (record,) = answer["slices"]
    assert record["file_path"] == "svc/main.go"
    assert record["substrate"] == "persisted_cfg"
    assert 20 in record["slice_lines"]


def test_interprocedural_slice_honours_its_bounds_and_flags_name_matching(polyglot):
    root, graph, _ = polyglot
    payload, answer = _run(
        root, graph, "slice",
        {"symbol": "list_items", "line": 35, "interprocedural": True,
         "max_hops": 5, "max_depth": 2},
    )
    assert answer["interprocedural"] is True
    (record,) = answer["slices"]
    callees = {hop["callee_fn"] for hop in record["cross_function"]}
    assert {"clean", "execute"} <= callees
    assert "interprocedural_name_matched" in payload["evidence"]["omissions"]


def test_why_this_edge_reads_a_producer_edge(polyglot):
    root, graph, _ = polyglot
    with sqlite3.connect(f"file:{graph.as_posix()}?mode=ro", uri=True) as conn:
        # Core CALLS edges carry no stable_id; the row id is their identity.
        (stable_id,) = conn.execute(
            "SELECT COALESCE(NULLIF(e.stable_id, ''), CAST(e.id AS TEXT)) "
            "FROM edges e JOIN nodes s ON s.id = e.source_id "
            "JOIN nodes t ON t.id = e.target_id WHERE e.type = 'CALLS' "
            "AND s.name = 'list_items' AND t.name = 'clean'"
        ).fetchone()
    _, result = execute_typed_action_fail_open(
        {"tool_name": "groundtruth", "tool_call_id": "real-why",
         "gt_action": {"kind": "why_this_edge", "arguments": {"edge_id": stable_id}}},
        repo_root=root, configuration={"graph_db": str(graph)},
    )
    payload = json.loads(result["output"])
    assert payload["decision"]["mode"] == "AUGMENT"
    answer = payload["direct_answer"]
    assert (answer["edge"]["type"], answer["source"]["name"], answer["target"]["name"]) == (
        "CALLS", "list_items", "clean")


def test_route_map_survives_an_api_call_only_route(gt_index, tmp_path):
    binary, info = gt_index
    files = dict(POLYGLOT_FILES)
    files["svc/api.js"] = (
        'const express = require("express");\n'
        "const app = express();\n"
        'app.get("/orders", (req, res) => res.send("x"));\n'
        "module.exports = app;\n"
    )
    files["svc/client2.js"] = (
        'async function orders() {\n  const r = await fetch("/orders");\n'
        "  return r.json();\n}\nmodule.exports = { orders };\n"
    )
    root = tmp_path / "repo"
    graph = _index(binary, info, files, root)
    _, answer = _run(root, graph, "route_map", {})
    assert "/items" in _routes(answer)
