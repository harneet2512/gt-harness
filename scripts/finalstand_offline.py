"""Provider-free Phase II certification and validation harness.

This module is designed for GitHub Actions and Codespaces.  It performs no
network access and never calls a model provider.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import os
import re
import shlex
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FINALSTAND = ROOT / "gt_finalstand"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
READ_COMMANDS = {
    "cat", "find", "git", "head", "ls", "pwd", "rg", "sed", "tail", "wc",
}
WRITE_COMMANDS = {
    "apply_patch", "cp", "mv", "rm", "touch", "truncate",
}
SHELL_OPERATORS = {"&&", "||", ";", "|", ">", ">>", "<"}
TYPED_KINDS = {
    "exact_literal_search", "definition", "references", "callers", "syntax",
    "patch_impact", "verification_status",
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def build_pre_artifact_provenance(
    *,
    offline_receipt_bytes: bytes,
    compatibility: dict[str, Any],
    gt_index_bytes: bytes,
    workflow_bytes: bytes,
    groundtruth_commit: str,
) -> dict[str, Any]:
    """Bind every FS-023 input available before the Actions artifact exists.

    The successful run and uploaded-artifact identities are necessarily absent
    while the workflow is still executing.  This receipt makes that bootstrap
    state explicit; post-run finalization must replace it with API-bound
    provenance before FS-023 can become COMPLETE.
    """
    offline = json.loads(offline_receipt_bytes)
    graph = offline.get("native_graph_battery")
    semantic_hash = (
        graph.get("semantic_artifact_sha256") if isinstance(graph, dict) else None
    )
    source_hash = compatibility.get("source_manifest_sha256")
    if offline.get("terminal") is not True:
        raise ValueError("FS-023 provenance requires a terminal offline receipt")
    if re.fullmatch(r"[0-9a-f]{40}", groundtruth_commit) is None:
        raise ValueError("GroundTruth commit must be an immutable lowercase 40-hex SHA")
    for name, value in (
        ("source manifest", source_hash),
        ("semantic artifact", semantic_hash),
    ):
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError(f"{name} SHA-256 is missing or invalid")
    return {
        "schema": "gt.fs023.provenance.v1",
        "offline_receipt": "receipts/offline_suite.json",
        "offline_receipt_sha256": hashlib.sha256(offline_receipt_bytes).hexdigest(),
        "recorded_groundtruth_commit": groundtruth_commit,
        "binary_sha256": hashlib.sha256(gt_index_bytes).hexdigest(),
        "source_manifest_sha256": source_hash,
        "semantic_artifact_sha256": semantic_hash,
        "workflow_definition": ".github/workflows/gt_finalstand_provider_free.yml",
        "workflow_definition_sha256": hashlib.sha256(workflow_bytes).hexdigest(),
        "workflow_execution_identity_bound": False,
        "missing_immutable_linkage": [
            "harness_execution_commit",
            "github_actions_run_id",
            "github_actions_run_url",
            "uploaded_artifact_bundle_sha256",
        ],
        "scope": (
            "Pre-artifact GitHub Actions receipt binding every immutable identity "
            "available inside the running workflow. It does not claim the run or "
            "artifact succeeded; post-run API binding remains mandatory."
        ),
    }


def classify_shell(command: str) -> str:
    """Classify observable shell structure without guessing semantic intent."""
    if not isinstance(command, str) or not command.strip():
        return "opaque"
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return "opaque"
    operators = [token for token in tokens if token in SHELL_OPERATORS]
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in SHELL_OPERATORS:
            segments.append([])
        else:
            segments[-1].append(token)
    heads = {segment[0] for segment in segments if segment}
    if not heads:
        return "opaque"
    has_read = bool(heads & READ_COMMANDS)
    has_write = bool(heads & WRITE_COMMANDS) or any(op in {">", ">>"} for op in operators)
    if has_read and has_write:
        return "mixed_read_write"
    if operators or len(segments) > 1:
        return "compound"
    if heads <= READ_COMMANDS:
        return "simple_read"
    if heads <= WRITE_COMMANDS:
        return "simple_write"
    return "opaque"


def classify_current_regex(command: str) -> str:
    """Approximate the legacy leading-command classifier for comparison only."""
    match = re.match(r"^\s*([A-Za-z0-9_.-]+)", command or "")
    if not match:
        return "opaque"
    head = match.group(1)
    if head in READ_COMMANDS:
        return "simple_read"
    if head in WRITE_COMMANDS:
        return "simple_write"
    return "opaque"


def classify_typed_action(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "invalid_typed"
    kind = payload.get("kind")
    arguments = payload.get("arguments")
    if kind in TYPED_KINDS and isinstance(arguments, dict):
        return "typed"
    return "invalid_typed"


def validate_language_manifest(
    manifest: dict[str, Any],
    certification_rows: list[dict[str, str]],
    compatibility: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema") != "gt.language_manifest.v1":
        errors.append("language manifest schema mismatch")
    languages = manifest.get("languages")
    if not isinstance(languages, list) or len(languages) != 30:
        errors.append("language manifest must contain exactly 30 languages")
        return errors
    names = [row.get("name") for row in languages if isinstance(row, dict)]
    if len(names) != 30 or len(set(names)) != 30 or names != sorted(names):
        errors.append("language names must be unique and canonically sorted")
    extensions: list[str] = []
    bool_fields = (
        "definitions", "calls", "imports", "bodies", "parameters",
        "return_types", "test_patterns",
    )
    for row in languages:
        if not isinstance(row, dict):
            errors.append("language entries must be objects")
            continue
        exts = row.get("extensions")
        if not isinstance(exts, list) or not exts or exts != sorted(exts):
            errors.append(f"extensions invalid for {row.get('name')}")
        else:
            extensions.extend(exts)
        for field in bool_fields:
            if type(row.get(field)) is not bool:
                errors.append(f"{field} must be boolean for {row.get('name')}")
    if len(extensions) != len(set(extensions)):
        errors.append("language extensions overlap")
    certified_names = {row["registry_identity"] for row in certification_rows}
    if set(names) != certified_names:
        errors.append("Go manifest languages differ from certification matrix")
    pairs = {
        (row["registry_identity"], row["operation"]) for row in certification_rows
    }
    if len(pairs) != 210:
        errors.append("certification matrix must contain 210 unique pairs")
    if compatibility is not None:
        manifest_hash = hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        if compatibility.get("schema") != "gt.language_operation_compatibility.v1":
            errors.append("language-operation compatibility schema mismatch")
        if compatibility.get("source_manifest_sha256") != manifest_hash:
            errors.append("live Go manifest hash differs from compatibility authority")
    return errors


def scan_forbidden(
    rules: dict[str, Any], repository_roots: dict[str, Path]
) -> dict[str, Any]:
    def module_path(module: str) -> tuple[str, Path] | None:
        candidates: list[tuple[str, Path]] = []
        if module == "gt_engine" or module.startswith("gt_engine."):
            candidates.append(("harness", repository_roots["harness"] / Path(*module.split("."))))
        elif module == "scripts" or module.startswith("scripts."):
            candidates.append(("harness", repository_roots["harness"] / Path(*module.split("."))))
        elif module == "groundtruth" or module.startswith("groundtruth."):
            candidates.append(
                (
                    "groundtruth",
                    repository_roots["groundtruth"] / "src" / Path(*module.split(".")),
                )
            )
        for root_name, base in candidates:
            file_path = base.with_suffix(".py")
            if file_path.is_file():
                return root_name, file_path
            package_path = base / "__init__.py"
            if package_path.is_file():
                return root_name, package_path
        return None

    reachable: dict[Path, str] = {}
    import_chains: dict[Path, tuple[str, ...]] = {}
    queue: list[tuple[str, Path, tuple[str, ...]]] = []
    for entrypoint in rules.get("entrypoints", []):
        root_name, relative = entrypoint.split(":", 1)
        root = repository_roots.get(root_name)
        if root is not None:
            queue.append((root_name, (root / relative).resolve(), (entrypoint,)))
    while queue:
        root_name, path, chain = queue.pop()
        if path in reachable or not path.is_file():
            continue
        root = repository_roots[root_name]
        reachable[path] = f"{root_name}:{path.relative_to(root).as_posix()}"
        import_chains[path] = chain
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        if root_name == "groundtruth":
            module_relative = path.relative_to(root / "src")
        else:
            module_relative = path.relative_to(root)
        module_parts = list(module_relative.with_suffix("").parts)
        is_package = module_parts[-1] == "__init__"
        if is_package:
            module_parts.pop()
        package_parts = module_parts if is_package else module_parts[:-1]
        def import_nodes(
            *, root_name: str = root_name, tree: ast.AST = tree
        ) -> list[ast.AST]:
            if root_name == "harness":
                return list(ast.walk(tree))
            result: list[ast.AST] = []
            stack = list(tree.body)
            while stack:
                node = stack.pop()
                result.append(node)
                if isinstance(
                    node,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
                ):
                    continue
                stack.extend(ast.iter_child_nodes(node))
            return result

        modules: set[str] = set()
        for node in import_nodes():
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    keep = max(0, len(package_parts) - node.level + 1)
                    base = package_parts[:keep]
                    if node.module:
                        base.extend(node.module.split("."))
                    imported = ".".join(base)
                else:
                    imported = node.module or ""
                if imported:
                    modules.add(imported)
                    for alias in node.names:
                        modules.add(f"{imported}.{alias.name}")
        for module in sorted(modules):
            resolved = module_path(module)
            if resolved is not None and resolved[1] not in reachable:
                next_label = (
                    f"{resolved[0]}:"
                    f"{resolved[1].relative_to(repository_roots[resolved[0]]).as_posix()}"
                )
                queue.append((resolved[0], resolved[1], (*chain, next_label)))

    findings: list[dict[str, Any]] = []
    for rule in rules.get("rules", []):
        pattern = re.compile(rule["pattern"], re.I)
        if rule.get("scope") == "reachable":
            targets = [
                (target, path) for path, target in sorted(reachable.items(), key=lambda x: x[1])
            ]
        else:
            targets = []
            for target in rule.get("targets", []):
                root_name, relative = target.split(":", 1)
                root = repository_roots.get(root_name)
                targets.append((target, root / relative if root else None))
        for target, path in targets:
            if path is None or not path.is_file():
                findings.append(
                    {"rule": rule["id"], "target": target, "kind": "missing_target"}
                )
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            symbols: list[tuple[str, int, str]] = []
            try:
                tree = ast.parse(text, filename=str(path))
            except SyntaxError:
                tree = None
            if tree is not None:
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        symbols.append((node.name, node.lineno, "definition"))
                    elif isinstance(node, ast.Name):
                        symbols.append((node.id, node.lineno, "runtime_reference"))
                    elif isinstance(node, ast.Attribute):
                        symbols.append((node.attr, node.lineno, "runtime_reference"))
                    elif isinstance(node, (ast.Import, ast.ImportFrom)):
                        for alias in node.names:
                            symbols.append((alias.name, node.lineno, "registration_import"))
            else:
                for line_number, line in enumerate(text.splitlines(), 1):
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        symbols.append((stripped, line_number, "text_fallback"))
            seen: set[tuple[int, str]] = set()
            for symbol, line_number, kind in symbols:
                if pattern.search(symbol) and (line_number, kind) not in seen:
                    seen.add((line_number, kind))
                    findings.append(
                        {
                            "rule": rule["id"],
                            "target": target,
                            "line": line_number,
                            "kind": kind,
                            "import_chain": list(import_chains.get(path, (target,))),
                        }
                    )
    return {
        "schema": "gt.finalstand.forbidden_scan.v1",
        "ok": not findings,
        "rules": len(rules.get("rules", [])),
        "reachable_files": len(reachable),
        "findings": findings,
    }


def run_offline_cases(payload: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    counts: dict[str, int] = {}

    actions = payload.get("action_identifiability", [])
    counts["action_identifiability"] = len(actions)
    for index, case in enumerate(actions):
        ast_actual = classify_shell(case["command"])
        regex_actual = classify_current_regex(case["command"])
        if ast_actual != case["ast_expected"]:
            failures.append(
                f"action[{index}] AST expected {case['ast_expected']}, got {ast_actual}"
            )
        if regex_actual != case["regex_expected"]:
            failures.append(
                f"action[{index}] regex expected {case['regex_expected']}, got {regex_actual}"
            )

    typed_actions = payload.get("typed_action_identifiability", [])
    counts["typed_action_identifiability"] = len(typed_actions)
    for index, case in enumerate(typed_actions):
        actual = classify_typed_action(case["payload"])
        if actual != case["expected"]:
            failures.append(f"typed_action[{index}] expected {case['expected']}, got {actual}")

    evidence = payload.get("evidence_sufficiency", [])
    counts["evidence_sufficiency"] = len(evidence)
    required = {
        "schema", "semantics", "coverage", "ambiguity", "omissions",
        "producer", "freshness",
    }
    for index, case in enumerate(evidence):
        artifact = case["artifact"]
        missing = sorted(required - set(artifact))
        valid = not missing and not (
            artifact.get("semantics") == "exact" and (
            artifact.get("ambiguity") or artifact.get("omissions")
            )
        )
        if valid is not case["expected_valid"]:
            failures.append(
                f"evidence[{index}] validity mismatch; missing={missing}, artifact={artifact}"
            )

    freshness = payload.get("freshness", [])
    counts["freshness"] = len(freshness)
    for index, case in enumerate(freshness):
        eligible = case["artifact_revision"] == case["request_revision"]
        if eligible is not case["expected_eligible"]:
            failures.append(f"freshness[{index}] eligibility mismatch")

    leaks = payload.get("observation_leak", [])
    counts["observation_leak"] = len(leaks)
    for index, case in enumerate(leaks):
        leaked = case["sentinel"] in case["model_visible_observation"]
        should_be_absent = case["decision"] in {"REPLACE", "SUPPRESS"}
        if should_be_absent == leaked:
            failures.append(f"observation_leak[{index}] sentinel policy violated")

    determinism_inputs = payload.get("determinism", [])
    counts["determinism"] = len(determinism_inputs)
    for index, case in enumerate(determinism_inputs):
        hashes = {hashlib.sha256(canonical_bytes(case)).hexdigest() for _ in range(10)}
        if len(hashes) != 1:
            failures.append(f"determinism[{index}] canonical bytes drifted")

    cost_samples = payload.get("cost_samples", [])
    durations: list[int] = []
    for sample in cost_samples:
        started = time.perf_counter_ns()
        canonical_bytes(sample)
        durations.append(time.perf_counter_ns() - started)
    counts["cost_samples"] = len(cost_samples)
    sorted_cost = sorted(durations)
    p50 = statistics.median(sorted_cost) if sorted_cost else 0
    p95 = sorted_cost[max(0, int(len(sorted_cost) * 0.95) - 1)] if sorted_cost else 0

    return {
        "schema": "gt.finalstand.offline_suite.v1",
        "ok": not failures,
        "counts": counts,
        "failures": failures,
        "cost": {"canonical_json_p50_ns": p50, "canonical_json_p95_ns": p95},
        "cost_scope": "compiler canonicalization microbenchmark only",
    }


def _write_analyzer_fixture(root: Path) -> dict[str, bool]:
    """Write the fixed adversarial corpus used by the native index battery."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pkg").mkdir()
    (root / "generated").mkdir()
    (root / "pkg" / "helper.py").write_text(
        "def helper(value: int) -> int:\n    return value + 1\n",
        encoding="utf-8",
    )
    (root / "pkg" / "app.py").write_text(
        "from typing import overload\n"
        "from .helper import helper\n\n"
        "@overload\n"
        "def convert(value: int) -> str: ...\n"
        "@overload\n"
        "def convert(value: str) -> str: ...\n"
        "def convert(value):\n    return str(helper(value))\n\n"
        "def shadowed(helper):\n    return helper(1)\n\n"
        "def dynamic(name):\n    return __import__(name)\n",
        encoding="utf-8",
    )
    (root / "generated" / "client.py").write_text(
        "# generated fixture\ndef generated_call():\n    return 'generated'\n",
        encoding="utf-8",
    )
    (root / "main.go").write_text(
        "package fixture\n\nfunc Helper(v int) int { return v + 1 }\n"
        "func Caller() int { return Helper(1) }\n",
        encoding="utf-8",
    )
    (root / "dirty.txt").write_bytes(b"line-one\r\nline-two\r\n")
    symlink_supported = True
    try:
        os.symlink(root / "pkg" / "helper.py", root / "helper_link.py")
    except (OSError, NotImplementedError):
        symlink_supported = False
    return {
        "overloads": True,
        "shadowing": True,
        "dynamic_import": True,
        "generated_file": True,
        "multiple_languages": True,
        "dirty_non_source_file": True,
        "symlink": symlink_supported,
    }


def _semantic_graph_snapshot(db_path: Path) -> dict[str, Any]:
    """Return a canonical graph projection with documented volatile fields removed."""
    def normalize(value: Any) -> Any:
        if isinstance(value, bytes):
            return {"bytes_hex": value.hex()}
        return value

    connection = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        snapshot: dict[str, Any] = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = [
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({quoted})")
            ]
            kept = [column for column in columns if column != "indexed_at"]
            select = ",".join('"' + c.replace('"', '""') + '"' for c in kept)
            rows = [
                [normalize(value) for value in row]
                for row in connection.execute(f"SELECT {select} FROM {quoted}")
            ]
            if table == "project_meta" and kept == ["key", "value"]:
                rows = [
                    row for row in rows
                    if row[0] not in {"root", "build_time_utc"}
                ]
            snapshot[table] = {"columns": kept, "rows": sorted(rows, key=canonical_bytes)}
        return snapshot
    finally:
        connection.close()


def _run_native_graph_battery(gt_index_bin: Path) -> dict[str, Any]:
    failures: list[str] = []
    cold_ns: list[int] = []
    semantic_hashes: list[str] = []
    database_hashes: list[str] = []
    with tempfile.TemporaryDirectory(prefix="gt-fs023-") as temporary:
        root = Path(temporary) / "repository"
        coverage = _write_analyzer_fixture(root)
        latest_db: Path | None = None
        for run in range(10):
            db_path = Path(temporary) / f"cold-{run}.db"
            started = time.perf_counter_ns()
            process = subprocess.run(
                [
                    str(gt_index_bin), "-root", str(root), "-output", str(db_path),
                    "-workers", "1",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
            cold_ns.append(time.perf_counter_ns() - started)
            if process.returncode != 0 or not db_path.is_file():
                failures.append(
                    f"cold_graph_build[{run}] exit={process.returncode}: "
                    f"{process.stderr[-500:]}"
                )
                continue
            semantic = _semantic_graph_snapshot(db_path)
            semantic_hashes.append(hashlib.sha256(canonical_bytes(semantic)).hexdigest())
            database_hashes.append(hashlib.sha256(db_path.read_bytes()).hexdigest())
            latest_db = db_path

        incremental_ns = 0
        query_ns: list[int] = []
        if latest_db is not None:
            app = root / "pkg" / "app.py"
            app.write_text(
                app.read_text(encoding="utf-8") + "\ndef added():\n    return 7\n",
                encoding="utf-8",
            )
            before = time.perf_counter_ns()
            process = subprocess.run(
                [
                    str(gt_index_bin), "-root", str(root), "-output", str(latest_db),
                    "-file", "pkg/app.py", "-workers", "1",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
            incremental_ns = time.perf_counter_ns() - before
            if process.returncode != 0:
                failures.append(
                    f"incremental_graph_build exit={process.returncode}: {process.stderr[-500:]}"
                )
            connection = sqlite3.connect(
                f"file:{latest_db.resolve().as_posix()}?mode=ro", uri=True
            )
            try:
                added = connection.execute(
                    "SELECT COUNT(*) FROM nodes WHERE name='added' AND file_path='pkg/app.py'"
                ).fetchone()[0]
                if added != 1:
                    failures.append(f"incremental graph omitted added symbol: count={added}")
                for _ in range(25):
                    before = time.perf_counter_ns()
                    connection.execute(
                        "SELECT name, file_path FROM nodes WHERE name=? ORDER BY file_path",
                        ("helper",),
                    ).fetchall()
                    query_ns.append(time.perf_counter_ns() - before)
            finally:
                connection.close()

    if len(semantic_hashes) != 10:
        failures.append(f"completed {len(semantic_hashes)} of 10 cold graph builds")
    if len(set(semantic_hashes)) > 1:
        failures.append("cold graph semantic artifacts were not byte-identical")
    return {
        "executed": True,
        "ok": not failures,
        "failures": failures,
        "corpus_coverage": coverage,
        "cold_builds": len(semantic_hashes),
        "semantic_artifact_sha256": semantic_hashes[0] if semantic_hashes else "",
        "semantic_artifacts_byte_identical": len(semantic_hashes) == 10
        and len(set(semantic_hashes)) == 1,
        "database_files_byte_identical": len(database_hashes) == 10
        and len(set(database_hashes)) == 1,
        "cost": {
            "cold_build_p50_ns": statistics.median(cold_ns) if cold_ns else 0,
            "cold_build_p95_ns": sorted(cold_ns)[max(0, int(len(cold_ns) * 0.95) - 1)]
            if cold_ns else 0,
            "incremental_build_ns": incremental_ns,
            "query_p50_ns": statistics.median(query_ns) if query_ns else 0,
            "query_p95_ns": sorted(query_ns)[max(0, int(len(query_ns) * 0.95) - 1)]
            if query_ns else 0,
        },
        "cost_scope": "native gt-index tiny adversarial fixture; workflow runner input",
    }


def _run_runtime_probes() -> dict[str, Any]:
    """Exercise real provider-free Mini-SWE request and delivery boundaries."""
    from gt_engine.miniswe_typed_actions import (
        build_action_request,
        execute_typed_action,
        parse_groundtruth_toolcalls,
    )
    from gt_engine.runtime_observation import certify_observation_equivalence

    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="gt-fs023-runtime-") as temporary:
        root = Path(temporary)
        source = root / "sample.py"
        source.write_text("needle = 1\n", encoding="utf-8")
        action = {
            "tool_call_id": "fs023-exact",
            "gt_action": {
                "kind": "exact_literal_search",
                "arguments": {"literal": "needle", "paths": ["sample.py"]},
                "requested_fidelity": "exact",
            },
        }
        first_request = build_action_request(action, repo_root=root)
        first = execute_typed_action(first_request, repo_root=root)
        first_payload = json.loads(first["output"])
        if first["returncode"] != 0 or first_payload.get("direct_answer") is None:
            failures.append("real exact-literal typed action did not return complete evidence")
        source.write_text("needle = 2\n", encoding="utf-8")
        second_request = build_action_request(action, repo_root=root)
        second = execute_typed_action(second_request, repo_root=root)
        second_payload = json.loads(second["output"])
        first_snapshot = first_payload["action_request"]["repository_snapshot"]
        second_snapshot = second_payload["action_request"]["repository_snapshot"]
        if first_snapshot == second_snapshot:
            failures.append("repository mutation did not invalidate the request snapshot")
        if first_payload.get("direct_answer") == second_payload.get("direct_answer"):
            failures.append("repository mutation did not change exact-literal evidence")

    sentinel = b"RAW_SENTINEL_FS023"
    expected = b'{"typed":"answer"}'
    clean = certify_observation_equivalence(
        raw_output=b"diagnostic:" + sentinel,
        final_observation=expected,
        expected_observation=expected,
        sentinel=sentinel,
    )
    leaked = certify_observation_equivalence(
        raw_output=b"diagnostic:" + sentinel,
        final_observation=expected + sentinel,
        expected_observation=expected,
        sentinel=sentinel,
    )
    if not clean["replacement_certified"] or leaked["replacement_certified"]:
        failures.append("sentinel equivalence gate did not distinguish clean and leaked bytes")

    call = SimpleNamespace(
        id="stock-bash-1",
        function=SimpleNamespace(name="bash", arguments=json.dumps({"command": "rg needle"})),
    )
    parsed = parse_groundtruth_toolcalls([call], format_error_template="{{ error }}")
    expected_bash = [{"command": "rg needle", "tool_call_id": "stock-bash-1", "tool_name": "bash"}]
    if parsed != expected_bash:
        failures.append(f"stock Bash parse parity drifted: {parsed!r}")

    return {
        "ok": not failures,
        "failures": failures,
        "typed_exact_literal_executed": True,
        "freshness_invalidation_executed": True,
        "sentinel_clean_replacement_certified": clean["replacement_certified"],
        "sentinel_leak_rejected": not leaked["replacement_certified"],
        "stock_bash_parse_parity": parsed == expected_bash,
        "gt_off_parity_regression": "tests/test_miniswe_runtime.py::"
        "test_gt_off_never_attaches_terminal_or_provider_authorities",
        "provider_calls": 0,
    }


def run_provider_free_battery(
    payload: dict[str, Any], *, gt_index_bin: Path | None = None
) -> dict[str, Any]:
    static = run_offline_cases(payload)
    runtime = _run_runtime_probes()
    if gt_index_bin is None:
        graph = {
            "executed": False,
            "ok": False,
            "failures": ["native gt-index binary was not supplied"],
            "cold_builds": 0,
        }
    elif not gt_index_bin.is_file():
        graph = {
            "executed": False,
            "ok": False,
            "failures": [f"native gt-index binary does not exist: {gt_index_bin}"],
            "cold_builds": 0,
        }
    else:
        graph = _run_native_graph_battery(gt_index_bin.resolve())
    terminal = bool(static["ok"] and runtime["ok"] and graph["ok"])
    return {
        "schema": "gt.finalstand.offline_suite.v2",
        "ok": static["ok"] and runtime["ok"],
        "terminal": terminal,
        "static_cases": static,
        "runtime_probes": runtime,
        "native_graph_battery": graph,
        "limitations": [] if terminal else [
            "FS-023 is non-terminal until the native graph battery passes in GitHub Actions"
        ],
    }


def validate_runbooks(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    required = {
        "CLEAN_MACHINE_RUNBOOK.md": (
            "## Preconditions", "## External workflow", "## Acceptance receipt",
        ),
        "ROLLBACK_RUNBOOK.md": (
            "## Trigger", "## Procedure", "## Verification", "## Receipt",
        ),
    }
    secret_re = re.compile(r"(?:api[_-]?key|token|password)\s*[:=]\s*\S+", re.I)
    for path in paths:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if not text:
            errors.append(f"missing runbook: {path.name}")
            continue
        for heading in required.get(path.name, ()):
            if heading not in text:
                errors.append(f"{path.name} missing heading {heading}")
        if secret_re.search(text):
            errors.append(f"{path.name} contains credential-shaped assignment")
    return errors


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    language = sub.add_parser("language")
    language.add_argument("--manifest", type=Path, required=True)
    language.add_argument("--out", type=Path, required=True)
    forbidden = sub.add_parser("forbidden")
    forbidden.add_argument("--rules", type=Path, required=True)
    forbidden.add_argument("--groundtruth-root", type=Path, required=True)
    forbidden.add_argument("--out", type=Path, required=True)
    offline = sub.add_parser("offline")
    offline.add_argument("--cases", type=Path, required=True)
    offline.add_argument("--gt-index-bin", type=Path)
    offline.add_argument("--require-terminal", action="store_true")
    offline.add_argument("--out", type=Path, required=True)
    runbooks = sub.add_parser("runbooks")
    runbooks.add_argument("--out", type=Path, required=True)
    provenance = sub.add_parser("provenance")
    provenance.add_argument("--offline-receipt", type=Path, required=True)
    provenance.add_argument("--groundtruth-root", type=Path, required=True)
    provenance.add_argument("--gt-index-bin", type=Path, required=True)
    provenance.add_argument("--workflow", type=Path, required=True)
    provenance.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "language":
        manifest_bytes = args.manifest.read_bytes()
        errors = validate_language_manifest(
            json.loads(manifest_bytes),
            _read_csv(FINALSTAND / "language_operation_certification.csv"),
            json.loads(
                (FINALSTAND / "language_operation_compatibility.json").read_text(
                    encoding="utf-8"
                )
            ),
        )
        result = {
            "schema": "gt.finalstand.language_certification_receipt.v1",
            "ok": not errors,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "errors": errors,
        }
    elif args.command == "forbidden":
        result = scan_forbidden(
            json.loads(args.rules.read_text(encoding="utf-8")),
            {"harness": ROOT, "groundtruth": args.groundtruth_root.resolve()},
        )
    elif args.command == "offline":
        result = run_provider_free_battery(
            json.loads(args.cases.read_text(encoding="utf-8")),
            gt_index_bin=args.gt_index_bin,
        )
        if args.require_terminal and not result["terminal"]:
            result["ok"] = False
    elif args.command == "runbooks":
        errors = validate_runbooks(
            [FINALSTAND / "CLEAN_MACHINE_RUNBOOK.md", FINALSTAND / "ROLLBACK_RUNBOOK.md"]
        )
        result = {
            "schema": "gt.finalstand.runbook_validation.v1",
            "ok": not errors,
            "errors": errors,
        }
    else:
        groundtruth_commit = subprocess.check_output(
            ["git", "-C", str(args.groundtruth_root), "rev-parse", "HEAD"],
            text=True,
        ).strip()
        result = build_pre_artifact_provenance(
            offline_receipt_bytes=args.offline_receipt.read_bytes(),
            compatibility=json.loads(
                (FINALSTAND / "language_operation_compatibility.json").read_text(
                    encoding="utf-8"
                )
            ),
            gt_index_bytes=args.gt_index_bin.read_bytes(),
            workflow_bytes=args.workflow.read_bytes(),
            groundtruth_commit=groundtruth_commit,
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
