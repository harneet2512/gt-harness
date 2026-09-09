"""The single planning provider call, and the validation of what comes back.

One call, before the first edit, against a graph that is complete and current.
It is modelled on ``miniswe_runtime.bootstrap_select_catalog``: a real provider
request that creates no Mini-SWE action, counted at the transport so receipt
reconciliation stays exact, and advisory throughout -- a failure degrades the
plan to ABSTAINED and the run proceeds exactly as stock Mini-SWE would.

Validation is the part that keeps the plan factual. The model may only cite ids
this module offered it. An unknown row, a node the graph does not contain, or a
mode member that was never on the menu is DROPPED and recorded as an
abstention, never repaired into something plausible. A plan that quietly invents
an anchor is the confident-and-wrong failure this whole design exists to avoid.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from . import (
    STATUS_PARTIAL,
    STATUS_READY,
    InteractionCell,
    PersistentPlan,
    PlanInputs,
    PlanRow,
)

PLAN_TOOL_NAME = "write_persistent_plan"
MAX_ROWS_OFFERED = 60
MAX_MODES_OFFERED = 16
MAX_CALLERS_SHOWN = 6
# The cartesian sweep is what made this call unaffordable. Measured on the
# first production run: 52 requirements against 24 modes is 1,248 cells to
# hold in mind, and on every task the planning call spent its ENTIRE output
# budget reasoning and returned no tool call at all -- the plan that shipped
# was the deterministic skeleton, with no design, no acceptance criteria and
# no interaction cells. The graph already records which anchor each mode was
# reached from, so the cells that can possibly matter are known without the
# model enumerating them.
# Sized from the measured rule, not guessed: the union of call edges and
# same-file yields 119 cells on the 52-requirement task, and a cap of 40 would
# have silently truncated two thirds of the sweep it exists to carry.
MAX_PAIRS_OFFERED = 140
MAX_DERIVED_ROWS = 24
MAX_COMMAND_CHARS = 300

VERIFICATION_KINDS = ("existing_test", "new_test", "command", "none")

# A verification command must run inside the repository. These name the
# benchmark's own machinery, which the plan may never reach into: reading the
# verifier would invalidate every number the run produces.
_FORBIDDEN_COMMAND_RE = re.compile(
    r"(?i)(?:^|[\s/])(?:/logs|/app/tests/config\.json|test\.patch|solution\.patch|"
    r"solve\.sh|grader\.py|task\.toml|fail_to_pass|pass_to_pass|f2p|p2p)\b"
)
_ABSOLUTE_OR_PARENT_RE = re.compile(r"(?:^|\s)(?:/|[A-Za-z]:[\\/]|\.\.[\\/])")


PLANNING_SYSTEM_PROMPT = (
    "You are performing DESIGN for a change request, before any code is "
    "written. Requirements analysis and current-state analysis are already "
    "complete and are given to you below; you are not being asked to repeat "
    "them.\n"
    "\n"
    "WHAT HAS ALREADY BEEN DONE FOR YOU\n"
    "* Requirements elicitation: every normative line of the change request, "
    "split out verbatim, one numbered requirement each.\n"
    "* Current-state analysis: the definitions each requirement resolves to in "
    "a verified code graph, with file and line.\n"
    "* Impact analysis: the callers of those definitions.\n"
    "* Configuration analysis: the alternate paths those definitions already "
    "take, and the arguments they already require. A required entry is a "
    "demand the host makes, not an option you may set.\n"
    "* Regression baseline: the repository's test result before any change.\n"
    "\n"
    "WHAT YOU OWE BACK\n"
    "1. DESIGN INTENT. Two to five sentences: what this change request means "
    "for this codebase -- which components already exist, what is missing, and "
    "how the missing part must fit the shape of what is there. Not a "
    "restatement of the request.\n"
    "2. DESIGN PER REQUIREMENT. For each requirement, one or two sentences on "
    "the change that satisfies it, naming the definitions it touches. This is "
    "the design; the anchors and the check only locate and prove it.\n"
    "3. ACCEPTANCE CRITERIA. For each requirement, a CONCRETE command that "
    "exercises it the way a caller would, through the real entry point, and "
    "observes the result the request describes. Acceptance is behaviour a "
    "human can verify, not an internal attribute: assert that the behaviour "
    "happens, never that the code exists. It must also be DIFFERENTIAL -- the "
    "criterion has to fail on the repository as it stands and pass once the "
    "change is made. A criterion that already passes today proves nothing, and "
    "it is how a requirement that was never implemented, or was implemented "
    "somewhere the caller never reaches, still looks satisfied. Where the "
    "request names an exact observable -- a value, a message, a status, a "
    "field -- match on that observable. A whole-suite command is acceptance "
    "only where the suite actually covers the requirement. A requirement that "
    "genuinely cannot be verified must carry no_check_reason instead of an "
    "invented command: unverifiable scope has to be visible as unverifiable.\n"
    "4. CONFIGURATION INTERACTIONS. The pairs to decide are listed for you: "
    "each is one requirement crossed with one mode its own definitions already "
    "reach. Decide those pairs only, and do not enumerate the full product -- "
    "the listed pairs are the ones the code can actually reach. REPORT ONLY "
    "THE CELLS THAT APPLY, and give considered_count so the pass stays "
    "auditable. Do not emit a row per non-applying cell: on a real task that "
    "was 73 emitted cells of which none applied, which spent output budget and "
    "said nothing. The applying cells are the requirements no reading of the "
    "request alone would enumerate, and they are the point of this section.\n"
    "4b. CONFLICT PASS. Two requirements, each sensible on its own, may not "
    "both hold at once; so may a new requirement and a rule the existing code "
    "already enforces. For each requirement ask: which demand of the path it "
    "touches could this violate, and what does the system do when it does? "
    "Anything added to an existing component inherits that component's "
    "contract, and that contract is not in the change request -- what it "
    "demands before it will run the new behaviour at all, and where in its "
    "existing sequence that behaviour takes effect. State which demands the "
    "new behaviour satisfies and which it must relax. A requirement can be "
    "fully implemented and still fail because the call was refused or the new "
    "code was never reached.\n"
    "5. DERIVED REQUIREMENTS. Where an interaction applies and needs its own "
    "acceptance, raise it as a derived requirement stating the behaviour under "
    "that specific member.\n"
    "6. TRACEABILITY AND COVERAGE. Cite only ids present in the input; never "
    "invent a node id, a requirement id, a mode symbol or a member name. Every "
    "requirement given to you must appear exactly once, carrying either an "
    "acceptance criterion or a no_check_reason. Coverage is at the clause you "
    "were handed, not at the level of the request as a whole. Anything the "
    "input does not settle belongs in abstentions, which is the open-items "
    "register for this design.\n"
    "7. SCOPE. Do not write implementation code. Do not restate the request or "
    "the context above -- the reader already has both, and a design that "
    "repeats them buries the part only you can supply. Give your recommended "
    "approach, not a menu of alternatives. Where a change repeats across many "
    "places, describe the pattern once rather than enumerating every site. "
    "Specify only what must be true when the work is complete.\n"
    "\n"
    "Record the design by calling the write_persistent_plan tool exactly once. "
    "You have one call and a finite output budget, and a design that is never "
    "written down is worth nothing: reach the tool call. Deliberate briefly, "
    "then write. If the budget is tight, cover every requirement shallowly "
    "rather than a few of them exhaustively -- an unlisted requirement reads "
    "as one nobody has to satisfy.\n"
)


def plan_tool_schema(inputs: PlanInputs) -> dict:
    """The single tool the planning call may invoke."""
    row_ids = [row.row_id for row in inputs.ledger.rows][:MAX_ROWS_OFFERED]
    return {
        "type": "function",
        "function": {
            "name": PLAN_TOOL_NAME,
            "description": (
                "Record the implementation plan. Cite only ids given in the "
                "message; anything else is dropped."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "understanding": {
                        "type": "string",
                        "description": (
                            "Two to five sentences: what this task asks for in "
                            "terms of the code above, what already exists, and "
                            "what is missing."
                        ),
                    },
                    "rows": {
                        "type": "array",
                        "description": "One entry per requirement row you can anchor.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "row_id": {"type": "string", "enum": row_ids},
                                "approach": {
                                    "type": "string",
                                    "description": (
                                        "One or two sentences: what must change "
                                        "for this requirement to hold, naming "
                                        "the definitions it touches."
                                    ),
                                },
                                "anchors": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "description": "node_id values from the input only.",
                                },
                                "verification_kind": {
                                    "type": "string",
                                    "enum": list(VERIFICATION_KINDS),
                                },
                                "verification_command": {
                                    "type": "string",
                                    "description": (
                                        "Repository-relative command that "
                                        "demonstrates this row. For observable "
                                        "behaviour, run it and grep for the "
                                        "exact string the task names."
                                    ),
                                },
                                "no_check_reason": {
                                    "type": "string",
                                    "description": (
                                        "Only when this row genuinely cannot "
                                        "be checked. Never a substitute for "
                                        "thinking of one."
                                    ),
                                },
                            },
                            "required": ["row_id", "approach"],
                        },
                    },
                    "considered_count": {
                        "type": "integer",
                        "description": (
                            "How many requirement x mode cells you swept, so "
                            "the matrix stays auditable without listing them."
                        ),
                    },
                    "interactions": {
                        "type": "array",
                        "description": (
                            "ONLY the cells where applies is true. Non-applying "
                            "cells are counted in considered_count, not listed."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "row_id": {"type": "string"},
                                "mode_symbol": {"type": "string"},
                                "member": {"type": "string"},
                                "applies": {"type": "boolean"},
                                "reason": {"type": "string"},
                            },
                            "required": ["row_id", "mode_symbol", "member", "applies"],
                        },
                    },
                    "derived_rows": {
                        "type": "array",
                        "description": (
                            "Behaviours implied by an applying interaction that "
                            "need their own proof."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "from_row_id": {"type": "string"},
                                "mode_symbol": {"type": "string"},
                                "member": {"type": "string"},
                                "verification_command": {"type": "string"},
                            },
                            "required": ["text", "from_row_id"],
                        },
                    },
                    "edit_order": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "row_id values, the order you would edit in.",
                    },
                    "abstentions": {
                        "type": "array",
                        "description": "Anything the input could not settle.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "row_id": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": ["reason"],
                        },
                    },
                },
                "required": ["understanding", "rows"],
            },
        },
    }


def mode_pairs(inputs: PlanInputs) -> list[tuple[str, Any]]:
    """The requirement-by-mode cells the graph itself connects.

    A mode candidate records the anchors it was reached from, and every anchor
    belongs to a requirement row, so the product that can possibly matter is
    already known. Emitting it beats asking for a full sweep: the sweep is
    quadratic in inputs the engine controls, and paying for it in the model's
    reasoning budget bought nothing on the one run that tried.

    Both rules run, always. Measured on a real 52-requirement task: call edges
    alone yield 27 cells reaching 18 requirements and 10 of 24 modes, while
    adding same-file yields 119 cells reaching 34 requirements and 19 modes --
    still a tenth of the 1,248-cell product. Treating same-file as a fallback
    for modes with no edge recovers none of that, because on that task every
    mode HAS an edge; it just has one to somebody else's anchor. A requirement
    that edits a file is subject to the switches already living in it.
    """
    rows_by_node: dict[int, list[str]] = {}
    rows_by_file: dict[str, list[str]] = {}
    for row_id, anchors in inputs.anchors.anchors.items():
        for anchor in anchors:
            rows_by_node.setdefault(anchor.node_id, []).append(row_id)
            path = str(anchor.file_path or "").replace("\\", "/").lstrip("./")
            if path and row_id not in rows_by_file.setdefault(path, []):
                rows_by_file[path].append(row_id)
    pairs: list[tuple[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for mode in inputs.anchors.modes:
        candidates = [
            row_id
            for node_id in mode.reached_from
            for row_id in rows_by_node.get(node_id, ())
        ]
        # Union, not fallback. A mode reached from one requirement's anchor
        # still governs every other requirement editing the file it lives in,
        # and that second group is the larger one.
        candidates += list(rows_by_file.get(mode.file_path, ()))
        for row_id in candidates:
            key = (row_id, mode.symbol)
            if key in seen:
                continue
            seen.add(key)
            pairs.append((row_id, mode))
    return pairs


def _render_inputs(inputs: PlanInputs) -> str:
    """The planning message: requirements, graph facts, modes, baseline, gaps."""
    lines: list[str] = ["REQUIREMENTS (verbatim, one per line of the task statement)"]
    anchors_by_row = inputs.anchors.anchors
    for row in inputs.ledger.rows[:MAX_ROWS_OFFERED]:
        section = f" [{row.section}]" if row.section else ""
        lines.append(f"{row.row_id}{section} {row.text}")
        for anchor in anchors_by_row.get(row.row_id, ())[:4]:
            signature = anchor.signature.replace("\n", " ")[:160]
            lines.append(
                f"    anchor node_id={anchor.node_id} {anchor.label} {anchor.name} "
                f"@ {anchor.file_path}:{anchor.start_line} ({anchor.basis})"
            )
            if signature:
                lines.append(f"      signature: {signature}")
            callers = inputs.anchors.callers.get(anchor.node_id, ())
            if callers:
                shown = ", ".join(
                    f"{caller.name} @ {caller.file_path}"
                    for caller in callers[:MAX_CALLERS_SHOWN]
                )
                more = len(callers) - MAX_CALLERS_SHOWN
                suffix = f" (+{more} more)" if more > 0 else ""
                lines.append(f"      callers: {shown}{suffix}")
        if not anchors_by_row.get(row.row_id):
            lines.append("    anchor: NONE - the graph resolved nothing for this row")

    pairs = mode_pairs(inputs)
    if pairs:
        lines.append("")
        lines.append(
            "CONFIGURATION PAIRS TO DECIDE. Each line is one requirement crossed "
            "with one mode that the graph says that requirement's own "
            "definitions already reach. An enum_like, config_like or flag_param "
            "member is a path the code already takes; a required_param is an "
            "argument the host already DEMANDS. This list IS the sweep: decide "
            "these pairs, report only the ones where behaviour must differ, and "
            "do not construct pairs that are not listed."
        )
        for row_id, mode in pairs[:MAX_PAIRS_OFFERED]:
            members = ", ".join(mode.members[:12])
            lines.append(
                f"  {row_id} x {mode.symbol} ({mode.kind}) "
                f"@ {mode.file_path}: {members}"
            )
        remaining = len(pairs) - MAX_PAIRS_OFFERED
        if remaining > 0:
            lines.append(f"  ... {remaining} further pairs not listed")

    # Modes that no pair reached are still shown, always. Rendering them only
    # when the pair list was EMPTY hid 14 of 24 modes on a measured task, which
    # left the planner unable to notice an interaction the pairing rule failed
    # to predict. The pairs are what must be decided; this is what may matter.
    paired = {mode.symbol for _row_id, mode in pairs}
    unpaired = [mode for mode in inputs.anchors.modes if mode.symbol not in paired]
    if unpaired:
        lines.append("")
        lines.append(
            "OTHER MODES in the same neighbourhood, which no requirement above "
            "was tied to. Not part of the sweep -- raise one only if you see an "
            "interaction the pairing missed:"
            if pairs
            else "EXISTING MODES near these definitions, tied to no specific "
            "requirement. Treat them as context, not as a sweep:"
        )
        for mode in unpaired[:MAX_MODES_OFFERED]:
            members = ", ".join(mode.members[:12])
            lines.append(
                f"  {mode.symbol} ({mode.kind}) @ {mode.file_path}: {members}"
            )
    elif not pairs:
        lines.append("")
        lines.append("EXISTING MODES: none reachable from these anchors.")

    lines.append("")
    lines.append(f"REPOSITORY TEST BASELINE: {inputs.baseline.summary()}")
    if inputs.baseline.captured and inputs.baseline.failing_names:
        lines.append(
            "  already failing before any edit: "
            + ", ".join(inputs.baseline.failing_names[:8])
        )
        lines.append(
            "  those are pre-existing; every OTHER test passing now must still pass."
        )

    if inputs.anchors.edit_order:
        lines.append("")
        lines.append(
            "SUGGESTED EDIT ORDER (callee before caller): "
            + " -> ".join(inputs.anchors.edit_order[:20])
        )

    if inputs.abstentions:
        lines.append("")
        lines.append("KNOWN GAPS in this input:")
        for target, reason in inputs.abstentions[:20]:
            lines.append(f"  {target}: {reason}")
    return "\n".join(lines)


def build_planning_messages(inputs: PlanInputs, issue_text: str) -> tuple[dict, ...]:
    return (
        {"role": "system", "content": PLANNING_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "TASK STATEMENT\n"
                f"{issue_text}\n\n"
                f"{_render_inputs(inputs)}\n\n"
                "Call write_persistent_plan once."
            ),
        },
    )


def command_is_admissible(command: str) -> tuple[bool, str]:
    """A verification command must stay inside the repository."""
    text = (command or "").strip()
    if not text:
        return False, "empty"
    if len(text) > MAX_COMMAND_CHARS:
        return False, "too_long"
    if _FORBIDDEN_COMMAND_RE.search(text):
        return False, "names_benchmark_harness"
    if _ABSOLUTE_OR_PARENT_RE.search(text):
        return False, "escapes_repository"
    return True, ""


def validate_plan(
    payload: Any, inputs: PlanInputs
) -> tuple[tuple[PlanRow, ...], tuple[InteractionCell, ...], tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Keep only what the input actually offered; drop and record the rest."""
    abstentions: list[tuple[str, str]] = []
    if not isinstance(payload, dict):
        return (), (), (), (("*", "plan_payload_not_an_object"),)

    known_rows = {row.row_id: row for row in inputs.ledger.rows}
    known_nodes = set(inputs.anchors.all_node_ids())
    modes_by_symbol = {mode.symbol: mode for mode in inputs.anchors.modes}

    rows: list[PlanRow] = []
    seen_rows: set[str] = set()
    for item in payload.get("rows") or ():
        if not isinstance(item, dict):
            abstentions.append(("*", "plan_row_not_an_object"))
            continue
        row_id = str(item.get("row_id") or "")
        ledger_row = known_rows.get(row_id)
        if ledger_row is None:
            abstentions.append((row_id or "?", "phantom_row_id"))
            continue
        if row_id in seen_rows:
            continue
        seen_rows.add(row_id)
        anchors: list[int] = []
        for value in item.get("anchors") or ():
            try:
                node_id = int(value)
            except (TypeError, ValueError):
                abstentions.append((row_id, "phantom_node_id"))
                continue
            if node_id in known_nodes:
                anchors.append(node_id)
            else:
                abstentions.append((row_id, "phantom_node_id"))
        kind = str(item.get("verification_kind") or "")
        if kind not in VERIFICATION_KINDS:
            kind = ""
        command = str(item.get("verification_command") or "").strip()
        if command:
            admissible, reason = command_is_admissible(command)
            if not admissible:
                abstentions.append((row_id, f"verification_command_{reason}"))
                command = ""
        if not command:
            # An unprovable requirement must be visible as one. Measured: 14 of
            # 25 rows came back with no command at all and nothing said so.
            stated = str(item.get("no_check_reason") or "").strip()[:120]
            abstentions.append((row_id, f"no_check:{stated or 'unstated'}"))
        rows.append(
            PlanRow(
                row_id=row_id,
                text=ledger_row.text,
                approach=str(item.get("approach") or "").strip()[:400],
                anchors=tuple(dict.fromkeys(anchors)),
                verification_kind=kind,
                verification_command=command,
            )
        )

    interactions: list[InteractionCell] = []
    for item in payload.get("interactions") or ():
        if not isinstance(item, dict):
            continue
        row_id = str(item.get("row_id") or "")
        symbol = str(item.get("mode_symbol") or "")
        member = str(item.get("member") or "")
        if row_id not in known_rows:
            abstentions.append((row_id or "?", "phantom_row_id"))
            continue
        mode = modes_by_symbol.get(symbol)
        if mode is None:
            abstentions.append((row_id, "phantom_mode_symbol"))
            continue
        if member not in mode.members:
            abstentions.append((row_id, "phantom_mode_member"))
            continue
        interactions.append(
            InteractionCell(
                row_id=row_id,
                mode_symbol=symbol,
                member=member,
                applies=bool(item.get("applies")),
                reason=str(item.get("reason") or "")[:200],
            )
        )

    applying = {
        (cell.row_id, cell.mode_symbol, cell.member)
        for cell in interactions
        if cell.applies
    }
    for item in (payload.get("derived_rows") or ())[:MAX_DERIVED_ROWS]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()[:500]
        parent = str(item.get("from_row_id") or "")
        symbol = str(item.get("mode_symbol") or "")
        member = str(item.get("member") or "")
        if not text or parent not in known_rows:
            abstentions.append((parent or "?", "phantom_derived_parent"))
            continue
        if symbol and (parent, symbol, member) not in applying:
            # A derived row must come from a cell the plan itself marked as
            # applying. Otherwise it is a new requirement with no provenance.
            abstentions.append((parent, "derived_row_without_applying_cell"))
            continue
        command = str(item.get("verification_command") or "").strip()
        if command:
            admissible, reason = command_is_admissible(command)
            if not admissible:
                abstentions.append((parent, f"verification_command_{reason}"))
                command = ""
        digest = hashlib.sha256(
            f"{parent}|{symbol}|{member}|{text}".encode("utf-8", "surrogatepass")
        ).hexdigest()[:12]
        rows.append(
            PlanRow(
                row_id=f"drv-{digest}",
                text=text,
                approach=str(item.get("approach") or "").strip()[:400],
                anchors=(),
                verification_kind=str(item.get("verification_kind") or "") or "new_test",
                verification_command=command,
                derived_from=parent,
                mode_symbol=symbol,
                mode_member=member,
            )
        )

    order = tuple(
        row_id
        for row_id in (payload.get("edit_order") or ())
        if isinstance(row_id, str) and row_id in seen_rows
    )
    for item in payload.get("abstentions") or ():
        if isinstance(item, dict) and item.get("reason"):
            abstentions.append(
                (str(item.get("row_id") or "*"), str(item["reason"])[:120])
            )
    return tuple(rows), tuple(interactions), order, tuple(abstentions)


def build_plan(payload: Any, inputs: PlanInputs, note: str = "") -> PersistentPlan:
    """Turn a validated tool payload into the immutable plan artifact.

    ``note`` records why the payload was unusable when it is, so the journal
    alone distinguishes "the model refused" from "the model never finished".
    """
    from .deterministic import build_deterministic_plan

    base = build_deterministic_plan(inputs)
    rows, interactions, order, abstentions = validate_plan(payload, inputs)
    combined = tuple(dict.fromkeys(tuple(inputs.abstentions) + tuple(abstentions)))
    if note:
        combined = tuple(dict.fromkeys(combined + (("*", note),)))
    if not rows:
        # Keep the floor. Every requirement, anchor, caller and covering-test
        # check was computed before the call and does not stop being true
        # because the response was unusable. The note records that the
        # enrichment did not happen.
        base.abstentions = combined or base.abstentions
        if base.abstentions:
            base.status = STATUS_PARTIAL
        base.process_id = _process_id(base)
        base.planning_receipt = _planning_receipt(base)
        return base
    rows = _merge_rows(base.rows, rows)
    order = order or base.edit_order
    if not order:
        order = tuple(
            row_id
            for row_id in inputs.anchors.edit_order
            if any(row.row_id == row_id for row in rows)
        ) or tuple(row.row_id for row in rows)
    status = STATUS_PARTIAL if combined else STATUS_READY
    plan = PersistentPlan(
        status=status,
        inputs=inputs,
        rows=rows,
        interactions=interactions,
        edit_order=order,
        abstentions=combined,
        origin="enriched",
        understanding=(
            str((payload or {}).get("understanding") or "").strip()[:1200]
            if isinstance(payload, dict)
            else ""
        ),
    )
    plan.process_id = hashlib.sha256(
        plan.canonical_json().encode("utf-8", "surrogatepass")
    ).hexdigest()
    plan.planning_receipt = _planning_receipt(plan)
    return plan


def _merge_rows(
    base: tuple[PlanRow, ...], enriched: tuple[PlanRow, ...]
) -> tuple[PlanRow, ...]:
    """Enrichment may sharpen a row or add one; it may never remove one.

    A requirement the prompt states does not stop existing because the planning
    call omitted it, so the deterministic row survives with its own anchors and
    its covering-test check. Where the call supplied a value, the call wins.
    """
    by_id = {row.row_id: row for row in base}
    for row in enriched:
        current = by_id.get(row.row_id)
        if current is None:
            by_id[row.row_id] = row
            continue
        by_id[row.row_id] = PlanRow(
            row_id=current.row_id,
            text=current.text,
            approach=row.approach or current.approach,
            anchors=row.anchors or current.anchors,
            verification_kind=row.verification_kind or current.verification_kind,
            verification_command=(
                row.verification_command or current.verification_command
            ),
            derived_from=row.derived_from,
            mode_symbol=row.mode_symbol,
            mode_member=row.mode_member,
        )
    return tuple(by_id.values())


def _process_id(plan: PersistentPlan) -> str:
    return hashlib.sha256(
        plan.canonical_json().encode("utf-8", "surrogatepass")
    ).hexdigest()


def _planning_receipt(plan: PersistentPlan) -> dict:
    """A ``gt.planning_process.v1`` receipt over graph-backed citations only."""
    citations = [
        {
            "row_id": row.row_id,
            "node_id": node_id,
            "source_revision": plan.inputs.source_revision,
            "graph_revision": plan.inputs.graph_revision,
        }
        for row in plan.rows
        for node_id in row.anchors
    ]
    return {
        "schema": "gt.planning_process.v1",
        "status": plan.status,
        "process_id": plan.process_id,
        "source_revision": plan.inputs.source_revision,
        "graph_revision": plan.inputs.graph_revision,
        "steps": [row.row_id for row in plan.rows],
        "citations": citations,
        "gaps": [reason for _target, reason in plan.abstentions],
    }


def response_finish_reason(response: Any) -> str:
    """Why the provider stopped. ``length`` means the plan never got written.

    Measured in production: the planning call returned finish_reason=length with
    4,096 completion tokens, all of them reasoning, no content and no tool call.
    A reasoning model spends its output budget thinking first, so a budget sized
    for the answer alone buys nothing but a truncated turn.
    """
    choices = (
        list(response.get("choices") or ())
        if isinstance(response, dict)
        else list(getattr(response, "choices", ()) or ())
    )
    if not choices:
        return ""
    first = choices[0]
    value = (
        first.get("finish_reason")
        if isinstance(first, dict)
        else getattr(first, "finish_reason", "")
    )
    return str(value or "")


def parse_tool_arguments(response: Any) -> dict | None:
    """Extract the plan tool's arguments from a provider response."""
    choices = (
        list(response.get("choices") or ())
        if isinstance(response, dict)
        else list(getattr(response, "choices", ()) or ())
    )
    if not choices:
        return None
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else getattr(first, "message", None)
    calls = (
        list(message.get("tool_calls") or ())
        if isinstance(message, dict)
        else list(getattr(message, "tool_calls", ()) or ())
    )
    for call in calls:
        function = call.get("function") if isinstance(call, dict) else getattr(call, "function", None)
        name = (
            function.get("name")
            if isinstance(function, dict)
            else getattr(function, "name", "")
        )
        if name != PLAN_TOOL_NAME:
            continue
        raw = (
            function.get("arguments")
            if isinstance(function, dict)
            else getattr(function, "arguments", "")
        )
        try:
            parsed = json.loads(str(raw or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None
