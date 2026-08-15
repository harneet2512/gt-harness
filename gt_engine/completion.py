"""Conservative executable completion certificates for the central agent.

This module does not ask a model whether work is complete and does not inspect
hidden grader tests.  It compiles only task-text predicates whose equivalence
can be expressed mechanically.  Any unrecognized obligation keeps the plan
partial and therefore disables automatic submission.
"""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
import shlex
from dataclasses import asdict, dataclass
from enum import StrEnum

from gt_engine.task_contract import TaskContract, TaskResourceRole, extract_task_contract


class CompletionStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class CompletionPredicate:
    predicate_id: str
    kind: str
    command: str
    obligation_ids: tuple[str, ...]
    target_paths: tuple[str, ...]
    dependency_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompletionPlan:
    status: CompletionStatus
    predicates: tuple[CompletionPredicate, ...]
    obligation_ids: tuple[str, ...]
    uncovered_obligation_ids: tuple[str, ...]
    target_paths: tuple[str, ...]

    @property
    def executable(self) -> bool:
        return self.status is CompletionStatus.COMPLETE and bool(self.predicates)

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "executable": self.executable,
            "predicates": [asdict(item) for item in self.predicates],
            "obligation_ids": list(self.obligation_ids),
            "uncovered_obligation_ids": list(self.uncovered_obligation_ids),
            "target_paths": list(self.target_paths),
        }


@dataclass(frozen=True, slots=True)
class PredicateObservation:
    predicate_id: str
    returncode: int
    output: str
    workspace_revision: str

    def as_dict(self) -> dict[str, object]:
        return {
            "predicate_id": self.predicate_id,
            "returncode": self.returncode,
            "output_sha256": hashlib.sha256(
                self.output.encode("utf-8", "surrogatepass")
            ).hexdigest(),
            "workspace_revision": self.workspace_revision,
        }


@dataclass(frozen=True, slots=True)
class CompletionCertificate:
    status: CompletionStatus
    auto_submit_eligible: bool
    workspace_revision: str
    action_id: int
    observations: tuple[PredicateObservation, ...]
    missing_predicate_ids: tuple[str, ...]
    failing_predicate_ids: tuple[str, ...]
    stale_predicate_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "auto_submit_eligible": self.auto_submit_eligible,
            "workspace_revision": self.workspace_revision,
            "action_id": self.action_id,
            "observations": [item.as_dict() for item in self.observations],
            "missing_predicate_ids": list(self.missing_predicate_ids),
            "failing_predicate_ids": list(self.failing_predicate_ids),
            "stale_predicate_ids": list(self.stale_predicate_ids),
            "reason_codes": list(self.reason_codes),
        }


_SAFE_PATH_RE = re.compile(r"^/?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
_EXACT_PIPE_RE = re.compile(
    r"(?i)\bcat\s+(?P<input>/?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)"
    r"\s*\|\s*(?P<decoder>/?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)"
    r"\s+gives\s+exactly\s+(?P<target>/?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)"
)
_SIZE_RE = re.compile(
    r"(?i)(?P<path>/?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)"
    r"[^.\n]{0,100}?\bat\s+most\s+(?P<limit>\d+)\s+bytes\b"
)


def _absolute(path: str, cwd: str) -> str | None:
    if not _SAFE_PATH_RE.fullmatch(path or ""):
        return None
    candidate = posixpath.normpath(path if path.startswith("/") else posixpath.join(cwd, path))
    root = posixpath.normpath(cwd)
    if candidate != root and not candidate.startswith(root.rstrip("/") + "/"):
        return None
    return candidate


def _predicate_id(kind: str, command: str, obligations: tuple[str, ...]) -> str:
    material = json.dumps(
        [kind, command, obligations], separators=(",", ":"), sort_keys=False
    )
    return "completion-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _compile_exact_decompression(
    contract: TaskContract, cwd: str
) -> tuple[CompletionPredicate, set[str]] | None:
    for obligation in contract.obligations:
        match = _EXACT_PIPE_RE.search(obligation.text)
        if match is None:
            continue
        source = _absolute(match.group("input"), cwd)
        decoder = _absolute(match.group("decoder"), cwd)
        target = _absolute(match.group("target").rstrip(".,;:"), cwd)
        if not source or not decoder or not target:
            return None
        qsource, qdecoder, qtarget = map(shlex.quote, (source, decoder, target))
        command = (
            "tmp=$(mktemp) && trap 'rm -f \"$tmp\"' EXIT && "
            f"test -f {qsource} && test -x {qdecoder} && "
            f"{qdecoder} < {qsource} > \"$tmp\" && cmp -s \"$tmp\" {qtarget}"
        )
        obligations = (obligation.obligation_id,)
        predicate = CompletionPredicate(
            predicate_id=_predicate_id("exact_decompression", command, obligations),
            kind="exact_decompression",
            command=command,
            obligation_ids=obligations,
            target_paths=(source,),
            dependency_paths=(source, decoder, target),
        )
        return predicate, set(obligations)
    return None


def _compile_artifact_size(
    contract: TaskContract, cwd: str
) -> tuple[CompletionPredicate, set[str]] | None:
    for obligation in contract.obligations:
        match = _SIZE_RE.search(obligation.text)
        if match is None:
            continue
        path = _absolute(match.group("path"), cwd)
        if not path:
            return None
        limit = int(match.group("limit"))
        if limit < 0:
            return None
        qpath = shlex.quote(path)
        command = f'test -f {qpath} && test "$(wc -c < {qpath})" -le {limit}'
        obligations = (obligation.obligation_id,)
        predicate = CompletionPredicate(
            predicate_id=_predicate_id("artifact_size", command, obligations),
            kind="artifact_size",
            command=command,
            obligation_ids=obligations,
            target_paths=(path,),
            dependency_paths=(path,),
        )
        return predicate, set(obligations)
    return None


def _compile_output_existence(
    contract: TaskContract,
    cwd: str,
    *,
    skip_paths: set[str],
) -> tuple[CompletionPredicate, ...]:
    """Compile progress probes for confirmed outputs without claiming coverage."""

    compiled: list[CompletionPredicate] = []
    for resource in contract.resources:
        if resource.role is not TaskResourceRole.OUTPUT or resource.confidence < 0.8:
            continue
        path = _absolute(resource.path, cwd)
        if not path or path in skip_paths:
            continue
        command = f"test -s {shlex.quote(path)}"
        compiled.append(
            CompletionPredicate(
                predicate_id=_predicate_id("required_output_exists", command, ()),
                kind="required_output_exists",
                command=command,
                obligation_ids=(),
                target_paths=(path,),
                dependency_paths=(path,),
            )
        )
    return tuple(compiled)


def compile_completion_plan(instruction: str, *, cwd: str = "/app") -> CompletionPlan:
    """Compile all mechanically equivalent predicates recognized in the task.

    Completeness is all-or-nothing: one uncovered normative obligation makes
    the plan PARTIAL.  That is the fail-open boundary that prevents a narrow
    certificate from terminating a task with requirements it never checked.
    """

    contract = extract_task_contract(instruction)
    compiled: list[CompletionPredicate] = []
    covered: set[str] = set()
    for compiler in (_compile_exact_decompression, _compile_artifact_size):
        result = compiler(contract, cwd)
        if result is None:
            continue
        predicate, obligation_ids = result
        compiled.append(predicate)
        covered.update(obligation_ids)
    compiled.extend(
        _compile_output_existence(
            contract,
            cwd,
            skip_paths={path for item in compiled for path in item.target_paths},
        )
    )
    obligation_ids = tuple(item.obligation_id for item in contract.obligations)
    uncovered = tuple(item for item in obligation_ids if item not in covered)
    status = (
        CompletionStatus.COMPLETE
        if compiled and obligation_ids and not uncovered
        else CompletionStatus.PARTIAL
    )
    target_paths = tuple(
        sorted({path for predicate in compiled for path in predicate.target_paths})
    )
    return CompletionPlan(
        status=status,
        predicates=tuple(compiled),
        obligation_ids=obligation_ids,
        uncovered_obligation_ids=uncovered,
        target_paths=target_paths,
    )


def certificate_from_observations(
    plan: CompletionPlan,
    observations: tuple[PredicateObservation, ...],
    *,
    workspace_revision: str,
    action_id: int,
) -> CompletionCertificate:
    """Issue a certificate only from complete, current, passing observations."""

    by_id = {item.predicate_id: item for item in observations}
    expected = tuple(item.predicate_id for item in plan.predicates)
    missing = tuple(item for item in expected if item not in by_id)
    failing = tuple(
        item for item in expected if item in by_id and by_id[item].returncode != 0
    )
    stale = tuple(
        item
        for item in expected
        if item in by_id and by_id[item].workspace_revision != workspace_revision
    )
    reasons: list[str] = []
    if plan.status is not CompletionStatus.COMPLETE:
        reasons.append("completion_plan_partial")
    if missing:
        reasons.append("predicate_observation_missing")
    if failing:
        reasons.append("predicate_failed")
    if stale:
        reasons.append("predicate_workspace_revision_stale")
    eligible = bool(expected) and not reasons
    return CompletionCertificate(
        status=CompletionStatus.COMPLETE if eligible else CompletionStatus.INCOMPLETE,
        auto_submit_eligible=eligible,
        workspace_revision=workspace_revision,
        action_id=action_id,
        observations=observations,
        missing_predicate_ids=missing,
        failing_predicate_ids=failing,
        stale_predicate_ids=stale,
        reason_codes=tuple(reasons) or ("all_executable_predicates_current_and_passing",),
    )
