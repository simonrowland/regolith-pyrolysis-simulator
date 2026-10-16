"""Shared authority gate for gas-rail standard-reaction blocks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _gas_rail_block_is_authoritative(block: Mapping[str, Any]) -> bool:
    """Return False for demoted / dormant gas_rail blocks (provenance only).

    Premise: a present ``gas_rail_standard_reaction`` mapping historically
    always selected the liquid-oxide Pref over Ellingham gas_fugacity.
    Algebra: demotion is an explicit status/authority gate, not key deletion,
    so the TE+JANAF coefficients remain inspectable while Builtin falls through
    to the gas-metal Ellingham root. Unitless status string / bool. Sanity:
    ``status: dormant_non_authoritative`` (MC-5 Ca, 2026-08-07) must not
    produce ``pressure_rail=gas_rail_liquid_oxide_standard_reaction``.
    """

    if block.get("authoritative") is False:
        return False
    status = str(
        block.get("status")
        or block.get("runtime_disposition")
        or block.get("authority_status")
        or ""
    ).strip().lower()
    if not status:
        return True
    dormant_markers = (
        "dormant",
        "dormant_non_authoritative",
        "inactive",
        "inactive_dormant",
        "inactive_provenance_only",
        "status_bearing_non_authoritative",
        "non_authoritative",
        "provenance_only",
    )
    return status not in dormant_markers and not status.startswith("dormant")
