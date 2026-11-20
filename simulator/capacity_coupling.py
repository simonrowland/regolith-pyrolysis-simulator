"""Pure shadow solve for cold-train/overhead capacity coupling."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from engines.builtin.overhead_bleed import (
    BuiltinOverheadBleedProvider,
    compressible_pressure_capacity_fraction,
    controlled_flow_capacity,
)
from simulator.overhead import pipe_species_outflow_mol_s
from simulator.physical_constants import GAS_CONSTANT, PA_PER_BAR
from simulator.thermal_train import FiniteCapacity, NoColdTrain
from simulator.transport_regime import TransportRegimeRefusal


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
    species_notices: tuple[Mapping[str, Any], ...] = ()

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
        object.__setattr__(
            self,
            "species_notices",
            tuple(
                MappingProxyType(dict(notice))
                for notice in self.species_notices
            ),
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
    pipe_diameter_m: float | None = None,
    pipe_length_m: float | None = None,
    outflow_cap_kg_s: float | None = None,
    redox_source_mol_s: Mapping[str, float] | None = None,
    redox_mol_s_at_partials: (
        Callable[[Mapping[str, float]], Mapping[str, float]] | None
    ) = None,
    authoritative: bool = False,
    rel_tol: float = DEFAULT_REL_TOL,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> CapacityShadowResult | CapacityShadowRefusal:
    controls = {
        "cold_train_capacity": capacity,
        "dt_hr": dt_hr,
        "bleed_conductance_kg_s": bleed_conductance_kg_s,
        "p_total_bar": 0.0,
        "p_downstream_bar": downstream_pressure_Pa / PA_PER_BAR,
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
    # Geometry, or an explicit outflow cap (0 is a closed line), is the
    # §2 balance. The Picard loop below stays the no-geometry shadow.
    # The live hour passes geometry and does not commit this loop.
    if (
        pipe_diameter_m is not None
        or pipe_length_m is not None
        or outflow_cap_kg_s is not None
    ):
        return _commit_headspace_balance(
            pre_holdup_mol=pre_holdup_mol,
            molar_mass_kg_mol=molar_mass_kg_mol,
            flux_kg_hr_at_partials=flux_kg_hr_at_partials,
            capacity=capacity,
            temperature_K=temperature_K,
            volume_m3=volume_m3,
            dt_hr=dt_hr,
            downstream_pressure_Pa=downstream_pressure_Pa,
            vessel_rating_Pa=vessel_rating_Pa,
            pipe_diameter_m=pipe_diameter_m,
            pipe_length_m=pipe_length_m,
            outflow_cap_kg_s=outflow_cap_kg_s,
            redox_source_mol_s=redox_source_mol_s,
            redox_mol_s_at_partials=redox_mol_s_at_partials,
            authoritative=authoritative,
            rel_tol=rel_tol,
            max_iterations=max_iterations,
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
            authoritative=authoritative,
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
    from simulator.evaporation import EvaporationFluxRefusal

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
        except EvaporationFluxRefusal:
            raise
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
            except EvaporationFluxRefusal:
                raise
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
        bleed_controls["p_total_bar"] = total_pressure_Pa / PA_PER_BAR
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
                upstream_pressure_bar=total_pressure_Pa / PA_PER_BAR,
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
                total_pressure_Pa / PA_PER_BAR,
                downstream_pressure_Pa / PA_PER_BAR,
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
        authoritative=authoritative,
    )


# --- Headspace balance (§2). One owner, two equations. ---
#
# Fast commits G = q_head + S - Q = 0 when that scalar has one sign
# change and the admission bound holds. Slow integrates
# dn_i/dt = q_i + S_i - Q_i(n) with solve_ivp (Radau). The booked
# bleed on that branch is B_i = ∫ Q_i dt, not q(end)·Δt. Newton
# proposes on the fast residual. Brent publishes the root. There is
# no seam that injects a Newton step: a returned Newton value is
# discarded.
#
# The slow tolerance is per species. atol_i = 1e-7 Pa * V / (R T) is
# one tenth of the 1e-6 Pa partial floor, in moles. A test on the total
# pressure is not the acceptance test: two partials can cancel there.

_ADMISSION_EXP_UNDERFLOW = 745.0
_SIGN_SCAN_POINTS = 4096
# Absolute partial tolerance imposed on every species. The solver's
# local test is rtol * |n_i| + atol_i, with atol_i this pressure times
# V / (R T). rtol is 1e-12. On the stranded N2 trace the frozen-initial
# decay rate already sits about 8.5e-7 Pa from the true varying-a drain,
# and rtol 1e-10 leaves another ~2e-7 Pa, which exceeds the 1e-6 Pa
# floor. 1e-12 keeps the solver's own gap under 1e-8 Pa. A multistep
# BDF at 1e-12 is still ~1e-4 Pa high on the choked megapascal piece.
_SLOW_ATOL_PA = 1.0e-7
_SLOW_RTOL = 1.0e-12


class _BalanceRefused(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class _HeadspaceStep:
    moles: dict[str, float]
    partials: dict[str, float]
    flux_kg_hr: dict[str, float]
    bled_mol: dict[str, float]
    bled_kg: float
    source_kg: float
    mass_end_kg: float
    mass_start_kg: float
    pressure_Pa: float
    iterations: int
    binding: str
    residual_Pa: float


def _ideal_gas_partials_Pa(
    moles: Mapping[str, float],
    *,
    temperature_K: float,
    volume_m3: float,
) -> dict[str, float]:
    # Premise: isothermal ideal gas. The inventory is the partial.
    # Algebra: p_i = n_i * R * T / V. R is GAS_CONSTANT, the full
    # CODATA value this module already imports. The 6-significant-figure
    # reader misses the 25.695650006 Pa pin by about 8e-6 Pa.
    # Unit check: mol * J/(mol·K) * K / m^3 = Pa.
    # Limit: n_i = 0 gives p_i = 0. The commanded millibar is not added.
    factor = GAS_CONSTANT * temperature_K / volume_m3
    return {species: moles[species] * factor for species in moles}


def _as_finite(value: object, reason: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise _BalanceRefused(reason) from exc
    if not math.isfinite(number):
        raise _BalanceRefused(reason)
    return number


class _HeadspaceLaw:
    """Trial-pressure source and outflow for one headspace step.

    S_redox is a molar rate (mol/s). A partition of the hour passes the
    hour's rate N/3600 s into every substep, including a halved
    substep. N/dt_substep would put the substep length into G and move
    the fast root. The callback, when present, is that hour rate and
    is not rescaled by the substep. A missing callback leaves the
    constant map, which chunk 2 passes as zeros.
    """

    def __init__(
        self,
        *,
        species: tuple[str, ...],
        masses: Mapping[str, float],
        flux: Callable[[Mapping[str, float]], Mapping[str, float]],
        temperature_K: float,
        volume_m3: float,
        downstream_pa: float,
        diameter_m: float | None,
        length_m: float | None,
        cap_kg_s: float | None,
        redox_mol_s: Mapping[str, float],
        redox_mol_s_at_partials: (
            Callable[[Mapping[str, float]], Mapping[str, float]] | None
        ) = None,
    ) -> None:
        self.species = species
        self.masses = dict(masses)
        self.flux = flux
        self.temperature_K = temperature_K
        self.volume_m3 = volume_m3
        self.downstream_pa = downstream_pa
        self.diameter_m = diameter_m
        self.length_m = length_m
        self.cap_kg_s = cap_kg_s
        self.redox_mol_s = {
            species_name: float(redox_mol_s.get(species_name, 0.0))
            for species_name in species
        }
        self.redox_mol_s_at_partials = redox_mol_s_at_partials
        # First notice per (species, reason). The sweep calls the pipe
        # on every trial; the hour keeps one flag, not one per sample.
        self.species_notices: dict[tuple[str, str], Mapping[str, Any]] = {}

    def _remember_species_notices(
        self,
        notices: tuple[Mapping[str, Any], ...] | tuple,
    ) -> None:
        for notice in notices:
            species = str(notice.get("species", ""))
            reason = str(notice.get("reason", ""))
            key = (species, reason)
            if key not in self.species_notices:
                self.species_notices[key] = dict(notice)

    def _redox_rates(self, partials: Mapping[str, float]) -> dict[str, float]:
        # Membership stays on the constant map built with the species
        # set. The callback is the hour rate N/3600 s. It is not divided
        # by the substep. A missing callback returns that constant map,
        # so a chunk-2 zero is unchanged.
        if self.redox_mol_s_at_partials is None:
            return self.redox_mol_s
        try:
            raw = self.redox_mol_s_at_partials(partials)
        except _BalanceRefused:
            raise
        except Exception as exc:
            raise _BalanceRefused(
                f"redox_source_refused:{type(exc).__name__}:{exc}"
            ) from exc
        if not isinstance(raw, Mapping):
            raise _BalanceRefused("redox_source_not_a_mapping")
        out = dict(self.redox_mol_s)
        for name, rate in raw.items():
            label = str(name)
            if label not in self.masses or label not in self.redox_mol_s:
                raise _BalanceRefused(f"missing_molar_mass:{label}")
            out[label] = _as_finite(rate, f"redox_source_not_finite:{label}")
        return out

    def partials_of(self, moles: Mapping[str, float]) -> dict[str, float]:
        return _ideal_gas_partials_Pa(
            moles,
            temperature_K=self.temperature_K,
            volume_m3=self.volume_m3,
        )

    def pressure_of(self, moles: Mapping[str, float]) -> float:
        return math.fsum(self.partials_of(moles).values())

    def moles_at_pressure(self, pressure_pa: float) -> dict[str, float]:
        # One species carries the whole pressure. Several species use
        # the sweep's own mole vector, not this inversion.
        species = self.species[0]
        if pressure_pa <= 0.0:
            return {species: 0.0}
        # n = p * V / (R T). Unit check: Pa * m^3 / (J/(mol·K) * K) = mol.
        moles = (
            pressure_pa
            * self.volume_m3
            / (GAS_CONSTANT * self.temperature_K)
        )
        return {species: moles}

    def _flux_kg_hr(self, partials: Mapping[str, float]) -> dict[str, float]:
        from simulator.evaporation import EvaporationFluxRefusal

        try:
            raw = self.flux(partials)
        except EvaporationFluxRefusal:
            raise
        except _BalanceRefused:
            raise
        except Exception as exc:
            raise _BalanceRefused(
                f"evaporation_flux_refused:{type(exc).__name__}:{exc}"
            ) from exc
        out: dict[str, float] = {}
        for name, rate in raw.items():
            if name not in self.masses or name not in self.redox_mol_s:
                raise _BalanceRefused(f"missing_molar_mass:{name}")
            number = _as_finite(rate, f"evaporation_flux_not_finite:{name}")
            # Chunk 4 removes the one-sided clamp. This chunk still
            # publishes max(0, rate).
            out[str(name)] = max(0.0, number)
        return out

    def _source_mol_s(self, partials: Mapping[str, float]) -> dict[str, float]:
        # kg/h / (kg/mol) / (s/h) = mol/s. 3600 is exact in binary64.
        # Limit: a non-positive published rate contributes no moles.
        flux = self._flux_kg_hr(partials)
        return {
            species: flux.get(species, 0.0) / self.masses[species] / 3600.0
            for species in self.species
        }

    def _flows(
        self,
        partials: Mapping[str, float],
    ) -> tuple[dict[str, float], dict[str, float], str, tuple]:
        source = self._source_mol_s(partials)
        pressure = math.fsum(partials[species] for species in self.species)
        moles = {
            species: partials[species]
            * self.volume_m3
            / (GAS_CONSTANT * self.temperature_K)
            for species in self.species
        }
        total_mol = math.fsum(max(0.0, moles[species]) for species in self.species)
        pipe_mol = {species: 0.0 for species in self.species}
        binding = "pipe"
        blend = 0.0
        if (
            self.diameter_m is not None
            and self.length_m is not None
            and total_mol > 0.0
            and pressure > 0.0
        ):
            holdup = {
                species: moles[species]
                for species in self.species
                if moles[species] > 0.0
            }
            try:
                outflow = pipe_species_outflow_mol_s(
                    pressure,
                    self.downstream_pa,
                    self.temperature_K,
                    holdup,
                    self.diameter_m,
                    self.length_m,
                    molar_mass_kg_mol=self.masses,
                )
            except TransportRegimeRefusal as exc:
                raise _BalanceRefused(str(exc)) from exc
            # The pipe flags an uncertified diameter and still returns
            # the flow. A missing mass stays a typed refusal above.
            self._remember_species_notices(outflow.species_notices)
            pipe_mol = {
                species: float(outflow.species_mol_s.get(species, 0.0))
                for species in self.species
            }
            binding = outflow.binding_cause
            blend = float(outflow.blend_weight)
        mass_kg_s = math.fsum(
            pipe_mol[species] * self.masses[species] for species in self.species
        )
        capped = False
        outflow_mol = pipe_mol
        if self.cap_kg_s is not None and self.diameter_m is None:
            # Constant cap, no duct. Q is that mass flow for any n > 0
            # and zero at an empty holdup, so one backward-Euler step of
            # a cap below the source is exact.
            if total_mol <= 0.0 or pressure <= 0.0 or self.cap_kg_s == 0.0:
                outflow_mol = {species: 0.0 for species in self.species}
            elif len(self.species) == 1:
                species = self.species[0]
                outflow_mol = {species: self.cap_kg_s / self.masses[species]}
            else:
                outflow_mol = {
                    species: self.cap_kg_s
                    * (moles[species] / total_mol)
                    / self.masses[species]
                    for species in self.species
                }
            binding = (
                "line_closed" if self.cap_kg_s == 0.0 else "controlled_o2_equipment"
            )
            capped = self.cap_kg_s > 0.0
            blend = 0.0
        elif self.cap_kg_s is not None and mass_kg_s > self.cap_kg_s:
            scale = 0.0 if mass_kg_s <= 0.0 else self.cap_kg_s / mass_kg_s
            outflow_mol = {
                species: pipe_mol[species] * scale for species in self.species
            }
            capped = True
            binding = "controlled_o2_equipment"
        if blend <= 0.0:
            bucket = 0
        elif blend >= 1.0:
            bucket = 2
        else:
            bucket = 1
        # The floor flag is the n_i = 0 kink. A difference that steps
        # off zero is not a derivative of the smooth piece.
        piece = (binding, bucket, capped, pressure <= 0.0)
        return source, outflow_mol, binding, piece

    def _scalar_g(self, pressure_pa: float) -> float:
        # G = q_head + S - Q. n0 and Δt are not arguments.
        # Unit check: each term is mol/s. Limit: Q = 0 and q + S > 0
        # leaves G positive, so a closed line has no fast root.
        moles = self.moles_at_pressure(pressure_pa)
        partials = self.partials_of(moles)
        source, outflow, _binding, _piece = self._flows(partials)
        species = self.species[0]
        return (
            source[species]
            + self._redox_rates(partials)[species]
            - outflow[species]
        )

    def _scalar_residual(
        self,
        n0: Mapping[str, float],
        dt_s: float,
        pressure_pa: float,
    ) -> float:
        # Premise: backward Euler, end-state rates over the whole substep.
        # Algebra: R = n - n0 - Δt (q + S - Q).
        # Unit check: (mol/s) * s = mol.
        # Limit: Q = 0 and constant q gives n = n0 + (q + S) Δt.
        moles = self.moles_at_pressure(pressure_pa)
        partials = self.partials_of(moles)
        source, outflow, _binding, _piece = self._flows(partials)
        species = self.species[0]
        return (
            moles[species]
            - n0[species]
            - dt_s
            * (
                source[species]
                + self._redox_rates(partials)[species]
                - outflow[species]
            )
        )

    def g_samples(self) -> list[tuple[float, float]]:
        return _pressure_sign_scan(self.downstream_pa, self._scalar_g)

    def piece_at(self, pressure_pa: float) -> tuple:
        _source, _outflow, _binding, piece = self._flows(
            self.partials_of(self.moles_at_pressure(max(pressure_pa, 0.0)))
        )
        return piece

    def outflow_kg_s(self, pressure_pa: float) -> tuple[float, tuple]:
        _source, outflow, _binding, piece = self._flows(
            self.partials_of(self.moles_at_pressure(max(pressure_pa, 0.0)))
        )
        mass = math.fsum(
            outflow[species] * self.masses[species] for species in self.species
        )
        return mass, piece

    def a_min(self, pressure_a: float, pressure_b: float) -> float:
        # a = dQ/dm. For one ideal-gas species dm = dP * M * V / (R T),
        # so a = (dQ_kg/dP) * R * T / (M * V).
        # Unit check: (kg/s/Pa) * (J/(mol·K)) * K / ((kg/mol) * m^3)
        # = 1/s. A flat sample has dQ/dP = 0, so a_min = 0 and the
        # step does not admit, even when another sample on the same
        # interval is steep. A pipe below P_down is that flat piece.
        # Skipping the zero and keeping the steep sample would admit
        # a fast root the holdup has not reached. A stencil that
        # changes piece (sonic kink, blend knot, cap, or the
        # empty-holdup floor) is not a measured slope and is skipped.
        # The low-pressure end, where this pipe's positive slope is
        # smallest, stays in the grid.
        lo = min(pressure_a, pressure_b)
        hi = max(pressure_a, pressure_b)
        start = lo if lo > 0.0 else max(hi * 1.0e-8, 1.0e-8)
        points = [start, hi]
        if hi > start * (1.0 + 1.0e-12):
            log_lo = math.log(start)
            log_hi = math.log(hi)
            points.extend(
                math.exp(log_lo + (log_hi - log_lo) * index / 64.0)
                for index in range(1, 64)
            )
        species = self.species[0]
        scale = (
            GAS_CONSTANT
            * self.temperature_K
            / (self.masses[species] * self.volume_m3)
        )
        slopes: list[float] = []
        for pressure in points:
            slope = self._dq_kg_dp(pressure)
            if slope is None or not math.isfinite(slope):
                continue
            if slope <= 0.0:
                return 0.0
            slopes.append(slope * scale)
        if not slopes:
            return 0.0
        return min(slopes)

    def _dq_kg_dp(self, pressure_pa: float) -> float | None:
        if pressure_pa <= 0.0:
            return None
        # Central relative 1e-6, the same step the design probe used
        # for a(P). One-sided when the other side would leave the piece.
        step = max(abs(pressure_pa) * 1.0e-6, 1.0e-9)
        mass_0, piece_0 = self.outflow_kg_s(pressure_pa)
        mass_up: float | None = None
        mass_down: float | None = None
        if pressure_pa + step > 0.0:
            mass, piece = self.outflow_kg_s(pressure_pa + step)
            if piece == piece_0:
                mass_up = mass
        if pressure_pa - step > 0.0:
            mass, piece = self.outflow_kg_s(pressure_pa - step)
            if piece == piece_0:
                mass_down = mass
        if mass_up is not None and mass_down is not None:
            return (mass_up - mass_down) / (2.0 * step)
        if mass_up is not None:
            return (mass_up - mass_0) / step
        if mass_down is not None:
            return (mass_0 - mass_down) / step
        return None

    def step_from_moles(
        self,
        n0: Mapping[str, float],
        dt_s: float,
        moles: Mapping[str, float],
        iterations: int,
        residual_Pa: float,
        *,
        added_mol: Mapping[str, float] | None = None,
        bled_mol_in: Mapping[str, float] | None = None,
    ) -> _HeadspaceStep:
        partials = self.partials_of(moles)
        source, outflow, binding, _piece = self._flows(partials)
        flux = self._flux_kg_hr(partials)
        mass_start = math.fsum(
            n0[species] * self.masses[species] for species in self.species
        )
        mass_end = math.fsum(
            moles[species] * self.masses[species] for species in self.species
        )
        if added_mol is None:
            # Fast root. m_source = (q + S) * M * Δt. At the root
            # q + S = Q, so the bled mass is the hour's source plus
            # the drained holdup. This is not the slow branch: a
            # source that changes inside the step is not q(end)·Δt.
            # Unit check: mol/s * kg/mol * s = kg.
            # Limit: already at the root, m0 = m_end and bled = (q + S) Δt.
            redox = self._redox_rates(partials)
            source_kg = math.fsum(
                (source[species] + redox[species])
                * self.masses[species]
                * dt_s
                for species in self.species
            )
            bled_mol = {
                species: n0[species]
                + (source[species] + redox[species]) * dt_s
                - moles[species]
                for species in self.species
            }
        else:
            # Slow branch. added_mol is A_i(Δt) = ∫ (q_i + S_i) dt and
            # bled_mol_in is B_i(Δt) = ∫ Q_i dt, both in mol. The caller
            # published n_end = n0 + A − B, so this booking is that
            # identity. Unit check: mol * kg/mol = kg.
            # Limit: constant q and Q = 0 gives A = (q + S) Δt and B = 0.
            source_kg = math.fsum(
                added_mol[species] * self.masses[species]
                for species in self.species
            )
            bled_mol = {
                species: bled_mol_in[species] for species in self.species
            }
        bled_kg = math.fsum(
            bled_mol[species] * self.masses[species] for species in self.species
        )
        return _HeadspaceStep(
            moles=dict(moles),
            partials=partials,
            flux_kg_hr=flux,
            bled_mol=bled_mol,
            bled_kg=bled_kg,
            source_kg=source_kg,
            mass_end_kg=mass_end,
            mass_start_kg=mass_start,
            pressure_Pa=math.fsum(partials.values()),
            iterations=iterations,
            binding=binding,
            residual_Pa=residual_Pa,
        )


def _log_pressure_samples(
    lo: float,
    hi: float,
    count: int,
    value_at: Callable[[float], float],
) -> list[tuple[float, float]]:
    """Log-spaced samples on [lo, hi]. Stop once two sign changes are in.

    The one-species grid and the mixture ray both used this spacing,
    the same two-crossing stop, and the same 1e12 Pa extension. One
    edit has to move both, or a second root past 1e8 Pa is a refusal
    on one path and a fast commit on the other.
    """

    log_lo = math.log(lo)
    log_hi = math.log(hi)
    samples: list[tuple[float, float]] = []
    crossings = 0
    previous: float | None = None
    for index in range(count):
        pressure = math.exp(log_lo + (log_hi - log_lo) * index / (count - 1))
        value = value_at(pressure)
        if not math.isfinite(value):
            raise _BalanceRefused("headspace_balance_nonconvergence")
        if previous is not None and previous * value < 0.0:
            crossings += 1
        if value == 0.0 and previous not in (None, 0.0):
            crossings += 1
        previous = value
        samples.append((pressure, value))
        if crossings >= 2:
            break
    return samples


def _pressure_sign_scan(
    downstream_pa: float,
    value_at: Callable[[float], float],
) -> list[tuple[float, float]]:
    """Sign samples of one scalar from vacuum or P_down up to 1e12 Pa.

    Floor is P_down, or 1e-8 Pa when the downstream pressure is 0, then
    a 4096-point log grid through 1e8 Pa. A still-positive end extends
    the same grid to 1e12 Pa. The explicit 0 Pa sample is only for a
    vacuum downstream, where P = 0 is the closed end of the pipe.
    """

    lo = downstream_pa if downstream_pa > 0.0 else 1.0e-8
    hi = max(1.0e8, lo * 10.0)
    samples: list[tuple[float, float]] = []
    if downstream_pa <= 0.0:
        samples.append((0.0, value_at(0.0)))
    samples.extend(_log_pressure_samples(lo, hi, _SIGN_SCAN_POINTS, value_at))
    if samples[-1][1] > 0.0:
        samples.extend(_log_pressure_samples(hi, 1.0e12, 1024, value_at)[1:])
    return samples


def _crossings(
    samples: list[tuple[float, float]],
) -> list[tuple[str, float, float]]:
    """One entry per sign change. A run of exact zeros is not a run of roots.

    product < 0 is a bracket. An isolated 0 with a nonzero neighbor is
    one exact root. A function that is 0 everywhere has no nonzero
    neighbor, so it is not a sign change: the slow branch then keeps
    a closed, sourceless holdup where it is.
    """

    found: list[tuple[str, float, float]] = []
    count = len(samples)
    for index, (pressure, value) in enumerate(samples):
        if value == 0.0:
            previous = samples[index - 1][1] if index else None
            nxt = samples[index + 1][1] if index + 1 < count else None
            neighbors = [item for item in (previous, nxt) if item is not None]
            if any(item != 0.0 for item in neighbors) and previous != 0.0:
                found.append(("exact", pressure, pressure))
            continue
        if index == 0:
            continue
        previous_p, previous_g = samples[index - 1]
        if previous_g == 0.0:
            continue
        if previous_g * value < 0.0:
            found.append(("bracket", previous_p, pressure))
    return found


def _newton_propose(
    func: Callable[[float], float],
    piece_at: Callable[[float], tuple],
    guess: float,
    lo: float,
    hi: float,
) -> float | None:
    """Damped Newton on one smooth piece. The caller discards the return.

    A step that leaves the piece (sonic kink, blend knot, cold-train
    cap, or n = 0) is rejected and shortened. Backtracking that only
    asks the residual to fall can stall on a kink; stalling returns
    None and is not a commit. The first guess may be the pressure of
    n0, clamped into the bracket. It is not the commanded pressure.
    """

    if not hi > lo:
        return None
    trial_x = min(max(guess, lo), hi)
    for _iteration in range(DEFAULT_MAX_ITERATIONS):
        value = func(trial_x)
        if value == 0.0:
            return trial_x
        piece = piece_at(trial_x)
        step_h = max(abs(trial_x) * 1.0e-6, 1.0e-9)
        right: float | None = None
        left: float | None = None
        if trial_x + step_h <= hi and piece_at(trial_x + step_h) == piece:
            right = func(trial_x + step_h)
        if trial_x - step_h >= lo and piece_at(trial_x - step_h) == piece:
            left = func(trial_x - step_h)
        if right is None and left is None:
            return None
        if right is not None and left is not None:
            derivative = (right - left) / (2.0 * step_h)
        elif right is not None:
            derivative = (right - value) / step_h
        else:
            derivative = (value - left) / step_h  # type: ignore[operator]
        if derivative == 0.0 or not math.isfinite(derivative):
            return None
        step = -value / derivative
        if not math.isfinite(step):
            return None
        accepted = False
        taken = step
        for _back in range(8):
            candidate = trial_x + step
            if lo <= candidate <= hi and piece_at(candidate) == piece:
                trial_value = func(candidate)
                if abs(trial_value) < abs(value):
                    trial_x = candidate
                    taken = step
                    accepted = True
                    break
            step *= 0.5
        if not accepted:
            return None
        if abs(taken) <= max(DEFAULT_ABS_TOL_PA, DEFAULT_REL_TOL * abs(trial_x)):
            return trial_x
    return None


def _brent(func: Callable[[float], float], lo: float, hi: float) -> tuple[float, int]:
    """Publish the root. An endpoint that is already 0 is that root.

    brentq needs a strict sign change, so an exact zero is returned
    before the call. xtol 1e-12 Pa and rtol 1e-14 sit under the
    shadow's 1e-6 Pa pressure tolerance.
    """

    if hi < lo:
        lo, hi = hi, lo
    low = func(lo)
    high = func(hi)
    if low == 0.0:
        return lo, 1
    if high == 0.0:
        return hi, 1
    if low * high > 0.0 or not hi > lo:
        raise _BalanceRefused("headspace_balance_nonconvergence")
    from scipy.optimize import brentq

    try:
        root, info = brentq(
            func,
            lo,
            hi,
            xtol=1.0e-12,
            rtol=1.0e-14,
            maxiter=200,
            full_output=True,
            disp=False,
        )
    except ValueError as exc:
        raise _BalanceRefused("headspace_balance_nonconvergence") from exc
    return float(root), max(int(info.iterations), 1)


def _solve_bracket(
    func: Callable[[float], float],
    piece_at: Callable[[float], tuple],
    crossing: tuple[str, float, float],
    guess: float,
) -> tuple[float, int]:
    # Newton first, including when it converges. Brent then publishes.
    # The last Newton iterate is not the commit.
    lo, hi = crossing[1], crossing[2]
    _newton_propose(func, piece_at, guess, min(lo, hi), max(lo, hi))
    if crossing[0] == "exact":
        return crossing[1], 1
    return _brent(func, lo, hi)


def _admits(law: _HeadspaceLaw, p0: float, p_root: float, dt_s: float) -> bool:
    # Premise: a linear drain dm/dt = -a (m - m_root) leaves a gap
    # |P(m0) - P_root| * exp(-a_min Δt). Admit when that gap is at
    # most 1e-6 Pa. exp underflows to 0 in binary64 once a Δt is
    # about 745 or more, and that underflow admits.
    # Limit: a gap already inside 1e-6 Pa admits with a_min unused.
    # A non-positive a_min (flat cap) does not admit.
    gap = abs(p0 - p_root)
    if gap <= DEFAULT_ABS_TOL_PA:
        return True
    a_min = law.a_min(p0, p_root)
    if a_min <= 0.0:
        return False
    product = a_min * dt_s
    if product >= _ADMISSION_EXP_UNDERFLOW:
        return True
    factor = math.exp(-product)
    if factor == 0.0:
        return True
    return gap * factor <= DEFAULT_ABS_TOL_PA


def _one_euler(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
) -> _HeadspaceStep:
    """One backward-Euler step. Never the fast root of G."""

    if len(law.species) == 1:
        return _euler_one(law, n0, dt_s)
    return _euler_many(law, n0, dt_s, DEFAULT_REL_TOL, DEFAULT_MAX_ITERATIONS)


def _euler_one(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
) -> _HeadspaceStep:
    species = law.species[0]
    # No reverse flow. §1 sets the outflow to 0 when P_up <= P_down, so
    # a holdup already below downstream stays there and the root of R
    # is outside a bracket that opens at P_down. Vacuum downstream is
    # already 0. A holdup above P_down keeps that floor.
    if law.pressure_of(n0) <= law.downstream_pa:
        p_lo = 0.0
    else:
        p_lo = law.downstream_pa if law.downstream_pa > 0.0 else 0.0
    zero = law.partials_of(law.moles_at_pressure(0.0))
    source0, _outflow0, _binding0, _piece0 = law._flows(zero)
    n_hi = n0[species] + max(
        0.0,
        source0[species] + law._redox_rates(zero)[species],
    ) * dt_s
    p_hi = n_hi * GAS_CONSTANT * law.temperature_K / law.volume_m3
    if p_hi <= p_lo:
        p_hi = p_lo + max(DEFAULT_ABS_TOL_PA, abs(p_lo))

    def residual(pressure: float) -> float:
        return law._scalar_residual(n0, dt_s, pressure)

    low = residual(p_lo)
    high = residual(p_hi)
    expands = 0
    while low * high > 0.0 and expands < 40:
        p_hi = p_hi * 2.0 if p_hi > 0.0 else 1.0
        high = residual(p_hi)
        expands += 1
    if low * high > 0.0 and low != 0.0 and high != 0.0:
        raise _BalanceRefused("headspace_balance_nonconvergence")
    guess = law.pressure_of(n0)
    # Discarded. Brent, or an endpoint that is already 0, publishes.
    _newton_propose(residual, law.piece_at, guess, p_lo, p_hi)
    if low == 0.0:
        root, iterations = p_lo, 1
    elif high == 0.0:
        root, iterations = p_hi, 1
    else:
        root, iterations = _brent(residual, p_lo, p_hi)
    moles = law.moles_at_pressure(root)
    residual_pa = abs(residual(root)) * (
        GAS_CONSTANT * law.temperature_K / law.volume_m3
    )
    return law.step_from_moles(n0, dt_s, moles, iterations, residual_pa)


def _slow_converge(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
) -> _HeadspaceStep:
    """Integrate dn_i/dt = q_i + S_i - Q_i over dt_s.

    Radau IIA, order 5, L-stable, one step. A rejected step drops its
    stage history, so a regime kink (sonic edge, blend knot) does not
    leave a multistep stencil straddling the jump. BDF at rtol 1e-12
    is still ~1e-4 Pa high on the choked piece at megapascal pressure,
    because that tolerance times the state is larger than the 1e-6 Pa
    floor and the multistep does not beat it there. Radau at rtol 1e-12
    does. The achieved gap is the per-species partial, not the total.

    The Jacobian is a difference that stays on the current pipe piece.
    The mixture mean free path is continuous in mole fraction, so a
    step across equal moles is not a kink. A probe that leaves the
    piece shrinks until it does not, then takes the one-sided
    difference. Error control crosses a sonic edge or a blend knot;
    this difference does not have to.

    Premise: the booked bleed is the outflow the step removed. A source
    that changes inside the step is not the end rate times the whole
    step. Algebra, one extra state per species for each integral:
      dA_i/dt = q_i + S_i
      dB_i/dt = Q_i
      dn_i/dt = dA_i/dt − dB_i/dt
    so n_i(dt) = n0_i + A_i(dt) − B_i(dt) and the bled moles are
    B_i(dt). That identity is what is published, so the closure is
    exact in the booked moles, not only when q is constant.
    atol on A and on B is the same per-species mole tolerance as n.
    Unit check: (mol/s)·s = mol, and Pa·m³/(J/(mol·K)·K) = mol.
    Limit: Q = 0 and constant q gives A = (q + S) dt, B = 0, and
    n = n0 + A, a degree-1 polynomial this order integrates in one step.
    """

    import numpy as np
    from scipy.integrate import solve_ivp

    species = law.species
    count = len(species)
    # State is n, then A = ∫(q+S) dt, then B = ∫Q dt. A(0) = B(0) = 0.
    y0 = (
        [float(n0[name]) for name in species]
        + [0.0] * count
        + [0.0] * count
    )
    pa_per_mol = GAS_CONSTANT * law.temperature_K / law.volume_m3
    # Premise: each partial is held to 1e-7 Pa. Algebra: n = p V / (R T),
    # so the mole tolerance is that pressure times V / (R T). A and B
    # are moles, so they take that same tolerance.
    atol_mol = _SLOW_ATOL_PA / pa_per_mol

    def moles_of(state) -> dict[str, float]:
        # A stage may step a draining inventory slightly negative.
        # The pipe law is defined on n >= 0. The end state is checked
        # against the partial floor before that clamp is published.
        # A and B are integrals, not an inventory.
        return {
            name: (float(state[index]) if float(state[index]) > 0.0 else 0.0)
            for index, name in enumerate(species)
        }

    def rates_of(
        moles: Mapping[str, float],
    ) -> tuple[list[float], list[float], list[float], tuple]:
        partials = law.partials_of(moles)
        source, outflow, _binding, piece = law._flows(partials)
        redox = law._redox_rates(partials)
        added: list[float] = []
        bled: list[float] = []
        net: list[float] = []
        for name in species:
            inflow = source[name] + redox[name]
            removed = outflow[name]
            rate = inflow - removed
            if not (
                math.isfinite(inflow)
                and math.isfinite(removed)
                and math.isfinite(rate)
            ):
                raise _BalanceRefused("headspace_balance_nonconvergence")
            added.append(inflow)
            bled.append(removed)
            net.append(rate)
        # Binding, blend bucket, cap. The empty-holdup flag is not a
        # kink a fill is forbidden to leave. Mole fraction is not a
        # piece: the mean free path is continuous across equal moles.
        return net, added, bled, piece[:3]

    def rhs(_time: float, state) -> list[float]:
        net, added, bled, _piece = rates_of(moles_of(state))
        return net + added + bled

    def jacobian(_time: float, state):
        # |dn| starts at |n| * 1e-8 and shrinks until the sample stays
        # on this piece. A column of zeros is not the fallback: that
        # is the flat update a crossed stencil returned on the
        # free-molecular piece. A and B do not enter the rates, so
        # their columns stay zero.
        moles = moles_of(state)
        base_net, base_added, base_bled, piece = rates_of(moles)
        width_count = 3 * count
        matrix = np.zeros((width_count, width_count))

        def column_of(up, down, span: float) -> list[float]:
            # up and down are (net, added, bled). One sample difference
            # keeps ∂n/∂n_j = ∂A/∂n_j − ∂B/∂n_j.
            return [
                (up[block][index] - down[block][index]) / span
                for block in range(3)
                for index in range(count)
            ]

        for column, name in enumerate(species):
            amount = moles[name]
            width = 1.0e-14 if amount == 0.0 else max(abs(amount) * 1.0e-8, 1.0e-18)
            chosen: list[float] | None = None
            for _shrink in range(24):
                up_moles = dict(moles)
                up_moles[name] = amount + width
                up = rates_of(up_moles)
                up_ok = up[3] == piece
                down = None
                down_ok = False
                if amount > width:
                    down_moles = dict(moles)
                    down_moles[name] = amount - width
                    down = rates_of(down_moles)
                    down_ok = down[3] == piece
                if up_ok and down_ok and down is not None:
                    chosen = column_of(up, down, 2.0 * width)
                    break
                if up_ok:
                    chosen = column_of(
                        up,
                        (base_net, base_added, base_bled, piece),
                        width,
                    )
                    break
                if down_ok and down is not None:
                    chosen = column_of(
                        (base_net, base_added, base_bled, piece),
                        down,
                        width,
                    )
                    break
                next_width = width * 0.1
                if next_width < 1.0e-18:
                    chosen = column_of(
                        up,
                        (base_net, base_added, base_bled, piece),
                        width,
                    )
                    break
                width = next_width
            if chosen is None:
                raise _BalanceRefused("headspace_balance_nonconvergence")
            for row in range(width_count):
                matrix[row, column] = chosen[row]
        return matrix

    try:
        solution = solve_ivp(
            rhs,
            (0.0, float(dt_s)),
            y0,
            method="Radau",
            rtol=_SLOW_RTOL,
            atol=atol_mol,
            jac=jacobian,
        )
    except _BalanceRefused:
        raise
    if not solution.success:
        raise _BalanceRefused("headspace_balance_nonconvergence")
    y_end = solution.y[:, -1]
    for value in y_end[:count]:
        amount = float(value)
        if not math.isfinite(amount):
            raise _BalanceRefused("headspace_balance_nonconvergence")
        # The integrated inventory may undershoot. A partial below
        # -1e-6 Pa is not the tolerance. The published moles are the
        # closure identity below, not this component.
        if amount < 0.0 and -amount * pa_per_mol > DEFAULT_ABS_TOL_PA:
            raise _BalanceRefused("headspace_balance_nonconvergence")
    added_mol: dict[str, float] = {}
    bled_mol: dict[str, float] = {}
    end: dict[str, float] = {}
    for index, name in enumerate(species):
        added = float(y_end[count + index])
        bled = float(y_end[2 * count + index])
        if not math.isfinite(added) or not math.isfinite(bled):
            raise _BalanceRefused("headspace_balance_nonconvergence")
        # Q is nonnegative. An undershoot of B inside the partial
        # floor is the tolerance. Past that floor the integral failed.
        if bled < 0.0:
            if -bled * pa_per_mol > DEFAULT_ABS_TOL_PA:
                raise _BalanceRefused("headspace_balance_nonconvergence")
            bled = 0.0
        # n_end = n0 + A − B. Publishing this, and booking B, is the
        # closure. A reconstructed partial below -1e-6 Pa is a refusal.
        # Inside the floor the inventory is empty and the bleed is the
        # holdup plus the source, which keeps the same identity.
        amount = float(n0[name]) + added - bled
        if amount < 0.0:
            if -amount * pa_per_mol > DEFAULT_ABS_TOL_PA:
                raise _BalanceRefused("headspace_balance_nonconvergence")
            amount = 0.0
            bled = float(n0[name]) + added
        added_mol[name] = added
        bled_mol[name] = bled
        end[name] = amount
    return law.step_from_moles(
        n0,
        dt_s,
        end,
        max(int(solution.nfev), 1),
        _SLOW_ATOL_PA,
        added_mol=added_mol,
        bled_mol_in=bled_mol,
    )


def _commit_one(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
) -> _HeadspaceStep:
    samples = law.g_samples()
    crossings = _crossings(samples)
    if len(crossings) >= 2:
        raise _BalanceRefused("headspace_balance_multiple_roots")
    if len(crossings) == 1:
        root, iterations = _solve_bracket(
            law._scalar_g,
            law.piece_at,
            crossings[0],
            law.pressure_of(n0),
        )
        if _admits(law, law.pressure_of(n0), root, dt_s):
            moles = law.moles_at_pressure(root)
            # |G| * Δt * R T / V is the pressure that imbalance would
            # accumulate. At the published root it is under 1e-6 Pa.
            imbalance = abs(law._scalar_g(root)) * dt_s
            residual_pa = imbalance * (
                GAS_CONSTANT * law.temperature_K / law.volume_m3
            )
            return law.step_from_moles(
                n0,
                dt_s,
                moles,
                iterations,
                residual_pa,
            )
    return _slow_converge(law, n0, dt_s)


def _slice_value(
    law: _HeadspaceLaw,
    moles: Mapping[str, float],
    species: str,
    n_i: float,
    n0: Mapping[str, float],
    dt_s: float,
    mode: str,
) -> float:
    trial = dict(moles)
    trial[species] = n_i
    partials = law.partials_of(trial)
    source, outflow, _binding, _piece = law._flows(partials)
    redox = law._redox_rates(partials)[species]
    if mode == "fast":
        return source[species] + redox - outflow[species]
    return (
        n_i
        - n0[species]
        - dt_s * (source[species] + redox - outflow[species])
    )


def _solve_slice(
    law: _HeadspaceLaw,
    moles: Mapping[str, float],
    species: str,
    n0: Mapping[str, float],
    dt_s: float,
    mode: str,
) -> tuple[dict[str, float], float, int]:
    def func(n_i: float) -> float:
        return _slice_value(law, moles, species, n_i, n0, dt_s, mode)

    if mode == "slow":
        frozen = dict(moles)
        frozen[species] = 0.0
        frozen_partials = law.partials_of(frozen)
        source0, _outflow, _binding, _piece = law._flows(frozen_partials)
        n_hi = n0[species] + max(
            0.0,
            source0[species] + law._redox_rates(frozen_partials)[species],
        ) * dt_s
    else:
        n_hi = max(
            1.0e8 * law.volume_m3 / (GAS_CONSTANT * law.temperature_K),
            n0[species],
            moles[species],
            1.0e-12,
        )
    # Premise: the fast residual cannot tell two partials apart once
    # both sit inside DEFAULT_ABS_TOL_PA. A sign change whose upper
    # mole is at or below that floor is the empty root, not a second
    # physical root. An exact zero at n = 0 stays, so a sourceless
    # species still commits empty.
    # Algebra: n_floor = P_floor * V / (R T).
    # Unit check: Pa * m^3 / (J/(mol·K) * K) = mol.
    # Limit: a bracket above the floor still counts. Two of those
    # still refuse.
    n_floor = (
        DEFAULT_ABS_TOL_PA
        * law.volume_m3
        / (GAS_CONSTANT * law.temperature_K)
    )

    def roots_of(samples_so_far):
        kept = []
        for kind, lo, hi in _crossings(samples_so_far):
            if hi <= n_floor and not (kind == "exact" and lo == 0.0):
                continue
            kept.append((kind, lo, hi))
        return kept

    samples = [(0.0, func(0.0))]
    if n_hi > 0.0:
        log_lo = math.log(1.0e-16)
        log_hi = math.log(max(n_hi, 1.0e-16))
        for index in range(1, 257):
            amount = math.exp(log_lo + (log_hi - log_lo) * index / 256.0)
            samples.append((amount, func(amount)))
            if len(roots_of(samples)) >= 2:
                raise _BalanceRefused("headspace_balance_multiple_roots")
    crossings = roots_of(samples)
    if len(crossings) >= 2:
        raise _BalanceRefused("headspace_balance_multiple_roots")
    if len(crossings) == 0:
        incoming = float(moles[species])
        # Premise: P_up <= P_down closes the pipe, so Q is 0. A
        # species with q + S = 0 then has G = 0 at every mole that
        # leaves the total pressure on that closed side. That plateau
        # is the root. The scan samples are zero and then negative
        # once the pipe opens, and a run of zeros is not a sign
        # change, so the bracket search reports no root.
        # Algebra: n_end = n_incoming. With q + S = 0,
        # bled = n0 - n_end.
        # Unit check: both amounts are moles.
        # Limit: a positive source on the closed side has G = q > 0,
        # so this does not fire. A residual that is nonzero at the
        # incoming mole still falls through.
        if func(incoming) == 0.0:
            crossings = [("exact", incoming, incoming)]
    if len(crossings) == 0:
        # A sign change that lived entirely under the floor was
        # dropped above. The holdup it would publish is inside
        # 1e-6 Pa, so the empty root is that root. A scan whose
        # largest residual cannot accumulate that floor over the
        # hour is the same state. A residual that stays on one side
        # and can accumulate more than the floor has no root here.
        raw = _crossings(samples)
        under_floor = bool(raw) and all(
            hi <= n_floor for _kind, _lo, hi in raw
        )
        peak = max(abs(value) for _pressure, value in samples)
        if under_floor or peak * dt_s <= n_floor:
            crossings = [("exact", 0.0, 0.0)]
        else:
            raise _BalanceRefused("headspace_balance_nonconvergence")

    def piece_at(n_i: float) -> tuple:
        trial = dict(moles)
        trial[species] = max(n_i, 0.0)
        _source, _outflow, _binding, piece = law._flows(law.partials_of(trial))
        return piece

    root, iterations = _solve_bracket(
        func,
        piece_at,
        crossings[0],
        moles[species],
    )
    updated = dict(moles)
    old_partial = law.partials_of(moles)[species]
    updated[species] = root
    new_partial = law.partials_of(updated)[species]
    return updated, abs(new_partial - old_partial), iterations


def _sweep(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
    mode: str,
    *,
    rel_tol: float,
    max_iterations: int,
) -> tuple[dict[str, float], int, float]:
    moles = {species: max(0.0, n0[species]) for species in law.species}
    total_iterations = 0
    last_move = math.inf
    for _sweep_index in range(max_iterations):
        last_move = 0.0
        for species in law.species:
            moles, move, iterations = _solve_slice(
                law,
                moles,
                species,
                n0,
                dt_s,
                mode,
            )
            last_move = max(last_move, move)
            total_iterations += iterations
        scale = max(law.pressure_of(moles), 1.0)
        if last_move <= max(DEFAULT_ABS_TOL_PA, rel_tol * scale):
            return moles, max(total_iterations, 1), last_move
    raise _BalanceRefused("headspace_balance_nonconvergence")


def _euler_many(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
    rel_tol: float,
    max_iterations: int,
) -> _HeadspaceStep:
    moles, iterations, residual_pa = _sweep(
        law,
        n0,
        dt_s,
        "slow",
        rel_tol=rel_tol,
        max_iterations=max_iterations,
    )
    return law.step_from_moles(n0, dt_s, moles, iterations, residual_pa)


def _ray_samples(
    law: _HeadspaceLaw,
    fractions: Mapping[str, float],
) -> list[tuple[float, float]]:
    def total_g(pressure: float) -> float:
        if pressure <= 0.0:
            partials = {species: 0.0 for species in law.species}
        else:
            partials = {
                species: pressure * fractions[species] for species in law.species
            }
        source, outflow, _binding, _piece = law._flows(partials)
        redox = law._redox_rates(partials)
        return math.fsum(
            source[species] + redox[species] - outflow[species]
            for species in law.species
        )

    return _pressure_sign_scan(law.downstream_pa, total_g)


def _fractions_of(
    law: _HeadspaceLaw,
    moles: Mapping[str, float],
) -> dict[str, float] | None:
    total = math.fsum(max(0.0, moles[species]) for species in law.species)
    if total <= 0.0:
        return None
    return {
        species: max(0.0, moles[species]) / total for species in law.species
    }


def _state_slope(law: _HeadspaceLaw, moles: Mapping[str, float]) -> float | None:
    """dQ_kg/dm at fixed composition. None when the stencil crosses a kink.

    Algebra: a = (Q(m(1+ε)) - Q(m(1-ε))) / (m(1+ε) - m(1-ε)).
    Unit check: (kg/s) / kg = 1/s. This is the mixture form of a_min.
    The one-species commit uses the pressure form instead, which the
    stranded-hour pins already exercise. Limit: an empty holdup has
    no slope.
    """

    total = math.fsum(max(0.0, moles[species]) for species in law.species)
    if total <= 0.0:
        return None
    epsilon = 1.0e-6

    def evaluate(scale: float) -> tuple[float, float, tuple]:
        scaled = {
            species: max(0.0, moles[species]) * scale for species in law.species
        }
        _source, outflow, _binding, piece = law._flows(law.partials_of(scaled))
        mass = math.fsum(
            scaled[species] * law.masses[species] for species in law.species
        )
        flow = math.fsum(
            outflow[species] * law.masses[species] for species in law.species
        )
        return mass, flow, piece

    mass_0, _flow_0, piece_0 = evaluate(1.0)
    mass_up, flow_up, piece_up = evaluate(1.0 + epsilon)
    mass_down, flow_down, piece_down = evaluate(1.0 - epsilon)
    if piece_up != piece_0 or piece_down != piece_0:
        return None
    delta_mass = mass_up - mass_down
    if delta_mass <= 0.0 or mass_0 <= 0.0:
        return None
    slope = (flow_up - flow_down) / delta_mass
    if not math.isfinite(slope):
        return None
    # A flat piece is a measured 0, not a missing stencil. Admission
    # treats 0 as a_min = 0. A kink above still returns None.
    if slope <= 0.0:
        return 0.0
    return slope


def _admits_mixture(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    n_root: Mapping[str, float],
    dt_s: float,
) -> bool:
    # Same bound as the one-species test. a_min is the smallest
    # positive dQ/dm on the segment. A flat sample is 0 and does not
    # admit. A kink (None) is skipped. No measured positive slope does
    # not admit: the slow branch then publishes the backward-Euler state.
    gap = abs(law.pressure_of(n0) - law.pressure_of(n_root))
    if gap <= DEFAULT_ABS_TOL_PA:
        return True
    slopes: list[float] = []
    for weight in (0.0, 0.25, 0.5, 0.75, 1.0):
        state = {
            species: (1.0 - weight) * n0[species] + weight * n_root[species]
            for species in law.species
        }
        slope = _state_slope(law, state)
        if slope is None:
            continue
        if slope <= 0.0:
            return False
        slopes.append(slope)
    if not slopes:
        return False
    product = min(slopes) * dt_s
    if product >= _ADMISSION_EXP_UNDERFLOW:
        return True
    factor = math.exp(-product)
    if factor == 0.0:
        return True
    return gap * factor <= DEFAULT_ABS_TOL_PA


def _commit_many(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
    *,
    rel_tol: float,
    max_iterations: int,
) -> _HeadspaceStep:
    fractions = _fractions_of(law, n0)
    if fractions is None:
        zero = {species: 0.0 for species in law.species}
        zero_partials = law.partials_of(zero)
        source, _outflow, _binding, _piece = law._flows(zero_partials)
        redox = law._redox_rates(zero_partials)
        weights = {
            species: max(0.0, source[species] + redox[species])
            for species in law.species
        }
        total = math.fsum(weights.values())
        if total <= 0.0:
            # Premise: n0 = 0 and q + S <= 0 at the zero partial, so
            # G = q + S - Q is 0 at P = 0. The pipe flow Q is 0 there
            # because the holdup is empty. A composition ray needs a
            # positive weight, and this state has none.
            # Algebra: n_end = 0, bled = 0.
            # Unit check: 0 mol/s * s = 0 mol.
            # Limit: a positive source builds the ray below instead.
            return law.step_from_moles(n0, dt_s, zero, 1, 0.0)
        fractions = {species: weights[species] / total for species in law.species}
    crossings = _crossings(_ray_samples(law, fractions))
    if len(crossings) >= 2:
        raise _BalanceRefused("headspace_balance_multiple_roots")
    if len(crossings) == 1:
        # A sweep that misses its tolerance refuses. It does not publish
        # the backward-Euler state of the slow branch.
        moles, iterations, residual_pa = _sweep(
            law,
            n0,
            dt_s,
            "fast",
            rel_tol=rel_tol,
            max_iterations=max_iterations,
        )
        if _admits_mixture(law, n0, moles, dt_s):
            return law.step_from_moles(n0, dt_s, moles, iterations, residual_pa)
    return _slow_converge(law, n0, dt_s)


def _publish(
    law: _HeadspaceLaw,
    step: _HeadspaceStep,
    capacity: NoColdTrain | FiniteCapacity,
    vessel_rating_Pa: float | None,
    *,
    authoritative: bool = False,
) -> CapacityShadowResult | CapacityShadowRefusal:
    if (
        vessel_rating_Pa is not None
        and step.pressure_Pa > vessel_rating_Pa
    ):
        return CapacityShadowRefusal(
            "vessel_total_pressure_exceeds_rating:"
            f"{step.pressure_Pa:.17g}>{vessel_rating_Pa:.17g}",
            step.iterations,
        )
    evap_kg_hr = math.fsum(step.flux_kg_hr.values())
    oxygen_evap = step.flux_kg_hr.get(OXYGEN_SPECIES, 0.0)
    _source, outflow, _binding, _piece = law._flows(step.partials)
    pipe_kg_hr = (
        math.fsum(
            outflow[species] * law.masses[species] for species in law.species
        )
        * 3600.0
    )
    if pipe_kg_hr > 0.0:
        pipe_saturation = evap_kg_hr / pipe_kg_hr
    elif (
        evap_kg_hr > 0.0
        and step.pressure_Pa <= DEFAULT_ABS_TOL_PA
        and step.bled_kg > 0.0
    ):
        # Premise: the ramp stops at infinite saturation. End-state
        # pipe flow is 0 when the committed holdup is empty. A source
        # that was booked out (bled_kg > 0) at a pressure inside the
        # 1e-6 Pa floor did leave through the pipe. That is within
        # capacity, so the ratio is 1. The throttle starts above 100%.
        # Algebra: saturation = source / throughput = 1 at a cleared root.
        # Unit check: both masses are kilograms, so the ratio is 1.
        # Limit: a line that is idle only because P_up is still below
        # P_down is the next branch. A sealed end with pressure above
        # that downstream pressure still reports inf.
        pipe_saturation = 1.0
    elif (
        evap_kg_hr > 0.0
        and law.diameter_m is not None
        and step.pressure_Pa <= law.downstream_pa
    ):
        # Premise: Q is 0 because P_up <= P_down. The duct is idle,
        # not full. The hour's source is accumulating toward the
        # pressure that opens the pipe. Infinite saturation freezes
        # the ramp on that approach, so the inventory never gets there.
        # Algebra: saturation = 0 while the forward check is closed.
        # Unit check: the ratio is dimensionless.
        # Limit: P_up above P_down with Q still 0 is a sealed end,
        # and the branch below reports inf.
        pipe_saturation = 0.0
    else:
        pipe_saturation = math.inf if evap_kg_hr > 0.0 else 0.0
    oxygen_saturation = (
        oxygen_evap / capacity.value_kg_hr
        if isinstance(capacity, FiniteCapacity)
        else None
    )
    if oxygen_saturation is None or pipe_saturation >= oxygen_saturation:
        combined = pipe_saturation
    else:
        combined = oxygen_saturation
    bled = dict(step.bled_mol)
    terminal = {
        species: moles
        for species, moles in bled.items()
        if species != OXYGEN_SPECIES
    }
    booked = step.mass_start_kg + step.source_kg
    accounted = step.mass_end_kg + step.bled_kg
    closure = (
        abs(booked - accounted) / booked * 100.0 if booked > 0.0 else 0.0
    )
    return CapacityShadowResult(
        capacity=capacity,
        partial_pressures_Pa=step.partials,
        evaporation_flux_kg_hr=step.flux_kg_hr,
        bled_species_mol=bled,
        terminal_offgas_mol=terminal,
        oxygen=OxygenShadowPartition(0.0, 0.0, 0.0, 0.0, 0.0),
        saturation=SaturationShadow(
            pipe_saturation,
            oxygen_saturation,
            combined,
            step.binding,
        ),
        iterations=step.iterations,
        max_delta_history_Pa=(step.residual_Pa,),
        mass_closure_error_pct=closure,
        authoritative=authoritative,
        species_notices=tuple(law.species_notices.values()),
    )


def _build_law(
    *,
    pre_holdup_mol: Mapping[str, float],
    molar_mass_kg_mol: Mapping[str, float],
    flux_kg_hr_at_partials: Callable[[Mapping[str, float]], Mapping[str, float]],
    capacity: NoColdTrain | FiniteCapacity,
    temperature_K: float,
    volume_m3: float,
    dt_hr: float,
    downstream_pressure_Pa: float,
    pipe_diameter_m: float | None,
    pipe_length_m: float | None,
    outflow_cap_kg_s: float | None,
    redox_source_mol_s: Mapping[str, float] | None,
    redox_mol_s_at_partials: (
        Callable[[Mapping[str, float]], Mapping[str, float]] | None
    ) = None,
) -> tuple[_HeadspaceLaw, dict[str, float]] | CapacityShadowRefusal:
    try:
        if not all(
            math.isfinite(value) and value > 0.0
            for value in (temperature_K, volume_m3, dt_hr)
        ):
            raise _BalanceRefused(
                "temperature_K, volume_m3, and dt_hr must be finite and positive"
            )
        downstream = _as_finite(
            downstream_pressure_Pa,
            "downstream_pressure_Pa must be finite and >= 0",
        )
        if downstream < 0.0:
            raise _BalanceRefused(
                "downstream_pressure_Pa must be finite and >= 0"
            )
        has_diameter = pipe_diameter_m is not None
        has_length = pipe_length_m is not None
        if has_diameter != has_length:
            raise _BalanceRefused("pipe_geometry_incomplete")
        diameter: float | None = None
        length: float | None = None
        if has_diameter:
            diameter = _as_finite(
                pipe_diameter_m,
                "pipe_diameter_m and pipe_length_m must be finite and positive",
            )
            length = _as_finite(
                pipe_length_m,
                "pipe_diameter_m and pipe_length_m must be finite and positive",
            )
            if diameter <= 0.0 or length <= 0.0:
                raise _BalanceRefused(
                    "pipe_diameter_m and pipe_length_m must be finite and positive"
                )
        cap_kg_s: float | None = None
        if isinstance(capacity, FiniteCapacity):
            # value_kg_hr is kg/h. / 3600 is kg/s. Unit check: kg/h / (s/h) = kg/s.
            cap_kg_s = capacity.value_kg_hr / 3600.0
        if outflow_cap_kg_s is not None:
            explicit = _as_finite(
                outflow_cap_kg_s,
                "outflow_cap_kg_s must be finite and >= 0",
            )
            if explicit < 0.0:
                raise _BalanceRefused("outflow_cap_kg_s must be finite and >= 0")
            cap_kg_s = explicit if cap_kg_s is None else min(cap_kg_s, explicit)
        if diameter is None and cap_kg_s is None:
            raise _BalanceRefused("headspace_balance_no_outflow")
        redox: dict[str, float] = {}
        if redox_source_mol_s is not None:
            for name, rate in redox_source_mol_s.items():
                redox[str(name)] = _as_finite(
                    rate,
                    f"redox_source_mol_s must be finite:{name}",
                )
        # One flux evaluation at the opening inventory. Species that
        # source introduces join the set at n0 = 0. A name with no
        # molar mass is a typed refusal, not a dropped source. Later
        # trials that invent a further name still refuse in _flux_kg_hr.
        factor = GAS_CONSTANT * temperature_K / volume_m3
        opening_partials: dict[str, float] = {}
        for name, amount in pre_holdup_mol.items():
            number = _as_finite(
                amount,
                "pre_holdup_mol must be finite and >= 0",
            )
            if number < 0.0:
                raise _BalanceRefused("pre_holdup_mol must be finite and >= 0")
            if number > 0.0:
                opening_partials[str(name)] = number * factor
        from simulator.evaporation import EvaporationFluxRefusal

        try:
            discovered = flux_kg_hr_at_partials(opening_partials)
        except EvaporationFluxRefusal:
            raise
        except _BalanceRefused:
            raise
        except Exception as exc:
            raise _BalanceRefused(
                f"evaporation_flux_refused:{type(exc).__name__}:{exc}"
            ) from exc
        if not isinstance(discovered, Mapping):
            raise _BalanceRefused("evaporation_flux_not_a_mapping")
        discovered_names: set[str] = set()
        for name, rate in discovered.items():
            _as_finite(rate, f"evaporation_flux_not_finite:{name}")
            discovered_names.add(str(name))
        names = set(pre_holdup_mol) | set(redox) | discovered_names
        if not names:
            raise _BalanceRefused("headspace_balance_no_species")
        masses: dict[str, float] = {}
        n0: dict[str, float] = {}
        for name in sorted(names):
            if name not in molar_mass_kg_mol:
                raise _BalanceRefused(f"missing_molar_mass:{name}")
            mass = _as_finite(
                molar_mass_kg_mol[name],
                f"missing_molar_mass:{name}",
            )
            if mass <= 0.0:
                raise _BalanceRefused(f"missing_molar_mass:{name}")
            masses[name] = mass
            amount = _as_finite(
                pre_holdup_mol.get(name, 0.0),
                "pre_holdup_mol must be finite and >= 0",
            )
            if amount < 0.0:
                raise _BalanceRefused("pre_holdup_mol must be finite and >= 0")
            n0[name] = amount
        law = _HeadspaceLaw(
            species=tuple(sorted(names)),
            masses=masses,
            flux=flux_kg_hr_at_partials,
            temperature_K=temperature_K,
            volume_m3=volume_m3,
            downstream_pa=downstream,
            diameter_m=diameter,
            length_m=length,
            cap_kg_s=cap_kg_s,
            redox_mol_s=redox,
            redox_mol_s_at_partials=redox_mol_s_at_partials,
        )
        return law, n0
    except _BalanceRefused as refused:
        return CapacityShadowRefusal(refused.reason, 0)


def _commit_headspace_balance(
    *,
    pre_holdup_mol: Mapping[str, float],
    molar_mass_kg_mol: Mapping[str, float],
    flux_kg_hr_at_partials: Callable[[Mapping[str, float]], Mapping[str, float]],
    capacity: NoColdTrain | FiniteCapacity,
    temperature_K: float,
    volume_m3: float,
    dt_hr: float,
    downstream_pressure_Pa: float,
    vessel_rating_Pa: float | None,
    pipe_diameter_m: float | None,
    pipe_length_m: float | None,
    outflow_cap_kg_s: float | None,
    redox_source_mol_s: Mapping[str, float] | None,
    redox_mol_s_at_partials: (
        Callable[[Mapping[str, float]], Mapping[str, float]] | None
    ) = None,
    authoritative: bool = False,
    rel_tol: float = DEFAULT_REL_TOL,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> CapacityShadowResult | CapacityShadowRefusal:
    built = _build_law(
        pre_holdup_mol=pre_holdup_mol,
        molar_mass_kg_mol=molar_mass_kg_mol,
        flux_kg_hr_at_partials=flux_kg_hr_at_partials,
        capacity=capacity,
        temperature_K=temperature_K,
        volume_m3=volume_m3,
        dt_hr=dt_hr,
        downstream_pressure_Pa=downstream_pressure_Pa,
        pipe_diameter_m=pipe_diameter_m,
        pipe_length_m=pipe_length_m,
        outflow_cap_kg_s=outflow_cap_kg_s,
        redox_source_mol_s=redox_source_mol_s,
        redox_mol_s_at_partials=redox_mol_s_at_partials,
    )
    if isinstance(built, CapacityShadowRefusal):
        return built
    law, n0 = built
    dt_s = dt_hr * 3600.0
    from simulator.evaporation import EvaporationFluxRefusal

    try:
        if len(law.species) == 1:
            step = _commit_one(law, n0, dt_s)
        else:
            step = _commit_many(
                law,
                n0,
                dt_s,
                rel_tol=rel_tol,
                max_iterations=max_iterations,
            )
    except EvaporationFluxRefusal:
        raise
    except _BalanceRefused as refused:
        return CapacityShadowRefusal(refused.reason, 0)
    return _publish(
        law,
        step,
        capacity,
        vessel_rating_Pa,
        authoritative=authoritative,
    )


def diagnostic_backward_euler_headspace_step(
    *,
    pre_holdup_mol: Mapping[str, float],
    molar_mass_kg_mol: Mapping[str, float],
    flux_kg_hr_at_partials: Callable[[Mapping[str, float]], Mapping[str, float]],
    temperature_K: float,
    volume_m3: float,
    dt_hr: float,
    downstream_pressure_Pa: float,
    pipe_diameter_m: float | None = None,
    pipe_length_m: float | None = None,
    outflow_cap_kg_s: float | None = None,
    redox_source_mol_s: Mapping[str, float] | None = None,
    capacity: NoColdTrain | FiniteCapacity | None = None,
    vessel_rating_Pa: float | None = None,
) -> CapacityShadowResult | CapacityShadowRefusal:
    """One R = 0 step. Not the fast root, and not halved.

    The commit solves G = 0 when admission holds. This function always
    solves the backward-Euler residual, so a single 3600 s step of the
    stranded hour stays at 248.85783689 Pa.
    """

    if capacity is None:
        capacity = NoColdTrain()
    built = _build_law(
        pre_holdup_mol=pre_holdup_mol,
        molar_mass_kg_mol=molar_mass_kg_mol,
        flux_kg_hr_at_partials=flux_kg_hr_at_partials,
        capacity=capacity,
        temperature_K=temperature_K,
        volume_m3=volume_m3,
        dt_hr=dt_hr,
        downstream_pressure_Pa=downstream_pressure_Pa,
        pipe_diameter_m=pipe_diameter_m,
        pipe_length_m=pipe_length_m,
        outflow_cap_kg_s=outflow_cap_kg_s,
        redox_source_mol_s=redox_source_mol_s,
    )
    if isinstance(built, CapacityShadowRefusal):
        return built
    law, n0 = built
    from simulator.evaporation import EvaporationFluxRefusal

    try:
        step = _one_euler(law, n0, dt_hr * 3600.0)
    except EvaporationFluxRefusal:
        raise
    except _BalanceRefused as refused:
        return CapacityShadowRefusal(refused.reason, 0)
    return _publish(law, step, capacity, vessel_rating_Pa)
