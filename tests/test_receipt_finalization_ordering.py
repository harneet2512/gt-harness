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

So the run has two correct-looking rules that contradict each other: do not wait
for a running graph build, and do not let anything append after the seal. This
test states the second one. It is xfail because the contradiction is real and
resolving it is a design decision about which rule yields -- not something to
paper over by relaxing the conservation check, which would be the same
substitution as editing a rehearsal's expected-error list.
"""
from __future__ import annotations

import json

import pytest

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


@pytest.mark.xfail(
    reason="measured on rehearsal-repair-05: a background graph build still "
           "running at close appends graph_build_mode and "
           "graph_rebuild_embedding after the manifest is sealed, giving "
           "manifest 186 against journal 188, so runtime receipt issuance "
           "fails with event_journal_conservation_failed and the receipt loses "
           "its whole provider-accounting block. close_graph_coordinator uses "
           "close(wait=False) deliberately, so the two rules genuinely "
           "conflict and the resolution is a design decision.",
    strict=False,
)
def test_nothing_appends_to_the_journal_after_the_session_closes(tmp_path):
    """The invariant receipt issuance actually depends on.

    A graph build that outlives `session.close` is reproduced here by appending
    exactly the two rows rehearsal 05 recorded, after the close and after the
    seal. Nothing is faked about the consequence: the same comparison the
    receipt makes is applied to the same sealed shape the manifest carries.
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

    # The in-flight build finishes here, exactly as it did in rehearsal 05.
    adapter.store.append("graph_build_mode", build_mode="full")
    adapter.store.append("graph_rebuild_embedding", refreshed=0)

    rows = _journal_rows(adapter)
    assert _conserved(sealed, rows), (
        f"manifest sealed at {sealed['event_count']} but the journal holds "
        f"{len(rows)} rows; the tail is "
        f"{[row.get('event') for row in rows[sealed['event_count']:]]}"
    )
