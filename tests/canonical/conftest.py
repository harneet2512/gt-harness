"""Canonical verification suite — shared producer/session fixtures.

Everything in this suite consumes a graph the *real* gt-index producer
builds from ``fixtures/polyglot``. Nothing fabricates graph content: when
no runnable producer exists the session fixture skips with the exact
reason, and the test modules additionally carry a ``skipif`` pre-filter on
candidate-path presence.

Producer resolution, first match wins (mirrors the policy in
``tests/test_typed_graph_real_producer.py`` plus the Windows smoke path):

1. ``GT_INDEX_BINARY`` (any platform, if ``-build-info`` runs);
2. ``C:\\gt-smoke-a6\\gt-index-new.exe`` — the Windows producer used by the
   smoke matrix on this host;
3. ``gt-index`` / ``gt-index.exe`` on ``PATH``;
4. the vendored ``vendor/gt-index-linux-amd64`` on Linux x86-64;
5. ``go build -tags sqlite_fts5`` of ``vendor/gt-index-src`` (stamped, so
   the analysis phase runs) when a Go toolchain is available.

The produced graph is bound into a :class:`MiniSweAdapter` +
:class:`GTSession` pair so tests exercise the same
``build_action_request`` -> ``execute_typed_action`` pipeline the
model-facing ``groundtruth`` tool and every capability facade use.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

# The polyglot fixture tree ships its own ``test_*.py`` files — they are
# fixture *input* for the producer, not pytest tests. Never collect them.
collect_ignore_glob = ["fixtures/*"]

CANON_DIR = Path(__file__).resolve().parent
HARNESS_ROOT = CANON_DIR.parents[1]
FIXTURE_DIR = CANON_DIR / "fixtures" / "polyglot"
GOLDEN_DIR = CANON_DIR / "goldens"

# Declared workspace revision: passed to the producer as -source-revision and
# to the query layer as graph_source_revision, so the graph-revision check
# compares the declared identity with itself — the same shape the runtime
# uses when EngineState owns the revision.
FIXTURE_REVISION = "canon-polyglot-rev-1"
SOURCE_REVISION_CAPABILITY = "source_revision_meta_v1"

# Fields whose values derive (directly or transitively) from the absolute
# repo/graph paths or from a hash chain over the whole request. They cannot
# be golden-pinned across machines or tmp dirs, so the scrubber replaces
# them; the structural tests assert their *form* instead of their value.
VOLATILE_KEYS = frozenset(
    {
        "artifact_id",
        "artifact_ids",
        "configuration_sha256",
        "envelope_sha256",
        "git_revision",
        "inputs_sha256",
        "receipt_sha256",
        "repo_id",
        "repository_id",
        "repository_snapshot_sha256",
        "request_sha256",
        "action_request_sha256",
        "root_sha256",
        "snapshot_sha256",
    }
)


@dataclass(frozen=True)
class CanonWorkspace:
    """One produced fixture graph plus everything needed to query it."""

    root: Path
    graph: Path
    binary: str
    build_info: dict


def _build_info(binary: str | Path) -> dict | None:
    """``-build-info`` is the producer's identity probe; anything that cannot
    answer it is not a usable producer for this suite."""
    try:
        probe = subprocess.run(
            [str(binary), "-build-info"], capture_output=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if probe.returncode != 0:
        return None
    try:
        info = json.loads(probe.stdout.decode("utf-8", "replace"))
    except ValueError:
        return None
    return info if isinstance(info, dict) and info.get("schema") else None


def producer_candidates() -> list[str]:
    """Every place a runnable producer may live on this host."""
    candidates: list[str] = []
    if os.environ.get("GT_INDEX_BINARY"):
        candidates.append(os.environ["GT_INDEX_BINARY"])
    # Windows smoke-matrix producer (this host's known-good binary).
    candidates.append(r"C:\gt-smoke-a6\gt-index-new.exe")
    for name in ("gt-index", "gt-index.exe"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    vendored = HARNESS_ROOT / "vendor" / "gt-index-linux-amd64"
    if sys.platform.startswith("linux") and platform.machine() in {
        "x86_64",
        "AMD64",
    }:
        candidates.append(str(vendored))
    return candidates


def find_gt_index(tmp_dir: Path | None = None) -> tuple[str, dict] | None:
    """Resolve a runnable producer, or build one from the vendored source."""
    for candidate in producer_candidates():
        info = _build_info(candidate)
        if info is not None:
            return candidate, info
    built = _go_build(tmp_dir) if tmp_dir is not None else None
    if built is not None:
        info = _build_info(built)
        if info is not None:
            return built, info
    return None


def _go_build(out_dir: Path) -> str | None:
    go = shutil.which("go")
    source = HARNESS_ROOT / "vendor" / "gt-index-src"
    if go is None or not (source / "cmd" / "gt-index").is_dir():
        return None
    commit_file = source / "SOURCE-COMMIT"
    commit = (
        commit_file.read_text(encoding="utf-8").strip()
        if commit_file.is_file()
        else ""
    ) or "vendored"
    binary = out_dir / ("gt-index.exe" if os.name == "nt" else "gt-index")
    # Every stamp is required: an unstamped build rolls the analysis phase
    # back (incomplete producer identity) and the graph loses derived edges.
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
            [
                go,
                "build",
                "-tags",
                "sqlite_fts5",
                "-ldflags",
                ldflags,
                "-o",
                str(binary),
                "./cmd/gt-index",
            ],
            cwd=source,
            capture_output=True,
            timeout=900,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return str(binary) if build.returncode == 0 and binary.is_file() else None


def index_fixture(
    binary: str,
    info: dict,
    src_root: Path,
    dst_root: Path,
    graph: Path,
) -> Path:
    """Copy the fixture to ``dst_root`` and index it into ``graph``.

    The fixture is copied — never indexed in place — so the workspace root
    is a scratch dir (keeping the checkout clean and making the produced
    ``repo_id``/absolute-path fields per-run values the scrubber owns).
    """
    shutil.copytree(src_root, dst_root)
    argv = [binary, "-root", str(dst_root), "-output", str(graph)]
    if SOURCE_REVISION_CAPABILITY in (info.get("capabilities") or ()):
        argv += ["-source-revision", FIXTURE_REVISION]
    build = subprocess.run(argv, capture_output=True, timeout=600)
    assert build.returncode == 0, build.stderr.decode("utf-8", "replace")[-2000:]
    assert graph.is_file(), f"producer left no graph at {graph}"
    return graph


@pytest.fixture(scope="session")
def gt_index(tmp_path_factory) -> tuple[str, dict]:
    resolved = find_gt_index(tmp_path_factory.mktemp("gt-index-build"))
    if resolved is None:
        pytest.skip(
            "no runnable gt-index: set GT_INDEX_BINARY, put the producer on "
            "PATH, or install go with CGO for a vendored-source build"
        )
    return resolved


@pytest.fixture(scope="session")
def polyglot_repo(gt_index, tmp_path_factory) -> CanonWorkspace:
    """The fixture repo indexed once per session."""
    binary, info = gt_index
    base = tmp_path_factory.mktemp("canon-polyglot")
    root = base / "repo"
    graph = index_fixture(binary, info, FIXTURE_DIR, root, base / "graph.db")
    return CanonWorkspace(root=root, graph=graph, binary=binary, build_info=info)


@pytest.fixture(scope="session")
def polyglot_conf(polyglot_repo) -> dict[str, Any]:
    """The configuration mapping the query layer sees for this graph."""
    return {
        "configuration_id": "canon-polyglot",
        "graph_db": str(polyglot_repo.graph),
        "graph_source_revision": FIXTURE_REVISION,
    }


@pytest.fixture(scope="session")
def polyglot_pair(gt_index, tmp_path_factory) -> tuple[CanonWorkspace, CanonWorkspace]:
    """Two independent copies of the fixture, indexed into two graphs —
    the determinism suite's pair. Distinct absolute roots prove that the
    produced content is location-independent."""
    binary, info = gt_index
    base = tmp_path_factory.mktemp("canon-pair")
    workspaces: list[CanonWorkspace] = []
    for tag in ("repo_a", "repo_b"):
        root = base / tag
        graph = index_fixture(binary, info, FIXTURE_DIR, root, base / f"{tag}.db")
        workspaces.append(
            CanonWorkspace(root=root, graph=graph, binary=binary, build_info=info)
        )
    return workspaces[0], workspaces[1]


@pytest.fixture(scope="session")
def polyglot_session(polyglot_repo, tmp_path_factory):
    """A GTSession+MiniSweAdapter bound to the produced graph — the same
    object pair the capability facades and the typed runtime consume."""
    from gt_engine.gt_session import GTSession, GTSessionConfig
    from gt_engine.miniswe_integration import MiniSweAdapter

    adapter = MiniSweAdapter(
        task_id="canon-polyglot",
        state_dir=tmp_path_factory.mktemp("canon-state"),
        predicates=[],
        repo_root=polyglot_repo.root,
        graph_db=str(polyglot_repo.graph),
    )
    adapter.engine_state.bind_initial_source(FIXTURE_REVISION)
    session = GTSession(GTSessionConfig(task_id="canon-polyglot"), engine=adapter)
    return session, adapter


def canon_json(value: Any) -> str:
    """Canonical serialization used for goldens and byte-comparisons."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _path_spellings(path: Path) -> tuple[str, ...]:
    """Every serialized spelling an absolute path can take inside JSON text."""
    raw = str(path)
    forms = {raw, raw.replace("\\", "/"), path.as_posix()}
    escaped = set(forms)
    for form in forms:
        # json.dumps escapes each backslash; strip the surrounding quotes.
        escaped.add(json.dumps(form)[1:-1])
    return tuple(sorted(escaped, key=len, reverse=True))


def scrub_payload(payload: Any, workspace: CanonWorkspace) -> Any:
    """Replace absolute-path-derived values so a payload is location-free.

    Two scrub passes: (1) values of ``VOLATILE_KEYS`` are replaced outright
    (hash chains over the request/config cannot be recomputed without the
    producer); (2) every string containing the absolute root or graph path —
    in any spelling — gets the placeholder substituted inside the string.
    Deterministic content (answers, omissions, content hashes, relative
    paths) is untouched.
    """
    root_tag = "<REPO_ROOT>"
    graph_tag = "<GRAPH_DB>"
    replacements = [
        *[(spelling, root_tag) for spelling in _path_spellings(workspace.root)],
        *[(spelling, graph_tag) for spelling in _path_spellings(workspace.graph)],
        (hashlib.sha256(str(workspace.root).encode("utf-8")).hexdigest(), root_tag),
        (hashlib.sha256(str(workspace.graph).encode("utf-8")).hexdigest(), graph_tag),
    ]

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: ("<VOLATILE>" if key in VOLATILE_KEYS else scrub(item))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, str):
            out = value
            for needle, tag in replacements:
                if needle and needle in out:
                    out = out.replace(needle, tag)
            return out
        return value

    return scrub(payload)


def canonical_output(output_text: str, workspace: CanonWorkspace) -> str:
    """A compiled-observation ``output`` string, scrubbed and re-serialized —
    the canonical byte string compared across machines and graph builds."""
    return canon_json(scrub_payload(json.loads(output_text), workspace))


def golden_assert(name: str, payload: Any, workspace: CanonWorkspace) -> None:
    """Compare ``payload`` against ``goldens/<name>.json``.

    ``GT_UPDATE_GOLDENS=1`` regenerates instead of comparing — the only
    sanctioned regeneration path. The stored form is the scrubbed payload
    pretty-printed with sorted keys, so a diff names the field that moved.
    """
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    target = GOLDEN_DIR / f"{name}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = (
        json.dumps(
            scrub_payload(payload, workspace),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if os.environ.get("GT_UPDATE_GOLDENS") == "1":
        target.write_text(rendered, encoding="utf-8")
        return
    if not target.is_file():
        raise AssertionError(
            f"golden {target} missing — regenerate with GT_UPDATE_GOLDENS=1 "
            f"after eyeballing the payload"
        )
    expected = target.read_text(encoding="utf-8")
    assert rendered == expected, (
        f"golden mismatch for {name}:\n"
        + "\n".join(
            __import__("difflib").unified_diff(
                expected.splitlines(),
                rendered.splitlines(),
                fromfile="golden",
                tofile="actual",
                lineterm="",
            )
        )
    )


def dump_table(graph: Path, table: str) -> list[dict[str, Any]]:
    """One table's full row set as dicts, ordered by rowid for stability."""
    with sqlite3.connect(f"file:{graph.as_posix()}?mode=ro", uri=True) as conn:
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
    return [dict(zip(cols, row)) for row in rows]


_HEX_TOKEN = __import__("re").compile(r"[0-9a-f]{32,}")


def _is_id_cell(value: Any) -> bool:
    """A cell whose whole value is one identity token (hex digest), or a
    JSON/text cell embedding identity tokens."""
    return isinstance(value, str) and bool(_HEX_TOKEN.search(value))


def assert_graphs_isomorphic(
    graph_a: Path, graph_b: Path, tables: tuple[str, ...]
) -> dict[str, str]:
    """Assert two produced graphs are identical modulo location-scoped ids.

    The producer stamps some columns with identities derived from the
    repository identity (``repo_id`` and everything hashed over it —
    ``stable_id``, ``callsite_id``, ``*_set_id``, process/community ids and
    the JSON id lists that reference them). Two builds of the same content
    under different roots must therefore produce rows that are equal in
    every content column and related by a consistent bijection on every id
    column. This function verifies exactly that:

    1. per table, per column: the multiset of values must be equal unless
       the column is id-bearing (then it may differ);
    2. rows are aligned by their content columns and every id token is
       mapped a->b into one global bijection — any inconsistency (one id
       mapping to two, or an unmapped id surviving into another table)
       fails with the offending value;
    3. the rewritten rows of A must equal the raw rows of B verbatim.

    Returns the witnessed a->b id map so callers can report its size.
    """
    id_map: dict[str, str] = {}
    id_map_rev: dict[str, str] = {}

    def remember(a_val: str, b_val: str, where: str) -> None:
        prior = id_map.get(a_val)
        if prior is not None and prior != b_val:
            raise AssertionError(
                f"id {a_val} maps to both {prior} and {b_val} ({where})"
            )
        prior_rev = id_map_rev.get(b_val)
        if prior_rev is not None and prior_rev != a_val:
            raise AssertionError(
                f"id {b_val} is claimed by both {prior_rev} and {a_val} ({where})"
            )
        id_map[a_val] = b_val
        id_map_rev[b_val] = a_val

    def resolve_cell(value: Any, mapping: dict[str, str]) -> tuple[str, tuple[str, ...]]:
        """Rewrite every known id token inside a cell through ``mapping``;
        returns (resolved_text, unknown_tokens)."""
        if not isinstance(value, str):
            return repr(value), ()
        unknown: list[str] = []

        def swap(match):
            token = match.group(0)
            mapped = mapping.get(token)
            if mapped is None:
                unknown.append(token)
                return f"@@{token}@@"
            return mapped

        return _HEX_TOKEN.sub(swap, value), tuple(unknown)

    for table in tables:
        rows_a = dump_table(graph_a, table)
        rows_b = dump_table(graph_b, table)
        assert len(rows_a) == len(rows_b), (
            f"{table}: {len(rows_a)} rows vs {len(rows_b)}"
        )
        cols = list(rows_a[0].keys()) if rows_a else []
        # The rowid is an insertion-order artifact, not content: the
        # producer emits edges/derived rows through Go map iteration, so
        # identical edge sets get different rowids across builds. Node
        # rowids are observed stable and stay compared; every other
        # table's `id` column is excluded from the signature entirely.
        # Inventory tables additionally carry foreign references INTO a
        # volatile rowid (edge_id -> edges.id etc.); the referenced
        # content is still verified — the row's content_sha256 digest is
        # deterministic — only the rowid linkage is excluded.
        volatile = set() if table == "nodes" else {"id"}
        volatile |= {
            "parser_edge_inventory": {"edge_id"},
            "parser_assertion_inventory": {"assertion_id"},
            "parser_property_inventory": {"property_id"},
        }.get(table, set())
        # A column is content iff its value multiset is identical in both
        # builds; everything else is id-bearing by definition here.
        id_cols = [
            c
            for c in cols
            if c not in volatile
            and sorted(map(repr, (r[c] for r in rows_a)))
            != sorted(map(repr, (r[c] for r in rows_b)))
        ]
        content_cols = [c for c in cols if c not in id_cols and c not in volatile]

        def signature(row: dict, mapping: dict[str, str]) -> tuple[tuple, tuple]:
            """(group_key, unresolved) — the group key carries every cell
            whose identity is already known (content verbatim, ids
            rewritten into the counterpart's space); unresolved lists the
            cells still carrying unmapped tokens."""
            key_parts: list[str] = []
            unresolved: list[tuple[str, str, tuple[str, ...]]] = []
            for c in sorted(cols):
                if c in volatile:
                    continue  # insertion-order rowid — not content
                if c in content_cols:
                    key_parts.append(repr(row[c]))
                    continue
                resolved, unknown = resolve_cell(row[c], mapping)
                if unknown:
                    unresolved.append((c, resolved, unknown))
                    key_parts.append("<?>")
                else:
                    key_parts.append(resolved)
            return tuple(key_parts), tuple(unresolved)

        # Both signatures live in B-space: A rows rewrite known a-tokens to
        # their b-images; B rows keep their own tokens but mark a cell
        # unresolved unless every token is already claimed by the map.
        b_known = {b_val: b_val for b_val in id_map_rev}
        groups_a: dict[tuple, list[tuple[dict, tuple]]] = {}
        groups_b: dict[tuple, list[tuple[dict, tuple]]] = {}
        for row in rows_a:
            sig, unresolved = signature(row, id_map)
            groups_a.setdefault(sig, []).append((row, unresolved))
        for row in rows_b:
            sig, unresolved = signature(row, b_known)
            groups_b.setdefault(sig, []).append((row, unresolved))
        if set(groups_a) != set(groups_b):
            only_a = sorted(set(groups_a) - set(groups_b))[:3]
            only_b = sorted(set(groups_b) - set(groups_a))[:3]
            raise AssertionError(
                f"{table}: resolved row sets diverge\n"
                f"  only in build A: {only_a}\n  only in build B: {only_b}"
            )
        for sig in groups_a:
            ga, gb = groups_a[sig], groups_b[sig]
            assert len(ga) == len(gb), (
                f"{table}: resolved group has {len(ga)} vs {len(gb)} rows"
            )
            # Rows whose every id is already mapped pair exactly; residual
            # ties sort by the raw id tuple (deterministic fallback).
            def order(item):
                row, _un = item
                return tuple(repr(row[c]) for c in sorted(id_cols))

            for (ra, un_a), (rb, un_b) in zip(
                sorted(ga, key=order), sorted(gb, key=order)
            ):
                unresolved_a = {c: (res, unk) for c, res, unk in un_a}
                unresolved_b = {c: (res, unk) for c, res, unk in un_b}
                assert set(unresolved_a) == set(unresolved_b), (
                    f"{table}: unresolved columns differ within a resolved "
                    f"group {sorted(unresolved_a)} vs {sorted(unresolved_b)}"
                )
                for c in sorted(unresolved_a):
                    _res, tok_a = unresolved_a[c]
                    _res2, tok_b = unresolved_b[c]
                    assert len(tok_a) == len(tok_b), (
                        f"{table}.{c}: token arity {ra[c]!r} vs {rb[c]!r}"
                    )
                    for xa, xb in zip(tok_a, tok_b):
                        remember(xa, xb, f"{table}.{c}")

    return id_map


# ---------------------------------------------------------------------------
# Runtime-intelligence fixtures (F.2/F.3)
#
# ``index_fixture`` shells the producer straight into ``-output graph.db``:
# real graph rows, but no certification manifest, so the synchronous amend
# chain can only refuse it (``parent_manifest_missing``). The runtime and
# cross-layer suites need an adoption-capable parent, so they bind a graph
# produced by the production ``indexer.ensure_index`` path — publication
# lock, revision store, manifest — into a live MiniSweAdapter + GTSession,
# the same object pair that performs the amend and serves the facades.
# ---------------------------------------------------------------------------


@dataclass
class RuntimeWorkspace:
    """One certified fixture workspace bound into the live engine pair.

    ``root`` is a tmp copy of the fixture tree — the only place tests may
    edit. ``graph`` is the certified published parent. ``adapter`` owns the
    journal/CAS store under ``state / task_id`` and the EngineState whose
    ``graph_current`` flag gates every freshness assertion.
    """

    root: Path
    state: Path
    task_id: str
    layout: Any
    graph: Path
    diagnostics: tuple[str, ...]
    adapter: Any
    session: Any
    binary: str
    build_info: dict

    def journal_events(self) -> list[dict[str, Any]]:
        """Every journaled row, in order — a read-only scan."""
        path = self.state / self.task_id / "events.jsonl"
        if not path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows

    def journal_event(self, event: str) -> dict[str, Any] | None:
        """The most recent row of one journal event type, or None."""
        last: dict[str, Any] | None = None
        for row in self.journal_events():
            if row.get("event") == event:
                last = row
        return last

    def cas_blob(self, namespace: str, digest: str) -> bytes | None:
        """One immutable CAS blob, or None — never synthesized."""
        target = self.state / self.task_id / namespace / f"{digest}.json"
        return target.read_bytes() if target.is_file() else None


@pytest.fixture
def runtime_workspace(gt_index, tmp_path, monkeypatch):
    """Factory producing certified runtime workspaces: ``build(tag)``.

    Each call copies ``fixtures/polyglot`` into a fresh scratch root,
    indexes it with the REAL producer through ``indexer.ensure_index``
    (which publishes the revision + certification manifest the amend
    chain requires), and binds the graph into ``MiniSweAdapter`` +
    ``GTSession`` with ``bind_initial_source`` — the same binding the
    production runtime performs. A test may call ``build`` more than
    once for independent engine instances; each call pays one real
    producer run.

    On Windows the production indexer refuses to spawn the producer
    until a verifiable descendant-teardown guard exists; these tests
    stand in a verified kill for it — the same established pattern as
    ``tests/test_index_incremental.py``. The refusal remains production
    behavior outside the suite.
    """
    from gt_engine import indexer
    from gt_engine.engine_state import RuntimeLayout
    from gt_engine.gt_session import GTSession, GTSessionConfig
    from gt_engine.miniswe_integration import MiniSweAdapter

    binary, info = gt_index
    # ensure_index resolves the binary itself; pin it to the very producer
    # the session fixture probed (also what a vendored-source build needs).
    monkeypatch.setenv("GT_INDEX_BINARY", binary)
    if os.name == "nt":
        def verified_test_kill(process):
            if process.poll() is None:
                process.kill()
            return True

        monkeypatch.setattr(
            indexer, "_has_verified_index_process_tree_guard", lambda: True
        )
        monkeypatch.setattr(
            indexer, "_kill_index_process_tree", verified_test_kill
        )

    import itertools

    counter = itertools.count()

    def build(tag: str = "ws") -> RuntimeWorkspace:
        n = next(counter)
        base = tmp_path / f"rt-{tag}-{n}"
        root = base / "repo"
        shutil.copytree(FIXTURE_DIR, root)
        state = base / "state"
        task_id = f"canon-rt-{tag}-{n}"
        layout = RuntimeLayout.resolve(
            workspace=root, state_root=state, task_id=task_id
        )
        diagnostics: list[str] = []
        graph = indexer.ensure_index(
            str(root),
            layout=layout,
            source_revision=FIXTURE_REVISION,
            diagnostics=diagnostics,
        )
        if graph is None:
            pytest.skip(
                "real producer could not publish a certified graph: "
                + ("; ".join(diagnostics) or "no diagnostics recorded")
            )
        adapter = MiniSweAdapter(
            task_id=task_id,
            state_dir=state,
            predicates=[],
            repo_root=root,
            graph_db=graph,
            layout=layout,
        )
        adapter.engine_state.bind_initial_source(FIXTURE_REVISION)
        session = GTSession(GTSessionConfig(task_id=task_id), engine=adapter)
        return RuntimeWorkspace(
            root=root,
            state=state,
            task_id=task_id,
            layout=layout,
            graph=Path(graph),
            diagnostics=tuple(diagnostics),
            adapter=adapter,
            session=session,
            binary=binary,
            build_info=info,
        )

    return build
