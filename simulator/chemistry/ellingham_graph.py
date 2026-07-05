"""Read-only query API over pressure-modified (sense-2) Ellingham data.

Diagnostic module for reading the Ellingham diagram at a temperature and pO2
operating point. Reuses the canonical JANAF linear ΔG(T) fits from
:mod:`simulator.chemistry.ellingham_thermo` and the Antoine + pO2 dissociation
lever from :mod:`engines.builtin.vapor_pressure` without mutating ledger,
vapor authority, or equilibrium paths.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simulator.chemistry.ellingham_thermo import (
    ELLINGHAM_THERMO,
    ellingham_delta_g_kj_per_mol_o2,
    ellingham_fit_segments,
    ellingham_stoichiometry,
)

# Match simulator.state.GAS_CONSTANT without importing simulator.state here.
GAS_CONSTANT_J_PER_MOL_K = 8.31446
CELSIUS_TO_KELVIN_OFFSET = 273.15

# Same evaporation floor used by BuiltinVaporPressureProvider.
EVOLUTION_PRESSURE_FLOOR_PA = 1e-15

_DEFAULT_VAPOR_PRESSURES_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "vapor_pressures.yaml"
)


@dataclass(frozen=True)
class EvolutionRankEntry:
    species: str
    P_eff_Pa: float
    metal_activity: float | None = None


def _require_species(species: str) -> str:
    key = str(species)
    if key not in ELLINGHAM_THERMO:
        raise KeyError(f"unknown Ellingham species: {key}")
    return key


def dissociation_delta_g(species: str, temperature_K: float) -> float:
    """ΔG of the oxide-formation reaction per mol O₂ at ``temperature_K`` (kJ)."""

    return ellingham_delta_g_kj_per_mol_o2(
        _require_species(species),
        float(temperature_K),
    )


def dissociation_equilibrium_constant(species: str, temperature_K: float) -> float:
    """K for the formation reaction, ``exp(ΔG·1000 / (R·T))``."""

    dG_kJ = dissociation_delta_g(species, temperature_K)
    T_K = float(temperature_K)
    return math.exp(dG_kJ * 1000.0 / (GAS_CONSTANT_J_PER_MOL_K * T_K))


def dissociation_pO2_threshold(
    species: str,
    temperature_K: float,
    *,
    a_oxide: float = 1.0,
) -> float:
    """pO₂ (bar) at which metal activity reaches unity for the parent oxide.

    Solves the same equilibrium used by the builtin vapor provider:
    ``a_M = (K · a_oxide^n_ox / pO2)^(1/n_M) = 1`` with ``a_oxide`` defaulting
    to unity (pure-oxide reference).
    """

    _require_species(species)
    _, n_ox = ellingham_stoichiometry(species)
    K = dissociation_equilibrium_constant(species, temperature_K)
    return K * (max(float(a_oxide), 0.0) ** n_ox)


def metal_activity_factor(
    species: str,
    temperature_K: float,
    pO2_bar: float,
    *,
    a_oxide: float = 1.0,
) -> float:
    """Ellingham metal activity ``a_M`` at ``(T, pO2)`` with ``a_oxide``."""

    n_M, n_ox = ellingham_stoichiometry(_require_species(species))
    K = dissociation_equilibrium_constant(species, temperature_K)
    pO2 = max(float(pO2_bar), 1e-30)
    numerator = K * (max(float(a_oxide), 0.0) ** n_ox) / pO2
    if numerator <= 0.0:
        return 0.0
    return min(numerator ** (1.0 / n_M), 1.0)


def _load_default_vapor_pressure_data() -> dict[str, Any]:
    import yaml

    return yaml.safe_load(_DEFAULT_VAPOR_PRESSURES_PATH.read_text())


def _resolve_vapor_pressure_data(
    vapor_pressure_data: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if vapor_pressure_data is None:
        return _load_default_vapor_pressure_data()
    return vapor_pressure_data


def _antoine_reference_pressure_Pa(
    antoine: Mapping[str, Any],
    temperature_K: float,
) -> float | None:
    A = antoine.get("A", 0)
    B = antoine.get("B", 0)
    C = antoine.get("C", 0)
    T_K = float(temperature_K)
    if not (A > 0 and T_K > 300):
        return None
    log_P = float(A) - float(B) / (T_K + float(C))
    if not math.isfinite(log_P) or log_P > 308.0:
        return None
    return 10.0 ** log_P


def effective_equilibrium_pressure_Pa(
    species: str,
    temperature_K: float,
    pO2_bar: float,
    *,
    vapor_pressure_data: Mapping[str, Any] | None = None,
    a_oxide: float = 1.0,
    vacuum_floor_bar: float = 1e-9,
) -> float:
    """Effective equilibrium pressure ``P_eff = a_M · P_sat`` (Pa).

    Metals use the builtin Ellingham + Antoine path. Oxide vapors (for example
    ``SiO``) use the declared Antoine row with activity and pO₂ scaling from
    :mod:`engines.builtin.vapor_pressure`.
    """

    from engines.builtin.vapor_pressure import vapor_pressure_antoine_coefficients

    data = _resolve_vapor_pressure_data(vapor_pressure_data)
    T_K = float(temperature_K)
    pO2 = max(float(pO2_bar), 1e-30)
    a_ox = max(float(a_oxide), 0.0)

    metals = data.get("metals", {}) or {}
    if species in metals:
        sp_data = metals[species] or {}
        if str(sp_data.get("consumer_status", "")).lower() == "inactive":
            return 0.0
        if species not in ELLINGHAM_THERMO:
            return 0.0
        antoine, _ = vapor_pressure_antoine_coefficients(sp_data, temperature_K=T_K)
        P_reference_Pa = _antoine_reference_pressure_Pa(antoine, T_K)
        if P_reference_Pa is None:
            return 0.0
        a_M = metal_activity_factor(species, T_K, pO2, a_oxide=a_ox)
        return a_M * P_reference_Pa

    oxide_vapors = data.get("oxide_vapors", {}) or {}
    if species in oxide_vapors:
        row = oxide_vapors[species] or {}
        antoine = (row.get("antoine", {}) or {})
        P_reference_Pa = _antoine_reference_pressure_Pa(antoine, T_K)
        if P_reference_Pa is None:
            return 0.0
        activity_exponent = float(row.get("oxide_activity_exponent", 1.0) or 1.0)
        activity_factor = max(a_ox, 0.0) ** activity_exponent
        P_eq_Pa = P_reference_Pa * activity_factor

        pO2_exponent = float(row.get("pO2_exponent", 0.0) or 0.0)
        if pO2_exponent:
            pO2_reference_bar = max(
                1e-30,
                float(row.get("pO2_reference_bar", 1.0) or 1.0),
            )
            P_eq_Pa *= (pO2 / pO2_reference_bar) ** pO2_exponent
        elif species == "SiO" and pO2 > float(vacuum_floor_bar):
            P_eq_Pa *= math.sqrt(float(vacuum_floor_bar) / pO2)
        return max(P_eq_Pa, 0.0)

    return 0.0


def evolves(
    species: str,
    temperature_K: float,
    pO2_bar: float,
    *,
    vapor_pressure_data: Mapping[str, Any] | None = None,
    a_oxide: float = 1.0,
    vacuum_floor_bar: float = 1e-9,
) -> bool:
    """Whether ``species`` exceeds the builtin evaporation pressure floor."""

    P_eff = effective_equilibrium_pressure_Pa(
        species,
        temperature_K,
        pO2_bar,
        vapor_pressure_data=vapor_pressure_data,
        a_oxide=a_oxide,
        vacuum_floor_bar=vacuum_floor_bar,
    )
    return P_eff > EVOLUTION_PRESSURE_FLOOR_PA


def evolution_order(
    temperature_K: float,
    pO2_bar: float,
    species_list: Sequence[str],
    *,
    vapor_pressure_data: Mapping[str, Any] | None = None,
    a_oxide: float = 1.0,
    vacuum_floor_bar: float = 1e-9,
) -> tuple[EvolutionRankEntry, ...]:
    """Species ranked by descending ``P_eff`` at the operating point."""

    ranked: list[EvolutionRankEntry] = []
    for species in species_list:
        P_eff = effective_equilibrium_pressure_Pa(
            species,
            temperature_K,
            pO2_bar,
            vapor_pressure_data=vapor_pressure_data,
            a_oxide=a_oxide,
            vacuum_floor_bar=vacuum_floor_bar,
        )
        metal_activity = None
        if species in ELLINGHAM_THERMO:
            metal_activity = metal_activity_factor(
                species,
                temperature_K,
                pO2_bar,
                a_oxide=a_oxide,
            )
        ranked.append(
            EvolutionRankEntry(
                species=str(species),
                P_eff_Pa=P_eff,
                metal_activity=metal_activity,
            )
        )
    ranked.sort(key=lambda entry: (-entry.P_eff_Pa, entry.species))
    return tuple(ranked)


def crossover_temperature_C(species_a: str, species_b: str) -> float | None:
    """Sense-1 ladder crossover in °C where the two ΔG lines intersect.

    Returns ``None`` when the algebraic root falls outside the shared JANAF fit
    segment, matching :meth:`BuiltinMetallothermicStepProvider._crossover_temperature_C`.
    """

    for segment_a in ellingham_fit_segments(species_a):
        for segment_b in ellingham_fit_segments(species_b):
            low_K = max(segment_a.range_K[0], segment_b.range_K[0])
            high_K = min(segment_a.range_K[1], segment_b.range_K[1])
            if low_K > high_K:
                continue
            dS_delta = (
                segment_a.dS_f_kJ_per_mol_K_per_mol_O2
                - segment_b.dS_f_kJ_per_mol_K_per_mol_O2
            )
            if abs(dS_delta) < 1e-15:
                continue
            root_K = (
                segment_a.dH_f_kJ_per_mol_O2 - segment_b.dH_f_kJ_per_mol_O2
            ) / dS_delta
            if low_K <= root_K <= high_K:
                return root_K - CELSIUS_TO_KELVIN_OFFSET
    return None


def crossover_temperature(
    species_a: str,
    species_b: str,
) -> float | None:
    """Alias for :func:`crossover_temperature_C` (returns °C)."""

    return crossover_temperature_C(species_a, species_b)


def to_chart_data(
    species_list: Sequence[str] | None = None,
    *,
    T_min_K: float = 1100.0,
    T_max_K: float = 2200.0,
    T_step_K: float = 25.0,
) -> dict[str, list[dict[str, float]]]:
    """ΔG-vs-T polylines as plain data for plotting or export."""

    species = tuple(species_list or ELLINGHAM_THERMO.keys())
    chart: dict[str, list[dict[str, float]]] = {}
    T = float(T_min_K)
    T_end = float(T_max_K)
    step = float(T_step_K)
    if step <= 0.0:
        raise ValueError("T_step_K must be positive")
    while T <= T_end + 1e-9:
        for name in species:
            chart.setdefault(name, []).append(
                {
                    "temperature_K": T,
                    "temperature_C": T - CELSIUS_TO_KELVIN_OFFSET,
                    "delta_g_kJ_per_mol_O2": dissociation_delta_g(name, T),
                }
            )
        T += step
    return chart