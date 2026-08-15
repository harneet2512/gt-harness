"""Deterministic lifecycle capability packs selected from task roles."""
from __future__ import annotations

from dataclasses import dataclass

from gt_engine.task_contract import TaskContract


@dataclass(frozen=True)
class CapabilityPack:
    pack_id: str
    version: str
    lifecycle: tuple[str, ...]
    predicate_kinds: tuple[str, ...]
    allowed_evidence: tuple[str, ...]


_PACKS = {
    "code_behavior": CapabilityPack(
        "code_build",
        "1",
        ("task_start", "research", "pre_edit", "post_edit", "test", "verify", "submit"),
        ("behavior", "artifact"),
        ("obligations", "localization", "caller_contract", "def_partition",
         "newfile_precedent", "signature_delta", "syntax_result",
         "covering_red", "recovery", "submit_refusal"),
    ),
    "data_transform": CapabilityPack(
        "data_transform",
        "1",
        ("task_start", "research", "pre_edit", "post_edit", "test", "verify", "submit"),
        ("behavior", "artifact", "numeric_threshold"),
        ("obligations", "localization", "caller_contract", "def_partition",
         "newfile_precedent", "signature_delta", "syntax_result",
         "covering_red", "recovery", "submit_refusal"),
    ),
    "content_scan": CapabilityPack(
        "repository_content",
        "1",
        ("task_start", "research", "pre_edit", "post_edit", "verify", "submit"),
        ("content_scope", "artifact"),
        ("obligations", "localization", "syntax_result", "recovery",
         "submit_refusal"),
    ),
}


def select_role_pack(contract: TaskContract) -> CapabilityPack:
    return _PACKS.get(contract.role, _PACKS["code_behavior"])
