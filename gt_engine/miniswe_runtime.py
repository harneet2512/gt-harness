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
import shlex
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import MethodType
from typing import Any

from minisweagent.exceptions import Submitted

from .gt_session import GTDecisionCandidate, GTMode, GTSession, GTSessionConfig
from .miniswe_evidence import (
    classify_event,
    is_submit_command,
    run_evidence_pipeline,
)
from .miniswe_integration import MiniSweAdapter, ProviderModelMismatch
from .provider_limits import (
    ProviderContextWindowUnavailable,
    ProviderRequestTooLarge,
    provider_request_tokens,
    render_and_admit_provider_request,
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

_VIEW_COMMANDS = frozenset({"cat", "sed", "less", "head", "tail", "nl", "bat"})

# These typed queries require a certified graph. Native action snapshots
# independently schedule nonblocking refresh through the same coordinator.
_GRAPH_DEPENDENT_TYPED_KINDS = frozenset({
    "definition", "references", "callers", "patch_impact", "why_this_edge",
})


@dataclass(frozen=True)
class _EvidenceCandidate:
    priority: int
    kind: str
    rendered: str
    metadata: dict[str, str]
    chain_head: str = ""
    dedup_key: str = ""
    previous_chain_head: str = ""
    artifact_reference: dict | None = None


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
    native_exact_provider_payload: Any | None = None

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
            if self.native_exact_provider_payload is None:
                if hasattr(self.model, "_gt_exact_provider_payload"):
                    delattr(self.model, "_gt_exact_provider_payload")
            else:
                self.model._gt_exact_provider_payload = self.native_exact_provider_payload
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


def _observation_bytes(result: Any) -> bytes:
    if isinstance(result, dict):
        extra = result.get("extra") or {}
        if isinstance(extra, dict) and "output_artifact" in extra:
            from gt_engine.output_evidence import EvidenceStore

            ref = extra["output_artifact"]
            # Canonical producers currently accept complete text. A transport
            # preview must not silently downgrade their supported semantics.
            # Streaming analyzer migration remains a separate open requirement.
            return EvidenceStore(ref["root"]).bytes(ref["sha256"])
        if isinstance(extra, dict) and "raw_output" in extra:
            return str(extra.get("raw_output") or "").encode("utf-8")
        return str(result.get("output") or result.get("message") or "").encode("utf-8")
    return str(result or "").encode("utf-8")


def _observation_output(result: Any) -> str:
    return _observation_bytes(result).decode("utf-8", "replace")


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
        action_index=adapter.global_action,
        executed=False,
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


def _run_submit_gate(session: GTSession, command: str, *, pre_execution: bool = False) -> bool:
    """Run the submit gate; True = accepted (let the submission through)."""
    adapter = session.engine
    if adapter is None or session.disabled:
        return True
    if not pre_execution and adapter.phase in {"IMPLEMENT", "VERIFY"}:
        if adapter.phase == "IMPLEMENT":
            adapter.begin_verify()
        adapter.begin_submit()
    if pre_execution and session.can_enforce:
        receipt = adapter.authorize_submit_suppression(command)
        if receipt is not None:
            adapter.begin_implement()
            return False
    if pre_execution:
        return True
    accepted, _batch = session.request_submit()
    if accepted or not session.can_enforce:
        return accepted
    # A policy decision alone cannot suppress native Mini-SWE. Suppression is
    # authorized only by the canonical provider boundary's durable proof that
    # zero action/provider bytes were dispatched. Missing authority fails open.
    session.degrade("terminal_refusal_authority", RuntimeError(
        "pre-execution suppression authority cannot refuse an executed action"
    ))
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
    try:
        lexer = shlex.shlex(
            command or "", posix=True, punctuation_chars="|;&"
        )
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return ()

    segments: list[list[str]] = [[]]
    for token in tokens:
        if token and all(char in "|;&" for char in token):
            segments.append([])
        else:
            segments[-1].append(token)

    found: list[str] = []
    for segment in segments:
        if not segment:
            continue
        head = Path(segment[0]).name.lower()
        if head not in _VIEW_COMMANDS:
            continue
        args = segment[1:]
        operands: list[str] = []
        if head == "sed":
            script_seen = False
            index = 0
            while index < len(args):
                value = args[index]
                if value == "--":
                    operands.extend(args[index + 1:])
                    break
                if value in {"-e", "--expression", "-f", "--file"}:
                    script_seen = True
                    index += 2
                    continue
                if value.startswith(("--expression=", "--file=")):
                    script_seen = True
                elif value.startswith("-"):
                    pass
                elif not script_seen:
                    script_seen = True
                else:
                    operands.append(value)
                index += 1
        else:
            option_values = {
                "head": {"-n", "--lines", "-c", "--bytes"},
                "tail": {"-n", "--lines", "-c", "--bytes", "-s", "--sleep-interval"},
                "nl": {"-b", "--body-numbering", "-d", "--section-delimiter", "-f",
                       "--footer-numbering", "-h", "--header-numbering", "-i",
                       "--line-increment", "-l", "--join-blank-lines", "-n",
                       "--number-format", "-s", "--number-separator", "-v",
                       "--starting-line-number", "-w", "--number-width"},
            }.get(head, set())
            index = 0
            while index < len(args):
                value = args[index]
                if value == "--":
                    operands.extend(args[index + 1:])
                    break
                if value in option_values:
                    index += 2
                    continue
                if value.startswith("-") or value.startswith((">", "<")):
                    index += 1
                    continue
                operands.append(value)
                index += 1
        found.extend(
            value for value in operands
            if value and value != "-" and not value.startswith((">", "<"))
        )
    return tuple(dict.fromkeys(found))


def _classify_test(command: str, output: str, returncode: int | None) -> str:
    from .runtime_observation import classify_execution_outcome

    outcome = classify_execution_outcome(command or "", output or "", returncode)
    return outcome if outcome in {"pass", "fail", "env_fail", "executed_no_tests"} else ""


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


def _refresh_native_graph(adapter: MiniSweAdapter, session: GTSession) -> None:
    if (session.capability_active("graph_refresh")
            and session.capability_active("graph_queries")):
        adapter.refresh_graph(phase="native_action")


def _state_exclusions(adapter: MiniSweAdapter) -> tuple[Path, ...]:
    return adapter.engine_state.layout.excluded_roots


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
    repository = Path(adapter.repo_root).resolve()
    abs_path = (repository / path).resolve()
    try:
        path = abs_path.relative_to(repository).as_posix()
    except ValueError:
        return None
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
        repository = Path(adapter.repo_root).resolve()
        abs_path = (repository / path).resolve()
        try:
            path = abs_path.relative_to(repository).as_posix()
        except ValueError:
            continue
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
    decision_session: GTSession | None = None,
    additional_candidates: tuple[GTDecisionCandidate, ...] = (),
    output_artifact: dict | None = None,
) -> str:
    """Collect eligible producers for the session-owned decision packet."""
    session = decision_session or _coerce_session(adapter)
    if adapter.contract is None:
        if decision_session is not None:
            session.queue_decision_candidates(additional_candidates)
            return ""
        return "\n".join(session.admit_decision_packet(
            additional_candidates, iteration=adapter.iteration, action_index=action_index,
        ).context_additions)
    covering = None
    candidates: list[_EvidenceCandidate] = []
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
                output_artifact=output_artifact,
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
        output_artifact=output_artifact,
    )
    if changed_files and allow_live_probes:
        from .miniswe_covering import run_syntax_probe

        syntax = run_syntax_probe(adapter, changed_files)
        if syntax:
            syntax = "[GT_EVIDENCE:syntax_result]\n" + syntax
            # Explicit ASSISTIVE mode may run this bounded probe. Its result is
            # evidence for the model, never an automatic execution gate.
            fallback = changed_files[0] if changed_files else "the edited file"
            first_file = next(
                (line.split(":")[0] for line in syntax.splitlines()
                 if ": syntax error" in line),
                fallback,
            )
            candidates.append(_EvidenceCandidate(
                90, "syntax_result", syntax,
                {"kind": "syntax_result", "dedup_key": f"syntax-{adapter.iteration}",
                 "target": first_file},
            ))
    proposed_dedup, proposed_head = adapter.pending_evidence_chain()
    from .output_evidence import EvidenceStore
    from .request_history import store_history_evidence

    evidence_store = EvidenceStore(adapter.engine_state.layout.evidence_root)
    result = run_evidence_pipeline(
        adapter.gateway_state(),
        event,
        dedup_chain=proposed_dedup,
        chain_head=proposed_head,
        episode_id=adapter.task_id,
        event_id=f"{adapter.task_id}:{adapter.iteration}:{action_index}",
        native=os.environ.get("GT_GATEWAY_NATIVE") == "1",
        model_prefix=True,
        commit=False,
        artifact_store=evidence_store,
    )
    for dose in result.doses:
        kind = str(dose.envelope.evidence_type or "")
        candidates.append(_EvidenceCandidate(
            100 if event.test_outcome in {"fail", "env_fail"} else 80,
            kind, dose.rendered,
            {"kind": kind, "dedup_key": str(dose.envelope.dedup_key or ""),
             "target": str(getattr(dose.envelope, "target", "") or "")},
            chain_head=dose.chain_head,
            dedup_key=str(dose.envelope.dedup_key or ""),
            previous_chain_head=dose.previous_chain_head or proposed_head,
            artifact_reference=dose.artifact_reference,
        ))
    verification, verification_metadata = adapter.verification_candidate()
    if verification:
        candidates.append(
            _EvidenceCandidate(
                70,
                "verification_plan",
                verification,
                verification_metadata,
                dedup_key=verification_metadata.get("dedup_key", ""),
            )
        )
    # A nearby example must not suppress an executed failure or current graph
    # evidence merely because this transaction also created a file.
    if created_files:
        from .miniswe_covering import run_newfile_precedent

        precedent = run_newfile_precedent(adapter, tuple(created_files))
        if precedent:
            candidates.append(_EvidenceCandidate(
                20, "new_file_destination",
                "[GT_EVIDENCE:new_file_destination]\n" + precedent,
                {"kind": "new_file_destination",
                 "dedup_key": f"newfile-{adapter._latest_transaction_sha256}",
                 "target": created_files[0], "semantics": "advisory"},
            ))
    cochange = _cochange_prior(adapter, command, changed_files)
    cochange_metadata = adapter.consume_model_visible_delivery_metadata()
    if cochange:
        candidates.append(_EvidenceCandidate(
            10, cochange_metadata.get("kind", "cochange_partner"), cochange,
            cochange_metadata or {"kind": "cochange_partner",
                                  "dedup_key": "cochange-unbound"},
        ))
    packet = [*additional_candidates]
    for ordinal, candidate in enumerate(candidates):
        metadata = dict(candidate.metadata)
        producer_artifact = metadata.pop("artifact_sha256", "")
        reference = candidate.artifact_reference or store_history_evidence(
            evidence_store, candidate.rendered.encode(), kind="decision_evidence",
        )
        packet.append(GTDecisionCandidate(
            rendered=candidate.rendered, **metadata,
            artifact_sha256=producer_artifact or reference["sha256"], artifact_reference=reference,
            unit_id=reference["sha256"],
            supersession_key=f"{candidate.kind}:{candidate.metadata.get('target') or candidate.metadata.get('dedup_key')}",
            source_revision=adapter.repository_revision,
            previous_chain_head=candidate.previous_chain_head,
            next_chain_head=candidate.chain_head,
            verification_candidate=(candidate.rendered
                                    if candidate.kind == "verification_plan" else ""),
            source_ordinal=ordinal,
            action_index=action_index,
            current_failure=(candidate.kind == "syntax_result"
                             or (candidate.priority == 100
                                 and event.test_outcome in {"fail", "env_fail"})),
        ))
    if decision_session is not None:
        session.queue_decision_candidates(packet)
        return ""
    return "\n".join(session.admit_decision_packet(
        packet, iteration=adapter.iteration, action_index=action_index,
    ).context_additions)


def _cochange_prior(
    adapter: MiniSweAdapter, command: str, changed_files: tuple[str, ...]
) -> str:
    """Advisory co-change dose for the files this action edited or viewed."""
    from .cochange_evidence import cochange_prior_dose

    files = tuple(dict.fromkeys((*changed_files, *_viewed_files(command))))
    if not files:
        return ""
    try:
        return cochange_prior_dose(adapter, files)
    except Exception:  # noqa: BLE001 - a prior is correct-or-quiet, never fatal
        return ""


def _coerce_session(owner: GTSession | MiniSweAdapter) -> GTSession:
    if isinstance(owner, GTSession):
        return owner
    return GTSession(
        GTSessionConfig(
            task_id=owner.task_id,
            repo_root=owner.repo_root,
            state_dir=str(owner.engine_state.layout.state_root),
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
    model._gt_session = session
    native_prepare = getattr(model, "_prepare_messages_for_api", None)
    native_query = getattr(model, "query", None)
    native_transport = getattr(model, "_query", None)
    native_add_messages = getattr(agent, "add_messages", None)
    native_exact_provider_payload = getattr(model, "_gt_exact_provider_payload", None)
    try:
        from minisweagent.models.litellm_model import BASH_TOOL, LitellmModel
    except ImportError:
        BASH_TOOL, LitellmModel = None, ()  # type: ignore[assignment,misc]
    if isinstance(model, LitellmModel):
        import litellm

        def tool_configurable_transport(
            _model: Any, messages: list[dict], **kwargs: Any
        ) -> Any:
            provider_tools = kwargs.pop("_gt_provider_tools", None) or [BASH_TOOL]
            kwargs.pop("_gt_select_catalog", None)
            try:
                return litellm.completion(
                    model=_model.config.model_name,
                    messages=messages,
                    tools=provider_tools,
                    **(_model.config.model_kwargs | kwargs),
                )
            except litellm.exceptions.AuthenticationError as exc:
                exc.message += (
                    " You can permanently set your API key with "
                    "`mini-extra config set KEY VALUE`."
                )
                raise

        def exact_provider_payload(
            messages: list[dict], kwargs: dict[str, Any]
        ) -> dict[str, Any]:
            call_kwargs = dict(kwargs)
            provider_tools = call_kwargs.pop("_gt_provider_tools", None) or [BASH_TOOL]
            call_kwargs.pop("_gt_select_catalog", None)
            return {
                "model": model.config.model_name,
                "messages": messages,
                "tools": provider_tools,
                **(dict(model.config.model_kwargs) | call_kwargs),
            }

        model._query = MethodType(tool_configurable_transport, model)
        model._gt_exact_provider_payload = exact_provider_payload
    if not session.disabled:
        adapter.attach_provider_boundary(model, agent)
    prepare = getattr(model, "_prepare_messages_for_api", None)
    transport = getattr(model, "_query", None)
    execute = getattr(agent, "execute_actions", None)
    environment = getattr(agent, "env", None)
    if not callable(prepare) or not callable(execute) or environment is None:
        raise TypeError("Mini-SWE agent must expose model normalization and execute_actions")

    if adapter.phase == "ORIENT" and not session.disabled:
        try:
            session.start()
        except Exception as exc:  # noqa: BLE001 - GT startup is fail-open
            session.degrade("session_start", exc)

    bootstrap_started = False
    bootstrap_preparing = False

    def prepare_messages(_model: Any, messages: list[dict]) -> list[dict]:
        if session.disabled:
            if native_add_messages is not None:
                agent.add_messages = native_add_messages
            return native_prepare(messages)
        prepared = prepare(messages)
        if bootstrap_preparing:
            return prepared
        baseline_prepared = prepared
        try:
            # Edits invalidate graph-derived claims, but an ordinary provider
            # turn does not consume the graph and must not synchronously rebuild
            # the whole repository. Native action boundaries schedule and poll
            # the existing asynchronous coordinator independently of queries.
            batch = session.before_model(messages, iteration=adapter.iteration)
            parts = batch.context_additions
            if parts and prepared and isinstance(prepared[-1], dict):
                last = dict(prepared[-1])
                content = last.get("content", "")
                if isinstance(content, str):
                    last["content"] = f"{content}\n\n" + "\n\n".join(parts)
                    prepared = [*prepared[:-1], last]
        except Exception as exc:  # noqa: BLE001 - prompt augmentation is fail-open
            adapter.discard_pending_provider_deliveries(
                reason="prepare_messages_error"
            )
            session.degrade("prepare_messages", exc)
            prepared = baseline_prepared
        return prepared

    def query_transport(_model: Any, messages: list[dict], **kwargs: Any) -> Any:
        """Admit only the exact, already-prepared payload sent to transport."""
        provider_tools = kwargs.pop("_gt_provider_tools", None)
        bootstrap_request = bool(kwargs.pop("_gt_select_catalog", False))
        if session.disabled:
            adapter.discard_pending_provider_deliveries(
                reason="gt_disabled_before_transport"
            )
            return native_transport(messages, **kwargs)
        if session.capability_model_visible("evidence_delivery"):
            recovery = adapter.prepare_recovery_delivery()
            if recovery:
                messages = [*messages, {"role": "user", "content": recovery}]
        context_window = int(os.environ.get("GT_PROVIDER_CONTEXT_WINDOW_TOKENS", "0") or 0)
        reserved_output = int(
            os.environ.get("GT_PROVIDER_RESERVED_OUTPUT_TOKENS", "0") or 0
        )
        metadata_source = os.environ.get("GT_PROVIDER_CONTEXT_WINDOW_SOURCE", "")
        config = getattr(_model, "config", None)
        model_name = str(
            getattr(config, "model_name", "")
            or getattr(_model, "model_name", "")
            or ""
        )
        model_kwargs = dict(
            getattr(config, "model_kwargs", None)
            or getattr(_model, "model_kwargs", None)
            or {}
        )
        tools = provider_tools if provider_tools is not None else getattr(_model, "tools", None)
        if tools is None:
            try:
                from minisweagent.models.litellm_model import BASH_TOOL

                tools = [BASH_TOOL]
            except ImportError:
                tools = None
        try:
            from .context import compact_provider_view
            from .output_evidence import EvidenceStore

            # This seam receives Mini-SWE's provider-rendered messages. Keep its
            # complete current action batch and archive older complete turns.
            messages, history_receipt = compact_provider_view(
                messages, checkpoint="",
                char_budget=max(1, context_window - reserved_output) * 2,
                max_tail_turns=max(2, len(messages)),
                artifact_store=EvidenceStore(adapter.engine_state.layout.evidence_root),
            )
            adapter.store.append("context_assembly", **history_receipt)
            messages, payload, admission = render_and_admit_provider_request(
                messages=messages, render_messages=lambda prepared: prepared,
                model=model_name, model_kwargs=model_kwargs, tools=tools,
                call_kwargs=kwargs,
                context_window_tokens=context_window,
                reserved_output_tokens=reserved_output,
                metadata_source=metadata_source,
                token_counter=provider_request_tokens,
            )
        except (ProviderContextWindowUnavailable, ProviderRequestTooLarge) as exc:
            details = (
                exc.admission.to_dict()
                if isinstance(exc, ProviderRequestTooLarge)
                else exc.to_dict()
            )
            adapter.store.append(
                "provider_admission",
                status="refused",
                reason=exc.code,
                **details,
            )
            adapter.discard_pending_provider_deliveries(reason=exc.code)
            raise
        except Exception:
            adapter.discard_pending_provider_deliveries(reason="provider_admission_error")
            raise
        adapter.store.append(
            "provider_admission",
            status="admitted",
            reason="within_provider_window",
            **admission.to_dict(),
        )
        try:
            delivery = adapter.bind_provider_payload(payload)
        except Exception:
            adapter.discard_pending_provider_deliveries(reason="request_receipt_error")
            raise
        if bootstrap_request:
            session.certify_select_catalog_offer(
                request_bytes=json.dumps(
                    payload, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), default=str,
                ).encode("utf-8"),
                tool_schema_bytes=json.dumps(
                    tools, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), default=str,
                ).encode("utf-8"),
                provider_request_id=delivery.request_id,
                delivery_ids=delivery.delivery_ids,
            )
        else:
            session.provider_request_admitted(delivery.delivery_ids)
        return transport(
            messages,
            **kwargs,
            **({"_gt_provider_tools": tools} if provider_tools is not None else {}),
            **({"_gt_select_catalog": True} if bootstrap_request else {}),
        )

    def bootstrap_select_catalog() -> None:
        """Run one explicit catalog request without creating Mini-SWE actions."""

        nonlocal bootstrap_started, bootstrap_preparing
        if bootstrap_started:
            return
        bootstrap_started = True
        offer = session.prepare_select_catalog()
        if offer is None:
            return
        captured: dict[str, Any] = {"arguments": None}
        original_parser = getattr(model, "_parse_actions", None)

        def parse_selection(_model: Any, response: Any) -> list[dict]:
            choices = (
                list(response.get("choices") or ())
                if isinstance(response, dict)
                else list(getattr(response, "choices", ()) or ())
            )
            message = None
            if choices:
                first = choices[0]
                message = first.get("message") if isinstance(first, dict) else getattr(first, "message", None)
            calls = (
                list(message.get("tool_calls") or ())
                if isinstance(message, dict)
                else list(getattr(message, "tool_calls", ()) or ())
            )
            if len(calls) != 1:
                return []
            call = calls[0]
            function = call.get("function") if isinstance(call, dict) else getattr(call, "function", None)
            name = function.get("name") if isinstance(function, dict) else getattr(function, "name", "")
            raw = function.get("arguments") if isinstance(function, dict) else getattr(function, "arguments", "")
            if name != "select_catalog":
                return []
            try:
                captured["arguments"] = json.loads(str(raw or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                captured["arguments"] = None
            return []

        try:
            if callable(original_parser):
                model._parse_actions = MethodType(parse_selection, model)
            bootstrap_preparing = True
            message = native_query(
                list(offer.messages),
                _gt_provider_tools=[dict(offer.tool)],
                _gt_select_catalog=True,
                temperature=0.0,
                max_tokens=256,
                num_retries=0,
            )
            extra = dict(message.get("extra") or {})
            if captured["arguments"] is None:
                captured["arguments"] = extra.get("select_catalog_args")
            response = extra.get("response")
            usage = response.get("usage") if isinstance(response, dict) else None
            model_id = response.get("model", "") if isinstance(response, dict) else ""
            adapter.bind_provider_response(
                response, usage=usage, model=model_id, next_actions=()
            )
            session.accept_select_catalog(captured["arguments"])
        except Exception as exc:  # noqa: BLE001 - selection is advisory
            adapter.bind_provider_failure(exc)
            session.fail_select_catalog(f"provider_error:{type(exc).__name__}")
        finally:
            bootstrap_preparing = False
            if callable(original_parser):
                model._parse_actions = original_parser

    def query(_model: Any, messages: list[dict], **kwargs: Any) -> dict:
        if session.disabled:
            return native_query(messages, **kwargs)
        try:
            bootstrap_select_catalog()
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
                    elif isinstance(exc, ProviderContextWindowUnavailable):
                        code, retryable = (
                            DiagnosticCode.GT_PROVIDER_CONTEXT_WINDOW_UNAVAILABLE,
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
        def submit_allowed(*, pre_execution: bool = False) -> bool:
            if session.disabled:
                return True
            try:
                return _run_submit_gate(session, command, pre_execution=pre_execution)
            except Exception as exc:  # noqa: BLE001 - GT policy is fail-open
                session.degrade("submit_gate", exc)
                return True

        for action_index, action in enumerate(actions, start=1):
            adapter.global_action += 1
            if is_typed_action(action):
                # The planner explicitly selected this typed action. It never
                # reaches the shell environment, and it is never inferred from
                # Bash text. A router/analyzer fault produces an INCOMPLETE
                # observation so Mini-SWE can select Bash on its next turn.
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
                    outputs.append(session.suppress(action,
                        {
                            "output": output,
                            "returncode": 2,
                            "exception_info": "repository-wide query fanout refused",
                            "extra": {"gt_typed_action": True},
                        }, reason="query_fanout_refused"
                    ))
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
                    outputs.append(session.suppress(action, {
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
                    }, reason="capability_disabled"))
                    continue
                if (
                    typed_kind in _GRAPH_DEPENDENT_TYPED_KINDS
                    and
                    adapter.graph_db
                    and not adapter.graph_fresh
                    and session.capability_active("graph_refresh")
                    and session.capability_active("graph_queries")
                ):
                    adapter.refresh_graph(phase="graph_query")
                graph_snapshot = adapter.graph_query_snapshot()
                request, result = session.execute(action, partial(execute_typed_action_fail_open,
                    action,
                    repo_root=adapter.repo_root or os.getcwd(),
                    configuration={
                        "graph_db": (
                            graph_snapshot.graph_path
                            if graph_snapshot.graph_current
                            and session.capability_active("graph_queries")
                            else ""
                        ),
                        "graph_fresh": graph_snapshot.graph_current,
                        "repository_revision": graph_snapshot.source_revision,
                        "gt_mode": session.mode.value,
                    },
                ))
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
            session.observe_select_catalog_action(_command(action))
            command = _command(action)
            if not command:
                # Stock Mini-SWE still delegates a malformed/empty action to
                # the environment. GT must observe less, not invent a result
                # or consume an action the baseline would have executed.
                outputs.append(session.execute(action, partial(environment.execute, action)))
                continue
            is_submit = False
            if not session.disabled:
                try:
                    is_submit = is_submit_command(command)
                except Exception as exc:  # noqa: BLE001 - detection is fail-open
                    session.degrade("submit_detection", exc)
            # Command-level fast path: the marker is literally in the command.
            if is_submit and not submit_allowed(pre_execution=True):
                outputs.append(session.suppress(action, dict(_NOT_EXECUTED), reason="submit_refused"))
                directives.append(_refusal_directive(adapter))
                continue
            preimage = None
            pre_snapshot = None
            if not session.disabled:
                try:
                    adapter.before_action("bash", command)
                    preimage = _capture_edit_preimage(adapter, command)
                    if (
                        adapter.repo_root
                        and session.capability_active("snapshot_authority")
                    ):
                        pre_snapshot = capture_workspace(
                            adapter.repo_root,
                            excluded_roots=_state_exclusions(adapter),
                        )
                        adapter.record_repository_snapshot(
                            pre_snapshot, boundary="before_action"
                        )
                        _refresh_native_graph(adapter, session)
                except Exception as exc:  # noqa: BLE001 - observation is fail-open
                    session.degrade("before_action", exc)
            pending_submission = None
            try:
                result = session.execute(action, partial(environment.execute, action))
            except Submitted as exc:
                # RESULT-level submit interception: the command's OUTPUT began
                # with the magic string even though the command text did not
                # Preserve the terminal while normal post-action processing
                # captures its workspace effects. A pre-execution suppression
                # receipt cannot authorize refusal after this command ran.
                pending_submission = exc
                result = getattr(exc, "gt_execution_result", None)
                if not isinstance(result, dict):
                    # Third-party environments may discard the raw result when
                    # raising Submitted. Preserve native termination and mark
                    # incomplete observation instead of inventing a result.
                    session.degrade("submitted_result_missing", RuntimeError("original result unavailable"))
                    raise
            outputs.append(result)
            if session.disabled:
                if pending_submission is not None:
                    raise pending_submission
                continue
            output = _observation_output(result)
            returncode = _returncode(result)
            output_artifact = (result.get("extra") or {}).get("output_artifact")
            timed_out = bool((result.get("extra") or {}).get("timed_out"))
            # A shell may exit zero before a child holding stdout times out.
            # Preserve its actual exit code in the artifact, never certify the
            # interrupted workload from that aggregate zero.
            semantic_returncode = None if timed_out else returncode
            try:
                changed_files, edit_before_after = _capture_edit_after(adapter, preimage)
                created_files: tuple[str, ...] = ()
                if pre_snapshot is not None:
                    post_snapshot = capture_workspace(
                        adapter.repo_root,
                        excluded_roots=_state_exclusions(adapter),
                    )
                    pre_graph_snapshot = adapter.graph_query_snapshot()
                    transaction = diff_workspace(
                        pre_snapshot,
                        post_snapshot,
                        action_id=adapter.global_action,
                        command=command,
                    )
                    adapter.record_repository_snapshot(
                        post_snapshot, boundary="after_action"
                    )
                    if transaction.complete:
                        # An empty authoritative diff is evidence of no edit.
                        # Do not retain shell-intent guesses from outside root.
                        changed_files = transaction.changed_paths
                        edit_before_after = {}
                    if transaction.changes:
                        transaction_artifacts = compile_transaction_artifacts(
                            transaction,
                            graph_db=(
                                pre_graph_snapshot.graph_path
                                if pre_graph_snapshot.graph_current
                                else None
                            ),
                        )
                        adapter.record_edit_transaction(transaction)
                        adapter.record_transaction_artifacts(transaction_artifacts)
                        adapter.prepare_verification_candidate(
                            transaction, pre_graph_snapshot
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
                _refresh_native_graph(adapter, session)
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
                    returncode=semantic_returncode,
                    action_index=action_index,
                )
                execution_candidates = []
                execution = compile_execution_evidence(
                    command=command,
                    output=output,
                    returncode=returncode,
                    action_id=adapter.global_action,
                    repository_revision=adapter.repository_revision,
                    output_artifact=output_artifact,
                    timed_out=timed_out,
                    environment_sha256=str((result.get("extra") or {}).get("environment_sha256") or ""),
                    output_artifact_path=(
                        str(Path(result["extra"]["output_artifact"]["root"])
                            / result["extra"]["output_artifact"]["sha256"])
                        if (result.get("extra") or {}).get("output_artifact") else ""
                    ),
                )
                if (
                    execution is not None
                    and session.capability_active("execution_evidence")
                ):
                    structured = adapter.record_execution_evidence(execution)
                    if session.capability_model_visible("execution_evidence"):
                        from .output_evidence import EvidenceStore
                        from .request_history import store_history_evidence

                        execution_digest = hashlib.sha256(execution.canonical_bytes()).hexdigest()
                        execution_reference = store_history_evidence(
                            EvidenceStore(adapter.engine_state.layout.evidence_root),
                            execution.canonical_bytes(), kind="execution_evidence",
                        )
                        execution_candidates.append(GTDecisionCandidate(
                            rendered=structured, kind="execution_evidence",
                            dedup_key=f"execution:{execution_digest}",
                            artifact_sha256=execution_digest,
                            artifact_reference=execution_reference,
                            current_failure=(execution.outcome in {"fail", "env_fail"}
                                             or execution.observed_test_outcome in {"fail", "env_fail"}),
                            action_index=adapter.global_action,
                            unit_id=execution_digest,
                            supersession_key=f"execution:{execution.command_sha256}",
                            source_revision=execution.repository_revision,
                        ))
                if session.capability_model_visible("evidence_delivery"):
                    rendered = _run_evidence(
                        adapter, command, output, semantic_returncode, adapter.global_action,
                        changed_files, edit_before_after, created_files,
                        allow_live_probes=session.allows_live_probes,
                        decision_session=session,
                        additional_candidates=tuple(execution_candidates),
                        output_artifact=output_artifact,
                    )
                else:
                    session.queue_decision_candidates(execution_candidates)
                    rendered = ""
                if rendered and session.model_visible:
                    rendered_by_index[action_index] = rendered
            except Exception as exc:  # noqa: BLE001 - preserve tool observation
                session.degrade("after_action", exc)
                rendered_by_index.clear()
                directives.clear()
            if pending_submission is not None:
                # Observe the policy outcome, but pre-execution authority cannot
                # suppress an already executed action or its native terminal.
                submit_allowed()
                raise pending_submission
        if not session.disabled and session.model_visible:
            for directive in adapter.pending_directives:
                directives.append({"role": "user", "content": directive})
        adapter.pending_directives = []
        formatter = getattr(model, "format_observation_messages", None)
        if session.disabled and native_add_messages is not None:
            agent.add_messages = native_add_messages
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
    if callable(transport):
        model._query = MethodType(query_transport, model)
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
        native_exact_provider_payload=native_exact_provider_payload,
    )
    agent._gt_runtime_hook_handle = handle
    return handle
