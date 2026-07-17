"""Pure shadow solve for cold-train/overhead capacity coupling."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from engines.builtin.overhead_bleed import (
    BuiltinOverheadBleedProvider,
    compressible_pressure_capacity_fraction,
    controlled_flow_capacity,
)
from simulator.physical_constants import GAS_CONSTANT
from simulator.thermal_train import FiniteCapacity, NoColdTrain


OXYGEN_SPECIES = "O2"
DEFAULT_REL_TOL = 1.0e-9
DEFAULT_ABS_TOL_PA = 1.0e-6
DEFAULT_MAX_ITERATIONS = 50


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


@dataclass(frozen=True)
class SaturationShadow:
    pipe: float
    oxygen: float | None
    combined: float
    binding_cause: str


@dataclass(frozen=True)
class CapacityShadowResult:
    capacity: NoColdTrain | FiniteCapacity
    partial_pressures_Pa: Mapping[str, float]
    evaporation_flux_kg_hr: Mapping[str, float]
    bled_species_mol: Mapping[str, float]
    terminal_offgas_mol: Mapping[str, float]
    oxygen: OxygenShadowPartition
    saturation: SaturationShadow
    iterations: int
    max_delta_history_Pa: tuple[float, ...]
    mass_closure_error_pct: float
    authoritative: bool = False

    def __post_init__(self) -> None:
        for name in (
            "partial_pressures_Pa",
            "evaporation_flux_kg_hr",
            "bled_species_mol",
            "terminal_offgas_mol",
        ):
            object.__setattr__(
                self,
                name,
                MappingProxyType(dict(getattr(self, name))),
            )


@dataclass(frozen=True)
class CapacityShadowRefusal:
    reason: str
    iterations: int
    authoritative: bool = False


class CapacityCouplingRefusalError(RuntimeError):
    def __init__(self, refusal: CapacityShadowRefusal) -> None:
        self.refusal = refusal
        super().__init__(refusal.reason)


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
) -> OxygenShadowPartition:
    # Ordinary bleed preserves the HEAD provenance split. Admission acts on
    # its melt-origin share. Relief is an additional debit from the full
    # post-admission melt-origin inventory, independent of ordinary bleed.
    # Unit check: (kg/hr)*hr/(kg/mol) and
    # (kg/(hr Pa))*Pa*hr/(kg/mol) both reduce to mol. Sanity anchors: with no
    # external holdup and ample capacity, all bled O2 is admitted; with zero
    # admission and closed relief, all melt-origin bled O2 remains held.
    external_holdup = min(
        max(0.0, external_o2_holdup_mol), max(0.0, overhead_o2_mol)
    )
    external = (
        min(bled_o2_mol, bled_o2_mol * external_holdup / overhead_o2_mol)
        if bled_o2_mol > 0.0 and overhead_o2_mol > 0.0
        else 0.0
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


def combined_saturation(
    *,
    total_evaporation_kg_hr: float,
    oxygen_evaporation_kg_hr: float,
    pipe_capacity_kg_hr: float,
    capacity: NoColdTrain | FiniteCapacity,
) -> SaturationShadow:
    pipe = (
        total_evaporation_kg_hr / pipe_capacity_kg_hr
        if pipe_capacity_kg_hr > 0.0
        else (math.inf if total_evaporation_kg_hr > 0.0 else 0.0)
    )
    oxygen = (
        oxygen_evaporation_kg_hr / capacity.value_kg_hr
        if isinstance(capacity, FiniteCapacity)
        else None
    )
    if oxygen is None or pipe >= oxygen:
        return SaturationShadow(pipe, oxygen, pipe, "pipe")
    return SaturationShadow(pipe, oxygen, oxygen, "oxygen")


def solve_capacity_shadow(
    *,
    pre_holdup_mol: Mapping[str, float],
    molar_mass_kg_mol: Mapping[str, float],
    flux_kg_hr_at_partials: Callable[[Mapping[str, float]], Mapping[str, float]],
    capacity: NoColdTrain | FiniteCapacity,
    head_bled_species_mol: Mapping[str, float],
    external_o2_holdup_mol: float,
    temperature_K: float,
    volume_m3: float,
    dt_hr: float,
    bleed_conductance_kg_s: float,
    downstream_pressure_Pa: float,
    k_relief_kg_hr_Pa: float,
    p_open_Pa: float,
    overhead_source_mol_hr_at_partials: (
        Callable[[Mapping[str, float]], Mapping[str, float]] | None
    ) = None,
    total_pressure_Pa_at_partials: (
        Callable[[Mapping[str, float]], float] | None
    ) = None,
    vessel_rating_Pa: float | None = None,
    accumulator_enabled: bool = False,
    cistern_fill_kg: float = 0.0,
    cavern_capacity_kg: float = 0.0,
    controlled_flow: bool = False,
    rel_tol: float = DEFAULT_REL_TOL,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> CapacityShadowResult | CapacityShadowRefusal:
    controls = {
        "cold_train_capacity": capacity,
        "dt_hr": dt_hr,
        "bleed_conductance_kg_s": bleed_conductance_kg_s,
        "p_total_bar": 0.0,
        "p_downstream_bar": downstream_pressure_Pa / 100000.0,
    }
    if not isinstance(accumulator_enabled, bool):
        return CapacityShadowRefusal(
            "accumulator_enabled must be a boolean",
            0,
        )
    if isinstance(capacity, FiniteCapacity):
        controls.update({
            "k_relief_kg_hr_Pa": k_relief_kg_hr_Pa,
            "p_open_Pa": p_open_Pa,
            "vessel_rating_Pa": vessel_rating_Pa,
        })
        if accumulator_enabled:
            controls.update({
                "accumulator_enabled": True,
                "cistern_fill_kg": cistern_fill_kg,
                "cavern_capacity_kg": cavern_capacity_kg,
            })
    invalid = BuiltinOverheadBleedProvider._invalid_destructive_control(controls)
    if invalid is not None:
        return CapacityShadowRefusal(invalid, 0)
    if not all(
        math.isfinite(value) and value > 0.0
        for value in (temperature_K, volume_m3, dt_hr)
    ):
        return CapacityShadowRefusal(
            "temperature_K, volume_m3, and dt_hr must be finite and positive",
            0,
        )
    if vessel_rating_Pa is not None and (
        not math.isfinite(vessel_rating_Pa) or vessel_rating_Pa <= 0.0
    ):
        return CapacityShadowRefusal(
            "vessel_rating_Pa must be finite and positive",
            0,
        )
    if not isinstance(capacity, FiniteCapacity):
        oxygen = OxygenShadowPartition(0.0, 0.0, 0.0, 0.0, 0.0)
        return CapacityShadowResult(
            capacity=capacity,
            partial_pressures_Pa={},
            evaporation_flux_kg_hr={},
            bled_species_mol=dict(head_bled_species_mol),
            terminal_offgas_mol={
                species: mol
                for species, mol in head_bled_species_mol.items()
                if species != OXYGEN_SPECIES
            },
            oxygen=oxygen,
            saturation=SaturationShadow(0.0, None, 0.0, "pipe"),
            iterations=0,
            max_delta_history_Pa=(),
            mass_closure_error_pct=0.0,
        )

    species_order = tuple(sorted(set(pre_holdup_mol) | set(molar_mass_kg_mol)))
    partials = {
        species: max(0.0, float(pre_holdup_mol.get(species, 0.0)))
        * GAS_CONSTANT
        * temperature_K
        / volume_m3
        for species in species_order
    }
    delta_history: list[float] = []
    final_bled: dict[str, float] = {}
    final_offgas: dict[str, float] = {}
    final_oxygen = OxygenShadowPartition(0.0, 0.0, 0.0, 0.0, 0.0)
    final_flux: dict[str, float] = {}
    final_source_mol_hr: dict[str, float] = {}
    final_evolved: dict[str, float] = {}
    final_residual: dict[str, float] = {}
    final_flow_capacity = None
    retained_holdup_kg = sum(
        max(0.0, float(pre_holdup_mol.get(species, 0.0)))
        * molar_mass_kg_mol[species]
        for species in species_order
    )

    # Premise: HK source rates depend on the pressure produced by their own
    # post-source, post-bleed residual holdup. Algebra defines the Picard map
    # F_s(p) = (RT/V)[n0_s + q_s(p)*dt/M_s - d_s(p)], with d_O2 equal to the
    # external+admitted+relieved debit and other d_s equal to head bleed; the
    # iteration residual is F(p)-p. Unit check: q*dt/M is mol and
    # mol*(J/(mol K))*K/m3 is Pa. Sanity anchor: zero source and zero debit
    # returns the initial ideal-gas pressure, and convergence requires
    # p*=F(p*) within the absolute/relative pressure tolerance.
    for iteration in range(1, max_iterations + 1):
        try:
            final_flux = {
                str(species): max(0.0, float(rate))
                for species, rate in flux_kg_hr_at_partials(partials).items()
            }
        except Exception as exc:
            return CapacityShadowRefusal(
                f"evaporation_flux_refused:{type(exc).__name__}:{exc}",
                iteration - 1,
            )
        if overhead_source_mol_hr_at_partials is None:
            source_mol_hr = {
                species: final_flux.get(species, 0.0)
                / molar_mass_kg_mol[species]
                for species in species_order
            }
        else:
            try:
                source_mol_hr = {
                    str(species): float(rate)
                    for species, rate in overhead_source_mol_hr_at_partials(
                        partials
                    ).items()
                }
            except Exception as exc:
                return CapacityShadowRefusal(
                    f"overhead_source_refused:{type(exc).__name__}:{exc}",
                    iteration - 1,
                )
        final_source_mol_hr = dict(source_mol_hr)
        final_evolved = {
            species: max(
                0.0,
                max(0.0, float(pre_holdup_mol.get(species, 0.0)))
                + source_mol_hr.get(species, 0.0) * dt_hr,
            )
            for species in species_order
        }
        total_mol = sum(final_evolved.values())
        total_kg = sum(
            final_evolved[species] * molar_mass_kg_mol[species]
            for species in species_order
        )
        total_pressure_Pa = (
            float(total_pressure_Pa_at_partials(partials))
            if total_pressure_Pa_at_partials is not None
            else sum(partials.values())
        )
        bleed_controls = dict(controls)
        bleed_controls["p_total_bar"] = total_pressure_Pa / 100000.0
        bleed_controls["bleed_conductance_kg_s"] = bleed_conductance_kg_s
        if controlled_flow:
            positive_source_kg_hr = sum(
                max(0.0, source_mol_hr.get(species, 0.0))
                * molar_mass_kg_mol[species]
                for species in species_order
            )
            final_flow_capacity = controlled_flow_capacity(
                pipe_capacity_kg_hr=bleed_conductance_kg_s * 3600.0,
                equipment_capacity_kg_hr=capacity.value_kg_hr,
                evolved_flux_kg_hr=positive_source_kg_hr,
                retained_holdup_kg=retained_holdup_kg,
                dt_hr=dt_hr,
                upstream_pressure_bar=total_pressure_Pa / 100000.0,
            )
            bleed_controls["effective_transport_capacity"] = (
                final_flow_capacity
            )
            bleed_controls["p_downstream_bar"] = (
                final_flow_capacity.downstream_pressure_bar
            )
        ordinary_bled = BuiltinOverheadBleedProvider._bled_species_mol(
            final_evolved,
            total_mol=total_mol,
            total_kg=total_kg,
            controls=bleed_controls,
        ) if total_mol > 0.0 and total_kg > 0.0 else {}
        bled_o2 = ordinary_bled.get(OXYGEN_SPECIES, 0.0)
        if OXYGEN_SPECIES in molar_mass_kg_mol:
            final_oxygen = partition_melt_oxygen(
                bled_o2_mol=bled_o2,
                overhead_o2_mol=final_evolved.get(OXYGEN_SPECIES, 0.0),
                external_o2_holdup_mol=external_o2_holdup_mol,
                capacity=capacity,
                dt_hr=dt_hr,
                p_o2_Pa=partials.get(OXYGEN_SPECIES, 0.0),
                k_relief_kg_hr_Pa=k_relief_kg_hr_Pa,
                p_open_Pa=p_open_Pa,
                molar_mass_kg_mol=molar_mass_kg_mol[OXYGEN_SPECIES],
                accumulator_enabled=accumulator_enabled,
                cistern_fill_kg=cistern_fill_kg,
                cavern_capacity_kg=cavern_capacity_kg,
            )
        final_bled = dict(ordinary_bled)
        if final_oxygen.debited_mol > 0.0:
            final_bled[OXYGEN_SPECIES] = final_oxygen.debited_mol
        else:
            final_bled.pop(OXYGEN_SPECIES, None)
        final_offgas = {
            species: mol
            for species, mol in ordinary_bled.items()
            if species != OXYGEN_SPECIES
        }
        final_residual = {
            species: max(
                0.0,
                final_evolved[species]
                - (
                    final_oxygen.debited_mol
                    if species == OXYGEN_SPECIES
                    else ordinary_bled.get(species, 0.0)
                ),
            )
            for species in species_order
        }
        next_partials = {
            species: final_residual[species]
            * GAS_CONSTANT
            * temperature_K
            / volume_m3
            for species in species_order
        }
        max_delta = max(
            (abs(next_partials[s] - partials[s]) for s in species_order),
            default=0.0,
        )
        scale = max(
            (
                max(abs(next_partials[s]), abs(partials[s]))
                for s in species_order
            ),
            default=0.0,
        )
        delta_history.append(max_delta)
        if max_delta <= max(DEFAULT_ABS_TOL_PA, rel_tol * scale):
            break
        partials = next_partials
    else:
        return CapacityShadowRefusal(
            "picard_non_convergence",
            max_iterations,
        )

    total_pressure_Pa = (
        float(total_pressure_Pa_at_partials(partials))
        if total_pressure_Pa_at_partials is not None
        else sum(partials.values())
    )
    if (
        vessel_rating_Pa is not None
        and total_pressure_Pa > vessel_rating_Pa
    ):
        return CapacityShadowRefusal(
            "vessel_total_pressure_exceeds_rating:"
            f"{total_pressure_Pa:.17g}>{vessel_rating_Pa:.17g}",
            iteration,
        )

    initial_plus_source_kg = sum(
        final_evolved[s] * molar_mass_kg_mol[s] for s in species_order
    )
    accounted_kg = sum(
        final_residual[s] * molar_mass_kg_mol[s] for s in species_order
    ) + sum(
        final_offgas[s] * molar_mass_kg_mol[s] for s in final_offgas
    ) + final_oxygen.debited_mol * molar_mass_kg_mol.get(OXYGEN_SPECIES, 0.0)
    closure_pct = (
        abs(initial_plus_source_kg - accounted_kg) / initial_plus_source_kg * 100.0
        if initial_plus_source_kg > 0.0
        else 0.0
    )
    positive_source_kg_hr = {
        species: max(0.0, source_mol_hr)
        * molar_mass_kg_mol[species]
        for species, source_mol_hr in final_source_mol_hr.items()
    }
    if controlled_flow and final_flow_capacity is not None:
        saturation = SaturationShadow(
            final_flow_capacity.saturation,
            None,
            final_flow_capacity.saturation,
            final_flow_capacity.binding_cause,
        )
    else:
        pipe_capacity_kg_hr = (
            bleed_conductance_kg_s
            * compressible_pressure_capacity_fraction(
                total_pressure_Pa / 100000.0,
                downstream_pressure_Pa / 100000.0,
            )
            * 3600.0
        )
        saturation = combined_saturation(
            total_evaporation_kg_hr=sum(positive_source_kg_hr.values()),
            oxygen_evaporation_kg_hr=positive_source_kg_hr.get(
                OXYGEN_SPECIES, 0.0
            ),
            pipe_capacity_kg_hr=pipe_capacity_kg_hr,
            capacity=capacity,
        )
    return CapacityShadowResult(
        capacity=capacity,
        partial_pressures_Pa=partials,
        evaporation_flux_kg_hr=final_flux,
        bled_species_mol=final_bled,
        terminal_offgas_mol=final_offgas,
        oxygen=final_oxygen,
        saturation=saturation,
        iterations=iteration,
        max_delta_history_Pa=tuple(delta_history),
        mass_closure_error_pct=closure_pct,
    )
