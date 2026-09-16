"""What `scripts/feature_accounting.py` may and may not claim about a run.

Every case here is a synthetic journal shaped like the real one. The shapes are
taken from two real journals - run 34996816912 (dynaconf, ended `timeout`) and
run 34907273607 (gitingest, ended `submitted_unverified`) - so a field name in
these fixtures is a field name the producer actually writes, not one invented
to make a test pass. Where a field is load-bearing the test names it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.feature_accounting import account, render  # noqa: E402


def delivery(unit: str, *, evidence_type: str = "", supersession: str = "",
             bound: bool = True) -> list[dict]:
    """The rows the producer writes for one delivery, from all three sides.

    `decision_context_unit_prepared` carries `supersession_key` (what the
    delivery IS); `delivery_prepared` carries `evidence_type` (the prompt-lane
    KIND its bytes are budgeted as). Both carry `delivery_identity` and both
    are STAGING: the unit only counts as model exposure once a
    `provider_delivery` row binds it to a real request (`bound=True`, the
    production shape - run 35016130850 bound 74/74 prepared identities).
    """
    rows = []
    if supersession:
        rows.append({
            "event": "decision_context_unit_prepared",
            "delivery_identity": unit,
            "supersession_key": supersession,
        })
    if evidence_type:
        rows.append({
            "event": "delivery_prepared",
            "delivery_identity": unit,
            "evidence_type": evidence_type,
        })
    if bound and rows:
        rows.append({
            "event": "provider_delivery",
            "delivery_ids": [unit],
        })
    return rows


def row_for(feature: str, report: dict) -> dict:
    return next(row for row in report["rows"] if row["feature"] == feature)


TASK_START = {"event": "runtime_layout"}


# --------------------------------------------------------------------------
# (a) evidence-type attribution
# --------------------------------------------------------------------------

def test_execution_evidence_is_a_channel_and_never_unattributed():
    # Arrange - a test-output delivery, keyed `execution:<command_sha256>`.
    events = [TASK_START, *delivery(
        "u1", evidence_type="execution_evidence", supersession="execution:abc",
    )]

    # Act
    report = account(events)

    # Assert
    assert report["unattributed_total"] == 0
    assert report["channel_evidence"] == {"execution_evidence": 1}


def test_verification_plan_is_a_channel_and_never_unattributed():
    events = [TASK_START, *delivery(
        "u1", evidence_type="verification_plan",
        supersession="verification_plan:pkg/mod.py",
    )]

    report = account(events)

    assert report["unattributed_total"] == 0
    assert report["channel_evidence"] == {"verification_plan": 1}


def test_a_channel_is_never_counted_as_a_census_feature():
    events = [TASK_START, *delivery(
        "u1", evidence_type="execution_evidence", supersession="execution:abc",
    )]

    report = account(events)

    assert all(row["delivered"] == 0 for row in report["rows"])


def test_context_delta_with_a_supersession_key_resolves_through_it():
    # gt_session.py:_plan_cursor_candidate ships the plan cursor as prompt-lane
    # KIND context_delta under supersession_key `plan_cursor:task`.
    events = [TASK_START, *delivery(
        "u1", evidence_type="context_delta", supersession="plan_cursor:task",
    )]

    report = account(events)

    assert report["unattributed_total"] == 0
    assert row_for("persistent_plan", report)["delivered"] == 1


def test_context_delta_without_a_supersession_key_is_lane_kind_only():
    # gt_session.py:_finalization_candidate ships an advisory with a dedup_key
    # and no supersession_key. It is a lane kind, not a feature, and it must
    # not be reported as evidence no feature claims.
    events = [TASK_START, *delivery("u1", evidence_type="context_delta")]

    report = account(events)

    assert report["unattributed_total"] == 0
    assert report["lane_kind_only"] == {"context_delta": 1}
    assert all(row["delivered"] == 0 for row in report["rows"])


def test_an_unknown_evidence_type_is_still_reported_unattributed():
    events = [TASK_START, *delivery("u1", evidence_type="not_a_real_type")]

    report = account(events)

    assert report["unattributed_evidence"] == {"not_a_real_type": 1}


def test_render_shows_channels_under_their_own_heading():
    events = [TASK_START, *delivery(
        "u1", evidence_type="execution_evidence", supersession="execution:abc",
    )]

    text = render(account(events))

    assert "channel evidence (not a feature)" in text
    assert "UNATTRIBUTED" not in text


# --------------------------------------------------------------------------
# (b) the submit boundary
# --------------------------------------------------------------------------

def test_submit_is_not_reached_when_the_run_ends_timeout_without_a_gate():
    # The dynaconf case: 0 plan_gate_decision rows, submission_patch_observed
    # only at `verification`/`reserve`, run_terminal terminal=timeout.
    events = [
        TASK_START,
        {"event": "submission_patch_observed", "stage": "verification"},
        {"event": "submission_patch_observed", "stage": "reserve"},
        {"event": "run_terminal", "terminal": "timeout",
         "supervisor_reason": "deadline_exceeded"},
    ]

    report = account(events)
    row = row_for("submit_refusal", report)

    assert row["state"] == "NOT_REACHED"
    assert row["triggered"] == "no"
    assert row["evidence"] == "its boundary never occurred: submit (run ended timeout)"


@pytest.mark.parametrize("witness", [
    {"event": "plan_gate_decision", "accepted": False},
    {"event": "submission_patch_observed", "stage": "submit"},
    {"event": "session_closed", "terminal": "submitted_unverified"},
    {"event": "run_terminal", "terminal": "submitted_verified"},
])
def test_submit_is_reached_by_any_of_its_three_witnesses(witness):
    report = account([TASK_START, witness])

    assert row_for("submit_refusal", report)["boundaries_reached"].get("submit")
    assert row_for("submit_refusal", report)["triggered"] == "yes"


def test_run_terminal_supplies_the_terminal_when_no_session_closed_exists():
    report = account([TASK_START, {"event": "run_terminal", "terminal": "timeout"}])

    assert report["terminal"] == "timeout"


def test_a_run_killed_before_any_terminal_row_leaves_submit_unknown():
    # No session_closed, no run_terminal, no gate: genuinely unknown, and
    # unknown must not read as NOT_REACHED.
    report = account([TASK_START])
    row = row_for("submit_refusal", report)

    assert row["state"] == "BOUNDARY_UNKNOWN"
    assert "session_closed" in row["evidence"]
    assert "run_terminal" in row["evidence"]


# --------------------------------------------------------------------------
# (c) the test_result, tool_result and search_result boundaries
# --------------------------------------------------------------------------

def test_test_result_comes_from_execution_evidence_kind_test():
    events = [TASK_START, {"event": "execution_evidence", "kind": "test",
                           "observed_test_outcome": "pass"}]

    report = account(events)

    assert report["boundaries_reached"]["test_result"] == 1


def test_a_non_test_execution_evidence_row_does_not_reach_test_result():
    events = [TASK_START, {"event": "execution_evidence", "kind": "command"}]

    report = account(events)

    assert "test_result" not in report["boundaries_reached"]


def test_tool_result_comes_from_execution_finished():
    events = [TASK_START, {"event": "execution_finished", "disposition": "returned"}]

    report = account(events)

    assert report["boundaries_reached"]["tool_result"] == 1


def test_search_result_comes_from_producer_invocation_event_type():
    # execution_started/execution_finished carry only `action_sha256`, never the
    # command, so a search cannot be identified there. `producer_invocation`
    # carries `event_type`, which IS the boundary vocabulary
    # (miniswe_evidence._derive_semantic_events).
    events = [TASK_START, {"event": "producer_invocation",
                           "event_type": "search_result",
                           "producer": "ranked_localization"}]

    report = account(events)

    assert report["boundaries_reached"]["search_result"] == 1
    assert row_for("def_partition", report)["triggered"] == "yes"


def test_a_failed_search_also_reaches_the_search_boundary():
    events = [TASK_START, {"event": "producer_invocation",
                           "event_type": "failed_search"}]

    report = account(events)

    assert report["boundaries_reached"]["search_result"] == 1


def test_no_search_witness_names_the_missing_field_rather_than_denying_it():
    # producer_invocation fires per producer dispatch, not per boundary, so its
    # absence does not prove the boundary never happened.
    report = account([TASK_START])
    row = row_for("def_partition", report)

    assert row["state"] == "BOUNDARY_UNKNOWN"
    assert row["triggered"] == "unknown"
    assert "producer_invocation.event_type=search_result" in row["evidence"]


# --------------------------------------------------------------------------
# (d) eligibility derivations
# --------------------------------------------------------------------------

def test_covering_red_is_eligible_when_a_failing_test_follows_a_source_edit():
    events = [
        TASK_START,
        {"event": "edit_transaction", "changed_paths": ["pkg/mod.py"]},
        {"event": "execution_evidence", "kind": "test", "observed_test_outcome": "fail"},
    ]

    row = row_for("covering_red", account(events))

    assert row["state"] == "STARVED"
    assert row["worked"] == "no"
    assert "observed_test_outcome=fail" in row["evidence"]


def test_covering_red_declines_correctly_when_every_test_passed_after_the_edit():
    events = [
        TASK_START,
        {"event": "edit_transaction", "changed_paths": ["pkg/mod.py"]},
        {"event": "execution_evidence", "kind": "test", "observed_test_outcome": "pass"},
    ]

    row = row_for("covering_red", account(events))

    assert row["state"] == "DECLINED_CORRECTLY"
    assert row["worked"] == "yes"


def test_covering_red_ignores_a_failure_that_preceded_the_edit():
    events = [
        TASK_START,
        {"event": "execution_evidence", "kind": "test", "observed_test_outcome": "fail"},
        {"event": "edit_transaction", "changed_paths": ["pkg/mod.py"]},
    ]

    assert row_for("covering_red", account(events))["state"] == "DECLINED_CORRECTLY"


def test_covering_red_does_not_count_a_test_only_edit_as_a_source_edit():
    events = [
        TASK_START,
        {"event": "edit_transaction", "changed_paths": ["tests/test_mod.py"]},
        {"event": "execution_evidence", "kind": "test", "observed_test_outcome": "fail"},
    ]

    assert row_for("covering_red", account(events))["state"] == "DECLINED_CORRECTLY"


# --------------------------------------------------------------------------
# (a.2) feature_evaluated rows - the local lanes' own census outranks proxies
# --------------------------------------------------------------------------

def test_feature_evaluated_outranks_the_covering_ordering_proxy():
    # The journal shows the proxy's eligible ordering (edit -> failing test)
    # but the lane's own row says the failure never named an edited file.
    # The lane verdict wins: this is a correct abstention, not starvation.
    events = [
        TASK_START,
        {"event": "edit_transaction", "changed_paths": ["pkg/mod.py"]},
        {"event": "execution_evidence", "kind": "test", "observed_test_outcome": "fail"},
        {
            "event": "feature_evaluated", "feature_id": "covering_red",
            "boundary": "test_result", "eligible": True,
            "outcome": "no_edited_file_link",
        },
    ]

    row = row_for("covering_red", account(events))

    assert row["state"] == "DECLINED_CORRECTLY"
    assert row["worked"] == "yes"
    assert "naming an edited file" in row["evidence"]


def test_feature_evaluated_covering_attributed_but_undelivered_is_starved():
    events = [
        TASK_START,
        {"event": "edit_transaction", "changed_paths": ["pkg/mod.py"]},
        {"event": "execution_evidence", "kind": "test", "observed_test_outcome": "fail"},
        {
            "event": "feature_evaluated", "feature_id": "covering_red",
            "boundary": "test_result", "eligible": True,
            "outcome": "attributed",
        },
    ]

    row = row_for("covering_red", account(events))

    assert row["state"] == "STARVED"
    assert "outcome=attributed" in row["evidence"]


def test_feature_evaluated_covering_without_prior_edit_declines():
    # The lane ran on a failing test and correctly saw nothing to attribute.
    # covering_red's declared boundaries are edit_result+submit; with no edit
    # at all, a submit witness (terminal row) is what makes the state
    # decidable - exactly the honest shape a no-edit run produces.
    events = [
        TASK_START,
        {"event": "execution_evidence", "kind": "test", "observed_test_outcome": "fail"},
        {"event": "session_closed", "terminal": "submitted_unverified"},
        {
            "event": "feature_evaluated", "feature_id": "covering_red",
            "boundary": "test_result", "eligible": False,
            "outcome": "no_prior_edit",
        },
    ]

    assert row_for("covering_red", account(events))["state"] == "DECLINED_CORRECTLY"


def test_feature_evaluated_recovery_tracked_no_steer_declines_correctly():
    events = [
        TASK_START,
        {"event": "execution_evidence", "kind": "test", "observed_test_outcome": "fail"},
        {
            "event": "feature_evaluated", "feature_id": "recovery",
            "boundary": "test_result", "eligible": True,
            "outcome": "tracked_no_steer",
        },
        {
            "event": "feature_evaluated", "feature_id": "recovery",
            "boundary": "test_result", "eligible": True,
            "outcome": "tracked_no_steer",
        },
    ]

    row = row_for("recovery", account(events))

    assert row["state"] == "DECLINED_CORRECTLY"
    assert row["worked"] == "yes"
    assert "fingerprints tracked" in row["evidence"]


def test_feature_evaluated_recovery_steer_due_but_undelivered_is_starved():
    events = [
        TASK_START,
        {"event": "execution_evidence", "kind": "test", "observed_test_outcome": "fail"},
        {
            "event": "feature_evaluated", "feature_id": "recovery",
            "boundary": "test_result", "eligible": True,
            "outcome": "steer_due",
        },
    ]

    row = row_for("recovery", account(events))

    assert row["state"] == "STARVED"
    assert "steer_due" in row["evidence"]


def test_def_partition_declines_correctly_when_the_lattice_never_matched():
    # Run 35016130850 dynaconf: 72 search-boundary invocations, zero
    # def_ref_partition rows. Dispatch on AMBIGUOUS_HIT/FLOOD is
    # unconditional, so absence of a row means the outcome never classified
    # ambiguous - never eligible, a correct abstention, not NO_DELIVERY.
    events = [
        TASK_START,
        {"event": "producer_invocation", "event_type": "search_result",
         "producer": "ranked_localization", "outcome": "entered"},
        {"event": "producer_invocation", "event_type": "search_result",
         "producer": "ranked_localization", "outcome": "returned_fact"},
        {"event": "producer_invocation", "event_type": "failed_search",
         "producer": "ranked_localization", "outcome": "returned_nothing"},
    ]

    row = row_for("def_partition", account(events))

    assert row["state"] == "DECLINED_CORRECTLY"
    assert "AMBIGUOUS_HIT/FLOOD" in row["evidence"]


def test_def_partition_dispatched_and_abstained_declines_correctly():
    # Dispatched (the lattice matched) but the producer found no production
    # definition to assert - a correct abstention, not starvation.
    events = [
        TASK_START,
        {"event": "producer_invocation", "event_type": "search_result",
         "producer": "def_ref_partition", "feature_id": "def_partition",
         "outcome": "entered", "returned_fact": False},
    ]

    row = row_for("def_partition", account(events))

    assert row["state"] == "DECLINED_CORRECTLY"
    assert "dispatched 1x" in row["evidence"]


def test_def_partition_returned_fact_but_undelivered_is_starved():
    events = [
        TASK_START,
        {"event": "producer_invocation", "event_type": "search_result",
         "producer": "def_ref_partition", "feature_id": "def_partition",
         "outcome": "returned_fact", "returned_fact": True},
    ]

    row = row_for("def_partition", account(events))

    assert row["state"] == "STARVED"
    assert "returned_fact=true" in row["evidence"]


def test_submit_refusal_counts_action_suppressed_as_the_delivery():
    # Run 35016130850 dynaconf: the gate saw active_red and the refusal WAS
    # enforced via session.suppress -> action_suppressed reason=submit_refused
    # (the suppressed action's result is what the model sees). Those rows
    # carry no delivery_identity, so without this arm an enforced refusal
    # audits as STARVED.
    events = [
        TASK_START,
        {"event": "submit_decision", "active_red": ["pred-1"]},
        {"event": "action_suppressed", "reason": "submit_refused"},
        {"event": "action_suppressed", "reason": "submit_refused"},
        {"event": "session_closed", "terminal": "submitted_unverified"},
    ]

    report = account(events)

    assert row_for("submit_refusal", report)["state"] == "DELIVERED"
    assert row_for("submit_refusal", report)["delivered"] == 2


def test_submit_refusal_without_suppression_still_starves():
    events = [
        TASK_START,
        {"event": "submit_decision", "active_red": ["pred-1"]},
        {"event": "session_closed", "terminal": "submitted_unverified"},
    ]

    assert row_for("submit_refusal", account(events))["state"] == "STARVED"


def test_newfile_precedent_is_eligible_when_a_change_operation_is_create():
    # runtime_observation.py:594 - operation is one of create/delete/modify.
    events = [TASK_START, {"event": "edit_transaction", "changed_paths": ["pkg/new.py"]}]
    transactions = [{"changes": [{"operation": "create", "path": "pkg/new.py"}]}]

    row = row_for("newfile_precedent", account(events, [], transactions))

    assert row["state"] == "STARVED"
    assert "operation=create" in row["evidence"]


def test_newfile_precedent_declines_when_every_change_is_a_modify():
    events = [TASK_START, {"event": "edit_transaction", "changed_paths": ["pkg/mod.py"]}]
    transactions = [{"changes": [{"operation": "modify", "path": "pkg/mod.py"}]}]

    row = row_for("newfile_precedent", account(events, [], transactions))

    assert row["state"] == "DECLINED_CORRECTLY"
    assert row["worked"] == "yes"


def test_newfile_precedent_stays_underivable_without_the_transaction_blobs():
    # Absence of the blobs is a statement about where this script looked.
    events = [TASK_START, {"event": "edit_transaction", "changed_paths": ["pkg/mod.py"]}]

    row = row_for("newfile_precedent", account(events, [], None))

    assert row["state"] == "NO_DELIVERY"
    assert "eligibility not derivable" in row["evidence"]


def test_a_transaction_recording_no_per_file_change_is_reported_unchecked():
    events = [TASK_START, {"event": "edit_transaction", "changed_paths": ["pkg/mod.py"]}]
    transactions = [{"changes": []}]

    row = row_for("newfile_precedent", account(events, [], transactions))

    assert "1 could not be checked" in row["evidence"]


def test_persistent_plan_is_delivered_by_its_own_journal_row():
    events = [TASK_START,
              {"event": "persistent_plan_built", "status": "PARTIAL"},
              {"event": "persistent_plan_delivered", "plan_rows": 1}]

    row = row_for("persistent_plan", account(events))

    assert row["state"] == "DELIVERED"
    assert row["delivered"] == 1


def test_persistent_plan_built_but_never_delivered_is_starved():
    events = [TASK_START, {"event": "persistent_plan_built", "status": "PARTIAL"}]

    row = row_for("persistent_plan", account(events))

    assert row["state"] == "STARVED"
    assert "persistent_plan_built" in row["evidence"]


def test_persistent_plan_without_a_built_row_declined_correctly():
    row = row_for("persistent_plan", account([TASK_START]))

    assert row["state"] == "DECLINED_CORRECTLY"
    assert "no persistent_plan_built row" in row["evidence"]


def test_plan_gate_directive_is_a_delivery_of_the_plan_gate():
    events = [
        TASK_START,
        {"event": "plan_gate_decision", "accepted": False},
        {"event": "plan_gate_directive_prepared", "rendered_bytes": 400},
        {"event": "session_closed", "terminal": "submitted_unverified"},
    ]

    row = row_for("plan_gate", account(events))

    assert row["state"] == "DELIVERED"
    assert row["delivered"] == 1


def test_plan_gate_that_accepted_every_submission_declined_correctly():
    events = [
        TASK_START,
        {"event": "plan_gate_decision", "accepted": True},
        {"event": "session_closed", "terminal": "submitted_verified"},
    ]

    row = row_for("plan_gate", account(events))

    assert row["state"] == "DECLINED_CORRECTLY"
    assert "plan_gate_decision" in row["evidence"]


def test_submit_refusal_declines_correctly_when_active_red_was_empty():
    events = [
        TASK_START,
        {"event": "submit_decision", "accepted": True, "active_red": []},
        {"event": "session_closed", "terminal": "submitted_unverified"},
    ]

    row = row_for("submit_refusal", account(events))

    assert row["state"] == "DECLINED_CORRECTLY"
    assert "active_red" in row["evidence"]


def test_submit_refusal_with_an_unresolved_red_and_no_refusal_is_starved():
    events = [
        TASK_START,
        {"event": "submit_decision", "accepted": True, "active_red": ["t::a"]},
        {"event": "session_closed", "terminal": "submitted_unverified"},
    ]

    row = row_for("submit_refusal", account(events))

    assert row["state"] == "STARVED"
    assert row["worked"] == "no"


def test_a_capability_alias_inherits_its_owner_state():
    events = [
        TASK_START,
        {"event": "submit_decision", "accepted": True, "active_red": []},
        {"event": "session_closed", "terminal": "submitted_unverified"},
    ]

    report = account(events)

    assert row_for("GT_SS_SUBMIT_RED", report)["state"] == "DECLINED_CORRECTLY"
    assert row_for("GT_SS_SUBMIT_RED", report)["evidence"].startswith("via submit_refusal:")


# --------------------------------------------------------------------------
# the report-level contract the owner reads
# --------------------------------------------------------------------------

def test_every_not_reached_row_names_its_boundary():
    events = [TASK_START, {"event": "run_terminal", "terminal": "timeout"}]

    report = account(events)

    for row in report["rows"]:
        if row["state"] == "NOT_REACHED":
            assert "its boundary never occurred: " in row["evidence"]
            assert row["evidence"].split("its boundary never occurred: ")[1].strip()


def test_every_unknown_trigger_names_the_journal_row_it_is_missing():
    report = account([TASK_START])

    for row in report["rows"]:
        if row["triggered"] == "unknown":
            assert "no journal row witnesses" in row["evidence"]
            assert "the run has no " in row["evidence"]


def test_producer_invocation_rows_decide_eligibility_when_nothing_better_exists():
    # `feature_id` is stamped by the producer recorder from the invocation's own
    # evidence_types, so this is the producer naming its census identity.
    events = [
        TASK_START,
        {"event": "producer_invocation", "event_type": "search_result",
         "feature_id": "def_partition", "outcome": "entered",
         "returned_fact": False},
        {"event": "producer_invocation", "event_type": "search_result",
         "feature_id": "def_partition", "outcome": "returned_nothing",
         "returned_fact": False},
    ]

    row = row_for("def_partition", account(events))

    assert row["state"] == "DECLINED_CORRECTLY"
    assert "returned_fact=true" in row["evidence"]


def test_a_producer_that_returned_a_fact_and_delivered_nothing_is_starved():
    events = [
        TASK_START,
        {"event": "producer_invocation", "event_type": "search_result",
         "feature_id": "def_partition", "outcome": "returned_fact",
         "returned_fact": True},
    ]

    assert row_for("def_partition", account(events))["state"] == "STARVED"


def test_an_underivable_row_names_the_producer_invocation_it_is_missing():
    # With no search boundary witnessed at all there is nothing to derive
    # from: search_result is not a decidable-absence boundary, so the honest
    # state is unknown - and it names the row that would have decided it.
    events = [TASK_START]

    row = row_for("def_partition", account(events))

    assert row["state"] == "BOUNDARY_UNKNOWN"
    assert "producer_invocation" in row["evidence"]


def test_blob_derived_eligibility_outranks_the_producer_invocation_fallback():
    events = [
        TASK_START,
        {"event": "edit_transaction", "changed_paths": ["pkg/mod.py"]},
        {"event": "producer_invocation", "event_type": "edit_result",
         "feature_id": "newfile_precedent", "outcome": "returned_fact",
         "returned_fact": True},
    ]
    transactions = [{"changes": [{"operation": "modify", "path": "pkg/mod.py"}]}]

    row = row_for("newfile_precedent", account(events, [], transactions))

    assert row["state"] == "DECLINED_CORRECTLY"
    assert "operation=create" in row["evidence"]
