"""Canonical, provider-free analysis of GT benchmark evidence."""

from gt_harness.analysis.delivery import DeliveryAuditError, audit_treatment_delivery
from gt_harness.analysis.metrics import TerminalDisposition, summarize_run
from gt_harness.analysis.uptake import measure_delivery_uptake

__all__ = [
    "DeliveryAuditError",
    "TerminalDisposition",
    "audit_treatment_delivery",
    "measure_delivery_uptake",
    "summarize_run",
]

