"""Shared SSO-R owner recipe certification surface."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from enum import Enum
from typing import Any


OWNER_RECIPE_T_C = 1650.0
OWNER_RECIPE_PO2_MBAR = 1.0e-6
OWNER_RECIPE_PN2_MBAR = 10.0
OWNER_RECIPE_TOTAL_PRESSURE_MBAR = OWNER_RECIPE_PO2_MBAR + OWNER_RECIPE_PN2_MBAR
OWNER_RECIPE_DOSE_FRACTION = 1.0
OWNER_RECIPE_STAGE_NAME = "alkali_early_fe"
OWNER_RECIPE_GAS_COVER_MODE = "pn2_sweep"
OWNER_CERTIFICATION_ASSERTION = "owner_pN2_recipe_point_requested_pO2_semantics"
OWNER_CERTIFIED_SURFACE_SOURCE = (
    "scripts/sso_r_validation_map.py owner_pN2_recipe_point_requested_pO2_semantics"
)


class CertificationReason(str, Enum):
    LIVE_RECIPE_REACHABILITY_PROBE_MISSING = (
        "live recipe reachability probe missing"
    )
    LIVE_RECIPE_UNREACHABLE = "live recipe unreachable"
    LIVE_RECIPE_IDENTITY_MISMATCH = "live recipe stage/gas identity mismatch"


def _same_identity_number(actual: Any, expected: Any) -> bool:
    try:
        actual_number = float(actual)
        expected_number = float(expected)
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        math.isfinite(actual_number)
        and math.isfinite(expected_number)
        and actual_number == expected_number
    )


def certification_reachability_reason(
    live_owner_probe: Mapping[str, Any] | None,
    owner_row: Mapping[str, Any] | None,
) -> CertificationReason | None:
    """Return why the live recipe probe cannot certify the configured owner row."""
    if live_owner_probe is None:
        return CertificationReason.LIVE_RECIPE_REACHABILITY_PROBE_MISSING
    if live_owner_probe.get("recipe_reachable") is not True:
        return CertificationReason.LIVE_RECIPE_UNREACHABLE
    if owner_row is None:
        return CertificationReason.LIVE_RECIPE_IDENTITY_MISMATCH

    matches_owner_row = (
        live_owner_probe.get("recipe_stage_name") == OWNER_RECIPE_STAGE_NAME
        and live_owner_probe.get("recipe_gas_cover_mode")
        == OWNER_RECIPE_GAS_COVER_MODE
        and _same_identity_number(owner_row.get("temperature_C"), OWNER_RECIPE_T_C)
        and _same_identity_number(
            owner_row.get("requested_pO2_mbar"), OWNER_RECIPE_PO2_MBAR
        )
        and _same_identity_number(
            owner_row.get("requested_pN2_mbar"), OWNER_RECIPE_PN2_MBAR
        )
        and _same_identity_number(
            owner_row.get("dose_fraction_of_full_FeO_equiv"),
            OWNER_RECIPE_DOSE_FRACTION,
        )
        and _same_identity_number(
            live_owner_probe.get("temperature_C"), owner_row.get("temperature_C")
        )
        and _same_identity_number(
            live_owner_probe.get("recipe_pO2_mbar"),
            owner_row.get("requested_pO2_mbar"),
        )
        and _same_identity_number(
            live_owner_probe.get("recipe_pN2_mbar"),
            owner_row.get("requested_pN2_mbar"),
        )
        and _same_identity_number(
            live_owner_probe.get("recipe_p_total_mbar"),
            owner_row.get("total_pressure_mbar"),
        )
        and _same_identity_number(
            owner_row.get("total_pressure_mbar"), OWNER_RECIPE_TOTAL_PRESSURE_MBAR
        )
    )
    if not matches_owner_row:
        return CertificationReason.LIVE_RECIPE_IDENTITY_MISMATCH
    return None


def owner_certification_row(
    rows: Iterable[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Return the first row matching the exact configured owner recipe vector."""
    return next(
        (
            row for row in rows
            if _same_identity_number(row.get("temperature_C"), OWNER_RECIPE_T_C)
            and _same_identity_number(
                row.get("requested_pO2_mbar"), OWNER_RECIPE_PO2_MBAR
            )
            and _same_identity_number(
                row.get("requested_pN2_mbar"), OWNER_RECIPE_PN2_MBAR
            )
            and _same_identity_number(
                row.get("dose_fraction_of_full_FeO_equiv"),
                OWNER_RECIPE_DOSE_FRACTION,
            )
        ),
        None,
    )
