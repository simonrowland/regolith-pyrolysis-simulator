"""Pure liner-interface contamination and first-liquid diagnostics.

The model reports equilibrium melt onset and bounded campaign counts. It does
not model dissolution or corrosion kinetics.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

import yaml

from simulator.accounting.formulas import parse_formula
from simulator.melt_backend.liquidus import LIQUIDUS_SOLID_EPSILON
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.scalar_boundary import is_declared_real_scalar


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

MAGEMIN_ENGINE_ID = "magemin:ig"
MAGEMIN_REQUEST_CACHE_VERSION = 1  # Hand-bump when adapter-visible settings change.
MAGEMIN_FO2_LOG = -9.0  # Fixed request setting; included in the request cache key.
MAGEMIN_CACHE_DECIMAL_PLACES = 6
MAGEMIN_PHI_CURVE_INTERVALS = 30
ELEMENT_CLOSURE_REL_TOLERANCE = 1.0e-3


class LinerCorrosionRefusal(ValueError):
    """Typed refusal from the liner-chemistry diagnostic."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = str(reason)
        self.detail = str(detail or reason)
        super().__init__(self.detail)

    def as_dict(self) -> dict[str, str]:
        return {"reason": self.reason, "detail": self.detail}


LiquidFractionSampler = Callable[[Mapping[str, float], float, float | None], float]


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
            element: count
            for element, count in species_formula.elements.items()
            if element != "O"
        }
        if len(cation_atoms) != 1:
            raise LinerCorrosionRefusal(
                "deposit_oxidation_mapping_invalid", species
            )
        cation = next(iter(cation_atoms))
        oxide_cation_count = float(oxide_formula.elements.get(cation, 0.0))
        species_cation_count = float(cation_atoms[cation])
        if (
            oxide_cation_count <= 0.0
            or species_formula.molar_mass_g_per_mol is None
            or oxide_formula.molar_mass_g_per_mol is None
        ):
            raise LinerCorrosionRefusal(
                "deposit_oxidation_mapping_invalid", species
            )
        oxide_moles_per_species_mole = species_cation_count / oxide_cation_count
        mass_ratio = (
            float(oxide_formula.molar_mass_g_per_mol)
            * oxide_moles_per_species_mole
            / float(species_formula.molar_mass_g_per_mol)
        )
        result[species] = kg * mass_ratio
    return result


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
    rho = _positive(density_kg_m3, "density_kg_m3")
    area = _positive(area_m2, "area_m2")
    depth = _positive(reacted_layer_depth_m, "reacted_layer_depth_m")
    liner_mass_kg = rho * area * depth
    denominator_kg = liner_mass_kg + math.fsum(checked.values())
    if not math.isfinite(denominator_kg) or denominator_kg <= 0.0:
        raise LinerCorrosionRefusal("liner_mass_invalid")
    return {
        species: kg / denominator_kg
        for species, kg in checked.items()
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
        oxide = DEPOSIT_OXIDE_BY_SPECIES[species]
        interface[oxide] = interface.get(oxide, 0.0) + 100.0 * kg / denominator_kg
    total_wt_pct = math.fsum(interface.values())
    if not math.isclose(total_wt_pct, 100.0, rel_tol=0.0, abs_tol=1e-9):
        raise LinerCorrosionRefusal(
            "interface_mass_closure_failed", f"mixed wt% totals {total_wt_pct:.12g}"
        )
    return {
        "composition_wt_pct": interface,
        "mixing_fraction_by_species": contamination_mixing_fractions(
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
) -> dict[str, float | str]:
    """Estimate d(liquid fraction)/d(deposit wt%) with a 1-point difference."""

    species = str(deposit_species).strip()
    oxide = DEPOSIT_OXIDE_BY_SPECIES.get(species)
    if oxide is None:
        raise LinerCorrosionRefusal("deposit_species_unmapped", species)
    base = validate_oxide_composition(liner_composition_wt_pct)
    service_C = _finite_real(temperature_C, "temperature_C")
    pressure = _positive(pressure_bar, "pressure_bar")
    zero_state = _checked_liquid_fraction(sampler(base, service_C, pressure))

    # A one wt%-point perturbation is a finite-difference unit, not a fitted
    # threshold: it compares the same sampled equilibrium before and after the
    # exact composition change and reports dphi per wt%-point.
    perturbed = {key: value * 0.99 for key, value in base.items()}
    perturbed[oxide] = perturbed.get(oxide, 0.0) + 1.0
    one_point_state = _checked_liquid_fraction(
        sampler(perturbed, service_C, pressure)
    )
    return {
        "species": species,
        "oxide": oxide,
        "delta_liquid_fraction_per_wt_pct": one_point_state - zero_state,
    }


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
    if not species:
        raise LinerCorrosionRefusal("campaign_deposit_missing")
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
    if isinstance(sampler, MageminLinerSampler) and any(
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
) -> tuple[int | str, dict[str, float], float]:
    deposits = _checked_deposit(initial_deposit_kg_by_species)
    service_sample_C = _finite_real(temperature_C, "temperature_C") - _finite_real(
        margin_C, "margin_C"
    )
    pressure = (
        None if pressure_bar is None else _positive(pressure_bar, "pressure_bar")
    )
    for completed in range(horizon + 1):
        composition = contaminated_interface_composition(
            liner_composition_wt_pct,
            deposits,
            density_kg_m3=density_kg_m3,
            area_m2=area_m2,
            reacted_layer_depth_m=reacted_layer_depth_m,
        )["composition_wt_pct"]
        criterion_signal = _checked_liquid_fraction(
            _sample_liquid_fraction(
                sampler,
                composition,
                deposits,
                liner_composition_wt_pct,
                service_sample_C,
                pressure,
            )
        )
        if criterion_signal > LIQUIDUS_SOLID_EPSILON:
            return completed, deposits, criterion_signal
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
    return f"> {horizon}", deposits, criterion_signal


def corrosion_campaign_recurrence(
    liner_composition_wt_pct: Mapping[str, Any],
    initial_deposit_kg_by_species: Mapping[str, Any],
    deposit_inert_kg_by_species: Mapping[str, Any],
    deposit_reactive_kg_by_species: Mapping[str, Any],
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
    """Explicitly project the two sticking bounds and coupled recurrence."""

    liner = validate_oxide_composition(liner_composition_wt_pct)
    inert = _checked_deposit(deposit_inert_kg_by_species)
    reactive = _checked_deposit(deposit_reactive_kg_by_species)
    branches = sticking_deposit_interval(inert, reactive)
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 0:
        raise LinerCorrosionRefusal("campaign_horizon_invalid")
    margin = _finite_real(margin_C, "margin_C")
    if margin < 0.0:
        raise LinerCorrosionRefusal("temperature_margin_invalid")

    # The high-rate branch reaches the criterion first (lo); the inert branch
    # is the slower bound (hi). Both are explicit M_(N+1)=M_N+D recurrences.
    lo, lo_deposit, lo_signal = _campaign_branch(
        liner,
        initial_deposit_kg_by_species,
        branches["reactive_upper_bound"]["deposit_kg_by_species"],
        density_kg_m3=density_kg_m3,
        area_m2=area_m2,
        reacted_layer_depth_m=reacted_layer_depth_m,
        temperature_C=temperature_C,
        pressure_bar=pressure_bar,
        margin_C=margin,
        horizon=horizon,
        sampler=sampler,
    )
    hi, hi_deposit, hi_signal = _campaign_branch(
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
    )
    coupled, coupled_deposit, coupled_signal = _campaign_branch(
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
        feedback_deposit_kg_by_species=(
            branches["reactive_upper_bound"]["deposit_kg_by_species"]
        ),
    )
    flags = [
        DEPOSIT_OXIDATION_STATE_FLAG,
        REACTED_LAYER_DEPTH_FLAG,
        TEMPERATURE_MARGIN_FLAG,
        HORIZON_FLAG,
        DISSOLUTION_KINETICS_FLAG,
        CORROSION_NO_RATE_FLAG,
    ]
    return {
        "campaigns_to_runaway": [lo, hi],
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
        "sticking_state": branches,
        "reacted_layer_depth_m": {
            "declared_h": _positive(reacted_layer_depth_m, "reacted_layer_depth_m"),
            "sensitivity_h_over_10_h_10h": list(reacted_layer_depths(reacted_layer_depth_m)),
        },
        "margin_C": margin,
        "horizon_campaigns": horizon,
        "flags": flags,
    }


def first_liquid_tier_a(
    invariant_table: Mapping[str, Any],
    composition_wt_pct: Mapping[str, Any],
) -> float:
    """Find the lowest digitized first-liquid invariant covering a bulk.

    Each invariant row names a ``first_liquid_temperature_C`` and oxide
    ``composition_bounds_wt_pct`` as ``[low, high]`` pairs. Missing or
    uncovered data refuses; it never becomes a zero-liquid answer.
    """

    composition = validate_oxide_composition(composition_wt_pct)
    rows = invariant_table.get("invariants") if isinstance(invariant_table, Mapping) else None
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        raise LinerCorrosionRefusal("liner_invariant_table_missing")
    matches: list[float] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        bounds = row.get("composition_bounds_wt_pct")
        if not isinstance(bounds, Mapping) or not bounds:
            continue
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
    if not matches:
        raise LinerCorrosionRefusal("tier_a_invariant_field_missing")
    return min(matches)


def tier_a_first_liquid_onset(
    invariant_table: Mapping[str, Any],
    composition_wt_pct: Mapping[str, Any],
    *,
    service_temperature_C: float,
    margin_C: float,
) -> dict[str, Any]:
    first_liquid_C = first_liquid_tier_a(invariant_table, composition_wt_pct)
    service_limit_C = _finite_real(service_temperature_C, "service_temperature_C") - _finite_real(
        margin_C, "margin_C"
    )
    return {
        "first_melt_T_C": first_liquid_C,
        "service_limit_T_C": service_limit_C,
        "onset": first_liquid_C <= service_limit_C,
        "flags": [TEMPERATURE_MARGIN_FLAG],
    }


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
    x_star: float | None = None,
) -> dict[str, Any]:
    """Build one terminal segment record, preserving unavailable fields as refusals."""

    raw_composition = interface_chemistry.get("composition")
    if not isinstance(raw_composition, Mapping) or not raw_composition:
        raise LinerCorrosionRefusal("liner_composition_missing")
    liner = validate_oxide_composition(raw_composition)
    deposits = _checked_deposit(deposit_kg_by_species)
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

    mixed = contaminated_interface_composition(
        liner,
        deposits,
        density_kg_m3=density,
        area_m2=area,
        reacted_layer_depth_m=depth,
    )
    sample_temperature_C = service_C - margin_C
    tier = str(interface_chemistry.get("criterion_tier") or "B").upper()
    refusals: list[dict[str, str]] = []
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
    if raw_porosity is None:
        refusals.append(
            LinerCorrosionRefusal("liner_porosity_missing").as_dict()
        )
    else:
        porosity = _finite_real(raw_porosity, "porosity_fraction")
        if not 0.0 <= porosity <= 1.0:
            refusals.append(
                LinerCorrosionRefusal("liner_porosity_invalid").as_dict()
            )

    first_melt_C: float | None = None
    liquid_fraction: float | None = None
    if tier == "A":
        table = interface_chemistry.get("invariant_table")
        if not isinstance(table, Mapping) or not table:
            raise LinerCorrosionRefusal("liner_invariant_table_missing")
        tier_a = tier_a_first_liquid_onset(
            table,
            mixed["composition_wt_pct"],
            service_temperature_C=service_C,
            margin_C=margin_C,
        )
        first_melt_C = float(tier_a["first_melt_T_C"])
        onset = bool(tier_a["onset"])
    elif tier == "B":
        if sampler is None:
            raise LinerCorrosionRefusal("liquid_fraction_sampler_missing")
        if pressure is None:
            raise LinerCorrosionRefusal("liner_service_pressure_missing")
        liquid_fraction = _checked_liquid_fraction(
            _sample_liquid_fraction(
                sampler,
                mixed["composition_wt_pct"],
                deposits,
                liner,
                sample_temperature_C,
                pressure,
            )
        )
        onset = liquid_fraction > LIQUIDUS_SOLID_EPSILON
        flags.append("tier_b_liquid_fraction_only_no_solidus_claim")
    else:
        raise LinerCorrosionRefusal("liner_criterion_tier_invalid", tier)

    species_aggressiveness: dict[str, Any] = {}
    if tier == "B" and sampler is not None and pressure is not None:
        for species, mass_kg in deposits.items():
            if mass_kg <= 0.0:
                continue
            try:
                species_aggressiveness[species] = species_aggressiveness_finite_difference(
                    liner,
                    species,
                    sampler=sampler,
                    temperature_C=sample_temperature_C,
                    pressure_bar=pressure,
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

    dissolution_ratio: float | None = None
    if onset:
        if x_star is None:
            refusals.append(
                LinerCorrosionRefusal(
                    "saturated_liquid_deposit_fraction_missing"
                ).as_dict()
            )
        else:
            dissolution_ratio = post_onset_dissolution_ratio(x_star)
        flags.append(DISSOLUTION_KINETICS_FLAG)
        flags.append(CORROSION_NO_RATE_FLAG)

    recurrence: dict[str, Any] | None = None
    if deposit_inert_kg_by_species is None or deposit_reactive_kg_by_species is None:
        refusals.append(
            LinerCorrosionRefusal("reactive_deposit_branch_missing").as_dict()
        )
    elif tier == "B":
        if sampler is None:
            refusals.append(
                LinerCorrosionRefusal("liquid_fraction_sampler_missing").as_dict()
            )
        elif pressure is None:
            refusals.append(
                LinerCorrosionRefusal("liner_service_pressure_missing").as_dict()
            )
        else:
            recurrence = corrosion_campaign_recurrence(
                liner,
                deposits,
                deposit_inert_kg_by_species,
                deposit_reactive_kg_by_species,
                density_kg_m3=density,
                area_m2=area,
                reacted_layer_depth_m=depth,
                temperature_C=service_C,
                pressure_bar=pressure,
                margin_C=margin_C,
                horizon=horizon,
                sampler=sampler,
            )
    elif tier == "A":
        recurrence = corrosion_campaign_recurrence(
            liner,
            deposits,
            deposit_inert_kg_by_species,
            deposit_reactive_kg_by_species,
            density_kg_m3=density,
            area_m2=area,
            reacted_layer_depth_m=depth,
            temperature_C=service_C,
            pressure_bar=pressure,
            margin_C=margin_C,
            horizon=horizon,
            sampler=TierAInvariantSampler(table),
        )
    else:
        raise LinerCorrosionRefusal("liner_criterion_tier_invalid", tier)

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
        "porosity_fraction": raw_porosity,
        "deposit_kg_by_species": deposits,
        "first_melt_T_C": first_melt_C,
        "service_T_C": service_C,
        "service_pressure_bar": pressure,
        "enamel": dict(enamel),
        "margin_C": margin_C,
        "liquid_fraction": liquid_fraction,
        "onset": onset,
        "species_aggressiveness": species_aggressiveness,
        "liner_consumed_kg_per_kg_deposit": dissolution_ratio,
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
        molar_mass = formula.molar_mass_g_per_mol
        if molar_mass is None or molar_mass <= 0.0:
            raise LinerCorrosionRefusal(ELEMENT_CLOSURE_FLAG, oxide)
        oxide_moles_per_100g = float(wt_pct) / float(molar_mass)
        for element, atoms in formula.elements.items():
            result[element] = result.get(element, 0.0) + oxide_moles_per_100g * float(atoms)
    return result


def _check_element_closure(
    bulk_composition_wt_pct: Mapping[str, float],
    result: Any,
) -> None:
    phase_masses = getattr(result, "phase_masses_kg", None)
    phase_compositions = getattr(result, "phase_compositions", None)
    if not isinstance(phase_masses, Mapping) or not phase_masses:
        raise LinerCorrosionRefusal(ELEMENT_CLOSURE_FLAG, "phase masses are absent")
    if not isinstance(phase_compositions, Mapping) or not phase_compositions:
        raise LinerCorrosionRefusal(ELEMENT_CLOSURE_FLAG, "phase compositions are absent")
    masses: dict[str, float] = {}
    for phase, raw_mass in phase_masses.items():
        mass = _finite_real(raw_mass, f"phase_masses_kg[{phase!r}]")
        if mass < 0.0:
            raise LinerCorrosionRefusal(ELEMENT_CLOSURE_FLAG, "negative phase mass")
        if mass > 0.0:
            masses[str(phase)] = mass
    total_mass = math.fsum(masses.values())
    if total_mass <= 0.0:
        raise LinerCorrosionRefusal(ELEMENT_CLOSURE_FLAG, "zero total phase mass")

    output_elements: dict[str, float] = {}
    for phase, mass in masses.items():
        raw_composition = phase_compositions.get(phase)
        if not isinstance(raw_composition, Mapping) or not raw_composition:
            raise LinerCorrosionRefusal(
                ELEMENT_CLOSURE_FLAG, f"phase {phase!r} has no oxide composition"
            )
        phase_comp = validate_oxide_composition(raw_composition)
        phase_elements = _element_moles_from_oxide_weights(phase_comp)
        mass_fraction = mass / total_mass
        for element, moles in phase_elements.items():
            output_elements[element] = output_elements.get(element, 0.0) + (
                mass_fraction * moles
            )
    input_elements = _element_moles_from_oxide_weights(bulk_composition_wt_pct)
    for element in set(input_elements) | set(output_elements):
        expected = input_elements.get(element, 0.0)
        actual = output_elements.get(element, 0.0)
        scale = max(abs(expected), 1.0e-12)
        if abs(actual - expected) / scale > ELEMENT_CLOSURE_REL_TOLERANCE:
            raise LinerCorrosionRefusal(
                ELEMENT_CLOSURE_FLAG,
                f"{element} closure differs by {abs(actual - expected) / scale:.6g}",
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
        self._curve_cache: dict[tuple[Any, ...], TabulatedLiquidFractionCurve] = {}

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
        _check_element_closure(composition, result)
        fraction = getattr(result, "liquid_fraction", None)
        if fraction is None:
            raise LinerCorrosionRefusal("engine_liquid_fraction_missing")
        liquid_fraction = _checked_liquid_fraction(fraction)
        self._point_cache[key] = liquid_fraction
        return liquid_fraction

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
        for index in range(MAGEMIN_PHI_CURVE_INTERVALS + 1):
            deposit_fraction = index / MAGEMIN_PHI_CURVE_INTERVALS
            composition = _composition_at_fraction(liner, deposit, deposit_fraction)
            try:
                liquid_fraction = self(composition, temperature, pressure)
            except LinerCorrosionRefusal as exc:
                # The table ends at the first domain boundary. No extrapolation
                # or result from outside the MAGEMin gate is used.
                if exc.reason == ENGINE_OUT_OF_DOMAIN_FLAG and points:
                    break
                raise
            points.append((deposit_fraction, liquid_fraction))
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
    if len(sides) != 2:
        raise LinerCorrosionDataError(
            source_id,
            "invalid_phase_reaction",
            f"{observation_id} reaction must have two printed sides",
        )
    parsed_sides: list[list[str]] = []
    phases: list[str] = []
    for side in sides:
        side = re.sub(r"\bL\+\s*$", "L", side.strip())
        side_phases = []
        for printed_phase in side.split("+"):
            phase = printed_phase.strip()
            if not phase:
                raise LinerCorrosionDataError(
                    source_id,
                    "invalid_phase_reaction",
                    f"{observation_id} has an empty phase token",
                )
            if re.fullmatch(r"L(?:\s*\([^)]*\))?", phase):
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
        raise LinerCorrosionDataError(
            source_id,
            "reaction_has_no_liquid",
            f"{observation_id} reaction has no liquid phase",
        )
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
            and values.get("quantity") == "transition_temperature"
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
        if "phases_as_printed" in values:
            phases = _phases(
                values["phases_as_printed"], source_id, observation_id
            )
            inferred_kind, _ = _reaction_type(reaction, source_id, observation_id)
        elif reaction:
            inferred_kind, phases = _reaction_type(
                reaction, source_id, observation_id
            )
        else:
            inferred_kind = values.get("invariant_type")
            phases = _phases(
                values.get("phases_as_printed"), source_id, observation_id
            )
        kind = values.get("invariant_type", inferred_kind)
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

    return records
