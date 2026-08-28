"""Independent-oracle scoring for deterministic decision-value fixtures.

Production observations are deliberately treated as untrusted input.  Source
support is recomputed from a separately authored corpus and the repository
bytes named by that corpus; a producer cannot certify itself by emitting a
``source_supported`` boolean.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SCHEMA = "gt.decision_value_corpus.v1"
_REQUIRED_LANGUAGE_GROUPS = {
    "python": frozenset({"python"}),
    "typescript/javascript": frozenset({"typescript", "javascript"}),
    "go": frozenset({"go"}),
    "rust": frozenset({"rust"}),
}


@dataclass(frozen=True, slots=True)
class LabeledFact:
    claim_id: str
    fact: str
    path: str
    start_line: int
    end_line: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class LabeledCase:
    case_id: str
    language: str
    task: str
    repository: Path
    repository_revision: str
    expected_owners: tuple[str, ...]
    facts: tuple[LabeledFact, ...]


@dataclass(frozen=True, slots=True)
class DecisionValueCorpus:
    source_path: Path
    cases: tuple[LabeledCase, ...]


@dataclass(frozen=True, slots=True)
class ScoredDecisionValueObservations:
    certified_fact_checks: tuple[dict[str, Any], ...]
    implementation_owner_cases: tuple[dict[str, Any], ...]


def _sha256_valid(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _required_text(row: Mapping[str, Any], key: str, *, context: str) -> str:
    value = str(row.get(key) or "").strip()
    if not value:
        raise ValueError(f"{context} requires non-empty {key}")
    return value


def _safe_relative_path(value: str, *, context: str) -> Path:
    path = Path(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"{context} path must stay relative to its repository")
    return path


def load_decision_value_corpus(path: str | Path) -> DecisionValueCorpus:
    """Load and structurally validate a separately authored corpus."""

    source_path = Path(path).resolve()
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != _SCHEMA:
        raise ValueError(f"decision-value corpus schema must be {_SCHEMA}")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("decision-value corpus requires non-empty cases")

    cases: list[LabeledCase] = []
    seen_ids: set[str] = set()
    corpus_root = source_path.parent.resolve()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict):
            raise ValueError(f"case {index} must be an object")
        context = f"case {index}"
        case_id = _required_text(raw_case, "case_id", context=context)
        if case_id in seen_ids:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen_ids.add(case_id)
        language = _required_text(raw_case, "language", context=context).casefold()
        task = _required_text(raw_case, "task", context=context)
        repository_relative = _safe_relative_path(
            _required_text(raw_case, "repository", context=context), context=context
        )
        repository = (corpus_root / repository_relative).resolve()
        try:
            repository.relative_to(corpus_root)
        except ValueError as exc:
            raise ValueError(f"{context} repository escapes corpus root") from exc
        if not repository.is_dir():
            raise ValueError(f"{context} repository does not exist: {repository_relative}")
        repository_revision = _required_text(
            raw_case, "repository_revision", context=context
        )
        expected_owners = tuple(
            dict.fromkeys(str(value).strip() for value in raw_case.get("expected_owners") or ())
        )
        if not expected_owners or any(not value for value in expected_owners):
            raise ValueError(f"{context} requires non-empty expected_owners")

        raw_facts = raw_case.get("facts")
        if not isinstance(raw_facts, list) or not raw_facts:
            raise ValueError(f"{context} requires non-empty facts")
        facts: list[LabeledFact] = []
        fact_ids: set[str] = set()
        for fact_index, raw_fact in enumerate(raw_facts):
            if not isinstance(raw_fact, dict):
                raise ValueError(f"{context} fact {fact_index} must be an object")
            fact_context = f"{context} fact {fact_index}"
            claim_id = _required_text(raw_fact, "claim_id", context=fact_context)
            if claim_id in fact_ids:
                raise ValueError(f"{context} has duplicate claim_id: {claim_id}")
            fact_ids.add(claim_id)
            relative = _safe_relative_path(
                _required_text(raw_fact, "path", context=fact_context),
                context=fact_context,
            )
            start_line = int(raw_fact.get("start_line") or 0)
            end_line = int(raw_fact.get("end_line") or 0)
            if start_line < 1 or end_line < start_line:
                raise ValueError(f"{fact_context} has an invalid source range")
            content_sha256 = _required_text(
                raw_fact, "content_sha256", context=fact_context
            ).casefold()
            if not _sha256_valid(content_sha256):
                raise ValueError(f"{fact_context} content_sha256 is invalid")
            facts.append(
                LabeledFact(
                    claim_id=claim_id,
                    fact=_required_text(raw_fact, "fact", context=fact_context),
                    path=relative.as_posix(),
                    start_line=start_line,
                    end_line=end_line,
                    content_sha256=content_sha256,
                )
            )
        cases.append(
            LabeledCase(
                case_id=case_id,
                language=language,
                task=task,
                repository=repository,
                repository_revision=repository_revision,
                expected_owners=expected_owners,
                facts=tuple(facts),
            )
        )

    languages = {case.language for case in cases}
    missing = [
        label
        for label, alternatives in _REQUIRED_LANGUAGE_GROUPS.items()
        if languages.isdisjoint(alternatives)
    ]
    if missing:
        raise ValueError("missing required languages: " + ", ".join(missing))
    return DecisionValueCorpus(source_path=source_path, cases=tuple(cases))


def _score_fact(
    case: LabeledCase,
    expected: LabeledFact,
    observation: Mapping[str, Any] | None,
) -> dict[str, Any]:
    reason = "source_supported"
    observed_fact: Mapping[str, Any] | None = None
    if observation is None:
        reason = "missing_observation"
    elif str(observation.get("repository_revision") or "") != case.repository_revision:
        reason = "repository_revision_mismatch"
    else:
        for candidate in observation.get("certified_facts") or ():
            if (
                isinstance(candidate, Mapping)
                and str(candidate.get("claim_id") or "") == expected.claim_id
            ):
                observed_fact = candidate
                break
        if observed_fact is None:
            reason = "missing_certified_fact"
        elif str(observed_fact.get("fact") or "") != expected.fact:
            reason = "fact_mismatch"
        else:
            evidence = observed_fact.get("source_evidence") or ()
            exact_evidence = any(
                isinstance(item, Mapping)
                and str(item.get("path") or "").replace("\\", "/") == expected.path
                and int(item.get("start_line") or 0) == expected.start_line
                and int(item.get("end_line") or 0) == expected.end_line
                and str(item.get("content_sha256") or "").casefold()
                == expected.content_sha256
                for item in evidence
            )
            if not exact_evidence:
                reason = "evidence_mismatch"

    source = case.repository / Path(expected.path)
    if reason == "source_supported":
        if not source.is_file():
            reason = "repository_file_missing"
        else:
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != expected.content_sha256:
                reason = "repository_hash_mismatch"
            elif expected.end_line > len(content.decode("utf-8", "replace").splitlines()):
                reason = "source_range_invalid"
    return {
        "case_id": case.case_id,
        "claim_id": expected.claim_id,
        "source_supported": reason == "source_supported",
        "reason": reason,
    }


def score_decision_value_observations(
    corpus: DecisionValueCorpus,
    observations: Iterable[Mapping[str, Any]],
) -> ScoredDecisionValueObservations:
    """Score untrusted production rows against the independent corpus."""

    by_case: dict[str, Mapping[str, Any]] = {}
    known_ids = {case.case_id for case in corpus.cases}
    for observation in observations:
        case_id = str(observation.get("case_id") or "")
        if case_id not in known_ids:
            raise ValueError(f"observation has unknown case_id: {case_id or '<empty>'}")
        if case_id in by_case:
            raise ValueError(f"duplicate observation for case_id: {case_id}")
        by_case[case_id] = observation

    fact_checks: list[dict[str, Any]] = []
    owner_cases: list[dict[str, Any]] = []
    for case in corpus.cases:
        observation = by_case.get(case.case_id)
        revision_matches = bool(
            observation is not None
            and str(observation.get("repository_revision") or "")
            == case.repository_revision
        )
        ranked = (
            [str(value) for value in observation.get("ranked_owners") or ()]
            if revision_matches and observation is not None
            else []
        )
        for expected_owner in case.expected_owners:
            owner_cases.append(
                {
                    "case_id": case.case_id,
                    "language": case.language,
                    "expected": expected_owner,
                    "ranked": ranked,
                }
            )
        fact_checks.extend(
            _score_fact(case, expected, observation) for expected in case.facts
        )
    return ScoredDecisionValueObservations(
        certified_fact_checks=tuple(fact_checks),
        implementation_owner_cases=tuple(owner_cases),
    )


__all__ = [
    "DecisionValueCorpus",
    "LabeledCase",
    "LabeledFact",
    "ScoredDecisionValueObservations",
    "load_decision_value_corpus",
    "score_decision_value_observations",
]
