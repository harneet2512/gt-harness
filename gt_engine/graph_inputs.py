"""Canonical policy for files that can change repository graph semantics."""

from __future__ import annotations

from pathlib import PurePosixPath

from gt_engine.language_registry import is_indexable_source

# This set is deliberately limited to files read by the native indexer's
# resolver passes.  Validation/build metadata is useful to the agent, but it
# cannot participate in graph identity unless the graph builder consumes it.
# Keeping that distinction prevents an unconsumed lockfile from making the
# graph receipt and the source mirror disagree.
GRAPH_METADATA_NAMES = frozenset(
    {
        "cargo.toml",
        "go.mod",
        "jsconfig.json",
        "package.json",
        "tsconfig.json",
    }
)


def is_graph_metadata(path: str) -> bool:
    """Return whether a file can alter parsing, imports, or validation discovery."""

    name = PurePosixPath(str(path or "").replace("\\", "/")).name.lower()
    return name in GRAPH_METADATA_NAMES


def is_graph_input(path: str, content: str | bytes | None = None) -> bool:
    """Return whether a file participates in graph identity."""

    return is_graph_metadata(path) or is_indexable_source(path, content)


__all__ = ["GRAPH_METADATA_NAMES", "is_graph_input", "is_graph_metadata"]
