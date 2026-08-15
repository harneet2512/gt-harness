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
