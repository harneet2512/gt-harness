from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .providers import Provider, StepResult, ToolCall
from .tools import TOOLS, BashTool, ToolError, dispatch

_GT_SAFE_EXCLUSION_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"--exclude-dir(?:=|\s+)['\"]?\.gt['\"]?"
    r"|--glob(?:=|\s+)['\"]?!\.gt(?:/\*)?['\"]?"
    r"|-not\s+-path\s+['\"]?(?:\./)?\.gt(?:/\*)?['\"]?"
    r"|-path\s+['\"]?(?:\./)?\.gt(?:/\*)?['\"]?\s+-prune"
    r")"
)
_GT_HARNESS_PATH_RE = re.compile(
    r"(?i)(?:^|[\s'\"=:(])(?:"
    r"(?:\./)?\.gt(?:/|[\s'\";|&)]|$)"
    r"|/installed-agent(?:/|[\s'\";|&)]|$)"
    r"|/logs/agent(?:/|[\s'\";|&)]|$)"
    r"|/tmp/\.nano-gt-state(?:/|[\s'\";|&)]|$)"
    r")"
)


def gt_harness_access_reason(
    tool_name: str,
    arguments: dict[str, Any],
) -> str | None:
    """Name an explicit GT/harness path access, excluding prune expressions."""
    name = str(tool_name or "")
    if name == "bash":
        candidate = _GT_SAFE_EXCLUSION_RE.sub(
            "", str(arguments.get("command") or "")
        )
    elif name in {"read_file", "edit_file"}:
        candidate = str(arguments.get("path") or "")
    else:
        return None
    if _GT_HARNESS_PATH_RE.search(candidate):
        return (
            "GroundTruth and harness state is outside the task filesystem "
            "contract; this access was not executed."
        )
    return None


def affordable_bash_timeout(
    *,
    requested_seconds: int,
    remaining_seconds: float | None,
    reserve_seconds: float,
) -> int | None:
    """Return a timeout that cannot consume the agent's finish reserve."""
    requested = max(1, int(requested_seconds))
    if remaining_seconds is None:
        return requested
    affordable = int(max(0.0, remaining_seconds - reserve_seconds))
    if affordable < 1:
        return None
    return min(requested, affordable)


@dataclass
class AgentResult:
    final_text: str | None
    stop_reason: str  # end_turn | unverified | max_iterations | max_tokens | error
    iterations: int
    total_input_tokens: int
    total_output_tokens: int
    total_cache_read_tokens: int
    transcript: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Agent:
    provider: Provider
    system: str
    max_iterations: int = 30
    max_input_tokens: int = 200_000
    truncation_char_budget: int = 120_000  # ~30k tokens of tool_result content
    gt_context_char_budget: int = 48_000
    verify: bool = True  # gate "done" behind tool evidence (see max_pushbacks)
    max_pushbacks: int = 3  # toolless "done"s challenged before giving in
    on_event: Callable[[dict[str, Any]], None] | None = None
    bash: BashTool | None = None
    gt_root: str | None = None  # codebase root for GroundTruth; None = GT off
    time_budget_seconds: float | None = None
    finalization_reserve_seconds: float = 180.0
    clock: Callable[[], float] = field(
        default=time.monotonic,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.bash is None:
            self.bash = BashTool()
        # GT integration point 1: auto-index the codebase and build the bridge.
        # None (no gt_root, non-code root, GT unavailable) leaves every code
        # path below byte-identical to stock nano-harness.
        self._gt = None
        if self.gt_root:
            try:
                from gt_engine import create_bridge
                self._gt = create_bridge(self.gt_root)
            except Exception:  # noqa: BLE001 - GT absent/broken: run stock nano
                self._gt = None

    def _emit(self, event: dict[str, Any]) -> None:
        if self.on_event:
            self.on_event(event)

    def _remaining_seconds(self) -> float | None:
        deadline = getattr(self, "_deadline", None)
        if deadline is None:
            return None
        return max(0.0, float(deadline) - float(self.clock()))

    def _sync_gt_budget(self) -> None:
        if self._gt is None:
            return
        try:
            self._gt.wall_seconds_remaining = self._remaining_seconds()
            self._gt.finalization_reserve_seconds = (
                self.finalization_reserve_seconds
            )
        except Exception:  # noqa: BLE001 - budget telemetry is advisory
            pass

    def run(self, task: str) -> AgentResult:
        self._deadline = (
            float(self.clock()) + float(self.time_budget_seconds)
            if self.time_budget_seconds is not None
            else None
        )
        task_content = task
        if self._gt is not None:
            self._gt.issue_text = task  # B-4: thread the real task text into GT
            self._gt.iteration_budget = self.max_iterations
            self._sync_gt_budget()
            # GT integration point 4: task-start delivery. Production's step-0
            # surface (the v1r brief: obligations + ranked localization) rides
            # the INITIAL user message, before the first provider call, so the
            # model's first decision is aided. Rendered/leak-guarded/budget-
            # checked/sealed inside the bridge; None (abstain or any fault)
            # leaves the message byte-identical to stock.
            try:
                gt_capsule = self._gt.task_start()
            except Exception:  # noqa: BLE001 - GT must never break task start
                gt_capsule = None
            if gt_capsule:
                task_content = task + "\n\n" + gt_capsule
        messages: list[dict[str, Any]] = [{"role": "user", "content": task_content}]
        transcript: list[dict[str, Any]] = [{"type": "user", "content": task_content}]
        total_in = total_out = total_cache = 0
        iteration = 0
        used_tools = False
        pushbacks_left = self.max_pushbacks if self.verify else 0
        challenged = False  # has any "done" been pushed back yet?
        tools_since_nudge = False  # successful tool evidence since last pushback
        # Re-arm after every later tool round. A clean probe describes only
        # the repository state at that boundary; stock verification can ask
        # the model to do more work and invalidate that clean decision.
        gt_submit_dirty = True

        try:
          while True:
            iteration += 1
            if iteration > self.max_iterations:
                return self._finish(AgentResult(
                    final_text=None, stop_reason="max_iterations",
                    iterations=iteration - 1,
                    total_input_tokens=total_in, total_output_tokens=total_out,
                    total_cache_read_tokens=total_cache, transcript=transcript,
                ))

            self._sync_gt_budget()
            if self._gt is not None and hasattr(self._gt, "progress_control"):
                try:
                    gt_control = self._gt.progress_control(iteration)
                except Exception:  # noqa: BLE001 - control is fail-open
                    gt_control = None
                if gt_control:
                    messages.append({"role": "user", "content": gt_control})
                    transcript.append({
                        "type": "user",
                        "content": gt_control,
                        "gt": "progress_control",
                    })
            # GT integration point 3: evidence-aware truncation. Stock path
            # (and stock bytes) whenever the GT bridge is absent; the GT path
            # itself falls back to stock truncation on any fault.
            if self._gt is None:
                self._truncate_if_needed(messages, transcript)
            provider_exposure_ids: tuple[str, ...] = ()
            request_messages = messages
            if self._gt is not None:
                try:
                    if hasattr(self._gt, "provider_message_view"):
                        request_messages = self._gt.provider_message_view(
                            messages,
                            char_budget=self.gt_context_char_budget,
                        )
                except Exception:  # noqa: BLE001 - telemetry never blocks inference
                    request_messages = messages
                try:
                    self._gt.trace_model_request(iteration, request_messages)
                except Exception:  # noqa: BLE001 - telemetry never blocks inference
                    pass
            previous_request_observer = None
            provider_observer_installed = False
            if self._gt is not None and hasattr(
                    self.provider, "request_observer"):
                try:
                    previous_request_observer = self.provider.request_observer

                    def _observe_provider_request(
                        provider_name, payload, request_iteration=iteration
                    ):
                        nonlocal provider_exposure_ids
                        provider_exposure_ids = self._gt.trace_provider_request(
                            request_iteration, provider_name, payload
                        )

                    self.provider.request_observer = _observe_provider_request
                    provider_observer_installed = True
                except Exception:  # noqa: BLE001 - tracing cannot block inference
                    provider_observer_installed = False
            try:
                sr: StepResult = self.provider.step(
                    request_messages, TOOLS, self.system
                )
            finally:
                if provider_observer_installed:
                    try:
                        self.provider.request_observer = previous_request_observer
                    except Exception:  # noqa: BLE001 - telemetry cleanup only
                        pass
            if self._gt is not None:
                try:
                    self._gt.trace_model_response(
                        iteration, sr, provider_exposure_ids
                    )
                except Exception:  # noqa: BLE001 - telemetry never changes the loop
                    pass
            total_in += sr.usage.input_tokens
            total_out += sr.usage.output_tokens
            total_cache += sr.usage.cache_read_tokens

            transcript.append({
                "type": "assistant", "text": sr.text,
                "tool_calls": [tc.model_dump() for tc in sr.tool_calls],
                "stop_reason": sr.stop_reason, "usage": sr.usage.model_dump(),
            })
            self._emit({"type": "assistant", "text": sr.text,
                        "tool_calls": sr.tool_calls})
            # Running totals every step: a run killed from outside (timeout)
            # must not take its token accounting down with it.
            self._emit({"type": "stats", "iteration": iteration,
                        "input_tokens": total_in, "output_tokens": total_out})

            messages.append(self._assistant_message(sr))

            # The cap is on per-step context size, not cumulative spend: every
            # step resends the whole conversation, so capping the sum would
            # silently end long tasks after a handful of steps. Cap breach takes
            # priority over natural completion even if the model said end_turn.
            if sr.usage.input_tokens >= self.max_input_tokens:
                return self._finish(AgentResult(
                    final_text=sr.text, stop_reason="max_tokens",
                    iterations=iteration,
                    total_input_tokens=total_in, total_output_tokens=total_out,
                    total_cache_read_tokens=total_cache, transcript=transcript,
                ))

            # Output cut off mid-response (often mid-tool-call JSON): nudge a
            # continuation instead of misreporting success.
            if sr.stop_reason == "max_tokens" and not sr.tool_calls:
                nudge = ("Your previous response was cut off by the output "
                         "token limit. Continue: re-issue the incomplete tool "
                         "call in full, or finish your answer.")
                messages.append({"role": "user", "content": nudge})
                transcript.append({"type": "user", "content": nudge})
                continue

            if sr.stop_reason == "end_turn":
                # Verify pass: models grade their own work generously, and
                # some end a turn merely *describing* their next action. A
                # "done" is only accepted when backed by *successful* tool
                # evidence since the last challenge; toolless (or failed-only)
                # dones get pushed back until max_pushbacks runs out. Skipped
                # when no tool was ever used.
                # GT verify hook (advisory, brief §10): ask GT - a synthetic
                # submit-boundary event through the same bridge - whether
                # submit_refusal/syntax_result evidence exists. If yes, spend
                # ONE pushback delivering that evidence as the nudge text.
                # No evidence / any fault: the stock gate proceeds unchanged.
                if (self._gt is not None and gt_submit_dirty
                        and pushbacks_left > 0
                        and (self.max_iterations - iteration) > 0):
                    gt_submit_dirty = False
                    from gt_engine.verify import submit_evidence
                    gt_nudge = submit_evidence(self._gt)  # None on abstain/fault
                    if gt_nudge:
                        pushbacks_left -= 1
                        challenged = True
                        tools_since_nudge = False
                        messages.append({"role": "user", "content": gt_nudge})
                        transcript.append({"type": "user", "content": gt_nudge,
                                           "gt": "submit_evidence"})
                        continue
                # Don't spend the last iteration on a pushback - a challenge
                # the model can't answer would return max_iterations and throw
                # away the summary it just produced.
                if used_tools and pushbacks_left > 0 and (
                        self.max_iterations - iteration) > 0 and (
                        not challenged or not tools_since_nudge):
                    pushbacks_left -= 1
                    challenged = True
                    tools_since_nudge = False
                    remaining = self.max_iterations - iteration
                    nudge = ("Your turn ended without a completed, verified "
                             f"result. You have {remaining} iterations left - "
                             "do not stop to describe what you plan to do; do "
                             "it now with tool calls, then re-read the original "
                             "task and prove each requirement is met by "
                             "running the relevant code or tests. Only when "
                             "everything passes, finish with your summary.")
                    messages.append({"role": "user", "content": nudge})
                    transcript.append({"type": "user", "content": nudge})
                    continue
                # Accepting a "done" the gate couldn't (or didn't need to)
                # challenge: if it has successful tool evidence behind it,
                # that's a genuine end_turn. If the gate simply ran out of
                # pushbacks or iterations, don't fail open - keep the text
                # but report "unverified" so the caller knows.
                verified = (not self.verify or not used_tools
                            or tools_since_nudge)
                return self._finish(AgentResult(
                    final_text=sr.text,
                    stop_reason="end_turn" if verified else "unverified",
                    iterations=iteration,
                    total_input_tokens=total_in, total_output_tokens=total_out,
                    total_cache_read_tokens=total_cache, transcript=transcript,
                ))

            # Stopped with no tool calls but not a clean end_turn: refusal,
            # content_filter, or an unmapped provider reason. Never report that
            # as success - surface the reason so the caller (and CLI exit code)
            # knows the run did not complete.
            if not sr.tool_calls:
                return self._finish(AgentResult(
                    final_text=sr.text, stop_reason=sr.stop_reason,
                    iterations=iteration,
                    total_input_tokens=total_in, total_output_tokens=total_out,
                    total_cache_read_tokens=total_cache, transcript=transcript,
                ))

            used_tools = True
            tool_results = self._execute_tool_calls(
                sr.tool_calls,
                transcript,
                can_request_follow=iteration < self.max_iterations,
            )
            if self._gt is not None:
                gt_submit_dirty = True
            # Only a *successful* tool counts as verification evidence - a
            # failed-only round must not satisfy the verify gate.
            if any(not r["is_error"] for r in tool_results):
                tools_since_nudge = True
            messages.append({"role": "user", "content": tool_results})
        except Exception as e:  # noqa: BLE001 - any failure becomes a result, not a crash
            transcript.append({"type": "error", "message": f"{type(e).__name__}: {e}"})
            return self._finish(AgentResult(
                final_text=f"agent error: {type(e).__name__}: {e}",
                stop_reason="error", iterations=iteration,
                total_input_tokens=total_in, total_output_tokens=total_out,
                total_cache_read_tokens=total_cache, transcript=transcript,
            ))

    def _finish(self, result: AgentResult) -> AgentResult:
        """Record the terminal agent outcome without changing result identity."""
        if self._gt is not None:
            try:
                self._gt.trace_run_completed(result)
            except Exception:  # noqa: BLE001 - telemetry never changes completion
                pass
        return result

    def _truncate_if_needed(self, messages: list[dict[str, Any]],
                            transcript: list[dict[str, Any]]) -> None:
        def total_chars() -> int:
            n = 0
            for m in messages:
                c = m.get("content")
                if isinstance(c, str):
                    n += len(c)
                elif isinstance(c, list):
                    for b in c:
                        n += len(b.get("text", "")) + len(b.get("content", ""))
                        # A tool_use block's args live under `input` - a huge
                        # edit_file `new` value hides here and re-inflates every
                        # request unless it is counted (and dropped) too.
                        for v in (b.get("input") or {}).values():
                            if isinstance(v, str):
                                n += len(v)
            return n

        if total_chars() <= self.truncation_char_budget:
            return

        # Drop oldest tool_result content first, then oversized tool_use inputs;
        # keep the block and its id so the tool_use/tool_result pairing survives.
        for m in messages:
            if not isinstance(m.get("content"), list):
                continue
            for b in m["content"]:
                if b.get("type") == "tool_result" and not str(
                        b.get("content", "")).startswith("[truncated"):
                    original_len = len(b.get("content", ""))
                    b["content"] = f"[truncated - {original_len} chars dropped]"
                    transcript.append({"type": "truncation",
                                       "tool_use_id": b.get("tool_use_id"),
                                       "dropped_chars": original_len})
                    if total_chars() <= self.truncation_char_budget:
                        return
        # Still over budget: shrink the largest string args of past tool_use
        # blocks (e.g. a giant edit_file `new`). The tool already ran; its
        # result is elsewhere in history, so the full input is no longer needed.
        for m in messages:
            if not isinstance(m.get("content"), list):
                continue
            for b in m["content"]:
                if b.get("type") != "tool_use":
                    continue
                inp = b.get("input") or {}
                for k, v in list(inp.items()):
                    if isinstance(v, str) and len(v) > 200 and not v.startswith(
                            "[truncated"):
                        inp[k] = f"[truncated - {len(v)} chars dropped]"
                        transcript.append({"type": "truncation",
                                           "tool_use_id": b.get("id"),
                                           "dropped_chars": len(v)})
                        if total_chars() <= self.truncation_char_budget:
                            return

    @staticmethod
    def _read_for_gt(path: Any) -> str | None:
        """File content snapshot for GT's edit bridges; None when unreadable
        (missing file = new-file creation, binary, bad arg). Never raises."""
        try:
            with open(path, encoding="utf-8", newline="") as f:
                return f.read()
        except Exception:  # noqa: BLE001
            return None

    def _assistant_message(self, sr: StepResult) -> dict[str, Any]:
        # Canonical shape: content blocks are the single source of truth for
        # both text and tool calls. Each provider re-serializes from these -
        # no duplicated tool_calls copy to drift out of sync under truncation.
        content_blocks: list[dict[str, Any]] = []
        if sr.text:
            content_blocks.append({"type": "text", "text": sr.text})
        for tc in sr.tool_calls:
            content_blocks.append({"type": "tool_use", "id": tc.id,
                                   "name": tc.name, "input": tc.arguments})
        return {"role": "assistant", "content": content_blocks}

    def _execute_tool_calls(self, calls: list[ToolCall],
                            transcript: list[dict[str, Any]],
                            *,
                            can_request_follow: bool = True,
                            ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for call in calls:
            effective_arguments = dict(call.arguments)
            budget_error = ""
            isolation_error = ""
            tool_control_code = ""
            if self._gt is not None:
                isolation_error = (
                    gt_harness_access_reason(
                        call.name, effective_arguments
                    ) or ""
                )
                if isolation_error:
                    tool_control_code = "harness_isolation"
                if not isolation_error and hasattr(
                    self._gt, "tool_control_reason"
                ):
                    try:
                        isolation_error = (
                            self._gt.tool_control_reason(
                                call.name, effective_arguments
                            ) or ""
                        )
                    except Exception:  # noqa: BLE001 - policy is fail-open
                        isolation_error = ""
                    if isolation_error:
                        tool_control_code = "lifecycle_control"
                if isolation_error and hasattr(
                    self._gt, "trace_tool_control"
                ):
                    try:
                        self._gt.trace_tool_control(
                            decision="REJECTED",
                            reason=isolation_error,
                            reason_code=tool_control_code,
                            tool_name=call.name,
                        )
                    except Exception:  # noqa: BLE001 - telemetry only
                        pass
            if call.name == "bash":
                raw_timeout = effective_arguments.get("timeout", 60)
                try:
                    requested_timeout = int(
                        60 if raw_timeout is None else raw_timeout
                    )
                except (TypeError, ValueError):
                    requested_timeout = None
                if requested_timeout is not None:
                    remaining_seconds = self._remaining_seconds()
                    allowed_timeout = affordable_bash_timeout(
                        requested_seconds=requested_timeout,
                        remaining_seconds=remaining_seconds,
                        reserve_seconds=self.finalization_reserve_seconds,
                    )
                    if allowed_timeout is None:
                        budget_error = (
                            "Command was not started because only the "
                            "wall-clock finish reserve remains. Summarize the "
                            "current verified state now; do not launch another "
                            "long-running command."
                        )
                    else:
                        effective_arguments["timeout"] = allowed_timeout
                    if self._gt is not None:
                        try:
                            self._gt.trace_tool_budget(
                                requested_seconds=requested_timeout,
                                allowed_seconds=allowed_timeout,
                                remaining_seconds=remaining_seconds,
                                reserve_seconds=(
                                    self.finalization_reserve_seconds
                                ),
                                decision=(
                                    "REJECTED"
                                    if allowed_timeout is None
                                    else (
                                        "CLAMPED"
                                        if allowed_timeout
                                        < requested_timeout
                                        else "ALLOWED"
                                    )
                                ),
                            )
                        except Exception:  # noqa: BLE001 - telemetry only
                            pass
            # GT edit bridges (B-3): edit-turn producers need the target file's
            # before/after content. Captured HERE (tools.py stays unchanged);
            # before-content read pre-dispatch, after-content post-dispatch.
            gt_edit_before: str | None = None
            if self._gt is not None and call.name == "edit_file":
                gt_edit_before = self._read_for_gt(
                    effective_arguments.get("path")
                )
                try:
                    self._gt.pre_edit_checkpoint(
                        call.name,
                        effective_arguments,
                        edit_before=gt_edit_before,
                    )
                except Exception:  # noqa: BLE001 - GT must never block dispatch
                    pass
            elif self._gt is not None and call.name == "bash":
                # Bash-mediated edit bridges: a redirect/sed edit cannot be
                # reverse-applied post-hoc, so the bridge snapshots the target
                # file at the PRE-dispatch boundary (never raises internally).
                try:
                    self._gt.capture_bash_preimage(effective_arguments)
                except Exception:  # noqa: BLE001 - GT must never break dispatch
                    pass
            try:
                if isolation_error:
                    raise ToolError(
                        isolation_error,
                        kind="gt_tool_control",
                        recovery="return_to_task_or_finish",
                    )
                if budget_error:
                    raise ToolError(
                        budget_error,
                        kind="wall_clock_budget",
                        recovery="finish_from_current_verified_state",
                    )
                output = dispatch(
                    call.name,
                    effective_arguments,
                    bash=self.bash,
                )
                is_error = False
            except ToolError as e:
                output = f"ERROR: {e}"
                is_error = True
            except Exception as e:  # noqa: BLE001 - a bad tool arg (e.g. wrong
                # type from a weak model) must come back as a fixable error,
                # not crash the whole run.
                output = f"ERROR: {type(e).__name__}: {e}"
                is_error = True
            # GT integration point 2: complete the observation with at most one
            # evidence dose, appended as a pure suffix. Any GT fault returns
            # the raw output unchanged (enrich also guards internally).
            if self._gt is not None:
                gt_edit_after: str | None = None
                if call.name == "edit_file" and not is_error:
                    gt_edit_after = self._read_for_gt(
                        effective_arguments.get("path")
                    )
                try:
                    output = self._gt.enrich(
                        call.name, effective_arguments, output, is_error,
                        edit_before=gt_edit_before, edit_after=gt_edit_after,
                        tool_call_id=call.id,
                        can_request_follow=can_request_follow)
                except Exception:  # noqa: BLE001 - GT must never break a tool turn
                    pass
            transcript.append({"type": "tool_result", "id": call.id,
                               "name": call.name, "output": output,
                               "is_error": is_error})
            self._emit({"type": "tool_result", "id": call.id,
                        "output": output, "is_error": is_error})
            results.append({"type": "tool_result", "tool_use_id": call.id,
                            "content": output, "is_error": is_error})
        return results
