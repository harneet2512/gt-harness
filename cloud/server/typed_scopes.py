"""Scope normalisation for the cloud agent's typed ``exact_literal_search``.

GroundTruth's deterministic literal-search producer treats every entry of the
``paths`` argument as a concrete filesystem path: it stats it and walks it
(``groundtruth/runtime/deterministic_queries.py`` ``_safe_scope`` / ``_iter_scope``).
Planners routinely write a glob instead — ``src/click/**`` — which names no
file, so the producer records ``missing_scope:src/click/**`` and correctly
abstains with ``semantics: incomplete``. The graph is fine; the scope never
existed. See ``docs/har85-literal-search.md``.

This module reduces such a glob to the concrete directory it selects, before
the request reaches the producer. The reduction is deliberately conservative:

* only a scope that actually contains a glob metacharacter is rewritten, so a
  plain typo still abstains honestly instead of silently widening to a parent;
* the rewrite keeps the longest leading run of literal path segments and drops
  the glob tail, so the searched scope is always a superset of the requested
  one -- evidence stays complete and never overclaims;
* if the reduced prefix does not exist inside the repository root, the original
  string is left untouched and the producer abstains exactly as before.

The producer echoes the scope it really searched in
``answer["scope"]``, so the planner always sees what was covered.

This lives under ``cloud/`` on purpose: the benchmark harness in ``gt_engine/``
is untouched.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

__all__ = [
    "GLOB_CHARACTERS",
    "build_scope_normalizing_model",
    "normalize_literal_search_arguments",
    "normalize_scope",
    "normalize_typed_action",
]

GLOB_CHARACTERS = frozenset("*?[")

_LITERAL_SEARCH_KIND = "exact_literal_search"
_GROUNDTRUTH_TOOL = "groundtruth"


def _contains_glob(value: str) -> bool:
    return any(character in GLOB_CHARACTERS for character in value)


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root)
    except (OSError, ValueError):
        return False
    return True


def normalize_scope(raw_scope: Any, repo_root: Path) -> Any:
    """Return the concrete scope a glob selects, or ``raw_scope`` unchanged.

    ``"src/click/**"`` becomes ``"src/click"``. ``"src/click"`` and
    ``"src/click/core.py"`` are returned unchanged; so is ``"src/clik/**"``,
    whose literal prefix does not exist.
    """
    if not isinstance(raw_scope, str) or not raw_scope or "\x00" in raw_scope:
        return raw_scope
    if not _contains_glob(raw_scope):
        return raw_scope

    root = Path(repo_root).resolve()
    posix = raw_scope.replace("\\", "/")
    if posix.startswith("/") or (len(posix) >= 2 and posix[1] == ":"):
        # Absolute scopes are the producer's business, not ours.
        return raw_scope

    if any(segment == ".." for segment in posix.split("/")):
        # Never reduce an escaping scope; let the producer reject it verbatim.
        return raw_scope

    literal_segments: list[str] = []
    for segment in posix.split("/"):
        if not segment or segment == ".":
            continue
        if _contains_glob(segment):
            break
        literal_segments.append(segment)

    prefix = "/".join(literal_segments) or "."
    candidate = root if prefix == "." else root / prefix
    if not _inside(root, candidate) or not candidate.exists():
        return raw_scope
    return prefix


def _unique(values: Iterable[Any]) -> list[Any]:
    seen: list[Any] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def normalize_literal_search_arguments(
    arguments: Mapping[str, Any], repo_root: Path
) -> dict[str, Any]:
    """Return a copy of ``arguments`` with glob-style ``paths`` made concrete."""
    normalized = dict(arguments)
    raw_paths = normalized.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        return normalized
    normalized["paths"] = _unique(
        normalize_scope(scope, repo_root) for scope in raw_paths
    )
    return normalized


def normalize_typed_action(action: Any, repo_root: Path) -> Any:
    """Return a copy of a ``groundtruth`` literal-search action with sane scopes."""
    if not isinstance(action, Mapping):
        return action
    if action.get("tool_name") != _GROUNDTRUTH_TOOL:
        return action
    gt_action = action.get("gt_action")
    if not isinstance(gt_action, Mapping):
        return action
    if str(gt_action.get("kind") or "") != _LITERAL_SEARCH_KIND:
        return action
    arguments = gt_action.get("arguments")
    if not isinstance(arguments, Mapping):
        return action
    rewritten = normalize_literal_search_arguments(arguments, repo_root)
    if rewritten == dict(arguments):
        return action
    updated_action = dict(action)
    updated_gt_action = dict(gt_action)
    updated_gt_action["arguments"] = rewritten
    updated_action["gt_action"] = updated_gt_action
    return updated_action


def build_scope_normalizing_model(*, repo_root: str | Path, **kwargs: Any) -> Any:
    """Build the typed Mini-SWE model with cloud-side scope normalisation.

    Imported lazily so this module stays importable without ``litellm``.
    """
    from gt_engine.miniswe_typed_actions import GroundTruthLitellmModel

    class ScopeNormalizingGroundTruthModel(GroundTruthLitellmModel):
        """``GroundTruthLitellmModel`` that makes glob scopes concrete."""

        def __init__(self, *, gt_repo_root: str | Path, **model_kwargs: Any) -> None:
            super().__init__(**model_kwargs)
            self.gt_repo_root = Path(gt_repo_root)

        def _parse_actions(self, response: Any) -> list[dict[str, Any]]:
            return [
                normalize_typed_action(action, self.gt_repo_root)
                for action in super()._parse_actions(response)
            ]

    return ScopeNormalizingGroundTruthModel(gt_repo_root=repo_root, **kwargs)
