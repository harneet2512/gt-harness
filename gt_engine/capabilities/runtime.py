"""Runtime capability facade — executed-observation and verification state."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gt_engine.capabilities._query import (
    engine_of,
    last_journal_event,
    read_cas_blob,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gt_engine.gt_session import GTSession


def _last_execution(session: "GTSession") -> dict[str, Any] | None:
    """The journal's last ``execution_evidence`` row, blob fields merged."""

    row = last_journal_event(session, "execution_evidence")
    if row is None:
        return None
    out = dict(row)
    digest = str(row.get("artifact_sha256") or "")
    raw = read_cas_blob(session, "execution_evidence", digest)
    if raw is not None:
        try:
            out["artifact"] = json.loads(raw)
        except ValueError:
            out["artifact"] = None
    else:
        out["artifact"] = None
    out["blob_missing"] = raw is None
    return out


def last_test_result(session: "GTSession") -> dict[str, Any] | None:
    """The last recorded executed-command evidence (test or otherwise).

    Journal row + CAS payload: command hash, kind, outcome,
    ``observed_test_outcome``, returncode, baseline classification.
    ``None`` when no execution has been recorded.
    """

    return _last_execution(session)


def covering_tests(
    session: "GTSession", files: list[str] | tuple[str, ...]
) -> dict[str, Any]:
    """Test files graph-covering ``files`` — the same selection
    ``change.affected_tests`` exposes; surfaced here as the runtime view."""

    from gt_engine.capabilities.change import affected_tests

    return affected_tests(session, files)


def failure_fingerprint(
    session: "GTSession", observation: str | None = None
) -> dict[str, Any]:
    """Deterministic failure signature via ``bridge.failure_fingerprint``.

    With ``observation`` given it fingerprints that text directly. Without
    it, the fingerprint is computed over the last recorded execution's raw
    output; ``None`` fields report when neither exists.
    """

    from gt_engine.bridge import failure_fingerprint as _fingerprint

    if observation is not None:
        return {
            "fingerprint": _fingerprint(observation),
            "basis": "explicit_observation",
        }
    last = _last_execution(session)
    if last is None:
        return {"fingerprint": "", "basis": None, "omissions": ["no_execution_recorded"]}
    raw_blob = str(last.get("raw_blob") or "")
    engine = engine_of(session)
    store_root = getattr(getattr(engine, "store", None), "root", None)
    text = ""
    if raw_blob and store_root is not None:
        try:
            text = Path(store_root, raw_blob).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            text = ""
    if not text:
        return {
            "fingerprint": "",
            "basis": "last_execution",
            "omissions": ["raw_output_unavailable"],
        }
    return {
        "fingerprint": _fingerprint(text),
        "basis": "last_execution",
        "action_id": last.get("action_id"),
        "raw_output_sha256": (last.get("artifact") or {}).get("raw_output_sha256"),
    }


def repeated_failure_state(session: "GTSession") -> dict[str, Any]:
    """The adapter's failure-recurrence bookkeeping (internal state read).

    ``_failure_recurrences`` counts how often each fingerprint recurred;
    ``_pending_recovery``/``_recovery_delivered`` bound the recovery steer.
    """

    engine = engine_of(session)
    recurrences = getattr(engine, "_failure_recurrences", {}) or {}
    first_epoch = getattr(engine, "_failure_first_epoch", {}) or {}
    pending = getattr(engine, "_pending_recovery", None)
    return {
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
        "recovery_delivered": int(getattr(engine, "_recovery_delivered", 0) or 0),
    }


def verification_state(session: "GTSession") -> dict[str, Any]:
    """Recorded verification posture: the last execution's classification.

    Carries the baseline-vs-current classification the engine computed at
    observation time — the recorded state, not a fresh verdict.
    """

    last = _last_execution(session)
    if last is None:
        return {"state": "none", "omissions": ["no_execution_recorded"]}
    classification = last.get("baseline_classification")
    artifact = last.get("artifact") or {}
    return {
        "state": "recorded",
        "kind": last.get("kind"),
        "outcome": last.get("outcome"),
        "observed_test_outcome": last.get("observed_test_outcome"),
        "returncode": last.get("returncode"),
        "baseline_classification": classification,
        "repository_revision": artifact.get("repository_revision")
        or last.get("repository_revision"),
        "timed_out": last.get("timed_out"),
    }
