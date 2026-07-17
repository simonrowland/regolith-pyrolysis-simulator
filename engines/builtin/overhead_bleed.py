"""Builtin OVERHEAD_BLEED provider."""

from __future__ import annotations

import math
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
from simulator.account_ids import OXYGEN_CISTERN_LIQUID_INVENTORY_ACCOUNT
from simulator.thermal_train import OXYGEN_VAPORIZATION_ENTHALPY_J_PER_MOL


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
) -> EffectiveTransportCapacity:
    """Resolve a controlled-pO2 flow boundary without prescribing suction."""

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
        # Premise: integrated compressible Poiseuille flow is
        # C(P1,P2)=k(P1^2-P2^2), while C0=k*P1^2 is the vacuum carrying
        # limit. Algebra gives P2=sqrt(max(P1^2-C/k,0)) =
        # P1*sqrt(max(1-C/C0,0)). Here C is the flow the equipment can
        # actually swallow this tick. Units: C/k is bar^2, so P2 is bar.
        # Limits: C->0 gives P2->P1; C->C0 gives P2->0; requested C>C0
        # has no real forward-flow solution and is reported by saturation.
        downstream_pressure = upstream_pressure * math.sqrt(
            max(1.0 - swallowed_flux / pipe_capacity, 0.0)
        )

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


def compressible_pressure_capacity_fraction(
    p_upstream_bar: float,
    p_downstream_bar: float,
) -> float:
    """Fraction of upstream-to-vacuum capacity available at finite P2."""

    try:
        p_upstream_bar = float(p_upstream_bar)
        p_downstream_bar = float(p_downstream_bar)
    except (TypeError, ValueError):
        return 0.0
    if (
        not math.isfinite(p_upstream_bar)
        or not math.isfinite(p_downstream_bar)
        or p_upstream_bar <= 0.0
        or p_downstream_bar >= p_upstream_bar
    ):
        return 0.0
    p_downstream_bar = max(0.0, p_downstream_bar)
    # DERIVATION: premise — a supplied conductance is the kg/s capacity at
    # upstream pressure P1 against vacuum. Compressible Poiseuille flow at
    # fixed geometry is proportional to P1^2-P2^2, so finite downstream
    # pressure multiplies capacity by (P1^2-P2^2)/P1^2. The factored form
    # below avoids cancellation as P2 approaches P1. Sanity: P2=0.5*P1
    # returns 0.75; P2 approaching P1 tends continuously to zero.
    pressure_ratio = p_downstream_bar / p_upstream_bar
    return (1.0 - pressure_ratio) * (1.0 + pressure_ratio)


class BuiltinOverheadBleedProvider(ChemistryProvider):
    """Authoritative pure-move bleed from process overhead gas."""

    name = "builtin-overhead-bleed"
    DECLARED_ACCOUNTS = frozenset({
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
        external_o2_bled_mol = (
            min(
                bled_o2_mol,
                bled_o2_mol * external_o2_holdup_mol / overhead_o2_mol,
            )
            if bled_o2_mol > 0.0 and overhead_o2_mol > 0.0
            else 0.0
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

        if not debited_mol:
            return IntentResult(
                intent=ChemistryIntent.OVERHEAD_BLEED,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "bled_species_mol": {},
                    "candidate_bled_species_mol": dict(candidate_bled_mol),
                    "o2_held_mol": o2_held_mol,
                },
            )
        debits = {PROCESS_OVERHEAD_GAS_ACCOUNT: debited_mol}
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
            pressure_square_fraction = compressible_pressure_capacity_fraction(
                controls.get("p_total_bar") or 0.0,
                controls.get("p_downstream_bar") or 0.0,
            )
            bleed_kg = (
                conductance * pressure_square_fraction * dt_hr * 3600.0
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
