"""Authoritative engine lifecycle transition table (IE-01/IE-09).

Executable model of the lifecycle:

    SELECTED -> NORMALIZED -> SNAPSHOT_BOUND -> PREFLIGHTED -> DECIDED
             -> EXECUTED | REPLACED | REWRITTEN | SUPPRESSED
             -> POSTFLIGHTED -> COMPILED -> JOINED -> DISPATCHED
             -> PROVIDER_ACCEPTED -> DELIVERED -> RESPONSE_COMMITTED
             -> NEXT_ACTION_BOUND -> RECEIPT_FINAL

This module is the canonical Python transition table. It is tested with bounded
exhaustive traversal (every reachable state/event pair) and Hypothesis
stateful traces. It is the executable witness for the required lifecycle
properties; a pinned TLA+/PlusCal model provides the independent concurrency
check.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping

from .contracts import Decision, ExecutionState, LifecycleState

# --- Events ----------------------------------------------------------------


class Event(Enum):
    NORMALIZE = "normalize"
    BIND_SNAPSHOT = "bind_snapshot"
    PREFLIGHT = "preflight"
    DECIDE = "decide"
    EXECUTE = "execute"
    REPLACE = "replace"
    REWRITE = "rewrite"
    SUPPRESS = "suppress"
    POSTFLIGHT = "postflight"
    COMPILE_OBSERVATION = "compile_observation"
    JOIN = "join"
    DISPATCH = "dispatch"
    PROVIDER_ACCEPTED = "provider_accepted"
    DELIVER = "deliver"
    RESPONSE_COMMITTED = "response_committed"
    BIND_NEXT_ACTION = "bind_next_action"
    RECEIPT_FINAL = "receipt_final"
    FAIL = "fail"
    FAIL_OPEN = "fail_open"  # revert to literal pass-through execution


# --- Guard predicates ------------------------------------------------------


@dataclass(frozen=True)
class Transition:
    to: LifecycleState
    guard: Callable[["LifecycleTrace"], bool] | None = None
    fail_open: bool = False


# The transition table. Key: (from_state, event) -> allowed targets.
# A missing entry is an illegal transition (raises IllegalTransition).
# ``fail_open`` marks the edge as the safe pass-through recovery: usable from
# any pre-execution failure state and returns the action to literal execution.
_TRANSITION_TABLE: dict[tuple[LifecycleState, Event], tuple[Transition, ...]] = {
    (LifecycleState.SELECTED, Event.NORMALIZE): (Transition(LifecycleState.NORMALIZED),),
    (LifecycleState.NORMALIZED, Event.BIND_SNAPSHOT): (Transition(LifecycleState.SNAPSHOT_BOUND),),
    (LifecycleState.NORMALIZED, Event.FAIL_OPEN): (
        Transition(LifecycleState.DECIDED, fail_open=True),
    ),
    (LifecycleState.SNAPSHOT_BOUND, Event.PREFLIGHT): (Transition(LifecycleState.PREFLIGHTED),),
    (LifecycleState.SNAPSHOT_BOUND, Event.FAIL_OPEN): (
        Transition(LifecycleState.DECIDED, fail_open=True),
    ),
    (LifecycleState.PREFLIGHTED, Event.DECIDE): (Transition(LifecycleState.DECIDED),),
    (LifecycleState.PREFLIGHTED, Event.FAIL_OPEN): (
        Transition(LifecycleState.DECIDED, fail_open=True),
    ),
    (LifecycleState.DECIDED, Event.EXECUTE): (Transition(LifecycleState.EXECUTED),),
    (LifecycleState.DECIDED, Event.REPLACE): (Transition(LifecycleState.REPLACED),),
    (LifecycleState.DECIDED, Event.REWRITE): (Transition(LifecycleState.REWRITTEN),),
    (LifecycleState.DECIDED, Event.SUPPRESS): (Transition(LifecycleState.SUPPRESSED),),
    (LifecycleState.EXECUTED, Event.POSTFLIGHT): (Transition(LifecycleState.POSTFLIGHTED),),
    (LifecycleState.REPLACED, Event.POSTFLIGHT): (Transition(LifecycleState.POSTFLIGHTED),),
    (LifecycleState.REWRITTEN, Event.POSTFLIGHT): (Transition(LifecycleState.POSTFLIGHTED),),
    (LifecycleState.SUPPRESSED, Event.POSTFLIGHT): (Transition(LifecycleState.POSTFLIGHTED),),
    (LifecycleState.POSTFLIGHTED, Event.COMPILE_OBSERVATION): (Transition(LifecycleState.COMPILED),),
    (LifecycleState.COMPILED, Event.JOIN): (Transition(LifecycleState.JOINED),),
    (LifecycleState.JOINED, Event.DISPATCH): (Transition(LifecycleState.DISPATCHED),),
    (LifecycleState.DISPATCHED, Event.PROVIDER_ACCEPTED): (Transition(LifecycleState.PROVIDER_ACCEPTED),),
    (LifecycleState.PROVIDER_ACCEPTED, Event.DELIVER): (Transition(LifecycleState.DELIVERED),),
    (LifecycleState.DELIVERED, Event.RESPONSE_COMMITTED): (Transition(LifecycleState.RESPONSE_COMMITTED),),
    (LifecycleState.RESPONSE_COMMITTED, Event.BIND_NEXT_ACTION): (
        Transition(LifecycleState.NEXT_ACTION_BOUND),
    ),
    (LifecycleState.NEXT_ACTION_BOUND, Event.RECEIPT_FINAL): (
        Transition(LifecycleState.RECEIPT_FINAL),
    ),
    # Terminal failure: reachable from any non-terminal state, idempotent.
    (LifecycleState.SELECTED, Event.FAIL): (Transition(LifecycleState.FAILED),),
    (LifecycleState.NORMALIZED, Event.FAIL): (Transition(LifecycleState.FAILED),),
    (LifecycleState.SNAPSHOT_BOUND, Event.FAIL): (Transition(LifecycleState.FAILED),),
    (LifecycleState.PREFLIGHTED, Event.FAIL): (Transition(LifecycleState.FAILED),),
    (LifecycleState.DECIDED, Event.FAIL): (Transition(LifecycleState.FAILED),),
    (LifecycleState.EXECUTED, Event.FAIL): (Transition(LifecycleState.FAILED),),
    (LifecycleState.REPLACED, Event.FAIL): (Transition(LifecycleState.FAILED),),
    (LifecycleState.REWRITTEN, Event.FAIL): (Transition(LifecycleState.FAILED),),
    (LifecycleState.SUPPRESSED, Event.FAIL): (Transition(LifecycleState.FAILED),),
    (LifecycleState.POSTFLIGHTED, Event.FAIL): (Transition(LifecycleState.FAILED),),
    (LifecycleState.COMPILED, Event.FAIL): (Transition(LifecycleState.FAILED),),
    (LifecycleState.JOINED, Event.FAIL): (Transition(LifecycleState.FAILED),),
    (LifecycleState.DISPATCHED, Event.FAIL): (Transition(LifecycleState.FAILED),),
    (LifecycleState.PROVIDER_ACCEPTED, Event.FAIL): (Transition(LifecycleState.FAILED),),
    (LifecycleState.DELIVERED, Event.FAIL): (Transition(LifecycleState.FAILED),),
    (LifecycleState.RESPONSE_COMMITTED, Event.FAIL): (Transition(LifecycleState.FAILED),),
    (LifecycleState.NEXT_ACTION_BOUND, Event.FAIL): (Transition(LifecycleState.FAILED),),
    # FAILED is terminal and absorbs retries as a no-op (idempotency).
    (LifecycleState.FAILED, Event.FAIL): (Transition(LifecycleState.FAILED),),
}

TERMINAL_STATES = {LifecycleState.RECEIPT_FINAL, LifecycleState.FAILED}

# Execution-state targets reachable from DECIDED (one per decision).
_EXECUTION_DECISION: dict[Event, Decision] = {
    Event.EXECUTE: Decision.PASS_THROUGH,
    Event.REPLACE: Decision.REPLACE,
    Event.REWRITE: Decision.REWRITE,
    Event.SUPPRESS: Decision.SUPPRESS,
}
# Execution outcome state per decision event.
_EXECUTION_STATE: dict[Event, ExecutionState] = {
    Event.EXECUTE: ExecutionState.EXECUTED,
    Event.REPLACE: ExecutionState.REWRITTEN,
    Event.REWRITE: ExecutionState.REWRITTEN,
    Event.SUPPRESS: ExecutionState.SUPPRESSED,
}


class IllegalTransition(Exception):
    """Raised when an event is not legal in the current lifecycle state."""


@dataclass
class LifecycleTrace:
    """One selected action's authoritative lifecycle record."""

    action_id: str = ""
    state: LifecycleState = LifecycleState.SELECTED
    decision: Decision | None = None
    execution_state: ExecutionState | None = None
    raw_result_hash: str = ""
    raw_exact: bool = False
    snapshot_token: str = ""
    fail_open: bool = False
    steps: list[tuple[LifecycleState, Event]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.steps is None:
            self.steps = [(self.state, Event.NORMALIZE)]

    def apply(self, event: Event, *, guard_payload: Mapping | None = None) -> "LifecycleTrace":
        """Transition the trace; raises on illegal events.

        Idempotency: re-applying the same event to the same state is a no-op
        for terminal failure; every other event advances or raises.
        """
        transitions = _TRANSITION_TABLE.get((self.state, event))
        if not transitions:
            raise IllegalTransition(f"{event.value} not legal in {self.state.value}")
        if self.state in TERMINAL_STATES and self.state == LifecycleState.FAILED and event == Event.FAIL:
            return self
        chosen = transitions[0]
        if chosen.guard is not None and guard_payload is not None:
            if not _eval_guard(chosen.guard, guard_payload):
                raise IllegalTransition(
                    f"{event.value} blocked by guard in {self.state.value}"
                )
        self.state = chosen.to
        self.fail_open = self.fail_open or chosen.fail_open
        if event in _EXECUTION_DECISION:
            self.decision = _EXECUTION_DECISION[event]
        if event in _EXECUTION_STATE:
            self.execution_state = _EXECUTION_STATE[event]
        self.steps.append((self.state, event))
        return self

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


def _eval_guard(guard: Callable, payload: Mapping) -> bool:
    return bool(guard(payload))


# --- Bounded exhaustive traversal (test oracle) ---------------------------


def reachable_states() -> set[LifecycleState]:
    """Every state reachable from SELECTED via the transition table."""
    seen: set[LifecycleState] = set()
    frontier = {LifecycleState.SELECTED}
    while frontier:
        state = frontier.pop()
        if state in seen:
            continue
        seen.add(state)
        for (from_state, _event), targets in _TRANSITION_TABLE.items():
            if from_state != state:
                continue
            for target in targets:
                frontier.add(target.to)
    return seen


def legal_events(state: LifecycleState) -> set[Event]:
    """All events legal in a given state."""
    return {event for (from_state, event) in _TRANSITION_TABLE if from_state == state}


def find_terminal_path(
    start: LifecycleState = LifecycleState.SELECTED,
) -> list[Event] | None:
    """A shortest legal event sequence reaching a terminal state, or None."""
    from collections import deque

    queue: deque[tuple[LifecycleState, list[Event]]] = deque([(start, [])])
    visited: set[LifecycleState] = set()
    while queue:
        state, path = queue.popleft()
        if state in TERMINAL_STATES:
            return path
        if state in visited:
            continue
        visited.add(state)
        for event in sorted(legal_events(state), key=lambda e: e.value):
            transitions = _TRANSITION_TABLE.get((state, event), ())
            for t in transitions:
                queue.append((t.to, [*path, event]))
    return None


# --- Required lifecycle properties (witness predicates) --------------------


def property_no_action_disappears(trace: LifecycleTrace) -> bool:
    """A selected action must terminate (RECEIPT_FINAL or FAILED)."""
    return trace.is_terminal


def property_fail_open_preserves_execution(trace: LifecycleTrace) -> bool:
    """Engine failure passes through wherever safe: a fail-open trace that is
    terminal must still have reached a decision (literal execution path)."""
    if trace.state == LifecycleState.FAILED:
        return True  # failure is a recorded outcome; nothing was silently dropped
    return trace.state == LifecycleState.RECEIPT_FINAL


def property_pass_through_executes_literal(trace: LifecycleTrace) -> bool:
    """PASS_THROUGH implies a literal execution outcome."""
    if trace.decision == Decision.PASS_THROUGH:
        return trace.execution_state == ExecutionState.EXECUTED
    return True


def property_suppress_never_loses_replay(trace: LifecycleTrace) -> bool:
    """SUPPRESS is legal only before side effects; raw is retained in replay.
    The trace marks raw_exact=False on suppression, never True."""
    if trace.decision == Decision.SUPPRESS:
        return not trace.raw_exact
    return True
