"""The requirement ledger keeps every normative prompt line verbatim.

The awilix fixture is the public task prompt only. No test file, no
fail-to-pass list and no solution patch is read here or anywhere in the
persistent-plan code: the ledger is derived from what the agent already sees.
"""
from __future__ import annotations

from pathlib import Path

from gt_engine.persistent_plan.ledger import (
    build_requirement_ledger,
    ledger_only_contract,
    row_id_for,
)
from gt_engine.task_contract import extract_task_contract
from gt_engine.verification_contract import compile_obligation_predicates

FIXTURE = Path(__file__).parent / "fixtures" / "persistent_plan" / "awilix_instruction.md"


def _awilix() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _texts(ledger) -> list[str]:
    return [row.text for row in ledger.rows]


def test_a_line_the_sentence_extractor_merged_becomes_its_own_row():
    """Every source line is pointable, whatever the extractor did with it.

    The generic property: no ledger row spans two source lines. This fixture
    exercises a line ``extract_task_contract`` merged into a run-on obligation
    beginning with a different assumption, so nothing tracked it on its own.
    """
    ledger = build_requirement_ledger(_awilix(), extract_task_contract(_awilix()))
    line = (
        "Scoped containers can be initialized independently; parent "
        "container's singletons are not reinitialized"
    )
    assert line in _texts(ledger)
    row = ledger.by_id(row_id_for(line))
    assert row is not None and row.section == "assumptions"


def test_each_assumption_is_a_separate_row():
    ledger = build_requirement_ledger(_awilix())
    assumptions = [row for row in ledger.rows if row.section == "assumptions"]
    assert len(assumptions) == 5
    assert assumptions[0].text.startswith("`initialize()` is idempotent")
    assert assumptions[-1].text.startswith("Works with both `asFunction()`")


def test_lines_without_a_directive_verb_survive():
    """Requirement lines carry no ``must``/``should``; the extractor needs one."""
    ledger = build_requirement_ledger(_awilix())
    texts = _texts(ledger)
    assert "console.log(result.metrics.database.level)" in texts
    assert "console.log(result.totalDuration)" in texts
    assert "const result = await container.initialize({ concurrency: 5 })" in texts


def test_structural_only_lines_are_skipped_with_a_reason():
    ledger = build_requirement_ledger(_awilix())
    assert "})" not in _texts(ledger)
    assert any(reason == "structural_only" for _line, reason in ledger.skipped)


def test_the_commit_instruction_is_a_row():
    """A run that never commits grades against a pristine base, so this is
    normative even though it is process rather than behaviour."""
    ledger = build_requirement_ledger(_awilix())
    assert any(row.text.startswith("IMPORTANT:") for row in ledger.rows)


def test_rows_are_verbatim_text_from_one_line():
    """A row is a byte-for-byte piece of a single source line, never a rewrite."""
    text = _awilix()
    lines = [" ".join(line.split()) for line in text.splitlines()]
    ledger = build_requirement_ledger(text)
    for row in ledger.rows:
        home = lines[row.line_no - 1]
        assert row.text in home, row.text
        assert row.text == row.text.strip()


def test_rows_link_to_the_obligations_that_swallowed_them():
    contract = extract_task_contract(_awilix())
    ledger = build_requirement_ledger(_awilix(), contract)
    linked = [row for row in ledger.rows if row.obligation_ids]
    assert linked, "no row linked to any extracted obligation"
    known = {item.obligation_id for item in contract.obligations}
    for row in linked:
        assert set(row.obligation_ids) <= known


def test_ledger_only_rows_compile_to_predicates():
    contract = extract_task_contract(_awilix())
    ledger = build_requirement_ledger(_awilix(), contract)
    assert ledger.ledger_only, "fixture must exercise the ledger-only path"
    synthetic = ledger_only_contract(ledger, _awilix())
    compiled = compile_obligation_predicates(synthetic)
    assert set(compiled) == {
        item.obligation_id for item in synthetic.obligations
    }
    for obligation_id, predicate in compiled.items():
        assert predicate.predicate_id
        assert predicate.obligation_id == obligation_id


def test_fenced_examples_and_non_normative_sections_are_excluded():
    text = (
        "**Background**\n"
        "This repository is a container library.\n"
        "\n"
        "**Requirements**\n"
        "The loader must retry twice.\n"
        "\n"
        "```js\n"
        "example.only.in.a.fence()\n"
        "```\n"
    )
    ledger = build_requirement_ledger(text)
    texts = _texts(ledger)
    assert "The loader must retry twice." in texts
    assert "This repository is a container library." not in texts
    assert "example.only.in.a.fence()" not in texts
    assert ledger.fenced_lines == 1


def test_duplicate_lines_collapse_once():
    text = "The cache must expire.\nThe cache must expire.\n"
    ledger = build_requirement_ledger(text)
    assert len(ledger.rows) == 1
    assert any(reason == "duplicate" for _line, reason in ledger.skipped)


def test_row_ids_are_stable_across_calls():
    first = build_requirement_ledger(_awilix())
    second = build_requirement_ledger(_awilix())
    assert [row.row_id for row in first.rows] == [row.row_id for row in second.rows]


def test_empty_prompt_yields_an_empty_ledger():
    ledger = build_requirement_ledger("")
    assert len(ledger) == 0
    assert ledger.counts()["rows"] == 0


def test_sentence_split_is_lossless_within_a_line():
    """Splitting inside a line must rejoin to the line, so nothing is lost.

    This is the invariant that separates a safe split from the cross-line merge
    that hides a requirement inside a neighbour's sentence.
    """
    from gt_engine.persistent_plan.ledger import _split_sentences

    for line in (
        "First clause here. Second clause follows. Third ends it.",
        "console.log(result.metrics.database.level)",
        "`initialize()` is idempotent, calling it twice returns immediately",
        "Version 1.2 shipped. It works.",
    ):
        assert " ".join(_split_sentences(line)) == line


def test_a_multi_sentence_line_becomes_several_rows():
    text = (
        "If any initializer throws, the container disposes services. "
        "When a failure occurs, in-flight initializers complete first. "
        "Errors from disposers do not override the original error.\n"
    )
    ledger = build_requirement_ledger(text)
    assert len(ledger.rows) == 3
    assert {row.line_no for row in ledger.rows} == {1}
    assert [row.sentence_index for row in ledger.rows] == [0, 1, 2]
    assert ledger.rows[1].text.startswith("When a failure occurs")


def test_no_row_spans_two_source_lines():
    """The property that would have made the merge visible."""
    text = _awilix()
    ledger = build_requirement_ledger(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    for row in ledger.rows:
        assert any(row.text in line for line in lines), row.text


def test_linking_preserves_every_row_field():
    """Relinking rebuilds rows; it must not drop one on the way through."""
    text = _awilix()
    unlinked = build_requirement_ledger(text)
    linked = build_requirement_ledger(text, extract_task_contract(text))
    assert len(unlinked.rows) == len(linked.rows)
    for before, after in zip(unlinked.rows, linked.rows, strict=True):
        assert before.row_id == after.row_id
        assert before.text == after.text
        assert before.line_no == after.line_no
        assert before.sentence_index == after.sentence_index
        assert before.section == after.section
        assert before.shape == after.shape
        assert before.subjects == after.subjects
        assert before.tokens == after.tokens


def test_submission_boilerplate_is_a_process_row_not_a_test_check():
    """The benchmark's submission suffix is normative but not test-provable.

    Smoke-20 bound it to a test file ("npm test ...intersections.test.ts"),
    where it could never pass for the right reason - an unprovable row that
    held `verified` unreachable on every task. It stays a ledger row, but the
    deterministic plan must classify it as a workspace-envelope check, not a
    bound test.
    """
    from gt_engine.persistent_plan import PlanInputs, BaselineResult, AnchorResult
    from gt_engine.persistent_plan.deterministic import build_deterministic_plan

    issue = (
        "## Required behavior\n"
        "1. Parent cancellation must cascade to all descendant handles.\n"
        "2. Child cancellation must not cancel its parent.\n"
        "\n"
        "IMPORTANT: Please work on this in a new branch from main and "
        "commit everything when you are done.\n"
    )
    ledger = build_requirement_ledger(issue)
    boiler = [row for row in ledger.rows
              if "new branch" in row.text or "when you are done" in row.text]
    assert boiler, "the submission directive is normative and must stay a row"

    inputs = PlanInputs(
        ledger=ledger,
        anchors=AnchorResult(anchors={}, edit_order=()),
        baseline=BaselineResult(status="captured", command=("npm", "test")),
        source_revision="rev", graph_revision="g",
        observed_source_revision="rev",
        covering={row.row_id: ("tests/cancel.test.ts",) for row in ledger.rows},
    )
    plan = build_deterministic_plan(inputs)
    for row in plan.rows:
        if row.row_id == boiler[0].row_id:
            assert row.verification_kind == "process"
            assert row.verification_command == ""
        else:
            assert row.verification_command


def test_process_directive_needs_two_markers():
    """A single workflow phrase inside a real requirement must not drop it."""
    from gt_engine.task_contract import _is_process_directive

    assert _is_process_directive(
        "IMPORTANT: Please work on this in a new branch from main "
        "and commit everything when you are done."
    )
    assert _is_process_directive("work on this in a new branch")
    assert not _is_process_directive("the CLI must open a pull request when tests pass")
    assert not _is_process_directive("commit everything when tests pass")
    assert not _is_process_directive(
        "Module::evaluate_with_evaluation must reject with the same reason"
    )


_DYNACONF_PROMPT = """\
Please solve this issue: [bug] using `@merge` with comma separated values, does not infer type

```py
settings = Dynaconf(
    data=[1,2,3]
)
```

```bash
APP_DATA="@merge 4,5,6" dynaconf list -k DATA
```

Result

```
DATA<list>: [1, 2, 3, "4", "5", "6"]
```

Expected

```
DATA<list>: [1, 2, 3, 4, 5, 6]
```

You are working in the `dynaconf/dynaconf` repository, checked out at `/testbed`. Investigate the issue described above and modify the code under `/testbed` to resolve it.
"""


def test_section_markers_and_harness_preamble_do_not_mint_rows():
    """Run 34919574013 (dynaconf-1241): bare-word section markers "Result" and
    "Expected" and the SWE-bench harness preamble all minted plan rows, and
    default_check bound the whole suite to them - unprovable rows that kept
    verified=false on a solved task. Markers set the section; preamble is
    workflow noise; only the real requirement survives."""
    from gt_engine.persistent_plan.ledger import build_requirement_ledger

    ledger = build_requirement_ledger(_DYNACONF_PROMPT)
    texts = [row.text for row in ledger.rows]
    assert len(ledger.rows) == 1
    assert "@merge" in texts[0]
    assert all(t not in {"Result", "Expected"} for t in texts)
    assert not any("working in" in t or "Investigate the issue" in t
                   for t in texts)
    # Markers still classify the lines that follow them as their section
    # (here, fenced blocks - so the only surviving row is in no section or
    # the opening one); the skipped journal records both markers.
    reasons = {reason for _line, reason in ledger.skipped}
    assert "section_marker" in reasons


def test_section_marker_updates_section_inside_non_normative():
    """A marker inside a non-normative section must still flip the section,
    or every following line is skipped under the wrong gate."""
    from gt_engine.persistent_plan.ledger import build_requirement_ledger

    ledger = build_requirement_ledger(
        "Background\nshared history only\n\n"
        "Expected\nThe parser must infer list types\n"
    )
    texts = [row.text for row in ledger.rows]
    assert texts == ["The parser must infer list types"]
    assert ledger.rows[0].section == "expected"


def test_harness_preamble_lines_are_workflow_noise():
    from gt_engine.task_contract import _is_workflow_noise

    assert _is_workflow_noise(
        "You are working in the `dynaconf/dynaconf` repository, "
        "checked out at `/testbed`.")
    assert _is_workflow_noise(
        "Investigate the issue described above and modify the code under "
        "`/testbed` to resolve it.")
    # A real requirement that merely mentions a repository must survive.
    assert not _is_workflow_noise(
        "The repository loader must keep `settings_file` absolute.")
