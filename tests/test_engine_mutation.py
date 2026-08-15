"""IE-07 mutation protocol tests: PROPOSE/PREFLIGHT/COMMIT + CAS semantics."""
from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from gt_engine.engine.contracts import EvidenceArtifact, MutationProposal
from gt_engine.engine.mutation import (
    AtomicWriteFailed,
    PreimageMismatch,
    StaleProposal,
    build_write_set,
    commit,
    preflight,
    propose,
    rollback,
)

TOKEN = "tok-1"


def _sha(payload):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _tree():
    return {"src/x.py": b"def old(): pass\n"}


def _proposal(tree, post, token=TOKEN, **overrides):
    kwargs = dict(
        snapshot_token=token,
        target_path="src/x.py",
        expected_preimage_hash=_sha(tree["src/x.py"]),
        proposed_postimage_bytes_or_patch=post,
    )
    kwargs.update(overrides)
    return propose(**kwargs)


def test_propose_computes_postimage_hash_and_roundtrips():
    tree = _tree()
    post = b"def new(): pass\n"
    p = _proposal(tree, post)
    assert p.proposed_postimage_hash == _sha(post)
    assert p.expected_preimage_hash == _sha(tree["src/x.py"])
    assert p.proposed_patch == ""
    assert MutationProposal.from_dict(p.to_dict()) == p


def test_propose_str_patch_is_encoded_and_stored():
    p = propose(TOKEN, "src/x.py", _sha(b"old"), "def new(): pass\n")
    assert p.proposed_patch == "def new(): pass\n"
    assert p.proposed_postimage_hash == _sha("def new(): pass\n")


def test_propose_is_deterministic_and_content_addressed():
    tree = _tree()
    a = _proposal(tree, b"x")
    b = _proposal(tree, b"x")
    c = _proposal(tree, b"y")
    assert a == b
    assert a.proposal_id == b.proposal_id
    assert a.proposal_id != c.proposal_id
    assert len(a.proposal_id) == 64


def test_commit_success_returns_content_addressed_receipt():
    tree = _tree()
    post = b"def new(): pass\n"
    p = _proposal(tree, post)
    receipt = commit(p, TOKEN, tree, postimage_bytes=post)
    assert receipt.atomic is True
    assert receipt.commit_id == receipt.commit_hash
    assert len(receipt.commit_hash) == 64
    assert receipt.proposal_id == p.proposal_id
    assert receipt.snapshot_token == TOKEN
    assert receipt.committed_files == {"src/x.py": _sha(post)}
    assert receipt.rollback == ()
    assert tree["src/x.py"] == post


def test_stale_snapshot_token_rejected():
    tree = _tree()
    p = _proposal(tree, b"new")
    with pytest.raises(StaleProposal):
        commit(p, "other-token", tree, postimage_bytes=b"new")


def test_preimage_mismatch_rejected_when_tree_changed():
    tree = _tree()
    p = _proposal(tree, b"new")
    tree["src/x.py"] = b"someone else changed me\n"
    with pytest.raises(PreimageMismatch):
        commit(p, TOKEN, tree, postimage_bytes=b"new")


def test_atomic_write_failure_rolls_back_tree():
    tree = {"a.txt": b"a-old", "b.txt": b"b-old"}
    ws = {"a.txt": b"a-new", "b.txt": b"b-new"}

    def flaky(write_tree, write_set):
        write_tree["a.txt"] = write_set["a.txt"]
        raise RuntimeError("disk full")

    p = propose(TOKEN, "a.txt", _sha(b"a-old"), b"a-new")
    with pytest.raises(AtomicWriteFailed) as exc:
        commit(p, TOKEN, tree, flaky, write_set=ws)
    assert tree == {"a.txt": b"a-old", "b.txt": b"b-old"}
    assert exc.value.rolled_back == ("a.txt", "b.txt")


def test_multi_file_write_set_create_modify_delete():
    tree = {"mod.py": b"old", "del.py": b"gone"}
    p = propose(TOKEN, "mod.py", _sha(b"old"), b"new")
    ws = build_write_set(p, b"new")
    ws["new.py"] = b"created"
    ws["del.py"] = None
    receipt = commit(p, TOKEN, tree, write_set=ws)
    assert tree["mod.py"] == b"new"
    assert tree["new.py"] == b"created"
    assert "del.py" not in tree
    assert receipt.committed_files == {
        "mod.py": _sha(b"new"),
        "new.py": _sha(b"created"),
    }
    assert "del.py" not in receipt.committed_files


def test_rename_is_delete_plus_create():
    tree = {"src/old.py": b"content"}
    p = propose(TOKEN, "src/new.py", "", b"content")
    ws = build_write_set(p, b"content", rename_from="src/old.py")
    assert ws == {"src/new.py": b"content", "src/old.py": None}
    commit(p, TOKEN, tree, write_set=ws)
    assert "src/old.py" not in tree
    assert tree["src/new.py"] == b"content"


def test_preflight_runs_on_proposed_bytes_not_tree():
    tree = {"app.py": b"def broken(:\n"}
    post = b"def fixed():\n    pass\n"
    seen = {}

    def syntax_producer(path, proposed_bytes):
        seen[path] = proposed_bytes
        return EvidenceArtifact(
            artifact_id="ev-syntax",
            owner="syntax_result",
            semantics="exact syntax result for changed file",
            content={"ok": b"pass" in proposed_bytes},
        )

    p = propose(TOKEN, "app.py", _sha(tree["app.py"]), post)
    artifacts = preflight(p, [syntax_producer], postimage_bytes=post)
    assert len(artifacts) == 1
    assert artifacts[0].content == {"ok": True}
    assert seen == {"app.py": post}
    assert tree["app.py"] == b"def broken(:\n"


def test_postflight_evidence_attached_from_preflight_producer():
    tree = _tree()
    post = b"def new(): pass\n"
    artifact = EvidenceArtifact(
        artifact_id="ev-1",
        owner="syntax_result",
        semantics="syntax check",
        content={"ok": True},
    )
    p = _proposal(tree, post)
    artifacts = preflight(p, [lambda path, bts: artifact], postimage_bytes=post)
    assert artifacts == (artifact,)
    committed = replace(p, preflight=artifacts)
    receipt = commit(committed, TOKEN, tree, postimage_bytes=post)
    assert receipt.postflight == (artifact,)


def test_rollback_restores_prior_bytes_and_absence():
    tree = {"x.py": b"old", "created.py": b"new"}
    rollback(tree, {"x.py": b"old", "created.py": None})
    assert tree == {"x.py": b"old"}
