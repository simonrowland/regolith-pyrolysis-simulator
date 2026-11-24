"""Proposed, explicit defaults used by relative QMS comparisons."""

from __future__ import annotations

from types import MappingProxyType

QMS_POLICY_REGISTRY = MappingProxyType(
    {
        "qms_run_group_assumed": MappingProxyType(
            {"status": "proposed", "basis": "located panel, experiment, and channel"}
        ),
        "qms_clock_anchor_assumed": MappingProxyType(
            {"status": "proposed", "basis": "first shared positive sample; no fitted shift or scale"}
        ),
        "qms_signal_response_assumed": MappingProxyType(
            {"status": "proposed", "basis": "proportional signal with no extra background or fragment correction"}
        ),
        "qms_provenance_assumed": MappingProxyType(
            {"status": "proposed", "basis": "located parent panel supplies a missing child locator"}
        ),
        "qms_oxygen_lineage_unverified": MappingProxyType(
            {"status": "proposed", "basis": "fresh prediction uses independent oxygen or DP-010"}
        ),
    }
)
