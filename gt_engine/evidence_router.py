"""Task-role and graph-relevance admission for model-facing GT evidence."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from gt_engine.task_contract import TaskContract, significant_tokens

_PATH_RE = re.compile(
    r"(?<![\w.-])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9_]+"
)
_CALLER_TYPES = frozenset(
    {"caller_contract", "caller_contract_view", "caller_break", "companion_surface"}
)
_COMPLETE_SCOPE_RE = re.compile(
    r"(?i)\b(?:all|any|entire|whole|throughout|across)\b.{0,48}"
    r"\b(?:repository|repo|files?|information|values?|keys?|secrets?)\b"
    r"|\b(?:not present|none remain|remove all|find and remove all)\b"
)
_NEWFILE_ENTITY_RE = re.compile(
    r"(?i)issue names new entity\b[^\n]*:\s*'([^']*)'"
)
_CONTENT_SIGNAL_RE = re.compile(
    r"(?i)(?:aws[_-]?(?:access|secret)|github|huggingface|hf[_-]|"
    r"tokens?|secrets?|credentials?|api[_ -]?keys?)"
)
_GENERIC_ANCHORS = frozenset(
    {
        "build", "change", "check", "code", "create", "data", "file",
        "files", "implement", "input", "output", "produce", "result",
        "script", "source", "test", "tests", "verify",
    }
)


def _normalized_hash(evidence_type: str, rendered: str) -> str:
    normalized = "\n".join(
        line.strip() for line in (rendered or "").splitlines() if line.strip()
    )
    return hashlib.sha256(
        f"{evidence_type}\0{normalized}".encode("utf-8", "surrogatepass")
    ).hexdigest()


def _paths(text: str) -> set[str]:
    return {p.replace("\\", "/").lower() for p in _PATH_RE.findall(text or "")}


def _requires_complete_scope(contract: TaskContract) -> bool:
    return any(
        _COMPLETE_SCOPE_RE.search(str(item.text or ""))
        for item in contract.obligations
    )


def _identity_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _component_keys(value: str) -> set[str]:
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value or "")
    return {
        part.lower()
        for part in re.split(r"[^A-Za-z0-9]+", parts)
        if len(part) >= 4 and part.lower() not in _GENERIC_ANCHORS
    }


def _contract_subject_keys(contract: TaskContract) -> set[str]:
    keys: set[str] = set()
    for item in contract.obligations:
        for subject in item.subjects:
            normalized = (subject or "").replace("\\", "/").rstrip("/")
            base = normalized.rsplit("/", 1)[-1]
            stem = base.rsplit(".", 1)[0] if "." in base else base
            for value in (base, stem):
                key = _identity_key(value)
                if len(key) >= 4:
                    keys.add(key)
                keys.update(_component_keys(value))
        keys.update(
            token for token in significant_tokens(item.text)
            if token not in _GENERIC_ANCHORS
        )
    return keys


def _localization_keys(rendered: str) -> set[str]:
    keys: set[str] = set()
    for path in _paths(rendered):
        base = path.rsplit("/", 1)[-1]
        stem = base.rsplit(".", 1)[0] if "." in base else base
        keys.update(
            key for value in (base, stem)
            if len(key := _identity_key(value)) >= 4
        )
        keys.update(_component_keys(base))
    for line in (rendered or "").splitlines():
        symbol = line.rsplit(":", 1)[-1].strip()
        key = _identity_key(symbol)
        if len(key) >= 4:
            keys.add(key)
        keys.update(_component_keys(symbol))
    return keys


def _malformed_newfile_entity(rendered: str) -> bool:
    match = _NEWFILE_ENTITY_RE.search(rendered or "")
    if not match:
        return False
    entity = match.group(1).strip()
    return not bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{1,63}", entity))


@dataclass
class EvidenceRouter:
    contract: TaskContract
    graph_files: frozenset[str] = frozenset()
    graph_symbols: frozenset[str] = frozenset()
    graph_revision: str = ""
    role_pack: Any | None = None
    relevant_graph_files: frozenset[str] = frozenset()
    _delivered: set[str] = field(default_factory=set)
    _scope_challenge_candidates: set[str] = field(default_factory=set)
    _scope_challenge_delivered: bool = False

    def admit(
        self,
        evidence_type: str,
        rendered: str,
        *,
        command: str,
        output: str,
        commit: bool = True,
    ) -> tuple[bool, str]:
        fingerprint = _normalized_hash(evidence_type, rendered)
        if fingerprint in self._delivered:
            return False, "semantic_duplicate"

        kind = str(evidence_type or "")
        try:
            from gt_engine.attribution import feature_for_evidence

            canonical = feature_for_evidence(kind) or kind
        except Exception:  # noqa: BLE001 - admission remains fail-closed below
            canonical = kind
        allowed = frozenset(
            str(item)
            for item in (
                getattr(self.role_pack, "allowed_evidence", ()) or ()
            )
        )
        if allowed and canonical not in allowed:
            return False, "role_pack_evidence_mismatch"
        if canonical == "newfile_precedent" and _malformed_newfile_entity(rendered):
            return False, "malformed_newfile_entity"
        if self.contract.role == "content_scan" and kind in _CALLER_TYPES:
            return False, "task_role_mismatch"

        rendered_paths = _paths(rendered)
        observed_paths = _paths(f"{command}\n{output}")
        graph_paths = {p.replace("\\", "/").lower() for p in self.graph_files}
        relevant_graph_paths = {
            p.replace("\\", "/").lower()
            for p in self.relevant_graph_files
        }
        graph_grounding_paths = relevant_graph_paths or graph_paths
        if kind == "localization":
            subject_keys = _contract_subject_keys(self.contract)
            location_keys = _localization_keys(rendered)
            subject_grounded = bool(subject_keys & location_keys)
            content_grounded = bool(_CONTENT_SIGNAL_RE.search(rendered or ""))
            if self.contract.role == "content_scan" and not (
                rendered_paths & observed_paths
            ):
                graph_grounded = bool(rendered_paths & graph_grounding_paths)
                if (
                    graph_grounded
                    and subject_keys
                    and not subject_grounded
                    and not content_grounded
                ):
                    return False, "localization_subject_mismatch"
                if (
                    graph_grounded
                    and _requires_complete_scope(self.contract)
                    and not self._scope_challenge_delivered
                ):
                    # A narrowed search is an observation, not the boundary of
                    # repository truth.  One graph-grounded candidate may
                    # challenge incomplete scope; the normal dose arbiter still
                    # decides whether it ships.
                    self._scope_challenge_candidates.add(fingerprint)
                    if commit:
                        self._scope_challenge_delivered = True
                    reason = "graph_scope_challenge"
                elif self._scope_challenge_delivered and graph_grounded:
                    return False, "scope_challenge_already_delivered"
                else:
                    return False, "not_grounded_in_content_search"
            else:
                reason = "admitted"
                if (
                    subject_keys
                    and not subject_grounded
                    and not content_grounded
                    and not (rendered_paths & observed_paths)
                ):
                    return False, "localization_subject_mismatch"
            if graph_grounding_paths and rendered_paths and not (
                rendered_paths & (graph_grounding_paths | observed_paths)
            ):
                return False, "graph_unrelated"
        else:
            reason = "admitted"

        if commit:
            self._delivered.add(fingerprint)
        return True, reason

    def commit(self, evidence_type: str, rendered: str) -> None:
        fingerprint = _normalized_hash(evidence_type, rendered)
        self._delivered.add(fingerprint)
        if fingerprint in self._scope_challenge_candidates:
            self._scope_challenge_delivered = True

    def carry_delivery_state_from(self, prior: EvidenceRouter | None) -> None:
        """Preserve semantic deduplication across a graph-context refresh."""
        if prior is not None:
            self._delivered.update(prior._delivered)
            self._scope_challenge_delivered = prior._scope_challenge_delivered
