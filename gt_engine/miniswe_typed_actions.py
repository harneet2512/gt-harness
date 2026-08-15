"""Harness-owned typed GroundTruth tool for Mini-SWE-Agent 2.3.

Mini-SWE 2.3 hardcodes a single Bash tool in both its LiteLLM request and its
tool-call parser.  This module extends those two *harness* seams without
modifying site-packages.  Bash remains byte-for-byte the stock action path;
only an explicitly selected ``groundtruth`` call enters the typed router.

Canonical observation-compiler contracts belong to GroundTruth core.  The
wire adapter below imports them when the installed wheel exposes
``groundtruth.runtime.observation_compiler``.  Until the vendored wheel is
updated it emits the same versioned mapping shape; it deliberately defines no
competing public contract classes.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import litellm
from jinja2 import StrictUndefined, Template
from minisweagent.exceptions import FormatError
from minisweagent.models.litellm_model import LitellmModel
from minisweagent.models.utils.actions_toolcall import BASH_TOOL

from gt_engine.generated_typed_capabilities import (
    CERTIFICATION_SHA256,
    CERTIFIED_SYNTAX_EXTENSIONS,
    CERTIFIED_SYNTAX_LANGUAGES,
    CERTIFIED_TYPED_KINDS,
    LANGUAGE_MANIFEST_SHA256,
    REMOVED_TYPED_KINDS,
)

if TYPE_CHECKING:
    from types import ModuleType


GROUNDTRUTH_TOOL = {
    "type": "function",
    "function": {
        "name": "groundtruth",
        "description": (
            "Run an explicitly selected deterministic repository query. "
            "Use bash for literal shell semantics, builds, tests, and edits."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": list(CERTIFIED_TYPED_KINDS),
                },
                "arguments": {
                    "type": "object",
                    "description": (
                        "Exact typed arguments. Literal search uses literal and paths; "
                        "syntax uses a certified file extension; "
                        "patch impact uses edited_files; verification status uses plan and result."
                    ),
                    "additionalProperties": True,
                },
                "requested_fidelity": {
                    "type": "string",
                    "enum": ["exact", "sound_overapprox", "execution_specific"],
                    "default": "exact",
                },
            },
            "required": ["kind", "arguments"],
            "additionalProperties": False,
            "x-groundtruth-certification": {
                "sha256": CERTIFICATION_SHA256,
                "removed_kinds": list(REMOVED_TYPED_KINDS),
                "syntax_languages": list(CERTIFIED_SYNTAX_LANGUAGES),
                "syntax_extensions": list(CERTIFIED_SYNTAX_EXTENSIONS),
            },
        },
    },
}


class _CoreCompiler(Protocol):
    """The narrow GroundTruth-core surface consumed by this harness."""

    ActionRequest: type


def _core_compiler() -> _CoreCompiler | ModuleType | None:
    try:
        from groundtruth.runtime import observation_compiler

        return observation_compiler  # type: ignore[return-value]
    except (ImportError, AttributeError):
        return None


def _format_error(template: str, error: str) -> FormatError:
    return FormatError(
        {
            "role": "user",
            "content": Template(template, undefined=StrictUndefined).render(
                actions=[], error=error
            ),
            "extra": {"interrupt_type": "FormatError"},
        }
    )


def parse_groundtruth_toolcalls(
    tool_calls: list[Any], *, format_error_template: str
) -> list[dict[str, Any]]:
    """Parse stock Bash plus the native typed tool without intent inference."""
    if not tool_calls:
        raise _format_error(
            format_error_template,
            "No tool calls found in the response. Every response MUST include "
            "at least one tool call.",
        )
    actions: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        name = str(getattr(tool_call.function, "name", "") or "")
        try:
            arguments = json.loads(tool_call.function.arguments)
        except Exception as exc:
            raise _format_error(
                format_error_template,
                f"Error parsing {name or 'unknown'} tool arguments: {exc}.",
            ) from exc
        if not isinstance(arguments, dict):
            raise _format_error(
                format_error_template, f"Arguments for tool '{name}' must be an object."
            )
        if name == "bash":
            if "command" not in arguments or not isinstance(arguments["command"], str):
                raise _format_error(
                    format_error_template,
                    "Missing string 'command' argument in bash tool call.",
                )
            actions.append(
                {
                    "command": arguments["command"],
                    "tool_call_id": tool_call.id,
                    "tool_name": "bash",
                }
            )
            continue
        if name == "groundtruth":
            # Detailed validation belongs to the typed router so malformed or
            # unsupported requests produce a visible INCOMPLETE artifact rather
            # than being silently reinterpreted as Bash.
            actions.append(
                {
                    "gt_action": arguments,
                    "tool_call_id": tool_call.id,
                    "tool_name": "groundtruth",
                }
            )
            continue
        raise _format_error(format_error_template, f"Unknown tool '{name}'.")
    return actions


class GroundTruthLitellmModel(LitellmModel):
    """LiteLLM Mini-SWE model advertising Bash and GroundTruth side by side."""

    tools = (BASH_TOOL, GROUNDTRUTH_TOOL)

    def _query(self, messages: list[dict[str, str]], **kwargs: Any):
        try:
            return litellm.completion(
                model=self.config.model_name,
                messages=messages,
                tools=list(self.tools),
                **(self.config.model_kwargs | kwargs),
            )
        except litellm.exceptions.AuthenticationError as exc:
            exc.message += (
                " You can permanently set your API key with "
                "`mini-extra config set KEY VALUE`."
            )
            raise

    def _parse_actions(self, response) -> list[dict[str, Any]]:
        tool_calls = response.choices[0].message.tool_calls or []
        return parse_groundtruth_toolcalls(
            tool_calls,
            format_error_template=self.config.format_error_template,
        )


def is_typed_action(action: Any) -> bool:
    return isinstance(action, Mapping) and action.get("tool_name") == "groundtruth"


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _snapshot_authority(
    repo_root: Path,
) -> tuple[str, tuple[tuple[str, str], ...], bool]:
    """Capture one complete file manifest and its working-tree identity.

    The query producer must hash the same captured authority that it uses to
    certify literal-search coverage.  A read/traversal failure remains part of
    the tree hash but revokes completeness, so it can never produce REPLACE.
    """
    digest = hashlib.sha256(b"gt.repository_snapshot.v1\0")
    if not repo_root.is_dir():
        return digest.hexdigest(), (), False
    files: list[tuple[str, str]] = []
    complete = True
    try:
        paths = sorted(repo_root.rglob("*"), key=lambda item: item.as_posix())
    except OSError:
        paths = []
        complete = False
    for path in paths:
        try:
            relative = path.relative_to(repo_root).as_posix()
        except ValueError:
            continue
        if relative == ".git" or relative.startswith(".git/"):
            continue
        try:
            if path.is_symlink():
                kind = b"L"
                data = os.readlink(path).encode("utf-8", "surrogatepass")
            elif path.is_file():
                kind = b"F"
                data = path.read_bytes()
                files.append((relative, hashlib.sha256(data).hexdigest()))
            else:
                continue
        except OSError:
            # A concurrently changing workspace cannot be certified as a
            # complete snapshot; incorporate a stable missing witness.
            kind, data = b"M", b""
            complete = False
        digest.update(kind)
        digest.update(relative.encode("utf-8", "surrogatepass"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest(), tuple(sorted(files)), complete


def _file_snapshot(repo_root: Path) -> str:
    """Content-address all working files, including untracked files and symlinks."""
    return _snapshot_authority(repo_root)[0]


def _graph_revision(path: str | Path | None, root: Path, fallback: str) -> str:
    """Read the graph's semantic revision; its container hash is not equivalent."""
    if not path:
        return fallback
    try:
        graph_path = Path(path)
        if not graph_path.is_absolute():
            graph_path = root / graph_path
        uri = f"file:{graph_path.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5) as connection:
            row = connection.execute(
                "SELECT value FROM project_meta WHERE key='post_revision'"
            ).fetchone()
        revision = str(row[0] or "") if row else ""
        return revision or fallback
    except (OSError, sqlite3.Error):
        return fallback


def _git_revision(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        revision = result.stdout.strip() if result.returncode == 0 else ""
        return revision or "WORKTREE"
    except (OSError, subprocess.SubprocessError):
        return "WORKTREE"


def _build_core_request(
    core: Any,
    *,
    wire: Mapping[str, Any],
    root: Path,
    configuration: Mapping[str, Any],
) -> Any:
    """Construct the GroundTruth-owned contracts from harness observations."""
    from groundtruth.runtime.reasoning_runtime import RevisionVector

    working_tree = str(wire["repository_snapshot"])
    configuration_inputs = hashlib.sha256(_canonical_bytes(configuration)).hexdigest()
    language_manifest = str(configuration.get("language_manifest_sha256") or "")
    if len(language_manifest) != 64:
        language_manifest = LANGUAGE_MANIFEST_SHA256
    binding = core.ConfigurationBinding(
        schema=core.CONFIGURATION_BINDING_SCHEMA,
        configuration_id=str(configuration.get("configuration_id") or "miniswe-default"),
        inputs_sha256=configuration_inputs,
        language_manifest_sha256=language_manifest,
        build_system=str(configuration.get("build_system") or "unspecified"),
    )
    graph_revision = _graph_revision(configuration.get("graph_db"), root, working_tree)
    snapshot = core.RepositorySnapshot(
        schema=core.REPOSITORY_SNAPSHOT_SCHEMA,
        repository_id=hashlib.sha256(str(root).encode("utf-8")).hexdigest(),
        root_sha256=hashlib.sha256(str(root).encode("utf-8")).hexdigest(),
        git_revision=_git_revision(root),
        # The harness fallback has no separate index-stage dirty-diff authority;
        # bind it conservatively to the complete working-tree identity.
        dirty_diff_sha256=working_tree,
        working_tree_sha256=working_tree,
        revisions=RevisionVector(
            repository_content=working_tree,
            graph=graph_revision,
            lsp=working_tree,
            runtime_evidence=working_tree,
        ),
        configuration=binding,
    )
    kind_names = {
        "exact_literal_search": "EXACT_LITERAL_SEARCH",
        "definition": "FIND_DEFINITION",
        "references": "FIND_REFERENCES",
        "callers": "FIND_CALLERS",
        "syntax": "SYNTAX_QUERY",
        "patch_impact": "PATCH_IMPACT",
        "verification_status": "VERIFICATION_STATUS",
    }
    fidelity_names = {
        "exact": "EXACT",
        "sound_overapprox": "SOUND_OVERAPPROX",
        "execution_specific": "EXECUTION_SPECIFIC",
    }
    kind = getattr(core.ActionKind, kind_names.get(str(wire["kind"]), "SHELL"))
    fidelity = getattr(
        core.RequestedFidelity,
        fidelity_names.get(str(wire["requested_fidelity"]), "EXACT"),
    )
    return core.ActionRequest.build(
        action_id=str(wire["action_id"]),
        kind=kind,
        arguments=dict(wire["arguments"]),
        snapshot=snapshot,
        requested_fidelity=fidelity,
        original_shell_form="",
    )


def build_action_request(
    action: Mapping[str, Any],
    *,
    repo_root: str | Path,
    configuration: Mapping[str, Any] | None = None,
) -> Any:
    """Bind a parsed call to the current repository and configuration.

    The installed wheel may temporarily lag the GroundTruth checkout.  In that
    case this returns a private versioned wire mapping, not a second public
    contract type.  Once core is present, its strict constructor/validator is
    the authority.
    """
    payload = action.get("gt_action")
    payload = dict(payload) if isinstance(payload, Mapping) else {}
    args = payload.get("arguments")
    args = dict(args) if isinstance(args, Mapping) else {}
    root = Path(repo_root).resolve()
    config = dict(configuration or {})
    request_id = str(action.get("tool_call_id") or "")
    wire = {
        "schema": "gt.action_request.v1",
        "action_id": request_id,
        "kind": str(payload.get("kind") or ""),
        "arguments": args,
        "repository_snapshot": _file_snapshot(root),
        "configuration": config,
        "configuration_sha256": hashlib.sha256(_canonical_bytes(config)).hexdigest(),
        "requested_fidelity": str(payload.get("requested_fidelity") or "exact"),
        "original_shell_form": "",
    }
    if not wire["action_id"]:
        wire["action_id"] = hashlib.sha256(_canonical_bytes(wire)).hexdigest()[:24]

    # The public schema and runtime gate share one generated certification
    # authority. A manually constructed removed action remains a typed,
    # fail-open request; it can never be laundered into ActionKind.SHELL.
    if wire["kind"] not in CERTIFIED_TYPED_KINDS:
        return wire

    core = _core_compiler()
    if core is None:
        return wire
    # GroundTruth core owns exact constructors and strict validation.
    return _build_core_request(
        core,
        wire=wire,
        root=root,
        configuration=config,
    )


def _request_mapping(request: Any) -> dict[str, Any]:
    if isinstance(request, Mapping):
        return dict(request)
    to_mapping = getattr(request, "to_mapping", None)
    if callable(to_mapping):
        return dict(to_mapping())
    if hasattr(request, "model_dump"):
        return dict(request.model_dump(mode="json"))
    core = _core_compiler()
    if core is not None:
        try:
            wire = json.loads(core.canonical_bytes(request))
            if "arguments_json" in wire and "arguments" not in wire:
                wire["arguments"] = json.loads(wire["arguments_json"])
            return wire
        except (AttributeError, TypeError, ValueError):
            pass
    if hasattr(request, "__dict__"):
        return dict(request.__dict__)
    raise TypeError("ActionRequest is not serializable")


def _safe_scope(root: Path, requested: Any) -> Path | None:
    scope = (root / str(requested or ".")).resolve()
    try:
        scope.relative_to(root)
    except ValueError:
        return None
    return scope


def _literal_search(request: Mapping[str, Any], root: Path) -> tuple[list[dict], list[str]]:
    args = request.get("arguments")
    args = dict(args) if isinstance(args, Mapping) else {}
    query = args.get("literal")
    if not isinstance(query, str) or not query:
        return [], ["missing_nonempty_query"]
    unknown_arguments = sorted(set(args) - {"literal", "paths"})
    if unknown_arguments:
        return [], [f"unsupported_argument:{name}" for name in unknown_arguments]
    if any(marker in query for marker in ("\x00", "\r", "\n")):
        return [], ["query_must_be_single_line_text"]
    raw_paths = args.get("paths", ["."])
    if not isinstance(raw_paths, list) or not raw_paths:
        return [], ["paths_must_be_nonempty_array"]
    scopes: list[Path] = []
    for raw_path in raw_paths:
        scope = _safe_scope(root, raw_path)
        if scope is None or not scope.exists():
            return [], ["scope_outside_repository_or_missing"]
        scopes.append(scope)
    query_bytes = query.encode("utf-8")
    paths = [
        path
        for scope in scopes
        for path in ([scope] if scope.is_file() else list(scope.rglob("*")))
    ]
    answer: list[dict[str, Any]] = []
    omissions: list[str] = []
    for path in sorted(paths, key=lambda item: item.as_posix()):
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            omissions.append("scope_escape")
            continue
        if relative == ".git" or relative.startswith(".git/"):
            continue
        if path.is_symlink():
            omissions.append(f"symlink:{relative}")
            continue
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            omissions.append(f"unreadable:{relative}")
            continue
        for line_number, line in enumerate(data.splitlines(), start=1):
            count = line.count(query_bytes)
            if count:
                answer.append(
                    {
                        "path": relative,
                        "line": line_number,
                        "occurrences": count,
                        "line_sha256": hashlib.sha256(line).hexdigest(),
                        "preview": line.decode("utf-8", "replace"),
                    }
                )
    return answer, sorted(set(omissions))


def _deterministic_query_api() -> tuple[type, Any] | None:
    """Return the canonical dispatcher, falling back only for a genuinely old wheel."""
    try:
        from groundtruth.runtime.deterministic_queries import (
            DeterministicQueryContext,
            execute_query,
        )

        return DeterministicQueryContext, execute_query
    except ModuleNotFoundError as exc:
        if exc.name == "groundtruth.runtime.deterministic_queries":
            return None
        raise


def _graph_definition_search(
    arguments: Mapping[str, Any],
    graph_db: str | Path | None,
    repo_root: Path,
) -> dict[str, Any] | None:
    """Graph-backed definition search: locate a symbol's definition.

    Queries the graph's nodes for the requested symbol and returns
    file:line:signature for each definition node (the graph-certified depth the
    typed tool previously lacked — definition was REMOVED only because it
    couldn't be certified; the populated graph now certifies it). Correct-or-
    quiet: no graph / no match -> None (the caller passes through).
    """
    symbol = str(arguments.get("symbol") or arguments.get("name") or arguments.get("query") or "").strip()
    if not symbol or not graph_db:
        return None
    import sqlite3

    db = Path(graph_db)
    if not db.is_absolute():
        db = repo_root / db
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT file_path, start_line, signature FROM nodes "
                "WHERE name = ? AND COALESCE(is_test,0)=0 ORDER BY start_line LIMIT 12",
                (symbol,),
            ).fetchall()
            if not rows:
                rows = con.execute(
                    "SELECT file_path, start_line, signature FROM nodes "
                    "WHERE name LIKE ? AND COALESCE(is_test,0)=0 ORDER BY start_line LIMIT 12",
                    (f"%{symbol}%",),
                ).fetchall()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return None
    if not rows:
        return None
    lines = [f"{fp}:{ln}:{(sig or '')[:80]}" for fp, ln, sig in rows if fp]
    return {
        "answer": "definition of %s:\n%s" % (symbol, "\n".join(lines)),
        "anchors": [f"{fp}:{ln}" for fp, ln, _sig in rows if fp],
        "complete": True,
    }


def execute_typed_action(
    request: Any,
    *,
    repo_root: str | Path,
    graph_db: str | Path | None = None,
) -> dict[str, Any]:
    """Execute a typed query and return a Mini-SWE environment-result mapping."""
    wire = _request_mapping(request)
    kind = str(wire.get("kind") or "")
    if kind.startswith("ActionKind."):
        kind = kind.rsplit(".", 1)[-1].lower()
    kind = {
        "find_definition": "definition",
        "find_references": "references",
        "find_callers": "callers",
        "syntax_query": "syntax",
    }.get(kind, kind)
    root = Path(repo_root).resolve()
    core = _core_compiler()
    query_api = _deterministic_query_api()
    certification_omission = ""
    if kind not in CERTIFIED_TYPED_KINDS:
        certification_omission = "typed_kind_removed"
    elif kind == "syntax":
        arguments = wire.get("arguments")
        arguments = dict(arguments) if isinstance(arguments, Mapping) else {}
        extension = Path(str(arguments.get("path") or "")).suffix.lower()
        if extension not in CERTIFIED_SYNTAX_EXTENSIONS:
            certification_omission = "syntax_language_removed"
    if certification_omission:
        evidence = {
            "schema": "gt.evidence_artifact.v1",
            "action_id": wire.get("action_id", ""),
            "answer": None,
            "anchors": [],
            "witnesses": [],
            "producer": "gt-harness.certification_gate.v1",
            "freshness": {"repository_snapshot": wire.get("repository_snapshot", "")},
            "semantics": "incomplete",
            "coverage": {},
            "omissions": [certification_omission],
            "raw_fallback": None,
        }
        direct_answer = None
        decision, reason, returncode = "PASS_THROUGH", certification_omission, 2
        decision_payload = {
            "schema": "gt.interception_decision.v1",
            "mode": decision,
            "reason_codes": [reason],
        }
    elif (
        core is not None
        and query_api is not None
        and isinstance(request, core.ActionRequest)
    ):
        context_type, execute_query = query_api
        try:
            graph_path = Path(graph_db) if graph_db else None
            if graph_path is not None and not graph_path.is_absolute():
                graph_path = root / graph_path
            snapshot_revision, snapshot_files, snapshot_complete = (
                _snapshot_authority(root)
            )
            artifact = execute_query(
                request,
                context_type(
                    root,
                    graph_path,
                    repository_content_revision=snapshot_revision,
                    working_tree_sha256=snapshot_revision,
                    snapshot_files=snapshot_files,
                    snapshot_complete=snapshot_complete,
                ),
            )
        except ValueError:
            artifact = None
        if artifact is not None:
            validation = core.validate(artifact)
            if validation:
                raise ValueError("invalid deterministic artifact: " + "|".join(validation))
            canonical_decision = core.evaluate_interception(request, (artifact,))
            validation = core.validate(canonical_decision)
            if validation:
                raise ValueError("invalid interception decision: " + "|".join(validation))
            decision = canonical_decision.mode.value
            evidence: Any = json.loads(core.canonical_bytes(artifact))
            direct_answer = artifact.direct_answer
            returncode = 2 if artifact.semantics is core.EvidenceSemantics.INCOMPLETE else 0
        else:
            canonical_decision = core.evaluate_interception(request, ())
            decision = canonical_decision.mode.value
            evidence = {
                "schema": "gt.evidence_artifact.v1",
                "action_id": wire.get("action_id", ""),
                "semantics": "incomplete",
                "direct_answer_json": "null",
                "omissions": ["producer_not_supported"],
            }
            direct_answer = None
            returncode = 2
        wire = json.loads(core.canonical_bytes(request))
        decision_payload = json.loads(core.canonical_bytes(canonical_decision))
    elif kind == "exact_literal_search":
        # Compatibility fallback for a vendored wheel that genuinely predates
        # the canonical deterministic-query dispatcher.
        answer, omissions = _literal_search(wire, root)
        complete = not omissions
        evidence: Any = {
            "schema": "gt.evidence_artifact.v1",
            "action_id": wire.get("action_id", ""),
            "answer": answer,
            "anchors": [f"{row['path']}:{row['line']}" for row in answer],
            "witnesses": [row["line_sha256"] for row in answer],
            "producer": "gt-harness.exact_literal_search.v1",
            "freshness": {"repository_snapshot": wire.get("repository_snapshot", "")},
            "semantics": "exact" if complete else "incomplete",
            "coverage": {"scope": wire.get("arguments", {}).get("paths", ["."])},
            "ambiguity": [],
            "omissions": omissions,
            "raw_fallback": None,
        }
        direct_answer = answer
        decision = "REPLACE" if complete else "AUGMENT"
        reason = "typed_exact_complete" if complete else "typed_analyzer_incomplete"
        returncode = 0 if complete else 2
        decision_payload = {
            "schema": "gt.interception_decision.v1",
            "mode": decision,
            "reason_codes": [reason],
        }
    else:
        evidence = {
            "schema": "gt.evidence_artifact.v1",
            "action_id": wire.get("action_id", ""),
            "answer": None,
            "anchors": [],
            "witnesses": [],
            "producer": "gt-harness.typed_router.v1",
            "freshness": {"repository_snapshot": wire.get("repository_snapshot", "")},
            "semantics": "incomplete",
            "coverage": {},
            "ambiguity": [],
            "omissions": ["producer_not_certified"],
            "raw_fallback": None,
        }
        direct_answer = None
        decision, reason, returncode = "PASS_THROUGH", "producer_not_certified", 2
        decision_payload = {
            "schema": "gt.interception_decision.v1",
            "mode": decision,
            "reason_codes": [reason],
        }
    result = {
        "schema": "gt.compiled_observation.v1",
        "action_request": wire,
        "evidence": evidence,
        # Model-facing structured projection. The canonical artifact remains
        # unmodified under ``evidence``; this avoids asking the model to decode
        # JSON nested inside ``direct_answer_json``.
        "direct_answer": direct_answer,
        "decision": decision_payload,
    }
    output = _canonical_bytes(result).decode("utf-8")
    return {
        "output": output,
        "returncode": returncode,
        "exception_info": "" if returncode == 0 else "typed evidence incomplete",
        "extra": {
            "gt_typed_action": True,
            "action_request_sha256": hashlib.sha256(
                _canonical_bytes(wire)
            ).hexdigest(),
            "compiled_observation_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
            "interception_decision": decision,
        },
    }


def execute_typed_action_fail_open(
    action: Mapping[str, Any],
    *,
    repo_root: str | Path,
    configuration: Mapping[str, Any] | None = None,
) -> tuple[Any | None, dict[str, Any]]:
    """Never let a typed-router defect become an environment shell action."""
    request: Any | None = None
    try:
        request = build_action_request(
            action, repo_root=repo_root, configuration=configuration
        )
        return request, execute_typed_action(
            request,
            repo_root=repo_root,
            graph_db=(configuration or {}).get("graph_db"),
        )
    except Exception as exc:  # noqa: BLE001 - native harness must continue
        payload = {
            "schema": "gt.compiled_observation.v1",
            "action_request": _request_mapping(request) if request is not None else None,
            "evidence": {
                "schema": "gt.evidence_artifact.v1",
                "semantics": "incomplete",
                "answer": None,
                "omissions": ["typed_router_failure"],
                "raw_fallback": None,
            },
            "direct_answer": None,
            "decision": {
                "schema": "gt.interception_decision.v1",
                "mode": "PASS_THROUGH",
                "reason_codes": ["typed_router_failure"],
            },
        }
        output = _canonical_bytes(payload).decode("utf-8")
        return request, {
            "output": output,
            "returncode": 2,
            "exception_info": f"GroundTruth typed action unavailable: {type(exc).__name__}",
            "extra": {
                "gt_typed_action": True,
                "compiled_observation_sha256": hashlib.sha256(
                    output.encode("utf-8")
                ).hexdigest(),
                "interception_decision": "PASS_THROUGH",
            },
        }
