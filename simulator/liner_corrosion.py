"""Pure liner-interface contamination and first-liquid diagnostics.

The model reports equilibrium melt onset and bounded campaign counts. It does
not model dissolution or corrosion kinetics.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace
import math
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable
import warnings

import yaml

from simulator.accounting.formulas import parse_formula, resolve_species_formula
from simulator.melt_backend.liquidus import (
    DEFAULT_LIQUIDUS_FINDER_MAX_T_C,
    LIQUIDUS_LIQUID_EPSILON,
    LIQUIDUS_SOLID_EPSILON,
)
from simulator.physical_constants import (
    CELSIUS_TO_KELVIN_OFFSET,
    PA_PER_BAR,
    PA_PER_MBAR,
)
from simulator.scalar_boundary import is_declared_real_scalar


EXTRACTS_DIR = Path(__file__).resolve().parents[1] / "data" / "literature" / "extracts"

DEPOSIT_OXIDE_BY_SPECIES = {
    "Na": "Na2O",
    "K": "K2O",
    "Fe": "FeO",
    "Mg": "MgO",
    "SiO": "SiO2",
}

DEPOSIT_OXIDATION_STATE_FLAG = "deposit_oxidation_state_assumed"
REACTED_LAYER_DEPTH_FLAG = "reacted_layer_depth_assumed"
TEMPERATURE_MARGIN_FLAG = "temperature_margin_assumed"
HORIZON_FLAG = "campaign_horizon_declared"
ENAMEL_GLASS_STATE_FLAG = "enamel_glass_state_assumed"
DISSOLUTION_KINETICS_FLAG = "equilibrium_dissolution_no_kinetics"
CORROSION_NO_RATE_FLAG = "corrosion_runaway_no_rate_model"
REACTIVE_STICKING_FLAG = "reactive_wall_sticking_upper_bound"
ENGINE_OUT_OF_DOMAIN_FLAG = "liner_first_melt_engine_out_of_domain"
ELEMENT_CLOSURE_FLAG = "engine_phase_closure_failed"
ELEMENT_CLOSURE_UNVERIFIED_FLAG = "engine_phase_closure_unverified"

MAGEMIN_ENGINE_ID = "magemin:ig"
MAGEMIN_REQUEST_CACHE_VERSION = 1  # Hand-bump when adapter-visible settings change.
MAGEMIN_FO2_LOG = -9.0  # Fixed request setting; included in the request cache key.
MAGEMIN_CACHE_DECIMAL_PLACES = 6
MAGEMIN_PHI_CURVE_INTERVALS = 30
ELEMENT_CLOSURE_REL_TOLERANCE = 1.0e-3
TIER_B_MELT_SCAN_MIN_C = 500.0
TIER_B_MELT_SCAN_MAX_C = 1600.0
TIER_B_MELT_SCAN_STEP_C = 25.0
TIER_B_COMPOSITION_NEIGHBOUR_DELTA_WT_PCT = 0.01
# d-419 first-liquid output flags identify engine eutectics, closure, and resolution.
ENGINE_MINOR_PHASE_EUTECTIC_FLAG = "engine_minor_phase_eutectic"
PHASE_CLOSURE_UNVERIFIED_FLAG = "phase_closure_unverified"
DEPOSIT_MELT_BELOW_RESOLUTION_FLAG = "deposit_melt_below_resolution"
ENGINE_BULK_MELT_REFERENCE_FALLBACK_FLAG = "engine_first_liquid_residual"
ONSET_BELOW_SCAN_RANGE_FLAG = "onset_below_scan_range"
ENGINE_COMPOSITION_SENSITIVE_FLAG = "engine_composition_sensitive"


class LinerCorrosionRefusal(ValueError):
    """Typed refusal from the liner-chemistry diagnostic."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = str(reason)
        self.detail = str(detail or reason)
        super().__init__(self.detail)

    def as_dict(self) -> dict[str, str]:
        return {"reason": self.reason, "detail": self.detail}


@dataclass(frozen=True)
class LinerRatingResolution:
    rating_temperature_C: float | None
    cap_temperature_C: float | None
    cap_source: str | None
    onset_at_service_temperature: bool | None


LiquidFractionSampler = Callable[
    [Mapping[str, float], float, float | None], float
]


@runtime_checkable
class CurveLiquidFractionSampler(Protocol):
    """Sampler able to reuse a cached phi(f) curve for a deposit mix line."""

    def __call__(
        self,
        composition_wt_pct: Mapping[str, float],
        temperature_C: float,
        pressure_bar: float | None,
    ) -> float: ...

    def curve(
        self,
        liner_composition_wt_pct: Mapping[str, Any],
        deposit_kg_by_species: Mapping[str, Any],
        *,
        temperature_C: float,
        pressure_bar: float,
    ) -> Any: ...


def _finite_real(value: Any, field: str) -> float:
    if not is_declared_real_scalar(value):
        raise LinerCorrosionRefusal("invalid_liner_input", f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise LinerCorrosionRefusal("invalid_liner_input", f"{field} must be finite")
    return result


def _positive(value: Any, field: str) -> float:
    result = _finite_real(value, field)
    if result <= 0.0:
        raise LinerCorrosionRefusal("invalid_liner_input", f"{field} must be positive")
    return result


def _resolve_rating_and_onset(
    pure_first_liquid_temperature_C: float | None,
    contaminated_first_liquid_temperature_C: float | None,
    margin_C: float,
    service_temperature_C: float | None,
    *,
    onset_below_scan_range: bool = False,
) -> LinerRatingResolution:
    """Resolve the d-419 margin, optional pure cap, and service onset once."""

    margin = _finite_real(margin_C, "margin_C")
    if margin < 0.0:
        raise LinerCorrosionRefusal("temperature_margin_invalid")
    pure_first_liquid = (
        _finite_real(pure_first_liquid_temperature_C, "pure_first_liquid_temperature_C")
        if pure_first_liquid_temperature_C is not None
        else None
    )
    contaminated_first_liquid = (
        _finite_real(
            contaminated_first_liquid_temperature_C,
            "contaminated_first_liquid_temperature_C",
        )
        if contaminated_first_liquid_temperature_C is not None
        else None
    )
    if onset_below_scan_range and contaminated_first_liquid is not None:
        raise LinerCorrosionRefusal(
            "liner_onset_boundary_invalid",
            "a below-scan onset cannot also have a finite contaminated first-liquid point",
        )
    cap = pure_first_liquid - margin if pure_first_liquid is not None else None
    # If the contaminated excess-melt scan resolves no onset, contamination
    # has not lowered the pure-liner onset. Retain the pure rating T_lit-margin
    # (d-419); the separately flagged below-scan case remains an unresolved
    # lower boundary and must not be assigned this finite rating.
    rating = (
        None
        if onset_below_scan_range
        else (
            contaminated_first_liquid - margin
            if contaminated_first_liquid is not None
            else cap
        )
    )
    if rating is not None and cap is not None:
        rating = min(rating, cap)
    if onset_below_scan_range:
        if service_temperature_C is None:
            raise LinerCorrosionRefusal("liner_service_temperature_missing")
        _finite_real(service_temperature_C, "service_temperature_C")
        onset: bool | None = True
    elif rating is None or service_temperature_C is None:
        onset = None
    else:
        onset = _finite_real(service_temperature_C, "service_temperature_C") >= rating
    return LinerRatingResolution(
        rating_temperature_C=rating,
        cap_temperature_C=cap,
        cap_source=("pure_liner_first_liquid" if pure_first_liquid is not None else None),
        onset_at_service_temperature=onset,
    )


def validate_oxide_composition(composition_wt_pct: Mapping[str, Any]) -> dict[str, float]:
    """Return a checked 100 wt% oxide map without silently normalizing it."""

    if not isinstance(composition_wt_pct, Mapping) or not composition_wt_pct:
        raise LinerCorrosionRefusal("liner_composition_missing")
    result: dict[str, float] = {}
    for raw_oxide, raw_wt_pct in composition_wt_pct.items():
        oxide = str(raw_oxide).strip()
        wt_pct = _finite_real(raw_wt_pct, f"composition_wt_pct[{oxide!r}]")
        if not oxide or wt_pct < 0.0:
            raise LinerCorrosionRefusal("liner_composition_invalid", oxide)
        try:
            formula = parse_formula(oxide)
        except Exception as exc:  # noqa: BLE001 - formula parser has typed internals
            raise LinerCorrosionRefusal(
                "liner_composition_invalid", f"unknown oxide {oxide!r}"
            ) from exc
        if "O" not in formula.elements or len(formula.elements) != 2:
            raise LinerCorrosionRefusal(
                "liner_composition_invalid",
                f"{oxide!r} is not a single-cation oxide formula",
            )
        result[oxide] = result.get(oxide, 0.0) + wt_pct
    total = math.fsum(result.values())
    if not math.isclose(total, 100.0, rel_tol=0.0, abs_tol=0.05):
        raise LinerCorrosionRefusal(
            "liner_composition_invalid",
            f"oxide wt% totals {total:.9g}, expected 100",
        )
    return result


def _positive_oxide_composition(
    composition_wt_pct: Mapping[str, float],
) -> dict[str, float]:
    """Drop zero-weight oxide keys before selecting a cited subsystem."""

    return {
        oxide: wt_pct
        for oxide, wt_pct in composition_wt_pct.items()
        if wt_pct > 0.0
    }


def _checked_deposit(deposit_kg_by_species: Mapping[str, Any]) -> dict[str, float]:
    if not isinstance(deposit_kg_by_species, Mapping):
        raise LinerCorrosionRefusal("deposit_by_species_invalid")
    result: dict[str, float] = {}
    for raw_species, raw_kg in deposit_kg_by_species.items():
        species = str(raw_species).strip()
        if species not in DEPOSIT_OXIDE_BY_SPECIES:
            raise LinerCorrosionRefusal(
                "deposit_species_unmapped", f"no oxide mapping for {species!r}"
            )
        kg = _finite_real(raw_kg, f"deposit_kg_by_species[{species!r}]")
        if kg < 0.0:
            raise LinerCorrosionRefusal("deposit_mass_negative", species)
        result[species] = result.get(species, 0.0) + kg
    return result


def _oxide_equivalent_kg_by_species(
    deposit_kg_by_species: Mapping[str, Any],
) -> dict[str, float]:
    """Convert deposited species mass to the assumed oxide-equivalent mass.

    The oxidation-state assumption adds the oxygen required by the mapped
    oxide. For each mapped formula, oxide moles equal deposited-species moles
    divided by the cation stoichiometric ratio; mass then follows from the two
    parsed formula weights. This is a composition bookkeeping conversion, not
    a modeled oxygen-transfer rate.
    """

    result: dict[str, float] = {}
    for species, kg in _checked_deposit(deposit_kg_by_species).items():
        oxide = DEPOSIT_OXIDE_BY_SPECIES[species]
        species_formula = parse_formula(species)
        oxide_formula = parse_formula(oxide)
        cation_atoms = {
            element: atoms
            for element, atoms in species_formula.atom_moles(1.0).items()
            if element != "O"
        }
        if len(cation_atoms) != 1:
            raise LinerCorrosionRefusal(
                "deposit_oxidation_mapping_invalid", species
            )
        cation = next(iter(cation_atoms))
        oxide_cation_count = float(oxide_formula.atom_moles(1.0).get(cation, 0.0))
        species_cation_count = float(cation_atoms[cation])
        oxide_molar_mass = oxide_formula.molar_mass_g_per_mol()
        species_molar_mass = species_formula.molar_mass_g_per_mol()
        if (
            oxide_cation_count <= 0.0
            or species_molar_mass is None
            or oxide_molar_mass is None
        ):
            raise LinerCorrosionRefusal(
                "deposit_oxidation_mapping_invalid", species
            )
        oxide_moles_per_species_mole = species_cation_count / oxide_cation_count
        mass_ratio = (
            float(oxide_molar_mass)
            * oxide_moles_per_species_mole
            / float(species_molar_mass)
        )
        result[species] = kg * mass_ratio
    return result


def deposit_oxide_equivalent_mass_by_oxide(
    deposit_kg_by_species: Mapping[str, Any],
) -> dict[str, float]:
    """Return cumulative deposit mass by assumed oxide, in kg."""

    masses_by_species = _oxide_equivalent_kg_by_species(deposit_kg_by_species)
    result: dict[str, float] = {}
    for species, mass_kg in masses_by_species.items():
        oxide = DEPOSIT_OXIDE_BY_SPECIES[species]
        result[oxide] = result.get(oxide, 0.0) + mass_kg
    return dict(sorted(result.items()))


def deposit_oxide_composition(
    deposit_kg_by_species: Mapping[str, Any],
) -> dict[str, float]:
    """Convert supplied deposit masses to an oxide wt% mixture.

    Oxidation states are assumptions from the t-1816 input contract: Na->Na2O,
    K->K2O, Fe->FeO, Mg->MgO and SiO->SiO2. No unknown species is coerced.
    """

    checked = _oxide_equivalent_kg_by_species(deposit_kg_by_species)
    oxide_kg: dict[str, float] = {}
    for species, kg in checked.items():
        if kg <= 0.0:
            continue
        oxide = DEPOSIT_OXIDE_BY_SPECIES[species]
        oxide_kg[oxide] = oxide_kg.get(oxide, 0.0) + kg
    total = math.fsum(oxide_kg.values())
    if total <= 0.0:
        return {}
    return {oxide: kg / total * 100.0 for oxide, kg in oxide_kg.items()}


def contamination_mixing_fractions(
    deposit_kg_by_species: Mapping[str, Any],
    *,
    density_kg_m3: float,
    area_m2: float,
    reacted_layer_depth_m: float,
) -> dict[str, float]:
    """Return deposit mass fractions in the reacted liner layer.

    If the initial layer mass is ``m0 = rho*A*h``, then after depositing
    oxide-equivalent masses ``M_i`` (species masses converted by the declared
    oxidation-state mapping) the total is ``m0 + sum(M_i)`` and each deposit
    fraction is ``f_i = M_i/(m0 + sum(M_i))``. Units close because
    kg / ((kg m^-3)(m^2)(m)) = kg/kg. The liner fraction is
    ``1 - sum(f_i) = m0/(m0 + sum(M_i))``. Limits: h->infinity makes every
    f_i->0; a single M_i->infinity makes its f_i->1.
    """

    checked = _oxide_equivalent_kg_by_species(deposit_kg_by_species)
    return _mixing_fractions_from_oxide_masses(
        checked,
        density_kg_m3=density_kg_m3,
        area_m2=area_m2,
        reacted_layer_depth_m=reacted_layer_depth_m,
    )


def _mixing_fractions_from_oxide_masses(
    oxide_equivalent_kg_by_species: Mapping[str, float],
    *,
    density_kg_m3: float,
    area_m2: float,
    reacted_layer_depth_m: float,
) -> dict[str, float]:
    """Mix already converted oxide masses with the reacted liner layer."""

    rho = _positive(density_kg_m3, "density_kg_m3")
    area = _positive(area_m2, "area_m2")
    depth = _positive(reacted_layer_depth_m, "reacted_layer_depth_m")
    liner_mass_kg = rho * area * depth
    denominator_kg = liner_mass_kg + math.fsum(oxide_equivalent_kg_by_species.values())
    if not math.isfinite(denominator_kg) or denominator_kg <= 0.0:
        raise LinerCorrosionRefusal("liner_mass_invalid")
    return {
        species: kg / denominator_kg
        for species, kg in oxide_equivalent_kg_by_species.items()
        if kg > 0.0
    }


def contaminated_interface_composition(
    liner_composition_wt_pct: Mapping[str, Any],
    deposit_kg_by_species: Mapping[str, Any],
    *,
    density_kg_m3: float,
    area_m2: float,
    reacted_layer_depth_m: float,
) -> dict[str, Any]:
    """Mass-mix the liner's reacted layer with mapped deposit oxides."""

    liner = validate_oxide_composition(liner_composition_wt_pct)
    deposits = _oxide_equivalent_kg_by_species(deposit_kg_by_species)
    rho = _positive(density_kg_m3, "density_kg_m3")
    area = _positive(area_m2, "area_m2")
    depth = _positive(reacted_layer_depth_m, "reacted_layer_depth_m")
    liner_mass_kg = rho * area * depth
    deposit_total_kg = math.fsum(deposits.values())
    denominator_kg = liner_mass_kg + deposit_total_kg
    if not math.isfinite(denominator_kg) or denominator_kg <= 0.0:
        raise LinerCorrosionRefusal("liner_mass_invalid")

    # Each liner oxide contributes its original wt% times m0/mtotal. Each
    # deposit oxide contributes its mapped species mass divided by mtotal.
    interface: dict[str, float] = {
        oxide: wt_pct * liner_mass_kg / denominator_kg
        for oxide, wt_pct in liner.items()
    }
    for species, kg in deposits.items():
        if kg <= 0.0:
            continue
        oxide = DEPOSIT_OXIDE_BY_SPECIES[species]
        interface[oxide] = interface.get(oxide, 0.0) + 100.0 * kg / denominator_kg
    total_wt_pct = math.fsum(interface.values())
    if not math.isclose(total_wt_pct, 100.0, rel_tol=0.0, abs_tol=1e-9):
        raise LinerCorrosionRefusal(
            "interface_mass_closure_failed", f"mixed wt% totals {total_wt_pct:.12g}"
        )
    return {
        "composition_wt_pct": interface,
        "mixing_fraction_by_species": _mixing_fractions_from_oxide_masses(
            deposits,
            density_kg_m3=rho,
            area_m2=area,
            reacted_layer_depth_m=depth,
        ),
        "flags": [DEPOSIT_OXIDATION_STATE_FLAG, REACTED_LAYER_DEPTH_FLAG],
    }


def reacted_layer_depths(reacted_layer_depth_m: float) -> tuple[float, float, float]:
    """Sensitivity depths h/10, h and 10h; h is always declared by the caller."""

    depth = _positive(reacted_layer_depth_m, "reacted_layer_depth_m")
    return (depth / 10.0, depth, depth * 10.0)


def post_onset_dissolution_ratio(x_star: float) -> float:
    """Equilibrium liner dissolved per kg deposit after onset: (1-x*)/x*."""

    fraction = _finite_real(x_star, "x_star")
    if fraction <= 0.0 or fraction > 1.0:
        raise LinerCorrosionRefusal(
            "dissolution_fraction_invalid", "x_star must be in (0, 1]"
        )
    return (1.0 - fraction) / fraction


def species_aggressiveness_finite_difference(
    liner_composition_wt_pct: Mapping[str, Any],
    deposit_species: str,
    *,
    sampler: LiquidFractionSampler,
    temperature_C: float,
    pressure_bar: float,
) -> dict[str, Any]:
    """Estimate d(liquid fraction)/d(deposit wt%) through Tier B continuity."""

    return _species_aggressiveness_finite_difference_with_reference(
        liner_composition_wt_pct,
        deposit_species,
        sampler=sampler,
        temperature_C=temperature_C,
        pressure_bar=pressure_bar,
        base_reference_profile=None,
    )


def _species_aggressiveness_finite_difference_with_reference(
    liner_composition_wt_pct: Mapping[str, Any],
    deposit_species: str,
    *,
    sampler: LiquidFractionSampler,
    temperature_C: float,
    pressure_bar: float,
    base_reference_profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Use corrected pure profiles for both sides of the one-point difference."""

    species = str(deposit_species).strip()
    oxide = DEPOSIT_OXIDE_BY_SPECIES.get(species)
    if oxide is None:
        raise LinerCorrosionRefusal("deposit_species_unmapped", species)
    base = validate_oxide_composition(liner_composition_wt_pct)
    service_C = _finite_real(temperature_C, "temperature_C")
    pressure = _positive(pressure_bar, "pressure_bar")
    if base_reference_profile is None:
        try:
            base_reference_profile = _tier_b_reference_profile(
                sampler,
                base,
                pressure_bar=pressure,
            )
        except LinerCorrosionRefusal as exc:
            raise LinerCorrosionRefusal(
                exc.reason,
                f"{species} aggressiveness baseline profile refused: {exc.detail}",
            ) from exc

    # A one wt%-point perturbation is a finite-difference unit, not a fitted
    # threshold. Resolve each side's d-421 profile first; if either profile is
    # composition-sensitive, its two agreeing neighbours supply the point
    # sample. This prevents an isolated endmember result from reversing the
    # reported aggressiveness sign.
    perturbed = {key: value * 0.99 for key, value in base.items()}
    perturbed[oxide] = perturbed.get(oxide, 0.0) + 1.0
    try:
        perturbed_reference_profile = _tier_b_reference_profile(
            sampler,
            perturbed,
            pressure_bar=pressure,
        )
    except LinerCorrosionRefusal as exc:
        raise LinerCorrosionRefusal(
            exc.reason,
            f"{species} aggressiveness perturbed profile refused: {exc.detail}",
        ) from exc
    zero_state = _tier_b_sample_at_temperature_with_reference(
        sampler,
        base,
        service_C,
        pressure,
        base_reference_profile,
        context=f"{species} aggressiveness baseline",
    )
    one_point_state = _tier_b_sample_at_temperature_with_reference(
        sampler,
        perturbed,
        service_C,
        pressure,
        perturbed_reference_profile,
        context=f"{species} aggressiveness perturbed composition",
    )
    result: dict[str, Any] = {
        "species": species,
        "oxide": oxide,
        "delta_liquid_fraction_per_wt_pct": one_point_state - zero_state,
    }
    continuity_evidence: dict[str, dict[str, Any]] = {}
    for name, profile in (
        ("baseline_profile", base_reference_profile),
        ("perturbed_profile", perturbed_reference_profile),
    ):
        evidence = profile.get("engine_composition_sensitive")
        if isinstance(evidence, Mapping):
            continuity_evidence[name] = dict(evidence)
    if continuity_evidence:
        result["engine_composition_sensitive"] = continuity_evidence
        result["flags"] = [ENGINE_COMPOSITION_SENSITIVE_FLAG]
    return result


def sticking_deposit_interval(
    deposit_inert_kg_by_species: Mapping[str, Any],
    deposit_reactive_kg_by_species: Mapping[str, Any],
) -> dict[str, Any]:
    """Return inert and reactive upper-bound deposit branches supplied upstream.

    The reactive branch couples alpha_s=1 to P_eq=0. Setting alpha_s alone
    leaves hot-wall Na's driving pressure non-positive, so it cannot create a
    positive reactive deposit rate; the caller must supply D_reactive from the
    coupled branch calculation.
    """

    inert = _checked_deposit(deposit_inert_kg_by_species)
    reactive = _checked_deposit(deposit_reactive_kg_by_species)
    species = set(inert) | set(reactive)
    for item in species:
        low = inert.get(item, 0.0)
        high = reactive.get(item, 0.0)
        if high < low:
            raise LinerCorrosionRefusal(
                "reactive_deposit_below_inert", f"{item}: {high} < {low}"
            )
    return {
        "inert": {species: inert.get(species, 0.0) for species in sorted(species)},
        "reactive_upper_bound": {
            "deposit_kg_by_species": {
                species: reactive.get(species, 0.0) for species in sorted(species)
            },
            "alpha_s": 1.0,
            "P_eq_Pa": 0.0,
            "flags": [REACTIVE_STICKING_FLAG],
        },
    }


def _checked_liquid_fraction(value: Any) -> float:
    fraction = _finite_real(value, "liquid_fraction")
    if fraction < 0.0 or fraction > 1.0:
        raise LinerCorrosionRefusal("liquid_fraction_invalid")
    return fraction


def _sample_liquid_fraction(
    sampler: LiquidFractionSampler,
    composition_wt_pct: Mapping[str, float],
    deposit_kg_by_species: Mapping[str, float],
    liner_composition_wt_pct: Mapping[str, float],
    temperature_C: float,
    pressure_bar: float | None,
) -> float:
    if isinstance(sampler, TierAInvariantSampler):
        return sampler(composition_wt_pct, temperature_C, pressure_bar)
    if pressure_bar is None:
        raise LinerCorrosionRefusal("liner_service_pressure_missing")
    if isinstance(sampler, CurveLiquidFractionSampler) and any(
        float(kg) > 0.0 for kg in deposit_kg_by_species.values()
    ):
        curve = sampler.curve(
            liner_composition_wt_pct,
            deposit_kg_by_species,
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
        )
        return curve(composition_wt_pct, temperature_C, pressure_bar)
    return sampler(composition_wt_pct, temperature_C, pressure_bar)


def _campaign_branch(
    liner_composition_wt_pct: Mapping[str, Any],
    initial_deposit_kg_by_species: Mapping[str, Any],
    branch_deposit_kg_by_species: Mapping[str, float],
    *,
    density_kg_m3: float,
    area_m2: float,
    reacted_layer_depth_m: float,
    temperature_C: float,
    pressure_bar: float | None,
    margin_C: float,
    horizon: int,
    sampler: LiquidFractionSampler,
    feedback_deposit_kg_by_species: Mapping[str, float] | None = None,
    pure_reference: Mapping[str, Any] | None = None,
    invariant_table: Mapping[str, Any] | None = None,
) -> tuple[int | str, dict[str, float], float, dict[str, Any] | None]:
    deposits = _checked_deposit(initial_deposit_kg_by_species)
    service_temperature = _finite_real(temperature_C, "temperature_C")
    pressure = (
        None if pressure_bar is None else _positive(pressure_bar, "pressure_bar")
    )
    last_activity: dict[str, Any] | None = None
    for completed in range(horizon + 1):
        composition = contaminated_interface_composition(
            liner_composition_wt_pct,
            deposits,
            density_kg_m3=density_kg_m3,
            area_m2=area_m2,
            reacted_layer_depth_m=reacted_layer_depth_m,
        )["composition_wt_pct"]
        if isinstance(sampler, TierAInvariantSampler):
            tier_a_table = (
                invariant_table
                if isinstance(invariant_table, Mapping)
                else sampler.invariant_table
            )
            _, resolution, _rating_cap_refusal = _tier_a_rating_state(
                liner_composition_wt_pct,
                composition,
                tier_a_table,
                margin_C=margin_C,
                service_temperature_C=service_temperature,
            )
            onset = resolution.onset_at_service_temperature
            if onset is None:
                raise LinerCorrosionRefusal("tier_a_onset_unresolved")
            criterion_signal = float(onset)
        else:
            if pure_reference is None:
                raise LinerCorrosionRefusal("liner_bulk_melt_reference_missing")
            last_activity = _tier_b_melt_activity_rating(
                liner_composition_wt_pct,
                composition,
                deposits,
                sampler=sampler,
                pressure_bar=pressure,
                margin_C=margin_C,
                service_temperature_C=service_temperature,
                literature_melting_temperature_C=pure_reference.get(
                    "literature_melting_temperature_C"
                ),
                pure_reference=pure_reference,
                invariant_table=invariant_table,
            )
            criterion_signal_value = last_activity.get(
                "criterion_signal_at_service_temperature"
            )
            if criterion_signal_value is None:
                criterion_signal_value = last_activity[
                    "excess_liquid_fraction_at_service_temperature"
                ]
            criterion_signal = float(criterion_signal_value)
            onset = _tier_b_onset_at_service_temperature(
                last_activity,
                service_temperature,
            )
        if onset:
            return completed, deposits, criterion_signal, last_activity
        if completed == horizon:
            break

        # s_N is the liquid fraction (Tier B) or invariant-field signal (Tier A).
        # Any positive signal selects the coupled reactive branch (alpha_s=1
        # AND P_eq=0); alpha without the pressure change is not a reactive bound.
        if feedback_deposit_kg_by_species is not None and criterion_signal > 0.0:
            increment = feedback_deposit_kg_by_species
        else:
            increment = branch_deposit_kg_by_species
        for species, kg in increment.items():
            deposits[species] = deposits.get(species, 0.0) + kg
    return f"> {horizon}", deposits, criterion_signal, last_activity


def _tier_b_reference_profile_single(
    sampler: LiquidFractionSampler,
    liner_composition_wt_pct: Mapping[str, Any],
    *,
    pressure_bar: float,
    literature_melting_temperature_C: float | None = None,
    allow_flat_endmember_profile: bool = False,
) -> dict[str, Any]:
    """Sample the d-419 grid and identify pure bulk and minor melting.

    The initial scan advances in 25 C increments from 500 C through 1600 C.
    If it has not reached the liquid finder's declared fully-liquid threshold,
    it extends in the same increments through that finder's 2200 C ceiling.
    Only a profile that reaches this threshold can use its largest adjacent
    liquid-fraction jump as the bulk melt point; this keeps the 1000-1025 C
    minor-phase eutectic from defining the bulk reference when the bulk melt
    transition lies above the initial scan.
    """

    liner = validate_oxide_composition(liner_composition_wt_pct)
    pressure = _positive(pressure_bar, "pressure_bar")
    temperatures = list(
        TIER_B_MELT_SCAN_MIN_C + index * TIER_B_MELT_SCAN_STEP_C
        for index in range(
            int((TIER_B_MELT_SCAN_MAX_C - TIER_B_MELT_SCAN_MIN_C)
                / TIER_B_MELT_SCAN_STEP_C) + 1
        )
    )
    liquid_by_temperature: dict[float, float] = {}
    diagnostics_by_temperature: dict[float, Mapping[str, Any]] = {}

    def sample_temperature(temperature: float) -> None:
        liquid_by_temperature[temperature] = _checked_liquid_fraction(
            _sample_liquid_fraction(
                sampler,
                liner,
                {},
                liner,
                temperature,
                pressure,
            )
        )
        diagnostic_reader = getattr(sampler, "sample_diagnostics", None)
        if callable(diagnostic_reader):
            detail = diagnostic_reader(liner, temperature, pressure)
            if isinstance(detail, Mapping):
                diagnostics_by_temperature[temperature] = detail

    for temperature in temperatures:
        sample_temperature(temperature)

    fully_liquid_threshold = 1.0 - LIQUIDUS_LIQUID_EPSILON
    scanned_liquid = tuple(liquid_by_temperature.values())
    constant_endmember_bracket: str | None = None
    if (
        allow_flat_endmember_profile
        and bool(scanned_liquid)
        and all(value == scanned_liquid[0] for value in scanned_liquid)
    ):
        if all(value <= LIQUIDUS_SOLID_EPSILON for value in scanned_liquid):
            constant_endmember_bracket = "all-solid-above-scan"
        elif all(value >= fully_liquid_threshold for value in scanned_liquid):
            constant_endmember_bracket = "all-liquid-below-scan"
    if constant_endmember_bracket is None:
        while (
            liquid_by_temperature[temperatures[-1]] < fully_liquid_threshold
            and temperatures[-1] < DEFAULT_LIQUIDUS_FINDER_MAX_T_C
        ):
            next_temperature = min(
                temperatures[-1] + TIER_B_MELT_SCAN_STEP_C,
                DEFAULT_LIQUIDUS_FINDER_MAX_T_C,
            )
            temperatures.append(next_temperature)
            sample_temperature(next_temperature)
        if liquid_by_temperature[temperatures[-1]] < fully_liquid_threshold:
            raise LinerCorrosionRefusal(
                "liner_reference_unresolved",
                (
                    f"pure-liner scan {temperatures[0]:g}-{temperatures[-1]:g} C "
                    f"reached f_liq={liquid_by_temperature[temperatures[-1]]:g}; "
                    f"fully-liquid threshold={fully_liquid_threshold:g}"
                ),
            )
    temperatures = tuple(temperatures)

    jumps = tuple(
        liquid_by_temperature[high] - liquid_by_temperature[low]
        for low, high in zip(temperatures, temperatures[1:])
    )
    if constant_endmember_bracket is not None:
        bulk_index = None
        bulk_bracket = None
        bulk_jump = None
    else:
        if not jumps or max(jumps) <= 0.0:
            raise LinerCorrosionRefusal("liner_bulk_melt_transition_missing")
        bulk_index = max(range(len(jumps)), key=jumps.__getitem__)
        bulk_bracket = [temperatures[bulk_index], temperatures[bulk_index + 1]]
        bulk_jump = jumps[bulk_index]
    # d-419 describes a minor-phase plateau. MAGEMin has shorter positive
    # steps below that plateau, so rank sub-bulk steps by the number of
    # consecutive samples staying within the same liquid-fraction epsilon.
    # The longest sustained step is the eutectic plateau; shorter events stay
    # visible in the full profile rather than defining the corrosion baseline.
    minor_candidates: list[tuple[int, int]] = []
    if bulk_index is not None:
        for index, jump in enumerate(jumps[:bulk_index]):
            if jump <= LIQUIDUS_SOLID_EPSILON:
                continue
            upper_index = index + 1
            plateau_fraction = liquid_by_temperature[temperatures[upper_index]]
            plateau_end_index = upper_index
            for next_index in range(upper_index + 1, bulk_index + 1):
                next_fraction = liquid_by_temperature[temperatures[next_index]]
                if abs(next_fraction - plateau_fraction) > LIQUIDUS_SOLID_EPSILON:
                    break
                plateau_end_index = next_index
            span = plateau_end_index - upper_index
            if span > 0:
                minor_candidates.append((index, span))
    minor_index = (
        max(minor_candidates, key=lambda candidate: candidate[1])[0]
        if minor_candidates
        else None
    )
    minor_phase: dict[str, Any] | None = None
    flags: list[str] = []
    if minor_index is not None:
        upper_temperature = temperatures[minor_index + 1]
        detail = diagnostics_by_temperature.get(upper_temperature, {})
        phases = detail.get("phases")
        minor_phase = {
            "temperature_bracket_C": [
                temperatures[minor_index],
                upper_temperature,
            ],
            "plateau_liquid_fraction": liquid_by_temperature[upper_temperature],
            "phases": list(phases) if isinstance(phases, Sequence) else [],
        }
        flags.append(ENGINE_MINOR_PHASE_EUTECTIC_FLAG)
    sampler_flags = set(getattr(sampler, "flags", ()) or ())
    if (
        ELEMENT_CLOSURE_UNVERIFIED_FLAG in sampler_flags
        or any(
            detail.get("element_closure_verified") is False
            for detail in diagnostics_by_temperature.values()
        )
    ):
        flags.append(PHASE_CLOSURE_UNVERIFIED_FLAG)
    first_liquid_bracket: list[float | None] | None = None
    for index, temperature in enumerate(temperatures):
        if liquid_by_temperature[temperature] > LIQUIDUS_SOLID_EPSILON:
            first_liquid_bracket = [
                temperatures[index - 1] if index else None,
                temperature,
            ]
            break
    result: dict[str, Any] = {
        "scan_temperature_C": list(temperatures),
        "liquid_fraction_by_temperature_C": liquid_by_temperature,
        "first_liquid_bracket_C": first_liquid_bracket,
        "bulk_melt_bracket_C": bulk_bracket,
        "bulk_melt_liquid_fraction_jump": bulk_jump,
        "engine_minor_phase_eutectic": minor_phase,
        "flags": flags,
    }
    if constant_endmember_bracket is not None:
        # Explicit bracket signatures let the shared d-421 selector compare
        # constant saturation profiles with ordinary onset brackets.
        result["constant_endmember_bracket"] = constant_endmember_bracket
    if literature_melting_temperature_C is not None:
        literature_temperature = _finite_real(
            literature_melting_temperature_C,
            "literature_melting_temperature_C",
        )
        result["literature_melting_temperature_C"] = literature_temperature
        result["bulk_melt_residual_vs_literature_C"] = [
            bulk_bracket[0] - literature_temperature,
            bulk_bracket[1] - literature_temperature,
        ]
        result["bulk_melt_residual_ticket"] = "b-1231"
    return result


def _tier_b_composition_neighbours(
    composition_wt_pct: Mapping[str, Any],
) -> tuple[dict[str, float], dict[str, float], float]:
    """Perturb the two largest oxide components by the declared resolution.

    This is a numerical continuity probe, not an uncertainty interval. The
    composition interface carries no source-precision field; formula-derived
    and generated compositions therefore use 0.01 wt%, the last reported digit
    of standard EPMA oxide analyses. The opposite perturbations preserve the
    sum, and explicit renormalization keeps the same 100 wt% basis.
    """

    nominal = validate_oxide_composition(composition_wt_pct)
    if len(nominal) < 2:
        raise LinerCorrosionRefusal(
            "tier_b_composition_neighbours_missing",
            "continuity probing requires two oxide components",
        )
    largest = sorted(nominal, key=lambda oxide: (-nominal[oxide], oxide))[:2]
    delta = TIER_B_COMPOSITION_NEIGHBOUR_DELTA_WT_PCT
    neighbours: list[dict[str, float]] = []
    for direction in (1.0, -1.0):
        shifted = dict(nominal)
        shifted[largest[0]] += direction * delta
        shifted[largest[1]] -= direction * delta
        if min(shifted.values()) < 0.0:
            raise LinerCorrosionRefusal(
                "tier_b_composition_neighbour_invalid",
                f"{largest[1]} cannot be shifted by {delta:g} wt%",
            )
        total = math.fsum(shifted.values())
        neighbours.append(
            {oxide: 100.0 * wt_pct / total for oxide, wt_pct in shifted.items()}
        )
    return neighbours[0], neighbours[1], delta


def _symmetric_mean(left: float, right: float) -> float:
    """Use both agreeing composition neighbours without privileging a side."""

    return 0.5 * (left + right)


def _first_liquid_profile_bracket(
    profile: Mapping[str, Any],
) -> Sequence[Any] | None:
    constant_endmember = profile.get("constant_endmember_bracket")
    if constant_endmember in (
        "all-solid-above-scan",
        "all-liquid-below-scan",
    ):
        return [constant_endmember]
    bracket = profile.get("first_liquid_bracket_C")
    if (
        isinstance(bracket, Sequence)
        and not isinstance(bracket, (str, bytes))
        and len(bracket) == 2
    ):
        return bracket[0], float(bracket[1])
    return None


def _composition_continuity_selection(
    nominal_profile: Mapping[str, Any] | None,
    first_neighbour_profile: Mapping[str, Any] | None,
    second_neighbour_profile: Mapping[str, Any] | None,
    *,
    delta_wt_pct: float,
    onset_bracket: Callable[[Mapping[str, Any]], Sequence[Any] | None],
    merge_neighbours: Callable[
        [Mapping[str, Any], Mapping[str, Any]], dict[str, Any] | None
    ],
    bulk_bracket: Callable[[Mapping[str, Any]], Sequence[Any] | None] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any] | None]:
    """Resolve one Tier B profile by the d-421 three-composition table."""

    def checked_bracket(
        profile: Mapping[str, Any] | None,
        reader: Callable[[Mapping[str, Any]], Sequence[Any] | None],
    ) -> list[Any] | None:
        if profile is None:
            return None
        value = reader(profile)
        return list(value) if value is not None else None

    nominal_onset = checked_bracket(nominal_profile, onset_bracket)
    first_onset = checked_bracket(first_neighbour_profile, onset_bracket)
    second_onset = checked_bracket(second_neighbour_profile, onset_bracket)
    details: dict[str, Any] = {
        "nominal_bracket_C": nominal_onset,
        "neighbour_bracket_C": first_onset,
        "neighbour_brackets_C": [first_onset, second_onset],
        "delta_wt_pct": delta_wt_pct,
    }
    nominal_bulk = first_bulk = second_bulk = None
    if bulk_bracket is not None:
        nominal_bulk = checked_bracket(nominal_profile, bulk_bracket)
        first_bulk = checked_bracket(first_neighbour_profile, bulk_bracket)
        second_bulk = checked_bracket(second_neighbour_profile, bulk_bracket)
        details.update(
            {
                "nominal_bulk_melt_bracket_C": nominal_bulk,
                "neighbour_bulk_melt_bracket_C": first_bulk,
                "neighbour_bulk_melt_brackets_C": [first_bulk, second_bulk],
            }
        )

    def signature(onset: list[Any] | None, bulk: list[Any] | None) -> tuple[Any, ...]:
        return (
            tuple(onset) if onset is not None else None,
            tuple(bulk) if bulk is not None else None,
        )

    nominal_signature = signature(nominal_onset, nominal_bulk)
    first_signature = signature(first_onset, first_bulk)
    second_signature = signature(second_onset, second_bulk)
    if (
        nominal_profile is not None
        and first_neighbour_profile is not None
        and second_neighbour_profile is not None
        and nominal_signature == first_signature == second_signature
    ):
        return (
            dict(nominal_profile),
            {"status": "passed", **details},
            None,
        )
    if (
        first_neighbour_profile is not None
        and second_neighbour_profile is not None
        and first_signature == second_signature
    ):
        merged = merge_neighbours(first_neighbour_profile, second_neighbour_profile)
        if merged is not None:
            return merged, {"status": "sensitive", **details}, details
    return None, {"status": "tier_a_fallback", **details}, details


def _tier_a_continuity_fallback(
    composition_wt_pct: Mapping[str, Any],
    invariant_table: Mapping[str, Any] | None,
    sensitivity: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(invariant_table, Mapping) or not invariant_table:
        raise LinerCorrosionRefusal(
            "tier_b_composition_continuity_unresolved",
            (
                "d-419 composition neighbours disagree and no Tier A invariant "
                f"table covers the probe; continuity={dict(sensitivity)!r}"
            ),
        )
    try:
        first_liquid_C = first_liquid_tier_a(
            invariant_table,
            composition_wt_pct,
        )
    except LinerCorrosionRefusal as exc:
        raise LinerCorrosionRefusal(
            exc.reason,
            (
                "d-419 Tier A fallback after disagreeing composition neighbours: "
                f"{exc.detail}; continuity={dict(sensitivity)!r}"
            ),
        ) from exc
    return {
        "tier_a_fallback": True,
        "tier_a_first_liquid_T_C": first_liquid_C,
        "engine_composition_sensitive": dict(sensitivity),
        "composition_continuity": {"status": "tier_a_fallback", **dict(sensitivity)},
        "flags": [ENGINE_COMPOSITION_SENSITIVE_FLAG],
    }


def _average_neighbour_profiles(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> dict[str, Any] | None:
    left_temperatures = tuple(left.get("scan_temperature_C", ()))
    right_temperatures = tuple(right.get("scan_temperature_C", ()))
    left_liquid = left.get("liquid_fraction_by_temperature_C")
    right_liquid = right.get("liquid_fraction_by_temperature_C")
    if not isinstance(left_liquid, Mapping) or not isinstance(right_liquid, Mapping):
        return None
    common_temperatures = tuple(
        temperature
        for temperature in left_temperatures
        if temperature in right_temperatures
        and temperature <= min(left_temperatures[-1], right_temperatures[-1])
    )
    bulk_bracket = left.get("bulk_melt_bracket_C")
    if bulk_bracket is not None and not set(bulk_bracket).issubset(common_temperatures):
        return None
    result = dict(left)
    averaged = {
        temperature: _symmetric_mean(
            _checked_liquid_fraction(left_liquid[temperature]),
            _checked_liquid_fraction(right_liquid[temperature]),
        )
        for temperature in common_temperatures
    }
    result["scan_temperature_C"] = list(common_temperatures)
    result["liquid_fraction_by_temperature_C"] = averaged
    if bulk_bracket is None:
        result["bulk_melt_liquid_fraction_jump"] = None
    else:
        low, high = bulk_bracket
        result["bulk_melt_liquid_fraction_jump"] = averaged[high] - averaged[low]
    result["flags"] = list(
        dict.fromkeys([*left.get("flags", ()), *right.get("flags", ())])
    )
    return result


def _tier_b_reference_profile(
    sampler: LiquidFractionSampler,
    liner_composition_wt_pct: Mapping[str, Any],
    *,
    pressure_bar: float,
    literature_melting_temperature_C: float | None = None,
    invariant_table: Mapping[str, Any] | None = None,
    allow_flat_endmember_profile: bool = False,
) -> dict[str, Any]:
    """Apply d-419's composition-continuity decision table to a pure profile.

    Melting changes continuously with composition, so a 0.01 wt% numerical
    nudge cannot create a hundreds-of-kelvin solidus jump. Formula-exact and
    Morey-rounded CaAl2Si2O8 profiles exercise this rule in the live MAGEMin
    test, which records route-specific engine values and version.
    Decision table: (1) if nominal and both neighbours have the same first-
    liquid and bulk brackets, keep nominal and record passed; (2) if only the
    nominal bracket differs, average the agreeing opposite neighbours and
    attach engine_composition_sensitive with both brackets and delta; (3) if
    the neighbours differ, Tier B is inapplicable for this probe, so use cited
    Tier A or refuse with its acquisition gap. The rule detects numerical
    endmember artefacts, not a physical composition threshold.
    """

    liner = validate_oxide_composition(liner_composition_wt_pct)
    first_neighbour, second_neighbour, delta = _tier_b_composition_neighbours(liner)
    profiles: list[dict[str, Any] | None] = []
    refusals: list[LinerCorrosionRefusal | None] = []
    for composition in (liner, first_neighbour, second_neighbour):
        try:
            profiles.append(
                _tier_b_reference_profile_single(
                    sampler,
                    composition,
                    pressure_bar=pressure_bar,
                    literature_melting_temperature_C=literature_melting_temperature_C,
                    allow_flat_endmember_profile=allow_flat_endmember_profile,
                )
            )
            refusals.append(None)
        except LinerCorrosionRefusal as exc:
            if exc.reason not in {
                "liner_reference_unresolved",
                "liner_bulk_melt_transition_missing",
                ENGINE_OUT_OF_DOMAIN_FLAG,
            }:
                raise
            profiles.append(None)
            refusals.append(exc)
    nominal_profile, first_profile, second_profile = profiles
    if all(profile is None for profile in profiles):
        first_refusal = next(refusal for refusal in refusals if refusal is not None)
        raise first_refusal
    selected, continuity, sensitivity = _composition_continuity_selection(
        nominal_profile,
        first_profile,
        second_profile,
        delta_wt_pct=delta,
        onset_bracket=_first_liquid_profile_bracket,
        merge_neighbours=_average_neighbour_profiles,
        bulk_bracket=lambda profile: profile.get("bulk_melt_bracket_C"),
    )
    if selected is None:
        fallback = _tier_a_continuity_fallback(
            liner,
            invariant_table,
            sensitivity or continuity,
        )
        engine_version = getattr(sampler, "engine_version", None)
        if engine_version is not None:
            fallback["engine_version"] = str(engine_version)
        return fallback
    if sensitivity is not None:
        selected["engine_composition_sensitive"] = sensitivity
        selected["flags"] = list(
            dict.fromkeys([*selected.get("flags", ()), ENGINE_COMPOSITION_SENSITIVE_FLAG])
        )
    selected["composition_continuity"] = continuity
    engine_version = getattr(sampler, "engine_version", None)
    if engine_version is not None:
        selected["engine_version"] = str(engine_version)
    return selected


def _tier_b_sample_at_temperature_with_reference(
    sampler: LiquidFractionSampler,
    composition_wt_pct: Mapping[str, Any],
    temperature_C: float,
    pressure_bar: float,
    reference_profile: Mapping[str, Any],
    *,
    context: str,
) -> float:
    """Apply a profile's d-421 continuity decision to a service point sample."""

    continuity = reference_profile.get("composition_continuity")
    status = continuity.get("status") if isinstance(continuity, Mapping) else None
    if status == "passed":
        return _checked_liquid_fraction(
            sampler(composition_wt_pct, temperature_C, pressure_bar)
        )
    if status == "sensitive":
        first, second, _delta = _tier_b_composition_neighbours(composition_wt_pct)
        return _symmetric_mean(
            _checked_liquid_fraction(sampler(first, temperature_C, pressure_bar)),
            _checked_liquid_fraction(sampler(second, temperature_C, pressure_bar)),
        )
    raise LinerCorrosionRefusal(
        "tier_b_composition_continuity_unresolved",
        f"{context}: no resolved Tier B continuity status ({status!r})",
    )


def _tier_b_reference_profile_for_liner(
    sampler: LiquidFractionSampler,
    liner_composition_wt_pct: Mapping[str, Any],
    *,
    pressure_bar: float,
    invariant_table: Mapping[str, Any] | None,
) -> dict[str, Any]:
    literature_reference = _tier_a_pure_phase_melting_reference(
        invariant_table,
        liner_composition_wt_pct,
    )
    profile = _tier_b_reference_profile(
        sampler,
        liner_composition_wt_pct,
        pressure_bar=pressure_bar,
        literature_melting_temperature_C=(
            literature_reference["temperature_C"]
            if literature_reference is not None
            else None
        ),
        invariant_table=invariant_table,
    )
    if literature_reference is not None:
        profile["literature_melting_temperature_C"] = literature_reference[
            "temperature_C"
        ]
        profile["literature_reference_citation"] = literature_reference["citation"]
    return profile


def _tier_b_contaminated_profile(
    sampler: LiquidFractionSampler,
    composition_wt_pct: Mapping[str, Any],
    *,
    liner_composition_wt_pct: Mapping[str, Any],
    deposit_kg_by_species: Mapping[str, Any],
    use_cached_curve: bool,
    temperatures: Sequence[float],
    pure_liquid_by_temperature_C: Mapping[float, float],
    pure_reference: Mapping[str, Any],
    pressure_bar: float,
    service_temperature_C: float | None,
) -> dict[str, Any]:
    composition = validate_oxide_composition(composition_wt_pct)
    liner = validate_oxide_composition(liner_composition_wt_pct)
    deposits = _checked_deposit(deposit_kg_by_species)
    contaminated: dict[float, float] = {}
    excess: dict[float, float] = {}

    def sample_contaminated(temperature: float) -> float:
        return _checked_liquid_fraction(
            _sample_liquid_fraction(
                sampler,
                composition,
                deposits if use_cached_curve else {},
                liner,
                temperature,
                pressure_bar,
            )
        )

    for temperature in temperatures:
        fraction = sample_contaminated(temperature)
        contaminated[temperature] = fraction
        excess[temperature] = fraction - float(pure_liquid_by_temperature_C[temperature])
    onset_bracket: list[float | None] | None = None
    for index, temperature in enumerate(temperatures):
        if excess[temperature] > LIQUIDUS_SOLID_EPSILON:
            onset_bracket = [temperatures[index - 1] if index else None, temperature]
            break

    # d-419 lower-bracket rule: an excess already present at the first scan
    # temperature may put onset below the service temperature. Continue in the
    # same scan step until the no-excess point brackets it, stopping at
    # absolute zero. If the sampler refuses or the floor is reached first,
    # retain [None, T_positive] so the rating path stays conservatively
    # unresolved; never replace this case with the pure-liner cap.
    if onset_bracket is not None and onset_bracket[0] is None:
        upper_temperature = float(onset_bracket[1])
        floor_C = -CELSIUS_TO_KELVIN_OFFSET
        while upper_temperature > floor_C:
            lower_temperature = max(
                floor_C,
                upper_temperature - TIER_B_MELT_SCAN_STEP_C,
            )
            try:
                lower_fraction = sample_contaminated(lower_temperature)
                lower_baseline = pure_liquid_by_temperature_C.get(lower_temperature)
                if lower_baseline is None:
                    lower_baseline = _tier_b_sample_at_temperature_with_reference(
                        sampler,
                        liner,
                        lower_temperature,
                        pressure_bar,
                        pure_reference,
                        context="pure-liner downward scan baseline",
                    )
            except LinerCorrosionRefusal:
                break
            contaminated[lower_temperature] = lower_fraction
            excess[lower_temperature] = lower_fraction - float(lower_baseline)
            if excess[lower_temperature] <= LIQUIDUS_SOLID_EPSILON:
                onset_bracket = [lower_temperature, upper_temperature]
                break
            onset_bracket = [None, lower_temperature]
            upper_temperature = lower_temperature

    service_fraction = None
    if service_temperature_C is not None:
        service_fraction = sample_contaminated(service_temperature_C)
    return {
        "scan_temperature_C": sorted(contaminated),
        "liquid_fraction_by_temperature_C": contaminated,
        "excess_liquid_fraction_by_temperature_C": excess,
        "contaminated_melt_bracket_C": onset_bracket,
        "service_liquid_fraction": service_fraction,
    }


def _average_contaminated_neighbours(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> dict[str, Any] | None:
    temperatures = tuple(left.get("scan_temperature_C", ()))
    if temperatures != tuple(right.get("scan_temperature_C", ())):
        return None
    result = dict(left)
    for field in (
        "liquid_fraction_by_temperature_C",
        "excess_liquid_fraction_by_temperature_C",
    ):
        left_values = left.get(field)
        right_values = right.get(field)
        if not isinstance(left_values, Mapping) or not isinstance(right_values, Mapping):
            return None
        result[field] = {
            temperature: _symmetric_mean(
                left_values[temperature], right_values[temperature]
            )
            for temperature in temperatures
        }
    if left.get("service_liquid_fraction") is not None:
        result["service_liquid_fraction"] = _symmetric_mean(
            float(left["service_liquid_fraction"]),
            float(right["service_liquid_fraction"]),
        )
    return result


def _tier_a_fallback_melt_activity(
    interface: Mapping[str, float],
    *,
    liner_composition_wt_pct: Mapping[str, Any],
    deposits: Mapping[str, float],
    invariant_table: Mapping[str, Any] | None,
    pure_reference: Mapping[str, Any],
    literature_melting_temperature_C: float | None,
    margin_C: float,
    service_temperature_C: float | None,
    sensitivity: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(invariant_table, Mapping) or not invariant_table:
        raise LinerCorrosionRefusal(
            "tier_b_composition_continuity_unresolved",
            (
                "d-419 composition neighbours disagree and no Tier A invariant "
                f"table covers the probe; continuity={dict(sensitivity)!r}"
            ),
        )
    try:
        contaminated_first = first_liquid_tier_a(invariant_table, interface)
    except LinerCorrosionRefusal as exc:
        raise LinerCorrosionRefusal(
            exc.reason,
            (
                "d-419 Tier A fallback after disagreeing composition neighbours: "
                f"{exc.detail}; continuity={dict(sensitivity)!r}"
            ),
        ) from exc
    contaminated_temperature = _finite_real(
        contaminated_first,
        "tier_a_contaminated_first_liquid_T_C",
    )
    literature_temperature = (
        _finite_real(literature_melting_temperature_C, "literature_melting_temperature_C")
        if literature_melting_temperature_C is not None
        else None
    )
    literature_source = "cited_literature" if literature_temperature is not None else None
    try:
        pure_first_liquid_C = first_liquid_tier_a(
            invariant_table,
            liner_composition_wt_pct,
        )
    except LinerCorrosionRefusal:
        # A table may cover the contaminated point but not the pure-liner
        # point. Preserve the pure Tier B result, or its T_lit reference, as
        # the cap source instead of treating the missing table row as a value.
        pure_first_liquid_C = pure_reference.get("tier_a_first_liquid_T_C")
        if pure_first_liquid_C is None:
            pure_first_liquid_C = literature_temperature
    resolution = _resolve_rating_and_onset(
        pure_first_liquid_C,
        contaminated_temperature,
        margin_C,
        service_temperature_C,
    )
    service_excess = 0.0
    if service_temperature_C is not None and deposits:
        if resolution.onset_at_service_temperature is None:
            raise LinerCorrosionRefusal("tier_a_fallback_onset_unresolved")
        service_excess = float(resolution.onset_at_service_temperature)
    result = {
        "pure_bulk_melt_bracket_C": pure_reference.get("bulk_melt_bracket_C"),
        "bulk_melt_residual_vs_literature_C": pure_reference.get(
            "bulk_melt_residual_vs_literature_C"
        ),
        "engine_minor_phase_eutectic": pure_reference.get(
            "engine_minor_phase_eutectic"
        ),
        "contaminated_melt_bracket_C": [
            contaminated_temperature,
            contaminated_temperature,
        ],
        "excess_liquid_fraction_at_upper_bracket": None,
        "excess_liquid_fraction_at_service_temperature": None,
        "criterion_signal_at_service_temperature": service_excess,
        "literature_melting_temperature_C": literature_temperature,
        "literature_reference_source": literature_source,
        "literature_reference_citation": pure_reference.get(
            "literature_reference_citation"
        ),
        "rating_temperature_C": resolution.rating_temperature_C,
        "rating_cap_temperature_C": resolution.cap_temperature_C,
        "rating_cap_source": resolution.cap_source,
        "onset_at_service_temperature": resolution.onset_at_service_temperature,
        "tier_a_fallback": True,
        "engine_composition_sensitive": dict(sensitivity),
        "composition_continuity": {
            "status": "tier_a_fallback",
            **dict(sensitivity),
        },
        "liquid_fraction_at_service_temperature": None,
        "flags": list(
            dict.fromkeys(
                [
                    *pure_reference.get("flags", ()),
                    ENGINE_COMPOSITION_SENSITIVE_FLAG,
                    "tier_a_composition_continuity_fallback",
                ]
            )
        ),
    }
    return result


def _tier_b_melt_activity_rating(
    liner_composition_wt_pct: Mapping[str, Any],
    interface_composition_wt_pct: Mapping[str, Any],
    deposit_kg_by_species: Mapping[str, Any],
    *,
    sampler: LiquidFractionSampler,
    pressure_bar: float,
    margin_C: float,
    literature_melting_temperature_C: float | None,
    pure_reference: Mapping[str, Any],
    service_temperature_C: float | None = None,
    invariant_table: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply d-419's excess-melt shift and conservative lower scan bracket.

    Premise: a pure-liner engine profile can contain a minor-phase eutectic
    unrelated to deposit corrosion.  At each common scan temperature, subtract
    that pure baseline from the contaminated liquid fraction and detect the
    first excess above the accounting owner's 1e-3 solid threshold.  The
    reported onset is the preceding (lower-temperature) 25 C grid point.
    Rating algebra is T_lit + (T_contaminated,lo - T_bulk,pure,lo) - margin;
    both bracket endpoints are Celsius, so the shift and rating are Celsius.
    With no deposit, excess is zero and the rating is T_lit - margin.  The cap
    at that pure rating prevents a deposit from improving the rating.
    """

    liner = validate_oxide_composition(liner_composition_wt_pct)
    interface = validate_oxide_composition(interface_composition_wt_pct)
    deposits = _checked_deposit(deposit_kg_by_species)
    pressure = _positive(pressure_bar, "pressure_bar")
    margin = _finite_real(margin_C, "margin_C")
    if margin < 0.0:
        raise LinerCorrosionRefusal("temperature_margin_invalid")
    reference_flags = list(pure_reference.get("flags", ()))
    if literature_melting_temperature_C is None:
        literature_melting_temperature_C = pure_reference.get(
            "literature_melting_temperature_C"
        )
    reference_sensitivity = pure_reference.get("engine_composition_sensitive")
    if pure_reference.get("tier_a_fallback"):
        sensitivity = (
            reference_sensitivity
            if isinstance(reference_sensitivity, Mapping)
            else {"nominal_bracket_C": None, "neighbour_bracket_C": None}
        )
        return _tier_a_fallback_melt_activity(
            interface,
            liner_composition_wt_pct=liner,
            deposits=deposits,
            invariant_table=invariant_table,
            pure_reference=pure_reference,
            literature_melting_temperature_C=literature_melting_temperature_C,
            margin_C=margin,
            service_temperature_C=service_temperature_C,
            sensitivity=sensitivity,
        )

    temperatures = tuple(float(value) for value in pure_reference["scan_temperature_C"])
    pure_liquid = pure_reference["liquid_fraction_by_temperature_C"]
    bulk_low = float(pure_reference["bulk_melt_bracket_C"][0])
    if literature_melting_temperature_C is None:
        # d-415's fallback remains in force when no cited pure-phase T_lit is
        # available: use the engine's pure bulk lower bracket as T_lit.
        literature_temperature = bulk_low
        literature_source = "engine_bulk_melt_lower_bracket"
        reference_flags.append(ENGINE_BULK_MELT_REFERENCE_FALLBACK_FLAG)
    else:
        literature_temperature = _finite_real(
            literature_melting_temperature_C,
            "literature_melting_temperature_C",
        )
        literature_source = "cited_literature"
    contaminated_first_liquid_C: float | None = literature_temperature
    onset_bracket: list[float | None] | None = None
    excess_at_upper: float | None = None
    service_excess = 0.0
    onset_below_scan_range = False
    contaminated_continuity: dict[str, Any] = {"status": "not_applicable"}
    contaminated_sensitivity: dict[str, Any] | None = None
    selected_contaminated: dict[str, Any] | None = None
    if deposits:
        contaminated_first_liquid_C = None
        scan_temperatures = tuple(
            temperature for temperature in temperatures if temperature <= bulk_low
        )
        first_neighbour, second_neighbour, delta = _tier_b_composition_neighbours(
            interface
        )
        nominal_uses_curve = False
        if deposits and isinstance(sampler, CurveLiquidFractionSampler):
            try:
                _infer_deposit_fraction(
                    liner,
                    deposit_oxide_composition(deposits),
                    interface,
                )
            except LinerCorrosionRefusal:
                # Private callers may supply a diagnostic composition that is
                # not the liner/deposit mix line; preserve their direct-sample
                # behavior instead of asking phi(f) to extrapolate it.
                pass
            else:
                nominal_uses_curve = True
        interface_profiles = []
        for index, composition in enumerate(
            (interface, first_neighbour, second_neighbour)
        ):
            interface_profiles.append(
                _tier_b_contaminated_profile(
                    sampler,
                    composition,
                    liner_composition_wt_pct=liner,
                    deposit_kg_by_species=deposits,
                    use_cached_curve=(index == 0 and nominal_uses_curve),
                    temperatures=scan_temperatures,
                    pure_liquid_by_temperature_C=pure_liquid,
                    pure_reference=pure_reference,
                    pressure_bar=pressure,
                    service_temperature_C=service_temperature_C,
                )
            )
        nominal_profile, first_profile, second_profile = interface_profiles
        selected_contaminated, contaminated_continuity, contaminated_sensitivity = (
            _composition_continuity_selection(
                nominal_profile,
                first_profile,
                second_profile,
                delta_wt_pct=delta,
                onset_bracket=lambda profile: profile.get(
                    "contaminated_melt_bracket_C"
                ),
                merge_neighbours=_average_contaminated_neighbours,
            )
        )
        if selected_contaminated is None:
            sensitivity = contaminated_sensitivity or contaminated_continuity
            return _tier_a_fallback_melt_activity(
                interface,
                liner_composition_wt_pct=liner,
                deposits=deposits,
                invariant_table=invariant_table,
                pure_reference=pure_reference,
                literature_melting_temperature_C=literature_melting_temperature_C,
                margin_C=margin,
                service_temperature_C=service_temperature_C,
                sensitivity=sensitivity,
            )
        onset_bracket = selected_contaminated["contaminated_melt_bracket_C"]
        if onset_bracket is not None:
            onset_below_scan_range = onset_bracket[0] is None
            excess_at_upper = selected_contaminated[
                "excess_liquid_fraction_by_temperature_C"
            ][onset_bracket[1]]
    if onset_below_scan_range:
        flags = [*reference_flags, ONSET_BELOW_SCAN_RANGE_FLAG]
    elif onset_bracket is None:
        flags = [*reference_flags, DEPOSIT_MELT_BELOW_RESOLUTION_FLAG]
    else:
        # Use the lower endpoint of both engine brackets. The engine contributes
        # only a temperature shift; the pure literature temperature supplies
        # the rated reference. The upper cap keeps contamination from improving
        # that rating even when the engine predicts a negative shift.
        contaminated_first_liquid_C = literature_temperature + (
            onset_bracket[0] - bulk_low
        )
        flags = list(reference_flags)
    if service_temperature_C is not None and deposits and selected_contaminated is not None:
        service_temperature = _finite_real(service_temperature_C, "service_temperature_C")
        service_fraction = selected_contaminated.get("service_liquid_fraction")
        baseline_service = _tier_b_sample_at_temperature_with_reference(
            sampler,
            liner,
            service_temperature,
            pressure,
            pure_reference,
            context="pure-liner service baseline",
        )
        service_excess = float(service_fraction) - baseline_service
    resolution = _resolve_rating_and_onset(
        literature_temperature,
        contaminated_first_liquid_C,
        margin,
        service_temperature_C,
        onset_below_scan_range=onset_below_scan_range,
    )
    result = {
        "pure_bulk_melt_bracket_C": list(pure_reference["bulk_melt_bracket_C"]),
        "bulk_melt_residual_vs_literature_C": pure_reference.get(
            "bulk_melt_residual_vs_literature_C"
        ),
        "engine_minor_phase_eutectic": pure_reference.get(
            "engine_minor_phase_eutectic"
        ),
        "contaminated_melt_bracket_C": onset_bracket,
        "excess_liquid_fraction_at_upper_bracket": excess_at_upper,
        "excess_liquid_fraction_at_service_temperature": service_excess,
        "literature_melting_temperature_C": literature_temperature,
        "literature_reference_source": literature_source,
        "literature_reference_citation": pure_reference.get(
            "literature_reference_citation"
        ),
        "rating_temperature_C": resolution.rating_temperature_C,
        "rating_cap_temperature_C": resolution.cap_temperature_C,
        "rating_cap_source": resolution.cap_source,
        "onset_at_service_temperature": resolution.onset_at_service_temperature,
        "liquid_fraction_at_service_temperature": None,
        "composition_continuity": contaminated_continuity,
        "flags": list(
            dict.fromkeys(
                [
                    *flags,
                    *(
                        [ENGINE_COMPOSITION_SENSITIVE_FLAG]
                        if contaminated_sensitivity is not None
                        else []
                    ),
                ]
            )
        ),
    }
    if selected_contaminated is not None:
        result["liquid_fraction_at_service_temperature"] = selected_contaminated.get(
            "service_liquid_fraction"
        )
    if contaminated_sensitivity is not None:
        result["engine_composition_sensitive"] = contaminated_sensitivity
    if isinstance(reference_sensitivity, Mapping):
        result["pure_reference_engine_composition_sensitive"] = dict(
            reference_sensitivity
        )
    return result


def _corrosion_campaign_recurrence_impl(
    liner_composition_wt_pct: Mapping[str, Any],
    initial_deposit_kg_by_species: Mapping[str, Any],
    deposit_inert_kg_by_species: Mapping[str, Any],
    deposit_reactive_kg_by_species: Mapping[str, Any] | None,
    *,
    density_kg_m3: float,
    area_m2: float,
    reacted_layer_depth_m: float,
    temperature_C: float,
    pressure_bar: float | None,
    margin_C: float,
    horizon: int,
    sampler: LiquidFractionSampler,
    invariant_table: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Explicitly project the two sticking bounds and coupled recurrence."""

    liner = validate_oxide_composition(liner_composition_wt_pct)
    inert = _checked_deposit(deposit_inert_kg_by_species)
    reactive_refusal: dict[str, str] | None = None
    try:
        if deposit_reactive_kg_by_species is None:
            raise LinerCorrosionRefusal("reactive_deposit_branch_missing")
        reactive = _checked_deposit(deposit_reactive_kg_by_species)
        branches = sticking_deposit_interval(inert, reactive)
    except LinerCorrosionRefusal as exc:
        reactive_refusal = exc.as_dict()
        reactive = None
        branches = {
            "inert": {species: mass for species, mass in sorted(inert.items())},
            "reactive_upper_bound": {
                "deposit_kg_by_species": None,
                "alpha_s": 1.0,
                "P_eq_Pa": 0.0,
                "flags": [REACTIVE_STICKING_FLAG],
                "status": "refused",
                "refusal": reactive_refusal,
            },
        }
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 0:
        raise LinerCorrosionRefusal("campaign_horizon_invalid")
    margin = _finite_real(margin_C, "margin_C")
    if margin < 0.0:
        raise LinerCorrosionRefusal("temperature_margin_invalid")

    pure_reference: dict[str, Any] | None = None
    if not isinstance(sampler, TierAInvariantSampler):
        if pressure_bar is None:
            raise LinerCorrosionRefusal("liner_service_pressure_missing")
        pure_reference = _tier_b_reference_profile_for_liner(
            sampler,
            liner,
            pressure_bar=pressure_bar,
            invariant_table=invariant_table,
        )

    # Compute both stipulated deposition branches independently: composition
    # changes can make the larger deposit branch reach first liquid later.
    hi, hi_deposit, hi_signal, hi_activity = _campaign_branch(
        liner,
        initial_deposit_kg_by_species,
        branches["inert"],
        density_kg_m3=density_kg_m3,
        area_m2=area_m2,
        reacted_layer_depth_m=reacted_layer_depth_m,
        temperature_C=temperature_C,
        pressure_bar=pressure_bar,
        margin_C=margin,
        horizon=horizon,
        sampler=sampler,
        pure_reference=pure_reference,
        invariant_table=invariant_table,
    )
    lo: int | str | None = None
    lo_deposit: dict[str, float] | None = None
    lo_signal: float | None = None
    lo_activity: dict[str, Any] | None = None
    coupled: int | str | None = None
    coupled_deposit: dict[str, float] | None = None
    coupled_signal: float | None = None
    coupled_activity: dict[str, Any] | None = None
    if reactive is not None:
        reactive_deposit = branches["reactive_upper_bound"]["deposit_kg_by_species"]
        try:
            lo, lo_deposit, lo_signal, lo_activity = _campaign_branch(
                liner,
                initial_deposit_kg_by_species,
                reactive_deposit,
                density_kg_m3=density_kg_m3,
                area_m2=area_m2,
                reacted_layer_depth_m=reacted_layer_depth_m,
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                margin_C=margin,
                horizon=horizon,
                sampler=sampler,
                pure_reference=pure_reference,
                invariant_table=invariant_table,
            )
        except LinerCorrosionRefusal as exc:
            reactive_refusal = exc.as_dict()
        else:
            coupled, coupled_deposit, coupled_signal, coupled_activity = _campaign_branch(
                liner,
                initial_deposit_kg_by_species,
                branches["inert"],
                density_kg_m3=density_kg_m3,
                area_m2=area_m2,
                reacted_layer_depth_m=reacted_layer_depth_m,
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                margin_C=margin,
                horizon=horizon,
                sampler=sampler,
                feedback_deposit_kg_by_species=reactive_deposit,
                pure_reference=pure_reference,
                invariant_table=invariant_table,
            )
    flags = [
        DEPOSIT_OXIDATION_STATE_FLAG,
        REACTED_LAYER_DEPTH_FLAG,
        TEMPERATURE_MARGIN_FLAG,
        HORIZON_FLAG,
        DISSOLUTION_KINETICS_FLAG,
        CORROSION_NO_RATE_FLAG,
    ]
    if pure_reference is not None:
        flags.extend(pure_reference.get("flags", ()))
    if reactive_refusal is not None:
        flags.append("reactive_deposit_branch_refused")
    onset_by_branch: dict[str, int | str | None] = {
        "inert_onset_campaign": hi,
        "reactive_onset_campaign": lo,
    }
    if lo is None:
        campaign_interval: list[int | str | None] = [None, hi]
        interval_branch_labels = {"lower": None, "upper": "inert"}
    else:
        onset_values = (
            ("inert", hi),
            ("reactive_upper_bound", lo),
        )

        def onset_order(item: tuple[str, int | str]) -> tuple[int, str]:
            branch, campaigns = item
            if isinstance(campaigns, str):
                return horizon + 1, branch
            return campaigns, branch

        ordered_onsets = sorted(onset_values, key=onset_order)
        lower_branch, lower_onset = ordered_onsets[0]
        upper_branch, upper_onset = ordered_onsets[-1]
        if lower_onset == upper_onset:
            interval_branch_labels = {"lower": "both", "upper": "both"}
        else:
            interval_branch_labels = {
                "lower": lower_branch,
                "upper": upper_branch,
            }
        campaign_interval = [lower_onset, upper_onset]
    return {
        "campaigns_to_runaway": campaign_interval,
        "onset_campaigns_by_branch": onset_by_branch,
        "campaign_interval_branch_labels": interval_branch_labels,
        "coupled_campaigns_to_runaway": coupled,
        "branch_end_deposit_kg_by_species": {
            "reactive_upper_bound": lo_deposit,
            "inert": hi_deposit,
            "coupled": coupled_deposit,
        },
        "criterion_signal_at_horizon_or_onset": {
            "reactive_upper_bound": lo_signal,
            "inert": hi_signal,
            "coupled": coupled_signal,
        },
        "melt_activity": {
            "pure_reference": pure_reference,
            "reactive_upper_bound": lo_activity,
            "inert": hi_activity,
            "coupled": coupled_activity,
        },
        "reactive_branch_refusal": reactive_refusal,
        "sticking_state": branches,
        "reacted_layer_depth_m": {
            "declared_h": _positive(reacted_layer_depth_m, "reacted_layer_depth_m"),
            "sensitivity_h_over_10_h_10h": list(reacted_layer_depths(reacted_layer_depth_m)),
        },
        "margin_C": margin,
        "horizon_campaigns": horizon,
        "flags": flags,
    }


def _tier_b_onset_at_service_temperature(
    melt_activity: Mapping[str, Any], service_temperature_C: float
) -> bool:
    resolved_onset = melt_activity.get("onset_at_service_temperature")
    if isinstance(resolved_onset, bool):
        return resolved_onset
    rating = melt_activity.get("rating_temperature_C")
    if rating is not None:
        resolution = _resolve_rating_and_onset(
            None,
            _finite_real(rating, "rating_temperature_C"),
            0.0,
            service_temperature_C,
        )
        if resolution.onset_at_service_temperature is not None:
            return resolution.onset_at_service_temperature
    bracket = melt_activity.get("contaminated_melt_bracket_C")
    if (
        ONSET_BELOW_SCAN_RANGE_FLAG in set(melt_activity.get("flags", ()))
        and isinstance(bracket, Sequence)
        and not isinstance(bracket, (str, bytes))
        and len(bracket) == 2
        and bracket[0] is None
    ):
        # No lower temperature was sampled; the shared resolution keeps the
        # conservative early-onset bound without inventing a finite rating.
        resolution = _resolve_rating_and_onset(
            None,
            None,
            0.0,
            service_temperature_C,
            onset_below_scan_range=True,
        )
        if resolution.onset_at_service_temperature is not None:
            return resolution.onset_at_service_temperature
    raise LinerCorrosionRefusal(
        "liner_onset_below_scan_range",
        "service-temperature onset cannot be bounded below the first Tier B scan sample",
    )


def corrosion_campaign_recurrence(
    liner_composition_wt_pct: Mapping[str, Any],
    initial_deposit_kg_by_species: Mapping[str, Any],
    deposit_inert_kg_by_species: Mapping[str, Any],
    deposit_reactive_kg_by_species: Mapping[str, Any] | None,
    *,
    density_kg_m3: float,
    area_m2: float,
    reacted_layer_depth_m: float,
    temperature_C: float,
    pressure_bar: float | None,
    margin_C: float,
    horizon: int,
    sampler: LiquidFractionSampler,
) -> dict[str, Any]:
    """Project campaigns without changing the public sampler-only API."""

    return _corrosion_campaign_recurrence_impl(
        liner_composition_wt_pct,
        initial_deposit_kg_by_species,
        deposit_inert_kg_by_species,
        deposit_reactive_kg_by_species,
        density_kg_m3=density_kg_m3,
        area_m2=area_m2,
        reacted_layer_depth_m=reacted_layer_depth_m,
        temperature_C=temperature_C,
        pressure_bar=pressure_bar,
        margin_C=margin_C,
        horizon=horizon,
        sampler=sampler,
        invariant_table=None,
    )


def _first_liquid_exclusion_reason(row: Mapping[str, Any]) -> str | None:
    """Classify source transitions that are not first-liquid observations."""
    phases = row.get("phases")
    if isinstance(phases, Sequence) and not isinstance(phases, (str, bytes)):
        if not any(_is_liquid_phase(phase) for phase in phases):
            return "no_liquid_phase"
        return None
    invariant_type = str(row.get("invariant_type", "")).lower()
    if "melting" in invariant_type or "fusion" in invariant_type:
        return None
    return "not_first_liquid_transition"


def _is_liquid_phase(phase: Any) -> bool:
    label = str(phase).strip()
    return label.lower() == "liquid" or re.fullmatch(
        r"L\d*(?:\s*\([^)]*\))?", label, re.IGNORECASE
    ) is not None


def _printed_half_digit(value: Any) -> float:
    """Return half of the last printed digit for a source value."""

    text = str(value).strip().lower()
    mantissa, marker, exponent = text.partition("e")
    decimal_places = len(mantissa.partition(".")[2])
    power = int(exponent) if marker else 0
    return 0.5 * (10.0 ** (power - decimal_places))


def _source_precision_matches(
    left: Mapping[str, float],
    right: Mapping[str, float],
    printed: Mapping[str, Any],
) -> bool:
    """Compare compositions within the cited composition's printed precision."""

    if set(left) != set(right) or set(left) != set(printed):
        return False
    return all(
        abs(left[oxide] - right[oxide]) <= _printed_half_digit(printed[oxide])
        for oxide in left
    )


def _phase_formula_matches_at_source_precision(
    composition: Mapping[str, float],
    phase_composition: Mapping[str, float],
    source_row_composition: Mapping[str, Any],
) -> bool:
    """Match a formula-owner phase at source-row and formula precision.

    The source row provides its transcribed liquid-composition precision;
    phase identity is also bounded by the formula-derived composition at the
    hundredth-wt-percent resolution. Use the tighter half-digit tolerance so
    a coarse printed liquid point cannot make a nearby bulk into a pure phase.
    Neither tolerance comes from the caller's number notation.
    """

    if (
        set(composition) != set(phase_composition)
        or set(composition) != set(source_row_composition)
    ):
        return False
    return all(
        abs(composition[oxide] - phase_composition[oxide])
        <= min(
            _printed_half_digit(source_row_composition[oxide]),
            _printed_half_digit(f"{phase_composition[oxide]:.2f}"),
        )
        for oxide in composition
    )


def _phase_oxide_composition(
    phase: str, components: Sequence[str]
) -> dict[str, float] | None:
    """Resolve a printed oxide-compound formula onto its subsystem oxide basis."""

    try:
        phase_formula = parse_formula(phase)
        phase_molar_mass = phase_formula.molar_mass_g_per_mol()
        if phase_molar_mass is None:
            return None
        component_moles: dict[str, float] = {}
        oxide_masses: dict[str, float] = {}
        expected_oxygen = 0.0
        represented_cations: set[str] = set()
        # Each phase cation count fixes its oxide moles; oxygen and total mass
        # must close to the parsed formula before its compatibility point is used.
        for oxide in components:
            oxide_formula = parse_formula(oxide)
            cations = [element for element in oxide_formula.elements if element != "O"]
            if len(cations) != 1 or "O" not in oxide_formula.elements:
                return None
            cation = cations[0]
            phase_cation_atoms = float(phase_formula.elements.get(cation, 0.0))
            oxide_cation_atoms = float(oxide_formula.elements[cation])
            moles = phase_cation_atoms / oxide_cation_atoms
            component_moles[oxide] = moles
            if moles:
                represented_cations.add(cation)
            expected_oxygen += moles * float(oxide_formula.elements["O"])
            oxide_molar_mass = oxide_formula.molar_mass_g_per_mol()
            if oxide_molar_mass is None:
                return None
            oxide_masses[oxide] = moles * oxide_molar_mass
        phase_cations = {
            element
            for element in phase_formula.elements
            if element != "O" and phase_formula.elements[element] > 0.0
        }
        if represented_cations != phase_cations or not math.isclose(
            expected_oxygen,
            float(phase_formula.elements.get("O", 0.0)),
            rel_tol=0.0,
            abs_tol=1.0e-9,
        ):
            return None
    except (LinerCorrosionRefusal, TypeError, ValueError):
        return None
    total = math.fsum(oxide_masses.values())
    if total <= 0.0 or not math.isclose(
        total, phase_molar_mass, rel_tol=0.0, abs_tol=1.0e-8
    ):
        return None
    return {oxide: 100.0 * mass / total for oxide, mass in oxide_masses.items()}


def _bulk_inside_open_compatibility_field(
    composition: Mapping[str, float],
    phase_compositions: Sequence[Mapping[str, float]],
) -> bool:
    """Test the strict interior of a binary segment or ternary triangle.

    The open boundary excludes a pure compound from borrowing an adjacent
    eutectic's temperature; its own congruent or peritectic transition applies.
    """

    if len(phase_compositions) not in {2, 3}:
        return False
    oxides = tuple(composition)
    if len(oxides) not in {2, 3} or any(
        set(point) != set(oxides) for point in phase_compositions
    ):
        return False
    if len(phase_compositions) == 2:
        first, second = phase_compositions
        vector = [first[oxide] - second[oxide] for oxide in oxides]
        offset = [composition[oxide] - second[oxide] for oxide in oxides]
        denominator = math.fsum(value * value for value in vector)
        if denominator == 0.0:
            return False
        fraction = math.fsum(a * b for a, b in zip(offset, vector)) / denominator
        residual = math.sqrt(
            math.fsum(
                (offset[index] - fraction * vector[index]) ** 2
                for index in range(len(oxides))
            )
        )
        return 1.0e-8 < fraction < 1.0 - 1.0e-8 and residual <= 1.0e-7

    first, second, third = phase_compositions
    oxide_a, oxide_b = oxides[:2]
    ax = first[oxide_a] - third[oxide_a]
    ay = first[oxide_b] - third[oxide_b]
    bx = second[oxide_a] - third[oxide_a]
    by = second[oxide_b] - third[oxide_b]
    px = composition[oxide_a] - third[oxide_a]
    py = composition[oxide_b] - third[oxide_b]
    determinant = ax * by - ay * bx
    if abs(determinant) <= 1.0e-12:
        return False
    first_weight = (px * by - py * bx) / determinant
    second_weight = (ax * py - ay * px) / determinant
    third_weight = 1.0 - first_weight - second_weight
    if min(first_weight, second_weight, third_weight) <= 1.0e-8:
        return False
    reconstructed = {
        oxide: (
            first_weight * first[oxide]
            + second_weight * second[oxide]
            + third_weight * third[oxide]
        )
        for oxide in oxides
    }
    return all(
        math.isclose(
            composition[oxide], reconstructed[oxide], rel_tol=0.0, abs_tol=1.0e-7
        )
        for oxide in oxides
    )


def _tier_a_reaction_sides(row: Mapping[str, Any]) -> list[list[str]] | None:
    reaction = row.get("phase_reaction_as_printed")
    if not isinstance(reaction, str) or not reaction.strip():
        return None
    try:
        sides, _phases = _reaction_parts(
            reaction,
            str(row.get("source_id", "tier-a")),
            str(row.get("observation_id", "invariant")),
        )
    except LinerCorrosionDataError:
        return None
    return sides


def _tier_a_pure_phase_melting_reference(
    invariant_table: Mapping[str, Any] | None,
    liner_composition_wt_pct: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Find an exact cited congruent-melting row for the pure liner phase."""

    if not isinstance(invariant_table, Mapping):
        return None
    liner = _positive_oxide_composition(
        validate_oxide_composition(liner_composition_wt_pct)
    )
    rows = invariant_table.get("invariants")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return None
    matches: list[tuple[float, Mapping[str, Any]]] = []
    for row in rows:
        if not isinstance(row, Mapping) or _first_liquid_exclusion_reason(row):
            continue
        phases = row.get("phases")
        if (
            not isinstance(phases, Sequence)
            or isinstance(phases, (str, bytes))
            or sum(_is_liquid_phase(phase) for phase in phases) != 1
            or len(phases) != 2
            or str(row.get("invariant_type", "")).lower() != "congruent melting"
        ):
            continue
        liquid_composition = row.get("liquid_composition")
        if not isinstance(liquid_composition, Mapping):
            continue
        try:
            liquid = validate_oxide_composition(liquid_composition)
        except LinerCorrosionRefusal:
            continue
        reaction_sides = _tier_a_reaction_sides(row)
        if reaction_sides is None:
            continue
        solid_phases = [
            phase
            for side in reaction_sides
            for phase in side
            if not _is_liquid_phase(phase)
        ]
        if len(solid_phases) != 1:
            continue
        phase_composition = _phase_oxide_composition(solid_phases[0], tuple(liner))
        if phase_composition is None or not _source_precision_matches(
            liner, liquid, row["liquid_composition"]
        ):
            continue
        temperature = _finite_real(row.get("T_invariant_C"), "T_invariant_C")
        matches.append((temperature, row))
    if not matches:
        return None
    temperatures = {temperature for temperature, _ in matches}
    if len(temperatures) > 1:
        raise LinerCorrosionRefusal(
            "liner_pure_melting_reference_ambiguous",
            ", ".join(
                f"{row.get('source_id')}:{row.get('observation_id')}={temperature} C"
                for temperature, row in matches
            ),
        )
    temperature, row = sorted(
        matches,
        key=lambda item: (
            str(item[1].get("source_id", "")),
            str(item[1].get("observation_id", "")),
        ),
    )[0]
    return {
        "temperature_C": temperature,
        "citation": {
            "source_id": row.get("source_id"),
            "observation_id": row.get("observation_id"),
            "locator": row.get("locator"),
            "phase_reaction_as_printed": row.get("phase_reaction_as_printed"),
        },
    }


def first_liquid_tier_a(
    invariant_table: Mapping[str, Any],
    composition_wt_pct: Mapping[str, Any],
) -> float:
    """Resolve first-liquid onset from declared bounds or exact cited points.

    Source transition points define compatibility fields through their printed
    solid phases. A bulk strictly inside a listed field first melts at that
    field's invariant temperature; a pure phase uses its own congruent or
    peritectic transition. A liquid composition by itself is not a bulk field.
    """

    composition = _positive_oxide_composition(
        validate_oxide_composition(composition_wt_pct)
    )
    _raise_tier_a_acquisition_gap(invariant_table, composition)
    rows = invariant_table.get("invariants") if isinstance(invariant_table, Mapping) else None
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        raise LinerCorrosionRefusal("liner_invariant_table_missing")
    matches: list[float] = []
    pure_phase_matches: list[float] = []
    unresolved_phase_rows: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        bounds = row.get("composition_bounds_wt_pct")
        if isinstance(bounds, Mapping) and bounds:
            covered = True
            for oxide, raw_range in bounds.items():
                if isinstance(raw_range, Mapping):
                    low = raw_range.get("min")
                    high = raw_range.get("max")
                elif isinstance(raw_range, (list, tuple)) and len(raw_range) == 2:
                    low, high = raw_range
                else:
                    covered = False
                    break
                lower = _finite_real(low, f"{oxide}.min")
                upper = _finite_real(high, f"{oxide}.max")
                wt_pct = composition.get(str(oxide), 0.0)
                if lower > upper or wt_pct < lower or wt_pct > upper:
                    covered = False
                    break
            if covered:
                matches.append(
                    _finite_real(
                        row.get("first_liquid_temperature_C"),
                        "first_liquid_temperature_C",
                    )
                )
            continue

        subsystem = row.get("subsystem")
        if not isinstance(subsystem, str):
            continue
        if _first_liquid_exclusion_reason(row) is not None:
            continue
        components = set(subsystem.split("-"))
        if set(composition) != components:
            continue
        invariant_type = str(row.get("invariant_type", "")).lower()
        phases = row.get("phases")
        has_liquid_phase = (
            isinstance(phases, Sequence)
            and not isinstance(phases, (str, bytes))
            and any(_is_liquid_phase(phase) for phase in phases)
        )
        if not has_liquid_phase and invariant_type not in {
            "congruent melting",
            "eutectic",
            "peritectic",
            "liquid-bearing invariant",
        }:
            # A reported fusion temperature without a stated melting mode does
            # not identify a compatibility field.
            continue
        reaction_sides = _tier_a_reaction_sides(row)
        if reaction_sides is None:
            unresolved_phase_rows.append(str(row.get("observation_id", "unknown")))
            continue
        if sum(
            _is_liquid_phase(phase)
            for side in reaction_sides
            for phase in side
        ) != 1:
            unresolved_phase_rows.append(str(row.get("observation_id", "unknown")))
            continue
        solid_sides = [
            [phase for phase in side if not _is_liquid_phase(phase)]
            for side in reaction_sides
        ]
        solid_phases = list(dict.fromkeys(phase for side in solid_sides for phase in side))
        phase_compositions = [
            _phase_oxide_composition(phase, tuple(sorted(components)))
            for phase in solid_phases
        ]
        if any(phase is None for phase in phase_compositions):
            unresolved_phase_rows.append(str(row.get("observation_id", "unknown")))
            continue
        phases_by_name = dict(zip(solid_phases, phase_compositions))
        liquid_composition = row.get("liquid_composition")
        if len(solid_phases) == 1 and invariant_type == "congruent melting":
            phase = solid_phases[0]
            phase_composition = phases_by_name[phase]
            if not isinstance(liquid_composition, Mapping):
                continue
            try:
                liquid = validate_oxide_composition(liquid_composition)
            except LinerCorrosionRefusal:
                continue
            # The single parsed solid formula establishes phase identity; the
            # source's printed liquid composition gives the composition match.
            # This avoids replacing source atomic-weight rounding with ours.
            if _source_precision_matches(
                composition, liquid, liquid_composition
            ):
                pure_phase_matches.append(
                    _finite_real(row.get("T_invariant_C"), "T_invariant_C")
                )
            continue
        if invariant_type == "peritectic":
            for side_index, solid_side in enumerate(solid_sides):
                if (
                    len(solid_side) != 1
                    or any(_is_liquid_phase(phase) for phase in reaction_sides[side_index])
                ):
                    continue
                phase_composition = phases_by_name[solid_side[0]]
                # Phase identity uses source-row precision and formula-owner
                # stoichiometry, never the caller's notation.
                # C3A is CaO 62.27 wt%, so 62/38 lies in the C12A7-C3A field
                # and takes Morey Table 6 row 2 (1395 C), while formula C3A
                # takes row 1 (1535 C).
                if (
                    isinstance(liquid_composition, Mapping)
                    and _phase_formula_matches_at_source_precision(
                        composition, phase_composition, liquid_composition
                    )
                ):
                    # A peritectic row applies at its single solid reactant;
                    # the adjacent compatibility segment is tested below.
                    pure_phase_matches.append(
                        _finite_real(row.get("T_invariant_C"), "T_invariant_C")
                    )
        if len(solid_phases) in {2, 3} and _bulk_inside_open_compatibility_field(
            composition,
            [phases_by_name[phase] for phase in solid_phases],
        ):
            matches.append(
                _finite_real(row.get("T_invariant_C"), "T_invariant_C")
            )
    if pure_phase_matches:
        # A cited pure phase is a compatibility-field endpoint even when its
        # rounded printed composition falls numerically just inside that field.
        return min(pure_phase_matches)
    if not matches:
        if unresolved_phase_rows:
            raise LinerCorrosionRefusal(
                "tier_a_compatibility_phases_missing",
                ", ".join(sorted(set(unresolved_phase_rows))),
            )
        raise LinerCorrosionRefusal(
            "tier_a_compatibility_field_missing",
            "no cited compatibility segment or pure-phase transition covers the bulk",
        )
    return min(matches)


def _raise_tier_a_acquisition_gap(
    invariant_table: Any, composition: Mapping[str, float]
) -> None:
    if not isinstance(invariant_table, Mapping):
        return
    gaps = invariant_table.get("acquisition_gaps", {})
    if isinstance(gaps, Mapping) and {"FeO", "CaO", "Al2O3"}.issubset(composition):
        gap = next(
            (
                (str(name), str(detail))
                for name, detail in gaps.items()
                if set(str(name).split("-")) == {"FeO", "CaO", "Al2O3"}
            ),
            None,
        )
        if gap is not None:
            raise LinerCorrosionRefusal(
                "liner_tier_a_acquisition_gap", f"{gap[0]}: {gap[1]}"
            )


def tier_a_first_liquid_onset(
    invariant_table: Mapping[str, Any],
    composition_wt_pct: Mapping[str, Any],
    *,
    service_temperature_C: float,
    margin_C: float,
) -> dict[str, Any]:
    first_liquid_C = first_liquid_tier_a(invariant_table, composition_wt_pct)
    margin = _finite_real(margin_C, "margin_C")
    service_temperature = _finite_real(service_temperature_C, "service_temperature_C")
    service_limit_C = service_temperature + margin
    resolution = _resolve_rating_and_onset(
        None,
        first_liquid_C,
        margin,
        service_temperature,
    )
    return {
        "first_melt_T_C": first_liquid_C,
        "service_limit_T_C": service_limit_C,
        "onset": resolution.onset_at_service_temperature,
        "flags": [TEMPERATURE_MARGIN_FLAG],
    }


def _tier_a_rating_state(
    liner_composition_wt_pct: Mapping[str, Any],
    interface_composition_wt_pct: Mapping[str, Any],
    invariant_table: Mapping[str, Any],
    *,
    margin_C: float,
    service_temperature_C: float,
) -> tuple[float, LinerRatingResolution, dict[str, str] | None]:
    """Resolve Tier A first liquid, pure cap, and shared service onset decision."""

    first_liquid_C = first_liquid_tier_a(
        invariant_table,
        interface_composition_wt_pct,
    )
    rating_cap_refusal = None
    try:
        pure_literature_temperature_C = first_liquid_tier_a(
            invariant_table,
            liner_composition_wt_pct,
        )
    except LinerCorrosionRefusal as exc:
        # The contaminated first-liquid point still rates onset without a pure
        # literature cap. Keep the cap's typed refusal separate: dissolution
        # uses the cited service-temperature liquid and does not need T_lit.
        pure_literature_temperature_C = None
        rating_cap_refusal = {
            "reason": "tier_a_pure_liner_rating_cap_unavailable",
            "detail": f"{exc.reason}: {exc.detail}",
        }
    resolution = _resolve_rating_and_onset(
        pure_literature_temperature_C,
        first_liquid_C,
        margin_C,
        service_temperature_C,
    )
    if resolution.rating_temperature_C is None:
        raise LinerCorrosionRefusal("tier_a_rating_unresolved")
    return first_liquid_C, resolution, rating_cap_refusal


@dataclass(frozen=True)
class TierAInvariantSampler:
    """Binary field-state sampler from the digitized first-liquid table."""

    invariant_table: Mapping[str, Any]

    def __call__(
        self,
        composition_wt_pct: Mapping[str, float],
        temperature_C: float,
        _pressure_bar: float | None,
    ) -> float:
        first_liquid_C = first_liquid_tier_a(
            self.invariant_table,
            composition_wt_pct,
        )
        return float(
            first_liquid_C <= _finite_real(temperature_C, "temperature_C")
        )


def species_aggressiveness_tier_a_finite_difference(
    liner_composition_wt_pct: Mapping[str, Any],
    deposit_species: str,
    *,
    invariant_table: Mapping[str, Any],
) -> dict[str, float | str]:
    """Estimate d(first-liquid temperature)/d(deposit wt%) from Tier A data."""

    species = str(deposit_species).strip()
    oxide = DEPOSIT_OXIDE_BY_SPECIES.get(species)
    if oxide is None:
        raise LinerCorrosionRefusal("deposit_species_unmapped", species)
    base = validate_oxide_composition(liner_composition_wt_pct)
    base_first_liquid_C = first_liquid_tier_a(invariant_table, base)
    perturbed = {key: value * 0.99 for key, value in base.items()}
    perturbed[oxide] = perturbed.get(oxide, 0.0) + 1.0
    perturbed_first_liquid_C = first_liquid_tier_a(invariant_table, perturbed)
    return {
        "species": species,
        "oxide": oxide,
        "delta_first_liquid_temperature_C_per_wt_pct": (
            perturbed_first_liquid_C - base_first_liquid_C
        ),
    }


def _run_corrosion_recurrence(
    liner: Mapping[str, float],
    initial_deposit: Mapping[str, float],
    deposit_inert: Mapping[str, Any],
    deposit_reactive: Mapping[str, Any] | None,
    *,
    density_kg_m3: float,
    area_m2: float,
    reacted_layer_depth_m: float,
    temperature_C: float,
    pressure_bar: float | None,
    margin_C: float,
    horizon: int,
    criterion_tier: str,
    invariant_table: Mapping[str, Any] | None,
    sampler: LiquidFractionSampler | None,
) -> dict[str, Any]:
    if criterion_tier == "A":
        if not isinstance(invariant_table, Mapping) or not invariant_table:
            raise LinerCorrosionRefusal("liner_invariant_table_missing")
        recurrence_sampler: LiquidFractionSampler = TierAInvariantSampler(
            invariant_table
        )
    elif criterion_tier == "B":
        if sampler is None:
            raise LinerCorrosionRefusal("liquid_fraction_sampler_missing")
        if pressure_bar is None:
            raise LinerCorrosionRefusal("liner_service_pressure_missing")
        recurrence_sampler = sampler
    else:
        raise LinerCorrosionRefusal("liner_criterion_tier_invalid", criterion_tier)
    return _corrosion_campaign_recurrence_impl(
        liner,
        initial_deposit,
        deposit_inert,
        deposit_reactive,
        density_kg_m3=density_kg_m3,
        area_m2=area_m2,
        reacted_layer_depth_m=reacted_layer_depth_m,
        temperature_C=temperature_C,
        pressure_bar=pressure_bar,
        margin_C=margin_C,
        horizon=horizon,
        sampler=recurrence_sampler,
        invariant_table=invariant_table,
    )


def _interface_state_at_depth(
    liner_composition_wt_pct: Mapping[str, Any],
    deposit_kg_by_species: Mapping[str, Any],
    *,
    density_kg_m3: float,
    area_m2: float,
    reacted_layer_depth_m: float,
    service_temperature_C: float,
    margin_C: float,
    pressure_bar: float | None,
    criterion_tier: str,
    invariant_table: Mapping[str, Any] | None,
    sampler: LiquidFractionSampler | None,
) -> dict[str, Any]:
    mixed = contaminated_interface_composition(
        liner_composition_wt_pct,
        deposit_kg_by_species,
        density_kg_m3=density_kg_m3,
        area_m2=area_m2,
        reacted_layer_depth_m=reacted_layer_depth_m,
    )
    if criterion_tier == "A":
        if not isinstance(invariant_table, Mapping) or not invariant_table:
            raise LinerCorrosionRefusal("liner_invariant_table_missing")
        first_melt_C, resolution, rating_cap_refusal = _tier_a_rating_state(
            liner_composition_wt_pct,
            mixed["composition_wt_pct"],
            invariant_table,
            margin_C=margin_C,
            service_temperature_C=service_temperature_C,
        )
        return {
            "mixed": mixed,
            "first_melt_T_C": first_melt_C,
            "rating_temperature_C": resolution.rating_temperature_C,
            "rating_cap_temperature_C": resolution.cap_temperature_C,
            "rating_cap_source": resolution.cap_source,
            "rating_cap_refusal": rating_cap_refusal,
            "liquid_fraction": None,
            "onset": resolution.onset_at_service_temperature,
        }
    if criterion_tier != "B":
        raise LinerCorrosionRefusal("liner_criterion_tier_invalid", criterion_tier)
    if sampler is None:
        raise LinerCorrosionRefusal("liquid_fraction_sampler_missing")
    if pressure_bar is None:
        raise LinerCorrosionRefusal("liner_service_pressure_missing")
    pure_reference = _tier_b_reference_profile_for_liner(
        sampler,
        liner_composition_wt_pct,
        pressure_bar=pressure_bar,
        invariant_table=invariant_table,
    )
    melt_activity = _tier_b_melt_activity_rating(
        liner_composition_wt_pct,
        mixed["composition_wt_pct"],
        deposit_kg_by_species,
        sampler=sampler,
        pressure_bar=pressure_bar,
        margin_C=margin_C,
        literature_melting_temperature_C=pure_reference.get(
            "literature_melting_temperature_C"
        ),
        pure_reference=pure_reference,
        service_temperature_C=service_temperature_C,
        invariant_table=invariant_table,
    )
    if melt_activity.get("tier_a_fallback"):
        liquid_fraction = None
    else:
        if _checked_deposit(deposit_kg_by_species):
            continuity_reference = melt_activity
            continuity_context = "mixed-interface service sample"
        else:
            continuity_reference = pure_reference
            continuity_context = "pure-liner service sample"
        liquid_fraction = _tier_b_sample_at_temperature_with_reference(
            sampler,
            mixed["composition_wt_pct"],
            service_temperature_C,
            pressure_bar,
            continuity_reference,
            context=continuity_context,
        )
    return {
        "mixed": mixed,
        "first_melt_T_C": (
            melt_activity["contaminated_melt_bracket_C"][0]
            if melt_activity["contaminated_melt_bracket_C"]
            else None
        ),
        "liquid_fraction": liquid_fraction,
        "rating_temperature_C": melt_activity.get("rating_temperature_C"),
        "rating_cap_temperature_C": melt_activity.get("rating_cap_temperature_C"),
        "rating_cap_source": melt_activity.get("rating_cap_source"),
        "onset": _tier_b_onset_at_service_temperature(
            melt_activity,
            service_temperature_C,
        ),
        "melt_activity": melt_activity,
        "pure_reference": pure_reference,
    }


def _tier_a_saturated_deposit_fraction(
    invariant_table: Mapping[str, Any],
    liner: Mapping[str, float],
    deposits: Mapping[str, float],
    mixed_composition: Mapping[str, float],
    service_temperature_C: float,
) -> float:
    rows = invariant_table.get("invariants")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise LinerCorrosionRefusal(
            "tier_a_saturation_data_at_service_temperature_missing"
        )
    deposit_oxides = deposit_oxide_composition(deposits)
    mixed_components = set(_positive_oxide_composition(mixed_composition))
    matches: set[float] = set()
    for row in rows:
        if not isinstance(row, Mapping) or _first_liquid_exclusion_reason(row):
            continue
        liquid = row.get("liquid_composition")
        subsystem = row.get("subsystem")
        temperature = row.get("T_invariant_C")
        if (
            not isinstance(liquid, Mapping)
            or not isinstance(subsystem, str)
            or set(subsystem.split("-")) != mixed_components
            or temperature is None
        ):
            continue
        # Dissolution saturation is evaluated at operating service temperature;
        # the first-melt point and its margin do not supply that liquid composition.
        if not math.isclose(
            _finite_real(temperature, "T_invariant_C"),
            service_temperature_C,
            rel_tol=0.0,
            abs_tol=1.0e-9,
        ):
            continue
        try:
            matches.add(
                _infer_deposit_fraction(liner, deposit_oxides, liquid)
            )
        except LinerCorrosionRefusal as exc:
            if exc.reason != "magemin_curve_composition_mismatch":
                raise
    if not matches:
        raise LinerCorrosionRefusal(
            "tier_a_saturation_data_at_service_temperature_missing"
        )
    if len(matches) != 1:
        raise LinerCorrosionRefusal("tier_a_saturated_liquid_composition_ambiguous")
    fraction = next(iter(matches))
    if fraction <= 0.0:
        raise LinerCorrosionRefusal("tier_a_saturated_liquid_deposit_fraction_invalid")
    return fraction


def _tier_b_saturated_deposit_fraction(
    sampler: LiquidFractionSampler,
    liner: Mapping[str, float],
    deposits: Mapping[str, float],
    *,
    temperature_C: float,
    pressure_bar: float | None,
    pure_reference_profile: Mapping[str, Any],
    invariant_table: Mapping[str, Any] | None,
) -> tuple[float, list[dict[str, Any]]]:
    if not isinstance(sampler, CurveLiquidFractionSampler):
        raise LinerCorrosionRefusal("tier_b_phi_curve_sampler_missing")
    if pressure_bar is None:
        raise LinerCorrosionRefusal("liner_service_pressure_missing")
    curve = sampler.curve(
        liner,
        deposits,
        temperature_C=temperature_C,
        pressure_bar=pressure_bar,
    )
    target = 1.0 - LIQUIDUS_LIQUID_EPSILON
    points = tuple(curve.points)
    if len(points) < 2:
        raise LinerCorrosionRefusal("tier_b_saturated_liquid_fraction_not_reached")
    corrected_points: list[tuple[float, float]] = []
    continuity_evidence: list[dict[str, Any]] = []
    for raw_fraction, raw_liquid_fraction in points:
        fraction = _finite_real(raw_fraction, "deposit_fraction")
        if (
            fraction < 0.0
            or fraction > 1.0
            or (corrected_points and fraction <= corrected_points[-1][0])
        ):
            raise LinerCorrosionRefusal("magemin_curve_fraction_invalid")
        composition = _composition_at_fraction(
            liner, curve.deposit_composition_wt_pct, fraction
        )
        try:
            if math.isclose(fraction, 0.0, rel_tol=0.0, abs_tol=1e-12):
                reference_profile = pure_reference_profile
            else:
                reference_profile = _tier_b_reference_profile(
                    sampler,
                    composition,
                    pressure_bar=pressure_bar,
                    invariant_table=invariant_table,
                    allow_flat_endmember_profile=True,
                )
            continuity = reference_profile.get("composition_continuity")
            status = continuity.get("status") if isinstance(continuity, Mapping) else None
            if status == "passed":
                # The cached curve is the nominal sampler's phi(f) table. Use
                # it after the profile confirms no composition discontinuity.
                corrected_fraction = _checked_liquid_fraction(raw_liquid_fraction)
            elif status == "sensitive":
                corrected_fraction = _tier_b_sample_at_temperature_with_reference(
                    sampler,
                    composition,
                    temperature_C,
                    pressure_bar,
                    reference_profile,
                    context=f"saturation curve at deposit fraction {fraction:g}",
                )
                sensitivity = reference_profile.get("engine_composition_sensitive")
                if not isinstance(sensitivity, Mapping):
                    raise LinerCorrosionRefusal(
                        "tier_b_saturation_continuity_unresolved",
                        f"deposit fraction {fraction:g}: sensitivity evidence missing",
                    )
                continuity_evidence.append(
                    {
                        "deposit_fraction": fraction,
                        "engine_composition_sensitive": dict(sensitivity),
                        "flags": [ENGINE_COMPOSITION_SENSITIVE_FLAG],
                    }
                )
            else:
                raise LinerCorrosionRefusal(
                    "tier_b_saturation_continuity_unresolved",
                    f"deposit fraction {fraction:g}: unresolved continuity status {status!r}",
                )
        except LinerCorrosionRefusal as exc:
            raise LinerCorrosionRefusal(
                "tier_b_saturation_continuity_unresolved",
                f"deposit fraction {fraction:g}: {exc.reason}: {exc.detail}",
            ) from exc
        corrected_points.append((fraction, corrected_fraction))
        if corrected_fraction >= target:
            if fraction <= 0.0:
                raise LinerCorrosionRefusal(
                    "tier_b_saturated_liquid_deposit_fraction_invalid"
                )
            if len(corrected_points) == 1:
                return fraction, continuity_evidence
            low_fraction, low_signal = corrected_points[-2]
            if low_signal >= target:
                return low_fraction, continuity_evidence
            low = low_fraction
            high = fraction
            for _ in range(52):
                midpoint = (low + high) / 2.0
                signal = interpolate_liquid_fraction_curve(
                    corrected_points, midpoint
                )
                if signal >= target:
                    high = midpoint
                else:
                    low = midpoint
            return high, continuity_evidence
    raise LinerCorrosionRefusal("tier_b_saturated_liquid_fraction_not_reached")


def build_liner_state_segment(
    interface_chemistry: Mapping[str, Any],
    deposit_kg_by_species: Mapping[str, Any],
    *,
    area_m2: float,
    service_temperature_C: float,
    pressure_bar: float | None,
    sampler: LiquidFractionSampler | None,
    deposit_inert_kg_by_species: Mapping[str, Any] | None = None,
    deposit_reactive_kg_by_species: Mapping[str, Any] | None = None,
    initial_deposit_kg_by_species: Mapping[str, Any] | None = None,
    x_star: float | None = None,
) -> dict[str, Any]:
    """Build one terminal segment record from cumulative interface deposits.

    ``deposit_kg_by_species`` is the cumulative measured deposit vector at the
    end of this campaign. ``initial_deposit_kg_by_species`` is its prior state;
    inert and reactive branch maps remain per-campaign increments for forecasts.
    """

    raw_composition = interface_chemistry.get("composition")
    if not isinstance(raw_composition, Mapping) or not raw_composition:
        raise LinerCorrosionRefusal("liner_composition_missing")
    liner = validate_oxide_composition(raw_composition)
    deposits = _checked_deposit(deposit_kg_by_species)
    if initial_deposit_kg_by_species is not None and not isinstance(
        initial_deposit_kg_by_species, Mapping
    ):
        raise LinerCorrosionRefusal("initial_liner_deposit_state_invalid")
    initial_deposits = _checked_deposit(initial_deposit_kg_by_species or {})
    h = interface_chemistry.get("reacted_layer_depth_m")
    if h is None:
        raise LinerCorrosionRefusal("reacted_layer_depth_missing")
    rho = interface_chemistry.get("density_kg_m3")
    if rho is None:
        raise LinerCorrosionRefusal("liner_density_missing")
    margin = interface_chemistry.get("margin_C")
    if margin is None:
        raise LinerCorrosionRefusal("temperature_margin_missing")
    horizon = interface_chemistry.get("horizon_campaigns")
    if horizon is None:
        raise LinerCorrosionRefusal("campaign_horizon_missing")
    area = _positive(area_m2, "area_m2")
    service_C = _finite_real(service_temperature_C, "service_temperature_C")
    if service_C <= -CELSIUS_TO_KELVIN_OFFSET:
        raise LinerCorrosionRefusal("liner_service_temperature_invalid")
    pressure = (
        None if pressure_bar is None else _positive(pressure_bar, "pressure_bar")
    )
    depth = _positive(h, "reacted_layer_depth_m")
    density = _positive(rho, "density_kg_m3")
    margin_C = _finite_real(margin, "margin_C")
    if margin_C < 0.0:
        raise LinerCorrosionRefusal("temperature_margin_invalid")
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 0:
        raise LinerCorrosionRefusal("campaign_horizon_invalid")

    tier = str(interface_chemistry.get("criterion_tier") or "B").upper()
    table = interface_chemistry.get("invariant_table")
    interface_state = _interface_state_at_depth(
        liner,
        deposits,
        density_kg_m3=density,
        area_m2=area,
        reacted_layer_depth_m=depth,
        service_temperature_C=service_C,
        margin_C=margin_C,
        pressure_bar=pressure,
        criterion_tier=tier,
        invariant_table=table,
        sampler=sampler,
    )
    mixed = interface_state["mixed"]
    refusals: list[dict[str, str]] = []
    rating_cap_refusal = interface_state.get("rating_cap_refusal")
    if isinstance(rating_cap_refusal, Mapping):
        refusals.append(dict(rating_cap_refusal))
    flags = list(mixed["flags"])
    flags.extend((TEMPERATURE_MARGIN_FLAG, HORIZON_FLAG))
    raw_enamel = interface_chemistry.get("enamel")
    if raw_enamel is None:
        enamel: Mapping[str, Any] = {}
    elif not isinstance(raw_enamel, Mapping):
        raise LinerCorrosionRefusal("liner_enamel_config_invalid")
    else:
        enamel = raw_enamel
    if enamel:
        if (
            enamel.get("composition") != "same_as_body"
            or enamel.get("state") != "glassy"
        ):
            raise LinerCorrosionRefusal("liner_enamel_config_invalid")
        flags.append(ENAMEL_GLASS_STATE_FLAG)

    raw_porosity = interface_chemistry.get("porosity_fraction")
    from simulator.furnace_materials import (
        InterfaceChemistryRefusal,
        require_interface_porosity,
    )

    try:
        require_interface_porosity(interface_chemistry)
    except InterfaceChemistryRefusal as exc:
        refusals.append({"reason": exc.reason, "detail": exc.detail})

    first_melt_C = interface_state["first_melt_T_C"]
    liquid_fraction = interface_state["liquid_fraction"]
    onset = bool(interface_state["onset"])
    melt_activity = interface_state.get("melt_activity")
    if isinstance(melt_activity, Mapping):
        flags.extend(melt_activity.get("flags", ()))
    sample_temperature_C = service_C
    if tier == "B":
        flags.append("tier_b_liquid_fraction_only_no_solidus_claim")

    species_aggressiveness: dict[str, Any] = {}
    if tier == "B" and sampler is not None and pressure is not None:
        for species, mass_kg in deposits.items():
            if mass_kg <= 0.0:
                continue
            try:
                base_reference = interface_state.get("pure_reference")
                if not isinstance(base_reference, Mapping):
                    raise LinerCorrosionRefusal(
                        "liner_bulk_melt_reference_missing",
                        f"{species} aggressiveness baseline",
                    )
                species_aggressiveness[species] = (
                    _species_aggressiveness_finite_difference_with_reference(
                        liner,
                        species,
                        sampler=sampler,
                        temperature_C=sample_temperature_C,
                        pressure_bar=pressure,
                        base_reference_profile=base_reference,
                    )
                )
            except LinerCorrosionRefusal as exc:
                refusals.append(exc.as_dict())
    elif tier == "A":
        for species, mass_kg in deposits.items():
            if mass_kg <= 0.0:
                continue
            try:
                species_aggressiveness[species] = (
                    species_aggressiveness_tier_a_finite_difference(
                        liner,
                        species,
                        invariant_table=table,
                    )
                )
            except LinerCorrosionRefusal as exc:
                refusals.append(exc.as_dict())
    if any(
        ENGINE_COMPOSITION_SENSITIVE_FLAG in result.get("flags", ())
        for result in species_aggressiveness.values()
        if isinstance(result, Mapping)
    ):
        flags.append(ENGINE_COMPOSITION_SENSITIVE_FLAG)

    dissolution_ratio: float | None = None
    dissolution_curve_continuity: list[dict[str, Any]] = []
    if x_star is None and deposits:
        try:
            # The margin gates current onset only. Saturation x* is a service-
            # temperature equilibrium quantity for the dissolution ratio.
            if tier == "A":
                if not isinstance(table, Mapping):
                    raise LinerCorrosionRefusal(
                        "tier_a_saturation_data_at_service_temperature_missing"
                    )
                x_star = _tier_a_saturated_deposit_fraction(
                    table,
                    liner,
                    deposits,
                    mixed["composition_wt_pct"],
                    service_C,
                )
            elif tier == "B":
                x_star, dissolution_curve_continuity = _tier_b_saturated_deposit_fraction(
                    sampler,
                    liner,
                    deposits,
                    temperature_C=service_C,
                    pressure_bar=pressure,
                    pure_reference_profile=interface_state["pure_reference"],
                    invariant_table=table,
                )
        except LinerCorrosionRefusal as exc:
            refusals.append(exc.as_dict())
    if x_star is not None:
        try:
            # x* is the oxide-equivalent deposit mass fraction, so the helper
            # returns kg liner / kg oxide equivalent. The exported field is
            # per kg of measured deposit species; convert bases through the
            # same species-to-oxide owner used by interface mixing.
            oxide_equivalent_kg = math.fsum(
                deposit_oxide_equivalent_mass_by_oxide(deposits).values()
            )
            raw_deposit_kg = math.fsum(deposits.values())
            if raw_deposit_kg <= 0.0:
                raise LinerCorrosionRefusal(
                    "deposit_mass_missing_for_dissolution_ratio"
                )
            dissolution_ratio = post_onset_dissolution_ratio(x_star) * (
                oxide_equivalent_kg / raw_deposit_kg
            )
        except LinerCorrosionRefusal as exc:
            refusals.append(exc.as_dict())
        if dissolution_ratio is not None:
            flags.append(DISSOLUTION_KINETICS_FLAG)
        if dissolution_curve_continuity:
            flags.append(ENGINE_COMPOSITION_SENSITIVE_FLAG)
    if onset:
        flags.append(CORROSION_NO_RATE_FLAG)

    recurrence: dict[str, Any] | None = None
    if deposit_inert_kg_by_species is None:
        refusals.append(
            LinerCorrosionRefusal("inert_deposit_branch_missing").as_dict()
        )
    else:
        try:
            recurrence = _run_corrosion_recurrence(
                liner,
                initial_deposits,
                deposit_inert_kg_by_species,
                deposit_reactive_kg_by_species,
                density_kg_m3=density,
                area_m2=area,
                reacted_layer_depth_m=depth,
                temperature_C=service_C,
                pressure_bar=pressure,
                margin_C=margin_C,
                horizon=horizon,
                criterion_tier=tier,
                invariant_table=table,
                sampler=sampler,
            )
        except LinerCorrosionRefusal as exc:
            refusals.append(exc.as_dict())
        if recurrence and recurrence.get("reactive_branch_refusal"):
            refusals.append(dict(recurrence["reactive_branch_refusal"]))

    depth_sensitivity: list[dict[str, Any]] = []
    for sensitivity_depth in reacted_layer_depths(depth):
        try:
            if math.isclose(sensitivity_depth, depth, rel_tol=0.0, abs_tol=0.0):
                sensitivity_state = interface_state
                sensitivity_recurrence = recurrence
            else:
                sensitivity_state = _interface_state_at_depth(
                    liner,
                    deposits,
                    density_kg_m3=density,
                    area_m2=area,
                    reacted_layer_depth_m=sensitivity_depth,
                    service_temperature_C=service_C,
                    margin_C=margin_C,
                    pressure_bar=pressure,
                    criterion_tier=tier,
                    invariant_table=table,
                    sampler=sampler,
                )
                sensitivity_recurrence = (
                    _run_corrosion_recurrence(
                        liner,
                        initial_deposits,
                        deposit_inert_kg_by_species,
                        deposit_reactive_kg_by_species,
                        density_kg_m3=density,
                        area_m2=area,
                        reacted_layer_depth_m=sensitivity_depth,
                        temperature_C=service_C,
                        pressure_bar=pressure,
                        margin_C=margin_C,
                        horizon=horizon,
                        criterion_tier=tier,
                        invariant_table=table,
                        sampler=sampler,
                    )
                    if deposit_inert_kg_by_species is not None
                    else None
                )
            depth_sensitivity.append(
                {
                    "reacted_layer_depth_m": sensitivity_depth,
                    "interface_wt_pct": sensitivity_state["mixed"][
                        "composition_wt_pct"
                    ],
                    "first_melt_T_C": sensitivity_state["first_melt_T_C"],
                    "liquid_fraction": sensitivity_state["liquid_fraction"],
                    "onset": sensitivity_state["onset"],
                    "rating_temperature_C": sensitivity_state[
                        "rating_temperature_C"
                    ],
                    "rating_cap_temperature_C": sensitivity_state.get(
                        "rating_cap_temperature_C"
                    ),
                    "rating_cap_source": sensitivity_state.get("rating_cap_source"),
                    "rating_cap_refusal": sensitivity_state.get(
                        "rating_cap_refusal"
                    ),
                    "melt_activity": sensitivity_state.get("melt_activity"),
                    "campaigns_to_runaway": (
                        sensitivity_recurrence["campaigns_to_runaway"]
                        if sensitivity_recurrence
                        else None
                    ),
                    "coupled_campaigns_to_runaway": (
                        sensitivity_recurrence["coupled_campaigns_to_runaway"]
                        if sensitivity_recurrence
                        else None
                    ),
                }
            )
        except LinerCorrosionRefusal as exc:
            depth_sensitivity.append(
                {
                    "reacted_layer_depth_m": sensitivity_depth,
                    "status": "refused",
                    "refusal": exc.as_dict(),
                }
            )

    return {
        "diagnostic_only": True,
        "citation": interface_chemistry.get("citation"),
        "generated_provenance": (
            dict(interface_chemistry["generated_provenance"])
            if isinstance(interface_chemistry.get("generated_provenance"), Mapping)
            else None
        ),
        "criterion_tier": tier,
        "liner_wt_pct": liner,
        "interface_wt_pct": mixed["composition_wt_pct"],
        "reacted_layer_depth_m": depth,
        "reacted_layer_depth_sensitivity_m": list(reacted_layer_depths(depth)),
        "depth_sensitivity": depth_sensitivity,
        "porosity_fraction": raw_porosity,
        "deposit_kg_by_species": deposits,
        "first_melt_T_C": first_melt_C,
        "rating_temperature_C": interface_state["rating_temperature_C"],
        "rating_cap_temperature_C": interface_state.get(
            "rating_cap_temperature_C"
        ),
        "rating_cap_source": interface_state.get("rating_cap_source"),
        "melt_activity": melt_activity,
        "service_T_C": service_C,
        "service_pressure_bar": pressure,
        "enamel": dict(enamel),
        "margin_C": margin_C,
        "liquid_fraction": liquid_fraction,
        "onset": onset,
        "species_aggressiveness": species_aggressiveness,
        "liner_consumed_kg_per_kg_deposit": dissolution_ratio,
        "dissolution_curve_continuity": dissolution_curve_continuity or None,
        "cumulative_deposit_oxide_equivalent_kg_by_oxide": (
            deposit_oxide_equivalent_mass_by_oxide(deposits)
        ),
        "sticking_state": recurrence["sticking_state"] if recurrence else None,
        "campaigns_to_runaway": (
            recurrence["campaigns_to_runaway"] if recurrence else None
        ),
        "coupled_campaigns_to_runaway": (
            recurrence["coupled_campaigns_to_runaway"] if recurrence else None
        ),
        "flags": list(dict.fromkeys((*flags, *(recurrence["flags"] if recurrence else ())))),
        "refusals": refusals,
    }


def _trace_deposits_by_segment(trace: Any) -> dict[str, dict[str, float]]:
    from simulator.trace import nested_wall_deposit_by_segment_species

    raw = getattr(trace, "wall_deposit_by_segment_species_kg", {}) or {}
    if not isinstance(raw, Mapping):
        raise LinerCorrosionRefusal("inert_deposit_trace_invalid")
    try:
        deposits = nested_wall_deposit_by_segment_species(raw)
    except ValueError as exc:
        raise LinerCorrosionRefusal(
            "inert_deposit_trace_invalid", str(exc)
        ) from exc
    if any(mass < 0.0 for by_species in deposits.values() for mass in by_species.values()):
        raise LinerCorrosionRefusal(
            "inert_deposit_trace_invalid", "wall deposit mass must be non-negative"
        )
    return deposits


def _reactive_deposit_upper_bound_for_segment(
    model: Any,
    segment: Any,
    inert_deposit_kg_by_species: Mapping[str, float],
    snapshots: Sequence[Any],
) -> dict[str, float]:
    """Replay recorded species supply through the wall owner at alpha=1, P_eq=0."""

    from simulator.wall_deposition import (
        SECONDS_PER_HOUR,
        wall_deposit_candidate_for_surface_kg,
    )

    if not snapshots:
        raise LinerCorrosionRefusal("reactive_deposit_operating_history_missing")

    records_by_species: dict[str, list[tuple[Any, Mapping[str, Any]]]] = {}
    inert_species = set(inert_deposit_kg_by_species)
    for snapshot in snapshots:
        candidates = getattr(
            snapshot, "wall_deposition_rate_shadow_candidate", {}
        ) or {}
        by_species = (
            candidates.get(str(segment.name), {})
            if isinstance(candidates, Mapping)
            else {}
        )
        if not isinstance(by_species, Mapping):
            continue
        for raw_species, record in by_species.items():
            species = str(raw_species)
            if not isinstance(record, Mapping):
                raise LinerCorrosionRefusal(
                    "reactive_deposit_operating_state_invalid",
                    f"{segment.name}:{species}: candidate is not a mapping",
                )
            supply_mol_s = _finite_real(
                record.get("available_supply_mol_s"),
                f"available_supply_mol_s[{segment.name},{species}]",
            )
            if supply_mol_s < 0.0:
                raise LinerCorrosionRefusal(
                    "reactive_deposit_operating_state_invalid",
                    f"{segment.name}:{species}: negative available supply",
                )
            if supply_mol_s > 0.0 or species in inert_species:
                records_by_species.setdefault(species, []).append(
                    (snapshot, record)
                )
    species_to_replay = sorted(inert_species | set(records_by_species))

    result: dict[str, float] = {}
    for species in species_to_replay:
        inert_mass_kg = float(inert_deposit_kg_by_species.get(species, 0.0))
        try:
            formula = resolve_species_formula(
                species, getattr(model, "species_formula_registry", None)
            )
        except Exception as exc:  # noqa: BLE001 - unknown formula is typed below
            raise LinerCorrosionRefusal(
                "reactive_deposit_species_formula_missing", species
            ) from exc
        molar_mass_kg_mol = formula.molar_mass_kg_per_mol()
        if molar_mass_kg_mol is None or molar_mass_kg_mol <= 0.0:
            raise LinerCorrosionRefusal(
                "reactive_deposit_species_formula_missing", species
            )

        reactive_mass_kg = 0.0
        saw_candidate = False
        T_cond_C = getattr(model, "condensation_temperatures_C", {}).get(species)
        if T_cond_C is None:
            raise LinerCorrosionRefusal(
                "reactive_deposit_condensation_temperature_missing", species
            )
        melt_temperature_C = getattr(
            getattr(model, "melt", None),
            "temperature_C",
            getattr(model, "gas_temperature_C", None),
        )
        for snapshot, record in records_by_species.get(species, ()):
            duration_h = _positive(
                getattr(snapshot, "duration_h", None), "snapshot.duration_h"
            )
            saw_candidate = True
            supply_mol_s = _finite_real(
                record.get("available_supply_mol_s"),
                f"available_supply_mol_s[{segment.name},{species}]",
            )
            if supply_mol_s < 0.0:
                raise LinerCorrosionRefusal(
                    "reactive_deposit_operating_state_invalid",
                    f"{segment.name}:{species}: negative available supply",
                )
            if supply_mol_s == 0.0:
                continue
            pressure = _finite_real(
                record.get("species_partial_pressure_pa"),
                f"species_partial_pressure_pa[{segment.name},{species}]",
            )
            if pressure < 0.0:
                raise LinerCorrosionRefusal(
                    "reactive_deposit_operating_state_invalid",
                    f"{segment.name}:{species}: negative partial pressure",
                )
            surface = record.get("surface")
            wall_temperature_K = (
                surface.get("wall_temperature_K")
                if isinstance(surface, Mapping)
                else None
            )
            wall_temperature_C = _finite_real(
                wall_temperature_K, "wall_temperature_K"
            ) - CELSIUS_TO_KELVIN_OFFSET
            if wall_temperature_C <= -CELSIUS_TO_KELVIN_OFFSET:
                raise LinerCorrosionRefusal(
                    "reactive_deposit_operating_state_invalid",
                    f"{segment.name}:{species}: wall temperature below absolute zero",
                )
            if pressure == 0.0:
                continue

            proxy = SimpleNamespace(
                materials=getattr(model, "materials", None),
                vapor_pressure_data=getattr(model, "vapor_pressure_data", None),
                species_formula_registry=getattr(
                    model, "species_formula_registry", None
                ),
                wall_temperature_input_refusals={},
                overhead_pressure_mbar=getattr(
                    model, "overhead_pressure_mbar", None
                ),
                wall_species_partial_pressures_pa={species: pressure},
                wall_species_partial_pressures_pa_by_segment={
                    str(segment.name): {species: pressure}
                },
                _species_partial_pressures_configured=True,
                species_partial_pressures_mbar={},
                gas_temperature_C=getattr(model, "gas_temperature_C", None),
                pipe_diameter_m=getattr(model, "pipe_diameter_m", None),
                regime_factor=getattr(model, "regime_factor", None),
                stir_factor=getattr(model, "stir_factor", None),
                radial_stir_factor=getattr(model, "radial_stir_factor", None),
                carrier_gas=getattr(model, "carrier_gas", None),
                last_wall_deposition_rate_shadow_candidate={},
                last_sticking_alpha_provenance_notice={},
            )
            reactive_segment = replace(
                segment, wall_temperature_C=wall_temperature_C
            )
            try:
                candidate = wall_deposit_candidate_for_surface_kg(
                    proxy,
                    species=species,
                    rate_kg_hr=supply_mol_s
                    * molar_mass_kg_mol
                    * SECONDS_PER_HOUR,
                    T_cond_C=float(T_cond_C),
                    melt_temperature_C=float(melt_temperature_C),
                    wall_temperature_C=wall_temperature_C,
                    surface_area_m2=float(segment.surface_area_m2),
                    segment=reactive_segment,
                    wall_surface_state={"alpha_s": 1.0, "P_eq_Pa": 0.0},
                )
            except Exception as exc:  # noqa: BLE001 - branch refusal is diagnostic
                raise LinerCorrosionRefusal(
                    "reactive_deposit_seam_refused",
                    str(getattr(exc, "reason", None) or exc),
                ) from exc
            if isinstance(candidate, Mapping):
                raise LinerCorrosionRefusal(
                    "reactive_deposit_seam_refused",
                    str(
                        candidate.get("reason")
                        or candidate.get("status")
                        or species
                    ),
                )
            candidate_kg_hr = _finite_real(
                candidate, "reactive_deposit_candidate"
            )
            reactive_mass_kg += candidate_kg_hr * duration_h
        if not saw_candidate:
            raise LinerCorrosionRefusal(
                "reactive_deposit_operating_state_missing",
                f"{segment.name}:{species}",
            )
        if reactive_mass_kg + 1.0e-15 < inert_mass_kg:
            raise LinerCorrosionRefusal(
                "reactive_deposit_upper_bound_below_inert",
                f"{segment.name}:{species}",
            )
        result[species] = reactive_mass_kg
    return result


def build_liner_state_for_run(
    material_id: str,
    simulator: Any,
    trace: Any,
    *,
    initial_deposit_kg_by_segment_species: Mapping[
        str, Mapping[str, Any]
    ] | None = None,
    run_deposit_kg_by_segment_species: Mapping[
        str, Mapping[str, Any]
    ] | None = None,
) -> dict[str, Any] | None:
    """Build terminal liner metadata in the physics owner module."""

    from simulator.furnace_materials import (
        InterfaceChemistryRefusal,
        load_furnace_materials,
        load_interface_chemistry,
        load_interface_invariant_table,
    )
    from simulator.trace import wall_temperature_by_segment_C

    catalog = load_furnace_materials()
    material = catalog.get(str(material_id))
    raw_chemistry = (
        material.get("interface_chemistry") if isinstance(material, Mapping) else None
    )
    if not isinstance(raw_chemistry, Mapping):
        return None

    model = getattr(simulator, "condensation_model", None)
    pipe_segments = tuple(getattr(model, "pipe_segments", ()) or ())
    if run_deposit_kg_by_segment_species is None:
        inert_by_segment = _trace_deposits_by_segment(trace)
    else:
        if not isinstance(run_deposit_kg_by_segment_species, Mapping) or any(
            not isinstance(name, str)
            or not isinstance(masses, Mapping)
            for name, masses in run_deposit_kg_by_segment_species.items()
        ):
            raise LinerCorrosionRefusal("inert_deposit_trace_invalid")
        try:
            inert_by_segment = {
                str(name): _checked_deposit(masses)
                for name, masses in run_deposit_kg_by_segment_species.items()
            }
        except LinerCorrosionRefusal as exc:
            raise LinerCorrosionRefusal(
                "inert_deposit_trace_invalid", exc.detail
            ) from exc
    initial_by_segment = (
        {}
        if initial_deposit_kg_by_segment_species is None
        else initial_deposit_kg_by_segment_species
    )
    if not isinstance(initial_by_segment, Mapping):
        raise LinerCorrosionRefusal("initial_liner_deposit_state_invalid")
    if any(not isinstance(name, str) for name in initial_by_segment):
        raise LinerCorrosionRefusal("initial_liner_deposit_state_invalid")
    segment_names = sorted(
        {str(segment.name) for segment in pipe_segments}
        | set(inert_by_segment)
        | {str(name) for name in initial_by_segment}
    ) or ["_run"]
    temperatures = wall_temperature_by_segment_C(simulator)
    try:
        chemistry = load_interface_chemistry(str(material_id), catalog=catalog)
        if chemistry is None:
            return None
        tier = str(chemistry.get("criterion_tier") or "B").upper()
        if tier == "A" or chemistry.get("invariant_table") or chemistry.get(
            "invariant_table_file"
        ):
            chemistry["invariant_table"] = load_interface_invariant_table(
                str(material_id), catalog=catalog
            )
    except InterfaceChemistryRefusal as exc:
        refusal = {"reason": exc.reason, "detail": exc.detail}
        return {
            "schema": "liner-state-v1",
            "segments": {
                name: {
                    "status": "refused",
                    "refusal": refusal,
                    "flags": list(raw_chemistry.get("flags", ()) or ()),
                }
                for name in segment_names
            },
        }

    segments_by_name = {str(segment.name): segment for segment in pipe_segments}
    sampler = MageminLinerSampler() if tier == "B" else None
    pressure_mbar = getattr(model, "overhead_pressure_mbar", None)
    pressure_bar = (
        None
        if pressure_mbar is None
        else float(pressure_mbar) * PA_PER_MBAR / PA_PER_BAR
    )
    records: dict[str, Any] = {}
    snapshots = tuple(getattr(trace, "snapshots", ()) or ())
    try:
        for name in segment_names:
            segment = segments_by_name.get(name)
            if segment is None:
                records[name] = {
                    "status": "refused",
                    "refusal": {"reason": "liner_segment_geometry_missing"},
                }
                continue
            service_temperature = temperatures.get(name)
            if service_temperature is None:
                records[name] = {
                    "status": "refused",
                    "refusal": {"reason": "liner_service_temperature_missing"},
                }
                continue
            inert = inert_by_segment.get(name, {})
            try:
                raw_initial = initial_by_segment.get(name, {})
                if not isinstance(raw_initial, Mapping):
                    raise LinerCorrosionRefusal(
                        "initial_liner_deposit_state_invalid", name
                    )
                initial = _checked_deposit(raw_initial)
                cumulative = dict(initial)
                for species, mass_kg in _checked_deposit(inert).items():
                    cumulative[species] = cumulative.get(species, 0.0) + mass_kg
            except LinerCorrosionRefusal as exc:
                records[name] = {
                    "status": "refused",
                    "refusal": exc.as_dict(),
                }
                continue
            try:
                reactive = _reactive_deposit_upper_bound_for_segment(
                    model, segment, inert, snapshots
                )
            except LinerCorrosionRefusal as exc:
                reactive = None
                reactive_refusal = exc.as_dict()
            else:
                reactive_refusal = None
            try:
                state = build_liner_state_segment(
                    chemistry,
                    cumulative,
                    area_m2=float(segment.surface_area_m2),
                    service_temperature_C=service_temperature,
                    pressure_bar=pressure_bar,
                    sampler=sampler,
                    deposit_inert_kg_by_species=inert,
                    deposit_reactive_kg_by_species=reactive,
                    initial_deposit_kg_by_species=initial,
                )
            except LinerCorrosionRefusal as exc:
                records[name] = {
                    "status": "refused",
                    "refusal": exc.as_dict(),
                    "flags": list(raw_chemistry.get("flags", ()) or ()),
                }
                continue
            if reactive_refusal is not None:
                state["refusals"].append(reactive_refusal)
            state["status"] = "partial" if state["refusals"] else "ok"
            state["deposit_inert_kg_by_species"] = dict(inert)
            state["deposit_reactive_kg_by_species"] = (
                None if reactive is None else dict(reactive)
            )
            records[name] = state
    finally:
        if sampler is not None:
            sampler.close()
    if sampler is not None and sampler.flags:
        for state in records.values():
            if state.get("status") in {"ok", "partial"}:
                state["flags"] = list(
                    dict.fromkeys((*state.get("flags", ()), *sampler.flags))
                )
    return {"schema": "liner-state-v1", "segments": records}


def attach_liner_state_metadata(
    run_metadata: dict[str, Any],
    material_id: str,
    simulator: Any,
    trace: Any,
) -> dict[str, Any] | None:
    """Attach the selected material's diagnostic to terminal run metadata."""

    state = build_liner_state_for_run(material_id, simulator, trace)
    if state is not None:
        run_metadata["liner_state"] = state
    return state


def _rounded_composition(composition_wt_pct: Mapping[str, Any]) -> tuple[tuple[str, float], ...]:
    return tuple(
        sorted(
            (
                str(oxide),
                round(_finite_real(value, f"composition_wt_pct[{oxide!r}]"), MAGEMIN_CACHE_DECIMAL_PLACES),
            )
            for oxide, value in composition_wt_pct.items()
        )
    )


def _rounded_pressure_bar(value: Any) -> float:
    # Significant digits preserve vacuum pressures such as 1e-6 bar; rounding
    # them to six decimal places would collapse distinct requests to zero.
    return float(format(_positive(value, "pressure_bar"), ".8g"))


def magemin_request_cache_key(
    *,
    engine: str,
    version: int,
    composition_wt_pct: Mapping[str, Any],
    temperature_C: float,
    pressure_bar: float,
    fO2_log: float,
) -> tuple[Any, ...]:
    """Request identity: engine, hand-bumped adapter version, rounded inputs."""

    engine_id = str(engine).strip()
    if not engine_id:
        raise LinerCorrosionRefusal("magemin_cache_engine_missing")
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        raise LinerCorrosionRefusal("magemin_cache_version_invalid")
    return (
        engine_id,
        version,
        _rounded_composition(composition_wt_pct),
        round(_finite_real(temperature_C, "temperature_C"), MAGEMIN_CACHE_DECIMAL_PLACES),
        _rounded_pressure_bar(pressure_bar),
        round(_finite_real(fO2_log, "fO2_log"), MAGEMIN_CACHE_DECIMAL_PLACES),
    )


def _element_moles_from_oxide_weights(
    composition_wt_pct: Mapping[str, float],
) -> dict[str, float]:
    result: dict[str, float] = {}
    for oxide, wt_pct in composition_wt_pct.items():
        try:
            formula = parse_formula(oxide)
        except Exception as exc:  # noqa: BLE001 - malformed engine component
            raise LinerCorrosionRefusal(
                ELEMENT_CLOSURE_FLAG, f"cannot parse oxide {oxide!r}"
            ) from exc
        molar_mass = formula.molar_mass_g_per_mol()
        if molar_mass is None or molar_mass <= 0.0:
            raise LinerCorrosionRefusal(ELEMENT_CLOSURE_FLAG, oxide)
        oxide_moles_per_100g = float(wt_pct) / float(molar_mass)
        for element, atom_moles in formula.atom_moles(oxide_moles_per_100g).items():
            result[element] = result.get(element, 0.0) + float(atom_moles)
    return result


def _check_element_closure(
    bulk_composition_wt_pct: Mapping[str, float],
    result: Any,
) -> bool:
    """Verify reported element closure, returning False when evidence is incomplete."""
    from simulator.melt_backend.base import (
        LiquidFractionInvalidError,
        liquid_fraction_from_phase_masses,
    )

    phase_masses = getattr(result, "phase_masses_kg", None)
    phase_compositions = getattr(result, "phase_compositions", None)
    if not isinstance(phase_masses, Mapping) or not phase_masses:
        raise LinerCorrosionRefusal(ELEMENT_CLOSURE_FLAG, "phase masses are absent")
    try:
        mass_fraction_liquid = liquid_fraction_from_phase_masses(phase_masses)
    except LiquidFractionInvalidError as exc:
        raise LinerCorrosionRefusal(ELEMENT_CLOSURE_FLAG, str(exc)) from exc
    if mass_fraction_liquid is None:
        raise LinerCorrosionRefusal(ELEMENT_CLOSURE_FLAG, "zero total phase mass")
    reported_fraction = getattr(result, "liquid_fraction", None)
    if reported_fraction is not None and not math.isclose(
        _checked_liquid_fraction(reported_fraction),
        mass_fraction_liquid,
        rel_tol=0.0,
        abs_tol=ELEMENT_CLOSURE_REL_TOLERANCE,
    ):
        raise LinerCorrosionRefusal(
            ELEMENT_CLOSURE_FLAG, "reported liquid fraction disagrees with phase masses"
        )
    masses: dict[str, float] = {}
    all_phase_masses: dict[str, float] = {}
    for phase, raw_mass in phase_masses.items():
        mass = _finite_real(raw_mass, f"phase_masses_kg[{phase!r}]")
        if mass < 0.0:
            raise LinerCorrosionRefusal(ELEMENT_CLOSURE_FLAG, "negative phase mass")
        all_phase_masses[str(phase)] = mass
        if mass > 0.0:
            masses[str(phase)] = mass
    total_mass = math.fsum(masses.values())
    if total_mass <= 0.0:
        raise LinerCorrosionRefusal(ELEMENT_CLOSURE_FLAG, "zero total phase mass")

    input_composition = validate_oxide_composition(bulk_composition_wt_pct)
    reported_bulk = getattr(result, "bulk_composition_wt_pct", None)
    if isinstance(reported_bulk, Mapping) and reported_bulk:
        _require_element_compositions_close(
            input_composition,
            validate_oxide_composition(reported_bulk),
            context="reported bulk composition",
        )
    if isinstance(phase_compositions, Mapping):
        for phase, raw_composition in phase_compositions.items():
            if not isinstance(raw_composition, Mapping) or not raw_composition:
                continue
            phase_name = str(phase)
            phase_mass = all_phase_masses.get(phase_name)
            if phase_mass is None:
                raise LinerCorrosionRefusal(
                    ELEMENT_CLOSURE_FLAG,
                    f"reported phase {phase_name} has no matching mass",
                )
            phase_composition = validate_oxide_composition(raw_composition)
            _require_phase_inventory_not_above_bulk(
                input_composition,
                phase_composition,
                phase_mass / total_mass,
                phase_name,
            )
    complete_phase_compositions = isinstance(phase_compositions, Mapping) and all(
        isinstance(phase_compositions.get(phase), Mapping)
        and bool(phase_compositions.get(phase))
        for phase in masses
    )
    if complete_phase_compositions:
        output_composition: dict[str, float] = {}
        for phase, mass in masses.items():
            phase_comp = validate_oxide_composition(phase_compositions[phase])
            mass_fraction = mass / total_mass
            for oxide, wt_pct in phase_comp.items():
                output_composition[oxide] = output_composition.get(oxide, 0.0) + (
                    mass_fraction * wt_pct
                )
        _require_element_compositions_close(
            input_composition, output_composition, context="phase assemblage"
        )
        return True

    liquid_composition = getattr(result, "liquid_composition_wt_pct", None)
    if isinstance(liquid_composition, Mapping) and liquid_composition:
        liquid_composition = validate_oxide_composition(liquid_composition)
    else:
        liquid_composition = None

    solid_composition = getattr(result, "solid_composition_wt_pct", None)
    if isinstance(solid_composition, Mapping) and solid_composition:
        solid_composition = validate_oxide_composition(solid_composition)
    else:
        solid_composition = None

    if mass_fraction_liquid <= ELEMENT_CLOSURE_REL_TOLERANCE:
        if solid_composition is not None:
            _require_element_compositions_close(
                input_composition,
                solid_composition,
                context="reported solid composition",
            )
            return True
        return False
    if mass_fraction_liquid >= 1.0 - ELEMENT_CLOSURE_REL_TOLERANCE:
        if liquid_composition is not None:
            _require_element_compositions_close(
                input_composition,
                liquid_composition,
                context="reported liquid composition",
            )
            return True
        return False

    if liquid_composition is not None and solid_composition is not None:
        output_composition = {}
        solid_fraction = 1.0 - mass_fraction_liquid
        for oxide in set(input_composition) | set(liquid_composition) | set(
            solid_composition
        ):
            output_composition[oxide] = (
                mass_fraction_liquid * liquid_composition.get(oxide, 0.0)
                + solid_fraction * solid_composition.get(oxide, 0.0)
            )
        _require_element_compositions_close(
            input_composition,
            output_composition,
            context="reported liquid and solid compositions",
        )
        return True

    # Incomplete phase compositions cannot establish closure. They can still
    # contradict it when a reported phase alone contains more oxide than the
    # bulk inventory permits; only that contradiction refuses the sample.
    if liquid_composition is not None:
        _require_phase_inventory_not_above_bulk(
            input_composition,
            liquid_composition,
            mass_fraction_liquid,
            "liquid",
        )
    if solid_composition is not None:
        _require_phase_inventory_not_above_bulk(
            input_composition,
            solid_composition,
            1.0 - mass_fraction_liquid,
            "solid",
        )
    return False


def _require_phase_inventory_not_above_bulk(
    bulk: Mapping[str, float],
    phase_composition: Mapping[str, float],
    phase_mass_fraction: float,
    phase_name: str,
) -> None:
    bulk_elements = _element_moles_from_oxide_weights(bulk)
    phase_elements = _element_moles_from_oxide_weights(phase_composition)
    for element in set(bulk_elements) | set(phase_elements):
        phase_moles = phase_mass_fraction * phase_elements.get(element, 0.0)
        bulk_moles = bulk_elements.get(element, 0.0)
        scale = max(abs(bulk_moles), 1.0e-12)
        if (phase_moles - bulk_moles) / scale > ELEMENT_CLOSURE_REL_TOLERANCE:
            raise LinerCorrosionRefusal(
                ELEMENT_CLOSURE_FLAG,
                f"reported {phase_name} inventory exceeds bulk {element} inventory",
            )


def _require_element_compositions_close(
    expected_composition: Mapping[str, float],
    actual_composition: Mapping[str, float],
    *,
    context: str,
) -> None:
    output_elements = _element_moles_from_oxide_weights(actual_composition)
    input_elements = _element_moles_from_oxide_weights(expected_composition)
    for element in set(input_elements) | set(output_elements):
        expected = input_elements.get(element, 0.0)
        actual = output_elements.get(element, 0.0)
        scale = max(abs(expected), 1.0e-12)
        if abs(actual - expected) / scale > ELEMENT_CLOSURE_REL_TOLERANCE:
            raise LinerCorrosionRefusal(
                ELEMENT_CLOSURE_FLAG,
                f"{context} {element} closure differs by "
                f"{abs(actual - expected) / scale:.6g}",
            )


@dataclass(frozen=True)
class TabulatedLiquidFractionCurve:
    """Linear interpolation of a cached MAGEMin phi(f) table."""

    liner_composition_wt_pct: Mapping[str, float]
    deposit_composition_wt_pct: Mapping[str, float]
    temperature_C: float
    pressure_bar: float
    points: tuple[tuple[float, float], ...]

    def __call__(
        self,
        composition_wt_pct: Mapping[str, float],
        temperature_C: float,
        pressure_bar: float,
    ) -> float:
        if (
            round(float(temperature_C), MAGEMIN_CACHE_DECIMAL_PLACES)
            != round(self.temperature_C, MAGEMIN_CACHE_DECIMAL_PLACES)
            or _rounded_pressure_bar(pressure_bar)
            != _rounded_pressure_bar(self.pressure_bar)
        ):
            raise LinerCorrosionRefusal("magemin_curve_request_mismatch")
        fraction = _infer_deposit_fraction(
            self.liner_composition_wt_pct,
            self.deposit_composition_wt_pct,
            composition_wt_pct,
        )
        return interpolate_liquid_fraction_curve(self.points, fraction)


def _infer_deposit_fraction(
    liner_composition_wt_pct: Mapping[str, float],
    deposit_composition_wt_pct: Mapping[str, float],
    composition_wt_pct: Mapping[str, float],
) -> float:
    oxides = set(liner_composition_wt_pct) | set(deposit_composition_wt_pct)
    delta = {
        oxide: deposit_composition_wt_pct.get(oxide, 0.0)
        - liner_composition_wt_pct.get(oxide, 0.0)
        for oxide in oxides
    }
    denominator = math.fsum(value * value for value in delta.values())
    if denominator == 0.0:
        return 0.0
    numerator = math.fsum(
        delta[oxide]
        * (
            _finite_real(composition_wt_pct.get(oxide, 0.0), f"{oxide} wt%")
            - liner_composition_wt_pct.get(oxide, 0.0)
        )
        for oxide in oxides
    )
    fraction = numerator / denominator
    if fraction < -1e-8 or fraction > 1.0 + 1e-8:
        raise LinerCorrosionRefusal("magemin_curve_composition_mismatch")
    expected = {
        oxide: (1.0 - fraction) * liner_composition_wt_pct.get(oxide, 0.0)
        + fraction * deposit_composition_wt_pct.get(oxide, 0.0)
        for oxide in oxides
    }
    for oxide in oxides:
        observed = _finite_real(composition_wt_pct.get(oxide, 0.0), f"{oxide} wt%")
        if not math.isclose(observed, expected[oxide], rel_tol=0.0, abs_tol=1e-5):
            raise LinerCorrosionRefusal("magemin_curve_composition_mismatch")
    return min(1.0, max(0.0, fraction))


def interpolate_liquid_fraction_curve(
    points: Sequence[tuple[float, float]], fraction: float
) -> float:
    """Piecewise-linear phi(f) interpolation inside the tabulated range."""

    value = _finite_real(fraction, "deposit_fraction")
    if value < 0.0 or value > 1.0 or len(points) < 2:
        raise LinerCorrosionRefusal("magemin_curve_fraction_out_of_range")
    fractions = [float(point[0]) for point in points]
    index = bisect_right(fractions, value)
    if index == 0 or index >= len(points):
        if value == fractions[0]:
            return _checked_liquid_fraction(points[0][1])
        if value == fractions[-1]:
            return _checked_liquid_fraction(points[-1][1])
        raise LinerCorrosionRefusal("magemin_curve_out_of_domain")
    low_f, low_phi = points[index - 1]
    high_f, high_phi = points[index]
    weight = (value - low_f) / (high_f - low_f)
    return _checked_liquid_fraction(float(low_phi) + weight * (float(high_phi) - float(low_phi)))


def _composition_at_fraction(
    liner: Mapping[str, float], deposit: Mapping[str, float], fraction: float
) -> dict[str, float]:
    oxides = set(liner) | set(deposit)
    return {
        oxide: (1.0 - fraction) * liner.get(oxide, 0.0)
        + fraction * deposit.get(oxide, 0.0)
        for oxide in oxides
        if (1.0 - fraction) * liner.get(oxide, 0.0)
        + fraction * deposit.get(oxide, 0.0)
        > 0.0
    }


class MageminLinerSampler:
    """Single-point MAGEMin adapter plus cached/interpolated phi(f) tables."""

    def __init__(self, backend: Any | None = None) -> None:
        if backend is None:
            from simulator.melt_backend.magemin import MAGEMinBackend

            backend = MAGEMinBackend()
        self._backend = backend
        self._initialized = False
        self._point_cache: dict[tuple[Any, ...], float] = {}
        self._sample_diagnostics: dict[tuple[Any, ...], dict[str, Any]] = {}
        self._curve_cache: dict[tuple[Any, ...], TabulatedLiquidFractionCurve] = {}
        self.flags: list[str] = []
        self.authority_level = "verified"

    @property
    def engine_version(self) -> str:
        version_reader = getattr(self._backend, "get_engine_version", None)
        return str(version_reader()) if callable(version_reader) else "unknown"

    def __call__(
        self,
        composition_wt_pct: Mapping[str, float],
        temperature_C: float,
        pressure_bar: float,
    ) -> float:
        composition = validate_oxide_composition(composition_wt_pct)
        temperature = _finite_real(temperature_C, "temperature_C")
        pressure = _positive(pressure_bar, "pressure_bar")
        from engines.magemin.domain import MAGEMinDomainGate

        valid, warnings, reason = MAGEMinDomainGate.validate_with_reason(composition)
        if not valid:
            raise LinerCorrosionRefusal(
                ENGINE_OUT_OF_DOMAIN_FLAG,
                f"{reason or 'magemin_domain_gate'}: {'; '.join(warnings)}",
            )
        key = magemin_request_cache_key(
            engine=MAGEMIN_ENGINE_ID,
            version=MAGEMIN_REQUEST_CACHE_VERSION,
            composition_wt_pct=composition,
            temperature_C=temperature,
            pressure_bar=pressure,
            fO2_log=MAGEMIN_FO2_LOG,
        )
        if key in self._point_cache:
            return self._point_cache[key]
        if not self._initialized:
            if not self._backend.initialize({"database": "ig"}):
                raise LinerCorrosionRefusal("engine_unavailable", MAGEMIN_ENGINE_ID)
            self._initialized = True
        result = self._backend.equilibrate(
            temperature,
            pressure_bar=pressure,
            fO2_log=MAGEMIN_FO2_LOG,
            projected_oxide_wt_pct=composition,
        )
        if getattr(result, "status", None) != "ok":
            status = str(getattr(result, "status", "unknown"))
            reason = (
                ENGINE_OUT_OF_DOMAIN_FLAG
                if status == "out_of_domain"
                else "engine_nonconvergence"
                if status == "not_converged"
                else "engine_unavailable"
                if status == "unavailable"
                else "engine_equilibrium_refused"
            )
            raise LinerCorrosionRefusal(reason, "; ".join(getattr(result, "warnings", ()) or ()))
        closure_verified = _check_element_closure(composition, result)
        if not closure_verified:
            if ELEMENT_CLOSURE_UNVERIFIED_FLAG not in self.flags:
                self.flags.append(ELEMENT_CLOSURE_UNVERIFIED_FLAG)
            self.authority_level = "predicted"
        fraction = getattr(result, "liquid_fraction", None)
        if fraction is None:
            raise LinerCorrosionRefusal("engine_liquid_fraction_missing")
        liquid_fraction = _checked_liquid_fraction(fraction)
        phases = getattr(result, "phases_present", ()) or ()
        self._sample_diagnostics[key] = {
            "phases": [str(phase) for phase in phases],
            "element_closure_verified": closure_verified,
            "phase_compositions_available": bool(
                getattr(result, "phase_compositions", None)
                or getattr(result, "liquid_composition_wt_pct", None)
            ),
        }
        self._point_cache[key] = liquid_fraction
        return liquid_fraction

    def sample_diagnostics(
        self,
        composition_wt_pct: Mapping[str, Any],
        temperature_C: float,
        pressure_bar: float,
    ) -> dict[str, Any]:
        """Return metadata for the matching cached single-point solve."""

        composition = validate_oxide_composition(composition_wt_pct)
        key = magemin_request_cache_key(
            engine=MAGEMIN_ENGINE_ID,
            version=MAGEMIN_REQUEST_CACHE_VERSION,
            composition_wt_pct=composition,
            temperature_C=_finite_real(temperature_C, "temperature_C"),
            pressure_bar=_positive(pressure_bar, "pressure_bar"),
            fO2_log=MAGEMIN_FO2_LOG,
        )
        return dict(self._sample_diagnostics.get(key, {}))

    def curve(
        self,
        liner_composition_wt_pct: Mapping[str, Any],
        deposit_kg_by_species: Mapping[str, Any],
        *,
        temperature_C: float,
        pressure_bar: float,
    ) -> TabulatedLiquidFractionCurve:
        liner = validate_oxide_composition(liner_composition_wt_pct)
        deposit = deposit_oxide_composition(deposit_kg_by_species)
        if not deposit:
            raise LinerCorrosionRefusal("campaign_deposit_missing")
        temperature = _finite_real(temperature_C, "temperature_C")
        pressure = _positive(pressure_bar, "pressure_bar")
        key = (
            MAGEMIN_ENGINE_ID,
            MAGEMIN_REQUEST_CACHE_VERSION,
            _rounded_composition(liner),
            _rounded_composition(deposit),
            round(temperature, MAGEMIN_CACHE_DECIMAL_PLACES),
            _rounded_pressure_bar(pressure),
            round(MAGEMIN_FO2_LOG, MAGEMIN_CACHE_DECIMAL_PLACES),
        )
        if key in self._curve_cache:
            return self._curve_cache[key]

        points: list[tuple[float, float]] = []
        last_valid_fraction = 0.0
        for index in range(MAGEMIN_PHI_CURVE_INTERVALS + 1):
            deposit_fraction = index / MAGEMIN_PHI_CURVE_INTERVALS
            composition = _composition_at_fraction(liner, deposit, deposit_fraction)
            try:
                liquid_fraction = self(composition, temperature, pressure)
            except LinerCorrosionRefusal as exc:
                # The table ends at the first domain boundary. No extrapolation
                # or result from outside the MAGEMin gate is used.
                if exc.reason == ENGINE_OUT_OF_DOMAIN_FLAG and points:
                    from engines.magemin.domain import MAGEMinDomainGate

                    invalid_fraction = deposit_fraction
                    admissible_fraction = last_valid_fraction
                    for _ in range(52):
                        midpoint = (admissible_fraction + invalid_fraction) / 2.0
                        candidate = _composition_at_fraction(liner, deposit, midpoint)
                        valid, _, _ = MAGEMinDomainGate.validate_with_reason(candidate)
                        if valid:
                            admissible_fraction = midpoint
                        else:
                            invalid_fraction = midpoint
                    if admissible_fraction > last_valid_fraction:
                        boundary_composition = _composition_at_fraction(
                            liner, deposit, admissible_fraction
                        )
                        boundary_fraction = self(
                            boundary_composition, temperature, pressure
                        )
                        points.append((admissible_fraction, boundary_fraction))
                    break
                raise
            points.append((deposit_fraction, liquid_fraction))
            last_valid_fraction = deposit_fraction
        curve = TabulatedLiquidFractionCurve(
            liner_composition_wt_pct=liner,
            deposit_composition_wt_pct=deposit,
            temperature_C=temperature,
            pressure_bar=pressure,
            points=tuple(points),
        )
        self._curve_cache[key] = curve
        return curve

    def close(self) -> None:
        close = getattr(self._backend, "close", None)
        if callable(close):
            close()
        self._initialized = False


class LinerCorrosionDataError(ValueError):
    """A source extract cannot safely supply a Tier A invariant record."""

    def __init__(self, source_id: str, reason: str, detail: str) -> None:
        self.source_id = source_id
        self.reason = reason
        super().__init__(f"{source_id}: {reason}: {detail}")


def _mapping(
    value: Any, source_id: str, observation_id: str, field: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LinerCorrosionDataError(
            source_id, "invalid_record", f"{observation_id}.{field} must be a mapping"
        )
    return value


def _read_extract(filename: str, expected_source_id: str) -> Mapping[str, Any]:
    path = EXTRACTS_DIR / filename
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LinerCorrosionDataError(
            expected_source_id, "source_unavailable", f"cannot read {filename}: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise LinerCorrosionDataError(
            expected_source_id, "invalid_yaml", f"cannot parse {filename}: {exc}"
        ) from exc
    if not isinstance(document, Mapping):
        raise LinerCorrosionDataError(
            expected_source_id, "invalid_extract", f"{filename} root must be a mapping"
        )
    if document.get("source_id") != expected_source_id:
        raise LinerCorrosionDataError(
            expected_source_id,
            "source_identity_mismatch",
            f"{filename} declares {document.get('source_id')!r}",
        )
    return document


def _finite_number(
    value: Any, source_id: str, observation_id: str, field: str
) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LinerCorrosionDataError(
            source_id, "invalid_numeric_value", f"{observation_id}.{field} is not numeric"
        )
    if not math.isfinite(value):
        raise LinerCorrosionDataError(
            source_id, "non_finite_value", f"{observation_id}.{field} is not finite"
        )
    return value


def _locator(
    observation: Mapping[str, Any], source_id: str, observation_id: str
) -> dict[str, Any]:
    locator = _mapping(
        observation.get("locator"), source_id, observation_id, "locator"
    )
    page = locator.get("page")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise LinerCorrosionDataError(
            source_id, "invalid_locator", f"{observation_id} has no physical page"
        )
    if not any(key in locator for key in ("table", "section", "figure")):
        raise LinerCorrosionDataError(
            source_id,
            "invalid_locator",
            f"{observation_id} needs a table, section, or figure locator",
        )
    return dict(locator)


def _phases(
    value: Any, source_id: str, observation_id: str
) -> list[str] | None:
    if value == "not stated":
        return None
    if not isinstance(value, list) or not value:
        raise LinerCorrosionDataError(
            source_id,
            "invalid_phase_list",
            f"{observation_id}.phases_as_printed must be a non-empty list or 'not stated'",
        )
    if any(not isinstance(phase, str) or not phase.strip() for phase in value):
        raise LinerCorrosionDataError(
            source_id, "invalid_phase_list", f"{observation_id} has an empty or non-text phase"
        )
    return list(value)


def _composition(
    values: Mapping[str, Any], source_id: str, observation_id: str
) -> dict[str, int | float] | None:
    if "liquid_composition_mass_percent" in values:
        raw = values["liquid_composition_mass_percent"]
    elif "composition_mass_percent_as_printed" in values:
        raw = values.get("composition_mass_percent_as_printed")
    elif "composition_mass_percent" in values:
        raw = values["composition_mass_percent"]
    elif values.get("liquid_composition_as_printed") == "not stated":
        return None
    else:
        raise LinerCorrosionDataError(
            source_id,
            "missing_composition_status",
            f"{observation_id} does not state whether a liquid composition is available",
        )
    if raw == "not stated":
        return None
    composition = _mapping(raw, source_id, observation_id, "liquid composition")
    if not composition:
        raise LinerCorrosionDataError(
            source_id, "invalid_composition", f"{observation_id} has an empty composition"
        )
    result: dict[str, int | float] = {}
    for oxide, value in composition.items():
        if not isinstance(oxide, str) or not oxide.strip():
            raise LinerCorrosionDataError(
                source_id,
                "invalid_composition_component",
                f"{observation_id} has a non-text oxide key",
            )
        result[oxide] = _finite_number(
            value, source_id, observation_id, f"composition.{oxide}"
        )
    return result


def _temperature(
    observation: Mapping[str, Any],
    values: Mapping[str, Any],
    source_id: str,
    observation_id: str,
) -> tuple[int | float, int | float | None]:
    has_celsius = "temperature_C" in values
    has_kelvin = "temperature_K" in values
    units = observation.get("units")
    if has_celsius == has_kelvin:
        raise LinerCorrosionDataError(
            source_id,
            "ambiguous_temperature_unit",
            f"{observation_id} must have exactly one printed Celsius or Kelvin value",
        )
    if has_kelvin:
        if units != "K":
            raise LinerCorrosionDataError(
                source_id, "unknown_temperature_unit", f"{observation_id} declares {units!r}"
            )
        temperature = _finite_number(
            values["temperature_K"], source_id, observation_id, "temperature_K"
        ) - CELSIUS_TO_KELVIN_OFFSET
        uncertainty_key = "temperature_uncertainty_K"
        uncertainty = (
            _finite_number(values[uncertainty_key], source_id, observation_id, uncertainty_key)
            if uncertainty_key in values
            else None
        )
        return temperature, uncertainty
    if not isinstance(units, str) or "°C" not in units:
        raise LinerCorrosionDataError(
            source_id, "unknown_temperature_unit", f"{observation_id} declares {units!r}"
        )
    temperature = _finite_number(
        values["temperature_C"], source_id, observation_id, "temperature_C"
    )
    uncertainty_key = "temperature_uncertainty_C"
    uncertainty = (
        _finite_number(values[uncertainty_key], source_id, observation_id, uncertainty_key)
        if uncertainty_key in values
        else None
    )
    return temperature, uncertainty


def _observation_record(
    observation: Mapping[str, Any], source_id: str
) -> dict[str, Any]:
    observation_id = observation.get("observation_id")
    if not isinstance(observation_id, str) or not observation_id:
        raise LinerCorrosionDataError(
            source_id, "missing_observation_id", "transition point has no id"
        )
    values = _mapping(observation.get("values"), source_id, observation_id, "values")
    subsystem = values.get("subsystem_as_printed")
    if not isinstance(subsystem, str) or not subsystem:
        raise LinerCorrosionDataError(
            source_id, "missing_subsystem", f"{observation_id} has no printed subsystem"
        )
    phases = _phases(values.get("phases_as_printed"), source_id, observation_id)
    temperature, uncertainty = _temperature(
        observation, values, source_id, observation_id
    )
    kind = values.get("invariant_type") or values.get("invariant_type_as_printed")
    if not isinstance(kind, str) or not kind:
        raise LinerCorrosionDataError(
            source_id, "missing_invariant_type", f"{observation_id} has no invariant type"
        )
    composition = _composition(values, source_id, observation_id)
    units = observation.get("units")
    if composition is not None and (
        not isinstance(units, str) or "wt%" not in units
    ):
        raise LinerCorrosionDataError(
            source_id,
            "unknown_composition_unit",
            f"{observation_id} has a composition with units {units!r}",
        )
    record: dict[str, Any] = {
        "source_id": source_id,
        "observation_id": observation_id,
        "subsystem": subsystem,
        "T_invariant_C": temperature,
        "phases": phases,
        "liquid_composition": composition,
        "liquid_composition_unit": "wt%" if composition is not None else None,
        "invariant_type": kind,
        "locator": _locator(observation, source_id, observation_id),
    }
    if uncertainty is not None:
        record["temperature_uncertainty_C"] = uncertainty
    for source_field in (
        "compound_as_printed",
        "phase_reaction_as_printed",
        "source_status",
        "temperature_K_as_printed",
        "table_reference_as_printed",
    ):
        if source_field in values:
            record[source_field] = values[source_field]
    return record


def _source_observation_records(
    document: Mapping[str, Any], source_id: str
) -> list[dict[str, Any]]:
    species = _mapping(document.get("species"), source_id, "extract", "species")
    cao = _mapping(species.get("CaO"), source_id, "extract", "species.CaO")
    observations = cao.get("observations")
    if not isinstance(observations, list):
        raise LinerCorrosionDataError(
            source_id, "invalid_observations", "species.CaO.observations must be a list"
        )
    records = []
    for observation in observations:
        if (
            not isinstance(observation, Mapping)
            or observation.get("type") != "transition_point"
        ):
            raise LinerCorrosionDataError(
                source_id,
                "invalid_observation_type",
                "invariant row is not a transition_point",
            )
        records.append(_observation_record(observation, source_id))
    return records


def _reaction_parts(
    reaction: Any, source_id: str, observation_id: str
) -> tuple[list[list[str]], list[str]]:
    if not isinstance(reaction, str) or not reaction.strip():
        raise LinerCorrosionDataError(
            source_id,
            "missing_phase_reaction",
            f"{observation_id} has no printed phase reaction",
        )
    sides = re.split(r"\s*(?:⇌|↔|→|->|=)\s*", reaction)
    if len(sides) not in {1, 2, 3}:
        raise LinerCorrosionDataError(
            source_id,
            "invalid_phase_reaction",
            f"{observation_id} reaction must have one to three printed sides",
        )
    parsed_sides: list[list[str]] = []
    phases: list[str] = []
    for side in sides:
        side = re.sub(r"\bL\d*\+\s*$", "L", side.strip())
        side_phases = []
        for printed_phase in side.split("+"):
            phase = printed_phase.strip()
            if not phase:
                raise LinerCorrosionDataError(
                    source_id,
                    "invalid_phase_reaction",
                    f"{observation_id} has an empty phase token",
                )
            if _is_liquid_phase(phase):
                phase = "liquid"
            side_phases.append(phase)
            if phase not in phases:
                phases.append(phase)
        parsed_sides.append(side_phases)
    return parsed_sides, phases


def _reaction_type(
    reaction: Any, source_id: str, observation_id: str
) -> tuple[str, list[str]]:
    sides, phases = _reaction_parts(reaction, source_id, observation_id)
    if "liquid" not in phases:
        return "subsolidus transition", phases
    if len(sides) == 1:
        return "liquid-bearing invariant", phases
    solid_counts = [sum(phase != "liquid" for phase in side) for side in sides]
    if sorted(solid_counts) == [0, 1]:
        kind = "congruent melting"
    elif 0 in solid_counts:
        kind = "eutectic"
    else:
        kind = "peritectic"
    return kind, phases


def _morey_context_records(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    source_id = "morey-1964-usgs-pp440l-phase-equilibria"
    species = _mapping(document.get("species"), source_id, "extract", "species")
    cao = _mapping(species.get("CaO"), source_id, "extract", "species.CaO")
    contexts = cao.get("context")
    if not isinstance(contexts, list):
        raise LinerCorrosionDataError(
            source_id, "invalid_context", "species.CaO.context must be a list"
        )
    table_subsystems = {
        6: "CaO-Al2O3",
        12: "Na2O-CaO-Al2O3",
        21: "MgO-CaO-Al2O3",
        26: "CaO-Al2O3-SiO2",
    }
    records = []
    for context in contexts:
        if not isinstance(context, Mapping):
            raise LinerCorrosionDataError(
                source_id, "invalid_record", "Morey context row must be a mapping"
            )
        values = _mapping(
            context.get("values"), source_id,
            str(context.get("observation_id", "context")), "values"
        )
        table_number = values.get("table_number")
        is_invariant_table_row = (
            isinstance(table_number, int)
            and not isinstance(table_number, bool)
            and table_number in table_subsystems
        )
        observation_id = context.get("observation_id")
        is_compound_transition = (
            isinstance(observation_id, str)
            and observation_id in {
                "morey1964_t6_c12a7_1455",
                "morey1964_t6_c12a7_1391",
                "morey1964_cas_gehlenite_1590",
                "morey1964_cas_c3as_1335",
            }
        )
        if not (is_invariant_table_row or is_compound_transition):
            continue
        if not isinstance(observation_id, str) or not observation_id:
            raise LinerCorrosionDataError(
                source_id, "missing_observation_id", "Morey transition has no id"
            )
        subsystem = values.get("subsystem_as_printed")
        if subsystem is None and is_invariant_table_row:
            subsystem = table_subsystems[table_number]
        if not isinstance(subsystem, str) or not subsystem:
            raise LinerCorrosionDataError(
                source_id, "missing_subsystem", f"{observation_id} has no subsystem"
            )
        temperature = _finite_number(
            values.get("temperature_C_as_printed"),
            source_id,
            observation_id,
            "temperature_C_as_printed",
        )
        units = context.get("units")
        if not isinstance(units, str) or "°C" not in units:
            raise LinerCorrosionDataError(
                source_id, "unknown_temperature_unit", f"{observation_id} declares {units!r}"
            )
        composition = _composition(values, source_id, observation_id)
        if composition is not None and "wt%" not in units.replace(" ", "") and "weight" not in str(
            values.get("composition_header_as_printed", "")
        ).lower():
            raise LinerCorrosionDataError(
                source_id, "unknown_composition_unit", f"{observation_id} has no weight-percent basis"
            )
        reaction = values.get("phase_reaction_as_printed")
        try:
            if reaction:
                inferred_kind, reaction_phases = _reaction_type(
                    reaction, source_id, observation_id
                )
                phases = (
                    _phases(values["phases_as_printed"], source_id, observation_id)
                    if "phases_as_printed" in values
                    else reaction_phases
                )
            else:
                inferred_kind = values.get("invariant_type")
                phases = _phases(
                    values.get("phases_as_printed"), source_id, observation_id
                )
        except LinerCorrosionDataError as exc:
            warnings.warn(
                "liner_corrosion_tier_a_unparsed "
                f"source_id={source_id} observation_id={observation_id} "
                f"reason={exc.reason}",
                RuntimeWarning,
                stacklevel=2,
            )
            continue
        kind = values.get("invariant_type", inferred_kind)
        if inferred_kind == "subsolidus transition" or (
            phases is not None
            and not any(_is_liquid_phase(phase) for phase in phases)
        ):
            kind = "subsolidus transition"
        if not isinstance(kind, str) or not kind:
            raise LinerCorrosionDataError(
                source_id, "missing_invariant_type", f"{observation_id} has no invariant type"
            )
        locator = _locator(context, source_id, observation_id)
        record: dict[str, Any] = {
            "source_id": source_id,
            "observation_id": observation_id,
            "subsystem": subsystem,
            "T_invariant_C": temperature,
            "phases": phases,
            "liquid_composition": composition,
            "liquid_composition_unit": "wt%" if composition is not None else None,
            "invariant_type": kind,
            "locator": locator,
        }
        if reaction is not None:
            record["phase_reaction_as_printed"] = reaction
        if "compound_as_printed" in values:
            record["compound_as_printed"] = values["compound_as_printed"]
        if "invariant_point_as_printed" in values:
            record["invariant_point_as_printed"] = values["invariant_point_as_printed"]
        if "temperature_qualification_as_printed" in values:
            record["temperature_qualification_as_printed"] = values[
                "temperature_qualification_as_printed"
            ]
        if "temperature_uncertainty_C_as_printed" in values:
            record["temperature_uncertainty_C"] = _finite_number(
                values["temperature_uncertainty_C_as_printed"],
                source_id,
                observation_id,
                "temperature_uncertainty_C_as_printed",
            )
        qualifier = values.get("qualifier")
        if isinstance(qualifier, str) and qualifier:
            record["source_qualifier"] = qualifier
            if "in error" in qualifier.lower():
                record["source_status"] = "source marks this point as in error"
        records.append(record)
    return records


def _shornikov_fusion_record(
    observation: Mapping[str, Any], species_name: str
) -> dict[str, Any]:
    source_id = "shornikov-1997-cao-al2o3"
    observation_id = observation.get("observation_id")
    if not isinstance(observation_id, str) or not observation_id:
        raise LinerCorrosionDataError(
            source_id, "missing_observation_id", "fusion temperature has no id"
        )
    values = _mapping(observation.get("values"), source_id, observation_id, "values")
    printed_temperature = values.get("T_fus_as_printed")
    match = (
        re.fullmatch(
            r"\s*([+-]?\d+(?:\.\d+)?)\s*(?:±\s*([+-]?\d+(?:\.\d+)?))?\s*",
            printed_temperature,
        )
        if isinstance(printed_temperature, str)
        else None
    )
    if match is None:
        raise LinerCorrosionDataError(
            source_id,
            "invalid_printed_temperature",
            f"{observation_id}.T_fus_as_printed is not a single point or point ± uncertainty",
        )
    temperature_k = float(match.group(1))
    stored_temperature = values.get("T_K", values.get("temperature_K"))
    if isinstance(stored_temperature, bool) or str(stored_temperature) != match.group(1):
        raise LinerCorrosionDataError(
            source_id,
            "temperature_value_mismatch",
            f"{observation_id} T_K does not match its printed Tfus value",
        )
    units = observation.get("units")
    locator = _locator(observation, source_id, observation_id)
    is_existing_fusion_enthalpy = (
        values.get("quantity") == "heat_of_fusion" and units == "kJ/mol"
    )
    if locator.get("table") != "3" or not (units == "K" or is_existing_fusion_enthalpy):
        raise LinerCorrosionDataError(
            source_id, "unknown_temperature_unit", f"{observation_id} declares {units!r}"
        )
    compound = values.get("printed_compound", values.get("compound_as_printed"))
    if not isinstance(compound, str) or not compound:
        raise LinerCorrosionDataError(
            source_id, "missing_compound", f"{observation_id} has no printed compound"
        )
    phase_statement = observation.get("phase")
    phases = _phases(values.get("phases_as_printed", "not stated"), source_id, observation_id)
    composition = _composition(
        {**values, "liquid_composition_as_printed": values.get("liquid_composition_as_printed", "not stated")},
        source_id,
        observation_id,
    )
    kind = values.get(
        "invariant_type_as_printed", "fusion temperature; melting mode not stated"
    )
    if not isinstance(kind, str) or not kind:
        raise LinerCorrosionDataError(
            source_id, "missing_invariant_type", f"{observation_id} has no fusion type"
        )
    record: dict[str, Any] = {
        "source_id": source_id,
        "observation_id": observation_id,
        "subsystem": "CaO-Al2O3",
        "T_invariant_C": temperature_k - CELSIUS_TO_KELVIN_OFFSET,
        "phases": phases,
        "liquid_composition": composition,
        "liquid_composition_unit": "wt%" if composition is not None else None,
        "invariant_type": kind,
        "locator": locator,
        "compound_as_printed": compound,
        "temperature_K_as_printed": printed_temperature,
    }
    uncertainty = match.group(2)
    if uncertainty is not None:
        record["temperature_uncertainty_C"] = float(uncertainty)
    if isinstance(phase_statement, str) and phase_statement:
        record["phase_as_printed"] = phase_statement
    if species_name:
        record["formula"] = species_name
    if "source_reference_as_printed" in values:
        record["source_reference_as_printed"] = values["source_reference_as_printed"]
    return record


def _shornikov_records(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    source_id = "shornikov-1997-cao-al2o3"
    species = _mapping(document.get("species"), source_id, "extract", "species")
    records = []
    for species_name, section_value in species.items():
        if not isinstance(species_name, str):
            raise LinerCorrosionDataError(
                source_id, "invalid_species", "species key must be text"
            )
        section = _mapping(section_value, source_id, "extract", f"species.{species_name}")
        observations = section.get("observations")
        if not isinstance(observations, list):
            raise LinerCorrosionDataError(
                source_id,
                "invalid_observations",
                f"species.{species_name}.observations must be a list",
            )
        for observation in observations:
            if not isinstance(observation, Mapping):
                raise LinerCorrosionDataError(
                    source_id, "invalid_record", f"species.{species_name} row must be a mapping"
                )
            values = observation.get("values")
            if not isinstance(values, Mapping) or "T_fus_as_printed" not in values:
                continue
            records.append(_shornikov_fusion_record(observation, species_name))
    return records


def _rankin_table_a_records(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    source_id = "rankin-wright-1915-ajs-cao-al2o3-sio2"
    species = _mapping(document.get("species"), source_id, "extract", "species")
    cao = _mapping(species.get("CaO"), source_id, "extract", "species.CaO")
    contexts = cao.get("context")
    if not isinstance(contexts, list):
        raise LinerCorrosionDataError(
            source_id, "invalid_context", "species.CaO.context must be a list"
        )
    records = []
    for context in contexts:
        if not isinstance(context, Mapping):
            raise LinerCorrosionDataError(
                source_id, "invalid_record", "Table A context row must be a mapping"
            )
        values = context.get("values")
        if not isinstance(values, Mapping) or values.get("summary_table") != "A":
            continue
        if values.get("quantity") != "transition_temperature":
            continue
        transformation = values.get("transformation_as_printed")
        formula = values.get("formula_as_printed")
        if not isinstance(transformation, str) or not isinstance(formula, str):
            raise LinerCorrosionDataError(
                source_id, "invalid_compound_transition", "Table A transition lacks printed text"
            )
        if not (
            transformation.startswith("Melting")
            or transformation.startswith("Dissociation into ")
        ):
            continue
        observation_id = context.get("observation_id")
        if not isinstance(observation_id, str) or not observation_id:
            raise LinerCorrosionDataError(
                source_id, "missing_observation_id", "Table A transition has no id"
            )
        components = values.get("compound_composition_mass_percent")
        if not isinstance(components, Mapping):
            raise LinerCorrosionDataError(
                source_id,
                "invalid_compound_composition",
                f"{observation_id} has no compound composition",
            )
        if any(not isinstance(component, str) or not component for component in components):
            raise LinerCorrosionDataError(
                source_id,
                "invalid_compound_composition",
                f"{observation_id} has a non-text component name",
            )
        if len(components) < 2:
            # Table A rows 1-3 are pure-component melting references, not the
            # multicomponent compound transitions used by the invariant table.
            continue
        subsystem = "-".join(components)
        if transformation.startswith("Melting"):
            phases = [formula, "liquid"]
            invariant_type = "congruent melting"
        else:
            products_text = transformation.removeprefix("Dissociation into ")
            products = [part.strip() for part in products_text.split(" and ")]
            if not products or any(not part for part in products):
                raise LinerCorrosionDataError(
                    source_id,
                    "invalid_compound_transition",
                    f"{observation_id} has unparsed dissociation products",
                )
            phases = [formula, *products]
            invariant_type = "dissociation"
        temperature = _finite_number(
            values.get("temperature_C"), source_id, observation_id, "temperature_C"
        )
        locator = _locator(context, source_id, observation_id)
        records.append(
            {
                "source_id": source_id,
                "observation_id": observation_id,
                "subsystem": subsystem,
                "T_invariant_C": temperature,
                "phases": phases,
                "liquid_composition": None,
                "liquid_composition_unit": None,
                "invariant_type": invariant_type,
                "transition_as_printed": transformation,
                "locator": locator,
            }
        )
    return records


def load_liner_corrosion_invariants() -> list[dict[str, Any]]:
    """Return source-located invariant and compound-transition rows in Celsius."""
    records: list[dict[str, Any]] = []

    rankin_id = "rankin-wright-1915-ajs-cao-al2o3-sio2"
    rankin = _read_extract(f"{rankin_id}.yaml", rankin_id)
    records.extend(_source_observation_records(rankin, rankin_id))
    records.extend(_rankin_table_a_records(rankin))

    morey_id = "morey-1964-usgs-pp440l-phase-equilibria"
    morey = _read_extract(f"{morey_id}.yaml", morey_id)
    records.extend(_morey_context_records(morey))

    shults_id = "shults-1995-calcium-aluminate-thermodynamics"
    shults = _read_extract(f"{shults_id}.yaml", shults_id)
    records.extend(_source_observation_records(shults, shults_id))

    shornikov_id = "shornikov-1997-cao-al2o3"
    shornikov = _read_extract(f"{shornikov_id}.yaml", shornikov_id)
    records.extend(_shornikov_records(shornikov))

    for record in records:
        exclusion = _first_liquid_exclusion_reason(record)
        if exclusion is not None:
            record["first_liquid_exclusion_reason"] = exclusion
    return records
