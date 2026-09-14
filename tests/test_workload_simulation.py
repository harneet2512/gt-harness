"""Deterministic workload simulation: sustained churn, cgroup pressure and
promotion races through the real adapter, asserted at the capability layer.

Run 34849119441 (the paid gate-one) proved the provider-free pyramid's
blind spot: every defect it surfaced was an interaction only sustained
load produces - edit cadence against ~20-minute promotion legs, resident
LSP servers against the cgroup guard, serving boundaries against dead
producer spawns, a salvaged tier invisible to a terminal-only reporter.

These scenarios replay that state space deterministically. The stub
seams are the process boundaries the unit tests already use - producer
outcomes, the LSP schedule factory, the merge computation, the wall
clock. Everything between is the production path: refusal
classification, the defer windows, escalation, the real
ProviderWaitScheduler thread, salvage drain and journal, and the
capability reporter reading the run's own journal.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from gt_engine.indexer import IndexBuildReceipt, IndexBuildStatus
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.run_diagnostics import DiagnosticCode
from gt_engine.runtime_observation import capture_workspace, diff_workspace


class _LegHandle:
    """A promotion leg whose completion the script controls.

    A real leg runs ~20 minutes while boundaries keep landing; releasing
    on demand is what makes the obsolete race deterministic instead of
    lucky.
    """

    def __init__(self, task_id: str, receipt: dict) -> None:
        self.task_id = task_id
        self._receipt = receipt
        self._released = False

    def release(self) -> None:
        self._released = True

    @property
    def done(self) -> bool:
        return self._released

    def terminal_receipt(self, *, timeout=None):
        return dict(self._receipt)


class _Sim:
    """Drives a real MiniSweAdapter through a scripted workload clock.

    Pressure is a script flag the producer stubs read at spawn time: a
    resident LSP leg is what squeezed the cgroup in the live run, so
    scenarios toggle it around leg lifetimes. Graph files are minted
    with the production convention - revision == sha256 of the bytes,
    carried in a sibling .manifest.json - which is what makes
    graph_publication rows and the salvage join read exactly like the
    paid run's.
    """

    def __init__(self, monkeypatch, tmp_path):
        self.tmp_path = tmp_path
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "mod.py").write_text("value = 1\n", encoding="utf-8")
        self.repo = repo
        self._pre = capture_workspace(repo)
        self._action = 0

        graph = tmp_path / "graph.db"
        graph.write_bytes(b"initial-graph-bytes")
        self._publish_manifest(graph)

        self.adapter = MiniSweAdapter(
            task_id="sim-task", state_dir=tmp_path / "state", predicates=[],
            repo_root=str(repo), graph_db=str(graph),
        )
        self.clock = {"now": 1000.0}
        monkeypatch.setattr(
            "gt_engine.miniswe_integration.time.monotonic",
            lambda: self.clock["now"],
        )

        self.pressure = False
        self.amend_spawns: list[float] = []
        self.build_spawns: list[float] = []
        self.leg_offers: list[str] = []
        self.in_flight: dict[str, _LegHandle] = {}
        self._seq = 0

        self._install_producer_stubs(monkeypatch)
        self._install_lsp_stubs(monkeypatch)

        self.adapter.start_task()
        self.adapter.record_repository_snapshot(
            capture_workspace(repo), boundary="task_start"
        )

    # -- stubs at the process boundaries ----------------------------------

    @staticmethod
    def _publish_manifest(
        graph: Path, *, parent_sha: str | None = None,
        build_mode: str | None = None,
    ) -> str:
        sha = hashlib.sha256(graph.read_bytes()).hexdigest()
        # The producer's own manifest fields (indexer.py:1481-1483): the
        # capability report walks parent_graph_sha256 ancestry to decide
        # whether a minted tier reached the adopted graph.
        payload: dict[str, object] = {
            "graph_sha256": sha, "graph_revision": sha
        }
        if parent_sha:
            payload["parent_graph_sha256"] = parent_sha
        if build_mode:
            payload["build_mode"] = build_mode
        graph.with_suffix(".manifest.json").write_text(
            json.dumps(payload), encoding="utf-8",
        )
        return sha

    def _new_graph(
        self, tag: str, *, parent_sha: str | None = None,
        build_mode: str | None = None,
    ) -> tuple[str, str]:
        self._seq += 1
        graph = self.tmp_path / f"{tag}-{self._seq}.db"
        graph.write_bytes(f"{tag}-graph-{self._seq}".encode())
        return str(graph), self._publish_manifest(
            graph, parent_sha=parent_sha, build_mode=build_mode
        )

    def _install_producer_stubs(self, monkeypatch) -> None:
        def amend(root, *, parent_graph, changed_paths, **kwargs):
            self.amend_spawns.append((self.clock["now"], self.pressure))
            if self.pressure:
                return None, (
                    "amend_failed:GT_INDEX_MEMORY_GUARD_TRIGGERED:exit=-9:"
                    "stderr=Pass 1: discovering files"
                ), ()
            parent_sha = hashlib.sha256(
                Path(parent_graph).read_bytes()
            ).hexdigest()
            graph, _sha = self._new_graph(
                "amended", parent_sha=parent_sha, build_mode="incremental"
            )
            return graph, "", ({"path": "mod.py", "updated": 1},)

        def build(root, **kwargs):
            self.build_spawns.append(self.clock["now"])
            if self.pressure:
                return IndexBuildReceipt(
                    IndexBuildStatus.BUILD_FAILED,
                    error_type="GT_INDEX_MEMORY_GUARD_TRIGGERED",
                    error_diagnostic="guard_killed",
                )
            graph, _sha = self._new_graph("rebuilt", build_mode="full")
            return IndexBuildReceipt(
                IndexBuildStatus.BUILT, graph_db=graph,
                graph_revision=hashlib.sha256(
                    Path(graph).read_bytes()
                ).hexdigest(),
                analysis_state="complete",
                source_revision=str(self.adapter.engine_state.source_revision),
            )

        monkeypatch.setattr(
            "gt_engine.indexer._ensure_index_incremental_unlocked", amend
        )
        monkeypatch.setattr(
            "gt_engine.indexer.ensure_index_with_receipt", build
        )
        monkeypatch.setattr(
            "gt_engine.indexer._receipt_for_published_graph",
            lambda graph_path, **kwargs: IndexBuildReceipt(
                IndexBuildStatus.BUILT_CORE_ONLY, graph_db=graph_path,
                graph_revision=hashlib.sha256(
                    Path(graph_path).read_bytes()
                ).hexdigest(),
                analysis_state="not_run", build_mode="incremental",
                source_revision=str(kwargs.get("source_revision") or ""),
            ),
        )

    def _install_lsp_stubs(self, monkeypatch) -> None:
        scheduler = type(
            "_Scheduler", (),
            {"_handles": [], "close": lambda self, **kw: None},
        )()
        self.adapter._lsp_scheduler = scheduler

        def leg_factory(request, base):
            self._seq += 1
            task_id = f"leg-{self._seq}"
            candidate = self.tmp_path / f"candidate-{self._seq}.db"
            connection = sqlite3.connect(candidate)
            connection.execute("CREATE TABLE mutations (k)")
            connection.commit()
            connection.close()
            candidate_sha = self._publish_manifest(candidate)
            receipt = {
                "terminal": True, "status": "succeeded", "publishable": True,
                "verified": 0, "corrected": 1, "selected": 6, "deleted": 0,
                "output_graph_sha256": candidate_sha,
                "source_revision": request.source_revision,
                "input_graph_revision": base.graph_revision,
                "candidate_path": str(candidate), "task_id": task_id,
                "language_receipts": {
                    "python": {"selection_complete": True}
                },
            }
            handle = _LegHandle(task_id, receipt)
            scheduler._handles.append(handle)
            # The fields the real scheduler stamps on the adapter
            # (miniswe_integration.py:3788-3794): the drain-side salvage
            # path reads both back.
            self.adapter._lsp_requests[task_id] = SimpleNamespace(
                repository_root=str(self.repo),
                source_revision=request.source_revision,
                graph_revision=base.graph_revision,
                candidate_path=str(candidate),
                repository_snapshot_sha256="0" * 64,
            )
            self.adapter._lsp_epochs[task_id] = self.adapter._edit_epoch
            self.adapter.store.append(
                "lsp_promotion_scheduled", task_id=task_id,
                source_revision=request.source_revision,
                graph_revision=base.graph_revision,
                repository_snapshot_sha256="0" * 64,
            )
            self.in_flight[task_id] = handle
            self.leg_offers.append(base.graph_revision)
            return handle

        monkeypatch.setattr(
            self.adapter, "_schedule_lsp_candidate", leg_factory
        )

        def fake_salvage_work(*, base_graph_path, staged_candidate,
                              stale_paths, scheduled):
            # The merge computation itself is covered by test_scoped_merge;
            # what this scenario exercises is everything around it - the
            # enqueue, the worker-thread drain, the journal row, the
            # drain-side CAS publish.
            def work():
                live_sha = hashlib.sha256(
                    Path(
                        self.adapter.engine_state.graph_path
                    ).read_bytes()
                ).hexdigest()
                graph, sha = self._new_graph(
                    "salvaged", parent_sha=live_sha, build_mode="merge"
                )
                return {
                    "outcome": "merged",
                    "live_graph_path": self.adapter.engine_state.graph_path,
                    "graph_path": graph,
                    "graph_revision": sha,
                    "source_revision": (
                        self.adapter.engine_state.source_revision
                    ),
                    "applied": 7, "inserted": 1, "updated": 6,
                    "skipped_stale": 0, "skipped_diverged": 0,
                }

            return work

        monkeypatch.setattr(
            self.adapter, "_salvage_work", fake_salvage_work
        )

    # -- the scripted boundaries -------------------------------------------

    def edit(self, *paths: str, advance: float = 0.0) -> None:
        """A real edit transaction - the epoch and dirty-set witness."""
        self.clock["now"] += advance
        for path in paths:
            target = self.repo / path
            target.write_text(
                target.read_text(encoding="utf-8") + "# churn\n",
                encoding="utf-8",
            )
        post = capture_workspace(self.repo)
        self._action += 1
        transaction = diff_workspace(
            self._pre, post, action_id=self._action,
            command=f"edit {' '.join(paths)}",
        )
        self._pre = post
        self.adapter.record_edit_transaction(transaction)

    def boundary(self, advance: float = 0.0) -> bool:
        """A serving boundary: snapshot, then amend / publish / schedule.

        The snapshot is what a promotion leg freezes its source revision
        from - the runtime records one per action boundary, so a sim that
        snapshots only at init freezes every request on a stale revision
        and no leg can ever publish.
        """
        self.clock["now"] += advance
        self.adapter.record_repository_snapshot(
            capture_workspace(self.repo), boundary="serving"
        )
        return self.adapter.refresh_graph()

    def dirty(self, *paths: str, advance: float = 0.0) -> None:
        """An unwitnessed edit marking - the serving-boundary amend site.

        ``record_edit_transaction`` fires the synchronous amend inline;
        ``note_edit`` only marks the overlay dirty, so the next
        ``refresh_graph`` is the spawn - the two refusal paths the live
        run exercised are driven separately on purpose.
        """
        self.clock["now"] += advance
        self.adapter.note_edit(list(paths))

    def provider_wait(self) -> None:
        """The agent's network block: launch pending work, drain at return."""
        self.adapter.provider_wait_begin()
        scheduler = self.adapter._wait_scheduler
        deadline = time.perf_counter() + 5.0
        while (
            scheduler is not None
            and scheduler._running
            and time.perf_counter() < deadline
        ):
            time.sleep(0.005)
        self.adapter.provider_wait_end()

    def finish_legs(self) -> None:
        """Complete every in-flight leg; the next boundary drains them."""
        for task_id, handle in list(self.in_flight.items()):
            handle.release()
            self.in_flight.pop(task_id)

    # -- the run's own evidence --------------------------------------------

    def journal(self, event: str) -> list[dict]:
        return [
            row for row in (
                json.loads(line)
                for line in self.adapter.store.path.read_text().splitlines()
            )
            if row.get("event") == event
        ]

    def refresh_events(self) -> list:
        return [
            event for event in self.adapter.diagnostics._events
            if event.code is DiagnosticCode.GT_GRAPH_REFRESH_FAILED
        ]

    def capabilities(self) -> dict:
        """The mandatory capability rows over the run's REAL journal.

        The reporter reads the store the adapter wrote - terminal blobs,
        salvage rows, publications - not a journal written by hand.
        """
        from gt_engine.gt_session import GTMode, GTSession

        stub = SimpleNamespace(
            _engine=SimpleNamespace(store=self.adapter.store),
            disabled=False, disabled_stage="", mode=GTMode.ENFORCED,
        )
        return {
            name: (str(state), evidence)
            for name, state, evidence, _ in
            GTSession._mandatory_capability_rows(stub)
        }


def _drive_churn(
    sim: "_Sim", *, ticks: int, cadence: float,
    note_edit_mod: int = 5, release_ticks: set[int] = frozenset(),
    pressure_fn=None,
) -> None:
    """One scripted workload tick loop shared by every storm scenario.

    Each tick is one edit (transaction or bare dirty-marking), one
    provider wait, one serving boundary - the three spawn sites the live
    run exercised. ``pressure_fn(sim, tick) -> bool`` decides the cgroup
    state AFTER the boundary, exactly where the resident-leg residency
    actually lands.
    """
    for tick in range(ticks):
        if note_edit_mod and tick % note_edit_mod == note_edit_mod - 1:
            sim.dirty("mod.py", advance=cadence)
        else:
            sim.edit("mod.py", advance=cadence)
        sim.provider_wait()
        sim.boundary()
        if pressure_fn is not None:
            sim.pressure = bool(pressure_fn(sim, tick))
        if tick in release_ticks:
            sim.finish_legs()


def _assert_bounded_pressure(sim: "_Sim", *, bound: int) -> None:
    """The storm invariants every scenario must hold, whatever the shape."""
    pressured = [t for t, squeezed in sim.amend_spawns if squeezed]
    assert len(pressured) <= bound, (
        f"spawn storm not bounded: {len(pressured)} pressured spawns "
        f"(bound {bound})"
    )
    assert sim.build_spawns == [], (
        "a heavier recovery build ran under measured pressure"
    )
    events = sim.refresh_events()
    assert all(
        event.severity == "WARNING"
        and event.classification == "consequential"
        and event.retryable
        for event in events
    ), events
    assert sim.adapter._amend_failure_streak == {}, (
        "resource-bound outcomes fed the deterministic ladder"
    )


def test_sustained_churn_salvages_every_raced_leg_and_attests_clean(
    monkeypatch, tmp_path
):
    """The gate-one workload shape, replayed: churn + pressure + races.

    120 edit transactions at ~45s cadence - the live run's 133 in 85min -
    a provider wait and a serving boundary per tick. A resident leg
    presses the cgroup on most ticks but not all, the same shape the
    live journal showed: refusals in bursts, the graph still advancing.
    Legs run ~20 ticks and every one loses the publication race because
    edits never stop - so the tier lands only through salvage, exactly
    like merge-b0bptwou in the paid run.

    The whole-run contract the gate-one violated:
    - spawns bounded by the windows, not by boundary count
    - every refresh event consequential/retryable - no terminal ERRORs
    - no escalation to a heavier build under measured pressure
    - lsp_promotion WORKING at seal through the salvage channel
    """
    sim = _Sim(monkeypatch, tmp_path)
    _drive_churn(
        sim, ticks=150, cadence=45.0,
        release_ticks={21, 49, 91, 133},
        # Resident legs press the cgroup on six ticks of seven - the
        # seventh is the gap the live graph actually advanced through.
        pressure_fn=lambda s, tick: bool(s.in_flight) and (tick % 7 != 0),
    )

    # ~85 pressured ticks, two spawn sites each, at a 120s memory window:
    # tens of suppressed attempts, a handful of real spawns. The live run
    # did one spawn per boundary. Successful amends through the gaps are
    # the graph legitimately advancing - they are not the storm metric.
    pressured = [t for t, squeezed in sim.amend_spawns if squeezed]
    assert 3 <= len(pressured) <= 40, (
        f"spawn storm not bounded: {len(pressured)} pressured spawns"
    )
    assert any(not squeezed for _, squeezed in sim.amend_spawns), (
        "the graph never advanced through a pressure gap"
    )
    assert sim.build_spawns == [], (
        "a heavier recovery build ran under measured pressure"
    )
    events = sim.refresh_events()
    assert events, "expected memory-family refresh evidence"
    assert all(
        event.severity == "WARNING"
        and event.classification == "consequential"
        and event.retryable
        for event in events
    )
    assert sim.adapter._amend_failure_streak == {}, (
        "resource-bound outcomes fed the deterministic ladder"
    )

    # The defer machinery actually engaged, one journal row per window.
    assert sim.journal("index_memory_backoff")
    assert sim.journal("graph_amend_deferred")

    # Every leg lost its race; every salvage landed the tier forward.
    terminals = sim.journal("lsp_promotion_terminal")
    assert len(terminals) >= 3
    assert all(
        row.get("disposition") == "obsolete" for row in terminals
    )
    salvages = sim.journal("lsp_salvage")
    refusals = sim.journal("lsp_salvage_refused")
    assert len(salvages) + len(refusals) >= 3, (
        "obsolete legs left the drain unaudited: "
        f"salvages={salvages} refusals={refusals}"
    )
    assert all(
        row.get("reason") in {"live_not_current", "unenumerated_edit"}
        for row in refusals
    ), f"unexpected refusal reasons: {refusals}"
    assert len(salvages) >= 2, (
        f"the tier never landed through salvage: refusals={refusals}"
    )
    assert all(row.get("outcome") == "published" for row in salvages)

    caps = sim.capabilities()
    assert caps["lsp_promotion"][0] == "WORKING", caps["lsp_promotion"]
    assert caps["gt_engine_enabled"][0] == "WORKING"


@pytest.mark.parametrize(
    "ticks,cadence,note_edit_mod,pressure_fn,bound",
    [
        # Continuous pressure at a tight cadence: every tick pressures the
        # cgroup, so the 120s window is the only governor - ~5 attempts in
        # 600s, not 60 boundaries' worth.
        (60, 10.0, 5, lambda s, tick: True, 12),
        # Slow cadence, sparse pressure: one pressured tick in three. The
        # 120s window lapses between squeezes, so each pressured tick can
        # fire once - bounded by pressured-tick count, not by boundary
        # count, which is the whole contract.
        (40, 90.0, 5, lambda s, tick: tick % 3 == 0, 14),
        # Front-loaded pressure that clears: the first quarter squeezes,
        # the rest is open - the graph must fully recover and no later
        # spawn may blame the window that already closed.
        (50, 45.0, 5, lambda s, tick: tick < 12, 10),
    ],
    ids=["continuous_tight", "sparse_slow", "front_loaded_clears"],
)
def test_parameterized_pressure_storm_stays_bounded(
    monkeypatch, tmp_path,
    ticks, cadence, note_edit_mod, pressure_fn, bound,
):
    """The storm contract is shape-independent, not one scripted run.

    Same driver, same invariants, three workloads: the windows bound
    pressured spawns under any cadence, resource-bound outcomes never
    escalate, and every refresh event stays consequential/retryable.
    """
    sim = _Sim(monkeypatch, tmp_path)
    _drive_churn(
        sim, ticks=ticks, cadence=cadence,
        note_edit_mod=note_edit_mod, pressure_fn=pressure_fn,
    )
    _assert_bounded_pressure(sim, bound=bound)


def test_pressure_persists_to_seal_fails_honest_and_bounded(
    monkeypatch, tmp_path
):
    """Pressure that never clears: bounded attempts, an honest verdict.

    If the cgroup stays squeezed for the whole run the graph stays stale
    - that is a legitimate outcome, and the run's evidence must say so
    without manufacturing a terminal producer fault. Every dead spawn is
    consequential, no escalation fires, and the capability layer - not
    the diagnostic stream - carries the failure axis.
    """
    sim = _Sim(monkeypatch, tmp_path)
    sim.pressure = True

    for _ in range(30):
        sim.edit("mod.py", advance=10.0)
        sim.boundary()

    # ~300 seconds of pressure at a 120s window: two or three attempts,
    # not thirty boundaries' worth.
    assert 1 <= len(sim.amend_spawns) <= 4, (
        f"persistent pressure still stormed: {len(sim.amend_spawns)}"
    )
    assert sim.build_spawns == [], (
        "memory-family refusals must never escalate to a rebuild"
    )
    assert sim.adapter.engine_state.graph_current is False
    assert all(
        event.severity == "WARNING"
        and event.classification == "consequential"
        and event.retryable
        for event in sim.refresh_events()
    )
    # The honest failure axis: a required capability reports the gap in
    # its own words, instead of the diagnostic stream manufacturing one.
    caps = sim.capabilities()
    assert any(
        state != "CapabilityState.WORKING"
        for name, (state, _evidence) in caps.items()
        if name in {"dense_retrieval", "lsp_promotion"}
    ), caps


def test_deterministic_amend_death_escalates_once_and_heals(
    monkeypatch, tmp_path
):
    """A non-memory producer defect: the ladder still works, a window apart.

    GT_INDEX_PROCESS_FAILED carries no memory code, so the deterministic
    contract applies unchanged: first refusal journaled-only, second on
    the same parent bytes buys the recovery build - just one spawn
    window between attempts instead of back-to-back stampedes.
    """
    sim = _Sim(monkeypatch, tmp_path)
    state = {"fail": True}

    def flaky(root, *, parent_graph, changed_paths, **kwargs):
        sim.amend_spawns.append((sim.clock["now"], False))
        if state["fail"]:
            return None, (
                "amend_failed:GT_INDEX_PROCESS_FAILED:exit=1:"
                "stderr=batch parent parser row differs"
            ), ()
        graph, _sha = sim._new_graph("healed")
        return graph, "", ({"path": "mod.py", "updated": 1},)

    monkeypatch.setattr(
        "gt_engine.indexer._ensure_index_incremental_unlocked", flaky
    )

    sim.edit("mod.py", advance=10.0)
    # The transaction amend died above and opened the spawn window; the
    # first boundary attempt waits it out, then dies: streak 1.
    assert sim.boundary(advance=61.0) is False
    # Streak 2 on identical parent bytes -> one escalation, one build.
    assert sim.boundary(advance=61.0) is True
    assert sim.build_spawns == [sim.build_spawns[0]]
    assert len(sim.build_spawns) == 1
    escalated = sim.journal("graph_amend_escalated")
    assert len(escalated) == 1

    # The refusal typing is unchanged: deterministic stays primary ERROR.
    assert all(
        event.severity == "ERROR"
        and event.classification == "primary"
        and not event.retryable
        for event in sim.refresh_events()
    )

    # The fresh parent amends clean - the ladder healed the chain.
    state["fail"] = False
    sim.edit("mod.py", advance=61.0)
    assert sim.boundary(advance=61.0) is True
    assert sim.adapter.engine_state.graph_current is True


def test_a_calm_run_attests_clean_with_zero_diagnostics(
    monkeypatch, tmp_path
):
    """The control: no pressure, legs publish, nothing to explain.

    A healthy run produces no diagnostic events at all - the check that
    proves the consequential channel is evidence about real events, not
    noise the run always emits.
    """
    sim = _Sim(monkeypatch, tmp_path)

    # The published path needs the certifier seam: certification of the
    # candidate bytes is the producer's own proof, covered elsewhere.
    from gt_engine.miniswe_integration import GraphBuildArtifact

    def certify(request, base, terminal):
        candidate = Path(str(terminal["candidate_path"]))
        return GraphBuildArtifact(
            True, str(candidate),
            hashlib.sha256(candidate.read_bytes()).hexdigest(),
        )

    monkeypatch.setattr(sim.adapter, "_certify_lsp_candidate", certify)

    for _ in range(10):
        sim.edit("mod.py", advance=45.0)
        sim.boundary()

    # A leg offered mid-churn drains obsolete - edits kept landing while
    # it ran, exactly as the sustained scenario drives. Its salvage is
    # enqueued during the poll, launched on the next wait, and its merge
    # published on the drain after that - the run is salvage-quiet only
    # once the second wait returns.
    sim.finish_legs()
    sim.provider_wait()
    sim.provider_wait()
    sim.boundary()

    # The quiet leg: scheduled on an unmoved base and released with no
    # edit between schedule and drain, it certifies and publishes. The
    # clock must pass the churn backoff the obsolete drain armed.
    sim.edit("mod.py", advance=45.0)
    sim.boundary(advance=600.0)
    assert sim.in_flight, "a calm current graph must offer a promotion"
    sim.finish_legs()
    sim.provider_wait()
    sim.boundary()

    terminals = sim.journal("lsp_promotion_terminal")
    assert terminals and terminals[-1].get("disposition") == "published"

    assert sim.refresh_events() == []
    assert list(sim.adapter.diagnostics._events) == []

    caps = sim.capabilities()
    assert caps["lsp_promotion"][0] == "WORKING", caps["lsp_promotion"]
    assert caps["gt_engine_enabled"][0] == "WORKING"


def test_a_published_salvage_schedules_a_fresh_leg_on_the_merged_graph(
    monkeypatch, tmp_path
):
    """Salvage adoption is still an adoption: schedule the next leg on it.

    The paid gate-one on the healed producer (run 34904339448) salvaged a
    raced leg onto the live graph (applied 13, skipped_diverged 10) and then
    sealed without ever scheduling an enrichment on the merged revision -
    partial coverage froze as the terminal state and the capability read
    DEGRADED. The drain-side publish now runs the same schedule call every
    other adoption point runs, so the tier converges while the run lives.
    """
    sim = _Sim(monkeypatch, tmp_path)

    sim.boundary()
    sim.edit("mod.py")
    sim.finish_legs()
    # The obsolete drain enqueues the salvage; the next wait launches it and
    # the drain after that publishes the merge. Boundaries after the publish
    # are where a post-adoption schedule could otherwise hide.
    sim.provider_wait()
    sim.provider_wait()
    # The obsolete drain arms a 60s churn backoff; the convergence schedule
    # is owed only once the window expires - advance the sim clock past it.
    sim.boundary()
    sim.boundary(advance=61.0)

    salvages = sim.journal("lsp_salvage")
    assert salvages and salvages[-1].get("outcome") == "published", (
        f"the scenario must drive a published salvage: {salvages}"
    )
    merged_revision = salvages[-1]["graph_revision"]
    scheduled = sim.journal("lsp_promotion_scheduled")
    assert any(
        row.get("graph_revision") == merged_revision for row in scheduled
    ), (
        "the salvage-published graph was never offered a convergence leg: "
        f"scheduled={[row.get('graph_revision') for row in scheduled]}"
    )


def test_seal_converges_the_tier_churn_denied_mid_run(
    monkeypatch, tmp_path
):
    """Seal is the one window a promotion leg cannot lose: no edit lands.

    Run 34904339448 sealed 26s after a partial salvage - inside the armed
    60s churn backoff - so the post-salvage schedule the drain performs
    could never fire and the partial tier froze as the verdict. Close must
    converge instead: drain the work already paid for, then offer the
    adopted graph the leg no publication can obsolete, before the journal
    seals. The churn defer is deliberately bypassed - there is no churn
    left to wait out.
    """
    sim = _Sim(monkeypatch, tmp_path)

    from gt_engine.miniswe_integration import GraphBuildArtifact

    def certify(request, base, terminal):
        candidate = Path(str(terminal["candidate_path"]))
        return GraphBuildArtifact(
            True, str(candidate),
            hashlib.sha256(candidate.read_bytes()).hexdigest(),
        )

    monkeypatch.setattr(sim.adapter, "_certify_lsp_candidate", certify)
    # A leg scheduled from inside close must finish there: release its
    # handle on schedule so the bounded join returns with a terminal.
    schedule = sim.adapter._schedule_lsp_candidate

    def releasing(request, base):
        handle = schedule(request, base)
        handle.release()
        return handle

    monkeypatch.setattr(
        sim.adapter, "_schedule_lsp_candidate", releasing
    )

    sim.boundary()
    sim.edit("mod.py")
    sim.finish_legs()
    sim.provider_wait()
    sim.provider_wait()
    salvages = sim.journal("lsp_salvage")
    assert salvages and salvages[-1].get("outcome") == "published", salvages
    merged = salvages[-1]["graph_revision"]

    # Seal inside the armed backoff - the paid run's real tail shape. No
    # boundary ever fired post-window; only close itself can converge.
    sim.adapter.close_graph_lifecycle()

    scheduled = sim.journal("lsp_promotion_scheduled")
    assert any(
        row.get("graph_revision") == merged for row in scheduled
    ), f"seal never offered the merged graph a leg: {scheduled}"
    terminals = sim.journal("lsp_promotion_terminal")
    assert terminals[-1].get("disposition") == "published", terminals[-1]
    assert sim.journal("lsp_seal_convergence")

    caps = sim.capabilities()
    assert caps["lsp_promotion"][0] == "WORKING", caps["lsp_promotion"]
