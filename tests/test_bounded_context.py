from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from gt_engine.context import (
    ContextAssemblyError,
    compact_provider_view,
    render_context_units,
)
from gt_engine.output_evidence import EvidenceStore
from gt_engine.provider_limits import (
    ProviderRequestTooLarge,
    render_and_admit_provider_request,
)
from gt_engine.request_history import load_history_evidence


def test_compaction_preserves_reasoning_and_pairs_tool_result_with_retrievable_bytes(
    tmp_path: Path,
) -> None:
    reasoning = "private reasoning that the provider requires"
    arguments = '{"command":"python -m pytest tests/test_real.py -q"}'
    complete_result = "failure evidence\n" * 2_000
    messages = [
        {"role": "system", "content": "policy"},
        {
            "role": "assistant",
            "content": None,
            "provider_specific_fields": {"reasoning": reasoning},
            "tool_calls": [{
                "id": "call-1",
                "function": {"name": "bash", "arguments": arguments},
            }],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": complete_result},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call-2",
                "function": {"name": "bash", "arguments": '{"command":"pwd"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call-2", "content": "current"},
    ]
    original = copy.deepcopy(messages)
    store = EvidenceStore(tmp_path / "state" / "output_evidence")

    view, receipt = compact_provider_view(
        messages,
        checkpoint="current state",
        char_budget=4_000,
        tail_turns=2,
        max_tail_turns=2,
        tool_output_chars=500,
        artifact_store=store,
    )

    assert messages == original
    old_assistant = next(row for row in view if row.get("role") == "assistant"
                         and row.get("tool_calls", [{}])[0].get("id") == "call-1")
    old_result = next(row for row in view if row.get("tool_call_id") == "call-1")
    assert old_assistant["provider_specific_fields"]["reasoning"] == reasoning
    assert old_assistant["tool_calls"][0]["function"]["arguments"] == arguments
    assert old_result["content"].startswith("[GT_HISTORY_EVIDENCE ")
    reference = receipt["evidence_references"][0]
    assert load_history_evidence(store.root, reference).decode() == complete_result
    assert reference["retrieval_command"].startswith("gt-evidence read ")


def test_context_units_require_explicit_supersession_and_never_slice_facts(
    tmp_path: Path,
) -> None:
    store = EvidenceStore(tmp_path / "state" / "output_evidence")
    old = {
        "unit_id": "failure-r1", "supersession_key": "current-failure",
        "content": "FAIL tests/test_a.py at revision one", "priority": 100,
    }
    new = {
        "unit_id": "failure-r2", "supersession_key": "current-failure",
        "supersedes": ["failure-r1"],
        "content": "PASS tests/test_a.py at revision two", "priority": 100,
    }
    low = {
        "unit_id": "weak-prior", "supersession_key": "weak-prior",
        "content": "W" * 2_000, "priority": 1,
    }

    rendered, receipt = render_context_units(
        [old, new, low], byte_budget=300, artifact_store=store
    )

    assert new["content"] in rendered
    assert old["content"] not in rendered
    assert "W" * 100 not in rendered
    assert receipt["superseded"] == [
        {"unit_id": "failure-r1", "superseded_by": "failure-r2"}
    ]
    omitted = next(row for row in receipt["omitted"] if row["unit_id"] == "weak-prior")
    assert load_history_evidence(store.root, omitted["reference"]) == low["content"].encode()

    with pytest.raises(ContextAssemblyError, match="implicit_supersession_forbidden"):
        render_context_units([old, {**new, "supersedes": []}], byte_budget=300)


def test_omitted_turns_are_preserved_as_paired_retrievable_groups(
    tmp_path: Path,
) -> None:
    messages = [{"role": "system", "content": "policy"}]
    for ordinal in range(4):
        messages.extend([
            {
                "role": "assistant",
                "provider_specific_fields": {"reasoning": f"reason-{ordinal}"},
                "tool_calls": [{
                    "id": f"call-{ordinal}",
                    "function": {"name": "bash", "arguments": f'{{"command":"run {ordinal}"}}'},
                }],
            },
            {"role": "tool", "tool_call_id": f"call-{ordinal}", "content": f"result-{ordinal}"},
        ])
    store = EvidenceStore(tmp_path / "state" / "output_evidence")

    view, receipt = compact_provider_view(
        messages, checkpoint="state", char_budget=1_000, tail_turns=1,
        max_tail_turns=1, artifact_store=store,
    )

    assert "GT_HISTORY_ARCHIVE" in str(view[0]["content"])
    archive = json.loads(load_history_evidence(
        store.root, receipt["history_archive_reference"]
    ))
    assert len(archive["groups"]) == 3
    first_group = json.loads(load_history_evidence(
        store.root, archive["groups"][0]["reference"]
    ))
    assert first_group[0]["provider_specific_fields"]["reasoning"] == "reason-0"
    assert first_group[0]["tool_calls"][0]["id"] == first_group[1]["tool_call_id"]


def test_provider_admission_counts_the_final_rendered_context() -> None:
    messages = [{"role": "user", "content": "task"}]

    with pytest.raises(ProviderRequestTooLarge) as captured:
        render_and_admit_provider_request(
            messages=messages,
            render_messages=lambda rows: [
                *rows, {"role": "user", "content": "rendered GT evidence"}
            ],
            model="fixture/model",
            context_window_tokens=100,
            reserved_output_tokens=20,
            metadata_source="fixture metadata",
            token_counter=lambda payload: 81 if len(payload["messages"]) == 2 else 1,
        )

    assert captured.value.request_tokens == 81


def test_provider_renderer_cannot_mutate_durable_history() -> None:
    messages = [{"role": "assistant", "content": [{"type": "text", "text": "kept"}]}]

    def mutate(rows):
        rows[0]["content"][0]["text"] = "rendered"
        return rows

    rendered, payload, _ = render_and_admit_provider_request(
        messages=messages,
        render_messages=mutate,
        model="fixture/model",
        context_window_tokens=100,
        reserved_output_tokens=20,
        metadata_source="fixture metadata",
        token_counter=lambda _payload: 1,
    )

    assert messages[0]["content"][0]["text"] == "kept"
    assert rendered[0]["content"][0]["text"] == "rendered"
    assert payload["messages"] == rendered


def test_history_evidence_corruption_is_rejected(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "state" / "output_evidence")
    _, receipt = render_context_units(
        [{"unit_id": "large", "supersession_key": "large", "content": "x" * 500,
          "priority": 1}],
        byte_budget=20,
        artifact_store=store,
    )
    reference = receipt["omitted"][0]["reference"]
    path = store.path(reference["sha256"])
    path.write_bytes(b"corrupt")

    with pytest.raises(ValueError, match="history_evidence_identity_mismatch"):
        load_history_evidence(store.root, reference)


def test_installed_history_archive_pages_drive_the_next_real_action(tmp_path):
    import base64
    import os
    import shlex
    import shutil
    import subprocess

    if not shutil.which("gt-evidence") or not shutil.which("bash"):
        pytest.skip("installed gt-evidence and Bash required")
    original = [
        {"role": "system", "content": "policy"},
        {"role": "assistant", "reasoning_content": "retain this reasoning",
         "tool_calls": [{"id": "first", "function": {"name": "bash", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "first", "content": "z" * 20000 + "\nFACT=37"},
        {"role": "assistant", "content": "Continue using the earlier fact."},
    ]
    store = EvidenceStore(tmp_path / "evidence")
    view, receipt = compact_provider_view(
        original, checkpoint="", char_budget=1500, tail_turns=1, max_tail_turns=1,
        artifact_store=store,
    )
    reference = receipt["history_archive_reference"]
    assert reference["retrieval_command"] in view[0]["content"]
    env = {**os.environ, "GT_EVIDENCE_ROOT": str(store.root)}

    def retrieve(ref):
        command = shlex.split(ref["retrieval_command"])
        result = bytearray()
        while True:
            process = subprocess.run(command, env=env, capture_output=True, check=True)
            page = json.loads(process.stdout)
            raw = (page["text"].encode() if page["encoding"] == "utf-8"
                   else base64.b64decode(page["base64"]))
            assert len(raw) <= 8192
            result.extend(raw)
            if page["continuation_offset"] is None:
                break
            command[-2] = str(page["continuation_offset"])
        return bytes(result)

    archive = json.loads(retrieve(reference))
    group = json.loads(retrieve(archive["groups"][0]["reference"]))
    assert group == original[1:3]
    fact = int(group[1]["content"].rsplit("FACT=", 1)[1])
    action = subprocess.run(["bash", "-c", f"printf %s {fact} > recovered.txt"],
                            cwd=tmp_path, capture_output=True, check=True)
    assert action.returncode == 0
    assert (tmp_path / "recovered.txt").read_text() == "37"
