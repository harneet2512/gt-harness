"""Nothing may append to the journal after the run seals its manifest.

`runtime_receipts.py:806-816` refuses to issue a runtime receipt unless the
sealed `reproducibility_manifest.json` still describes the journal on disk:
same event count, same head, valid, no issues. That is a conservation rule, and
it is load-bearing -- when it fails the full receipt is not written at all and a
degraded one goes out carrying `receipt_issuance:
runtime_receipt_issuance_failed`, with the entire provider-accounting block
missing: input and output tokens, the call counters, both bootstrap counters,
total_cost and the treatment receipt.

Measured on two real rehearsals:

    rehearsal-repair-04   manifest 183, journal 183   receipt issued in full
    rehearsal-repair-05   manifest 186, journal 188   issuance FAILED

The two extra rows in 05 are `graph_build_mode` and `graph_rebuild_embedding`:
a background graph build that was still running when the run finished and
appended after the manifest was sealed. The ordering that allows it is
deliberate at every step. `GTSession.close` calls `close_graph_coordinator`,
which calls `close(wait=False)` on purpose -- an uncooperative in-flight pass
otherwise holds the process open past its deadline and the supervisor turns a
scored submission into an infra timeout. `close(wait=False)` cancels QUEUED work
but a RUNNING build keeps going. The manifest is then sealed later still, in
`miniswe_gt_run`, and that build's rows land after it.

So the run had two correct-looking rules that contradict each other: do not wait
for a running graph build, and do not let anything append after the seal.

RESOLVED, and neither rule was relaxed. The coordinator still does not wait, and
the conservation check is untouched -- relaxing it would have been the same
substitution as editing a rehearsal's expected-error list. What yields is the
one thing that is neither: the two rows are DIAGNOSTICS, written by the build
worker inside `except Exception: pass` under "reporting never fails a rebuild".
They now go through `MiniSweAdapter._append_observation`, which drops them once
`close_graph_coordinator` has sealed the journal and counts what it dropped, so
the loss is a number the run can report rather than a silent hole. A diagnostic
is a cheaper thing to lose than every token, call counter and treatment receipt
on the run's receipt.
"""
from __future__ import annotations

import json

from gt_engine.gt_session import GTMode, GTSession, GTSessionConfig
from gt_engine.miniswe_integration import MiniSweAdapter


def _journal_rows(adapter) -> list[dict]:
    return [json.loads(line)
            for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _seal(adapter) -> dict:
    """What `write_reproducibility_manifest` records for the journal."""
    receipt = adapter.store.receipt()
    return {"event_count": int(receipt["event_count"]),
            "event_head": str(receipt["event_head"])}


def _conserved(sealed: dict, rows: list[dict]) -> bool:
    """The rule from runtime_receipts, applied to a sealed manifest."""
    return bool(rows
                and sealed["event_count"] == len(rows)
                and sealed["event_head"] == rows[-1].get("event_hash"))


def test_a_sealed_manifest_describes_the_journal_it_was_sealed_from(tmp_path):
    """The uncontroversial half: seal with nothing in flight and it holds."""
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = MiniSweAdapter(task_id="seal-quiet", state_dir=tmp_path / "state",
                             repo_root=str(repo), predicates=())
    session = GTSession(
        GTSessionConfig(task_id=adapter.task_id, repo_root=str(repo),
                        state_dir=str(adapter.store.root.parent), mode=GTMode.ADVISORY),
        engine=adapter,
    )
    adapter.store.append("final_state", terminal="submitted")
    session.close("submitted")
    assert _conserved(_seal(adapter), _journal_rows(adapter))


def test_a_late_build_observation_is_dropped_rather_than_breaking_the_receipt(tmp_path):
    """The invariant receipt issuance actually depends on.

    A graph build that outlives `session.close` is reproduced here through the
    same seam the build worker uses, `_append_observation`, which is how
    `graph_build_mode` and `graph_rebuild_embedding` reach the journal. Both
    are diagnostics the worker already treats as best-effort -- their appends
    sit inside `except Exception: pass` under the comment "reporting never
    fails a rebuild" -- so dropping one that arrives after the journal is
    sealed costs a diagnostic, while keeping it costs the entire
    provider-accounting block of the receipt.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = MiniSweAdapter(task_id="seal-race", state_dir=tmp_path / "state",
                             repo_root=str(repo), predicates=())
    session = GTSession(
        GTSessionConfig(task_id=adapter.task_id, repo_root=str(repo),
                        state_dir=str(adapter.store.root.parent), mode=GTMode.ADVISORY),
        engine=adapter,
    )
    adapter.store.append("final_state", terminal="submitted")
    session.close("submitted")
    sealed = _seal(adapter)

    # The in-flight build finishes here, exactly as it did in rehearsal 05,
    # and reports through the seam the real worker uses.
    adapter._append_observation("graph_build_mode", mode="full", reason="")
    adapter._append_observation("graph_rebuild_embedding", state="skipped")

    rows = _journal_rows(adapter)
    assert _conserved(sealed, rows), (
        f"manifest sealed at {sealed['event_count']} but the journal holds "
        f"{len(rows)} rows; the tail is "
        f"{[row.get('event') for row in rows[sealed['event_count']:]]}"
    )


def test_a_build_observation_before_the_close_is_journalled_normally(tmp_path):
    """The half that stops the seal from eating every diagnostic.

    `graph_build_mode` is what distinguishes an amend from a full rebuild in
    the journal, and the last time that distinction was missing a
    caller_coverage improvement was credited to producer code that had never
    executed. Sealing at construction rather than at close would drop every one
    of these silently and no other test would notice -- a mutation that set the
    flag early survived the post-close test on its own.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = MiniSweAdapter(task_id="seal-open", state_dir=tmp_path / "state",
                             repo_root=str(repo), predicates=())
    before = len(_journal_rows(adapter))

    assert adapter._append_observation("graph_build_mode", mode="batch", reason="") is True
    assert adapter._append_observation("graph_rebuild_embedding", state="refreshed") is True

    rows = _journal_rows(adapter)
    assert len(rows) == before + 2
    assert [row["event"] for row in rows[-2:]] == [
        "graph_build_mode", "graph_rebuild_embedding"]
    assert adapter._dropped_observations == 0


def test_the_run_can_tell_how_many_observations_the_seal_dropped(tmp_path):
    """A silent drop is the failure mode this whole file is about."""
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = MiniSweAdapter(task_id="seal-count", state_dir=tmp_path / "state",
                             repo_root=str(repo), predicates=())
    session = GTSession(
        GTSessionConfig(task_id=adapter.task_id, repo_root=str(repo),
                        state_dir=str(adapter.store.root.parent), mode=GTMode.ADVISORY),
        engine=adapter,
    )
    session.close("submitted")
    assert adapter._append_observation("graph_build_mode", mode="full") is False
    assert adapter._append_observation("graph_rebuild_embedding", state="skipped") is False
    assert adapter._dropped_observations == 2
