"""Say which GT features worked in a run, from the run's own evidence.

Why this is not the feature matrix
----------------------------------
``gt_engine.feature_matrix`` decides each feature by running a TEST:
``FEATURE_EVIDENCE`` and ``FEATURE_NEGATIVE_EVIDENCE`` map a feature to pytest
node ids. That answers "does the test pass", which is a statement about the
suite, not about the benchmark run. It is the wrong instrument for the question
the owner asks of every run - which features worked and which did not - and it
answers it identically whether the run solved the task or died in construction.

This derives the answer from the journal the run actually wrote. Every delivery
carries an ``evidence_type``, and ``attribution.feature_for_evidence`` already
maps those onto the census identities, so nothing new has to be invented or
maintained alongside the producer.

An answer of "unknown" has to earn itself
-----------------------------------------
Half this table used to read unknown, and none of those unknowns were about
the run: they were about which rows this script opened. Two rules now hold.

A boundary is NOT_REACHED only when its witness is one the producer writes on
every path it could have occurred on (``DECIDABLE_ABSENCE``); otherwise the row
stays unknown and the evidence string NAMES the journal row it wanted, so a
reader can check the claim instead of trusting it.

An evidence type is "unattributed" only when no feature and no channel claims
it. ``execution_evidence`` and ``verification_plan`` are capability CHANNELS
and get their own section; ``context_delta`` is a prompt-lane byte budget that
resolves through its supersession key, or is reported as lane-kind-only when it
carries none. None of the three is a census gap, and reporting them as one
turned 19 ordinary deliveries into a phantom drift.

Three states, and the third is not a failure
--------------------------------------------
DELIVERED      the feature put evidence in front of the model, with a count.
REFUSED        it was reached and declined, which is a working feature saying no.
NOT TRIGGERED  its precondition never occurred in this task.

Keeping NOT TRIGGERED distinct from REFUSED is the whole point. A task with no
new files cannot exercise ``newfile_precedent``, and reporting that as a failure
would train a reader to ignore the column. But a POST-EDIT feature silent across
a run with a hundred edits is not untriggered by nature - it is a symptom, and
run 34064560259 is the case: the graph was frozen at task start, so
``syntax_result``, ``signature_delta`` and ``covering_red`` had nothing to fire
against and the accounting showed 4 of 12.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gt_engine.attribution import (  # noqa: E402
    CAPABILITY_OWNERS,
    DIRECT_FEATURES,
    LANE_KINDS,
    channel_for_evidence,
    feature_for_evidence,
    feature_for_supersession,
)
from gt_engine.delivery_budget import REFUSAL_EVENTS  # noqa: E402

# The submit boundary is decided by a POSITIVE DECLARATION, not by an absence.
# session_closed fires once on every path and carries how the run ended, so
# reading its terminal says "this run ended by exhausting its budget" rather
# than "the conditional branch that logs a submit was not taken". The obvious
# candidate, submit_decision, sits inside `if not self.can_enforce:` - deriving
# never-happened from an unlogged conditional is the absent-means-negative shape
# this file exists to avoid, and it would have been wrong for an enforcing run.
#
# A run killed before close writes no session_closed at all. That is genuinely
# unknown and must not read as NOT_REACHED, which is the distinction that makes
# this derivation safe where the other was not.
SUBMIT_TERMINALS = frozenset({"submitted", "submitted_verified", "submitted_unverified"})

# A run can end without ever reaching close. run_terminal is the supervisor's
# own row and carries the same `terminal` field, so a timed-out run says
# `timeout` here rather than saying nothing at all - which is exactly the case
# 34996816912 was in, and why its whole submit column read unknown.
TERMINAL_EVENTS = ("session_closed", "run_terminal")

SCHEMA = "gt.feature_accounting.v1"

# Everything this script opens. Printed with each underivable row so a reader
# can tell "the run does not record it" from "the script did not look".
EXAMINED = "events.jsonl, transaction_artifacts/, edit_transactions/"


def _row(name: str, **fields: str):
    """A predicate matching one journal event name, optionally on field values."""
    def check(event: dict) -> bool:
        if str(event.get("event") or "") != name:
            return False
        return all(str(event.get(key) or "") == value for key, value in fields.items())
    return check


def _submit_terminal(event: dict) -> bool:
    return (
        str(event.get("event") or "") in TERMINAL_EVENTS
        and str(event.get("terminal") or "") in SUBMIT_TERMINALS
    )


# Which journal rows prove a declared boundary was reached. The feature
# vocabulary (edit_result, submit, search_result, file_view, test_result,
# tool_result, task_start) and the journal's own event names are different
# vocabularies, and inventing a join between them is the mistake this file
# exists to stop making. Every join below is one the producer itself writes:
#
#   producer_invocation.event_type IS the feature vocabulary - the gateway
#   stamps the semantic boundary from miniswe_evidence._derive_semantic_events
#   onto every dispatch (miniswe_integration.py:5134).
#   execution_evidence.kind == "test" is the parsed test observation.
#   execution_finished is one completed repository action.
#
# Each entry is (what the row is, predicate). The label is printed verbatim
# when the witness is missing, so an unknown always names the field it wanted.
BOUNDARY_EVIDENCE = {
    "task_start": (
        ("runtime_layout", _row("runtime_layout")),
    ),
    "edit_result": (
        ("edit_transaction", _row("edit_transaction")),
    ),
    "file_view": (
        ("producer_invocation.event_type=file_view",
         _row("producer_invocation", event_type="file_view")),
    ),
    "search_result": (
        ("producer_invocation.event_type=search_result",
         _row("producer_invocation", event_type="search_result")),
        ("producer_invocation.event_type=failed_search",
         _row("producer_invocation", event_type="failed_search")),
    ),
    "test_result": (
        ("execution_evidence.kind=test", _row("execution_evidence", kind="test")),
    ),
    "tool_result": (
        ("execution_finished", _row("execution_finished")),
    ),
    "submit": (
        ("plan_gate_decision", _row("plan_gate_decision")),
        ("submission_patch_observed.stage=submit",
         _row("submission_patch_observed", stage="submit")),
        (f"session_closed/run_terminal terminal in {sorted(SUBMIT_TERMINALS)}",
         _submit_terminal),
    ),
}

# Boundaries whose ABSENCE is itself evidence, because their witness is written
# once per occurrence on every path: the layout at task start, a transaction per
# edit, a parsed observation per test, a finish per action, and for submit a row
# that fires whether the gate accepted, refused, or the supervisor killed the
# run.
#
# file_view and search_result are deliberately NOT here. producer_invocation is
# emitted per producer DISPATCH, not per boundary, so zero rows means "no
# producer was dispatched at that boundary" and cannot be read as "the model
# never searched". Those stay unknown, and the unknown names the field.
DECIDABLE_ABSENCE = frozenset({
    "task_start", "edit_result", "test_result", "tool_result", "submit",
})

# Deliveries a feature proves with its own journal row instead of through the
# delivery_identity ledger. Both of these put bytes in front of the model -
# the rendered plan (gt_session persistent plan delivery) and the plan gate's
# directive, appended to the next request as a user message
# (miniswe_runtime.py:2118) - and neither travels as an evidence_type, so the
# ledger alone reports them as silence.
ROW_DELIVERIES = {
    "persistent_plan_delivered": "persistent_plan",
    "plan_gate_directive_prepared": "plan_gate",
}

# Events that stage bytes into the pending provider payload but are not, by
# themselves, model exposure. The wire-binding proof is a provider_delivery
# row naming the identity in ``delivery_ids``; a staged identity that never
# gets one was prepared, not delivered. (gt_trajectory_eval already draws the
# same line: "Blobs without a delivered event prove preparation, not
# delivery".)
STAGING_EVENTS = frozenset({
    "delivery_prepared",
    "decision_context_unit_prepared",
})

# Paths whose edit cannot start a covering-test regression, because they are
# the test. Kept crude on purpose: a false NEGATIVE here would invent an
# eligibility the run never had.
_TEST_PATH_MARKERS = ("test_", "_test.", "/tests/", "tests/", "conftest.py")


def edit_eligibility(artifacts: list[dict]) -> dict[str, dict]:
    """What the edit-boundary features were ELIGIBLE for, from the run's blobs.

    Eligibility does not have to come from the eligibility check. The producer
    writes one ``transaction_artifacts`` blob per edit carrying the same
    preconditions the features test:

        syntax[].diagnostics non-empty  -> syntax_result was eligible
        signatures[].changed non-empty  -> signature_delta's FIRST half held

    Both were previously reported as "eligibility unrecorded", and that was a
    statement about where this script looked, not about the run: the 38 blobs
    sit in the same directory as the journal. In 34095557374 they say syntax was
    clean on all 38 edits (correct silence, 38 times) and a signature changed
    twice - and on both of those, caller_coverage was ``unavailable``, so
    signature_delta's second half could not hold. Starved, with a named cause,
    rather than unknown.
    """
    syntax_eligible = signature_eligible = 0
    syntax_checked = syntax_unchecked = 0
    coverage: collections.Counter = collections.Counter()
    for blob in artifacts:
        for entry in blob.get("syntax") or ():
            # A syntax entry only carries a verdict when a certified post-image
            # parser exists for the language. `status: unsupported` with
            # `reason: no_harness_certified_postimage_parser` means the check
            # DID NOT RUN, and counting those as clean edits inflates the
            # feature's coverage: 28 of 143 entries in run 34064560259 were
            # unparseable, so "correctly quiet on every edit" was never true of
            # every edit.
            if entry.get("status") == "exact":
                syntax_checked += 1
                if entry.get("diagnostics"):
                    syntax_eligible += 1
            else:
                syntax_unchecked += 1
        for entry in blob.get("signatures") or ():
            if entry.get("changed"):
                signature_eligible += 1
        coverage[blob.get("caller_coverage")] += 1
    return {
        "syntax_result": {
            "eligible": syntax_eligible,
            "of": syntax_checked,
            "unchecked": syntax_unchecked,
            "declined_because": (
                f"its precondition never held in {syntax_checked} checked "
                "edit transactions"
            ),
            # No artifact on record contains a non-empty `diagnostics`, so this
            # negative has never been positively controlled - the query has not
            # been shown capable of finding one. `valid: True` on every parsed
            # entry corroborates it independently; corroboration is not a
            # control, and the row says so rather than implying otherwise.
            "uncontrolled_negative": True,
        },
        "signature_delta": {
            "eligible": signature_eligible,
            "of": len(artifacts),
            "caller_coverage": dict(coverage),
            "declined_because": (
                f"its precondition never held in {len(artifacts)} checked "
                "edit transactions"
            ),
        },
    }


def _is_test_path(path: str) -> bool:
    text = str(path or "").replace("\\", "/")
    name = text.rsplit("/", 1)[-1]
    return any(marker in text for marker in _TEST_PATH_MARKERS) or name.startswith("test_")


def journal_eligibility(
    events: list[dict],
    transactions: list[dict] | None = None,
    known: frozenset[str] | set[str] = frozenset(),
) -> dict[str, dict]:
    """The preconditions that live in the journal rather than in a blob.

    Three of these rows used to read "eligibility not derivable", which is a
    claim about where this script looked. The producer writes every field they
    need; the script was not reading them.

    ``known`` names the features an earlier, stronger source already decided.
    They are excluded from the producer_invocation fallback at the bottom:
    "the edit changed no signature" describes the run, while "the producer
    returned nothing" describes only the producer, and the first outranks the
    second wherever both exist.

    A feature absent from the returned mapping stays underivable ON PURPOSE -
    the caller prints that scope rather than an unknown. `newfile_precedent`
    is the case: without the ``edit_transactions/`` blobs there is no
    ``operation`` field anywhere, and answering "no file was created" from the
    journal alone would be answering from an absence.
    """
    eligible: dict[str, dict] = {}

    # feature_evaluated rows - the Mini-SWE local lanes' own census. Lanes
    # like attribute_test_failure and note_failure_fingerprint never pass
    # through the gateway's producer_invocation vocabulary, so without this
    # the audit cannot tell "evaluated and abstained" from "never entered" -
    # the exact gap behind gtbridge_owned_features_unwired. Where these rows
    # exist they outrank every derived proxy: the lane records the deciding
    # boolean itself.
    lane_evals: dict[str, list[dict]] = collections.defaultdict(list)
    for event in events:
        if str(event.get("event") or "") == "feature_evaluated":
            lane_evals[str(event.get("feature_id") or "")].append(event)

    if lane_evals.get("covering_red"):
        # The lane's own verdict: eligible means edited files existed when a
        # failing test arrived; `attributed` means the failure output named
        # an edited file and a candidate was produced. Eligible-but-unlinked
        # is a correct abstention - the output never tied the failure to the
        # edit - not starvation.
        rows_ev = lane_evals["covering_red"]
        attributed = sum(
            1 for r in rows_ev if str(r.get("outcome") or "") == "attributed"
        )
        # `eligible` on the row is the lane's precondition (edited files
        # existed when a failing test arrived), not a produced candidate.
        # Only `attributed` means the lane built a candidate that delivery
        # could have dropped - the STARVED trigger. Eligible-but-unlinked
        # means the output never tied the failure to the edit: abstention.
        eligible["covering_red"] = {
            "eligible": attributed,
            "of": len(rows_ev),
            "declined_because": (
                f"of {len(rows_ev)} feature_evaluated rows at test_result, "
                "none found the failing output naming an edited file"
            ),
            "starved_because": (
                f"{attributed} of {len(rows_ev)} covering evaluations produced "
                "a candidate (outcome=attributed) and nothing was delivered"
            ),
        }
    else:
        # covering_red - an executed covering test fails because of an edited
        # source file. The attribution half (which source broke which test) is the
        # covering runner's own job and is not in the journal; what IS in the
        # journal is the ordering that has to hold before it can have one. Edits
        # confined to test files are excluded: a test that fails after only tests
        # changed is not a regression in a source file.
        source_edits = 0
        failure_followed = 0
        pending = 0
        for event in events:
            name = str(event.get("event") or "")
            if name == "edit_transaction":
                paths = [str(p) for p in event.get("changed_paths") or ()]
                if any(not _is_test_path(path) for path in paths):
                    source_edits += 1
                    pending += 1
            elif (
                name == "execution_evidence"
                and str(event.get("kind") or "") == "test"
                and str(event.get("observed_test_outcome") or "") == "fail"
                and pending
            ):
                failure_followed += pending
                pending = 0
        eligible["covering_red"] = {
            "eligible": failure_followed,
            "of": source_edits,
            "declined_because": (
                "no execution_evidence kind=test observed_test_outcome=fail followed "
                f"any of the {source_edits} source-file edit transactions"
            ),
            "starved_because": (
                f"{failure_followed} of {source_edits} source-file edit transactions "
                "were followed by an execution_evidence kind=test "
                "observed_test_outcome=fail and no covering verdict was delivered"
            ),
        }

    if lane_evals.get("recovery"):
        # GT_HYPOTHESIS's owning lane: a fingerprint tracked = evaluated;
        # steer_due = the recurrence precondition held and a steer candidate
        # exists. tracked_no_steer is the honest abstention (first sighting,
        # no post-edit recurrence, or the two-steer budget spent).
        rows_ev = lane_evals["recovery"]
        steer_due = sum(
            1 for r in rows_ev if str(r.get("outcome") or "") == "steer_due"
        )
        eligible["recovery"] = {
            "eligible": steer_due,
            "of": len(rows_ev),
            "declined_because": (
                f"{len(rows_ev)} failure fingerprints tracked; none recurred "
                "after an intervening edit within the two-steer budget"
            ),
            "starved_because": (
                f"{steer_due} of {len(rows_ev)} evaluations reached steer_due "
                "and no recovery_steer delivery followed"
            ),
        }

    # def_partition and the other outcome-lattice features (name_fold,
    # wrong_surface, body_concept) differ from covering_red/recovery: their
    # dispatch is UNCONDITIONAL once classify_outcome lands on their class
    # (gateway _produce_def_ref_partition on AMBIGUOUS_HIT/FLOOD carries no
    # kill-switch), and every dispatch - entered, abstained, suppressed - is
    # journaled through producer_recorder. So on a reached search boundary a
    # missing invocation row is not an audit gap: it means the lattice never
    # classified a search ambiguous or flooded, which is "never eligible", a
    # correct abstention. The eligibility decision lives in the outcome
    # classifier, not in a feature_evaluated row.
    search_boundaries = [
        event for event in events
        if str(event.get("event") or "") == "producer_invocation"
        and str(event.get("event_type") or "") in {"search_result", "failed_search"}
    ]
    if search_boundaries:
        lattice_dispatched = [
            event for event in events
            if str(event.get("event") or "") == "producer_invocation"
            and str(event.get("feature_id") or "") == "def_partition"
        ]
        # A candidate exists only when an invocation carried returned_fact -
        # entered-but-abstained (production_definition_absent et al.) is the
        # producer correctly declining on its own evidence, same semantics as
        # the generic producer_invocation fallback below.
        facts = sum(
            1 for event in lattice_dispatched if event.get("returned_fact")
        )
        if not lattice_dispatched:
            eligible["def_partition"] = {
                "eligible": 0,
                "of": len(search_boundaries),
                "declined_because": (
                    f"{len(search_boundaries)} search boundaries ran the "
                    "outcome lattice and none classified AMBIGUOUS_HIT/FLOOD "
                    "- dispatch on that outcome is unconditional, so no "
                    "invocation row means the precondition never held"
                ),
                "starved_because": "",
            }
        else:
            eligible["def_partition"] = {
                "eligible": facts,
                "of": len(search_boundaries),
                "declined_because": (
                    f"dispatched {len(lattice_dispatched)}x on "
                    f"{len(search_boundaries)} search boundaries; no "
                    "invocation carries returned_fact=true"
                ),
                "starved_because": (
                    f"{facts} of {len(lattice_dispatched)} dispatches "
                    "carried returned_fact=true and nothing was delivered"
                ),
            }

    # newfile_precedent - a created file exposes a sibling/registry precedent.
    # runtime_observation.py:594 writes one of create/delete/modify per change,
    # in the edit_transactions blob rather than in the journal row, which
    # carries only `changed_paths`.
    if transactions is not None:
        creates = 0
        inspected = 0
        silent = 0
        for blob in transactions:
            changes = blob.get("changes") or ()
            if not changes:
                silent += 1
                continue
            inspected += 1
            if any(str(c.get("operation") or "") == "create" for c in changes):
                creates += 1
        eligible["newfile_precedent"] = {
            "eligible": creates,
            "of": inspected,
            "unchecked": silent,
            "declined_because": (
                f"no change in {inspected} edit transactions carries "
                "operation=create"
            ),
            "starved_because": (
                f"{creates} of {inspected} edit transactions carry a change with "
                "operation=create and no precedent was delivered"
            ),
        }

    # persistent_plan - built once, before the first edit, and delivered once.
    built = sum(1 for e in events if str(e.get("event") or "") == "persistent_plan_built")
    eligible["persistent_plan"] = {
        "eligible": 1 if built else 0,
        "of": 1,
        "declined_because": "no persistent_plan_built row exists",
        "starved_because": (
            f"{built} persistent_plan_built row(s) exist and no "
            "persistent_plan_delivered or plan_cursor delivery followed"
        ),
    }

    # plan_gate - consulted at submit; `accepted: false` is the gate holding.
    decisions = [e for e in events if str(e.get("event") or "") == "plan_gate_decision"]
    if decisions:
        refusals = sum(1 for e in decisions if e.get("accepted") is False)
        eligible["plan_gate"] = {
            "eligible": refusals,
            "of": len(decisions),
            "declined_because": (
                f"all {len(decisions)} plan_gate_decision rows carry accepted=true "
                "(no plan row was left without evidence)"
            ),
            "starved_because": (
                f"{refusals} of {len(decisions)} plan_gate_decision rows carry "
                "accepted=false and no plan_gate_directive_prepared row followed"
            ),
        }

    # submit_refusal - refuse once when submission is attempted with unresolved
    # positive failing evidence. submit_decision.active_red IS that evidence.
    submits = [e for e in events if str(e.get("event") or "") == "submit_decision"]
    if submits:
        reds = sum(1 for e in submits if e.get("active_red"))
        eligible["submit_refusal"] = {
            "eligible": reds,
            "of": len(submits),
            "declined_because": (
                f"active_red was empty on all {len(submits)} submit_decision rows"
            ),
            "starved_because": (
                f"{reds} of {len(submits)} submit_decision rows carry a non-empty "
                "active_red and no refusal was delivered"
            ),
        }
    # Last resort for every other feature: the gateway's own invocation rows.
    # `feature_id` is stamped by the producer recorder from the invocation's
    # evidence_types (miniswe_integration.py:5140), so this is the producer
    # naming its own census identity rather than a join this script invented.
    # `returned_fact` is the producer saying it had something; a dispatch that
    # returned nothing is the producer declining, which is the distinction the
    # whole table is for.
    #
    # Applied ONLY where nothing better exists - the blob-derived preconditions
    # above describe what the edit looked like, which is stronger than what the
    # producer did about it.
    invocations: collections.defaultdict = collections.defaultdict(list)
    for event in events:
        if str(event.get("event") or "") == "producer_invocation":
            invocations[str(event.get("feature_id") or "")].append(event)
    for feature, calls in invocations.items():
        if not feature or feature in eligible or feature in known:
            continue
        facts = sum(1 for e in calls if e.get("returned_fact"))
        entered = sum(1 for e in calls if str(e.get("outcome") or "") == "entered")
        eligible[feature] = {
            "eligible": facts,
            "of": len(calls),
            "declined_because": (
                f"its producer was dispatched {len(calls)} times, entered {entered}, "
                "and no invocation carries returned_fact=true"
            ),
            "starved_because": (
                f"{facts} of {len(calls)} producer_invocation rows carry "
                "returned_fact=true and nothing was delivered"
            ),
        }
    return eligible


def account(
    events: list[dict],
    artifacts: list[dict] | None = None,
    transactions: list[dict] | None = None,
) -> dict:
    """Resolve each DELIVERY once, then count deliveries - never events.

    The two identity-bearing event classes are disjoint and describe the same
    deliveries from opposite sides: decision_context_unit_prepared / admitted /
    refused carry supersession_key, while delivery_prepared / receipt /
    evidence_delivery / context_addition_delivery carry evidence_type. Both
    carry delivery_identity.

    Attributing per EVENT counts one delivery up to four times and, worse, can
    place a single delivery in a feature via one row and in the unattributed
    bucket via another - which is what made 306 context_delta rows look
    unclaimed while the same deliveries were already attributed to obligations
    through their supersession key. Resolve identity -> feature first, taking
    the declared supersession key over the lane kind, then count identities.
    """
    identity_feature: dict[str, str] = {}
    identity_refused: dict[str, bool] = {}
    identity_channel: dict[str, str] = {}
    identity_kinds: dict[str, str] = {}
    identity_bound: set[str] = set()
    identity_events: dict[str, set[str]] = collections.defaultdict(set)
    reached: collections.Counter[str] = collections.Counter()
    witnessed: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    terminal: str | None = None

    for position, event in enumerate(events):
        name = str(event.get("event") or "")
        for boundary, witnesses in BOUNDARY_EVIDENCE.items():
            for label, matches in witnesses:
                if matches(event):
                    reached[boundary] += 1
                    witnessed[boundary][label] += 1

        if name in TERMINAL_EVENTS and event.get("terminal") is not None:
            # session_closed wins when both exist: it is the engine's own close,
            # while run_terminal is the supervisor's, and only one of them is
            # written on the path where the engine got to finish.
            if terminal is None or name == "session_closed":
                terminal = str(event.get("terminal") or "")
        if name in ROW_DELIVERIES:
            # These carry no delivery_identity of their own, so they get a
            # synthetic one keyed by position. It cannot collide with a real
            # identity (those are hex digests) and cannot collide with itself.
            synthetic = f"{name}#{position}"
            identity_feature[synthetic] = ROW_DELIVERIES[name]
            identity_events[synthetic].add(name)
        # provider_delivery.delivery_ids is the wire-binding proof: a staged
        # delivery counts as model exposure only when a request carried it.
        # prepared_deliveries_discarded is the rollback for a refused request -
        # its identities were staged and revoked, which is a refusal.
        if name == "provider_delivery":
            for did in event.get("delivery_ids") or ():
                identity_bound.add(str(did))
        if name == "prepared_deliveries_discarded":
            for did in event.get("delivery_ids") or ():
                identity_refused[str(did)] = True
        supersession = str(event.get("supersession_key") or "")
        evidence_type = str(event.get("evidence_type") or "")
        if not supersession and not evidence_type:
            continue
        # A row without a delivery_identity is not a delivery. `receipt` rows
        # carry an evidence_type but no identity - they are receipts FOR
        # deliveries - and giving each one a synthetic unit inflated every
        # count by the number of receipts. The unit is the delivery or nothing.
        unit = str(event.get("delivery_identity") or "")
        if not unit:
            continue

        # The producer's declared identity beats the envelope. supersession_key
        # says what a delivery IS ("obligations:task"); evidence_type on those
        # same deliveries is the prompt-lane KIND (context_contract for the
        # first contract delivery, context_delta for every one after,
        # gt_session.py:582), which says only how its bytes are budgeted.
        #
        # A capability CHANNEL is checked before the census: execution_evidence
        # and verification_plan are real deliveries that no feature identity
        # claims by design, and letting them fall through to the unattributed
        # bucket reported a transport lane as a census gap.
        feature = channel = None
        if supersession:
            channel = channel_for_evidence(supersession)
            if not channel:
                feature = feature_for_supersession(supersession)
        if not feature and not channel and evidence_type:
            channel = channel_for_evidence(evidence_type)
            if not channel:
                feature = feature_for_evidence(evidence_type)
        if feature:
            identity_feature[unit] = feature
        elif channel:
            identity_channel.setdefault(unit, channel)
        else:
            identity_kinds.setdefault(unit, supersession or evidence_type)
        identity_events[unit].add(name)
        if name in REFUSAL_EVENTS:
            identity_refused[unit] = True

    delivered: collections.Counter[str] = collections.Counter()
    refused: collections.Counter[str] = collections.Counter()
    prepared_unbound: collections.Counter[str] = collections.Counter()
    for unit, feature in identity_feature.items():
        if identity_refused.get(unit):
            refused[feature] += 1
        elif (
            identity_events.get(unit)
            and identity_events[unit] <= STAGING_EVENTS
            and unit not in identity_bound
        ):
            # delivery_prepared / decision_context_unit_prepared stage bytes
            # into the pending provider payload; without a provider_delivery
            # binding (or a second, exposure-side row) the model never saw
            # them. Prepared is not delivered (run 34625781346 already needed
            # the distinction for its discarded staging).
            prepared_unbound[feature] += 1
        else:
            delivered[feature] += 1
    # submit_refusal's delivery is the suppression itself: session.suppress
    # journals action_suppressed rows carrying reason=submit_refused, and the
    # suppressed action's result is what the model sees (gt_session.py:1721).
    # They carry no delivery_identity, so the identity pass above can never
    # see them - count them here or an enforced refusal reads as starvation.
    for event in events:
        if (
            str(event.get("event") or "") == "action_suppressed"
            and str(event.get("reason") or "") == "submit_refused"
        ):
            delivered["submit_refusal"] += 1
    # One delivery can be seen from several sides, and the sides disagree about
    # how much they know: the ledger row carries the supersession key, the lane
    # row carries only the kind. Resolve at the end so the best-informed view of
    # an identity wins, rather than whichever row happened to come first.
    resolved = set(identity_feature) | set(identity_channel)
    channels = collections.Counter(
        channel for unit, channel in identity_channel.items()
        if unit not in identity_feature
    )
    lane_only = collections.Counter(
        kind for unit, kind in identity_kinds.items()
        if unit not in resolved and kind in LANE_KINDS
    )
    unattributed = collections.Counter(
        kind for unit, kind in identity_kinds.items()
        if unit not in resolved and kind not in LANE_KINDS
    )

    from_blobs = edit_eligibility(artifacts or [])
    eligible = {
        **from_blobs,
        **journal_eligibility(events, transactions, known=set(from_blobs)),
    }
    rows = []
    # All 19 identities, not the 12 owners. Seven of the 19 are capability
    # aliases whose evidence is produced by another feature (CAPABILITY_OWNERS),
    # and collapsing them silently reported a 12-row table for a 19-feature
    # census - a reader counting rows would conclude seven features had been
    # dropped. Show every identity; an alias states its owner and inherits its
    # state, so nothing is double counted and nothing is hidden.
    for feature in sorted(DIRECT_FEATURES):
        owner = CAPABILITY_OWNERS.get(feature)
        source = owner or feature
        count, declined = delivered[source], refused[source]
        boundaries = tuple(DIRECT_FEATURES[source].get("boundaries", ()))
        derivable = [b for b in boundaries if b in BOUNDARY_EVIDENCE]
        hits = {b: reached[b] for b in derivable if reached[b]}
        # A boundary is only DENIABLE when its witness is written on every path
        # it could have occurred on. `submit` earns that only once a terminal
        # row exists to read: a run killed before either close row is genuinely
        # unknown, and unknown must not be rendered as "never happened".
        deniable = [
            b for b in derivable
            if b in DECIDABLE_ABSENCE and (b != "submit" or terminal is not None)
        ]
        undecidable = [b for b in boundaries if b not in deniable and b not in hits]
        if count:
            state, evidence = "DELIVERED", f"{count} deliveries the model saw"
        elif declined:
            state, evidence = "REFUSED", f"{declined} refusals"
        elif hits:
            # The distinction that matters, derived rather than left to a reader
            # who happens to know which features are post-edit. "The boundary
            # happened 105 times and this feature said nothing" is a symptom;
            # "the boundary never happened" is not.
            # NOT "SILENT". That word reads as failure, and this state cannot
            # distinguish failure from correct silence.
            #
            # A feature's BOUNDARY firing is not the same as its own precondition
            # firing. syntax_result is eligible only when an edit produced a
            # syntax or name error (bridge.py:2253); across fourteen CLEAN edits
            # its silence is correct behaviour. The producer emits the deciding
            # boolean - _trace_record("feature.evaluated", ..., {"eligible": ...})
            # - but GTBridge, which owns that call, is never constructed on this
            # path: create_bridge (gt_engine/__init__.py:13) is its only
            # instantiation site and has ZERO callers in gt_engine, scripts or
            # eval, and the pinned wheel does not reference it either. So no
            # eligibility row exists to read, in this run or any other.
            #
            # Say what is known. The delivery did not happen; whether the feature
            # declined, failed, or was never eligible is unrecorded.
            state = "NO_DELIVERY"
            where = ", ".join(f"{b} x{n}" for b, n in sorted(hits.items()))
            elig = eligible.get(source)
            if elig is None:
                # An UNKNOWN is a claim about the world; "I did not look" is a
                # claim about the reader. Only one of those is verifiable from
                # in here, and this script used to emit the other. Say the scope.
                # Name the row that would have decided it. "Eligibility not
                # derivable" is a claim about the reader; "the run has no
                # producer_invocation row with feature_id=recovery" is a claim
                # about the journal, and a reader can check it.
                evidence = (
                    f"no delivery at {where}; eligibility not derivable - the run "
                    f"has no producer_invocation row with feature_id={source} "
                    f"(examined: {EXAMINED})"
                )
            elif not elig["eligible"]:
                state = "DECLINED_CORRECTLY"
                evidence = f"no delivery at {where}; {elig['declined_because']}"
                if elig.get("unchecked"):
                    evidence += f"; {elig['unchecked']} could not be checked"
                if elig.get("uncontrolled_negative"):
                    evidence += "; negative uncontrolled - no artifact on record trips it"
            else:
                state = "STARVED"
                detail = ""
                if elig.get("caller_coverage"):
                    unavail = elig["caller_coverage"].get("unavailable", 0)
                    detail = (f"; caller_coverage unavailable on {unavail}/"
                              f"{elig['of']} edits")
                evidence = elig.get("starved_because") or (
                    f"eligible {elig['eligible']}x in {elig['of']} edit "
                    "transactions and delivered nothing"
                )
                evidence += detail
        elif not undecidable:
            state = "NOT_REACHED"
            evidence = "its boundary never occurred: " + ", ".join(sorted(deniable))
            if "submit" in deniable and terminal:
                evidence += f" (run ended {terminal})"
        else:
            # Not "no journal evidence defines X". That sentence was true of the
            # script, not of the journal, and it is what put seven of this run's
            # 21 rows in the unknown column while the deciding rows sat in the
            # same file. An unknown now has to name the row it wanted.
            state = "BOUNDARY_UNKNOWN"
            wanted = [
                label
                for boundary in sorted(undecidable)
                for label, _ in BOUNDARY_EVIDENCE.get(boundary, ())
            ]
            evidence = (
                "no journal row witnesses " + ", ".join(sorted(undecidable))
                + "; the run has no " + ", ".join(wanted) + " row"
                + f" (examined: {EXAMINED})"
            )
        if owner:
            evidence = f"via {owner}: {evidence}"
        rows.append({
            "feature": feature,
            "alias_of": owner or "",
            "state": state,
            "delivered": count,
            "refused": declined,
            "evidence": evidence,
            "kind": DIRECT_FEATURES[feature].get("kind", ""),
            "boundaries": list(boundaries),
            "boundaries_reached": hits,
            # The owner's two questions, separated. A feature declares a
            # trigger; the only things worth knowing are whether that trigger
            # fired and, if it did, whether the feature then did its job.
            # Collapsing them loses the distinction that matters: a feature that
            # never had the chance is not a feature that failed.
            "triggered": {
                "DELIVERED": "yes", "REFUSED": "yes", "NO_DELIVERY": "yes",
                "NOT_REACHED": "no", "BOUNDARY_UNKNOWN": "unknown",
                "DECLINED_CORRECTLY": "yes", "STARVED": "yes",
            }[state],
            "worked": {
                "DELIVERED": "yes", "REFUSED": "no", "NO_DELIVERY": "unknown",
                "NOT_REACHED": "n/a", "BOUNDARY_UNKNOWN": "unknown",
                "DECLINED_CORRECTLY": "yes", "STARVED": "no",
            }[state],
            "trigger": DIRECT_FEATURES[source].get("trigger", ""),
        })
    return {
        "schema": SCHEMA,
        "journal_rows": len(events),
        "identities": len(rows),
        "direct_features": sum(1 for row in rows if not row["alias_of"]),
        "capability_aliases_shown": sum(1 for row in rows if row["alias_of"]),
        "deliveries_resolved": len(identity_feature),
        "prepared_unbound": dict(prepared_unbound),
        "delivered": sum(1 for row in rows if row["state"] == "DELIVERED"),
        "refused": sum(1 for row in rows if row["state"] == "REFUSED"),
        "no_delivery": sum(1 for row in rows if row["state"] == "NO_DELIVERY"),
        "not_reached": sum(1 for row in rows if row["state"] == "NOT_REACHED"),
        "boundary_unknown": sum(1 for row in rows if row["state"] == "BOUNDARY_UNKNOWN"),
        "boundaries_reached": dict(sorted(reached.items())),
        "boundary_witnesses": {
            boundary: dict(sorted(counts.items()))
            for boundary, counts in sorted(witnessed.items())
        },
        "terminal": terminal,
        "capability_aliases": dict(sorted(CAPABILITY_OWNERS.items())),
        # Deliveries that are accounted for WITHOUT being a census feature.
        # They are reported because they are real bytes the model saw, and they
        # are reported separately because calling them unattributed implied a
        # census gap that does not exist.
        "channel_evidence": dict(channels.most_common()),
        "channel_evidence_total": sum(channels.values()),
        "lane_kind_only": dict(lane_only.most_common()),
        "lane_kind_only_total": sum(lane_only.values()),
        "unattributed_evidence": dict(unattributed.most_common()),
        "unattributed_total": sum(unattributed.values()),
        "rows": rows,
    }


def render(report: dict) -> str:
    lines = [
        f"{'FEATURE':22s} {'TRIGGERED':10s} {'WORKED':7s} EVIDENCE FROM THE RUN",
        "-" * 96,
    ]
    for row in report["rows"]:
        lines.append(
            f"{row['feature']:22s} {row['triggered']:10s} {row['worked']:7s} {row['evidence']}"
        )
    lines.append("-" * 96)
    t = collections.Counter(row["triggered"] for row in report["rows"])
    w = collections.Counter(row["worked"] for row in report["rows"])
    lines.append(
        f"TRIGGERED  yes {t['yes']}  no {t['no']}  unknown {t['unknown']}"
        f"      WORKED  yes {w['yes']}  no {w['no']}  n/a {w['n/a']}  unknown {w['unknown']}"
    )
    lines.append(
        f"{report['identities']} identities = {report['direct_features']} features"
        f" + {report['capability_aliases_shown']} capability aliases"
        f"   ({report['journal_rows']} journal rows, terminal {report.get('terminal')})"
    )
    lines.append("")
    lines.append("WHAT EACH ONE IS WAITING FOR:")
    for row in report["rows"]:
        if row["trigger"]:
            lines.append(f"  {row['feature']:22s} {row['trigger']}")
    if report.get("channel_evidence_total"):
        lines.append("")
        lines.append(
            f"channel evidence (not a feature) - {report['channel_evidence_total']} "
            "deliveries a capability ships as a lane, which no census identity claims:"
        )
        for kind, count in report["channel_evidence"].items():
            lines.append(f"    {kind:38s} {count}")
    if report.get("lane_kind_only_total"):
        lines.append("")
        lines.append(
            f"prompt-lane kind only - {report['lane_kind_only_total']} deliveries "
            "carrying a byte-budget kind and no supersession key:"
        )
        for kind, count in report["lane_kind_only"].items():
            lines.append(f"    {kind:38s} {count}")
    if report["unattributed_total"]:
        lines.append("")
        lines.append(
            f"UNATTRIBUTED evidence ({report['unattributed_total']} items) - "
            "a type no feature claims:"
        )
        for kind, count in report["unattributed_evidence"].items():
            lines.append(f"    {kind:38s} {count}")
    return chr(10).join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal", help="events.jsonl from the run, or a directory holding it")
    parser.add_argument("--json-out", help="write the gt.feature_accounting.v1 report here")
    args = parser.parse_args()

    path = Path(args.journal)
    if path.is_dir():
        found = sorted(path.rglob("events.jsonl"))
        if not found:
            print(f"no events.jsonl under {path}", file=sys.stderr)
            return 2
        path = found[0]
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    blobs = sorted(path.parent.glob("transaction_artifacts/*.json"))
    artifacts = [json.loads(b.read_text(encoding="utf-8")) for b in blobs]
    # `edit_transactions/` is a SEPARATE directory from `transaction_artifacts/`
    # and carries a different thing: the per-change `operation`
    # (create/delete/modify) that newfile_precedent's precondition is made of.
    # None, not [], when the directory is absent - an empty list would let the
    # script report "nothing was created" about a run it never opened.
    edits = sorted(path.parent.glob("edit_transactions/*.json"))
    transactions = (
        [json.loads(b.read_text(encoding="utf-8")) for b in edits]
        if (path.parent / "edit_transactions").is_dir() else None
    )
    report = account(events, artifacts, transactions)
    print(render(report))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
