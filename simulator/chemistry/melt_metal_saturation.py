"""Melt oxide/metal saturation floors and redox-buffer planning."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from simulator.chemistry.ellingham_graph import (
    dissociation_pO2_threshold,
    ellingham_oxide_stoichiometry_for_formula,
)
from simulator.chemistry.ellingham_thermo import (
    ELLINGHAM_THERMO,
    ellingham_stoichiometry,
)
from simulator.chemistry.melt_activity import melt_oxide_activity


class MeltMetalSaturationError(ValueError):
    """Typed refusal for invalid melt metal-saturation calculations."""


def melt_metal_saturation_fO2_floor_rows(
    temperature_K: float,
    composition_mol: Mapping[str, float],
    *,
    oxide_to_metal: Mapping[str, tuple[str, float, float]],
) -> list[tuple[float, str, float]]:
    """Resolve supported oxide floors in descending threshold order."""

    T_K = float(temperature_K)
    floors: list[tuple[float, str, float]] = []
    for oxide, (metal, _, _) in oxide_to_metal.items():
        if float(composition_mol.get(oxide, 0.0) or 0.0) <= 0.0:
            continue
        if metal not in ELLINGHAM_THERMO:
            continue
        # JANAF only applies when its metal/oxide reaction matches the
        # stoichiometry of the ledger oxide being examined.
        expected = ellingham_oxide_stoichiometry_for_formula(
            oxide,
            oxide_to_metal=oxide_to_metal,
        )
        if expected is None:
            continue
        actual = ellingham_stoichiometry(metal)
        if not all(
            math.isclose(left, right, rel_tol=0.0, abs_tol=1e-9)
            for left, right in zip(expected, actual)
        ):
            continue
        activity = melt_oxide_activity(
            oxide,
            composition_mol,
            temperature_K=T_K,
        )
        if activity is None:
            continue
        parent_activity = activity.thermodynamic_parent_activity()
        if parent_activity <= 0.0:
            continue
        # Since metal activity is at most one, fO2/bar must be at least
        # K*a_oxide**n_oxide. The returned threshold is in bar.
        threshold_bar = dissociation_pO2_threshold(
            metal,
            T_K,
            a_oxide=parent_activity,
        )
        if not math.isfinite(threshold_bar) or threshold_bar <= 0.0:
            raise MeltMetalSaturationError(
                "metal-saturation fO2 floor is invalid for "
                f"{oxide}: pO2_bar={threshold_bar!r}"
            )
        floors.append((math.log10(threshold_bar), oxide, parent_activity))
    return sorted(floors, key=lambda row: row[0], reverse=True)


def melt_metal_saturation_fO2_floor_log(
    temperature_K: float,
    composition_mol: Mapping[str, float],
    *,
    oxide_to_metal: Mapping[str, tuple[str, float, float]],
) -> tuple[float, str, float] | None:
    """Return the highest supported oxide/metal saturation floor."""

    rows = melt_metal_saturation_fO2_floor_rows(
        temperature_K,
        composition_mol,
        oxide_to_metal=oxide_to_metal,
    )
    return rows[0] if rows else None


def unsupported_melt_metal_saturation_notice(
    composition_mol: Mapping[str, float],
) -> dict[str, str] | None:
    """Notice that no citable condensed CoO formation segment is accepted."""

    if (
        float(composition_mol.get("CoO", 0.0) or 0.0) <= 0.0
        or "Co" in ELLINGHAM_THERMO
    ):
        return None
    return {
        "type": "typed_notice",
        "kind": "melt_metal_saturation_oxide_not_on_ellingham_owner",
        "oxide": "CoO",
        "metal": "Co",
        "reason_code": "no_accepted_ellingham_formation_segment",
        "message": "CoO not on the Ellingham owner; Co not buffered",
    }


def melt_metal_saturation_buffer_not_reached_notice(
    *,
    reason_code: str,
    binding_oxide: str,
    floor_fO2_log: float,
    best_reachable_fO2_log: float,
) -> dict[str, float | str]:
    """Describe a floor crossing the available buffer could not reach."""

    return {
        "type": "typed_notice",
        "kind": "melt_metal_saturation_buffer_not_reached",
        "reason_code": reason_code,
        "binding_oxide": binding_oxide,
        "floor_fO2_log": float(floor_fO2_log),
        "best_reachable_fO2_log": float(best_reachable_fO2_log),
    }


def plan_melt_metal_saturation_buffer(
    *,
    candidate_fO2_log: float,
    redox_capacity_mol_per_ln: float,
    temperature_K: float,
    composition_mol: Mapping[str, float],
    oxide_to_metal: Mapping[str, tuple[str, float, float]],
    upper_bound_fO2_log: float,
    oxygen_noop_mol: float,
) -> dict[str, Any]:
    """Plan sequential, capacity-limited oxide buffering against the live floor.

    For an Ellingham reaction normalized to one mol O2, reducing ``n_ox``
    mol oxide produces ``n_M`` mol metal and releases that O2 once. The
    resolver orders accepted lunar binders by their current thresholds, so
    inventory exhaustion proceeds from NiO to FeO as each live floor changes.
    CoO stays out of the plan until its Ellingham owner has an accepted fit.
    """

    candidate = float(candidate_fO2_log)
    capacity = float(redox_capacity_mol_per_ln)
    if not math.isfinite(candidate):
        raise MeltMetalSaturationError(
            "melt metal-saturation buffer candidate fO2 must be finite, "
            f"got {candidate_fO2_log!r}"
        )
    if not math.isfinite(capacity) or capacity <= 0.0:
        raise MeltMetalSaturationError(
            "melt metal-saturation buffer redox capacity must be finite "
            f"and positive, got {redox_capacity_mol_per_ln!r}"
        )
    floor_rows = melt_metal_saturation_fO2_floor_rows(
        temperature_K,
        composition_mol,
        oxide_to_metal=oxide_to_metal,
    )
    if not floor_rows or candidate >= floor_rows[0][0]:
        return {
            "oxide_extent_mol_by_oxide": {},
            "reduction_order": [],
            "oxygen_deficit_o2_mol": 0.0,
            "oxygen_buffered_o2_mol": 0.0,
            "oxygen_unbuffered_o2_mol": 0.0,
            "candidate_fO2_log_after_buffer": candidate,
            "floor_log_after_buffer": floor_rows[0][0] if floor_rows else None,
        }

    initial_deficit_o2_mol = (
        (floor_rows[0][0] - candidate) * math.log(10.0) * capacity
    )
    remaining_o2_mol = initial_deficit_o2_mol
    current_fO2_log = candidate
    upper_bound_log = max(candidate, float(upper_bound_fO2_log))
    working_composition = {
        str(oxide): float(amount)
        for oxide, amount in composition_mol.items()
    }
    extents: dict[str, float] = {}
    order: list[str] = []

    while floor_rows and floor_rows[0][0] > current_fO2_log + 1.0e-12:
        if remaining_o2_mol <= oxygen_noop_mol:
            break
        _floor_log, oxide, _activity = floor_rows[0]
        stoichiometry = ellingham_oxide_stoichiometry_for_formula(
            oxide,
            oxide_to_metal=oxide_to_metal,
        )
        if stoichiometry is None:
            break
        n_metal_per_o2, n_oxide_per_o2 = stoichiometry
        if n_metal_per_o2 <= 0.0 or n_oxide_per_o2 <= 0.0:
            break
        available_oxide_mol = max(
            0.0,
            float(working_composition.get(oxide, 0.0) or 0.0),
        )
        capacity_o2_mol = available_oxide_mol / n_oxide_per_o2
        max_buffer_o2_mol = min(remaining_o2_mol, capacity_o2_mol)
        if max_buffer_o2_mol <= oxygen_noop_mol:
            break

        def candidate_and_floor_after(
            buffered_o2_mol: float,
        ) -> tuple[float, list[tuple[float, str, float]]]:
            composition_after = dict(working_composition)
            composition_after[oxide] = max(
                0.0,
                available_oxide_mol - buffered_o2_mol * n_oxide_per_o2,
            )
            candidate_after = min(
                upper_bound_log,
                current_fO2_log
                + buffered_o2_mol / (capacity * math.log(10.0)),
            )
            rows_after = melt_metal_saturation_fO2_floor_rows(
                temperature_K,
                composition_after,
                oxide_to_metal=oxide_to_metal,
            )
            return candidate_after, rows_after

        max_candidate_log, max_floor_rows = candidate_and_floor_after(
            max_buffer_o2_mol
        )
        reaches_live_floor = (
            not max_floor_rows
            or max_candidate_log >= max_floor_rows[0][0] - 1.0e-12
        )
        if reaches_live_floor:
            low_o2_mol = 0.0
            high_o2_mol = max_buffer_o2_mol
            for _ in range(60):
                middle_o2_mol = (low_o2_mol + high_o2_mol) / 2.0
                middle_candidate_log, middle_floor_rows = (
                    candidate_and_floor_after(middle_o2_mol)
                )
                if (
                    not middle_floor_rows
                    or middle_candidate_log
                    >= middle_floor_rows[0][0] - 1.0e-12
                ):
                    high_o2_mol = middle_o2_mol
                else:
                    low_o2_mol = middle_o2_mol
            buffered_o2_mol = high_o2_mol
        else:
            buffered_o2_mol = max_buffer_o2_mol

        oxide_debit_mol = buffered_o2_mol * n_oxide_per_o2
        if (
            capacity_o2_mol <= remaining_o2_mol
            and buffered_o2_mol
            >= capacity_o2_mol
            - max(oxygen_noop_mol, 1.0e-12 * capacity_o2_mol)
        ):
            buffered_o2_mol = capacity_o2_mol
            oxide_debit_mol = available_oxide_mol
        extents[oxide] = extents.get(oxide, 0.0) + (
            buffered_o2_mol * n_metal_per_o2
        )
        order.append(oxide)
        working_composition[oxide] = max(
            0.0,
            available_oxide_mol - oxide_debit_mol,
        )
        current_fO2_log, floor_rows = candidate_and_floor_after(
            buffered_o2_mol
        )
        remaining_o2_mol = max(0.0, remaining_o2_mol - buffered_o2_mol)
        if not floor_rows or current_fO2_log >= floor_rows[0][0] - 1.0e-12:
            break

    floor_log_after = floor_rows[0][0] if floor_rows else None
    return {
        "oxide_extent_mol_by_oxide": extents,
        "reduction_order": order,
        "oxygen_deficit_o2_mol": initial_deficit_o2_mol,
        "oxygen_buffered_o2_mol": initial_deficit_o2_mol - remaining_o2_mol,
        "oxygen_unbuffered_o2_mol": max(0.0, remaining_o2_mol),
        "candidate_fO2_log_after_buffer": current_fO2_log,
        "floor_log_after_buffer": floor_log_after,
    }
