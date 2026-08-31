"""Task-role and graph-relevance admission for model-facing GT evidence."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from gt_engine.task_contract import TaskContract, significant_tokens


class EligibilityReceiptError(ValueError):
    """The eligibility receipt could not be sealed without losing evidence."""


def _logical_request_bytes(payload: Any) -> bytes:
    """Return transport-independent request content, excluding credentials/headers."""
    excluded = {
        "headers", "header", "credentials", "credential", "auth",
        "authorization", "api_key", "token",
    }

    def leaves(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [item for child in value for item in leaves(child)]
        if isinstance(value, dict):
            return [
                item
                for key, child in value.items()
                if str(key).lower() not in excluded
                for item in leaves(child)
            ]
        return []

    return "".join(leaves(payload)).encode("utf-8", "surrogatepass")


def _receipt_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def build_eligibility_receipt(
    *,
    decision_id: str,
    iteration_id: str,
    claims: list[dict[str, Any]],
    baseline_request: Any,
    final_request: Any,
    framing_encoding_bytes: int = 0,
    prior_event_digest: str | None = None,
) -> dict[str, Any]:
    """Seal an eligibility decision at the complete logical request boundary."""
    if not isinstance(decision_id, str) or not decision_id.strip():
        raise EligibilityReceiptError("decision_identity")
    if not isinstance(iteration_id, str) or not iteration_id.strip():
        raise EligibilityReceiptError("iteration_identity")
    if not isinstance(claims, list):
        raise EligibilityReceiptError("claims_invalid")
    normalised: list[dict[str, Any]] = []
    seen: set[str] = set()
    for claim in claims:
        if not isinstance(claim, dict):
            raise EligibilityReceiptError("claims_invalid")
        claim_id = str(claim.get("claim_id") or "").strip()
        source = str(claim.get("source") or "").strip()
        content = claim.get("content")
        disposition = str(claim.get("disposition") or "").strip().lower()
        reason = str(claim.get("reason") or "").strip()
        if not claim_id or claim_id in seen or not source or not isinstance(content, str):
            raise EligibilityReceiptError("claim_identity")
        if disposition not in {"admitted", "refused"} or not reason:
            raise EligibilityReceiptError("claim_disposition")
        seen.add(claim_id)
        content_bytes = content.encode("utf-8", "surrogatepass")
        normalised.append({
            "claim_id": claim_id,
            "source": source,
            "content_sha256": hashlib.sha256(content_bytes).hexdigest(),
            "byte_count": len(content_bytes),
            "disposition": disposition,
            "reason": reason,
        })
    normalised.sort(key=lambda item: item["claim_id"])
    try:
        framing = int(framing_encoding_bytes)
    except (TypeError, ValueError):
        raise EligibilityReceiptError("framing_encoding_invalid") from None
    if framing < 0:
        raise EligibilityReceiptError("framing_encoding_invalid")
    baseline_bytes = _logical_request_bytes(baseline_request)
    final_bytes = _logical_request_bytes(final_request)
    admitted = sum(
        item["byte_count"] for item in normalised if item["disposition"] == "admitted"
    )
    refused = sum(
        item["byte_count"] for item in normalised if item["disposition"] == "refused"
    )
    refused_contents = [
        claim["content"].encode("utf-8", "surrogatepass")
        for claim in claims
        if str(claim.get("disposition") or "").lower() == "refused"
        and isinstance(claim.get("content"), str)
    ]
    if any(content and content in final_bytes for content in refused_contents):
        raise EligibilityReceiptError("refused_content_in_final")
    provider_delta = len(final_bytes) - len(baseline_bytes)
    if provider_delta < 0 or provider_delta != admitted + framing:
        raise EligibilityReceiptError("provider_delta_conservation")
    receipt: dict[str, Any] = {
        "schema": "gt.eligibility_receipt.v1",
        "status": "SEALED",
        "degraded": False,
        "unverified": False,
        "decision_id": decision_id.strip(),
        "iteration_id": iteration_id.strip(),
        "claims": normalised,
        "baseline_logical_request_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
        "baseline_logical_request_bytes": len(baseline_bytes),
        "final_logical_request_sha256": hashlib.sha256(final_bytes).hexdigest(),
        "final_logical_request_bytes": len(final_bytes),
        "admitted_bytes": admitted,
        "refused_bytes": refused,
        "framing_encoding_bytes": framing,
        "provider_delta_bytes": provider_delta,
        "refused_bytes_in_final": 0,
        "prior_event_digest": prior_event_digest,
    }
    receipt["receipt_digest_sha256"] = hashlib.sha256(_receipt_bytes(receipt)).hexdigest()
    return receipt


def verify_eligibility_receipt(receipt: dict[str, Any]) -> bool:
    try:
        if (
            receipt.get("schema") != "gt.eligibility_receipt.v1"
            or receipt.get("status") != "SEALED"
        ):
            return False
        supplied = receipt.get("receipt_digest_sha256")
        if not isinstance(supplied, str):
            return False
        body = dict(receipt)
        body.pop("receipt_digest_sha256", None)
        return hashlib.sha256(_receipt_bytes(body)).hexdigest() == supplied
    except (TypeError, ValueError):
        return False


def reconcile_provider_bytes(receipt: dict[str, Any], provider_request: Any) -> dict[str, Any]:
    """Reconcile the sealed logical request against the actual provider view."""
    if not verify_eligibility_receipt(receipt):
        raise EligibilityReceiptError("receipt_unverified")
    logical = _logical_request_bytes(provider_request)
    digest = hashlib.sha256(logical).hexdigest()
    return {
        "receipt_digest_sha256": receipt["receipt_digest_sha256"],
        "provider_logical_request_sha256": digest,
        "provider_logical_request_bytes": len(logical),
        "provider_final_matches": digest == receipt.get("final_logical_request_sha256")
        and len(logical) == receipt.get("final_logical_request_bytes"),
    }

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
    last_eligibility_receipt: dict[str, Any] = field(default_factory=dict, init=False)

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

    def seal_eligibility_receipt(
        self,
        *,
        decision_id: str,
        iteration_id: str,
        claims: list[dict[str, Any]],
        baseline_request: Any,
        final_request: Any,
        framing_encoding_bytes: int = 0,
        prior_event_digest: str | None = None,
        provider_exception: str | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        """Seal the model-bound decision, degrading to native bytes on failure."""
        try:
            receipt = build_eligibility_receipt(
                decision_id=decision_id,
                iteration_id=iteration_id,
                claims=claims,
                baseline_request=baseline_request,
                final_request=final_request,
                framing_encoding_bytes=framing_encoding_bytes,
                prior_event_digest=prior_event_digest,
            )
            if provider_exception:
                receipt["provider_exception"] = provider_exception
                receipt["receipt_digest_sha256"] = hashlib.sha256(
                    _receipt_bytes(receipt)
                ).hexdigest()
            return final_request, receipt
        except EligibilityReceiptError as exc:
            # No sealed benefit may survive a failed receipt: transport only the
            # untouched native baseline and mark the run degraded/unverified.
            degraded = {
                "schema": "gt.eligibility_receipt.v1",
                "status": "DEGRADED",
                "degraded": True,
                "unverified": True,
                "decision_id": decision_id,
                "iteration_id": iteration_id,
                "failure_reason": str(exc),
                "provider_exception": provider_exception,
                "native_baseline_only": True,
                "provider_calls": 0,
                "benchmark_runs": 0,
            }
            degraded["receipt_digest_sha256"] = hashlib.sha256(_receipt_bytes(degraded)).hexdigest()
            return baseline_request, degraded

    def admit_decision(
        self,
        *,
        decision_id: str,
        iteration_id: str,
        candidates: list[dict[str, Any]],
        baseline_request: Any,
        final_request: Any,
        prior_event_digest: str | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        """Model-facing admission boundary that always seals, including empty evidence."""
        claims: list[dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            evidence_type = str(candidate.get("evidence_type") or "")
            rendered = str(candidate.get("rendered") or "")
            keep, reason = self.admit(
                evidence_type,
                rendered,
                command=str(candidate.get("command") or ""),
                output=str(candidate.get("output") or ""),
                commit=False,
            )
            claims.append(
                {
                    "claim_id": str(candidate.get("claim_id") or f"claim-{index}"),
                    "source": evidence_type or "gt",
                    "content": rendered,
                    "disposition": "admitted" if keep else "refused",
                    "reason": reason,
                }
            )
        transported, receipt = self.seal_eligibility_receipt(
            decision_id=decision_id,
            iteration_id=iteration_id,
            claims=claims,
            baseline_request=baseline_request,
            final_request=final_request,
            prior_event_digest=prior_event_digest,
        )
        self.last_eligibility_receipt = receipt
        return transported, receipt

    def carry_delivery_state_from(self, prior: EvidenceRouter | None) -> None:
        """Preserve semantic deduplication across a graph-context refresh."""
        if prior is not None:
            self._delivered.update(prior._delivered)
            self._scope_challenge_delivered = prior._scope_challenge_delivered
