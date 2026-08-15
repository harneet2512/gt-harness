from __future__ import annotations

from gt_engine.diagnostics import extract_diagnostic_anchors


def test_extracts_repo_bound_python_and_javascript_diagnostic_anchors():
    output = """
Traceback (most recent call last):
  File "/app/src/worker.py", line 41, in process_item
    raise ValueError("bad")
ValueError: bad
    at render (/app/web/view.ts:17:9)
"""

    anchors = extract_diagnostic_anchors(
        output,
        repository_paths=("src/worker.py", "web/view.ts", "outside.py"),
        cwd="/app",
    )

    assert [(row.path, row.line, row.symbol, row.kind) for row in anchors] == [
        ("src/worker.py", 41, "process_item", "python_traceback"),
        ("web/view.ts", 17, "render", "javascript_stack"),
    ]


def test_extracts_compiler_locations_but_rejects_non_repository_paths():
    output = """
src/main.rs:12:7: error[E0425]: cannot find value `missing`
pkg/worker.go:9:2: undefined: helper
/tmp/generated.rs:1:1: error: irrelevant
"""

    anchors = extract_diagnostic_anchors(
        output,
        repository_paths=("src/main.rs", "pkg/worker.go"),
        cwd="/app",
    )

    assert [(row.path, row.line, row.kind) for row in anchors] == [
        ("src/main.rs", 12, "compiler_location"),
        ("pkg/worker.go", 9, "compiler_location"),
    ]


def test_diagnostic_extraction_abstains_on_unverified_or_malformed_paths():
    anchors = extract_diagnostic_anchors(
        "../secret.py:4: error\nunknown.py:7: error\nnot a location",
        repository_paths=("src/known.py",),
        cwd="/app",
    )

    assert anchors == ()
