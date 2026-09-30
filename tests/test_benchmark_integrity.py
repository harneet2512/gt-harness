"""Benchmark integrity: the harness may only use what a developer would have.

GT's measured solve rates are valid only if nothing the agent runs with can see the
benchmark's grading material: the verifier's tests (test.patch, /tests), its pass/fail
node-id lists (config.json f2p/p2p), its reports or rewards (/logs/verifier), or a gold
solution. Error analysis may read those offline; the harness never may. This test fails
if harness code gains a reference to them outside the deny-list that exists to block them.
See docs/benchmarks/benchmark_integrity.md.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = [
    *sorted((ROOT / "gt_engine").rglob("*.py")),
    ROOT / "scripts" / "miniswe_thin_run.py",
    ROOT / "scripts" / "miniswe_gt_run.py",
    ROOT / "eval" / "miniswe_thin_agent.py",
    ROOT / "eval" / "miniswe_agent.py",
]
FORBIDDEN = re.compile(
    r"""["' ]/tests\b|test\.patch|solution\.patch|\bf2p\b|\bp2p\b|f2p_node|p2p_node|fail_to_pass|pass_to_pass"""
    r"""|/logs/verifier|reward\.(?:json|txt)|\bctrf\b|grader\.py|["'/]solution/|gold[_ ]?patch""",
    re.IGNORECASE,
)
# The one place that names these on purpose: the deny-list refusing them.
ALLOWED = {("gt_engine/persistent_plan/bootstrap.py", "_FORBIDDEN_COMMAND_RE")}


def _hits():
    for path in HARNESS:
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for number, line in enumerate(lines, 1):
            if not FORBIDDEN.search(line) or line.lstrip().startswith("#"):
                continue
            window = "\n".join(lines[max(0, number - 4):number])
            if any(rel == allowed_path and marker in window for allowed_path, marker in ALLOWED):
                continue
            yield f"{rel}:{number}: {line.strip()[:120]}"


def test_harness_code_never_references_benchmark_grading_material():
    hits = list(_hits())
    assert not hits, "harness references benchmark grading material:\n" + "\n".join(hits)


def test_the_guard_itself_detects_a_reference(tmp_path):
    probe = 'config = json.load(open("/app/tests/config.json"))["p2p_node_ids"]'
    assert FORBIDDEN.search(probe)
