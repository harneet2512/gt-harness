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

from . import PersistentPlan

PLAN_TAG = "GT_PERSISTENT_PLAN"
MAX_BLOCK_CHARS = 8_000


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


def render_plan_block(plan: PersistentPlan, *, limit: int = MAX_BLOCK_CHARS) -> str:
    """The immutable artifact the model reads for the rest of the task."""
    if plan.status == "ABSTAINED" or not plan.rows:
        return ""
    head = [
        f"[{PLAN_TAG}]",
        "Requirement index: " + ", ".join(row.row_id for row in plan.rows),
        "Inspect full rows with `gt-plan show <row-id>`. Revise a design with "
        "`gt-plan revise <row-id> --file <json>`; bind an argv check with "
        "`gt-plan bind-check <row-id> --file <json>`. These requests cannot grant evidence.",
        "Design produced before implementation began, from the change request "
        "and a verified code graph. It is advisory: inspect anything, disagree "
        "with anything, and follow your own evidence. It is not a boundary.",
    ]
    if plan.understanding:
        head.extend(["", "DESIGN INTENT:", f"  {plan.understanding}"])
    head.extend(
        ["", "REQUIREMENTS - each needs acceptance evidence before this is done:"]
    )
    body: list[str] = []
    ordered = list(plan.edit_order) or [row.row_id for row in plan.rows]
    rendered: set[str] = set()
    for row_id in ordered:
        row = plan.row(row_id)
        if row is None or row_id in rendered:
            continue
        rendered.add(row_id)
        body.append(f"  {row.row_id}: {row.text}")
        if row.approach:
            body.append(f"      design: {row.approach}")
        if row.anchors:
            anchors = _anchor_labels(plan, row.anchors)
            if anchors:
                body.append(f"      touches: {anchors}")
        if row.verification_command:
            body.append(f"      acceptance: {row.verification_command}")
        elif row.verification_kind:
            body.append(f"      acceptance: {row.verification_kind} (no command given)")
    for row in plan.rows:
        if row.row_id in rendered:
            continue
        rendered.add(row.row_id)
        origin = f" [from {row.derived_from} under {row.mode_symbol}.{row.mode_member}]" if row.is_derived else ""
        body.append(f"  {row.row_id}: {row.text}{origin}")
        if row.approach:
            body.append(f"      design: {row.approach}")
        if row.verification_command:
            body.append(f"      acceptance: {row.verification_command}")

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
    if dropped:
        kept.append(f"  ... {dropped} more plan lines omitted for length")
    return "\n".join([*head, *kept])


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
