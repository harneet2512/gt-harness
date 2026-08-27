"""Deterministic, source-receipted semantic facts for decision context.

The repository graph already establishes symbol identity and structural
relationships.  This module adds a bounded semantic layer from exact source
bytes: local value flow, return flow, explicit shape assertions, and a small
set of versioned library contracts.  It never calls a model and never treats
model-authored source as new provider-visible evidence.
"""

from __future__ import annotations

import ast
import hashlib
import re
import textwrap
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from importlib.metadata import version
from typing import Any

from tree_sitter_language_pack import get_parser

from gt_engine.hybrid_retrieval import EvidenceOrigin, RepositoryDocument


class SemanticFactKind(StrEnum):
    VALUE_FLOW = "value_flow"
    RETURN_FLOW = "return_flow"
    CALL_ARGUMENT_FLOW = "call_argument_flow"
    SHAPE_CONSTRAINT = "shape_constraint"
    CONTROL_DEPENDENCY = "control_dependency"


class SemanticGraphStatus(StrEnum):
    READY = "READY"
    READY_WITH_DECLARED_LIMITATIONS = "READY_WITH_DECLARED_LIMITATIONS"
    ABSTAIN = "ABSTAIN"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class SemanticGraphFact:
    claim_id: str
    kind: SemanticFactKind
    path: str
    start_line: int
    end_line: int
    scope: str
    subject: str
    relation: str
    object: str
    evidence: str
    provenance: tuple[str, ...]
    source_revision: str
    diagnostic_relevant: bool = False
    library_model: str = ""

    @property
    def rendered(self) -> str:
        label = {
            SemanticFactKind.VALUE_FLOW: "Value flow",
            SemanticFactKind.RETURN_FLOW: "Return flow",
            SemanticFactKind.CALL_ARGUMENT_FLOW: "Argument flow",
            SemanticFactKind.SHAPE_CONSTRAINT: "Shape contract",
            SemanticFactKind.CONTROL_DEPENDENCY: "Control dependency",
        }[self.kind]
        suffix = f" [model={self.library_model}]" if self.library_model else ""
        return (
            f"- {label} {self.path}:{self.start_line} ({self.scope or '<module>'}): "
            f"{self.subject} {self.relation} {self.object}{suffix}"
        )

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["kind"] = self.kind.value
        row["rendered"] = self.rendered
        return row


@dataclass(frozen=True, slots=True)
class SemanticGraphReceipt:
    source_revision: str
    builder_version: str
    documents_attempted: int
    documents_indexed: int
    documents_failed: int
    facts_by_kind: dict[str, int] = field(default_factory=dict)
    duplicate_facts_removed: int = 0
    limitations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SemanticGraphProjection:
    status: SemanticGraphStatus
    facts: tuple[SemanticGraphFact, ...]
    receipt: SemanticGraphReceipt
    truncated_count: int = 0

    @property
    def rendered_text(self) -> str:
        if not self.facts:
            return ""
        return "\n".join(
            ("Deterministic semantic graph facts (exact source revision):",)
            + tuple(fact.rendered for fact in self.facts)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "facts": [fact.as_dict() for fact in self.facts],
            "receipt": self.receipt.as_dict(),
            "truncated_count": self.truncated_count,
        }


_REQUIRED_TREE_SITTER_VERSION = "0.25.2"
_TREE_SITTER_RUNTIME_VERSION = version("tree-sitter")
_BUILDER_VERSION = (
    f"cross-language-semantic-slice-v3:tree-sitter-{_TREE_SITTER_RUNTIME_VERSION}"
)
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TRACEBACK_FRAME = re.compile(
    r"File\s+[\"'](?P<path>[^\"']+)[\"'],\s+line\s+(?P<line>\d+)"
    r"(?:,\s+in\s+(?P<scope>[A-Za-z_][A-Za-z0-9_]*))?"
)


def _normalize_path(value: str) -> str:
    result = str(value or "").strip().replace("\\", "/")
    while result.startswith("./"):
        result = result[2:]
    return result


def _stable_id(*parts: object) -> str:
    material = "\x1f".join(str(part or "") for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _expr(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return type(node).__name__


def _names(node: ast.AST | None) -> tuple[str, ...]:
    if node is None:
        return ()
    values = {
        item.id
        for item in ast.walk(node)
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
    }
    return tuple(sorted(values))


def _targets(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(item for child in node.elts for item in _targets(child))
    if isinstance(node, ast.Attribute):
        return (_expr(node),)
    if isinstance(node, ast.Subscript):
        return (_expr(node),)
    return ()


def _call_name(node: ast.Call) -> str:
    return _expr(node.func)


def _parse_document(document: RepositoryDocument) -> ast.AST | None:
    text = str(document.text or "")
    for candidate in (text, textwrap.dedent(text)):
        try:
            return ast.parse(candidate)
        except (IndentationError, SyntaxError, ValueError):
            continue
    return None


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".")[0]] = item.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                aliases[item.asname or item.name] = f"{node.module}.{item.name}"
    return aliases


def _resolved_call(node: ast.Call, aliases: dict[str, str]) -> str:
    raw = _call_name(node)
    head, separator, tail = raw.partition(".")
    resolved = aliases.get(head, head)
    return resolved + (separator + tail if separator else "")


def _diagnostic_frames(diagnostics: Iterable[str]) -> tuple[tuple[str, int, str], ...]:
    frames: list[tuple[str, int, str]] = []
    for diagnostic in diagnostics:
        for match in _TRACEBACK_FRAME.finditer(str(diagnostic or "")):
            frames.append(
                (
                    _normalize_path(match.group("path")),
                    int(match.group("line")),
                    str(match.group("scope") or ""),
                )
            )
    return tuple(frames)


def _diagnostic_relevant(
    *, path: str, line: int, scope: str, frames: tuple[tuple[str, int, str], ...]
) -> bool:
    return any(
        (frame_path == path or frame_path.endswith("/" + path) or path.endswith("/" + frame_path))
        and (
            (frame_scope and frame_scope == scope)
            or abs(int(frame_line) - int(line)) <= 4
        )
        for frame_path, frame_line, frame_scope in frames
    )


def _fact(
    *,
    kind: SemanticFactKind,
    document: RepositoryDocument,
    node: ast.AST,
    scope: str,
    subject: str,
    relation: str,
    object_: str,
    source_revision: str,
    frames: tuple[tuple[str, int, str], ...],
    provenance: tuple[str, ...],
    library_model: str = "",
) -> SemanticGraphFact:
    start = int(document.start_line or 1) + max(0, int(getattr(node, "lineno", 1)) - 1)
    end = int(document.start_line or 1) + max(
        0, int(getattr(node, "end_lineno", getattr(node, "lineno", 1))) - 1
    )
    evidence = _expr(node)
    path = _normalize_path(document.path)
    return SemanticGraphFact(
        claim_id=_stable_id(
            kind.value,
            path,
            start,
            scope,
            subject,
            relation,
            object_,
            source_revision,
        ),
        kind=kind,
        path=path,
        start_line=start,
        end_line=max(start, end),
        scope=scope,
        subject=subject,
        relation=relation,
        object=object_,
        evidence=evidence[:600],
        provenance=provenance,
        source_revision=source_revision,
        diagnostic_relevant=_diagnostic_relevant(
            path=path, line=start, scope=scope, frames=frames
        ),
        library_model=library_model,
    )


def _scope_by_node(tree: ast.AST) -> dict[int, str]:
    scopes: dict[int, str] = {}

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.stack.append(node.name)
            scopes[id(node)] = node.name
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def generic_visit(self, node: ast.AST) -> None:
            scopes.setdefault(id(node), self.stack[-1] if self.stack else "")
            super().generic_visit(node)

    Visitor().visit(tree)
    return scopes


def _facts_for_document(
    document: RepositoryDocument,
    tree: ast.AST,
    *,
    source_revision: str,
    frames: tuple[tuple[str, int, str], ...],
) -> tuple[SemanticGraphFact, ...]:
    facts: list[SemanticGraphFact] = []
    aliases = _import_aliases(tree)
    scopes = _scope_by_node(tree)
    definitions: dict[str, list[tuple[str, ...]]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            definitions.setdefault(node.name, []).append(
                tuple(argument.arg for argument in node.args.args)
            )

    for node in ast.walk(tree):
        scope = scopes.get(id(node), "")
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            value = node.value
            target_nodes = node.targets if isinstance(node, ast.Assign) else (node.target,)
            dependencies = _names(value)
            rendered_value = _expr(value)
            for target_node in target_nodes:
                for target in _targets(target_node):
                    facts.append(
                        _fact(
                            kind=SemanticFactKind.VALUE_FLOW,
                            document=document,
                            node=node,
                            scope=scope,
                            subject=target,
                            relation="<-",
                            object_=(
                                f"{rendered_value} (depends on: {', '.join(dependencies)})"
                                if dependencies
                                else rendered_value
                            ),
                            source_revision=source_revision,
                            frames=frames,
                            provenance=("python_ast", "exact_assignment"),
                        )
                    )
        elif isinstance(node, ast.Return) and node.value is not None:
            dependencies = _names(node.value)
            facts.append(
                _fact(
                    kind=SemanticFactKind.RETURN_FLOW,
                    document=document,
                    node=node,
                    scope=scope,
                    subject=f"{scope or '<module>'}.return",
                    relation="<-",
                    object_=(
                        f"{_expr(node.value)} (depends on: {', '.join(dependencies)})"
                        if dependencies
                        else _expr(node.value)
                    ),
                    source_revision=source_revision,
                    frames=frames,
                    provenance=("python_ast", "exact_return"),
                )
            )
        elif isinstance(node, (ast.If, ast.While)):
            facts.append(
                _fact(
                    kind=SemanticFactKind.CONTROL_DEPENDENCY,
                    document=document,
                    node=node,
                    scope=scope,
                    subject="body",
                    relation="executes when",
                    object_=_expr(node.test),
                    source_revision=source_revision,
                    frames=frames,
                    provenance=("python_ast", "explicit_control_predicate"),
                )
            )
        elif isinstance(node, ast.Assert):
            expression = _expr(node.test)
            if ".shape" in expression or ".size(" in expression or ".ndim" in expression:
                facts.append(
                    _fact(
                        kind=SemanticFactKind.SHAPE_CONSTRAINT,
                        document=document,
                        node=node,
                        scope=scope,
                        subject="asserted tensor shape",
                        relation="requires",
                        object_=expression,
                        source_revision=source_revision,
                        frames=frames,
                        provenance=("python_ast", "explicit_assertion"),
                    )
                )

        if not isinstance(node, ast.Call):
            continue
        resolved = _resolved_call(node, aliases)
        if resolved == "torch.nn.functional.linear" and len(node.args) >= 2:
            bindings = [
                f"input={_expr(node.args[0])}",
                f"weight={_expr(node.args[1])}",
            ]
            if len(node.args) >= 3:
                bindings.append(f"bias={_expr(node.args[2])}")
            facts.append(
                _fact(
                    kind=SemanticFactKind.SHAPE_CONSTRAINT,
                    document=document,
                    node=node,
                    scope=scope,
                    subject="torch.nn.functional.linear contract:",
                    relation="",
                    object_=(
                        "input[-1] == weight[-1]; output[-1] == weight[-2]; "
                        + ", ".join(bindings)
                    ),
                    source_revision=source_revision,
                    frames=frames,
                    provenance=("python_ast", "verified_library_model"),
                    library_model="pytorch.linear.v1",
                )
            )
        # Only a direct name with exactly one local definition is safe to bind.
        # Attribute calls require type resolution; guessing from the final name
        # creates convincing but false data-flow edges.
        raw_name = node.func.id if isinstance(node.func, ast.Name) else ""
        candidates = definitions.get(raw_name, [])
        parameters = candidates[0] if len(candidates) == 1 else ()
        if parameters and node.args:
            for parameter, argument in zip(parameters, node.args, strict=False):
                facts.append(
                    _fact(
                        kind=SemanticFactKind.CALL_ARGUMENT_FLOW,
                        document=document,
                        node=node,
                        scope=scope,
                        subject=_expr(argument),
                        relation="flows to",
                        object_=f"{raw_name}.{parameter}",
                        source_revision=source_revision,
                        frames=frames,
                        provenance=("python_ast", "unique_local_definition"),
                    )
                )
    unique = {fact.claim_id: fact for fact in facts}
    return tuple(unique.values())


_TREE_SITTER_LANGUAGE_BY_SUFFIX = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
}
_FUNCTION_NODE_TYPES = frozenset(
    {
        "function_declaration",
        "function_expression",
        "method_definition",
        "method_declaration",
        "arrow_function",
        "function_item",
        "closure_expression",
    }
)
_ASSIGNMENT_FIELDS = {
    "variable_declarator": (("name",), ("value",)),
    "assignment_expression": (("left",), ("right",)),
    "augmented_assignment_expression": (("left",), ("right",)),
    "assignment_statement": (("left",), ("right",)),
    "short_var_declaration": (("left",), ("right",)),
    "var_spec": (("name",), ("value",)),
    "let_declaration": (("pattern",), ("value",)),
}
_RETURN_NODE_TYPES = frozenset({"return_statement", "return_expression"})
_CONTROL_NODE_TYPES = frozenset(
    {"if_statement", "if_expression", "while_statement", "while_expression"}
)


def _node_text(node: Any | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[int(node.start_byte) : int(node.end_byte)].decode("utf-8", errors="replace")


def _field(node: Any, names: tuple[str, ...]) -> Any | None:
    for name in names:
        value = node.child_by_field_name(name)
        if value is not None:
            return value
    return None


def _syntax_scope(node: Any, source: bytes) -> str:
    parent = node.parent
    while parent is not None:
        if parent.type in _FUNCTION_NODE_TYPES:
            name = parent.child_by_field_name("name")
            if name is not None:
                return _node_text(name, source)
            # Arrow functions are normally owned by a variable declarator.
            owner = parent.parent
            if owner is not None:
                owner_name = owner.child_by_field_name("name")
                if owner_name is not None:
                    return _node_text(owner_name, source)
        parent = parent.parent
    return ""


def _syntax_identifiers(node: Any | None, source: bytes) -> tuple[str, ...]:
    if node is None:
        return ()
    values: set[str] = set()
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in {"identifier", "field_identifier", "type_identifier"}:
            values.add(_node_text(current, source))
        stack.extend(current.named_children)
    return tuple(sorted(value for value in values if value))


def _syntax_fact(
    *,
    kind: SemanticFactKind,
    document: RepositoryDocument,
    node: Any,
    source: bytes,
    scope: str,
    subject: str,
    relation: str,
    object_: str,
    source_revision: str,
    language: str,
) -> SemanticGraphFact:
    start = int(document.start_line or 1) + int(node.start_point.row)
    end = int(document.start_line or 1) + int(node.end_point.row)
    path = _normalize_path(document.path)
    return SemanticGraphFact(
        claim_id=_stable_id(
            kind.value, path, start, scope, subject, relation, object_, source_revision
        ),
        kind=kind,
        path=path,
        start_line=start,
        end_line=max(start, end),
        scope=scope,
        subject=subject,
        relation=relation,
        object=object_,
        evidence=_node_text(node, source)[:600],
        provenance=("tree_sitter", language, "exact_source_span"),
        source_revision=source_revision,
    )


def _tree_sitter_facts(
    document: RepositoryDocument,
    *,
    language: str,
    source_revision: str,
) -> tuple[SemanticGraphFact, ...] | None:
    source = str(document.text or "").encode("utf-8")
    tree = get_parser(language).parse(source)
    if tree.root_node.has_error:
        return None
    facts: list[SemanticGraphFact] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        stack.extend(reversed(node.named_children))
        scope = _syntax_scope(node, source)
        assignment = _ASSIGNMENT_FIELDS.get(node.type)
        if assignment is not None:
            target = _field(node, assignment[0])
            value = _field(node, assignment[1])
            if target is not None and value is not None:
                target_text = _node_text(target, source)
                value_text = _node_text(value, source)
                dependencies = tuple(
                    value
                    for value in _syntax_identifiers(value, source)
                    if value not in set(_syntax_identifiers(target, source))
                )
                facts.append(
                    _syntax_fact(
                        kind=SemanticFactKind.VALUE_FLOW,
                        document=document,
                        node=node,
                        source=source,
                        scope=scope,
                        subject=target_text,
                        relation="<-",
                        object_=(
                            f"{value_text} (depends on: {', '.join(dependencies)})"
                            if dependencies
                            else value_text
                        ),
                        source_revision=source_revision,
                        language=language,
                    )
                )
        elif node.type in _RETURN_NODE_TYPES:
            value = node.child_by_field_name("value")
            if value is None and node.named_children:
                value = node.named_children[-1]
            value_text = _node_text(value, source)
            if value_text:
                dependencies = _syntax_identifiers(value, source)
                facts.append(
                    _syntax_fact(
                        kind=SemanticFactKind.RETURN_FLOW,
                        document=document,
                        node=node,
                        source=source,
                        scope=scope,
                        subject=f"{scope or '<module>'}.return",
                        relation="<-",
                        object_=(
                            f"{value_text} (depends on: {', '.join(dependencies)})"
                            if dependencies
                            else value_text
                        ),
                        source_revision=source_revision,
                        language=language,
                    )
                )
        elif node.type in _CONTROL_NODE_TYPES:
            condition = node.child_by_field_name("condition")
            if condition is not None:
                facts.append(
                    _syntax_fact(
                        kind=SemanticFactKind.CONTROL_DEPENDENCY,
                        document=document,
                        node=node,
                        source=source,
                        scope=scope,
                        subject="body",
                        relation="executes when",
                        object_=_node_text(condition, source),
                        source_revision=source_revision,
                        language=language,
                    )
                )
    return tuple({fact.claim_id: fact for fact in facts}.values())


def compile_semantic_graph(
    documents: Iterable[RepositoryDocument],
    *,
    source_revision: str,
    task: str = "",
    anchor_paths: Iterable[str] = (),
    anchor_symbols: Iterable[str] = (),
    diagnostics: Iterable[str] = (),
    max_facts: int = 12,
) -> SemanticGraphProjection:
    """Compile bounded semantic facts from exact, preexisting source spans."""

    if _TREE_SITTER_RUNTIME_VERSION != _REQUIRED_TREE_SITTER_VERSION:
        return SemanticGraphProjection(
            status=SemanticGraphStatus.FAILED,
            facts=(),
            receipt=SemanticGraphReceipt(
                source_revision=source_revision,
                builder_version=_BUILDER_VERSION,
                documents_attempted=0,
                documents_indexed=0,
                documents_failed=0,
                limitations=(
                    "unsupported_tree_sitter_runtime:"
                    f"expected={_REQUIRED_TREE_SITTER_VERSION}:"
                    f"actual={_TREE_SITTER_RUNTIME_VERSION}",
                ),
            ),
        )

    normalized_anchors = {_normalize_path(path) for path in anchor_paths if path}
    symbol_anchors = {str(symbol or "").strip() for symbol in anchor_symbols if symbol}
    task_tokens = {token.lower() for token in _TOKEN.findall(str(task or "")) if len(token) > 2}
    frames = _diagnostic_frames(diagnostics)
    attempted = indexed = failed = 0
    limitations: list[str] = []
    facts: list[SemanticGraphFact] = []
    seen_documents: set[tuple[str, int | None, int | None, str]] = set()

    for document in documents:
        key = (document.path, document.start_line, document.end_line, document.text)
        if key in seen_documents:
            continue
        seen_documents.add(key)
        if document.origin is not EvidenceOrigin.PREEXISTING_REPOSITORY:
            limitations.append(f"{document.origin.value}_source_rejected")
            continue
        attempted += 1
        suffix = "." + document.path.lower().rsplit(".", 1)[-1] if "." in document.path else ""
        if suffix in {".py", ".pyi"}:
            tree = _parse_document(document)
            if tree is None:
                failed += 1
                limitations.append("python_source_parse_failed")
                continue
            indexed += 1
            facts.extend(
                _facts_for_document(
                    document,
                    tree,
                    source_revision=source_revision,
                    frames=frames,
                )
            )
            continue
        language = _TREE_SITTER_LANGUAGE_BY_SUFFIX.get(suffix)
        if language is None:
            attempted -= 1
            limitations.append("semantic_language_unsupported")
            continue
        syntax_facts = _tree_sitter_facts(
            document,
            language=language,
            source_revision=source_revision,
        )
        if syntax_facts is None:
            failed += 1
            limitations.append(f"{language}_source_parse_failed")
            continue
        indexed += 1
        facts.extend(syntax_facts)

    unique_facts = {fact.claim_id: fact for fact in facts}
    duplicate_facts_removed = len(facts) - len(unique_facts)
    facts = list(unique_facts.values())

    def relevant(fact: SemanticGraphFact) -> bool:
        if fact.diagnostic_relevant:
            return True
        if normalized_anchors and fact.path in normalized_anchors:
            return True
        if symbol_anchors and fact.scope in symbol_anchors:
            return True
        text = f"{fact.scope} {fact.subject} {fact.object}".lower()
        return bool(task_tokens and task_tokens & set(_TOKEN.findall(text)))

    relevant_facts = [fact for fact in facts if relevant(fact)]
    kind_priority = {
        SemanticFactKind.SHAPE_CONSTRAINT: 0,
        SemanticFactKind.VALUE_FLOW: 1,
        SemanticFactKind.CALL_ARGUMENT_FLOW: 2,
        SemanticFactKind.RETURN_FLOW: 3,
        SemanticFactKind.CONTROL_DEPENDENCY: 4,
    }
    relevant_facts.sort(
        key=lambda fact: (
            not fact.diagnostic_relevant,
            kind_priority[fact.kind],
            fact.path,
            fact.start_line,
            fact.claim_id,
        )
    )
    maximum = max(1, int(max_facts))
    selected = tuple(relevant_facts[:maximum])
    truncated = max(0, len(relevant_facts) - len(selected))
    counts = Counter(fact.kind.value for fact in selected)
    if truncated:
        limitations.append("semantic_fact_limit")
    if limitations:
        status = (
            SemanticGraphStatus.READY_WITH_DECLARED_LIMITATIONS
            if selected
            else SemanticGraphStatus.ABSTAIN
        )
    else:
        status = SemanticGraphStatus.READY if selected else SemanticGraphStatus.ABSTAIN
    return SemanticGraphProjection(
        status=status,
        facts=selected,
        receipt=SemanticGraphReceipt(
            source_revision=source_revision,
            builder_version=_BUILDER_VERSION,
            documents_attempted=attempted,
            documents_indexed=indexed,
            documents_failed=failed,
            facts_by_kind=dict(sorted(counts.items())),
            duplicate_facts_removed=duplicate_facts_removed,
            limitations=tuple(dict.fromkeys(limitations)),
        ),
        truncated_count=truncated,
    )


__all__ = [
    "SemanticFactKind",
    "SemanticGraphFact",
    "SemanticGraphProjection",
    "SemanticGraphReceipt",
    "SemanticGraphStatus",
    "compile_semantic_graph",
]
