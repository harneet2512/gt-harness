from __future__ import annotations

import hashlib

import pytest

from gt_engine.persistent_execution_state import (
    CatalogItem,
    Feature18Lifecycle,
    SelectCatalogAbstention,
    SelectCatalogStage,
    build_feature18_catalog,
    build_select_catalog_tool,
    parse_select_catalog_arguments,
)


def _catalog():
    return build_feature18_catalog(
        source_revision="source-1",
        workspace_revision="workspace-1",
        graph_revision="graph-1",
        items=(
            CatalogItem(item_id="focus-1", kind="focus", label="service.py", content_sha256="a" * 64),
            CatalogItem(item_id="check-1", kind="validation", label="pytest", content_sha256="b" * 64),
        ),
    )


def test_feature18_selection_lifecycle_is_content_safe_and_action_bound():
    lifecycle = Feature18Lifecycle.from_catalog(_catalog(), event_id="event-1")
    assert lifecycle.stage is SelectCatalogStage.CANDIDATE

    request = b'{"tool":"select_catalog","ids":["focus-1"]}'
    lifecycle.certify(
        attempted_ids=("focus-1",),
        selected_ids=("focus-1",),
        request_bytes=request,
        tool_schema_bytes=b"schema-v1",
        argument_bytes=b"focus-1",
        provider_request_id="request-1",
    )
    lifecycle.deliver(delivery_id="delivery-1")
    lifecycle.consume(selected_ids=("focus-1",), resulting_action="inspect service.py")
    lifecycle.validate(validation="pytest passed", contradicted=False)

    receipt = lifecycle.receipt()
    assert receipt["schema"] == "gt.select_catalog_lifecycle.v1"
    assert receipt["feature_id"] == "select_catalog"
    assert receipt["stage"] == "VALIDATED"
    assert receipt["selected_ids"] == ["focus-1"]
    assert receipt["request_sha256"] == hashlib.sha256(request).hexdigest()


def test_feature18_rejects_duplicate_and_out_of_catalog_ids_without_consumption():
    lifecycle = Feature18Lifecycle.from_catalog(_catalog(), event_id="event-2")
    lifecycle.certify(
        attempted_ids=("focus-1", "focus-1"),
        selected_ids=(),
        request_bytes=b"bad",
        tool_schema_bytes=b"schema-v1",
        argument_bytes=b"bad",
        provider_request_id="request-2",
    )
    assert lifecycle.stage is SelectCatalogStage.ABSTAINED
    assert lifecycle.abstention_reason is SelectCatalogAbstention.DUPLICATE_ID
    assert lifecycle.receipt()["selected_ids"] == []

    other = Feature18Lifecycle.from_catalog(_catalog(), event_id="event-3")
    other.certify(
        attempted_ids=("missing",),
        selected_ids=(),
        request_bytes=b"bad",
        tool_schema_bytes=b"schema-v1",
        argument_bytes=b"bad",
        provider_request_id="request-3",
    )
    assert other.stage is SelectCatalogStage.ABSTAINED
    assert other.abstention_reason is SelectCatalogAbstention.OUT_OF_CATALOG


def test_feature18_wrong_transition_is_rejected():
    lifecycle = Feature18Lifecycle.from_catalog(_catalog(), event_id="event-4")
    with pytest.raises(ValueError, match="DELIVERED requires CERTIFIED"):
        lifecycle.deliver(delivery_id="delivery-4")


def test_select_catalog_schema_and_parser_are_bound_to_visible_ids():
    catalog = _catalog()
    tool = build_select_catalog_tool(catalog)
    enum = tool["function"]["parameters"]["properties"]["ids"]["items"]["enum"]
    assert enum == ["check-1", "focus-1"]
    assert parse_select_catalog_arguments({"ids": ["focus-1"]}, catalog) == (
        ("focus-1",), ("focus-1",),
    )
    assert parse_select_catalog_arguments({"ids": ["missing"]}, catalog) == (
        ("missing",), (),
    )
    assert parse_select_catalog_arguments({"ids": ["focus-1", "focus-1"]}, catalog) == (
        ("focus-1", "focus-1"), (),
    )
