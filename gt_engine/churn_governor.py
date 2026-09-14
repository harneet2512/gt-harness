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
# Verification commands reuse the wheel's canonical runner pattern: the plan
# binder admits exactly TEST_RUNNER_RE commands, so any runner the binder can
# bind must read as verification here or a bound check run counts as a stall
# turn. The previous local copy drifted narrow (no ./mvnw, vendor/bin/phpunit,
# node_modules/.bin/jest, bazel, sbt testOnly, turbo run test) and even missed
# plain `gradle test` - run 34801009507's dead-check class reappearing as
# governor false stalls.
from groundtruth.runtime.patterns import TEST_RUNNER_RE as _VERIFICATION

_VERIFICATION_EXTRA = re.compile(
    r"(^|[;&|\s])(?:go\s+vet\b|(?:npx\s+|yarn\s+|pnpm\s+)?ava\b)"
)
# Active engineering: compiling, building, or executing produced artifacts.
# Run 34801009507 task fd showed the blind spot - the agent probed a Rust API
# by writing throwaway crates under /tmp and running `cargo build`/`cargo run`
# plus the produced binary. None of that mutates the repo diff and none of it
# matches *test, so fifty turns of genuine work read as a stall and the
# governor killed a working run. Executing something the agent made is the
# same class of evidence as running a test: the environment answered a
# question. Inline interpreters (`python -c`, `node -e`) are excluded on
# purpose - ad-hoc eval is how archaeology scripts run, not engineering.
_BUILD_OR_EXEC = re.compile(
    r"(^|[;&|\s])("
    r"cargo\s+(?:build|check|run|clippy|bench|doc|install|fix)"
    r"|rustc\b|go\s+(?:build|run|install|generate)"
    r"|npm\s+(?:run|exec|start|install|ci)|npx\s+\S"
    r"|yarn\s+(?:run|build|install|add)|pnpm\s+(?:run|build|install|add)"
    r"|make\b(?!.*\b(?:test|check)\b)|cmake\b|tsc\b|javac\b"
    r"|gcc\b|g\+\+\b|clang\b|cc\b|mvn\s+(?:compile|package|install|verify)"
    r"|(?:[^\s;&|]+/)?mvnw\s+(?:compile|package|install|verify|deploy)"
    r"|gradle(?:w)?\s+(?:build|assemble|compileJava|jar|check)"
    r"|(?:[^\s;&|]+/)?gradlew\s+(?:build|assemble|compileJava|jar)"
    r"|dotnet\s+(?:build|publish|pack|restore)"
    r"|sbt\s+(?:compile|package|assembly|publish)"
    r"|mix\s+(?:compile|deps\.get|release)|bazel\s+(?:build|run)\b"
    r"|nx\s+(?:build|run|serve)\b|turbo\s+(?:run\s+)?build"
    r"|composer\s+(?:install|update|dump-autoload)|bundle\s+install"
    r"|deno\s+(?:task|compile|bundle)|rake\s+(?:build|compile)"
    r"|pip(?:3)?\s+install"
    r"|python\d*\s+(?!-c\b)\S+\.py|python\d*\s+-m\s+\S"
    r"|node\s+(?!-e\b)\S+\.js|tsx?\s+\S+\.ts"
    r"|bash\s+\S+\.sh|sh\s+\S+\.sh"
    r"|\./[^\s]*(?:target|bin|build|dist|out)[^\s]*|\./a\.out"
    r")"
)
# File-mutation intent, lexically: redirects and in-place editors mutate a
# file whether or not it lives under the watched repo root. This is what
# catches probe writes under /tmp that the repo diff cannot see. Plain `cat`
# without a redirect still reads as a read.
_WRITE_INTENT = re.compile(
    r"(?:cat\b[^;&|]*>>?|>>?\s*\S|tee\b|sed\s+-i|perl\s+-pi\b"
    r"|\bpatch\b|git\s+apply|apply_patch|\bcp\b|\bmv\b|mkdir\b|touch\b"
    r"|install\s+-[dm]|rsync\b|chmod\b|ln\b)"
)

Signal = Literal["", "steer", "abort", "verify_steer"]


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
        max_verify_steers: int | None = None,
        verify_steer_streak: int | None = None,
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
        self.max_verify_steers = (
            max_verify_steers if max_verify_steers is not None
            else _env_int("GT_CHURN_MAX_VERIFY_STEERS", 2)
        )
        self.verify_steer_streak = (
            verify_steer_streak if verify_steer_streak is not None
            else _env_int("GT_CHURN_VERIFY_STEER_STREAK", 20)
        )
        self.disabled = disabled
        self._commands: deque[str] = deque(maxlen=self.window)
        self._stall_turns = 0
        self._turns = 0
        self._steers = 0
        self._verify_steers = 0
        self._verify_fail_streak = 0
        self._last_steer_turn = -10**9
        self._last_verify_steer_turn = -10**9
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

    def observe(
        self,
        command: str,
        *,
        productive: bool = False,
        returncode: int | None = None,
    ) -> Signal:
        """Feed one executed action; get the governor's reaction."""
        if self.disabled or self.aborted:
            return "abort" if self.aborted else ""
        self._turns += 1
        normalized = self._normalize(command)
        self._commands.append(normalized)
        is_verification = bool(
            _VERIFICATION.search(normalized)
            or _VERIFICATION_EXTRA.search(normalized)
        )
        if is_verification and returncode is not None:
            # Quiet non-convergence (smoke20 pest/bandit-taint/testem-pl):
            # edit -> test -> edit -> test with every test failing reads as
            # fully productive to the stall counter. Only a passing check is
            # evidence the loop is converging; intervening edits do not
            # reset the streak because they are the loop, not progress.
            if returncode == 0:
                self._verify_fail_streak = 0
            else:
                self._verify_fail_streak += 1
        if (
            productive
            or is_verification
            or _BUILD_OR_EXEC.search(normalized)
            or _WRITE_INTENT.search(normalized)
        ):
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
        # A stall abort is only honest after steering had its chance: a run
        # that was never steered gets steered, not killed. ``max_steers == 0``
        # is the operator's explicit no-steering choice, where the plain stall
        # limit is the only protection left.
        stall_abort_ready = self.max_steers == 0 or (
            self._steers > 0 and steer_grace_elapsed
        )
        if (
            (self._stall_turns >= self.abort_stall and stall_abort_ready)
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
        # Any sustained unproductive stall earns a steer - not only GT-state
        # archaeology. An agent reading source files for steer_stall actions
        # without an edit or check is exactly the case a corrective delivery
        # exists for.
        if (
            self._steers < self.max_steers
            and self._turns - self._last_steer_turn >= 15
            and (
                self._stall_turns >= self.steer_stall
                or repeat >= self.repeat_abort - 5
            )
        ):
            self._steers += 1
            self._last_steer_turn = self._turns
            self.steer_events.append(self._turns)
            return "steer"
        # A failing-verification streak earns a convergence steer, never an
        # abort: the agent is working, and on a genuinely hard task long
        # iteration is legitimate - killing it repeats the fd mistake. The
        # streak has its own steer budget so it cannot starve stall steers.
        if (
            self._verify_steers < self.max_verify_steers
            and self._verify_fail_streak >= self.verify_steer_streak
            and self._turns - self._last_verify_steer_turn >= 15
        ):
            self._verify_steers += 1
            self._last_verify_steer_turn = self._turns
            return "verify_steer"
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

    @property
    def verify_fail_streak(self) -> int:
        return self._verify_fail_streak

    @property
    def verify_steers_issued(self) -> int:
        return self._verify_steers
