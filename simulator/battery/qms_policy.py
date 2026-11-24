"""Explicit defaults and ratified assumptions for relative QMS comparisons."""

from __future__ import annotations

from types import MappingProxyType

QMS_POLICY_REGISTRY = MappingProxyType(
    {
        "qms_boundary_assumed": MappingProxyType(
            {
                "status": "proposed",
                "basis": "one shared known boundary, or shared-unreported when none is known",
            }
        ),
        "qms_clock_anchor_assumed": MappingProxyType(
            {"status": "ratified", "basis": "first shared positive sample above a printed floor, otherwise raw > 0; native time and index retained; no fitted shift or scale"}
        ),
        "qms_signal_response_assumed": MappingProxyType(
            {"status": "ratified", "basis": "assume only unverified proportionality, background treatment, or isolation"}
        ),
        "qms_provenance_assumed": MappingProxyType(
            {"status": "ratified", "basis": "located parent panel supplies a missing child locator"}
        ),
    }
)
