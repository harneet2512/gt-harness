"""Runtime capability facade — executed-observation and verification state."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gt_engine.capabilities._query import (
    CapabilityResult,
    engine_of,
    last_journal_event,
    read_cas_blob,
    wrap,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gt_engine.gt_session import GTSession

_EXEC_PROVENANCE = "gt_engine.miniswe_integration.record_execution_evidence"


def _last_execution(session: "GTSession") -> tuple[dict[str, Any] | None, list[str]]:
    """The journal's last ``execution_evidence`` row, CAS payload merged."""

    row = last_journal_event(session, "execution_evidence")
    if row is None:
        return None, ["no_execution_recorded"]
    out = dict(row)
    digest = str(row.get("artifact_sha256") or "")
    raw = read_cas_blob(session, "execution_evidence", digest)
    omissions: list[str] = []
    if raw is not None:
        try:
            out["artifact"] = json.loads(raw)
        except ValueError:
            out["artifact"] = None
            omissions.append("execution_blob_undecodable")
    else:
        out["artifact"] = None
        omissions.append("execution_blob_missing")
    return out, omissions


def last_test_result(session: "GTSession") -> CapabilityResult:
    """The last recorded executed-command evidence (test or otherwise).

    Journal row + CAS payload: command hash, kind, outcome,
    ``observed_test_outcome``, returncode, baseline classification.
    ``unavailable`` when no execution has been recorded.
    """

    started = time.perf_counter()
    last, omissions = _last_execution(session)
    if last is None:
        return wrap(
            session, "last_test_result",
            status="unavailable",
            omissions=tuple(omissions),
            provenance=_EXEC_PROVENANCE,
            started_ms=started,
        )
    return wrap(
        session, "last_test_result",
        answer=last,
        status="ok" if not omissions else "partial",
        omissions=tuple(omissions),
        semantics="exact",
        provenance=_EXEC_PROVENANCE,
        started_ms=started,
    )


def covering_tests(
    session: "GTSession", files: list[str] | tuple[str, ...]
) -> CapabilityResult:
    """Test files graph-covering ``files`` — the same selection
    ``change.affected_tests`` exposes; surfaced here as the runtime view."""

    from gt_engine.capabilities.change import affected_tests

    result = affected_tests(session, files)
    return CapabilityResult(
        capability="covering_tests",
        status=result.status,
        answer=result.answer,
        omissions=result.omissions,
        limitations=result.limitations,
        semantics=result.semantics,
        graph_revision=result.graph_revision,
        source_revision=result.source_revision,
        fresh=result.fresh,
        cost=result.cost,
        provenance=result.provenance,
    )


def failure_fingerprint(
    session: "GTSession", observation: str | None = None
) -> CapabilityResult:
    """Deterministic failure signature via ``bridge.failure_fingerprint``.

    With ``observation`` given it fingerprints that text directly. Without
    it, the fingerprint is computed over the last recorded execution's raw
    output; omissions report when neither exists.
    """

    started = time.perf_counter()
    provenance = "gt_engine.bridge.failure_fingerprint"
    from gt_engine.bridge import failure_fingerprint as _fingerprint

    if observation is not None:
        return wrap(
            session, "failure_fingerprint",
            answer={
                "fingerprint": _fingerprint(observation),
                "basis": "explicit_observation",
            },
            status="ok",
            semantics="exact",
            provenance=provenance,
            started_ms=started,
        )
    last, omissions = _last_execution(session)
    if last is None:
        return wrap(
            session, "failure_fingerprint",
            status="unavailable",
            omissions=tuple(omissions),
            provenance=provenance,
            started_ms=started,
        )
    raw_blob = str(last.get("raw_blob") or "")
    store_root = getattr(
        getattr(engine_of(session), "store", None), "root", None
    )
    text = ""
    if raw_blob and store_root is not None:
        try:
            text = Path(store_root, raw_blob).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            text = ""
    if not text:
        return wrap(
            session, "failure_fingerprint",
            status="abstain",
            omissions=tuple(omissions + ["raw_output_unavailable"]),
            provenance=provenance,
            started_ms=started,
        )
    return wrap(
        session, "failure_fingerprint",
        answer={
            "fingerprint": _fingerprint(text),
            "basis": "last_execution",
            "action_id": last.get("action_id"),
            "raw_output_sha256": (last.get("artifact") or {}).get(
                "raw_output_sha256"
            ),
        },
        status="ok" if not omissions else "partial",
        omissions=tuple(omissions),
        semantics="exact",
        provenance=provenance,
        started_ms=started,
    )


def repeated_failure_state(session: "GTSession") -> CapabilityResult:
    """The adapter's failure-recurrence bookkeeping (internal state read).

    ``_failure_recurrences`` counts how often each fingerprint recurred;
    ``_pending_recovery``/``_recovery_delivered`` bound the recovery steer.
    """

    started = time.perf_counter()
    engine = engine_of(session)
    recurrences = getattr(engine, "_failure_recurrences", {}) or {}
    first_epoch = getattr(engine, "_failure_first_epoch", {}) or {}
    pending = getattr(engine, "_pending_recovery", None)
    return wrap(
        session, "repeated_failure_state",
        answer={
            "fingerprints": {
                fp: {
                    "recurrences": count,
                    "first_epoch": first_epoch.get(fp),
                }
                for fp, count in sorted(recurrences.items())
            },
            "pending_recovery": (
                {"fingerprint": pending[0], "epoch": pending[1]}
                if pending
                else None
            ),
            "recovery_delivered": int(
                getattr(engine, "_recovery_delivered", 0) or 0
            ),
        },
        status="ok",
        omissions=() if recurrences else ("no_failures_recorded",),
        semantics="exact",
        provenance="gt_engine.miniswe_integration.note_failure_fingerprint",
        started_ms=started,
    )


def verification_state(session: "GTSession") -> CapabilityResult:
    """Recorded verification posture: the last execution's classification.

    Carries the baseline-vs-current classification the engine computed at
    observation time — the recorded state, not a fresh verdict.
    """

    started = time.perf_counter()
    provenance = "gt_engine.miniswe_integration._classify_execution_vs_baseline"
    last, omissions = _last_execution(session)
    if last is None:
        return wrap(
            session, "verification_state",
            status="unavailable",
            answer={"state": "none"},
            omissions=tuple(omissions),
            provenance=provenance,
            started_ms=started,
        )
    artifact = last.get("artifact") or {}
    return wrap(
        session, "verification_state",
        answer={
            "state": "recorded",
            "kind": last.get("kind"),
            "outcome": last.get("outcome"),
            "observed_test_outcome": last.get("observed_test_outcome"),
            "returncode": last.get("returncode"),
            "baseline_classification": last.get("baseline_classification"),
            "repository_revision": artifact.get("repository_revision")
            or last.get("repository_revision"),
            "timed_out": last.get("timed_out"),
        },
        status="ok" if not omissions else "partial",
        omissions=tuple(omissions),
        semantics="exact",
        provenance=provenance,
        started_ms=started,
    )
