"""E: assert the capability registry against the code it describes.

The registry in ``gt_engine/capabilities/registry.py`` is only worth having
if it cannot drift: every ``implementation``/``facade``/``tests`` anchor must
resolve, every ``MODEL_FACING`` entry must have a real call path from
``gt_engine/miniswe_runtime.py`` to its implementation, every ``COMPUTED``
entry must not, and the set of kinds the registry marks ``typed_on_request``
must equal ``CERTIFIED_TYPED_KINDS``.

Reachability is a bounded static call graph, not a grep for the name: the
runtime file's referenced names seed the walk (import and callsite check),
then each visited function/method contributes the names its own body
references. Attribute calls (``adapter.x``, ``session.x``) resolve to any
``gt_engine`` def of that bare name, and ``getattr(obj, "name")`` string
arguments count as references — both are real dispatch seams in this code.
"""
from __future__ import annotations

import ast
import importlib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from gt_engine.capabilities import registry
from gt_engine.generated_typed_capabilities import CERTIFIED_TYPED_KINDS

REPO_ROOT = Path(__file__).resolve().parents[2]
GT_ENGINE_DIR = REPO_ROOT / "gt_engine"
RUNTIME_MODULE = "gt_engine.miniswe_runtime"

FACADE_MODULES = (
    "freshness",
    "localization",
    "structure",
    "analysis",
    "change",
    "runtime",
)


# --------------------------------------------------------------------------
# static module index + reachability
# --------------------------------------------------------------------------


def _gt_engine_modules() -> dict[str, Path]:
    """``gt_engine.<sub>.<mod>`` -> path for every module in the package."""
    out: dict[str, Path] = {}
    for path in sorted(GT_ENGINE_DIR.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).with_suffix("")
        out[".".join(rel.parts)] = path
    return out


def _resolve_import(from_module: str, node: ast.ImportFrom) -> str:
    """Absolute module name an ImportFrom in ``from_module`` binds."""
    if node.level:
        package = from_module.split(".")[:-1]
        base = package[: len(package) - (node.level - 1)]
        return ".".join([*base, node.module] if node.module else base)
    return node.module or ""


@dataclass
class _ModuleIndex:
    defs: dict[str, list[ast.AST]] = field(default_factory=dict)
    imports: dict[str, tuple[str, str]] = field(default_factory=dict)
    scope_refs: dict[int, frozenset[str]] = field(default_factory=dict)
    file_refs: frozenset[str] = frozenset()


def _referenced_names(node: ast.AST) -> frozenset[str]:
    """Every identifier/attribute a scope mentions, plus getattr("x") strings."""
    refs: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            refs.add(child.id)
        elif isinstance(child, ast.Attribute):
            refs.add(child.attr)
        elif (
            isinstance(child, ast.Call)
            and len(child.args) >= 2
            and isinstance(child.args[1], ast.Constant)
            and isinstance(child.args[1].value, str)
            and (
                (isinstance(child.func, ast.Name) and child.func.id == "getattr")
                or (
                    isinstance(child.func, ast.Attribute)
                    and child.func.attr == "getattr"
                )
            )
        ):
            refs.add(child.args[1].value)
    return frozenset(refs)


def _parse_module(path: Path) -> _ModuleIndex:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    index = _ModuleIndex()
    module_name = ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            index.defs.setdefault(node.name, []).append(node)
            index.scope_refs[id(node)] = _referenced_names(node)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import(module_name, node)
            for alias in node.names:
                index.imports[alias.asname or alias.name] = (module, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                index.imports[alias.asname or alias.name.split(".", 1)[0]] = (
                    alias.name,
                    "",
                )
    index.file_refs = _referenced_names(tree)
    return index


@pytest.fixture(scope="module")
def call_graph() -> frozenset[tuple[str, str]]:
    """(module, def-name) pairs reachable from miniswe_runtime.py.

    The root set is every name referenced anywhere in the runtime file (the
    import-and-callsite check); each hop then expands only the referenced
    names inside the resolved def's own body.
    """
    modules = _gt_engine_modules()
    parsed = {name: _parse_module(path) for name, path in modules.items()}
    global_defs: dict[str, set[tuple[str, str]]] = {}
    for module, index in parsed.items():
        for name in index.defs:
            global_defs.setdefault(name, set()).add((module, name))

    visited: set[tuple[str, str]] = set()
    stack: list[tuple[str, str]] = [
        (RUNTIME_MODULE, name) for name in parsed[RUNTIME_MODULE].file_refs
    ]
    while stack:
        module, name = stack.pop()
        index = parsed.get(module)
        if index is None:
            continue
        targets: set[tuple[str, str]] = set()
        if name in index.defs:
            targets.add((module, name))
        if name in index.imports:
            imported_module, original = index.imports[name]
            if imported_module in parsed and original in parsed[imported_module].defs:
                targets.add((imported_module, original))
        targets |= global_defs.get(name, set())
        for target in sorted(targets - visited):
            visited.add(target)
            tmodule, tname = target
            for node in parsed[tmodule].defs[tname]:
                stack.extend(
                    (tmodule, ref) for ref in parsed[tmodule].scope_refs[id(node)]
                )
    return frozenset(visited)


# --------------------------------------------------------------------------
# anchor resolution
# --------------------------------------------------------------------------


def _split_anchor(anchor: str) -> tuple[str, str]:
    file_part, _, qualname = anchor.partition(":")
    assert qualname, f"anchor missing ':qualname': {anchor}"
    return file_part, qualname


def _repo_module_name(file_part: str) -> str | None:
    path = REPO_ROOT / file_part
    if not file_part.endswith(".py") or not path.is_file():
        return None
    return ".".join(Path(file_part).with_suffix("").parts)


def _qualname_defined(path: Path, qualname: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parts = qualname.split(".")

    def walk(body: list[ast.stmt], remaining: list[str]) -> bool:
        for node in body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name == remaining[0]
            ):
                if len(remaining) == 1:
                    return True
                return walk(node.body, remaining[1:])
        return False

    return walk(tree.body, parts)


def _anchor_resolves(anchor: str) -> bool:
    file_part, qualname = _split_anchor(anchor)
    repo_path = REPO_ROOT / file_part
    if repo_path.is_file():
        return _qualname_defined(repo_path, qualname)
    module = importlib.import_module(file_part)  # e.g. groundtruth.runtime.*
    target = module
    for part in qualname.split("."):
        target = getattr(target, part)
    return callable(target)


def _test_ref_resolves(ref: str) -> bool:
    path_part, _, func = ref.partition("::")
    path = REPO_ROOT / path_part
    return path.is_file() and _qualname_defined(path, func)


# --------------------------------------------------------------------------
# registry shape
# --------------------------------------------------------------------------


def test_registry_vocabulary_and_invariants():
    for entry in registry.entries():
        assert entry.classification in registry.CLASSIFICATIONS, entry.name
        assert entry.state in registry.STATES, entry.name
        assert registry.valid_delivery_path(entry.delivery_path), entry.name
        if entry.state == "MODEL_FACING":
            assert entry.delivery_path != "none", (
                f"{entry.name} claims MODEL_FACING with no delivery path"
            )
        for kind in entry.typed_kinds:
            assert kind in CERTIFIED_TYPED_KINDS, (
                f"{entry.name} delegates to uncertified kind {kind}"
            )


def test_every_public_facade_is_registered():
    """Every public function the package exposes has a registry entry, and a
    registered facade is missing only when it is honestly marked pending."""
    registered: dict[str, registry.CapabilityEntry] = {}
    for entry in registry.entries():
        if entry.facade:
            file_part, qualname = _split_anchor(entry.facade)
            registered[f"{file_part}:{qualname}"] = entry
    for module in FACADE_MODULES:
        path = GT_ENGINE_DIR / "capabilities" / f"{module}.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not (
                node.name.startswith("_")
            ):
                key = f"gt_engine/capabilities/{module}.py:{node.name}"
                assert key in registered, (
                    f"public facade {module}.{node.name} has no registry entry"
                )
    present = {
        f"gt_engine/capabilities/{module}.py:{node.name}"
        for module in FACADE_MODULES
        for node in ast.parse(
            (GT_ENGINE_DIR / "capabilities" / f"{module}.py").read_text(
                encoding="utf-8"
            )
        ).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    for key, entry in registered.items():
        if key not in present:
            assert entry.facade_pending, (
                f"{entry.name} anchors facade {key} which does not exist and "
                "is not marked facade_pending"
            )


def test_implementation_anchors_resolve():
    for entry in registry.entries():
        assert _anchor_resolves(entry.implementation), (
            f"{entry.name}: implementation anchor does not resolve: "
            f"{entry.implementation}"
        )


def test_facade_anchors_resolve_unless_pending():
    for entry in registry.entries():
        if not entry.facade:
            continue
        file_part, qualname = _split_anchor(entry.facade)
        path = REPO_ROOT / file_part
        if entry.facade_pending and not path.is_file():
            continue
        assert path.is_file(), f"{entry.name}: missing facade file {file_part}"
        if entry.facade_pending and not _qualname_defined(path, qualname):
            continue  # named by plan item D; lands with the in-flight refactor
        assert _qualname_defined(path, qualname), (
            f"{entry.name}: facade anchor does not resolve: {entry.facade}"
        )


def test_test_references_resolve():
    for entry in registry.entries():
        for ref in entry.tests:
            assert _test_ref_resolves(ref), (
                f"{entry.name}: test reference does not resolve: {ref}"
            )


# --------------------------------------------------------------------------
# delivery truth
# --------------------------------------------------------------------------


def _impl_graph_target(anchor: str) -> tuple[str, str] | None:
    file_part, qualname = _split_anchor(anchor)
    module = _repo_module_name(file_part)
    if module is None:
        return None
    return module, qualname.split(".")[-1]


def test_model_facing_entries_are_reachable(call_graph):
    """Every MODEL_FACING entry has a call path from miniswe_runtime.py to
    its implementation (import + callsite check, then transitive bodies)."""
    for entry in registry.by_state("MODEL_FACING"):
        target = _impl_graph_target(entry.implementation)
        assert target is not None, (
            f"{entry.name}: MODEL_FACING implementation {entry.implementation} "
            "is not a repo module the graph can check"
        )
        assert target in call_graph, (
            f"{entry.name}: no call path from miniswe_runtime.py to "
            f"{entry.implementation}"
        )


def test_computed_only_entries_have_no_path(call_graph):
    for entry in registry.by_state("COMPUTED"):
        target = _impl_graph_target(entry.implementation)
        if target is None:
            continue
        assert target not in call_graph, (
            f"{entry.name}: marked COMPUTED but miniswe_runtime.py reaches "
            f"{entry.implementation}"
        )


# --------------------------------------------------------------------------
# typed-kind truth
# --------------------------------------------------------------------------


def test_typed_on_request_iff_certified():
    """The kinds the registry marks delivered through typed_on_request are
    exactly CERTIFIED_TYPED_KINDS."""
    assert registry.typed_on_request_kinds() == frozenset(CERTIFIED_TYPED_KINDS)


def test_every_certified_kind_has_a_registry_entry():
    kind_entries = {
        entry.typed_kinds[0]
        for entry in registry.entries()
        if entry.name.startswith("kind.")
    }
    assert kind_entries == set(CERTIFIED_TYPED_KINDS)


def test_facade_typed_kind_mapping_matches_source():
    """A facade marked typed_kinds must literally delegate to those kinds in
    its own source (or in a same-package facade it calls)."""
    module_sources = {
        module: (GT_ENGINE_DIR / "capabilities" / f"{module}.py").read_text(
            encoding="utf-8"
        )
        for module in FACADE_MODULES
    }
    facade_funcs: dict[str, tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for module, source in module_sources.items():
        for node in ast.parse(source).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                facade_funcs[node.name] = (module, node)

    def combined_source(entry) -> str:
        _, qualname = _split_anchor(entry.facade)
        seen: set[str] = set()
        texts: list[str] = []
        frontier = [qualname.split(".")[-1]]
        while frontier:
            fname = frontier.pop()
            if fname in seen or fname not in facade_funcs:
                continue
            seen.add(fname)
            module, node = facade_funcs[fname]
            segment = ast.get_source_segment(module_sources[module], node) or ""
            texts.append(segment)
            frontier.extend(
                child.func.id
                for child in ast.walk(node)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id in facade_funcs
            )
        return "\n".join(texts)

    for entry in registry.entries():
        if not entry.facade or not entry.typed_kinds or entry.facade_pending:
            continue
        source = combined_source(entry)
        for kind in entry.typed_kinds:
            assert f'"{kind}"' in source, (
                f"{entry.name}: registry claims delegation to {kind} but the "
                f"facade source (including same-package callees) never names it"
            )
