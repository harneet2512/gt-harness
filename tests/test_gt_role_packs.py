from gt_engine.role_packs import select_role_pack
from gt_engine.task_contract import TaskContract


def _contract(role: str) -> TaskContract:
    return TaskContract(role=role, obligations=())


def test_role_pack_selection_is_deterministic():
    assert select_role_pack(_contract("code_behavior")).pack_id == "code_build"
    assert select_role_pack(_contract("data_transform")).pack_id == "data_transform"
    assert select_role_pack(_contract("content_scan")).pack_id == (
        "repository_content"
    )


def test_content_pack_excludes_caller_contract_noise():
    pack = select_role_pack(_contract("content_scan"))
    assert "caller_contract" not in pack.allowed_evidence
    assert "content_scope" in pack.predicate_kinds


def test_code_and_data_packs_keep_navigation_and_new_file_evidence():
    for role in ("code_behavior", "data_transform"):
        allowed = set(select_role_pack(_contract(role)).allowed_evidence)
        assert {"def_partition", "newfile_precedent"} <= allowed


# --- the co-change prior allow-list ----------------------------------------
#
# `cochange_prior` is an editing signal: "the file you just touched has a
# historical companion". It belongs in the two packs whose lifecycle contains
# pre_edit/post_edit and whose predicate kinds are behavioural, and nowhere
# else. `repository_content` is a completeness sweep -- a historical companion
# neither widens nor closes its scope, and its allowed set is deliberately the
# minimum that serves a scan.


def test_code_and_data_packs_carry_the_cochange_prior():
    for role in ("code_behavior", "data_transform"):
        allowed = set(select_role_pack(_contract(role)).allowed_evidence)
        assert "cochange_prior" in allowed


def test_the_content_scan_pack_does_not_carry_the_cochange_prior():
    pack = select_role_pack(_contract("content_scan"))

    assert "cochange_prior" not in pack.allowed_evidence


def test_the_router_admits_a_cochange_partner_only_where_the_pack_allows_it():
    from gt_engine.evidence_router import EvidenceRouter

    rendered = (
        "src/a.py: co-change prior revision=r partner=src/b.py count=4 "
        "support=4 window=unrecorded "
        "provenance=cochanges(file_a=src/a.py,file_b=src/b.py) "
        "status=prior_not_resolution"
    )
    for role, expected in (("code_behavior", True), ("content_scan", False)):
        contract = _contract(role)
        router = EvidenceRouter(contract, role_pack=select_role_pack(contract))
        keep, reason = router.admit(
            "cochange_partner", rendered, command="cat src/a.py", output=""
        )
        assert keep is expected, reason
        if not expected:
            assert reason == "role_pack_evidence_mismatch"
