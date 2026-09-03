from __future__ import annotations

from gt_engine.graph_utilisation import GRAPH_BACKED_FEATURES, graph_utilisation

# The exact delivered set from paid run 33708231670.
RUN_33708231670 = [
    {"evidence_type": "new_file_destination"},
    {"evidence_type": "context_delta"},
    {"evidence_type": "trace_frame"},
    {"evidence_type": "missing_role_postcreate:implementation"},
    {"evidence_type": "context_contract"},
]


def test_the_paid_run_shows_no_graph_backed_delivery():
    """The run attestation passed as 'graph invalid' but nobody measured use."""

    report = graph_utilisation(RUN_33708231670)

    assert report["graph_backed_delivery"] is False
    assert report["graph_backed_features"] == []


def test_a_caller_contract_delivery_evidences_graph_use():
    report = graph_utilisation([{"evidence_type": "caller_contract_view"}])

    assert report["graph_backed_delivery"] is True
    assert report["graph_backed_features"] == ["caller_contract"]


def test_cochange_and_signature_deltas_also_evidence_graph_use():
    report = graph_utilisation(
        [{"evidence_type": "cochange_partner"}, {"evidence_type": "signature_mismatch"}]
    )

    assert report["graph_backed_features"] == ["cochange_prior", "signature_delta"]


def test_trace_frame_is_not_treated_as_graph_evidence():
    """It maps to localization but is runtime-derived, not graph-derived."""

    assert graph_utilisation([{"evidence_type": "trace_frame"}])["graph_backed_delivery"] is False
    assert "localization" not in GRAPH_BACKED_FEATURES


def test_kind_is_accepted_when_evidence_type_is_absent():
    report = graph_utilisation([{"kind": "def_ref_partition"}])

    assert report["graph_backed_features"] == ["def_partition"]


def test_unknown_evidence_types_are_ignored_not_counted():
    report = graph_utilisation([{"evidence_type": "not_a_real_envelope"}])

    assert report["delivered_features"] == []
    assert report["graph_backed_delivery"] is False


def test_no_deliveries_is_not_graph_use():
    report = graph_utilisation([])

    assert report["graph_backed_delivery"] is False
    assert report["delivered_features"] == []


def test_features_are_deduplicated_and_sorted():
    report = graph_utilisation(
        [
            {"evidence_type": "caller_contract"},
            {"evidence_type": "caller_contract_search"},
            {"evidence_type": "new_file_destination"},
        ]
    )

    assert report["delivered_features"] == ["caller_contract", "newfile_precedent"]


# --- the co-change row gate -------------------------------------------------
#
# `cochange_prior` is now emittable, so it can reach this report. It must not
# be able to discharge the graph-evidence obligation on a graph whose
# `cochanges` table is empty -- which is every graph built from a depth-1
# clone. The delivery is still reported; only the enforcement set is gated.


def test_cochange_alone_does_not_discharge_the_obligation_when_rows_are_unstated():
    report = graph_utilisation([{"evidence_type": "cochange_partner"}])

    assert report["graph_backed_features"] == ["cochange_prior"]
    assert report["enforcement_features"] == []
    assert report["graph_backed_delivery"] is False


def test_cochange_alone_does_not_discharge_the_obligation_on_an_empty_table():
    report = graph_utilisation(
        [{"evidence_type": "cochange_partner"}], cochange_rows=0
    )

    assert report["cochange_rows"] == 0
    assert report["enforcement_features"] == []
    assert report["graph_backed_delivery"] is False


def test_cochange_discharges_the_obligation_once_rows_exist():
    report = graph_utilisation(
        [{"evidence_type": "cochange_partner"}], cochange_rows=23_720
    )

    assert report["cochange_rows"] == 23_720
    assert report["enforcement_features"] == ["cochange_prior"]
    assert report["graph_backed_delivery"] is True


def test_the_gate_touches_no_other_graph_backed_feature():
    report = graph_utilisation(
        [{"evidence_type": "caller_contract_view"}], cochange_rows=0
    )

    assert report["enforcement_features"] == ["caller_contract"]
    assert report["graph_backed_delivery"] is True


def test_a_gated_cochange_never_suppresses_a_real_graph_delivery():
    report = graph_utilisation(
        [
            {"evidence_type": "cochange_partner"},
            {"evidence_type": "signature_mismatch"},
        ]
    )

    assert report["graph_backed_features"] == ["cochange_prior", "signature_delta"]
    assert report["enforcement_features"] == ["signature_delta"]
    assert report["graph_backed_delivery"] is True


def test_cochange_prior_is_a_declared_graph_backed_feature():
    assert "cochange_prior" in GRAPH_BACKED_FEATURES
