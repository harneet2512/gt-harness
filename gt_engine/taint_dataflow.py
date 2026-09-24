"""Statement-level Python taint dataflow bound to certified graph callsites.

The certified producer's ``taint`` kind answers symbol-level CALLS reachability.
This module adds the harness-side statement layer for Python: it rebuilds
intraprocedural CFG/def-use structure from the checked-out source via the
installed ``groundtruth.runtime.cfg_analysis`` engine, seeds attacker-controlled
external uses in the source function, propagates taint through def-use chains,
crosses functions through the producer's resolved ``resolution_callsites`` rows
(actual-argument-to-parameter binding plus callee-return flow), and reports
bounded evidence.

Everything this module cannot prove is named in ``dataflow_omissions`` rather
than silently dropped. The certified producer artifacts are never modified;
augmentation happens after the wheel query completes, on the harness side only.
"""

from __future__ import annotations

import ast
import builtins
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

_MAX_FILES = 24
_MAX_FUNCS = 48
_MAX_ROUNDS = 16
_MAX_EVENTS = 512
_MAX_PATH_HOPS = 8
_MAX_PATHS = 32

_BUILTIN_NAMES = set(dir(builtins)) | {"self", "cls"}


@dataclass
class _Func:
    key: tuple[str, int]
    node_id: int
    name: str
    file: str
    tree: ast.FunctionDef | ast.AsyncFunctionDef
    cfg: Any
    chains: Any
    param_names: list[str]
    calls: list[ast.Call] = field(default_factory=list)


@dataclass
class _State:
    funcs: dict[tuple[str, int], _Func] = field(default_factory=dict)
    param_taint: dict[tuple[str, int], dict[str, tuple]] = field(default_factory=dict)
    return_taint: dict[tuple[str, int], tuple | None] = field(default_factory=dict)
    taint_chains: dict[tuple[tuple[str, int], tuple[str, int]], tuple] = field(
        default_factory=dict
    )
    callers: dict[tuple[str, int], set[tuple[str, int]]] = field(default_factory=dict)
    paths: list[dict[str, Any]] = field(default_factory=list)
    cuts: list[dict[str, Any]] = field(default_factory=list)
    unresolved_reaches: list[dict[str, Any]] = field(default_factory=list)
    omissions: set[str] = field(default_factory=set)
    seen_paths: dict[tuple, dict[str, Any]] = field(default_factory=dict)
    seen_cuts: dict[tuple, dict[str, Any]] = field(default_factory=dict)
    seen_unresolved: set[tuple] = field(default_factory=set)
    events: int = 0


class _FileIndex:
    """Parsed Python file: tree plus a FunctionDef index keyed by first line."""

    def __init__(self, path: Path, rel: str) -> None:
        self.rel = rel
        self.tree = ast.parse(path.read_bytes(), filename=str(path))
        self.by_line: dict[int, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.by_line.setdefault(node.lineno, node)


def _callee_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _loads_of(expr: ast.AST) -> list[tuple[str, int]]:
    # Mirror the use-space ``cfg.effects`` produces: bare ``Load`` names plus
    # rendered attribute chains (``a.b`` yields "a.b" and "a").
    out: list[tuple[str, int]] = []
    for node in ast.walk(expr):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            out.append((node.id, node.lineno))
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            try:
                out.append((ast.unparse(node), node.lineno))
            except (ValueError, TypeError):
                pass
    return out


def _calls_in(expr: ast.AST) -> Iterable[ast.Call]:
    # Nested def/class bodies are separate CFG scopes: when ``expr`` is a
    # scope-defining statement (or contains one), only its decorators,
    # defaults, bases, and keywords belong to it — never body statements.
    if isinstance(expr, (ast.FunctionDef, ast.AsyncFunctionDef)):
        stack: list[ast.AST] = [
            *expr.decorator_list,
            *expr.args.defaults,
            *(d for d in expr.args.kw_defaults if d is not None),
        ]
    elif isinstance(expr, ast.ClassDef):
        stack = [*expr.decorator_list, *expr.bases, *expr.keywords]
    else:
        stack = [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Call):
            yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stack.extend(child.decorator_list)
                stack.extend(child.args.defaults)
                stack.extend(d for d in child.args.kw_defaults if d is not None)
                continue
            if isinstance(child, ast.ClassDef):
                stack.extend(child.decorator_list)
                stack.extend(child.bases)
                stack.extend(child.keywords)
                continue
            stack.append(child)


class _Engine:
    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        repo_root: Path,
        sink_ids: set[int],
        sanitizer_ids: set[int],
        sanitizer_names: set[str],
    ) -> None:
        self.conn = conn
        self.repo_root = repo_root
        self.sink_ids = sink_ids
        self.sanitizer_ids = sanitizer_ids
        self.sanitizer_names = sanitizer_names
        self.state = _State()
        self.pending: dict[tuple[str, int], dict[str, tuple]] = {}
        self.dirty_returns: set[tuple[str, int]] = set()
        self.files: dict[str, _FileIndex] = {}
        self.callsites: dict[str, list[dict[str, Any]]] = {}
        for row in conn.execute(
            "SELECT source_file, source_line, callee,"
            " selected_target_native_id FROM resolution_callsites"
        ):
            self.callsites.setdefault(str(row[0]), []).append(
                {
                    "line": int(row[1]),
                    "callee": str(row[2] or ""),
                    "target": row[3],
                }
            )
        self.nodes_by_id: dict[int, tuple[str, str, int]] = {}
        for row in conn.execute(
            "SELECT id, name, file_path, start_line FROM nodes"
            " WHERE label IN ('Function','Method')"
        ):
            self.nodes_by_id[int(row[0])] = (str(row[1]), str(row[2]), int(row[3]))
        self.local_names_by_file: dict[str, set[str]] = {}
        for name, file_path, _line in self.nodes_by_id.values():
            self.local_names_by_file.setdefault(file_path, set()).add(name)

    def file_index(self, rel: str) -> _FileIndex | None:
        if rel in self.files:
            return self.files[rel]
        if len(self.files) >= _MAX_FILES:
            self.state.omissions.add("dataflow_files_truncated")
            return None
        path = (self.repo_root / rel).resolve()
        try:
            path.relative_to(self.repo_root)
        except ValueError:
            self.state.omissions.add(f"dataflow_path_outside_root:{rel}")
            return None
        try:
            index = _FileIndex(path, rel)
        except (OSError, SyntaxError, ValueError):
            self.state.omissions.add(f"dataflow_parse_failed:{rel}")
            return None
        self.files[rel] = index
        return index

    def func_for_node(self, node_id: int) -> _Func | None:
        meta = self.nodes_by_id.get(node_id)
        if meta is None:
            return None
        name, file_path, start_line = meta
        if not file_path.endswith(".py"):
            self.state.omissions.add(f"dataflow_python_only:{file_path}")
            return None
        key = (file_path, start_line)
        cached = self.state.funcs.get(key)
        if cached is not None:
            return cached
        if len(self.state.funcs) >= _MAX_FUNCS:
            self.state.omissions.add("dataflow_functions_truncated")
            return None
        index = self.file_index(file_path)
        if index is None:
            return None
        tree = index.by_line.get(start_line)
        if tree is None or getattr(tree, "name", "") != name:
            self.state.omissions.add(f"dataflow_function_not_in_source:{name}")
            return None
        from groundtruth.runtime.cfg_analysis import (
            build_cfg,
            reaching_definitions,
            use_def_chains,
        )

        cfg = build_cfg(tree, source_lines=None)
        chains = use_def_chains(cfg, reaching_definitions(cfg, tree))
        params = [a.arg for a in (*tree.args.posonlyargs, *tree.args.args)]
        params += [a.arg for a in tree.args.kwonlyargs]
        func = _Func(
            key=key,
            node_id=node_id,
            name=name,
            file=file_path,
            tree=tree,
            cfg=cfg,
            chains=chains,
            param_names=params,
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func.calls.append(node)
        self.state.funcs[key] = func
        return func

    def row_for_call(self, func: _Func, call: ast.Call) -> dict[str, Any] | None:
        same_line = [
            r
            for r in self.callsites.get(func.file, [])
            if r["line"] == call.lineno
        ]
        if not same_line:
            return None
        name = _callee_name(call)
        matched = [r for r in same_line if r["callee"] == name]
        if matched:
            return matched[0]
        if len(same_line) == 1:
            return same_line[0]
        return None

    def _is_sanitizer_call(self, call: ast.Call, row: dict[str, Any] | None) -> bool:
        # Resolved callsites match by node identity: a same-named function in
        # another module is never a sanitizer unless an entry resolved to it.
        if row and row.get("target") is not None:
            return int(row["target"]) in self.sanitizer_ids
        # Unresolved callsites fall back to bare-name entries only, and the
        # imprecision is named.
        name = _callee_name(call)
        if name and name in self.sanitizer_names:
            self.state.omissions.add(f"sanitizer_match_unresolved:{name}")
            return True
        return False

    def analyze(
        self, func: _Func, param_chains: Mapping[str, tuple], seed_externals: bool
    ) -> None:
        state = self.state
        key = func.key
        cfg = func.cfg
        chains = func.chains
        tainted_defs: set[tuple[str, int]] = set()
        tainted_uses: set[tuple[str, int]] = set()
        tainted_call_ids: set[int] = set()
        local_names = self.local_names_by_file.get(func.file, set())
        for pname, chain in param_chains.items():
            dkey = (pname, func.tree.lineno)
            tainted_defs.add(dkey)
            state.taint_chains[(key, dkey)] = chain
        state.param_taint[key] = dict(param_chains)

        # Loads inside a sanitizer call are consumed by the sanitizer: they
        # feed its cut record, never the enclosing statement's defs, returns,
        # or other calls' argument binding.
        sanitizer_loads: list[tuple[int, frozenset]] = []
        for c in func.calls:
            row = self.row_for_call(func, c)
            if self._is_sanitizer_call(c, row):
                sanitizer_loads.append((id(c), frozenset(_loads_of(c))))

        def laundered_for(owner: ast.Call | None) -> frozenset:
            return frozenset().union(
                *(loads for cid, loads in sanitizer_loads if cid != id(owner))
            ) if sanitizer_loads else frozenset()

        def chain_of_use(ukey: tuple[str, int]) -> tuple | None:
            return state.taint_chains.get((key, ("__use__", *ukey)))

        def put_chain(full_key: tuple, chain: tuple | None) -> bool:
            # Chains converge upward: a longer chain carries strictly more
            # provenance (extra param_bind/return_flow hops), so a late-arriving
            # richer lineage replaces a shorter first attribution.
            if chain is None:
                return False
            existing = state.taint_chains.get(full_key)
            if existing is None or len(chain) > len(existing):
                state.taint_chains[full_key] = chain
                return True
            return False

        def arg_taint(expr: ast.AST, owner: ast.Call | None = None) -> tuple | None:
            laundered = laundered_for(owner)
            for n, line in _loads_of(expr):
                if (n, line) in laundered:
                    continue
                if (n, line) in tainted_uses:
                    return chain_of_use((n, line)) or (
                        {"kind": "flow", "var": n, "line": line},
                    )
                for dkey in chains.use_to_defs.get((n, line), ()):
                    if dkey in tainted_defs:
                        return state.taint_chains.get((key, dkey))
            for inner in _calls_in(expr):
                if id(inner) in tainted_call_ids:
                    return state.taint_chains.get((key, ("__call__", inner.lineno)))
            return None

        return_chain: tuple | None = state.return_taint.get(key)
        for _round in range(_MAX_ROUNDS):
            changed = False
            for bid in sorted(cfg.blocks):
                for item in cfg.blocks[bid].statements:
                    defs, uses = cfg.effects(item)
                    for u in uses:
                        ukey = (u.name, u.line)
                        reaching = chains.use_to_defs.get(ukey, set())
                        hit = next(
                            (d for d in reaching if d in tainted_defs), None
                        )
                        if hit is not None:
                            if ukey not in tainted_uses:
                                tainted_uses.add(ukey)
                                changed = True
                            if put_chain(
                                (key, ("__use__", u.name, u.line)),
                                state.taint_chains.get((key, hit)),
                            ):
                                changed = True
                            continue
                        if ukey in tainted_uses:
                            continue
                        if (
                            seed_externals
                            and not reaching
                            and u.name not in local_names
                            and u.name not in _BUILTIN_NAMES
                        ):
                            tainted_uses.add(ukey)
                            if put_chain(
                                (key, ("__use__", u.name, u.line)),
                                (
                                    {
                                        "kind": "external_seed",
                                        "var": u.name,
                                        "file": func.file,
                                        "line": u.line,
                                    },
                                ),
                            ):
                                changed = True
                    for call in _calls_in(item):
                        row = self.row_for_call(func, call)
                        if self._is_sanitizer_call(call, row):
                            continue
                        target_meta = (
                            self.nodes_by_id.get(int(row["target"]))
                            if row and row["target"] is not None
                            else None
                        )
                        if target_meta is not None:
                            rt = state.return_taint.get(
                                (target_meta[1], target_meta[2])
                            )
                            if rt:
                                tainted_call_ids.add(id(call))
                                # ``rt`` may lengthen in a later round as the
                                # callee's return lineage grows — the recorded
                                # ``__call__`` chain upgrades with it.
                                if put_chain(
                                    (key, ("__call__", call.lineno)),
                                    rt
                                    + (
                                        {
                                            "kind": "return_flow",
                                            "callsite": f"{func.file}:{call.lineno}",
                                            "callee": target_meta[0],
                                        },
                                    ),
                                ):
                                    changed = True
                    # Loads inside a sanitizer call feed the sanitizer, not
                    # the statement's own outputs: they cannot taint this
                    # item's defs or a return through it.
                    laundered: set[tuple[str, int]] = set()
                    for call in _calls_in(item):
                        row = self.row_for_call(func, call)
                        if self._is_sanitizer_call(call, row):
                            laundered.update(_loads_of(call))
                    # A resolved callee's return-flow chain carries the true
                    # lineage (seed -> param_bind -> return_flow); it is
                    # preferred over seeded/bare uses of the callee expression.
                    item_taint: tuple | None = None
                    for call in _calls_in(item):
                        if id(call) in tainted_call_ids:
                            item_taint = state.taint_chains.get(
                                (key, ("__call__", call.lineno))
                            )
                            break
                    if item_taint is None:
                        for u in uses:
                            ukey = (u.name, u.line)
                            if ukey in tainted_uses and ukey not in laundered:
                                item_taint = chain_of_use(ukey) or (
                                    {"kind": "flow", "var": u.name, "line": u.line},
                                )
                                break
                    if item_taint:
                        for d in defs:
                            dkey = (d.name, d.line)
                            if dkey not in tainted_defs:
                                tainted_defs.add(dkey)
                                if d.weak:
                                    state.omissions.add(
                                        "weak_mutation_flow_overapprox"
                                    )
                                    if "." in d.name:
                                        # A tainted attribute store
                                        # (``obj.x = v``) rides the object,
                                        # not a parameter — the cross-function
                                        # flow is untracked.
                                        state.omissions.add(
                                            "attribute_flow_untracked"
                                        )
                            if put_chain((key, dkey), item_taint):
                                changed = True
                    if isinstance(item, ast.Return):
                        for u in uses:
                            ukey = (u.name, u.line)
                            if ukey in tainted_uses and ukey not in laundered:
                                cand = chain_of_use(ukey)
                                if cand and (
                                    return_chain is None
                                    or len(cand) > len(return_chain)
                                ):
                                    return_chain = cand
                                    changed = True
                                break
            if not changed:
                break
        else:
            state.omissions.add("dataflow_local_fixpoint_capped")

        if state.return_taint.get(key) != return_chain:
            state.return_taint[key] = return_chain
            self.dirty_returns.add(key)

        for call in func.calls:
            self._handle_call(func, call, arg_taint)

    def _handle_call(self, func: _Func, call: ast.Call, arg_taint: Any) -> None:
        state = self.state
        if state.events > _MAX_EVENTS:
            state.omissions.add("dataflow_events_truncated")
            return
        row = self.row_for_call(func, call)
        tainted_arg = False
        for expr in [*(a.value if isinstance(a, ast.Starred) else a for a in call.args)]:
            if arg_taint(expr, call):
                tainted_arg = True
        for kw in call.keywords:
            if arg_taint(kw.value, call):
                tainted_arg = True
        target_id = row["target"] if row else None
        callee_label = (row or {}).get("callee") or _callee_name(call)
        if target_id is None:
            if tainted_arg:
                ukey = (func.file, call.lineno, callee_label)
                if ukey not in state.seen_unresolved:
                    state.seen_unresolved.add(ukey)
                    state.events += 1
                    state.unresolved_reaches.append(
                        {
                            "callsite": f"{func.file}:{call.lineno}",
                            "callee": callee_label,
                            "reason": "callsite_unresolved",
                        }
                    )
                    state.omissions.add("callsite_args_unresolved")
            return
        target_meta = self.nodes_by_id.get(int(target_id))
        if target_meta is None:
            if tainted_arg:
                state.omissions.add("callsite_args_unresolved")
            return
        tname, tfile, tline = target_meta
        target_key = (tfile, tline)
        state.callers.setdefault(target_key, set()).add(func.key)
        target = self.func_for_node(int(target_id))
        if target is None:
            return
        params = target.param_names
        bound: dict[str, tuple] = {}
        pos_index = 0
        for expr in call.args:
            if isinstance(expr, ast.Starred):
                if arg_taint(expr.value, call):
                    state.omissions.add("star_args_unbound")
                continue
            if pos_index >= len(params):
                break
            chain = arg_taint(expr, call)
            if chain:
                bound[params[pos_index]] = chain + (
                    {
                        "kind": "param_bind",
                        "callsite": f"{func.file}:{call.lineno}",
                        "callee": tname,
                        "param": params[pos_index],
                    },
                )
            pos_index += 1
        for kw in call.keywords:
            if kw.arg is None:
                if arg_taint(kw.value, call):
                    state.omissions.add("star_args_unbound")
                continue
            chain = arg_taint(kw.value, call)
            if chain and kw.arg in params:
                bound[kw.arg] = chain + (
                    {
                        "kind": "param_bind",
                        "callsite": f"{func.file}:{call.lineno}",
                        "callee": tname,
                        "param": kw.arg,
                    },
                )
            elif chain:
                state.omissions.add(f"kwarg_unbound:{kw.arg}")
        if target.node_id in self.sanitizer_ids and bound:
            ckey = (target.name, func.file, call.lineno)
            existing_cut = state.seen_cuts.get(ckey)
            if existing_cut is None:
                cut = {
                    "sanitizer": target.name,
                    "callsite": f"{func.file}:{call.lineno}",
                    "tainted_params": sorted(bound),
                }
                state.seen_cuts[ckey] = cut
                state.cuts.append(cut)
            elif len(bound) > len(existing_cut["tainted_params"]):
                existing_cut["tainted_params"] = sorted(bound)
        merged = state.param_taint.setdefault(target_key, {})
        new = {
            k: v
            for k, v in bound.items()
            if k not in merged or len(v) > len(merged[k])
        }
        if new:
            merged.update(new)
            self.pending[target_key] = dict(self.pending.get(target_key, {}), **new)
        if int(target_id) in self.sink_ids and bound:
            pkey = (func.key, int(target_id), call.lineno)
            longest = max(bound.values(), key=len)
            existing_path = state.seen_paths.get(pkey)
            if existing_path is not None:
                # A longer re-bound chain carries more provenance; refresh the
                # recorded hops so the answer shows the full lineage.
                if len(longest) > len(existing_path["hops"]):
                    hops = list(longest)[-_MAX_PATH_HOPS:]
                    if len(longest) > _MAX_PATH_HOPS:
                        state.omissions.add("dataflow_path_hops_truncated")
                    existing_path["hops"] = hops
                    existing_path["tainted_params"] = sorted(bound)
            else:
                state.events += 1
                if len(state.paths) >= _MAX_PATHS:
                    state.omissions.add("dataflow_paths_truncated")
                else:
                    hops = list(longest)[-_MAX_PATH_HOPS:]
                    if len(longest) > _MAX_PATH_HOPS:
                        state.omissions.add("dataflow_path_hops_truncated")
                    path = {
                        "caller": func.name,
                        "caller_file": func.file,
                        "sink": tname,
                        "sink_file": tfile,
                        "sink_line": call.lineno,
                        "tainted_params": sorted(bound),
                        "hops": hops,
                    }
                    state.seen_paths[pkey] = path
                    state.paths.append(path)


def _resolve_symbol_nodes(
    conn: sqlite3.Connection,
    name: str,
    *,
    path: str | None,
    language: str | None,
) -> list[tuple[int, str, str, int]]:
    sql = (
        "SELECT id, name, file_path, start_line FROM nodes"
        " WHERE label IN ('Function','Method') AND name = ?"
    )
    params: list[Any] = [name]
    if path:
        sql += " AND file_path LIKE ?"
        params.append(f"{path.rstrip('/').rstrip('%')}%")
    rows = list(conn.execute(sql, params))
    if language and language.lower() == "python":
        rows = [r for r in rows if str(r[2]).endswith(".py")]
    return [(int(r[0]), str(r[1]), str(r[2]), int(r[3])) for r in rows]


def augment_taint(
    *,
    conn: sqlite3.Connection,
    repo_root: Path,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Run bounded statement-level taint dataflow for one ``taint`` request.

    Returns ``{"dataflow_paths", "sanitizer_cuts", "unresolved_reaches",
    "dataflow_scope", "dataflow_omissions"}``. Analysis gaps are always named
    in ``dataflow_omissions``; this function never fabricates coverage.
    """
    source = str(arguments.get("source") or "")
    sink = str(arguments.get("sink") or "")
    path_arg = arguments.get("path")
    path = str(path_arg) if isinstance(path_arg, str) and path_arg else None
    lang_arg = arguments.get("language")
    language = str(lang_arg) if isinstance(lang_arg, str) and lang_arg else None
    sanitizers_raw = arguments.get("sanitizers")
    sanitizers: set[str] = set()
    if isinstance(sanitizers_raw, Sequence) and not isinstance(sanitizers_raw, str):
        sanitizers = {str(s) for s in sanitizers_raw if isinstance(s, str) and s}

    src_rows = _resolve_symbol_nodes(conn, source, path=path, language=language)
    sink_rows = (
        _resolve_symbol_nodes(conn, sink, path=path, language=language)
        if sink
        else []
    )
    sink_ids = {r[0] for r in sink_rows}

    # Sanitizer entries resolve to graph nodes. ``file.py:name`` scopes to one
    # definition; a bare name denotes every callable with that name (matching
    # breadth is named as an ambiguity), and an entry matching nothing is an
    # unresolved reference, not silence.
    sanitizer_ids: set[int] = set()
    sanitizer_names: set[str] = set()
    entry_omissions: list[str] = []
    for entry in sorted(sanitizers):
        if ":" in entry:
            entry_path, _, entry_name = entry.rpartition(":")
            rows = _resolve_symbol_nodes(
                conn, entry_name, path=entry_path, language=language
            )
        else:
            rows = _resolve_symbol_nodes(
                conn, entry, path=path, language=language
            )
            sanitizer_names.add(entry)
        if not rows:
            entry_omissions.append(f"sanitizer_unresolved:{entry}")
        elif len(rows) > 1:
            entry_omissions.append(f"sanitizer_name_ambiguous:{entry}")
        sanitizer_ids.update(r[0] for r in rows)

    engine = _Engine(
        conn=conn,
        repo_root=repo_root,
        sink_ids=sink_ids,
        sanitizer_ids=sanitizer_ids,
        sanitizer_names=sanitizer_names,
    )
    state = engine.state
    state.omissions.update(entry_omissions)
    if len({r[2] for r in src_rows}) > 1:
        state.omissions.add(f"dataflow_source_ambiguous:{source}")
    if len({r[2] for r in sink_rows}) > 1:
        state.omissions.add(f"dataflow_sink_ambiguous:{sink}")

    root_keys: set[tuple[str, int]] = set()
    for node_id, _name, _file, _line in src_rows:
        func = engine.func_for_node(node_id)
        if func is not None:
            root_keys.add(func.key)
            engine.pending[func.key] = {}
    if not root_keys:
        state.omissions.add(f"dataflow_source_unresolved:{source}")
    if sink and not sink_ids:
        state.omissions.add(f"dataflow_sink_unresolved:{sink}")
    elif not sink:
        state.omissions.add("dataflow_sink_unspecified")

    rounds = 0
    while engine.pending and rounds < _MAX_ROUNDS:
        rounds += 1
        pending = engine.pending
        engine.pending = {}
        for key in sorted(pending):
            func = state.funcs.get(key)
            if func is None:
                continue
            merged = dict(state.param_taint.get(key, {}))
            merged.update(pending[key])
            engine.analyze(func, merged, seed_externals=key in root_keys)
        for changed_key in sorted(engine.dirty_returns):
            for caller_key in engine.state.callers.get(changed_key, ()):
                if caller_key != changed_key:
                    engine.pending.setdefault(
                        caller_key, dict(state.param_taint.get(caller_key, {}))
                    )
        engine.dirty_returns.clear()
    if engine.pending:
        state.omissions.add("dataflow_fixpoint_rounds_capped")

    return {
        "dataflow_paths": state.paths,
        "sanitizer_cuts": state.cuts,
        "unresolved_reaches": state.unresolved_reaches,
        "dataflow_scope": {
            "seed_model": "unresolvable_uses_in_source_function_are_attacker_controlled",
            "binding": "resolution_callsites_actual_to_parameter",
            "rounds": rounds,
            "functions_analyzed": len(state.funcs),
        },
        "dataflow_omissions": sorted(state.omissions),
    }
