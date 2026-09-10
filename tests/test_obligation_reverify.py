"""The post-edit reverification pass, which had never once done its work.

`obligation_reverified` produced 0 rows in every run ever recorded, while the
same runs discarded proofs 33 times and the model paid roughly ten steps to
re-establish each one. The pass was not unreachable: it ran on every edit and
returned at its first line, because candidacy and invalidation were decided by
two different rules that never intersected.

Candidacy came from `_affected_predicate_ids`, which requires the obligation's
English text to literally quote a filename (`_PATH_RE` over the obligation
prose). Invalidation came from the receipt dependency footprint, which is
`conservative_execution_footprint` and matches every edit. So proofs were
destroyed by a rule whose members were never eligible for the rescue.

There was no test of any kind for this path. These are it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from gt_engine.miniswe_controller import Predicate, PredicateStatus
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.runtime_observation import capture_workspace, diff_workspace
from gt_engine.task_contract import Obligation, TaskContract
from gt_engine.verification_contract import compile_obligation_predicates


def _rows(adapter: MiniSweAdapter, event: str) -> list[dict]:
    lines = Path(adapter.store.path).read_text(encoding="utf-8").splitlines()
    return [row for line in lines if (row := json.loads(line)).get("event") == event]


def _proven_adapter(tmp_path: Path) -> tuple[MiniSweAdapter, str, str, Path]:
    """A repository with one obligation proven GREEN by a re-runnable command.

    The obligation text quotes no filename, which is the normal case for an
    obligation compiled from issue prose, and is exactly why its scope is
    empty and it was never a reverification candidate.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "docs").mkdir()
    (repo / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "docs" / "readme.md").write_text("notes\n", encoding="utf-8")
    (repo / "tests" / "test_consumer.py").write_text(
        "import unittest\n"
        "from src.consumer import value\n"
        "class ConsumerAnswerTest(unittest.TestCase):\n"
        "    def test_consumer_answer(self):\n"
        "        print('Consumer answer check')\n"
        "        self.assertEqual(value(), 1)\n"
        "        self.assertIn('1', str(value()))\n"
        "        import hashlib\n"
        "        print('GT_SEMANTIC_ASSERT relation=contains literal_sha256=' +\n"
        "              hashlib.sha256(b'1').hexdigest() + ' result=pass')\n",
        encoding="utf-8",
    )
    (repo / "src" / "consumer.py").write_text(
        "from .helper import answer\ndef value(): return answer\n", encoding="utf-8"
    )
    (repo / "src" / "helper.py").write_text("answer = 1\n", encoding="utf-8")

    contract = TaskContract(
        "code_behavior",
        (Obligation("consumer", "Consumer answer must contain '1'.", "task"),),
    )
    compiled = compile_obligation_predicates(contract)["consumer"]
    adapter = MiniSweAdapter(
        task_id="reverify", state_dir=tmp_path / "state",
        predicates=[Predicate(compiled.predicate_id, compiled.obligation_id)],
        contract=contract, repo_root=repo,
    )
    adapter.start_task()
    command = f'"{sys.executable}" -B -m unittest tests.test_consumer -v'
    first = subprocess.run(command, cwd=repo, shell=True, capture_output=True,
                           text=True, timeout=60)
    output = first.stdout + first.stderr
    assert first.returncode == 0, output
    assert adapter.evaluate_observation(
        command, output, returncode=first.returncode, action_index=1
    ) == (compiled.predicate_id,)
    assert adapter.predicate_status(compiled.predicate_id) is PredicateStatus.GREEN
    return adapter, compiled.predicate_id, command, repo


def _edit(adapter: MiniSweAdapter, repo: Path, relative: str, text: str) -> tuple[str, ...]:
    before = capture_workspace(repo)
    (repo / relative).write_text(text, encoding="utf-8")
    transaction = diff_workspace(before, capture_workspace(repo), action_id=2,
                                 command=f"edit {relative}")
    adapter.note_edit(transaction.changed_paths)
    return transaction.changed_paths


def test_an_unrelated_edit_invalidates_without_replaying_shell(tmp_path, monkeypatch):
    """Unknown dependencies invalidate; only registered argv checks may rerun."""
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    adapter, predicate_id, _command, repo = _proven_adapter(tmp_path)

    _edit(adapter, repo, "docs/readme.md", "notes, revised\n")

    reverified = _rows(adapter, "obligation_reverified")
    assert reverified, "the reverification pass produced no row at all"
    row = reverified[-1]
    assert row["skipped"] == "no_registered_check"
    assert row["candidates"] == [predicate_id]
    assert row["commands_run"] == 0
    assert row["preserved"] == []
    assert adapter.predicate_status(predicate_id) is PredicateStatus.UNKNOWN
    assert predicate_id in adapter.unmet_predicates


def test_a_breaking_edit_leaves_the_proof_discarded(tmp_path, monkeypatch):
    """Correct-or-quiet: this can preserve a proof, never invent one."""
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    adapter, predicate_id, _command, repo = _proven_adapter(tmp_path)

    _edit(adapter, repo, "src/helper.py", "answer = 2\n")

    row = _rows(adapter, "obligation_reverified")[-1]
    assert row["commands_run"] == 0
    assert row["preserved"] == []
    assert adapter.predicate_status(predicate_id) is PredicateStatus.UNKNOWN


def test_a_pass_that_does_nothing_says_so(tmp_path, monkeypatch):
    """A silent return is why zero rows read as "nothing needed re-proving".

    The row is what separates quiet from broken, and its absence is what let
    this survive every run without anyone being able to see it.
    """
    monkeypatch.delenv("GT_VERIFY_EXECUTE", raising=False)
    adapter, predicate_id, _command, repo = _proven_adapter(tmp_path)

    _edit(adapter, repo, "docs/readme.md", "notes, revised\n")

    row = _rows(adapter, "obligation_reverified")[-1]
    assert row["skipped"] == "no_registered_check"
    assert row["commands_run"] == 0
    assert row["candidates"] == [predicate_id]


def test_invalidation_reports_what_was_actually_discarded(tmp_path, monkeypatch):
    """This row used to claim the opposite of what happened.

    `proven_discarded`/`proven_surviving` were computed against the scope-match
    set, which is empty for an obligation whose text quotes no filename. The
    row therefore reported every proof as surviving an edit that the controller
    had already wiped. It is now computed against the set the controller
    actually applied, with the scope match retained beside it so the
    narrow-versus-total question the row was added to settle is still
    answerable from one row.
    """
    monkeypatch.delenv("GT_VERIFY_EXECUTE", raising=False)
    adapter, predicate_id, _command, repo = _proven_adapter(tmp_path)

    _edit(adapter, repo, "docs/readme.md", "notes, revised\n")

    row = _rows(adapter, "obligation_invalidation")[-1]
    assert row["proven_before"] == [predicate_id]
    assert row["invalidated"] == [predicate_id]
    assert row["proven_discarded"] == [predicate_id]
    assert row["proven_surviving"] == []
    # The obligation's text quotes no filename, so nothing matched by scope --
    # which is precisely the divergence that made the pass unreachable.
    assert row["scope_matched"] == []
    assert adapter.predicate_status(predicate_id) is PredicateStatus.UNKNOWN


def test_note_edit_returns_the_set_it_applied(tmp_path):
    """The controller's return value is the ground truth about the edit.

    `invalidate` is a request; the footprint check below it adds to that set.
    A caller that assumed its own request was applied described the edit
    wrongly, which is what the two tests above pin from the outside.
    """
    adapter, predicate_id, _command, _repo = _proven_adapter(tmp_path)

    applied = super(MiniSweAdapter, adapter).note_edit(("docs/readme.md",), invalidate=())

    assert applied == frozenset({predicate_id})


def test_no_proofs_means_a_named_skip_not_silence(tmp_path, monkeypatch):
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    adapter = MiniSweAdapter(task_id="reverify-empty", state_dir=tmp_path / "state",
                             predicates=[], repo_root=repo)
    adapter.start_task()

    adapter.note_edit(("app.py",))

    row = _rows(adapter, "obligation_reverified")[-1]
    assert row["skipped"] == "no_registered_check"
    assert row["preserved"] == []
