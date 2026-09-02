"""GTSession — the narrow, versioned, single-owner GT interface.

T4.2: the deep-research verdict found the harness split lifecycle ownership
between the monkeypatched seam and Groundtruth's own machinery. This module is
the one supported facade the runner talks to. It owns the session lifecycle
(task received -> model round trips -> submit decision -> completion state) and
exposes a small typed API. Everything GT does is behind this surface; nothing
else in the runner reaches into the engine.

Capability negotiation: the host tells GTSession what it can actually provide.
Anything it cannot provide degrades assurance visibly (DEGRADED_ASSURANCE is
recorded) instead of being silently approximated.
"""
from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .delivery_budget import PROMPT_CONTEXT_BYTE_LIMIT, truncate_utf8

# Capabilities a host can declare (see the verdict's negotiation list).
HOST_CAPABILITIES = (
    "exact_provider_payload",     # finalized logical request bytes are exact
    "provider_response_ids",      # real provider request/response ids bound
    "structured_actions",         # actions are parsed, not free-text heuristics
    "structured_results",         # results carry exit code + ordered output
    "workspace_deltas",           # create/modify/delete workspace deltas
    "filesystem_snapshots",       # content-addressed snapshots
    "tool_call_deferral",         # tool calls can be deferred to the host
    "parsed_test_results",        # test output parsed with outcome/protocol
    "streaming",                  # ordered output streaming
    "checkpointing",              # crash-safe resume
    "trusted_verifier",           # clean-env verifier outside the agent
)


class Assurance(StrEnum):
    FULL = "FULL"                 # all declared capabilities actually present
    DEGRADED = "DEGRADED"         # some capability declared but not honored


class GTMode(StrEnum):
    """Capability-preserving rollout modes.

    Only ``ENFORCED`` may prevent a baseline action. New mechanisms are
    expected to start in ``SHADOW`` and graduate through ``ADVISORY`` or
    ``ASSISTIVE`` after evidence supports doing so.
    """

    OFF = "off"
    SHADOW = "shadow"
    ADVISORY = "advisory"
    ASSISTIVE = "assistive"
    ENFORCED = "enforced"


@dataclass
class GTSessionConfig:
    task_id: str
    repo_root: str = ""
    state_dir: str = ""
    graph_db: str | None = None
    capabilities: tuple[str, ...] = ()
    issue_text: str = ""
    mode: GTMode | str = GTMode.ADVISORY
    fail_open: bool = True
    context_budget_bytes: int = PROMPT_CONTEXT_BYTE_LIMIT
    capability_modes: Mapping[str, GTMode | str] = field(default_factory=dict)
    disabled_capabilities: tuple[str, ...] = ()
    delivery_path: str = "compiled"


@dataclass
class GTDecisionBatch:
    """The only thing GT returns per decision point.

    context_additions: bytes to surface in the prompt (bounded).
    evidence:           fact/evidence keys produced.
    policy:             policy decisions (e.g. deny a submit).
    verification:       verification requests/results.
    provenance:         provenance event rows.
    degraded:           assurance notes when a declared capability was missing.
    """
    context_additions: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    policy: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    provenance: list[dict] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not any([self.context_additions, self.evidence, self.policy,
                        self.verification, self.provenance, self.degraded])


class GTSession:
    """Single-owner GT session facade.

    The concrete engine lives behind ``_engine`` (currently the MiniSweAdapter
    seam); the facade is the stable versioned surface. A future consolidation
    can swap the engine for Groundtruth's AttemptReasoningRuntime without the
    runner changing.
    """

    SCHEMA = "gt.session.v1"

    def __init__(self, config: GTSessionConfig, engine: Any | None = None):
        self.config = config
        self._engine = engine
        self.mode = GTMode(str(config.mode))
        self._assurance: list[str] = []
        self.assurance_state = Assurance.FULL
        global_killed = os.environ.get("GT_KILL_SWITCH", "").strip().lower() in {
            "1", "true", "yes", "on",
        }
        self.disabled = self.mode is GTMode.OFF or global_killed
        self.disabled_stage = (
            "global_kill_switch" if global_killed
            else "off" if self.disabled else ""
        )
        self._terminal: str | None = None
        self._task_start_shipped = False
        self._capability_check()

    @property
    def engine(self) -> Any | None:
        """The integration engine, exposed read-only to the runtime seam."""
        return self._engine

    @property
    def model_visible(self) -> bool:
        return not self.disabled and self.mode in {
            GTMode.ADVISORY, GTMode.ASSISTIVE, GTMode.ENFORCED,
        }

    @property
    def allows_live_probes(self) -> bool:
        """Implicit commands require two explicit, auditable opt-ins."""
        return (
            not self.disabled
            and self.mode is GTMode.ASSISTIVE
            and os.environ.get("GT_ALLOW_LIVE_PROBES", "").strip() == "1"
            and os.environ.get("GT_VERIFY_EXECUTE", "").strip() == "1"
        )

    @property
    def can_enforce(self) -> bool:
        return not self.disabled and self.mode is GTMode.ENFORCED

    @staticmethod
    def _capability_key(capability: str) -> str:
        key = capability.strip().lower().replace("-", "_")
        if not key or any(not (char.isalnum() or char == "_") for char in key):
            raise ValueError(f"invalid capability name: {capability!r}")
        return key

    def capability_mode(self, capability: str) -> GTMode:
        """Resolve one capability's mode without changing the global posture."""
        key = self._capability_key(capability)
        disabled = {self._capability_key(item) for item in self.config.disabled_capabilities}
        disabled.update(
            self._capability_key(item)
            for item in os.environ.get("GT_DISABLED_CAPABILITIES", "").split(",")
            if item.strip()
        )
        if key in disabled:
            return GTMode.OFF
        env_name = f"GT_CAPABILITY_{key.upper()}_MODE"
        configured = os.environ.get(env_name)
        if configured is None:
            configured = self.config.capability_modes.get(key, self.mode)
        try:
            return GTMode(str(configured).lower())
        except ValueError:
            # An invalid switch must disable the capability, not broaden it.
            return GTMode.OFF

    def capability_active(self, capability: str) -> bool:
        return not self.disabled and self.capability_mode(capability) is not GTMode.OFF

    def capability_model_visible(self, capability: str) -> bool:
        return self.capability_active(capability) and self.capability_mode(capability) in {
            GTMode.ADVISORY, GTMode.ASSISTIVE, GTMode.ENFORCED,
        }

    def degrade(self, stage: str, error: BaseException) -> None:
        """Disable GT for the rest of the run while preserving Mini-SWE.

        This operation is idempotent: the first fault is the causal fault and
        later calls are merely consequences of an already disabled observer.
        """
        if self.disabled:
            return
        self.disabled = True
        self.disabled_stage = stage
        self.assurance_state = Assurance.DEGRADED
        note = f"{stage}: {type(error).__name__}: {str(error)[:300]}"
        self._assurance.append(note)
        try:
            if self._engine is not None:
                self._engine.store.append(
                    "gt_degraded_fail_open",
                    stage=stage,
                    error_type=type(error).__name__,
                    error=str(error)[:300],
                )
        except Exception:
            # The state sink may itself be the failed component. Fail-open must
            # not recurse through another telemetry failure.
            pass

    # -- capability negotiation -------------------------------------------
    def _capability_check(self) -> None:
        declared = set(self.config.capabilities)
        if "exact_provider_payload" in declared and not self.config.state_dir:
            self._assurance.append("exact_provider_payload declared without state_dir")
        if "trusted_verifier" in declared and os.environ.get("GT_VERIFY_EXECUTE") != "1":
            self._assurance.append("trusted_verifier declared but GT_VERIFY_EXECUTE!=1")
        if self._assurance:
            self.assurance_state = Assurance.DEGRADED

    def degraded_notes(self) -> tuple[str, ...]:
        return tuple(self._assurance)

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> GTDecisionBatch:
        if self._engine is None or self.disabled:
            return GTDecisionBatch()
        self._engine.start_task()
        return GTDecisionBatch(provenance=[{"event": "session_started"}])

    def before_model(self, messages: list[dict], iteration: int) -> GTDecisionBatch:
        """Deliver context additions (contract/localization) before a model call."""
        if self._engine is None or self.disabled:
            return GTDecisionBatch()
        additions: list[str] = []
        contract_was_shipped = bool(self._engine.contract_shipped)
        delta = self._engine.next_contract_delta(
            max_chars=min(
                self.config.context_budget_bytes, PROMPT_CONTEXT_BYTE_LIMIT
            )
        )
        if delta:
            tag = "GT_TASK_CONTRACT" if iteration == 0 else "GT_OBLIGATION_DELTA"
            rendered = truncate_utf8(
                f"[{tag}]\n{delta}", PROMPT_CONTEXT_BYTE_LIMIT
            )
            if self.model_visible:
                kind = "context_delta" if contract_was_shipped else "context_contract"
                payload_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
                if self._engine.admit_model_visible_delivery(
                    lane="prompt",
                    kind=kind,
                    rendered=rendered,
                    action_index=0,
                    iteration=iteration,
                    dedup_key=f"prompt:{kind}:{iteration}:{payload_hash}",
                    target="provider_prompt",
                ):
                    additions.append(rendered)
            else:
                self._engine.store.append(
                    "shadow_context_computed",
                    iteration=iteration,
                    rendered_bytes=len(rendered.encode("utf-8")),
                )
        if (
            iteration == 0
            and not self._task_start_shipped
            and self.config.delivery_path == "compiled"
        ):
            self._task_start_shipped = True
            localization = self._engine.task_start_localization()
            if localization:
                if self.model_visible:
                    additions.append(localization)
                else:
                    self._engine.store.append(
                        "shadow_task_start_localization",
                        rendered_bytes=len(localization.encode("utf-8")),
                    )
        return GTDecisionBatch(context_additions=additions)

    def after_action(
        self,
        *,
        command: str,
        output: str,
        returncode: int | None,
        action_index: int,
    ) -> GTDecisionBatch:
        if self._engine is None or self.disabled:
            return GTDecisionBatch()
        batch = GTDecisionBatch()
        if self._engine.contract is not None:
            self._engine.evaluate_observation(
                command, output, returncode=returncode, action_index=action_index
            )
            self._engine.evaluate_failing_observation(
                command, output, returncode=returncode, action_index=action_index
            )
        return batch

    def request_submit(self) -> tuple[bool, GTDecisionBatch]:
        """The submit decision. Returns (accepted, decision batch)."""
        if self._engine is None or self.disabled:
            return True, GTDecisionBatch()
        if not self.can_enforce:
            blocking = tuple(getattr(self._engine, "blocking_predicates", ()))
            if blocking:
                self._engine.store.append(
                    "submit_advisory",
                    mode=self.mode.value,
                    predicate_ids=list(blocking),
                )
            accepted = self._engine.advisory_submit_decision()
            return accepted, GTDecisionBatch()
        accepted = self._engine.submit_decision()
        batch = GTDecisionBatch(policy=["accept" if accepted else "deny"])
        return accepted, batch

    def completion_state(self) -> dict[str, Any]:
        """Final session state, including honest verified/unverified."""
        if self._engine is None:
            return {"verified": False, "terminal": "internal_error"}
        state = dict(self._engine.final_state())
        state.update({
            "gt_mode": self.mode.value,
            "gt_disabled": self.disabled,
            "gt_disabled_stage": self.disabled_stage,
            "assurance": self.assurance_state.value,
        })
        return state

    def close(self, terminal: str) -> None:
        self._terminal = terminal
        if self._engine is not None:
            self._engine.store.append("session_closed", terminal=terminal)
