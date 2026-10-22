"""Builtin OVERHEAD_BLEED provider."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from engines.builtin._common import (
    build_atom_balance_proof,
    diagnostic_control_audit,
    reject_wrong_intent,
    unpack_controls,
)
from simulator.chemistry.kernel.capabilities import (
    CapabilityProfile,
    ChemistryIntent,
)
from simulator.chemistry.kernel.dto import (
    IntentRequest,
    IntentResult,
    LedgerTransitionProposal,
)
from simulator.chemistry.kernel.provider import ChemistryProvider
from simulator.scalar_boundary import is_declared_real_scalar
from simulator.physical_constants import GAS_CONSTANT
from simulator.account_ids import OXYGEN_CISTERN_LIQUID_INVENTORY_ACCOUNT
from simulator.thermal_train import OXYGEN_VAPORIZATION_ENTHALPY_J_PER_MOL
from simulator.transport_regime import (
    gas_dynamic_viscosity_Pa_s,
    pipe_downstream_pressure_pa_for_flow_fraction,
)


PROCESS_OVERHEAD_GAS_ACCOUNT = "process.overhead_gas"
TERMINAL_OFFGAS_ACCOUNT = "terminal.offgas"
OXYGEN_MELT_OFFGAS_ACCOUNT = "terminal.oxygen_melt_offgas_stored"
OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT = (
    "terminal.oxygen_melt_offgas_vented_to_vacuum"
)
OXYGEN_BUBBLER_EXTERNAL_VENTED_ACCOUNT = (
    "terminal.oxygen_bubbler_external_vented_to_vacuum"
)
OXYGEN_SPECIES = "O2"


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


class BuiltinOverheadBleedProvider(ChemistryProvider):
    """Authoritative pure-move bleed from process overhead gas."""

    name = "builtin-overhead-bleed"
    DECLARED_ACCOUNTS = frozenset({
        'reservoir.reagent.sweep_gas',
        PROCESS_OVERHEAD_GAS_ACCOUNT,
        TERMINAL_OFFGAS_ACCOUNT,
        OXYGEN_MELT_OFFGAS_ACCOUNT,
        OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT,
        OXYGEN_BUBBLER_EXTERNAL_VENTED_ACCOUNT,
        OXYGEN_CISTERN_LIQUID_INVENTORY_ACCOUNT,
    })

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.OVERHEAD_BLEED}),
            is_authoritative_for=frozenset({ChemistryIntent.OVERHEAD_BLEED}),
            declared_accounts=self.DECLARED_ACCOUNTS,
            consumes_fO2=False,
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        from simulator.accounting.formulas import resolve_species_formula

        wrong_intent = reject_wrong_intent(
            request, ChemistryIntent.OVERHEAD_BLEED
        )
        if wrong_intent is not None:
            return wrong_intent

        control_audit = diagnostic_control_audit(request, include_fO2=False)
        controls = unpack_controls(request)
        invalid_control = self._invalid_destructive_control(controls)
        if invalid_control is not None:
            return IntentResult(
                intent=ChemistryIntent.OVERHEAD_BLEED,
                status="unsupported",
                control_audit=control_audit,
                diagnostic={"reason": invalid_control},
            )
        holdup_mol = {
            str(species): max(0.0, float(mol))
            for species, mol in dict(
                request.account_view.accounts.get(
                    PROCESS_OVERHEAD_GAS_ACCOUNT, {}
                ) or {}
            ).items()
            if max(0.0, float(mol)) > 0.0
        }
        inlet_mol = dict(request.account_view.accounts.get('reservoir.reagent.sweep_gas', {}) or {})
        if inlet_mol and 'sweep_outflow_species_mol' not in controls:
            return IntentResult(intent=ChemistryIntent.OVERHEAD_BLEED, status='unsupported',
                                diagnostic={'reason': 'sweep source requires integrated sweep outflow'})
        for species, mol in inlet_mol.items():
            holdup_mol[species] = holdup_mol.get(species, 0.0) + mol
        if not holdup_mol:
            return IntentResult(
                intent=ChemistryIntent.OVERHEAD_BLEED,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic={"bled_species_mol": {}},
            )

        registry = request.account_view.species_formula_registry
        molar_mass = {
            species: resolve_species_formula(
                species, registry
            ).molar_mass_kg_per_mol()
            for species in holdup_mol
        }
        total_mol = sum(holdup_mol.values())
        total_kg = sum(
            holdup_mol[species] * molar_mass[species]
            for species in holdup_mol
        )
        if total_mol <= 0.0 or total_kg <= 0.0:
            return IntentResult(
                intent=ChemistryIntent.OVERHEAD_BLEED,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic={"bled_species_mol": {}},
            )

        capacity_shortfall = controls.get('sweep_capacity_shortfall')
        outlet_fraction = 1.0
        if 'sweep_outflow_species_mol' in controls:
            candidate_bled_mol = dict(controls['sweep_outflow_species_mol'])
            if any(n > holdup_mol.get(s, 0.0) + 1e-12 * max(1.0, holdup_mol.get(s, 0.0))
                   for s, n in candidate_bled_mol.items()):
                return IntentResult(intent=ChemistryIntent.OVERHEAD_BLEED, status='unsupported',
                                    diagnostic={'reason': 'sweep outflow exceeds available species inventory plus inlet'})
            candidate_bled_mol = {s: min(n, holdup_mol.get(s, 0.0))
                                  for s, n in candidate_bled_mol.items() if n > 0.0}
            requested_outlet_o2_mol = candidate_bled_mol.get(OXYGEN_SPECIES, 0.0)
            allowed = self._bled_species_mol(
                holdup_mol, total_mol=total_mol, total_kg=total_kg, controls=controls,
            )
            allowed_mass = sum(n * molar_mass[s] for s, n in allowed.items())
            requested_mass = sum(n * molar_mass[s] for s, n in candidate_bled_mol.items())
            # A changing-pressure plan can discharge more than its FINAL
            # instantaneous capacity times dt during depressurization. Its
            # owner already integrated the physical rate; do not clip it again.
            if (controls.get('sweep_capacity_integrated') is not True
                    and requested_mass > allowed_mass + 1e-12 * max(1.0, requested_mass)):
                # Direct provider callers may still request an unachievable
                # integrated outlet. Saturate the OUTLET, never the admitted
                # carrier. Live sweep callers already integrate changing P and
                # source-separated residence in the overhead balance owner.
                outlet_fraction = allowed_mass / requested_mass
                candidate_bled_mol = {s: n * outlet_fraction
                                      for s, n in candidate_bled_mol.items() if n * outlet_fraction > 0.0}
                residual_mol = sum(holdup_mol.values()) - sum(candidate_bled_mol.values())
                capacity_shortfall = {
                    'reason': 'duct_capacity_shortfall',
                    'requested_outlet_kg': requested_mass, 'allowed_outlet_kg': allowed_mass,
                    'achieved_pressure_pa': residual_mol * GAS_CONSTANT
                    * float(controls['headspace_temperature_K']) / float(controls['headspace_volume_m3']),
                }
        else:
            candidate_bled_mol = self._bled_species_mol(
                holdup_mol,
                total_mol=total_mol,
                total_kg=total_kg,
                controls=controls,
            )
        credits: dict[str, dict[str, float]] = {}
        offgas: dict[str, float] = {}

        bled_o2_mol = candidate_bled_mol.get(OXYGEN_SPECIES, 0.0)
        bled_o2_kg = bled_o2_mol * molar_mass.get(OXYGEN_SPECIES, 0.0)
        overhead_o2_mol = holdup_mol.get(OXYGEN_SPECIES, 0.0)
        external_o2_holdup_mol = self._external_o2_holdup_mol(
            controls,
            overhead_o2_mol,
        )
        integrated_external = controls.get('sweep_external_o2_outflow_mol')
        if 'sweep_outflow_species_mol' in controls:
            if external_o2_holdup_mol > 0.0 and integrated_external is None:
                return IntentResult(intent=ChemistryIntent.OVERHEAD_BLEED, status='unsupported',
                                    diagnostic={'reason': 'missing_input: source-separated external O2 sweep outlet'})
            if integrated_external is not None and (
                integrated_external > requested_outlet_o2_mol or integrated_external > external_o2_holdup_mol
                or requested_outlet_o2_mol - integrated_external > overhead_o2_mol - external_o2_holdup_mol
                + 1e-12 * max(1.0, overhead_o2_mol)
            ):
                return IntentResult(intent=ChemistryIntent.OVERHEAD_BLEED, status='unsupported',
                                    diagnostic={'reason': 'source-separated sweep outlet exceeds available O2 class'})
        if integrated_external is not None:
            integrated_external *= outlet_fraction
        from simulator.capacity_coupling import external_oxygen_outlet_mol

        external_o2_bled_mol = external_oxygen_outlet_mol(
            bled_o2_mol=bled_o2_mol, overhead_o2_mol=overhead_o2_mol,
            external_o2_holdup_mol=external_o2_holdup_mol,
            integrated_external_o2_mol=integrated_external,
        )
        melt_o2_bled_mol = max(0.0, bled_o2_mol - external_o2_bled_mol)
        capacity = controls.get("cold_train_capacity")
        o2_held_mol = 0.0
        o2_accumulated_mol = 0.0
        cistern_fill_kg = 0.0
        accumulator_enabled = controls.get("accumulator_enabled") is True
        from simulator.thermal_train import FiniteCapacity

        if isinstance(capacity, FiniteCapacity):
            if (
                overhead_o2_mol > 0.0
                and molar_mass.get(OXYGEN_SPECIES, 0.0) > 0.0
            ):
                from simulator.capacity_coupling import partition_melt_oxygen

                cistern_fill_mol = float(
                    request.account_view.accounts.get(
                        OXYGEN_CISTERN_LIQUID_INVENTORY_ACCOUNT, {}
                    ).get(OXYGEN_SPECIES, 0.0)
                    or 0.0
                )
                cistern_fill_kg = (
                    cistern_fill_mol
                    * molar_mass.get(OXYGEN_SPECIES, 0.0)
                )
                if accumulator_enabled and cistern_fill_kg > float(
                    controls["cavern_capacity_kg"]
                ):
                    return IntentResult(
                        intent=ChemistryIntent.OVERHEAD_BLEED,
                        status="unsupported",
                        control_audit=control_audit,
                        diagnostic={
                            "reason": (
                                "cistern_fill_kg must not exceed "
                                "cavern_capacity_kg"
                            )
                        },
                    )

                partition = partition_melt_oxygen(
                    bled_o2_mol=bled_o2_mol,
                    overhead_o2_mol=overhead_o2_mol,
                    external_o2_holdup_mol=external_o2_holdup_mol,
                    integrated_external_o2_mol=integrated_external,
                    capacity=capacity,
                    dt_hr=float(controls.get("dt_hr", 1.0)),
                    p_o2_Pa=float(controls.get("p_ref_Pa", 0.0)),
                    k_relief_kg_hr_Pa=float(
                        controls.get("k_relief_kg_hr_Pa", 0.0)
                    ),
                    p_open_Pa=float(controls.get("p_open_Pa", 0.0)),
                    molar_mass_kg_mol=molar_mass.get(OXYGEN_SPECIES, 0.0),
                    accumulator_enabled=accumulator_enabled,
                    cistern_fill_kg=cistern_fill_kg,
                    cavern_capacity_kg=float(
                        controls.get("cavern_capacity_kg", 0.0)
                    ),
                )
                external_o2_bled_mol = partition.external_mol
                o2_stored_mol = partition.admitted_mol
                o2_accumulated_mol = partition.accumulated_mol
                melt_o2_vented_mol = partition.relieved_mol
                o2_held_mol = partition.held_mol
                melt_o2_bled_mol = (
                    o2_stored_mol
                    + o2_accumulated_mol
                    + melt_o2_vented_mol
                )
            else:
                o2_stored_mol = 0.0
                melt_o2_vented_mol = 0.0
        else:
            melt_o2_vented_mol = 0.0
            o2_stored_mol = melt_o2_bled_mol
        o2_vented_mol = melt_o2_vented_mol + external_o2_bled_mol
        debited_mol = dict(candidate_bled_mol)
        actual_o2_debit_mol = (
            external_o2_bled_mol
            + o2_stored_mol
            + o2_accumulated_mol
            + melt_o2_vented_mol
        )
        if actual_o2_debit_mol > 0.0:
            debited_mol[OXYGEN_SPECIES] = actual_o2_debit_mol
        else:
            debited_mol.pop(OXYGEN_SPECIES, None)
        if o2_stored_mol > 0.0:
            credits[OXYGEN_MELT_OFFGAS_ACCOUNT] = {
                OXYGEN_SPECIES: o2_stored_mol
            }
        if o2_accumulated_mol > 0.0:
            credits[OXYGEN_CISTERN_LIQUID_INVENTORY_ACCOUNT] = {
                OXYGEN_SPECIES: o2_accumulated_mol
            }
        if melt_o2_vented_mol > 0.0:
            credits[OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT] = {
                OXYGEN_SPECIES: melt_o2_vented_mol
            }
        if external_o2_bled_mol > 0.0:
            credits[OXYGEN_BUBBLER_EXTERNAL_VENTED_ACCOUNT] = {
                OXYGEN_SPECIES: external_o2_bled_mol
            }

        for species, mol in candidate_bled_mol.items():
            if species == OXYGEN_SPECIES:
                continue
            if mol > 0.0:
                offgas[species] = mol
        if offgas:
            credits[TERMINAL_OFFGAS_ACCOUNT] = offgas

        if not debited_mol and not inlet_mol:
            return IntentResult(
                intent=ChemistryIntent.OVERHEAD_BLEED,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "bled_species_mol": {},
                    "candidate_bled_species_mol": dict(candidate_bled_mol),
                    "o2_held_mol": o2_held_mol,
                    **({"capacity_shortfall": capacity_shortfall} if capacity_shortfall else {}),
                },
            )
        # Only this transition admits delivered carrier into the chamber and
        # removes integrated outlet. Net each species against the inlet so a
        # tick may discharge more than the old chamber stock without overdraft.
        chamber_debits = {s: n - inlet_mol.get(s, 0.0)
                          for s, n in debited_mol.items() if n > inlet_mol.get(s, 0.0)}
        chamber_credits = {s: n - debited_mol.get(s, 0.0)
                           for s, n in inlet_mol.items() if n > debited_mol.get(s, 0.0)}
        debits = {PROCESS_OVERHEAD_GAS_ACCOUNT: chamber_debits} if chamber_debits else {}
        if inlet_mol:
            debits['reservoir.reagent.sweep_gas'] = inlet_mol
        if chamber_credits:
            credits[PROCESS_OVERHEAD_GAS_ACCOUNT] = chamber_credits
        atom_proof = build_atom_balance_proof(
            debits, credits, registry, resolve_species_formula
        )
        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason="overhead_bleed",
            atom_balance_proof=atom_proof,
        )

        return IntentResult(
            intent=ChemistryIntent.OVERHEAD_BLEED,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic={
                "bled_species_mol": dict(debited_mol),
                **({"capacity_shortfall": capacity_shortfall} if capacity_shortfall else {}),
                "candidate_bled_species_mol": dict(candidate_bled_mol),
                "bled_total_kg": sum(
                    mol * molar_mass[species]
                    for species, mol in debited_mol.items()
                ),
                "bled_o2_mol": debited_mol.get(OXYGEN_SPECIES, 0.0),
                "bled_o2_kg": debited_mol.get(OXYGEN_SPECIES, 0.0)
                * molar_mass.get(OXYGEN_SPECIES, 0.0),
                "candidate_bled_o2_mol": bled_o2_mol,
                "o2_stored_mol": o2_stored_mol,
                "o2_admitted_mol": o2_stored_mol,
                "o2_relieved_mol": melt_o2_vented_mol,
                "o2_held_mol": o2_held_mol,
                "o2_vented_mol": o2_vented_mol,
                "o2_vented_kg": o2_vented_mol * molar_mass.get(
                    OXYGEN_SPECIES, 0.0
                ),
                "melt_o2_bled_mol": melt_o2_bled_mol,
                "melt_o2_vented_mol": melt_o2_vented_mol,
                "external_o2_holdup_mol": external_o2_holdup_mol,
                "external_o2_bled_mol": external_o2_bled_mol,
                "external_o2_vented_mol": external_o2_bled_mol,
                "external_o2_vented_kg": (
                    external_o2_bled_mol * molar_mass.get(OXYGEN_SPECIES, 0.0)
                ),
                "p_ref_Pa": controls.get("p_ref_Pa"),
                **(
                    {
                        "o2_accumulated_mol": o2_accumulated_mol,
                        "cistern_fill_kg": cistern_fill_kg,
                        "cistern_fill_after_kg": (
                            cistern_fill_kg
                            + o2_accumulated_mol
                            * molar_mass.get(OXYGEN_SPECIES, 0.0)
                        ),
                        "cavern_capacity_kg": controls[
                            "cavern_capacity_kg"
                        ],
                        "refreeze_duty_kWh_deferred": (
                            o2_accumulated_mol
                            * OXYGEN_VAPORIZATION_ENTHALPY_J_PER_MOL
                            / 3_600_000.0
                        ),
                    }
                    if accumulator_enabled
                    else {}
                ),
            },
        )

    @staticmethod
    def _external_o2_holdup_mol(controls: dict, overhead_o2_mol: float) -> float:
        raw = controls.get("external_o2_in_overhead_mol", 0.0)
        try:
            external_o2_mol = float(raw)
        except (TypeError, ValueError):
            external_o2_mol = 0.0
        return min(max(0.0, external_o2_mol), max(0.0, overhead_o2_mol))

    @staticmethod
    def _bled_species_mol(
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

    @staticmethod
    def _invalid_destructive_control(controls: dict) -> str | None:
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
            from simulator.thermal_train import FiniteCapacity, NoColdTrain

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
