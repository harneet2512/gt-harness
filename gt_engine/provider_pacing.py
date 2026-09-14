"""Bounded, attested pacing inside Mini-SWE's provider retry loop.

Mini-SWE retries transport failures with a fixed exponential schedule. Every
job in a parallel cohort draws the same schedule, so a provider-side limit
turns twenty trials into synchronized retry waves that keep colliding with
the account ceiling until the attempt budget exhausts. This module decides
the delay applied to each retryable attempt failure before it propagates to
the retry loop: the server-declared ``Retry-After`` when the provider
publishes one (bounded by a cap so a pathological hint cannot stall a trial),
otherwise a uniform jitter draw that decorrelates the cohort's retry phases.

Pacing never swallows, classifies, or converts failures. The decision is a
pure function of the exception; the caller journals the paced attempt and
re-raises the original exception so the terminal receipt stays owned by the
outer query wrapper.
"""

from __future__ import annotations

import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from .run_diagnostics import DiagnosticCode, classify_provider_failure

JITTER_MAX_ENV = "GT_PROVIDER_RETRY_JITTER_MAX_SECONDS"
RETRY_AFTER_CAP_ENV = "GT_PROVIDER_RETRY_AFTER_CAP_SECONDS"
DEFAULT_JITTER_MAX_SECONDS = 45.0
DEFAULT_RETRY_AFTER_CAP_SECONDS = 120.0

_sleep: Callable[[float], None] = time.sleep
_uniform: Callable[[float, float], float] = random.uniform


def _env_seconds(name: str, default: float, environ: dict[str, str]) -> float:
    raw = str(environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"provider_pacing_env_invalid:{name}") from exc
    if value < 0:
        raise ValueError(f"provider_pacing_env_invalid:{name}")
    return value


def retry_after_seconds(exc: BaseException) -> float | None:
    """Server-declared wait from a Retry-After header, if one was sent.

    Provider SDK errors carry the response (or headers) on the exception. A
    numeric value is seconds; an HTTP-date is measured against now. Anything
    unparseable is treated as no hint rather than guessed.
    """
    headers = getattr(exc, "headers", None)
    if headers is None:
        headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except AttributeError:
        return None
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return max(0.0, (when - datetime.now(UTC)).total_seconds())
    except Exception:  # noqa: BLE001 - an unparseable hint is no hint
        return None


@dataclass(frozen=True, slots=True)
class PacingDecision:
    """The pacing verdict for one failed provider attempt."""

    code: DiagnosticCode
    retryable: bool
    delay_seconds: float
    basis: str  # "retry_after" | "jitter" | "jitter_disabled" | ""


@dataclass(frozen=True, slots=True)
class PacingPolicy:
    """Resolved pacing bounds; fixed at hook install before any paid call."""

    jitter_max_seconds: float = DEFAULT_JITTER_MAX_SECONDS
    retry_after_cap_seconds: float = DEFAULT_RETRY_AFTER_CAP_SECONDS

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> PacingPolicy:
        env = os.environ if environ is None else environ
        return cls(
            jitter_max_seconds=_env_seconds(JITTER_MAX_ENV, DEFAULT_JITTER_MAX_SECONDS, env),
            retry_after_cap_seconds=_env_seconds(
                RETRY_AFTER_CAP_ENV, DEFAULT_RETRY_AFTER_CAP_SECONDS, env
            ),
        )

    def decide(self, exc: BaseException) -> PacingDecision:
        """Classify the failure and choose the delay before it re-raises."""
        code, retryable = classify_provider_failure(exc)
        if not retryable:
            return PacingDecision(code, False, 0.0, "")
        declared = retry_after_seconds(exc)
        if declared is not None:
            return PacingDecision(
                code, True, min(declared, self.retry_after_cap_seconds), "retry_after"
            )
        if self.jitter_max_seconds <= 0:
            return PacingDecision(code, True, 0.0, "jitter_disabled")
        return PacingDecision(code, True, _uniform(0.0, self.jitter_max_seconds), "jitter")

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            _sleep(seconds)
