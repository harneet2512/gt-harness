"""Concrete hooks for Mini-SWE-Agent 2.x.

The hooks use Mini-SWE's public agent/environment methods and its final
``_prepare_messages_for_api`` normalization seam. They are opt-in and preserve
stock behavior when not installed.

Seam responsibilities:

- **submit gate (W4)** — detect the real ``COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT``
  magic string in the COMMAND before execution; refuse (with a bounded tool
  message) when the contract gate is unmet, otherwise let the command run (the
  environment raises ``Submitted`` and the agent exits).
- **evidence pipeline (W2)** — classify each command result, derive the fine
  lifecycle boundary, run the Groundtruth gateway one-call (``augment`` ->
  ``arbitrate`` -> ``render_envelope``), and byte-splice the single capsule into
  the observation the model already reads.
- **provider response binding (W5)** — wrap ``model.query`` to record the
  terminal provider response + usage and bind it to the latest delivery.
- **contract delivery (E1)** — one full task contract at iteration 1, then
  obligation deltas only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MethodType
from typing import Any

from minisweagent.exceptions import Submitted

from .gt_session import GTMode, GTSession, GTSessionConfig
from .miniswe_evidence import (
    cap_evidence,
    classify_event,
    is_submit_command,
    run_evidence_pipeline,
)
from .miniswe_integration import MiniSweAdapter, ProviderModelMismatch
from .provider_limits import (
    ProviderRequestTooLarge,
    build_provider_request_envelope,
    enforce_provider_request_limit,
)
from .run_diagnostics import DiagnosticCode, DiagnosticEvent, classify_provider_failure
from .runtime_observation import (
    EditTransaction,
    capture_workspace,
    compile_execution_evidence,
    compile_transaction_artifacts,
    diff_workspace,
)

_SUBMIT_REFUSED_OUTPUT = "submission withheld by the Groundtruth contract gate"

_VIEW_CMD_RE = re.compile(r"(?:^|\s)(?:cat|sed|less|head|tail|nl|bat)\s+['\"]?([^\s'\"|;>&]+)")


def _created_files_excluding_exact_renames(
    transaction: EditTransaction,
) -> tuple[str, ...]:
    """Conservatively suppress create advice for byte-identical rename pairs."""
    deleted_hashes = {
        item.before_sha256 for item in transaction.changes
        if item.operation == "delete" and item.before_sha256
    }
    return tuple(
        item.path for item in transaction.changes
        if item.operation == "create" and item.after_sha256 not in deleted_hashes
    )


@dataclass
class RuntimeHookHandle:
    installed: bool = True
    agent: Any | None = None
    model: Any | None = None
    original_prepare: Any | None = None
    original_query: Any | None = None
    original_execute: Any | None = None
    session: GTSession | None = None
    native_prepare: Any | None = None
    native_query: Any | None = None
    native_transport: Any | None = None
    native_add_messages: Any | None = None

    def restore(self) -> None:
        """Restore Mini-SWE's original methods exactly (transparent bypass)."""
        if not self.installed:
            return
        if self.model is not None:
            if self.native_prepare is not None:
                self.model._prepare_messages_for_api = self.native_prepare
            if self.native_query is not None:
                self.model.query = self.native_query
            if self.native_transport is not None:
                self.model._query = self.native_transport
        if self.agent is not None and self.native_add_messages is not None:
            self.agent.add_messages = self.native_add_messages
        if self.agent is not None and self.original_execute is not None:
            self.agent.execute_actions = self.original_execute
            if getattr(self.agent, "_gt_runtime_hook_handle", None) is self:
                delattr(self.agent, "_gt_runtime_hook_handle")
        self.installed = False


def _command(action: Any) -> str:
    if isinstance(action, dict):
        return str(action.get("cmd") or action.get("command") or "")
    return str(getattr(action, "cmd", "") or getattr(action, "command", "") or "")


def _observation_output(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("output") or result.get("message") or "")
    return str(result or "")


def _returncode(result: Any) -> int | None:
    if isinstance(result, dict):
        rc = result.get("returncode")
        if rc is not None:
            return rc
        return -1 if result.get("exception_info") else 0
    return getattr(result, "returncode", 0)


_NOT_EXECUTED = {
    "output": "",
    "returncode": -1,
    "exception_info": "action was not executed",
}


def _refusal_directive(adapter: MiniSweAdapter) -> dict:
    """Visible proof-backed refusal that preserves continued exploration."""
    adapter.store.append(
        "submit_refusal",
        command_sha256=None,
        iteration=adapter.iteration,
        reasons=list(adapter.blocking_reasons),
    )
    unmet = adapter.blocking_obligation_texts()
    delta = adapter.next_contract_delta(max_chars=1000)
    lines = [
        "GT ENFORCED SUBMIT GATE: submission was not executed because current, "
        "workspace-bound RED evidence remains. You may continue with any "
        "search, edit, test, or alternate hypothesis before retrying.",
        "Transparent bypass: run this harness in advisory mode to restore stock "
        "Mini-SWE submission behavior.",
        "Active RED:",
    ]
    lines += [f"- {reason}" for reason in (unmet or ("active failure",))]
    if delta:
        lines.append(delta)
    return {"role": "user", "content": "\n".join(lines)}


def _run_submit_gate(session: GTSession, command: str) -> bool:
    """Run the submit gate; True = accepted (let the submission through)."""
    adapter = session.engine
    if adapter is None or session.disabled:
        return True
    if adapter.phase in {"IMPLEMENT", "VERIFY"}:
        if adapter.phase == "IMPLEMENT":
            adapter.begin_verify()
        adapter.begin_submit()
    if session.can_enforce:
        receipt = adapter.authorize_submit_suppression(command)
        if receipt is not None:
            adapter.begin_implement()
            return False
    accepted, _batch = session.request_submit()
    if accepted or not session.can_enforce:
        return accepted
    # A policy decision alone cannot suppress native Mini-SWE. Suppression is
    # authorized only by the canonical provider boundary's durable proof that
    # zero action/provider bytes were dispatched. Missing authority fails open.
    return True


def _refusal_text(adapter: MiniSweAdapter) -> str:
    blocking = adapter.blocking_reasons
    delta = adapter.next_contract_delta(max_chars=1200)
    lines = ["GT SUBMIT REFUSED: contract obligations are not fully proven.", "Unmet:"]
    lines += [f"- {reason}" for reason in (blocking or ("active failure",))]
    if delta:
        lines.append(delta)
    return "\n".join(lines)


def _viewed_files(command: str) -> tuple[str, ...]:
    from groundtruth.runtime.gateway import KIND_VIEW, classify_command

    if classify_command(command or "") != KIND_VIEW:
        return ()
    match = _VIEW_CMD_RE.search(command or "")
    return (match.group(1),) if match else ()


def _classify_test(command: str, output: str, returncode: int | None) -> str:
    try:
        from groundtruth.runtime.patterns import classify_test_observation

        outcome, _protocol = classify_test_observation(
            command or "", output or "", returncode
        )
        return outcome or ""
    except Exception:  # noqa: BLE001 - test classification is correct-or-quiet
        return ""


def _workspace_fingerprint(repo_root: str) -> dict[str, tuple[int, int] | str]:
    """Best-effort (mtime_ns, size) fingerprint of git-tracked files.

    The authoritative edit signal: ANY command that mutates a tracked file is
    an ``edit_result`` regardless of how it wrote (heredoc, ``python - <<EOF``,
    ``tee``, ``sed``, redirect, ...). Falls back to ``{}`` when ``repo_root``
    is not a git checkout, in which case ``bash_edit_targets`` remains the
    only edit detector.
    """
    if not repo_root:
        return {}
    try:
        import subprocess

        proc = subprocess.run(
            ["git", "-C", repo_root, "ls-files", "-z", "--cached", "--others",
             "--exclude-standard"],
            capture_output=True, timeout=8,
        )
    except Exception:  # noqa: BLE001 - non-git workspace is correct-or-quiet
        return {}
    if proc.returncode != 0:
        return {}
    files = proc.stdout.decode("utf-8", "replace").split("\0")
    fingerprint: dict[str, tuple[int, int] | str] = {}
    for rel in files:
        if not rel:
            continue
        try:
            st = os.stat(os.path.join(repo_root, rel))
            fingerprint[rel] = (st.st_mtime_ns, st.st_size)
        except OSError:
            fingerprint[rel] = "missing"
    return fingerprint


def _state_exclusion(adapter: MiniSweAdapter) -> Path:
    """Exclude harness state without excluding a repo-root test fixture."""
    repository = Path(adapter.repo_root).resolve()
    state_parent = adapter.store.root.parent.resolve()
    try:
        state_parent.relative_to(repository)
    except ValueError:
        return adapter.store.root.resolve()
    return (
        adapter.store.root.resolve()
        if state_parent == repository
        else state_parent
    )


def _capture_edit_preimage(
    adapter: MiniSweAdapter, command: str
) -> dict[str, str] | None:
    """Read the before content of a single edit target, if the command edits one."""
    from gt_engine.bridge import bash_edit_targets

    if not adapter.repo_root:
        return None
    targets = bash_edit_targets(command)
    if not targets:
        return None
    path = targets[0]
    abs_path = path if os.path.isabs(path) else os.path.join(adapter.repo_root, path)
    if not os.path.isfile(abs_path):
        return None
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as handle:
            return {path: handle.read()}
    except OSError:
        return None


def _capture_edit_after(
    adapter: MiniSweAdapter, preimage: dict[str, str] | None
) -> tuple[tuple[str, ...], dict[str, tuple[str | None, str]]]:
    """Read the after content and compute changed files + before/after pairs."""
    if not preimage:
        return (), {}
    edit_before_after: dict[str, tuple[str | None, str]] = {}
    changed: list[str] = []
    for path, before in preimage.items():
        abs_path = path if os.path.isabs(path) else os.path.join(adapter.repo_root, path)
        try:
            with open(abs_path, encoding="utf-8", errors="replace") as handle:
                after = handle.read()
        except OSError:
            after = None
        if after != before:
            changed.append(path)
            edit_before_after[path] = (before, after or "")
    return tuple(changed), edit_before_after


def _run_evidence(
    adapter: MiniSweAdapter,
    command: str,
    output: str,
    returncode: int | None,
    action_index: int,
    changed_files: tuple[str, ...],
    edit_before_after: dict[str, tuple[str | None, str]],
    created_files: tuple[str, ...] = (),
    *,
    allow_live_probes: bool = False,
) -> str:
    """One-dose GT evidence for an executed action; returns rendered bytes."""
    if adapter.contract is None:
        return ""
    if created_files:
        from .miniswe_covering import run_newfile_precedent

        precedent = run_newfile_precedent(adapter, tuple(created_files))
        if precedent:
            precedent = (
                "[GT_EVIDENCE:new_file_destination]\n"
                + cap_evidence(precedent, 600)
            )
            adapter.stage_model_visible_delivery(
                kind="new_file_destination",
                dedup_key=f"newfile-{adapter._latest_transaction_sha256}",
                target=created_files[0],
                semantics="advisory",
            )
            return precedent
    covering = None
    if changed_files and allow_live_probes:
        from .miniswe_covering import run_covering_lane

        covering = run_covering_lane(adapter, changed_files)
    elif returncode and _classify_test(command, output, returncode) in ("fail", "env_fail"):
        # Attribute the model's OWN failing test to the edited surface without
        # executing any additional command. This is advisory provenance, not
        # independent proof and therefore never creates execution authority.
        from .miniswe_covering import attribute_test_failure

        covering = attribute_test_failure(adapter, command, output, returncode=returncode)
        # A4/GT_HYPOTHESIS: track the failure fingerprint; a recurrence after
        # an edit schedules a bounded, transient recovery steer.
        try:
            from groundtruth.runtime.adapters.miniswe import (
                canonical_test_failure_fingerprint,
            )

            event_pre = classify_event(
                command, output, returncode,
                action_index=action_index,
                cwd=adapter.repo_root or os.getcwd(),
                changed_files=changed_files,
                viewed_files=_viewed_files(command),
                test_outcome="fail",
            )
            fingerprint = canonical_test_failure_fingerprint(event_pre)
            if fingerprint:
                adapter.note_failure_fingerprint(
                    fingerprint, epoch=adapter.workspace_epoch
                )
        except Exception:  # noqa: BLE001 - recovery tracking is correct-or-quiet
            pass
    event = classify_event(
        command,
        output,
        returncode,
        action_index=action_index,
        cwd=adapter.repo_root or os.getcwd(),
        changed_files=changed_files,
        viewed_files=_viewed_files(command),
        edit_before_after=edit_before_after or None,
        covering=covering,
        test_outcome=_classify_test(command, output, returncode),
    )
    if changed_files and allow_live_probes:
        from .miniswe_covering import run_newfile_precedent, run_syntax_probe

        syntax = run_syntax_probe(adapter, changed_files)
        if syntax:
            syntax = "[GT_EVIDENCE:syntax_result]\n" + cap_evidence(syntax)
            # Explicit ASSISTIVE mode may run this bounded probe. Its result is
            # evidence for the model, never an automatic execution gate.
            fallback = changed_files[0] if changed_files else "the edited file"
            first_file = next(
                (line.split(":")[0] for line in syntax.splitlines()
                 if ": syntax error" in line),
                fallback,
            )
            adapter.stage_model_visible_delivery(
                kind="syntax_result",
                dedup_key=f"syntax-{adapter.iteration}",
                target=first_file,
            )
            return syntax
    result = run_evidence_pipeline(
        adapter.gateway_state(),
        event,
        dedup_chain=adapter._dedup_chain,
        chain_head=adapter._chain_head,
        episode_id=adapter.task_id,
        event_id=f"{adapter.task_id}:{adapter.iteration}:{action_index}",
        native=os.environ.get("GT_GATEWAY_NATIVE") == "1",
        model_prefix=True,
    )
    if result.chain_head:
        adapter._chain_head = result.chain_head
    if result.sealed and result.envelope is not None:
        adapter.stage_model_visible_delivery(
            kind=str(result.envelope.evidence_type or ""),
            dedup_key=str(result.envelope.dedup_key or ""),
            target=str(getattr(result.envelope, "target", "") or ""),
        )
        # Self-diagnosing splice: the trajectory tool messages carry the exact
        # evidence type so a post-run census is exact, not heuristic.
        return result.rendered
    return cap_evidence(result.rendered)


def _coerce_session(owner: GTSession | MiniSweAdapter) -> GTSession:
    if isinstance(owner, GTSession):
        return owner
    return GTSession(
        GTSessionConfig(
            task_id=owner.task_id,
            repo_root=owner.repo_root,
            state_dir=str(owner.store.root.parent),
            mode=GTMode.ADVISORY,
        ),
        engine=owner,
    )


def install_runtime_hooks(
    agent: Any, owner: GTSession | MiniSweAdapter
) -> RuntimeHookHandle:
    """Install hooks once and return a stable handle.

    ``agent`` is expected to be a Mini-SWE ``DefaultAgent`` instance. A clear
    error is raised for missing seams instead of silently degrading attribution.
    """
    existing = getattr(agent, "_gt_runtime_hook_handle", None)
    if existing is not None:
        return existing
    session = _coerce_session(owner)
    adapter = session.engine
    if adapter is None:
        raise TypeError("GTSession requires an integration engine")
    model = getattr(agent, "model", None)
    native_prepare = getattr(model, "_prepare_messages_for_api", None)
    native_query = getattr(model, "query", None)
    native_transport = getattr(model, "_query", None)
    native_add_messages = getattr(agent, "add_messages", None)
    if not session.disabled:
        adapter.attach_provider_boundary(model, agent)
    prepare = getattr(model, "_prepare_messages_for_api", None)
    execute = getattr(agent, "execute_actions", None)
    environment = getattr(agent, "env", None)
    if not callable(prepare) or not callable(execute) or environment is None:
        raise TypeError("Mini-SWE agent must expose model normalization and execute_actions")

    if adapter.phase == "ORIENT" and not session.disabled:
        try:
            session.start()
        except Exception as exc:  # noqa: BLE001 - GT startup is fail-open
            session.degrade("session_start", exc)

    def prepare_messages(_model: Any, messages: list[dict]) -> list[dict]:
        prepared = prepare(messages)
        if session.disabled:
            return prepared
        try:
            if (
                adapter.graph_db
                and not adapter.graph_fresh
                and session.capability_active("graph_refresh")
            ):
                adapter.refresh_graph(phase="delivery")
            batch = session.before_model(messages, iteration=adapter.iteration)
            parts = batch.context_additions
            if parts and prepared and isinstance(prepared[-1], dict):
                last = dict(prepared[-1])
                content = last.get("content", "")
                if isinstance(content, str):
                    last["content"] = f"{content}\n\n" + "\n\n".join(parts)
                    prepared = [*prepared[:-1], last]
            adapter.bind_provider_payload({
                "messages": prepared,
                "model": str(getattr(_model, "model_name", "") or ""),
                "model_kwargs": dict(getattr(_model, "model_kwargs", {}) or {}),
                "tools": getattr(_model, "tools", None),
            })
        except Exception as exc:  # noqa: BLE001 - prompt augmentation is fail-open
            session.degrade("prepare_messages", exc)
        return prepared

    def query(_model: Any, messages: list[dict], **kwargs: Any) -> dict:
        try:
            # Admission covers the complete logical envelope. This happens
            # before original_query, so a refusal produces zero provider calls.
            enforce_provider_request_limit(
                build_provider_request_envelope(
                    messages=messages,
                    model=str(getattr(_model, "model_name", "") or ""),
                    model_kwargs=dict(getattr(_model, "model_kwargs", {}) or {}),
                    tools=getattr(_model, "tools", None),
                    call_kwargs=kwargs,
                )
            )
            message = original_query(messages, **kwargs)
        except Exception as exc:
            if not session.disabled:
                try:
                    adapter.bind_provider_failure(exc)
                    if isinstance(exc, ProviderRequestTooLarge):
                        code, retryable = (
                            DiagnosticCode.GT_PROVIDER_REQUEST_TOO_LARGE,
                            False,
                        )
                    else:
                        code, retryable = classify_provider_failure(exc)
                    adapter.diagnostics.record(
                        DiagnosticEvent.create(
                            code=code, severity="ERROR", phase="provider_transport",
                            subsystem="provider", capability="provider_transport",
                            task_id=adapter.task_id, classification="primary",
                            cause=type(exc).__name__, impact="provider_response_unavailable",
                            recovery=(
                                "refine_request_before_retry"
                                if not retryable
                                else "retry_transient_transport_failure"
                            ),
                            retryable=retryable,
                            event_sequence=int(adapter.store.receipt()["event_count"]),
                        )
                    )
                except Exception as receipt_exc:  # noqa: BLE001
                    session.degrade("provider_failure_receipt", receipt_exc)
            raise
        if not session.disabled:
            try:
                extra = message.get("extra") or {}
                response = extra.get("response")
                usage = ((response or {}).get("usage")
                         if isinstance(response, dict) else None)
                model_id = ((response or {}).get("model", "")
                            if isinstance(response, dict) else "")
                adapter.bind_provider_response(
                    response,
                    usage=usage,
                    model=model_id,
                    next_actions=tuple(extra.get("actions") or ()),
                )
            except ProviderModelMismatch:
                # A model substitution invalidates an A/B run. This is a
                # research-integrity failure, not an optional GT advisory.
                raise
            except Exception as exc:  # noqa: BLE001 - receipt failure is fail-open
                session.degrade("provider_response_receipt", exc)
        return message

    def execute_actions(_agent: Any, message: dict) -> list[dict]:
        from .miniswe_typed_actions import (
            execute_typed_action_fail_open,
            is_typed_action,
        )

        actions = tuple((message.get("extra") or {}).get("actions") or ())
        outputs: list[dict] = []
        repository_wide_queries = 0
        typed_turn_bytes = 0
        rendered_by_index: dict[int, str] = {}
        typed_by_index: dict[int, dict[str, Any]] = {}
        directives: list[dict] = []
        def submit_allowed() -> bool:
            if session.disabled:
                return True
            try:
                return _run_submit_gate(session, command)
            except Exception as exc:  # noqa: BLE001 - GT policy is fail-open
                session.degrade("submit_gate", exc)
                return True

        for action_index, action in enumerate(actions, start=1):
            if is_typed_action(action):
                # The planner explicitly selected this typed action. It never
                # reaches the shell environment, and it is never inferred from
                # Bash text. A router/analyzer fault produces an INCOMPLETE
                # observation so Mini-SWE can select Bash on its next turn.
                adapter.global_action += 1
                typed_kind = str((action.get("gt_action") or {}).get("kind") or "")
                typed_arguments = (action.get("gt_action") or {}).get("arguments") or {}
                scopes = typed_arguments.get("paths", ["."])
                repository_wide = bool(
                    typed_kind == "exact_literal_search"
                    and isinstance(scopes, list)
                    and "." in scopes
                )
                if repository_wide and repository_wide_queries >= 1:
                    output = json.dumps(
                        {
                            "schema": "gt.compiled_observation.v1",
                            "direct_answer": None,
                            "evidence": {
                                "schema": "gt.evidence_artifact.v1",
                                "semantics": "incomplete",
                                "omissions": ["query_fanout_refused"],
                            },
                            "decision": {
                                "schema": "gt.interception_decision.v1",
                                "mode": "PASS_THROUGH",
                                "reason_codes": ["refine_query_scope_next_turn"],
                            },
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    outputs.append(
                        {
                            "output": output,
                            "returncode": 2,
                            "exception_info": "repository-wide query fanout refused",
                            "extra": {"gt_typed_action": True},
                        }
                    )
                    continue
                repository_wide_queries += int(repository_wide)
                capability = f"typed_{typed_kind}"
                if not (
                    session.capability_active("typed_actions")
                    and session.capability_active(capability)
                ):
                    payload = {
                        "schema": "gt.compiled_observation.v1",
                        "action_request": None,
                        "direct_answer": None,
                        "evidence": {
                            "schema": "gt.evidence_artifact.v1",
                            "semantics": "incomplete",
                            "omissions": ["capability_disabled"],
                        },
                        "decision": {
                            "schema": "gt.interception_decision.v1",
                            "mode": "PASS_THROUGH",
                            "reason_codes": ["capability_disabled"],
                        },
                    }
                    output = json.dumps(
                        payload, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"),
                    )
                    outputs.append({
                        "output": output,
                        "returncode": 2,
                        "exception_info": "GroundTruth typed capability disabled",
                        "extra": {
                            "gt_typed_action": True,
                            "compiled_observation_sha256": hashlib.sha256(
                                output.encode("utf-8")
                            ).hexdigest(),
                            "interception_decision": "PASS_THROUGH",
                        },
                    })
                    continue
                if (
                    adapter.graph_db
                    and not adapter.graph_fresh
                    and session.capability_active("graph_refresh")
                    and session.capability_active("graph_queries")
                ):
                    adapter.refresh_graph(phase="graph_query")
                request, result = execute_typed_action_fail_open(
                    action,
                    repo_root=adapter.repo_root or os.getcwd(),
                    configuration={
                        "graph_db": (
                            adapter.graph_db
                            if adapter.graph_fresh
                            and session.capability_active("graph_queries")
                            else ""
                        ),
                        "graph_fresh": adapter.graph_fresh,
                        "repository_revision": adapter.repository_revision,
                        "gt_mode": session.mode.value,
                    },
                )
                result_bytes = len(str(result.get("output") or "").encode("utf-8"))
                if typed_turn_bytes + result_bytes > 49_152:
                    output = json.dumps(
                        {
                            "schema": "gt.compiled_observation.v1",
                            "direct_answer": None,
                            "evidence": {
                                "schema": "gt.evidence_artifact.v1",
                                "semantics": "incomplete",
                                "omissions": ["query_turn_budget_exceeded"],
                            },
                            "decision": {
                                "schema": "gt.interception_decision.v1",
                                "mode": "PASS_THROUGH",
                                "reason_codes": ["refine_query_scope_next_turn"],
                            },
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    result = {
                        "output": output,
                        "returncode": 2,
                        "exception_info": "typed query turn budget exceeded",
                        "extra": {"gt_typed_action": True},
                    }
                    result_bytes = len(output.encode("utf-8"))
                typed_turn_bytes += result_bytes
                outputs.append(result)
                extra = dict(result.get("extra") or {})
                typed_by_index[action_index] = {
                    "action_index": adapter.global_action,
                    "tool_call_id": str(action.get("tool_call_id") or ""),
                    "kind": str((action.get("gt_action") or {}).get("kind") or ""),
                    "action_request_sha256": str(
                        extra.get("action_request_sha256") or ""
                    ),
                    "compiled_observation_sha256": str(
                        extra.get("compiled_observation_sha256") or ""
                    ),
                    "interception_decision": str(
                        extra.get("interception_decision") or "PASS_THROUGH"
                    ),
                    "canonical_contract": type(request).__module__.startswith(
                        "groundtruth."
                    ) if request is not None else False,
                }
                continue
            command = _command(action)
            if not command:
                # Stock Mini-SWE still delegates a malformed/empty action to
                # the environment. GT must observe less, not invent a result
                # or consume an action the baseline would have executed.
                outputs.append(environment.execute(action))
                continue
            is_submit = False
            if not session.disabled:
                try:
                    is_submit = is_submit_command(command)
                except Exception as exc:  # noqa: BLE001 - detection is fail-open
                    session.degrade("submit_detection", exc)
            # Command-level fast path: the marker is literally in the command.
            if is_submit:
                if submit_allowed():
                    outputs.append(environment.execute(action))
                else:
                    outputs.append(dict(_NOT_EXECUTED))
                    directives.append(_refusal_directive(adapter))
                continue
            preimage = None
            pre_snapshot = None
            if not session.disabled:
                try:
                    adapter.global_action += 1
                    adapter.before_action("bash", command)
                    preimage = _capture_edit_preimage(adapter, command)
                    if (
                        adapter.repo_root
                        and session.capability_active("snapshot_authority")
                    ):
                        pre_snapshot = capture_workspace(
                            adapter.repo_root,
                            excluded_roots=(_state_exclusion(adapter),),
                        )
                        adapter.record_repository_snapshot(
                            pre_snapshot, boundary="before_action"
                        )
                except Exception as exc:  # noqa: BLE001 - observation is fail-open
                    session.degrade("before_action", exc)
            try:
                result = environment.execute(action)
            except Submitted:
                # RESULT-level submit interception: the command's OUTPUT began
                # with the magic string even though the command text did not
                # (an adversarial shell-joined bypass). The gate is the
                # authority here, exactly as Mini-SWE's own _check_finished is
                # for a legitimate submit. Refused -> instruction-channel
                # directive; accepted -> let the run exit via the raised
                # Submitted.
                if submit_allowed():
                    raise
                outputs.append(dict(_NOT_EXECUTED))
                directives.append(_refusal_directive(adapter))
                continue
            outputs.append(result)
            if session.disabled:
                continue
            output = _observation_output(result)
            returncode = _returncode(result)
            try:
                changed_files, edit_before_after = _capture_edit_after(adapter, preimage)
                created_files: tuple[str, ...] = ()
                if pre_snapshot is not None:
                    post_snapshot = capture_workspace(
                        adapter.repo_root,
                        excluded_roots=(_state_exclusion(adapter),),
                    )
                    transaction = diff_workspace(
                        pre_snapshot,
                        post_snapshot,
                        action_id=adapter.global_action,
                        command=command,
                    )
                    adapter.record_repository_snapshot(
                        post_snapshot, boundary="after_action"
                    )
                    if transaction.changes:
                        adapter.record_edit_transaction(transaction)
                        adapter.record_transaction_artifacts(
                            compile_transaction_artifacts(
                                transaction,
                                graph_db=(
                                    adapter.graph_db if adapter.graph_fresh else None
                                ),
                            )
                        )
                        changed_files = transaction.changed_paths
                        created_files = _created_files_excluding_exact_renames(transaction)
                        edit_before_after = {
                            item.path: (
                                item.before.decode("utf-8", "replace")
                                if item.before is not None else None,
                                item.after.decode("utf-8", "replace")
                                if item.after is not None else "",
                            )
                            for item in transaction.changes
                            if item.before is not None or item.after is not None
                        }
                if changed_files:
                    if adapter.phase != "IMPLEMENT":
                        adapter.begin_implement()
                    adapter.note_edit(changed_files)
                if returncode not in (None, 0):
                    adapter.record_episode_failure(
                        command=command,
                        output=output,
                        returncode=returncode,
                        pre_state_revision=(
                            pre_snapshot.revision if pre_snapshot is not None else ""
                        ),
                    )
                lower_command = command.lower()
                if any(word in lower_command for word in (
                    "pytest", "test", "check", "verify"
                )) and adapter.phase == "IMPLEMENT":
                    adapter.begin_verify()
                adapter.after_observation(output)
                session.after_action(
                    command=command,
                    output=output,
                    returncode=returncode,
                    action_index=action_index,
                )
                rendered = ""
                if session.capability_active("evidence_delivery"):
                    rendered = _run_evidence(
                        adapter, command, output, returncode, adapter.global_action,
                        changed_files, edit_before_after, created_files,
                        allow_live_probes=session.allows_live_probes,
                    )
                    if not session.capability_model_visible("evidence_delivery"):
                        rendered = ""
                execution = compile_execution_evidence(
                    command=command,
                    output=output,
                    returncode=returncode,
                    action_id=adapter.global_action,
                    repository_revision=adapter.repository_revision,
                )
                if (
                    execution is not None
                    and session.capability_active("execution_evidence")
                ):
                    structured = adapter.record_execution_evidence(execution)
                    if session.capability_model_visible("execution_evidence"):
                        rendered = "\n".join(
                            part for part in (structured, rendered) if part
                        )
                if rendered and session.model_visible:
                    metadata = adapter.consume_model_visible_delivery_metadata()
                    kind = metadata.pop("kind", "execution_evidence")
                    dedup_key = metadata.pop(
                        "dedup_key",
                        f"action:{adapter.iteration}:{action_index}:{kind}",
                    )
                    if adapter.admit_model_visible_delivery(
                        lane="sealed",
                        kind=kind,
                        rendered=rendered,
                        action_index=action_index,
                        iteration=adapter.iteration,
                        dedup_key=dedup_key,
                        **metadata,
                    ):
                        rendered_by_index[action_index] = rendered
                if adapter.pending_transient and session.model_visible:
                    directives.append({
                        "role": "user", "content": adapter.pending_transient,
                    })
                    adapter.pending_transient = ""
            except Exception as exc:  # noqa: BLE001 - preserve tool observation
                session.degrade("after_action", exc)
                rendered_by_index.clear()
                directives.clear()
        if not session.disabled and session.model_visible:
            for directive in adapter.pending_directives:
                directives.append({"role": "user", "content": directive})
        adapter.pending_directives = []
        formatter = getattr(model, "format_observation_messages", None)
        if callable(formatter):
            formatted = list(formatter(message, outputs, agent.get_template_vars()))
            if not session.disabled:
                baseline_formatted = list(formatted)
                try:
                    for index, obs in enumerate(formatted):
                        if (index + 1 in rendered_by_index
                                and obs.get("role") == "tool"):
                            content = str(obs.get("content") or "")
                            obs = dict(obs)
                            obs["content"] = (
                                f"<gt-facts>\n{rendered_by_index[index + 1]}"
                                f"\n</gt-facts>\n{content}"
                            )
                            formatted[index] = obs
                    for index, obs in enumerate(formatted):
                        typed = typed_by_index.get(index + 1)
                        if typed is None:
                            continue
                        adapter.record_typed_observation(
                            **typed,
                            final_observation_sha256=hashlib.sha256(
                                str(obs.get("content") or "").encode("utf-8")
                            ).hexdigest(),
                        )
                except Exception as exc:  # noqa: BLE001 - splice is fail-open
                    session.degrade("observation_splice", exc)
                    formatted = baseline_formatted
                    directives = []
            return agent.add_messages(*formatted, *directives)
        return [*outputs, *directives]

    original_query = getattr(model, "query", None)
    if not callable(original_query):
        raise TypeError("Mini-SWE model must expose query() for response binding")

    model._gt_original_prepare_messages_for_api = prepare
    model._prepare_messages_for_api = MethodType(prepare_messages, model)
    model._gt_original_query = original_query
    model.query = MethodType(query, model)
    agent._gt_original_execute_actions = execute
    agent.execute_actions = MethodType(execute_actions, agent)
    handle = RuntimeHookHandle(
        agent=agent,
        model=model,
        original_prepare=prepare,
        original_query=original_query,
        original_execute=execute,
        session=session,
        native_prepare=native_prepare,
        native_query=native_query,
        native_transport=native_transport,
        native_add_messages=native_add_messages,
    )
    agent._gt_runtime_hook_handle = handle
    return handle
