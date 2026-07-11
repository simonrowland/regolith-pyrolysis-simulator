"""AlphaMELTS-activity volatility diagnostic.

This helper is intentionally not a chemistry provider. It samples
AlphaMELTS melt-oxide activities at fixed melt pressures, then evaluates the
builtin analytical vapor-pressure equations with those activities across a
pO2 grid. The output is a diagnostic dictionary only.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from engines.alphamelts.domain import (
    AlphaMELTSDomainGate,
    canonical_melt_oxide_activity_name,
)
from engines.builtin.vapor_pressure import (
    COEFF_BLOCK_ANTOINE,
    FIT_TARGET_STANDARD_REACTION,
    VaporPressureComputationError,
    _ELLINGHAM_THERMO,
    _is_noncertifying_pseudo_vapor_pressure_runtime,
    _range_tuple,
    vapor_pressure_antoine_coefficients,
    vapor_pressure_source_label,
    vapor_pressure_valid_range_K,
)
from engines.domain_reason import OutOfDomainReason, reason_value
from simulator.chemistry.ellingham_thermo import (
    ELLINGHAM_AUTHORITY_LIMIT_FLAG,
    ellingham_authority_diagnostic,
    ellingham_delta_g_kj_per_mol_o2,
    ellingham_fit_extrapolation,
    ellingham_fit_range_K,
    ellingham_stoichiometry,
)
from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR
from simulator.physical_constants import GAS_CONSTANT

ActivitySource = Callable[..., Any]

PRIMARY_ACTIVITY_PRESSURE_BAR = 1.0
COMPARISON_ACTIVITY_PRESSURE_BAR = 0.1

# The 2026-06-03 pressure sweep found identical melt activities while only the
# vapor changed. A 0.1% relative gate is deliberately loose versus numerical
# roundoff but tight enough to catch any pressure-dependent activity model.
PRESSURE_INSENSITIVITY_REL_TOL = 1.0e-3
PRESSURE_INSENSITIVITY_ABS_TOL = 1.0e-8


def alphamelts_activity_volatility_diagnostic(
    *,
    composition_wt_pct: Mapping[str, float],
    pO2_grid_bar: Sequence[float],
    temperature_C: float,
    activity_source: ActivitySource | None = None,
    vapor_pressure_data: Mapping[str, Any] | None = None,
    fO2_log: float = -9.0,
    primary_pressure_bar: float = PRIMARY_ACTIVITY_PRESSURE_BAR,
    comparison_pressure_bar: float = COMPARISON_ACTIVITY_PRESSURE_BAR,
    pressure_rel_tol: float = PRESSURE_INSENSITIVITY_REL_TOL,
    pressure_abs_tol: float = PRESSURE_INSENSITIVITY_ABS_TOL,
    vacuum_floor_bar: float = DEFAULT_VACUUM_FLOOR_BAR,
) -> dict[str, Any]:
    """Return diagnostic vapor pressures from AlphaMELTS melt activities.

    ``activity_source`` is dependency-injected so default unit tests never run
    real AlphaMELTS. It must accept keyword arguments ``temperature_C``,
    ``pressure_bar``, ``composition_wt_pct`` and ``fO2_log`` and return either
    an ``EquilibriumResult``-like object or a mapping with
    ``diagnostic_oxide_activities`` or exact oxide-labeled
    ``activity_coefficients``/``activities``. Endmember/component labels are
    not converted into oxide activities.
    """

    comp_wt = _finite_positive_mapping(composition_wt_pct)
    pO2_values = _normalise_pO2_grid(pO2_grid_bar)
    source = activity_source or alphamelts_equilibrium_activity_source
    vapor_data = dict(vapor_pressure_data or _load_default_vapor_pressure_data())

    domain = _alphamelts_domain_diagnostic(
        composition_wt_pct=comp_wt,
        temperature_C=temperature_C,
        pressure_bar=primary_pressure_bar,
    )

    primary_raw = source(
        temperature_C=float(temperature_C),
        pressure_bar=float(primary_pressure_bar),
        composition_wt_pct=comp_wt,
        fO2_log=float(fO2_log),
    )
    comparison_raw = source(
        temperature_C=float(temperature_C),
        pressure_bar=float(comparison_pressure_bar),
        composition_wt_pct=comp_wt,
        fO2_log=float(fO2_log),
    )
    primary = _coerce_activity_sample(primary_raw)
    comparison = _coerce_activity_sample(comparison_raw)

    gate = _pressure_insensitivity_gate(
        primary["activity_coefficients"],
        comparison["activity_coefficients"],
        rel_tol=pressure_rel_tol,
        abs_tol=pressure_abs_tol,
        primary_pressure_bar=primary_pressure_bar,
        comparison_pressure_bar=comparison_pressure_bar,
    )

    source_limits = _source_extrapolation_limits(primary, comparison)
    extrapolation_limited = bool(
        domain["extrapolation_limited"] or source_limits
    )
    if gate["status"] != "ok":
        return {
            "status": "falsification_flagged",
            "diagnostic_only": True,
            "extrapolation_limited": extrapolation_limited,
            "alphamelts_domain": domain,
            "activity_source_extrapolation_limits": source_limits,
            "activity_pressure_gate": gate,
            "activity_samples": {
                "primary": primary,
                "comparison": comparison,
            },
            "warnings": tuple(
                list(primary.get("warnings", ()))
                + list(comparison.get("warnings", ()))
                + ["pressure_insensitivity_gate_falsified"]
            ),
        }

    activities = dict(primary["activity_coefficients"])
    if not activities:
        return {
            "status": "no_activities",
            "diagnostic_only": True,
            "extrapolation_limited": True,
            "alphamelts_domain": domain,
            "activity_source_extrapolation_limits": source_limits,
            "activity_pressure_gate": gate,
            "activity_samples": {
                "primary": primary,
                "comparison": comparison,
            },
            "warnings": tuple(
                list(primary.get("warnings", ()))
                + ["AlphaMELTS activity source returned no melt activities"]
            ),
        }

    grid = []
    vapor_model_limits: dict[str, Any] = {}
    for pO2_bar in pO2_values:
        computed = _analytical_vapor_pressures_from_activities(
            vapor_pressure_data=vapor_data,
            temperature_C=float(temperature_C),
            pO2_bar=pO2_bar,
            melt_oxide_activities=activities,
            composition_wt_pct=comp_wt,
            vacuum_floor_bar=float(vacuum_floor_bar),
        )
        vapor_model_limits.update(computed["extrapolated_beyond_valid_range_K"])
        grid.append(
            {
                "pO2_bar": pO2_bar,
                "species": computed["species"],
            }
        )

    ellingham_authority = ellingham_authority_diagnostic(
        {
            key: value
            for key, value in vapor_model_limits.items()
            if isinstance(value, Mapping)
            and value.get("authority_flag") == ELLINGHAM_AUTHORITY_LIMIT_FLAG
        },
        consumer="alphamelts-volatility-diagnostic",
    )
    extrapolation_limited = bool(
        extrapolation_limited
        or vapor_model_limits
        or ellingham_authority.get(ELLINGHAM_AUTHORITY_LIMIT_FLAG)
    )

    return {
        "status": "ok",
        "diagnostic_only": True,
        "extrapolation_limited": extrapolation_limited,
        "alphamelts_domain": domain,
        "activity_source_extrapolation_limits": source_limits,
        "activity_pressure_gate": gate,
        "activity_sample_pressure_bar": float(primary_pressure_bar),
        "activity_comparison_pressure_bar": float(comparison_pressure_bar),
        "melt_oxide_activities": activities,
        "pO2_grid_bar": tuple(pO2_values),
        "grid": tuple(grid),
        "vapor_model_extrapolated_beyond_valid_range_K": vapor_model_limits,
        "ellingham_authority": ellingham_authority,
        "activity_samples": {
            "primary": primary,
            "comparison": comparison,
        },
        "warnings": tuple(
            list(domain.get("warnings", ()))
            + list(primary.get("warnings", ()))
            + list(comparison.get("warnings", ()))
        ),
    }


def alphamelts_equilibrium_activity_source(
    *,
    temperature_C: float,
    pressure_bar: float,
    composition_wt_pct: Mapping[str, float],
    fO2_log: float = -9.0,
) -> Any:
    """Run the real AlphaMELTS backend and return its equilibrium result."""

    from simulator.melt_backend.alphamelts import AlphaMELTSBackend

    backend = AlphaMELTSBackend()
    if not backend.initialize({}):
        return {
            "status": "unavailable",
            "backend_status_reason": OutOfDomainReason.BACKEND_UNAVAILABLE.value,
            "warnings": ("AlphaMELTS backend unavailable",),
            "activity_coefficients": {},
        }
    return backend.equilibrate(
        temperature_C=float(temperature_C),
        composition_kg=dict(composition_wt_pct),
        fO2_log=float(fO2_log),
        pressure_bar=float(pressure_bar),
        subprocess_run_mode="isothermal",
    )


def _analytical_vapor_pressures_from_activities(
    *,
    vapor_pressure_data: Mapping[str, Any],
    temperature_C: float,
    pO2_bar: float,
    melt_oxide_activities: Mapping[str, float],
    composition_wt_pct: Mapping[str, float],
    vacuum_floor_bar: float = DEFAULT_VACUUM_FLOOR_BAR,
) -> dict[str, Any]:
    T_K = float(temperature_C) + 273.15
    if T_K < 400.0:
        return {
            "species": {},
            "extrapolated_beyond_valid_range_K": {},
        }
    pO2 = _finite_positive("pO2_bar", pO2_bar)
    floor_bar = _finite_positive("vacuum_floor_bar", vacuum_floor_bar)
    activities = _canonical_activity_mapping(melt_oxide_activities)
    comp_wt = _finite_positive_mapping(composition_wt_pct)
    by_species: dict[str, dict[str, Any]] = {}
    extrapolations: dict[str, Any] = {}

    for species in _ELLINGHAM_THERMO:
        sp_data = (vapor_pressure_data.get("metals", {}) or {}).get(species, {}) or {}
        if not sp_data or str(sp_data.get("consumer_status", "")).lower() == "inactive":
            continue
        parent_oxide = str(sp_data.get("parent_oxide", "") or "")
        if not parent_oxide:
            continue
        coefficient_block = None
        antoine, coefficient_block = vapor_pressure_antoine_coefficients(
            sp_data,
            temperature_K=T_K,
        )
        if _is_noncertifying_pseudo_vapor_pressure_runtime(
            species,
            sp_data,
            temperature_K=T_K,
        ):
            continue
        A = float(antoine.get("A", 0.0) or 0.0)
        B = float(antoine.get("B", 0.0) or 0.0)
        C = float(antoine.get("C", 0.0) or 0.0)
        if not (A > 0.0 and T_K > 300.0):
            continue
        activity = float(activities.get(parent_oxide, 0.0) or 0.0)
        wt_activity = max(0.0, float(comp_wt.get(parent_oxide, 0.0) or 0.0) / 100.0)
        if activity <= 0.0 and wt_activity <= 0.0:
            continue
        valid_range = vapor_pressure_valid_range_K(
            sp_data,
            coefficient_block,
            temperature_K=T_K,
        )
        if valid_range and len(valid_range) == 2:
            valid_low = float(valid_range[0])
            valid_high = float(valid_range[1])
            if T_K < valid_low or T_K > valid_high:
                extrapolations[species] = {
                    "temperature_K": T_K,
                    "valid_range_K": (valid_low, valid_high),
                        "authority_status": "extrapolation_limited",
                }
        P_reference_Pa = _pow10(A - B / (T_K + C), species=species)
        if str(sp_data.get("fit_target", "") or "") == FIT_TARGET_STANDARD_REACTION:
            activity_exponent = float(
                sp_data.get("oxide_activity_exponent", 1.0) or 1.0
            )
            pO2_exponent = float(sp_data.get("pO2_exponent", 0.0) or 0.0)
            pO2_reference_bar = max(
                1e-30,
                float(sp_data.get("pO2_reference_bar", 1.0) or 1.0),
            )

            def standard_pressure(oxide_value: float) -> tuple[float, float]:
                if oxide_value <= 0.0:
                    return 0.0, 0.0
                activity_factor = max(oxide_value, 0.0) ** activity_exponent
                pressure = P_reference_Pa * activity_factor
                if pO2_exponent:
                    pressure *= (pO2 / pO2_reference_bar) ** pO2_exponent
                return _finite_number(
                    pressure,
                    species=species,
                    field="P_eq_standard_reaction",
                ), activity_factor

            P_eq_Pa, activity_factor = standard_pressure(activity)
            P_eq_wt_Pa, wt_activity_factor = standard_pressure(wt_activity)
            by_species[species] = _species_payload(
                parent_oxide=parent_oxide,
                P_eq_Pa=P_eq_Pa,
                P_eq_wt_fraction_Pa=P_eq_wt_Pa,
                melt_oxide_activity=activity,
                wt_fraction_activity=wt_activity,
                activity_factor=activity_factor,
                wt_fraction_activity_factor=wt_activity_factor,
                P_reference_Antoine_Pa=P_reference_Pa,
                pO2_bar=pO2,
                source=vapor_pressure_source_label(
                    "alphamelts_activity_diagnostic",
                    sp_data,
                    coefficient_block=coefficient_block,
                    temperature_K=T_K,
                    extrapolated=str(species) in extrapolations,
                ),
            )
            continue
        ellingham_extrapolation = ellingham_fit_extrapolation(
            T_K,
            species=species,
            consumer="alphamelts-volatility-diagnostic",
        )
        if ellingham_extrapolation is not None:
            extrapolations[species] = ellingham_extrapolation
        P_eq_Pa, metal_activity = _metal_vapor_pressure_Pa(
            species=species,
            T_K=T_K,
            pO2_bar=pO2,
            oxide_activity=activity,
            P_reference_Pa=P_reference_Pa,
        )
        P_eq_wt_Pa, metal_activity_wt = _metal_vapor_pressure_Pa(
            species=species,
            T_K=T_K,
            pO2_bar=pO2,
            oxide_activity=wt_activity,
            P_reference_Pa=P_reference_Pa,
        )
        by_species[species] = _species_payload(
            parent_oxide=parent_oxide,
            P_eq_Pa=P_eq_Pa,
            P_eq_wt_fraction_Pa=P_eq_wt_Pa,
            melt_oxide_activity=activity,
            wt_fraction_activity=wt_activity,
            activity_factor=metal_activity,
            wt_fraction_activity_factor=metal_activity_wt,
            P_reference_Antoine_Pa=P_reference_Pa,
            pO2_bar=pO2,
            source_label=_diagnostic_source_label(
                species,
                sp_data,
                coefficient_block=coefficient_block,
                temperature_K=T_K,
                extrapolated=species in extrapolations,
            ),
        )

    for species, data in (vapor_pressure_data.get("oxide_vapors", {}) or {}).items():
        data = data or {}
        antoine = data.get("antoine", {}) or {}
        A = float(antoine.get("A", 0.0) or 0.0)
        B = float(antoine.get("B", 0.0) or 0.0)
        C = float(antoine.get("C", 0.0) or 0.0)
        if not A > 0.0:
            continue
        parent_oxide = str(data.get("parent_oxide", "") or "")
        if not parent_oxide:
            continue
        activity = float(activities.get(parent_oxide, 0.0) or 0.0)
        wt_activity = max(0.0, float(comp_wt.get(parent_oxide, 0.0) or 0.0) / 100.0)
        if activity <= 0.0 and wt_activity <= 0.0:
            continue
        valid_range = _range_tuple(data.get("valid_range_K", [0.0, 9999.0]))
        if valid_range is not None:
            valid_low, valid_high = valid_range
            if T_K < valid_low:
                continue
            if T_K > valid_high:
                allowed_range = _range_tuple(data.get("extrapolation_allowed_range_K"))
                if allowed_range is None:
                    raise VaporPressureComputationError(
                        "oxide_vapor_pressure_out_of_validated_range: "
                        f"species={species} temperature_K={T_K:.2f} "
                        f"valid_range_K=[{valid_low:g}, {valid_high:g}] "
                        "extrapolation_allowed_range_K=absent"
                    )
                allowed_low, allowed_high = allowed_range
                if T_K < allowed_low or T_K > allowed_high:
                    raise VaporPressureComputationError(
                        "oxide_vapor_pressure_out_of_validated_range: "
                        f"species={species} temperature_K={T_K:.2f} "
                        f"valid_range_K=[{valid_low:g}, {valid_high:g}] "
                        "extrapolation_allowed_range_K="
                        f"[{allowed_low:g}, {allowed_high:g}]"
                    )
                extrapolations[str(species)] = {
                    "temperature_K": T_K,
                    "valid_range_K": (valid_low, valid_high),
                    "extrapolation_allowed_range_K": (allowed_low, allowed_high),
                    "authority_status": "extrapolation_limited",
                }
        exponent = float(data.get("oxide_activity_exponent", 1.0) or 1.0)
        P_reference_Pa = _pow10(A - B / (T_K + C), species=str(species))
        P_eq_Pa, activity_factor = _oxide_vapor_pressure_Pa(
            species=str(species),
            data=data,
            P_reference_Pa=P_reference_Pa,
            oxide_activity=activity,
            activity_exponent=exponent,
            pO2_bar=pO2,
            vacuum_floor_bar=floor_bar,
        )
        P_eq_wt_Pa, wt_activity_factor = _oxide_vapor_pressure_Pa(
            species=str(species),
            data=data,
            P_reference_Pa=P_reference_Pa,
            oxide_activity=wt_activity,
            activity_exponent=exponent,
            pO2_bar=pO2,
            vacuum_floor_bar=floor_bar,
        )
        by_species[str(species)] = _species_payload(
            parent_oxide=parent_oxide,
            P_eq_Pa=P_eq_Pa,
            P_eq_wt_fraction_Pa=P_eq_wt_Pa,
            melt_oxide_activity=activity,
            wt_fraction_activity=wt_activity,
            activity_factor=activity_factor,
            wt_fraction_activity_factor=wt_activity_factor,
            P_reference_Antoine_Pa=P_reference_Pa,
            pO2_bar=pO2,
            source_label=_diagnostic_source_label(
                str(species),
                data,
                coefficient_block=COEFF_BLOCK_ANTOINE,
                temperature_K=T_K,
                extrapolated=str(species) in extrapolations,
            ),
        )

    return {
        "species": by_species,
        "extrapolated_beyond_valid_range_K": extrapolations,
    }


def _metal_vapor_pressure_Pa(
    *,
    species: str,
    T_K: float,
    pO2_bar: float,
    oxide_activity: float,
    P_reference_Pa: float,
) -> tuple[float, float]:
    if oxide_activity <= 0.0:
        return 0.0, 0.0
    n_M, n_ox = ellingham_stoichiometry(species)
    dG_f_kJ = ellingham_delta_g_kj_per_mol_o2(species, T_K)
    try:
        K_decomp = math.exp(dG_f_kJ * 1000.0 / (GAS_CONSTANT * T_K))
    except OverflowError as exc:
        raise VaporPressureComputationError(
            f"vapor_pressure_nonfinite: species={species} field=K_decomp"
        ) from exc
    numerator = _finite_number(
        K_decomp * (oxide_activity ** n_ox) / pO2_bar,
        species=species,
        field="metal_activity_numerator",
    )
    if numerator <= 0.0:
        return 0.0, 0.0
    metal_activity = min(
        _finite_number(
            numerator ** (1.0 / n_M),
            species=species,
            field="metal_activity",
        ),
        1.0,
    )
    return (
        _finite_number(
            metal_activity * P_reference_Pa,
            species=species,
            field="P_eq_Pa",
        ),
        metal_activity,
    )


def _oxide_vapor_pressure_Pa(
    *,
    species: str,
    data: Mapping[str, Any],
    P_reference_Pa: float,
    oxide_activity: float,
    activity_exponent: float,
    pO2_bar: float,
    vacuum_floor_bar: float,
) -> tuple[float, float]:
    if oxide_activity <= 0.0:
        return 0.0, 0.0
    activity_factor = max(oxide_activity, 0.0) ** activity_exponent
    P_eq_Pa = _finite_number(
        P_reference_Pa * activity_factor,
        species=species,
        field="P_eq_activity",
    )
    pO2_exponent = float(data.get("pO2_exponent", 0.0) or 0.0)
    if pO2_exponent:
        pO2_reference_bar = max(
            1.0e-30,
            float(data.get("pO2_reference_bar", 1.0) or 1.0),
        )
        scale = (pO2_bar / pO2_reference_bar) ** pO2_exponent
        P_eq_Pa = _finite_number(
            P_eq_Pa * scale,
            species=species,
            field="P_eq_pO2",
        )
        activity_factor *= scale
    elif species == "SiO" and pO2_bar > vacuum_floor_bar:
        suppression = math.sqrt(vacuum_floor_bar / pO2_bar)
        P_eq_Pa = _finite_number(
            P_eq_Pa * suppression,
            species=species,
            field="P_eq_suppressed",
        )
        activity_factor *= suppression
    return P_eq_Pa, activity_factor


def _species_payload(
    *,
    parent_oxide: str,
    P_eq_Pa: float,
    P_eq_wt_fraction_Pa: float,
    melt_oxide_activity: float,
    wt_fraction_activity: float,
    activity_factor: float,
    wt_fraction_activity_factor: float,
    P_reference_Antoine_Pa: float,
    pO2_bar: float,
    source_label: str,
) -> dict[str, Any]:
    return {
        "P_eq_Pa": P_eq_Pa,
        "P_eq_wt_fraction_Pa": P_eq_wt_fraction_Pa,
        "P_eq_delta_vs_wt_fraction_Pa": P_eq_Pa - P_eq_wt_fraction_Pa,
        "P_eq_ratio_vs_wt_fraction": _safe_ratio(P_eq_Pa, P_eq_wt_fraction_Pa),
        "parent_oxide": parent_oxide,
        "melt_oxide_activity": melt_oxide_activity,
        "wt_fraction_activity": wt_fraction_activity,
        "activity_ratio_vs_wt_fraction": _safe_ratio(
            melt_oxide_activity,
            wt_fraction_activity,
        ),
        "activity_factor": activity_factor,
        "wt_fraction_activity_factor": wt_fraction_activity_factor,
        "P_reference_Antoine_Pa": P_reference_Antoine_Pa,
        "pO2_bar": pO2_bar,
        "source_label": source_label,
    }


def _pressure_insensitivity_gate(
    primary: Mapping[str, float],
    comparison: Mapping[str, float],
    *,
    rel_tol: float,
    abs_tol: float,
    primary_pressure_bar: float,
    comparison_pressure_bar: float,
) -> dict[str, Any]:
    primary_clean = _canonical_activity_mapping(primary)
    comparison_clean = _canonical_activity_mapping(comparison)
    all_oxides = sorted(set(primary_clean) | set(comparison_clean))
    mismatches: dict[str, dict[str, Any]] = {}
    for oxide in all_oxides:
        if oxide not in primary_clean or oxide not in comparison_clean:
            mismatches[oxide] = {
                "primary": primary_clean.get(oxide),
                "comparison": comparison_clean.get(oxide),
                "reason": "activity_missing_at_one_pressure",
            }
            continue
        a_primary = float(primary_clean[oxide])
        a_comparison = float(comparison_clean[oxide])
        delta = abs(a_primary - a_comparison)
        tolerance = max(abs_tol, rel_tol * max(abs(a_primary), abs(a_comparison)))
        if delta > tolerance:
            mismatches[oxide] = {
                "primary": a_primary,
                "comparison": a_comparison,
                "abs_delta": delta,
                "tolerance": tolerance,
                "relative_delta": _safe_ratio(delta, max(abs(a_primary), abs(a_comparison))),
            }
    return {
        "status": "falsified" if mismatches else "ok",
        "primary_pressure_bar": float(primary_pressure_bar),
        "comparison_pressure_bar": float(comparison_pressure_bar),
        "relative_tolerance": float(rel_tol),
        "absolute_tolerance": float(abs_tol),
        "basis": (
            "2026-06-03 AlphaMELTS finding: melt activities were pressure-"
            "insensitive over the diagnostic low-pressure sweep; only vapor "
            "pressures changed. Gate tolerates 0.1% relative / 1e-8 absolute "
            "numeric drift and flags larger pressure dependence."
        ),
        "mismatches": mismatches,
    }


def _alphamelts_domain_diagnostic(
    *,
    composition_wt_pct: Mapping[str, float],
    temperature_C: float,
    pressure_bar: float,
) -> dict[str, Any]:
    valid, warnings, reason = AlphaMELTSDomainGate.validate_with_reason(
        composition_wt_pct
    )
    crash_point = {
        "temperature_C": float(temperature_C),
        "pressure_bar": float(pressure_bar),
        "composition_wt_pct": _finite_positive_mapping(composition_wt_pct),
    }
    status = "diagnostic"
    reason_code = reason_value(reason)
    if not valid:
        status = "extrapolation_limited"
    return {
        "status": status,
        "extrapolation_limited": not valid,
        "backend_status_reason": reason_code,
        "warnings": tuple(warnings),
        "out_of_domain_crash_point": crash_point if not valid else None,
    }


def _source_extrapolation_limits(*samples: Mapping[str, Any]) -> dict[str, Any]:
    limits: dict[str, Any] = {}
    for idx, sample in enumerate(samples):
        diagnostics = dict(sample.get("diagnostics", {}) or {})
        reason = reason_value(
            sample.get("backend_status_reason")
            or diagnostics.get("backend_status_reason")
        )
        vapor_pressure_status = reason_value(
            diagnostics.get("vapor_pressure_backend_status")
        )
        vapor_pressure_degraded = (
            vapor_pressure_status in {"fallback", "not_attempted"}
            or diagnostics.get("authoritative_for_requested_vapor_pressure") is False
        )
        if (
            sample.get("status") == "out_of_domain"
            or diagnostics.get("authoritative_for_requested_conditions") is False
            or diagnostics.get("operating_point_clamped")
        ):
            limits[f"activity_sample_{idx}"] = {
                "backend_status_reason": reason or "clamped_operating_point",
                "authority_status": "extrapolation_limited",
                "diagnostics": diagnostics,
            }
        if vapor_pressure_degraded:
            status = vapor_pressure_status or "fallback"
            limits[f"vapor_pressure_sample_{idx}"] = {
                "vapor_pressure_backend_status": status,
                "vapor_pressure_backend_status_reason": reason_value(
                    diagnostics.get("vapor_pressure_backend_status_reason")
                ),
                "vapor_pressure_fallback_source": reason_value(
                    diagnostics.get("vapor_pressure_fallback_source")
                ),
                "authority_status": "vapor_pressure_facet_degraded",
                "diagnostic_only": True,
                "diagnostics": diagnostics,
            }
    return limits


def _coerce_activity_sample(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        diagnostics = dict(value.get("diagnostics", {}) or {})
        raw_activities = (
            value.get("diagnostic_oxide_activities")
            or diagnostics.get("diagnostic_oxide_activities")
            or value.get("melt_oxide_activities")
            or value.get("activity_coefficients")
            or value.get("activities")
            or {}
        )
        return {
            "status": str(value.get("status", "ok")),
            "backend_status_reason": reason_value(
                value.get("backend_status_reason")
                or diagnostics.get("backend_status_reason")
            ),
            "activity_coefficients": _canonical_activity_mapping(raw_activities),
            "warnings": tuple(str(w) for w in value.get("warnings", ()) or ()),
            "diagnostics": diagnostics,
        }
    diagnostics = dict(getattr(value, "diagnostics", {}) or {})
    raw_activities = (
        diagnostics.get("diagnostic_oxide_activities")
        or getattr(value, "diagnostic_oxide_activities", None)
        or getattr(value, "melt_oxide_activities", None)
        or {}
    )
    return {
        "status": str(getattr(value, "status", "ok")),
        "backend_status_reason": reason_value(
            getattr(value, "backend_status_reason", None)
            or diagnostics.get("backend_status_reason")
        ),
        "activity_coefficients": _canonical_activity_mapping(raw_activities),
        "warnings": tuple(str(w) for w in getattr(value, "warnings", ()) or ()),
        "diagnostics": diagnostics,
    }


def _canonical_activity_mapping(values: Mapping[str, float]) -> dict[str, float]:
    result: dict[str, float] = {}
    for raw_name, raw_value in dict(values or {}).items():
        oxide = canonical_melt_oxide_activity_name(raw_name)
        if oxide is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if value > 0.0 and math.isfinite(value):
            result[oxide] = value
    return result


def _finite_positive_mapping(values: Mapping[str, float]) -> dict[str, float]:
    result: dict[str, float] = {}
    for key, value in dict(values or {}).items():
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric > 0.0 and math.isfinite(numeric):
            result[str(key)] = numeric
    return result


def _normalise_pO2_grid(values: Sequence[float]) -> tuple[float, ...]:
    grid = tuple(_finite_positive("pO2_bar", value) for value in values)
    if not grid:
        raise ValueError("pO2_grid_bar must contain at least one value")
    return grid


def _finite_positive(name: str, value: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric, got {value!r}") from exc
    if not math.isfinite(numeric) or numeric <= 0.0:
        raise ValueError(f"{name} must be finite and > 0, got {value!r}")
    return numeric


def _finite_number(value: float, *, species: str, field: str) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise VaporPressureComputationError(
            "vapor_pressure_nonfinite: "
            f"species={species} field={field} value={value!r}"
        )
    return numeric


def _pow10(log10_value: float, *, species: str) -> float:
    try:
        return _finite_number(10.0 ** float(log10_value), species=species, field="P_reference_Pa")
    except OverflowError as exc:
        raise VaporPressureComputationError(
            f"vapor_pressure_nonfinite: species={species} field=P_reference_Pa"
        ) from exc


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0.0:
        return None
    return float(numerator) / float(denominator)


def _diagnostic_source_label(
    species: str,
    data: Mapping[str, Any],
    *,
    coefficient_block: str | None,
    temperature_K: float,
    extrapolated: bool,
) -> str:
    base = vapor_pressure_source_label(
        "builtin_authoritative",
        data,
        coefficient_block=coefficient_block,
        temperature_K=temperature_K,
        authority_limited_by_ellingham_fit_range=extrapolated,
    )
    status = "extrapolation_limited" if extrapolated else "diagnostic"
    return f"alphamelts_activity_{status}:{species}:{base}"


def _load_default_vapor_pressure_data() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    with (root / "data" / "vapor_pressures.yaml").open() as handle:
        return yaml.safe_load(handle) or {}


__all__ = (
    "COMPARISON_ACTIVITY_PRESSURE_BAR",
    "PRESSURE_INSENSITIVITY_ABS_TOL",
    "PRESSURE_INSENSITIVITY_REL_TOL",
    "PRIMARY_ACTIVITY_PRESSURE_BAR",
    "alphamelts_activity_volatility_diagnostic",
    "alphamelts_equilibrium_activity_source",
)
