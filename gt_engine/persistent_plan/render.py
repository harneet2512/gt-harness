"""Rendering: one immutable block for the prefix, short lines for the tail.

The plan block is written once, before the first main call, into the durable
task message -- measured across a live run that message was byte-identical in
182 of 184 requests, so it is the one place a large artifact can sit without
being re-sent.

There is deliberately NO second renderer for progress. Because the plan's rows
are merged into the task contract, their status already rides the existing
``[GT_OBLIGATION_DELTA]`` at the tail, which is bounded, deduplicated and
counted by the delivery census. A parallel progress channel would be a second
way to say the same thing, and this codebase has already paid for dead parallel
paths more than once.
"""
from __future__ import annotations

import hashlib

from . import PersistentPlan

PLAN_TAG = "GT_PERSISTENT_PLAN"
# The block sits in the durable task message, which is byte-identical across
# requests and rides the provider prefix cache — the one surface where full
# content is free. An 8K cap used to truncate every requirement body below
# the index, turning the plan into a pointer to `gt-plan show` that cost the
# agent a turn per row (run 34656860834 fetched rows at cmd 113, lost them to
# history elision, and re-fetched at cmd 526).
MAX_BLOCK_CHARS = 48_000


def _truncate(lines: list[str], limit: int) -> tuple[list[str], int]:
    """Keep whole lines up to a byte-ish budget; report how many were dropped."""
    kept: list[str] = []
    used = 0
    for index, line in enumerate(lines):
        cost = len(line) + 1
        if used + cost > limit:
            return kept, len(lines) - index
        kept.append(line)
        used += cost
    return kept, 0


def render_plan_block(plan: PersistentPlan, *, limit: int = MAX_BLOCK_CHARS,
                      receipt: dict | None = None) -> str:
    """The immutable artifact the model reads for the rest of the task."""
    if plan.status == "ABSTAINED" or not plan.rows:
        if receipt is not None:
            receipt.clear()
        return ""
    head = [
        f"[{PLAN_TAG}]",
        "Requirement index: " + ", ".join(row.row_id for row in plan.rows),
        "Each requirement's design and acceptance check are inlined below. "
        "`gt-plan show <row-id>` shows a row's full record and "
        "`gt-plan show --source` the retained source/examples; revise "
        "a design with `gt-plan revise <row-id> --file <json>` or bind an argv "
        "check with `gt-plan bind-check <row-id> --file <json>`. "
        "These requests cannot grant evidence.",
        "Design produced before implementation began, from the change request "
        "and a verified code graph. It is advisory: inspect anything, disagree "
        "with anything, and follow your own evidence. It is not a boundary.",
        f"Context captured at source revision {plan.inputs.source_revision or 'unrecorded'}; "
        f"graph revision {plan.inputs.graph_revision or 'unrecorded'}. "
        "Anchors describe that capture, not proof of the current workspace after edits or restart.",
    ]
    if not plan.inputs.anchors_are_current:
        # A restart re-indexes the workspace, and the revision it finds is the
        # one the agent is editing. When it differs from the captured one, every
        # file path, line number and signature below describes a tree that is
        # no longer there. Saying so is the difference between a stale map and
        # a map presented as current.
        head.append(
            f"STALE ANCHORS: the workspace is now at {plan.inputs.observed_source_revision}, "
            f"not the {plan.inputs.source_revision} this design was built from. Line numbers, "
            "file paths and signatures below were not re-validated; confirm each before relying on it."
        )
    if plan.understanding:
        head.extend(["", "DESIGN INTENT:", f"  {plan.understanding}"])
    head.extend(
        ["", "REQUIREMENTS - each needs acceptance evidence before this is done:"]
    )
    body: list[str] = []
    row_ranges: dict[str, tuple[int, int]] = {}
    ordered = list(plan.edit_order) or [row.row_id for row in plan.rows]
    rendered: set[str] = set()
    for row_id in ordered:
        row = plan.row(row_id)
        if row is None or row_id in rendered:
            continue
        rendered.add(row_id)
        start = len(body)
        body.append(f"  {row.row_id}: {row.text}")
        if row.approach:
            body.append(f"      design: {row.approach}")
        anchors = _anchor_labels(plan, row.anchors) if row.anchors else ""
        if anchors:
            body.append(f"      touches: {anchors}")
        elif row.symbol_basis == "unmapped":
            body.append(
                "      touches: no existing symbol resolved for this "
                "requirement; treat it as new code"
            )
        acceptance = _acceptance_line(row)
        if acceptance:
            body.append(acceptance)
        row_ranges[row_id] = (start, len(body))
        pending = plan.pending_interactions(row_id)
        if pending:
            body.append("      interaction assessment pending: " + ", ".join(f"{symbol}.{member}" for symbol, member in pending))
            row_ranges[row_id] = (start, len(body))
    for row in plan.rows:
        if row.row_id in rendered:
            continue
        rendered.add(row.row_id)
        start = len(body)
        origin = f" [from {row.derived_from} under {row.mode_symbol}.{row.mode_member}]" if row.is_derived else ""
        body.append(f"  {row.row_id}: {row.text}{origin}")
        if row.approach:
            body.append(f"      design: {row.approach}")
        acceptance = _acceptance_line(row)
        if acceptance:
            body.append(acceptance)
        row_ranges[row.row_id] = (start, len(body))
        pending = plan.pending_interactions(row.row_id)
        if pending:
            body.append("      interaction assessment pending: " + ", ".join(f"{symbol}.{member}" for symbol, member in pending))
            row_ranges[row.row_id] = (start, len(body))

    applying = plan.applicable_cells
    if applying:
        body.append("")
        body.append(
            "CONFIGURATION INTERACTIONS that apply - modes this change must still "
            "be correct under:"
        )
        for cell in applying[:24]:
            reason = f" ({cell.reason})" if cell.reason else ""
            body.append(
                f"  {cell.row_id} under {cell.mode_symbol}.{cell.member}{reason}"
            )

    blast = _blast_radius_lines(plan)
    if blast:
        body.append("")
        body.append("IMPACT - callers reached by changing the definitions above:")
        body.extend(blast)

    baseline = plan.inputs.baseline
    body.append("")
    if baseline.captured:
        body.append(
            f"REGRESSION BASELINE before any change: {baseline.passed} passing, "
            f"{baseline.failed} failing via `{' '.join(baseline.command)}`. "
            "Every test passing now must still pass at the end."
        )
        if baseline.failing_names:
            body.append(
                "  already failing on arrival (not yours): "
                + ", ".join(baseline.failing_names[:6])
            )
    else:
        body.append(
            f"REGRESSION BASELINE: not captured ({baseline.status}). No regression "
            "check is available, so be conservative with existing behaviour."
        )

    if plan.abstentions:
        body.append("")
        body.append("OPEN ITEMS - this design could not settle these:")
        for target, reason in plan.abstentions[:12]:
            body.append(f"  {target}: {reason}")

    body.append("")
    body.append(
        "DONE when every requirement above has acceptance evidence and the "
        "regression baseline is intact."
    )

    kept, dropped = _truncate(body, max(0, limit - sum(len(x) + 1 for x in head)))
    kept_count = len(kept)
    if dropped:
        kept.append(f"  ... {dropped} more plan lines omitted for length")
    block = "\n".join([*head, *kept])
    if receipt is not None:
        receipt.clear()
        receipt.update({
            "plan_rendering_layout": "gt.plan_rendering.v1",
            "indexed_row_ids": [row.row_id for row in plan.rows],
            "rendered_requirement_row_ids": [key for key, (start, _) in row_ranges.items() if start < kept_count],
            "complete_row_block_ids": [key for key, (_, end) in row_ranges.items() if end <= kept_count],
            "omitted_requirement_row_ids": [key for key, (start, _) in row_ranges.items() if start >= kept_count],
            "rendered_sha256": hashlib.sha256(block.encode("utf-8")).hexdigest(),
            "plan_rows_basis": "indexed_not_fully_delivered",
        })
    return block


def _acceptance_line(row) -> str:
    """The check, and whether the repository already contains it.

    A command over a file the repository does not have is a test still to be
    written. Printing it the same way as a check that can be run right now
    tells the agent something false in the one place it cannot verify cheaply:
    running it yields a collection error, which looks like neither a red test
    nor a missing file.
    """
    if not row.verification_command:
        return (
            f"      acceptance: {row.verification_kind} (no command given)"
            if row.verification_kind
            else ""
        )
    line = f"      acceptance: {row.verification_command}"
    if row.check_basis == "proposed":
        named = ", ".join(row.check_missing_paths[:3]) or "the file it names"
        return f"{line} - {named} does not exist yet; write it first"
    if row.check_basis == "existing":
        return f"{line} - runs against files already in the repository"
    return line


def _anchor_labels(plan: PersistentPlan, node_ids: tuple[int, ...]) -> str:
    """Name each anchor, and say which ones are only a lexical guess.

    An exact-name anchor is the identifier the prompt wrote resolved in the
    graph. A lexical anchor is a text match against prose that named no symbol,
    and it is routinely wrong -- on a real graph a line about a default value
    matched an unrelated ``default`` in a webhook module. Rendering both the
    same way would make a guess read like a fact, which is the exact failure
    this plan exists to remove.
    """
    lookup = {
        anchor.node_id: anchor
        for anchors in plan.inputs.anchors.anchors.values()
        for anchor in anchors
    }
    parts: list[str] = []
    for node_id in node_ids[:4]:
        anchor = lookup.get(node_id)
        if anchor is None:
            continue
        label = f"{anchor.name} @ {anchor.file_path}:{anchor.start_line}"
        if anchor.basis != "exact_name":
            label += " (name guess, unconfirmed)"
        parts.append(label)
    return ", ".join(parts)


def _blast_radius_lines(plan: PersistentPlan, *, limit: int = 8) -> list[str]:
    lookup = {
        anchor.node_id: anchor
        for anchors in plan.inputs.anchors.anchors.values()
        for anchor in anchors
    }
    planned = {node_id for row in plan.rows for node_id in row.anchors}
    lines: list[str] = []
    for node_id in sorted(planned):
        callers = plan.inputs.anchors.callers.get(node_id, ())
        anchor = lookup.get(node_id)
        if not callers or anchor is None:
            continue
        names = ", ".join(
            f"{caller.name} @ {caller.file_path}" for caller in callers[:5]
        )
        more = len(callers) - 5
        suffix = f" (+{more} more)" if more > 0 else ""
        lines.append(f"  {anchor.name}: {names}{suffix}")
        if len(lines) >= limit:
            break
    return lines
