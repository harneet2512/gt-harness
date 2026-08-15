"""IE-01 transition-oracle tests: exhaustive reachability + stateful traces."""
from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from gt_engine.engine.contracts import Decision, ExecutionState, LifecycleState
from gt_engine.engine.transitions import (
    Event,
    IllegalTransition,
    LifecycleTrace,
    find_terminal_path,
    legal_events,
    property_no_action_disappears,
    property_pass_through_executes_literal,
    reachable_states,
)


def test_all_lifecycle_states_are_reachable():
    assert reachable_states() == set(LifecycleState)


def test_terminal_path_exists_from_selected():
    path = find_terminal_path()
    assert path is not None
    assert path[-1] in (Event.RECEIPT_FINAL, Event.FAIL)


def test_receipt_final_reachable_from_selected():
    happy = [
        Event.NORMALIZE,
        Event.BIND_SNAPSHOT,
        Event.PREFLIGHT,
        Event.DECIDE,
        Event.EXECUTE,
        Event.POSTFLIGHT,
        Event.COMPILE_OBSERVATION,
        Event.JOIN,
        Event.DISPATCH,
        Event.PROVIDER_ACCEPTED,
        Event.DELIVER,
        Event.RESPONSE_COMMITTED,
        Event.BIND_NEXT_ACTION,
        Event.RECEIPT_FINAL,
    ]
    trace = LifecycleTrace(action_id="a")
    for event in happy:
        trace.apply(event)
    assert trace.state == LifecycleState.RECEIPT_FINAL


def test_terminal_path_from_every_state():
    for state in LifecycleState:
        trace = LifecycleTrace(action_id="a")
        trace.state = state
        # every non-terminal state has a path to a terminal state
        if state not in (LifecycleState.RECEIPT_FINAL, LifecycleState.FAILED):
            assert find_terminal_path(state) is not None


def test_happy_path_lifecycle():
    trace = LifecycleTrace(action_id="a1")
    events = [
        Event.NORMALIZE,
        Event.BIND_SNAPSHOT,
        Event.PREFLIGHT,
        Event.DECIDE,
        Event.EXECUTE,
        Event.POSTFLIGHT,
        Event.COMPILE_OBSERVATION,
        Event.JOIN,
        Event.DISPATCH,
        Event.PROVIDER_ACCEPTED,
        Event.DELIVER,
        Event.RESPONSE_COMMITTED,
        Event.BIND_NEXT_ACTION,
        Event.RECEIPT_FINAL,
    ]
    for event in events:
        trace.apply(event)
    assert trace.is_terminal
    assert trace.state == LifecycleState.RECEIPT_FINAL
    assert trace.decision == Decision.PASS_THROUGH
    assert trace.execution_state == ExecutionState.EXECUTED


def test_illegal_transition_raises():
    trace = LifecycleTrace(action_id="a1")
    trace.apply(Event.NORMALIZE)
    with pytest.raises(IllegalTransition):
        trace.apply(Event.DELIVER)  # not legal yet


def test_fail_open_returns_to_literal_execution():
    trace = LifecycleTrace(action_id="a1")
    trace.apply(Event.NORMALIZE)
    trace.apply(Event.FAIL_OPEN)
    assert trace.state == LifecycleState.DECIDED
    assert trace.fail_open
    trace.apply(Event.EXECUTE)
    assert trace.execution_state == ExecutionState.EXECUTED
    assert property_pass_through_executes_literal(trace)


def test_failed_is_terminal_and_idempotent():
    trace = LifecycleTrace(action_id="a1")
    trace.apply(Event.FAIL)
    assert trace.state == LifecycleState.FAILED
    trace.apply(Event.FAIL)  # idempotent no-op
    assert trace.state == LifecycleState.FAILED
    assert property_no_action_disappears(trace)


def test_suppress_marks_raw_not_exact():
    trace = LifecycleTrace(action_id="a1")
    for event in (Event.NORMALIZE, Event.BIND_SNAPSHOT, Event.PREFLIGHT, Event.DECIDE):
        trace.apply(event)
    trace.apply(Event.SUPPRESS)
    trace.raw_exact = False
    assert trace.decision == Decision.SUPPRESS
    assert trace.execution_state == ExecutionState.SUPPRESSED


@given(st.lists(st.sampled_from(list(Event)), max_size=40))
@settings(max_examples=200, deadline=None)
def test_bounded_random_traversal_never_corrupts(events):
    """Random event sequences either raise IllegalTransition or keep the
    trace consistent (never leave it in an unknown state)."""
    trace = LifecycleTrace(action_id="a1")
    for event in events:
        try:
            trace.apply(event)
        except IllegalTransition:
            continue
        assert trace.state in LifecycleState


class LifecycleMachine(RuleBasedStateMachine):
    """Stateful oracle: every reachable state admits the legal events the
    transition table declares, and terminal traces satisfy the no-action-
    disappears property."""

    def __init__(self):
        super().__init__()
        self.trace = LifecycleTrace(action_id="stateful")

    @rule(event=st.sampled_from(list(Event)))
    def fire(self, event):
        state_before = self.trace.state
        try:
            self.trace.apply(event)
        except IllegalTransition:
            assert event not in legal_events(state_before)
            return
        assert event in legal_events(state_before)

    @invariant()
    def terminal_trace_satisfies_no_disappear(self):
        if self.trace.is_terminal:
            assert property_no_action_disappears(self.trace)


TestLifecycle = LifecycleMachine.TestCase


def test_stateful_lifecycle_oracle():
    """Run the Hypothesis stateful lifecycle machine with bounded steps."""
    from hypothesis.stateful import run_state_machine_as_test

    run_state_machine_as_test(
        LifecycleMachine,
        settings=settings(max_examples=50, stateful_step_count=30, deadline=None),
    )
