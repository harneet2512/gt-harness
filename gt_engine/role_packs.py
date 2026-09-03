"""Deterministic lifecycle capability packs selected from task roles.

`cochange_prior` is allowed in `code_build` and `data_transform` only. Both are
editing lifecycles -- they carry `pre_edit`/`post_edit` and behavioural
predicate kinds -- and "the file you just touched has a historical companion"
is actionable exactly there. `repository_content` is a completeness sweep over
content: a historical companion neither widens nor closes its scope, and that
pack's allowed set is deliberately the minimum a scan needs, which is why it
already excludes `caller_contract` and every other structural graph fact.
"""
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
        ("obligations", "localization", "caller_contract", "cochange_prior",
         "def_partition", "newfile_precedent", "signature_delta",
         "syntax_result", "covering_red", "recovery", "submit_refusal"),
    ),
    "data_transform": CapabilityPack(
        "data_transform",
        "1",
        ("task_start", "research", "pre_edit", "post_edit", "test", "verify", "submit"),
        ("behavior", "artifact", "numeric_threshold"),
        ("obligations", "localization", "caller_contract", "cochange_prior",
         "def_partition", "newfile_precedent", "signature_delta",
         "syntax_result", "covering_red", "recovery", "submit_refusal"),
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
