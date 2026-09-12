"""Detect action-loop churn before it consumes a run's budget.

Run 34656860834 burned 111 minutes and 769 provider turns while the agent
re-fetched its own elided history: 54% of commands were GT-internal
archaeology and only two edit transactions ever landed. The wall clock was
the only governor, so the waste ran to the deadline.

This detector watches the command stream in-process. A bounded steering
delivery gets one chance to break the loop; if the stall continues the
governor recommends a typed abort so the supervisor can seal the journals
and conserve the bytes honestly instead of buying another hour of churn.

Productivity means a workspace mutation or a bound verification run - the
same signals the persistent-plan ledger already treats as progress. Pure
reads, searches and GT-artifact paging never reset the stall counter.
"""

from __future__ import annotations

import os
import re
from collections import deque
from typing import Literal

# Commands whose only effect is reading GT's own plumbing. Matching is
# deliberately lexical: these surfaces never contain task work.
_ARCHAEOLOGY = re.compile(
    r"gt-evidence|gt-plan|gt-state|miniswe_trajectory|output_evidence"
    r"|/logs/agent|deliveries/|events\.jsonl|provider_events\.jsonl"
)
# A command that runs the project's own checks is a productive action even
# without an edit - it is the agent verifying, not rummaging.
_VERIFICATION = re.compile(
    r"(^|[;&|\s])(pytest|py\.test|go\s+test|cargo\s+test|npm\s+test"
    r"|yarn\s+test|pnpm\s+test|mvn\s+test|gradle(test|w\s+test)"
    r"|make\s+(test|check)|ctest|tox|unittest|rspec|go\s+vet)\b"
)

Signal = Literal["", "steer", "abort"]


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(os.environ.get(name, "") or default)))
    except ValueError:
        return default


class ChurnGovernor:
    """Sliding-window loop detector fed once per agent action.

    ``observe`` returns ``"steer"`` at most ``max_steers`` times and
    ``"abort"`` once the stall is unrecoverable. Defaults are tuned so a
    run like 34656860834 (churn onset ~turn 30, two edits in 840 commands)
    aborts near turn 60-100 instead of at the deadline.
    """

    def __init__(
        self,
        *,
        window: int | None = None,
        steer_stall: int | None = None,
        abort_stall: int | None = None,
        churn_ratio: float | None = None,
        repeat_abort: int | None = None,
        max_steers: int | None = None,
        disabled: bool | None = None,
    ) -> None:
        self.window = window if window is not None else _env_int("GT_CHURN_WINDOW", 40)
        self.steer_stall = (
            steer_stall if steer_stall is not None
            else _env_int("GT_CHURN_STEER_STALL", 25)
        )
        self.abort_stall = (
            abort_stall if abort_stall is not None
            else _env_int("GT_CHURN_ABORT_STALL", 50)
        )
        self.churn_ratio = (
            churn_ratio if churn_ratio is not None
            else _env_float("GT_CHURN_RATIO", 0.4)
        )
        self.repeat_abort = (
            repeat_abort if repeat_abort is not None
            else _env_int("GT_CHURN_REPEAT_ABORT", 15)
        )
        self.max_steers = (
            max_steers if max_steers is not None
            else _env_int("GT_CHURN_MAX_STEERS", 2)
        )
        if disabled is None:
            disabled = os.environ.get("GT_CHURN_DISABLED", "") in {"1", "true", "yes"}
        self.disabled = disabled
        self._commands: deque[str] = deque(maxlen=self.window)
        self._stall_turns = 0
        self._turns = 0
        self._steers = 0
        self._last_steer_turn = -10**9
        self.aborted = False
        self.steer_events: list[int] = []

    @staticmethod
    def _normalize(command: str) -> str:
        return " ".join(str(command or "").split())

    def _churn_ratio(self) -> float:
        if not self._commands:
            return 0.0
        churned = sum(1 for c in self._commands if _ARCHAEOLOGY.search(c))
        return churned / len(self._commands)

    def _repeat_max(self) -> int:
        """Longest consecutive run of one identical command.

        Frequency across the window cannot tell a healthy edit/test cycle
        (pytest run 20 times in 80 turns) from hammering (the same cat 20
        times back-to-back); only the consecutive run can.
        """
        best = current = 0
        previous = None
        for command in self._commands:
            current = current + 1 if command == previous else 1
            previous = command
            best = max(best, current)
        return best

    def observe(self, command: str, *, productive: bool = False) -> Signal:
        """Feed one executed action; get the governor's reaction."""
        if self.disabled or self.aborted:
            return "abort" if self.aborted else ""
        self._turns += 1
        normalized = self._normalize(command)
        self._commands.append(normalized)
        if productive or _VERIFICATION.search(normalized):
            self._stall_turns = 0
        else:
            self._stall_turns += 1
        repeat = self._repeat_max()
        churn = self._churn_ratio()
        steered_recently = (
            self._steers > 0 and self._turns - self._last_steer_turn <= self.window
        )
        # A steer needs runway to work: the text only reaches the model on the
        # next provider request, so the churn-specific abort waits ~10 actions
        # for the agent to react before concluding the steer failed.
        steer_grace_elapsed = self._turns - self._last_steer_turn >= 10
        if (
            self._stall_turns >= self.abort_stall
            or repeat >= self.repeat_abort
            or (
                steered_recently
                and steer_grace_elapsed
                and self._stall_turns >= self.steer_stall + 15
                and churn >= 0.5
            )
        ):
            self.aborted = True
            return "abort"
        if (
            self._steers < self.max_steers
            and self._turns - self._last_steer_turn >= 15
            and (
                (self._stall_turns >= self.steer_stall and churn >= self.churn_ratio)
                or repeat >= self.repeat_abort - 5
            )
        ):
            self._steers += 1
            self._last_steer_turn = self._turns
            self.steer_events.append(self._turns)
            return "steer"
        return ""

    @property
    def stall_turns(self) -> int:
        return self._stall_turns

    @property
    def turns_observed(self) -> int:
        return self._turns

    @property
    def steers_issued(self) -> int:
        return self._steers
