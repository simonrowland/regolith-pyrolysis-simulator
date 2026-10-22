"""Pure gas removal, capacity arithmetic and oxygen-origin partitioning (L1)."""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from simulator.scalar_boundary import is_declared_real_scalar
from simulator.transport_regime import (
    gas_dynamic_viscosity_Pa_s,
    pipe_downstream_pressure_pa_for_flow_fraction,
)

OXYGEN_SPECIES = "O2"
_COLD_TRAIN_RATING_REFERENCE_NAMES = frozenset({"p_ref_Pa", "T_ref_K"})


def _finite_nonnegative(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be numeric") from exc
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")
    return number


def _finite_positive(value: Any, name: str) -> float:
    number = _finite_nonnegative(value, name)
    if number <= 0.0:
        raise ValueError(f"{name} must be positive")
    return number


@dataclass(frozen=True)
class NoColdTrain:
    reason: str = "not_configured"


@dataclass(frozen=True)
class FiniteCapacity:
    value_kg_hr: float
    p_ref_Pa: float | None = None
    T_ref_K: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "value_kg_hr", _finite_positive(self.value_kg_hr, "value_kg_hr")
        )
        for name in _COLD_TRAIN_RATING_REFERENCE_NAMES:
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _finite_positive(value, name))


@dataclass(frozen=True)
class EffectiveTransportCapacity:
    """One-tick controlled-flow boundary shared by bleed and diagnostics."""

    pipe_capacity_kg_hr: float
    equipment_capacity_kg_hr: float | None
    effective_capacity_kg_hr: float
    evolved_flux_kg_hr: float
    retained_holdup_kg: float
    demand_flux_kg_hr: float
    swallowed_flux_kg_hr: float
    saturation: float
    upstream_pressure_bar: float
    downstream_pressure_bar: float
    binding_cause: str


def controlled_flow_capacity(
    *,
    pipe_capacity_kg_hr: float,
    equipment_capacity_kg_hr: float | None,
    evolved_flux_kg_hr: float,
    retained_holdup_kg: float = 0.0,
    dt_hr: float = 1.0,
    equipment_capacity_required: bool = True,
    upstream_pressure_bar: float,
    pipe_diameter_m: float,
    pipe_length_m: float,
    gas_temperature_K: float,
    gas_molar_mass_kg_mol: float,
) -> EffectiveTransportCapacity:
    """Resolve a controlled-pO2 flow boundary without prescribing suction."""

    declared = {
        "pipe_capacity_kg_hr": pipe_capacity_kg_hr,
        "evolved_flux_kg_hr": evolved_flux_kg_hr,
        "retained_holdup_kg": retained_holdup_kg,
        "dt_hr": dt_hr,
        "upstream_pressure_bar": upstream_pressure_bar,
        "pipe_diameter_m": pipe_diameter_m,
        "pipe_length_m": pipe_length_m,
        "gas_temperature_K": gas_temperature_K,
        "gas_molar_mass_kg_mol": gas_molar_mass_kg_mol,
    }
    if equipment_capacity_kg_hr is not None:
        declared["equipment_capacity_kg_hr"] = equipment_capacity_kg_hr
    if any(
        not is_declared_real_scalar(value, allow_numeric_str=True)
        for value in declared.values()
    ):
        raise TypeError("controlled-flow capacity input is missing")

    pipe_diameter_m = float(pipe_diameter_m)
    pipe_length_m = float(pipe_length_m)
    gas_temperature_K = float(gas_temperature_K)
    gas_molar_mass_kg_mol = float(gas_molar_mass_kg_mol)
    if not all(math.isfinite(value) and value > 0.0 for value in (
        pipe_diameter_m, pipe_length_m, gas_temperature_K, gas_molar_mass_kg_mol,
    )):
        raise ValueError("controlled-flow pipe geometry and gas properties must be finite and positive")

    pipe_capacity = max(0.0, float(pipe_capacity_kg_hr))
    equipment_capacity = (
        max(0.0, float(equipment_capacity_kg_hr))
        if equipment_capacity_kg_hr is not None
        else 0.0
    )
    evolved_flux = max(0.0, float(evolved_flux_kg_hr))
    retained_holdup = max(0.0, float(retained_holdup_kg))
    tick_duration_hr = max(0.0, float(dt_hr))
    upstream_pressure = max(0.0, float(upstream_pressure_bar))
    effective_capacity = (
        min(pipe_capacity, equipment_capacity)
        if equipment_capacity_kg_hr is not None
        else (0.0 if equipment_capacity_required else pipe_capacity)
    )
    # retained_holdup [kg] / tick_duration [hr] = the average kg/hr needed
    # to evacuate pre-tick inventory during this tick.  Adding the live
    # evolved source gives the full boundary demand without counting the
    # current source twice in retained inventory.
    holdup_drain_flux = (
        retained_holdup / tick_duration_hr
        if tick_duration_hr > 0.0
        else 0.0
    )
    demand_flux = evolved_flux + holdup_drain_flux
    swallowed_flux = min(demand_flux, effective_capacity)
    saturation = (
        demand_flux / effective_capacity
        if effective_capacity > 0.0
        else (math.inf if demand_flux > 0.0 else 0.0)
    )

    if equipment_capacity_kg_hr is None or equipment_capacity <= 0.0:
        binding_cause = "controlled_o2_no_equipment"
    elif equipment_capacity <= pipe_capacity:
        binding_cause = "controlled_o2_equipment"
    else:
        binding_cause = "pipe"

    if upstream_pressure <= 0.0 or pipe_capacity <= 0.0:
        downstream_pressure = upstream_pressure
    else:
        downstream_pressure = pipe_downstream_pressure_pa_for_flow_fraction(
            pipe_diameter_m, pipe_length_m, gas_molar_mass_kg_mol,
            gas_dynamic_viscosity_Pa_s(gas_temperature_K), gas_temperature_K,
            upstream_pressure * 1.0e5, swallowed_flux / pipe_capacity,
        ) / 1.0e5

    return EffectiveTransportCapacity(
        pipe_capacity_kg_hr=pipe_capacity,
        equipment_capacity_kg_hr=(
            equipment_capacity
            if equipment_capacity_kg_hr is not None
            else None
        ),
        effective_capacity_kg_hr=effective_capacity,
        evolved_flux_kg_hr=evolved_flux,
        retained_holdup_kg=retained_holdup,
        demand_flux_kg_hr=demand_flux,
        swallowed_flux_kg_hr=swallowed_flux,
        saturation=saturation,
        upstream_pressure_bar=upstream_pressure,
        downstream_pressure_bar=downstream_pressure,
        binding_cause=binding_cause,
    )


@dataclass(frozen=True)
class OxygenShadowPartition:
    external_mol: float
    admitted_mol: float
    accumulated_mol: float
    relieved_mol: float
    held_mol: float

    @property
    def debited_mol(self) -> float:
        return (
            self.external_mol
            + self.admitted_mol
            + self.accumulated_mol
            + self.relieved_mol
        )


def external_oxygen_outlet_mol(*, bled_o2_mol: float, overhead_o2_mol: float,
                             external_o2_holdup_mol: float,
                             integrated_external_o2_mol: float | None = None) -> float:
    """Use a transport-derived outlet, or the legacy uniform batch split."""
    if integrated_external_o2_mol is not None:
        return integrated_external_o2_mol
    external_holdup = external_oxygen_holdup_mol(overhead_o2_mol=overhead_o2_mol, external_o2_holdup_mol=external_o2_holdup_mol)
    return (min(bled_o2_mol, bled_o2_mol * external_holdup / overhead_o2_mol)
            if bled_o2_mol > 0.0 and overhead_o2_mol > 0.0 else 0.0)


def partition_melt_oxygen(
    *,
    bled_o2_mol: float,
    overhead_o2_mol: float,
    external_o2_holdup_mol: float,
    capacity: FiniteCapacity,
    dt_hr: float,
    p_o2_Pa: float,
    k_relief_kg_hr_Pa: float,
    p_open_Pa: float,
    molar_mass_kg_mol: float,
    accumulator_enabled: bool = False,
    cistern_fill_kg: float = 0.0,
    cavern_capacity_kg: float = 0.0,
    integrated_external_o2_mol: float | None = None,
) -> OxygenShadowPartition:
    # Ordinary bleed preserves the HEAD provenance split. Admission acts on
    # its melt-origin share. Relief is an additional debit from the full
    # post-admission melt-origin inventory, independent of ordinary bleed.
    # Unit check: (kg/hr)*hr/(kg/mol) and
    # (kg/(hr Pa))*Pa*hr/(kg/mol) both reduce to mol. Sanity anchors: with no
    # external holdup and ample capacity, all bled O2 is admitted; with zero
    # admission and closed relief, all melt-origin bled O2 remains held.
    external_holdup = external_oxygen_holdup_mol(overhead_o2_mol=overhead_o2_mol, external_o2_holdup_mol=external_o2_holdup_mol)
    external = external_oxygen_outlet_mol(
        bled_o2_mol=bled_o2_mol, overhead_o2_mol=overhead_o2_mol,
        external_o2_holdup_mol=external_holdup,
        integrated_external_o2_mol=integrated_external_o2_mol,
    )
    melt_inventory = max(0.0, overhead_o2_mol - external_holdup)
    melt_bled = min(melt_inventory, max(0.0, bled_o2_mol - external))
    admitted = min(
        melt_bled,
        capacity.value_kg_hr * dt_hr / molar_mass_kg_mol,
    )
    remainder = max(0.0, melt_inventory - admitted)
    accumulated = 0.0
    if accumulator_enabled:
        available_cistern_kg = max(0.0, cavern_capacity_kg - cistern_fill_kg)
        accumulated = min(
            remainder,
            available_cistern_kg / molar_mass_kg_mol,
        )
        remainder = max(0.0, remainder - accumulated)
    relief_law_mol = (
        k_relief_kg_hr_Pa
        * max(0.0, p_o2_Pa - p_open_Pa)
        * dt_hr
        / molar_mass_kg_mol
    )
    relieved = min(remainder, max(0.0, relief_law_mol))
    held = max(0.0, remainder - relieved)
    return OxygenShadowPartition(
        external, admitted, accumulated, relieved, held
    )


def bled_species_mol(
    holdup_mol: dict[str, float],
    *,
    total_mol: float,
    total_kg: float,
    controls: dict,
) -> dict[str, float]:
    if controls.get("force_drain_all", False):
        return dict(holdup_mol)

    conductance_raw = controls.get("bleed_conductance_kg_s")
    if conductance_raw is None:
        conductance_raw = controls.get("bleed_conductance_kg_s_per_bar")
    conductance = max(0.0, float(conductance_raw or 0.0))
    dt_hr = max(0.0, float(controls.get("dt_hr", 1.0)))
    flow_capacity = controls.get("effective_transport_capacity")
    if isinstance(flow_capacity, EffectiveTransportCapacity):
        # The shared boundary already resolved the one allowed mass flow.
        # Downstream pressure is its diagnostic inversion only; it must
        # never be fed back into committed disposition.
        bleed_kg = flow_capacity.swallowed_flux_kg_hr * dt_hr
    else:
        # The caller supplies the owner's capacity at the actual TWO
        # pressures. A vacuum-capacity multiplier would count the drop
        # twice and, in molecular flow, over-credit a half-pressure sink.
        upstream = float(controls.get("p_total_bar") or 0.0)
        downstream = float(controls.get("p_downstream_bar") or 0.0)
        bleed_kg = (
            conductance * dt_hr * 3600.0
            if math.isfinite(upstream) and math.isfinite(downstream)
            and upstream > downstream >= 0.0 else 0.0
        )
    if bleed_kg <= 0.0:
        return {}
    bleed_kg = min(total_kg, bleed_kg)
    avg_molar_mass = total_kg / total_mol
    bleed_total_mol = min(total_mol, bleed_kg / avg_molar_mass)
    if bleed_total_mol <= 0.0:
        return {}
    return {
        species: min(mol, mol * bleed_total_mol / total_mol)
        for species, mol in holdup_mol.items()
        if mol > 0.0
    }


def invalid_destructive_control(controls: dict) -> str | None:
    if 'sweep_external_o2_outflow_mol' in controls:
        value = controls['sweep_external_o2_outflow_mol']
        if ('sweep_outflow_species_mol' not in controls or not is_declared_real_scalar(value)
                or not math.isfinite(float(value)) or float(value) < 0.0):
            return 'sweep_external_o2_outflow_mol requires an integrated sweep and finite non-negative mol'
    if 'sweep_outflow_species_mol' in controls:
        vector = controls['sweep_outflow_species_mol']
        if not isinstance(vector, Mapping) or any(
            not isinstance(s, str) or isinstance(n, bool) or not isinstance(n, (int, float))
            or not math.isfinite(n) or n < 0.0 for s, n in vector.items()
        ):
            return 'sweep_outflow_species_mol must contain finite non-negative mol per species per step'
        for name in ('headspace_volume_m3', 'headspace_temperature_K'):
            if not is_declared_real_scalar(controls.get(name)) or not math.isfinite(float(controls[name])) or float(controls[name]) <= 0.0:
                return f'{name} must be positive and finite for integrated sweep transport'
    for legacy_name in ("o2_vented_kg", "max_o2_flow_kg_hr"):
        if legacy_name in controls:
            return f"{legacy_name} is retired; OVERHEAD_BLEED owns partitioning"
    finite_capacity = False
    if "cold_train_capacity" in controls:
        capacity = controls["cold_train_capacity"]
        if not isinstance(capacity, (NoColdTrain, FiniteCapacity)):
            return "cold_train_capacity must be NoColdTrain or FiniteCapacity"
        if isinstance(capacity, FiniteCapacity) and (
            not math.isfinite(capacity.value_kg_hr)
            or capacity.value_kg_hr <= 0.0
        ):
            return "cold_train_capacity must be finite and positive"
        finite_capacity = isinstance(capacity, FiniteCapacity)
    force_drain_all = controls.get("force_drain_all", False)
    if not isinstance(force_drain_all, bool):
        return "force_drain_all must be a boolean"
    accumulator_enabled = controls.get("accumulator_enabled", False)
    if not isinstance(accumulator_enabled, bool):
        return "accumulator_enabled must be a boolean"
    if accumulator_enabled and not finite_capacity:
        return "accumulator_enabled requires FiniteCapacity"
    conductance_name = None
    if controls.get("bleed_conductance_kg_s") is not None:
        conductance_name = "bleed_conductance_kg_s"
    elif controls.get("bleed_conductance_kg_s_per_bar") is not None:
        conductance_name = "bleed_conductance_kg_s_per_bar"
    if conductance_name is not None:
        raw_conductance = controls[conductance_name]
        if isinstance(raw_conductance, bool):
            return f"{conductance_name} must be a finite non-negative number"
        try:
            conductance = float(raw_conductance)
        except (TypeError, ValueError):
            return f"{conductance_name} must be a finite non-negative number"
        if not math.isfinite(conductance) or conductance < 0.0:
            return f"{conductance_name} must be a finite non-negative number"
    for name, default in (
        ("dt_hr", 1.0),
        ("p_total_bar", 0.0),
        ("p_downstream_bar", 0.0),
        ("external_o2_in_overhead_mol", 0.0),
        ("k_relief_kg_hr_Pa", 0.0),
        ("p_open_Pa", 0.0),
        ("p_ref_Pa", 0.0),
        ("vessel_rating_Pa", 0.0),
        ("cavern_capacity_kg", 0.0),
    ):
        if name not in controls:
            continue
        raw = controls.get(name, default)
        if raw is None:
            return f"{name} must be a finite non-negative number"
        if isinstance(raw, bool):
            return f"{name} must be a finite non-negative number"
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return f"{name} must be a finite non-negative number"
        if not math.isfinite(value) or value < 0.0:
            return f"{name} must be a finite non-negative number"
    if finite_capacity:
        for name in (
            "k_relief_kg_hr_Pa",
            "p_open_Pa",
            "vessel_rating_Pa",
        ):
            try:
                value = float(controls[name])
            except (KeyError, TypeError, ValueError):
                return f"{name} must be a finite positive number"
            if not math.isfinite(value) or value <= 0.0:
                return f"{name} must be a finite positive number"
        if float(controls["p_open_Pa"]) >= float(
            controls["vessel_rating_Pa"]
        ):
            return "p_open_Pa must be below vessel_rating_Pa"
    if accumulator_enabled:
        try:
            cavern_capacity_kg = float(controls["cavern_capacity_kg"])
        except (KeyError, TypeError, ValueError):
            return (
                "accumulator requires positive cavern_capacity_kg"
            )
        if cavern_capacity_kg <= 0.0:
            return "cavern_capacity_kg must be a finite positive number"
    return None


def removal_partition(
    holdup_mol: Mapping[str, float],
    candidate_bled_mol: Mapping[str, float],
    molar_mass_kg_mol: Mapping[str, float],
    controls: Mapping,
    *,
    cistern_fill_mol: float = 0.0,
    integrated_external_o2_mol: float | None = None,
) -> tuple[dict[str, float], OxygenShadowPartition]:
    """Apply oxygen admission/relief to an already resolved ordinary outlet."""
    bled_o2 = candidate_bled_mol.get(OXYGEN_SPECIES, 0.0)
    overhead_o2 = holdup_mol.get(OXYGEN_SPECIES, 0.0)
    external_holdup = external_oxygen_holdup_mol(overhead_o2_mol=overhead_o2,
        external_o2_holdup_mol=float(controls.get("external_o2_in_overhead_mol", 0.0)))
    oxygen_mass = molar_mass_kg_mol.get(OXYGEN_SPECIES, 0.0)
    capacity = controls.get("cold_train_capacity")
    if isinstance(capacity, FiniteCapacity) and overhead_o2 > 0.0 and oxygen_mass > 0.0:
        oxygen = partition_melt_oxygen(
            bled_o2_mol=bled_o2, overhead_o2_mol=overhead_o2,
            external_o2_holdup_mol=external_holdup,
            integrated_external_o2_mol=integrated_external_o2_mol,
            capacity=capacity, dt_hr=float(controls.get("dt_hr", 1.0)),
            p_o2_Pa=float(controls.get("p_ref_Pa", 0.0)),
            k_relief_kg_hr_Pa=float(controls.get("k_relief_kg_hr_Pa", 0.0)),
            p_open_Pa=float(controls.get("p_open_Pa", 0.0)),
            molar_mass_kg_mol=oxygen_mass,
            accumulator_enabled=controls.get("accumulator_enabled") is True,
            cistern_fill_kg=cistern_fill_mol * oxygen_mass,
            cavern_capacity_kg=float(controls.get("cavern_capacity_kg", 0.0)),
        )
    else:
        external = external_oxygen_outlet_mol(
            bled_o2_mol=bled_o2, overhead_o2_mol=overhead_o2,
            external_o2_holdup_mol=external_holdup,
            integrated_external_o2_mol=integrated_external_o2_mol,
        )
        oxygen = OxygenShadowPartition(external, max(0.0, bled_o2 - external), 0.0, 0.0, 0.0)
    debited = dict(candidate_bled_mol)
    if oxygen.debited_mol > 0.0:
        debited[OXYGEN_SPECIES] = oxygen.debited_mol
    else:
        debited.pop(OXYGEN_SPECIES, None)
    return debited, oxygen


def bounded_sweep_outlet_mol(holdup_mol, requested_outlet_mol):
    """Resolve the accepted outlet against available inventory at binary64 precision."""
    return {s: min(n, holdup_mol.get(s, 0.0))
            for s, n in requested_outlet_mol.items() if n > 0.0}


def net_sweep_transfer_mol(holdup_mol, inlet_mol, removed_mol):
    """Net inlet/outlet once, matching the ledger transfer evaluation order."""
    debits = {s: n - inlet_mol.get(s, 0.0)
              for s, n in removed_mol.items() if n > inlet_mol.get(s, 0.0)}
    credits = {s: n - removed_mol.get(s, 0.0)
               for s, n in inlet_mol.items() if n > removed_mol.get(s, 0.0)}
    # Premise: one net chamber lot per species. n_final=n_old-debit+credit,
    # in mol. For inlet==outlet both lots vanish and old inventory survives;
    # subtracting after adding the whole inlet would lose small retained loads.
    final = dict(holdup_mol)
    for species, mol in debits.items():
        final[species] = final.get(species, 0.0) - mol
    for species, mol in credits.items():
        final[species] = final.get(species, 0.0) + mol
    return debits, credits, final


def external_oxygen_holdup_mol(*, overhead_o2_mol, external_o2_holdup_mol):
    return min(max(0.0, external_o2_holdup_mol), max(0.0, overhead_o2_mol))
