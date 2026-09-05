"""Snapshot-bound observation contracts for the Mini-SWE runtime seam.

Snapshot capture is independent of GroundTruth's installed wheel; test outcome
classification reuses its protocol classifier and abstains when unavailable.
This module records what the harness itself can observe: a repository snapshot at
an action boundary, one multi-file transaction for one selected action, and
the exact bytes returned by an executed build or test.  Semantic analyzers may
consume these records, but cannot weaken their byte identity.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .repository_identity import canonical_repository_bytes

_SCHEMA = "gt.runtime_observation.v1"
_SKIP_DIRS = frozenset({
    ".git", ".gt", ".gt-state", ".groundtruth", ".hg", ".svn", ".venv", "venv",
    "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "build", "dist", "target", "vendor",
})
_MAX_CAPTURE_BYTES = 1_000_000
_TEST_RE = re.compile(
    r"(?i)(?:^|[;&|]\s*)(?:python\s+-m\s+pytest|pytest|tox|nox|"
    r"npm\s+(?:run\s+)?test|pnpm\s+(?:run\s+)?test|yarn\s+test|"
    r"python\s+-m\s+unittest|go\s+test|cargo\s+test|dotnet\s+test|"
    r"mvn(?:w)?\s+test|gradle(?:w)?\s+test|make\s+(?:test|check)|"
    r"bazel\s+test|meson\s+test|ctest|rspec|phpunit)\b"
)
_BUILD_RE = re.compile(
    r"(?i)(?:^|[;&|]\s*)(?:npm\s+(?:run\s+)?build|pnpm\s+(?:run\s+)?build|"
    r"yarn\s+build|cargo\s+build|go\s+build|dotnet\s+build|"
    r"mvn(?:w)?\s+(?:package|compile)|gradle(?:w)?\s+(?:build|assemble)|"
    r"bazel\s+build|meson\s+compile|make(?:\s|$)|cmake\s+--build|"
    r"python\s+-m\s+build)\b"
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


@dataclass(frozen=True)
class FileState:
    path: str
    kind: str
    sha256: str
    size: int
    captured: bytes | None = None

    def mapping(self, *, include_content: bool = False) -> dict[str, object]:
        row: dict[str, object] = {
            "path": self.path,
            "kind": self.kind,
            "sha256": self.sha256,
            "size": self.size,
        }
        if include_content:
            row["content_hex"] = self.captured.hex() if self.captured is not None else None
        return row


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: str
    revision: str
    files: tuple[FileState, ...]
    complete: bool
    omissions: tuple[str, ...]

    def canonical_bytes(self) -> bytes:
        return _canonical({
            "schema": _SCHEMA,
            "kind": "repository_snapshot",
            "root_sha256": hashlib.sha256(self.root.encode("utf-8")).hexdigest(),
            "revision": self.revision,
            "complete": self.complete,
            "omissions": list(self.omissions),
            "files": [item.mapping() for item in self.files],
        })


@dataclass(frozen=True)
class FileChange:
    path: str
    operation: str
    before_sha256: str | None
    after_sha256: str | None
    before: bytes | None
    after: bytes | None

    def mapping(self) -> dict[str, object]:
        return {
            "path": self.path,
            "operation": self.operation,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "before_content_hex": self.before.hex() if self.before is not None else None,
            "after_content_hex": self.after.hex() if self.after is not None else None,
        }


@dataclass(frozen=True)
class EditTransaction:
    action_id: int
    command_sha256: str
    pre_revision: str
    post_revision: str
    changes: tuple[FileChange, ...]
    complete: bool
    omissions: tuple[str, ...]
    transaction_sha256: str

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return tuple(change.path for change in self.changes)

    def canonical_bytes(self, *, include_transaction_hash: bool = True) -> bytes:
        row: dict[str, object] = {
            "schema": _SCHEMA,
            "kind": "edit_transaction",
            "action_id": self.action_id,
            "command_sha256": self.command_sha256,
            "pre_revision": self.pre_revision,
            "post_revision": self.post_revision,
            "complete": self.complete,
            "omissions": list(self.omissions),
            "changes": [change.mapping() for change in self.changes],
        }
        if include_transaction_hash:
            row["transaction_sha256"] = self.transaction_sha256
        return _canonical(row)


@dataclass(frozen=True)
class ExecutionEvidence:
    action_id: int
    kind: str
    protocol: str
    outcome: str
    command_sha256: str
    returncode: int | None
    repository_revision: str
    raw_output: bytes

    @property
    def raw_output_sha256(self) -> str:
        return hashlib.sha256(self.raw_output).hexdigest()

    def canonical_bytes(self) -> bytes:
        return _canonical({
            "schema": _SCHEMA,
            "kind": self.kind,
            "protocol": self.protocol,
            "outcome": self.outcome,
            "action_id": self.action_id,
            "command_sha256": self.command_sha256,
            "returncode": self.returncode,
            "repository_revision": self.repository_revision,
            "raw_output_sha256": self.raw_output_sha256,
            "raw_output_bytes": len(self.raw_output),
            "raw_preserved": True,
        })


def capture_workspace(
    root: str | Path, *, excluded_roots: tuple[str | Path, ...] = ()
) -> WorkspaceSnapshot:
    """Capture a deterministic, content-addressed repository snapshot.

    Every readable file contributes its complete hash. Files up to one MiB are
    retained as transaction witnesses; larger files remain hash-addressed and
    make a before/after transaction incomplete rather than silently truncated.
    """
    resolved = Path(root).resolve()
    excluded = tuple(Path(path).resolve() for path in excluded_roots)
    files: list[FileState] = []
    omissions: list[str] = []
    if not resolved.is_dir():
        omissions.append("repository_root_missing")
    else:
        git_paths: tuple[Path, ...] | None = None
        try:
            top = subprocess.run(
                ["git", "-C", str(resolved), "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
            if Path(top.stdout.strip()).resolve() == resolved:
                listed = subprocess.run(
                    [
                        "git", "-C", str(resolved), "ls-files", "-z",
                        "--cached", "--others", "--exclude-standard",
                    ],
                    check=True,
                    capture_output=True,
                    timeout=8,
                )
                git_paths = tuple(
                    resolved / value
                    for value in listed.stdout.decode(
                        "utf-8", "surrogateescape"
                    ).split("\0")
                    if value and os.path.lexists(resolved / value)
                )
        except (OSError, subprocess.SubprocessError):
            git_paths = None

        if git_paths is None:
            candidates: list[Path] = []
            for dirpath, dirnames, filenames in os.walk(
                resolved, followlinks=False
            ):
                base = Path(dirpath)
                dirnames[:] = sorted(
                    name for name in dirnames
                    if name not in _SKIP_DIRS
                    and not any(
                        (base / name).resolve() == target
                        or target in (base / name).resolve().parents
                        for target in excluded
                    )
                )
                candidates.extend(base / name for name in sorted(filenames))
            paths = tuple(candidates)
        else:
            paths = git_paths

        for path in paths:
            if any(path.resolve() == target or target in path.resolve().parents
                   for target in excluded):
                continue
            try:
                relative = path.relative_to(resolved).as_posix()
                if path.is_symlink():
                    payload = os.readlink(path).encode("utf-8", "surrogatepass")
                    kind = "symlink"
                else:
                    payload = path.read_bytes()
                    kind = "file"
                identity_payload = (
                    canonical_repository_bytes(payload)
                    if kind == "file"
                    else payload
                )
                files.append(FileState(
                    path=relative,
                    kind=kind,
                    sha256=hashlib.sha256(identity_payload).hexdigest(),
                    size=len(identity_payload),
                    captured=payload if len(payload) <= _MAX_CAPTURE_BYTES else None,
                ))
            except OSError:
                omissions.append(f"unreadable:{path.name}")
    identity = _canonical([item.mapping() for item in files])
    revision = hashlib.sha256(b"gt.workspace.v1\0" + identity).hexdigest()
    return WorkspaceSnapshot(
        root=str(resolved),
        revision=revision,
        files=tuple(files),
        complete=not omissions,
        omissions=tuple(sorted(omissions)),
    )


def diff_workspace(
    before: WorkspaceSnapshot,
    after: WorkspaceSnapshot,
    *,
    action_id: int,
    command: str,
) -> EditTransaction:
    """Compile all changes caused within one selected action into one record."""
    old = {item.path: item for item in before.files}
    new = {item.path: item for item in after.files}
    changes: list[FileChange] = []
    omissions = [*before.omissions, *after.omissions]
    for path in sorted(set(old) | set(new)):
        left, right = old.get(path), new.get(path)
        if left is not None and right is not None and left.sha256 == right.sha256:
            continue
        operation = "create" if left is None else "delete" if right is None else "modify"
        if left is not None and left.captured is None:
            omissions.append(f"before_content_too_large:{path}")
        if right is not None and right.captured is None:
            omissions.append(f"after_content_too_large:{path}")
        changes.append(FileChange(
            path=path,
            operation=operation,
            before_sha256=left.sha256 if left else None,
            after_sha256=right.sha256 if right else None,
            before=left.captured if left else None,
            after=right.captured if right else None,
        ))
    base = EditTransaction(
        action_id=action_id,
        command_sha256=hashlib.sha256(command.encode("utf-8")).hexdigest(),
        pre_revision=before.revision,
        post_revision=after.revision,
        changes=tuple(changes),
        complete=before.complete and after.complete and not omissions,
        omissions=tuple(sorted(set(omissions))),
        transaction_sha256="",
    )
    digest = hashlib.sha256(base.canonical_bytes(include_transaction_hash=False)).hexdigest()
    return EditTransaction(**{**base.__dict__, "transaction_sha256": digest})


def _protocol(command: str) -> str:
    lowered = command.lower()
    protocols = (
        "pytest", "tox", "nox", "npm", "pnpm", "yarn", "cargo", "go",
        "dotnet", "mvn", "gradle", "ctest", "rspec", "phpunit", "make",
        "cmake",
    )
    for name in protocols:
        if re.search(rf"(?:^|[\s;&|]){re.escape(name)}(?:[\s;&|]|$)", lowered):
            return name
    return "unknown"


def compile_execution_evidence(
    *,
    command: str,
    output: str,
    returncode: int | None,
    action_id: int,
    repository_revision: str,
) -> ExecutionEvidence | None:
    kind = "test" if _TEST_RE.search(command) else "build" if _BUILD_RE.search(command) else ""
    if not kind:
        return None
    outcome = classify_execution_outcome(command, output, returncode, kind=kind)
    return ExecutionEvidence(
        action_id=action_id,
        kind=kind,
        protocol=_protocol(command),
        outcome=outcome,
        command_sha256=hashlib.sha256(command.encode("utf-8")).hexdigest(),
        returncode=returncode,
        repository_revision=repository_revision,
        raw_output=output.encode("utf-8"),
    )


def classify_execution_outcome(command: str, output: str, returncode: int | None,
                               *, kind: str = "test") -> str:
    """Conservative outcome shared by context and verification consumers.

    A shell's aggregate exit cannot attribute a pipeline/compound result to one
    check. Do not rewrite commands or guess segment status from their output.
    Protocol parsing uses the certified producer; missing parsing means unknown.
    """
    if returncode is None:
        return "unknown"
    if returncode < 0:
        return "interrupted"
    if returncode == 124:
        return "timeout"
    if re.search(r"[;&|`\n]|\$\(", command):
        return "unknown"
    if kind == "build":
        return "pass" if returncode == 0 else "fail"
    try:
        from groundtruth.runtime.patterns import classify_test_observation
    except ImportError:
        return "unknown"
    outcome, _ = classify_test_observation(command, output, returncode)
    return outcome or "unknown"


def compile_transaction_artifacts(
    transaction: EditTransaction,
    *,
    graph_db: str | Path | None = None,
) -> dict[str, object]:
    """Attach deterministic syntax, patch, and pre-edit caller facts.

    Caller rows are explicitly graph-recorded, not claimed complete. If the
    graph is unavailable or stale the caller artifact is omitted rather than
    approximated from text.
    """
    def python_signatures(data: bytes | None, path: str) -> dict[str, str] | None:
        if data is None:
            return {}
        try:
            tree = ast.parse(data.decode("utf-8"), filename=path)
        except (SyntaxError, UnicodeDecodeError):
            return None
        found: dict[str, str] = {}

        class SignatureVisitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.scope: list[str] = []

            def _visit_function(
                self, node: ast.FunctionDef | ast.AsyncFunctionDef
            ) -> None:
                qualified = ".".join((*self.scope, node.name))
                signature = "|".join((
                    type(node).__name__,
                    ast.dump(node.args, annotate_fields=True, include_attributes=False),
                    ast.dump(
                        node.returns,
                        annotate_fields=True,
                        include_attributes=False,
                    ) if node.returns is not None else "",
                    str(node.type_comment or ""),
                ))
                found[qualified] = signature
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._visit_function(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self._visit_function(node)

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

        SignatureVisitor().visit(tree)
        return found

    patches: list[dict[str, object]] = []
    syntax: list[dict[str, object]] = []
    signatures: list[dict[str, object]] = []
    parser_rows: dict[str, dict] = {}
    try:
        from .parser_inspection import ParserInspectionRequest, inspect_sources

        inspection_requests = []
        for change in transaction.changes:
            for side, content in (("before", change.before), ("after", change.after)):
                if content is not None and Path(change.path).suffix.lower() in {
                    ".py", ".pyi", ".go", ".ts", ".tsx", ".js", ".jsx", ".rs",
                }:
                    inspection_requests.append(ParserInspectionRequest(
                        f"{transaction.transaction_sha256}:{side}:{change.path}",
                        change.path, content,
                    ))
        parser_rows = {
            str(row["request_id"]): row for row in inspect_sources(inspection_requests)
        }
    except (OSError, RuntimeError, subprocess.SubprocessError):
        parser_rows = {}
    for change in transaction.changes:
        before_text = (
            change.before.decode("utf-8", "replace").splitlines(keepends=True)
            if change.before is not None else []
        )
        after_text = (
            change.after.decode("utf-8", "replace").splitlines(keepends=True)
            if change.after is not None else []
        )
        text_roundtrip = (
            (change.before is None or "".join(before_text).encode("utf-8") == change.before)
            and (change.after is None or "".join(after_text).encode("utf-8") == change.after)
        )
        patch = (
            "".join(difflib.unified_diff(
                before_text, after_text,
                fromfile=f"a/{change.path}", tofile=f"b/{change.path}",
            ))
            if text_roundtrip else ""
        )
        patches.append({
            "path": change.path,
            "operation": change.operation,
            "representation": "unified_diff_utf8" if text_roundtrip else "full_postimage",
            "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
            "patch": patch,
            "before_sha256": change.before_sha256,
            "after_sha256": change.after_sha256,
            # Exact reconstruction authority for text, binary, create, and delete.
            "postimage_hex": change.after.hex() if change.after is not None else None,
            "truncated": False,
        })
        if change.after is None:
            syntax.append({
                "path": change.path,
                "language": "unknown",
                "status": "not_applicable_deleted",
                "post_revision": transaction.post_revision,
            })
        else:
            after_key = f"{transaction.transaction_sha256}:after:{change.path}"
            parsed_after = parser_rows.get(after_key)
            if parsed_after is not None:
                syntax.append({
                    "path": change.path,
                    "language": str(parsed_after.get("language") or "unknown"),
                    "status": "exact" if parsed_after.get("complete") else "incomplete",
                    "valid": bool(parsed_after.get("complete")),
                    "diagnostics": list(parsed_after.get("diagnostics") or ()),
                    "post_revision": transaction.post_revision,
                    "producer": str(parsed_after.get("parser_identity") or ""),
                    "content_sha256": str(parsed_after.get("content_sha256") or ""),
                })
            elif change.path.endswith((".py", ".pyi")):
                try:
                    ast.parse(change.after.decode("utf-8"), filename=change.path)
                    syntax.append({
                        "path": change.path,
                        "language": "python",
                        "status": "exact",
                        "valid": True,
                        "post_revision": transaction.post_revision,
                        "producer": "python.ast.parse",
                    })
                except (SyntaxError, UnicodeDecodeError) as exc:
                    syntax.append({
                        "path": change.path,
                        "language": "python",
                        "status": "exact",
                        "valid": False,
                        "line": int(getattr(exc, "lineno", 0) or 0),
                        "column": int(getattr(exc, "offset", 0) or 0),
                        "error": type(exc).__name__,
                        "post_revision": transaction.post_revision,
                        "producer": "python.ast.parse",
                    })
            else:
                syntax.append({
                    "path": change.path,
                    "language": "unknown",
                    "status": "unsupported",
                    "reason": "no_harness_certified_postimage_parser",
                    "post_revision": transaction.post_revision,
                })
        before_key = f"{transaction.transaction_sha256}:before:{change.path}"
        after_key = f"{transaction.transaction_sha256}:after:{change.path}"
        parsed_before, parsed_after = parser_rows.get(before_key), parser_rows.get(after_key)
        parser_side_available = (
            (parsed_before is not None or change.before is None)
            and (parsed_after is not None or change.after is None)
            and (parsed_before is not None or parsed_after is not None)
        )
        if parser_side_available:
            parser_complete = (
                (parsed_before is None or bool(parsed_before.get("complete")))
                and (parsed_after is None or bool(parsed_after.get("complete")))
            )
            if not parser_complete:
                signatures.append({"path": change.path, "status": "unavailable_invalid_syntax",
                                   "post_revision": transaction.post_revision})
            else:
                def signature_map(row: dict | None) -> tuple[dict[str, str], bool]:
                    found: dict[str, str] = {}
                    ambiguous = False
                    for item in (row or {}).get("declarations") or ():
                        name = str(
                            item.get("qualified_name") or item.get("name") or ""
                        )
                        signature = str(item.get("signature") or "")
                        if not name or name in found:
                            ambiguous = True
                        else:
                            found[name] = signature
                    return found, ambiguous

                before_signatures, before_ambiguous = signature_map(parsed_before)
                after_signatures, after_ambiguous = signature_map(parsed_after)
                if before_ambiguous or after_ambiguous:
                    signatures.append({
                        "path": change.path,
                        "status": "unavailable_ambiguous_declaration_identity",
                        "post_revision": transaction.post_revision,
                    })
                    continue
                before_names, after_names = set(before_signatures), set(after_signatures)
                signatures.append({
                    "path": change.path, "status": "exact",
                    "added": sorted(after_names - before_names),
                    "removed": sorted(before_names - after_names),
                    "changed": sorted(name for name in before_names & after_names
                                      if before_signatures[name] != after_signatures[name]),
                    "post_revision": transaction.post_revision,
                    "producer": str(
                        (parsed_after or parsed_before or {}).get("parser_identity") or ""
                    ),
                })
        elif change.path.endswith((".py", ".pyi")):
            before_signatures = python_signatures(change.before, change.path)
            after_signatures = python_signatures(change.after, change.path)
            if before_signatures is None or after_signatures is None:
                signatures.append({
                    "path": change.path,
                    "status": "unavailable_invalid_syntax",
                    "post_revision": transaction.post_revision,
                })
            else:
                before_names = set(before_signatures)
                after_names = set(after_signatures)
                signatures.append({
                    "path": change.path,
                    "status": "exact",
                    "added": sorted(after_names - before_names),
                    "removed": sorted(before_names - after_names),
                    "changed": sorted(
                        name for name in before_names & after_names
                        if before_signatures[name] != after_signatures[name]
                    ),
                    "post_revision": transaction.post_revision,
                    "producer": "python.ast",
                })
        else:
            signatures.append({
                "path": change.path,
                "status": "unsupported",
                "reason": "no_harness_certified_signature_extractor",
                "post_revision": transaction.post_revision,
            })
    callers: list[dict[str, object]] = []
    graph = Path(graph_db) if graph_db else None
    if graph is not None and graph.is_file():
        try:
            paths = transaction.changed_paths
            placeholders = ",".join("?" for _ in paths)
            query = (
                "SELECT DISTINCT src.name,src.file_path,e.source_line,tgt.name,"
                "tgt.file_path FROM edges e "
                "JOIN nodes src ON src.id=e.source_id "
                "JOIN nodes tgt ON tgt.id=e.target_id "
                f"WHERE e.type='CALLS' AND tgt.file_path IN ({placeholders}) "
                "ORDER BY src.file_path,e.source_line,src.name LIMIT 200"
            )
            connection = sqlite3.connect(
                f"file:{graph.resolve().as_posix()}?mode=ro", uri=True
            )
            try:
                rows = connection.execute(query, paths).fetchall()
            finally:
                connection.close()
            callers = [{
                "caller": str(row[0] or ""),
                "caller_path": str(row[1] or ""),
                "caller_line": int(row[2] or 0),
                "target": str(row[3] or ""),
                "target_path": str(row[4] or ""),
                "semantics": "graph_recorded",
            } for row in rows]
        except sqlite3.Error:
            callers = []
    return {
        "schema": "gt.transaction_artifacts.v1",
        "transaction_sha256": transaction.transaction_sha256,
        "pre_revision": transaction.pre_revision,
        "post_revision": transaction.post_revision,
        "syntax": syntax,
        "signatures": signatures,
        "patches": patches,
        "callers": callers,
        "caller_coverage": "graph_recorded" if graph is not None else "unavailable",
    }


def certify_observation_equivalence(
    *, raw_output: bytes, final_observation: bytes, expected_observation: bytes,
    sentinel: bytes,
) -> dict[str, object]:
    """Certify narrow byte equivalence and prove a raw sentinel did not leak."""
    equivalent = final_observation == expected_observation
    sentinel_absent = bool(sentinel) and sentinel not in final_observation
    return {
        "schema": "gt.observation_equivalence.v1",
        "raw_output_sha256": hashlib.sha256(raw_output).hexdigest(),
        "final_observation_sha256": hashlib.sha256(final_observation).hexdigest(),
        "expected_observation_sha256": hashlib.sha256(expected_observation).hexdigest(),
        "byte_equivalent": equivalent,
        "sentinel_absent": sentinel_absent,
        "replacement_certified": equivalent and sentinel_absent,
    }


__all__ = [
    "EditTransaction", "ExecutionEvidence", "FileChange", "FileState",
    "WorkspaceSnapshot", "capture_workspace", "compile_execution_evidence",
    "certify_observation_equivalence", "compile_transaction_artifacts",
    "diff_workspace",
]
