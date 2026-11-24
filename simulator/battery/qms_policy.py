"""Proposed, explicit defaults used by relative QMS comparisons."""

from __future__ import annotations

from types import MappingProxyType

from simulator.battery.enums import NoticeKind, Quantity
from simulator.battery.records import Notice


QMS_POLICY_REGISTRY = MappingProxyType(
    {
        "qms_run_group_assumed": MappingProxyType(
            {"status": "proposed", "basis": "located panel, experiment, and channel"}
        ),
        "qms_trace_relation_assumed": MappingProxyType(
            {"status": "proposed", "basis": "unique matching admitted series"}
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
        "qms_censored_signal_comparison": MappingProxyType(
            {"status": "proposed", "basis": "positive one-sided signal bound normalized by the positive source anchor"}
        ),
        "qms_oxygen_lineage_unverified": MappingProxyType(
            {"status": "proposed", "basis": "fresh prediction uses independent oxygen or DP-010"}
        ),
    }
)


def qms_policy_notice(
    policy_id: str,
    *,
    origin: str,
    detail: str | None = None,
) -> Notice:
    """Create a typed flagged notice only for a registered QMS policy."""
    entry = QMS_POLICY_REGISTRY.get(policy_id)
    if entry is None:
        raise ValueError(f"unregistered QMS scoring policy {policy_id!r}")
    reason = policy_id
    if detail:
        reason = f"{reason}: {detail}"
    return Notice(
        kind=NoticeKind.UNVERIFIED_APPARATUS,
        affected_quantities=(Quantity.ION_INTENSITY,),
        reason=reason,
        origin=origin,
    )
