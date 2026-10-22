"""Builtin OVERHEAD_BLEED provider."""

from __future__ import annotations


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
from simulator.environment import capacity_partial_pressures_pa
from simulator.account_ids import OXYGEN_CISTERN_LIQUID_INVENTORY_ACCOUNT
from simulator.physical_constants import OXYGEN_VAPORIZATION_ENTHALPY_J_PER_MOL
from simulator.gas_balance import (
    EffectiveTransportCapacity, controlled_flow_capacity,
    FiniteCapacity, NoColdTrain, bled_species_mol, invalid_destructive_control,
    external_oxygen_outlet_mol, partition_melt_oxygen,
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
        invalid_control = invalid_destructive_control(controls)
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
            allowed = bled_species_mol(
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
                    'achieved_pressure_pa': capacity_partial_pressures_pa(
                        {'residual': residual_mol}, float(controls['headspace_volume_m3']),
                        float(controls['headspace_temperature_K']),
                    )['residual'],
                }
        else:
            candidate_bled_mol = bled_species_mol(
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

        if isinstance(capacity, FiniteCapacity):
            if (
                overhead_o2_mol > 0.0
                and molar_mass.get(OXYGEN_SPECIES, 0.0) > 0.0
            ):
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

    _bled_species_mol = staticmethod(bled_species_mol)
    _invalid_destructive_control = staticmethod(invalid_destructive_control)
