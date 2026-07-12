"""Builtin OVERHEAD_BLEED provider."""

from __future__ import annotations

import math

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

        bled_mol = self._bled_species_mol(
            holdup_mol,
            total_mol=total_mol,
            total_kg=total_kg,
            controls=controls,
        )
        if not bled_mol:
            return IntentResult(
                intent=ChemistryIntent.OVERHEAD_BLEED,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic={"bled_species_mol": {}},
            )

        debits = {PROCESS_OVERHEAD_GAS_ACCOUNT: dict(bled_mol)}
        credits: dict[str, dict[str, float]] = {}
        offgas: dict[str, float] = {}

        bled_o2_mol = bled_mol.get(OXYGEN_SPECIES, 0.0)
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
        melt_o2_bled_kg = melt_o2_bled_mol * molar_mass.get(OXYGEN_SPECIES, 0.0)
        melt_o2_vented_kg = self._o2_vented_kg(
            melt_o2_bled_kg, controls
        )
        melt_o2_vented_mol = (
            melt_o2_vented_kg / molar_mass[OXYGEN_SPECIES]
            if melt_o2_bled_mol > 0.0 and molar_mass.get(OXYGEN_SPECIES, 0.0) > 0.0
            else 0.0
        )
        melt_o2_vented_mol = min(melt_o2_bled_mol, max(0.0, melt_o2_vented_mol))
        o2_stored_mol = max(0.0, melt_o2_bled_mol - melt_o2_vented_mol)
        o2_vented_mol = melt_o2_vented_mol + external_o2_bled_mol
        if o2_stored_mol > 0.0:
            credits[OXYGEN_MELT_OFFGAS_ACCOUNT] = {
                OXYGEN_SPECIES: o2_stored_mol
            }
        if melt_o2_vented_mol > 0.0:
            credits[OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT] = {
                OXYGEN_SPECIES: melt_o2_vented_mol
            }
        if external_o2_bled_mol > 0.0:
            credits[OXYGEN_BUBBLER_EXTERNAL_VENTED_ACCOUNT] = {
                OXYGEN_SPECIES: external_o2_bled_mol
            }

        for species, mol in bled_mol.items():
            if species == OXYGEN_SPECIES:
                continue
            if mol > 0.0:
                offgas[species] = mol
        if offgas:
            credits[TERMINAL_OFFGAS_ACCOUNT] = offgas

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
                "bled_species_mol": dict(bled_mol),
                "bled_total_kg": sum(
                    mol * molar_mass[species]
                    for species, mol in bled_mol.items()
                ),
                "bled_o2_mol": bled_o2_mol,
                "bled_o2_kg": bled_o2_kg,
                "o2_stored_mol": o2_stored_mol,
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
        force_drain_all = controls.get("force_drain_all", False)
        if not isinstance(force_drain_all, bool):
            return "force_drain_all must be a boolean"
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
            ("o2_vented_kg", 0.0),
            ("max_o2_flow_kg_hr", 0.0),
            ("external_o2_in_overhead_mol", 0.0),
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
        return None

    @staticmethod
    def _o2_vented_kg(bled_o2_kg: float, controls: dict) -> float:
        if bled_o2_kg <= 0.0:
            return 0.0
        if "o2_vented_kg" in controls:
            return min(bled_o2_kg, max(0.0, float(controls["o2_vented_kg"])))
        max_o2_flow_kg_hr = max(
            0.0, float(controls.get("max_o2_flow_kg_hr") or 0.0)
        )
        if max_o2_flow_kg_hr <= 0.0:
            return 0.0
        dt_hr = max(0.0, float(controls.get("dt_hr", 1.0)))
        max_stored_kg = max_o2_flow_kg_hr * dt_hr
        return max(0.0, bled_o2_kg - max_stored_kg)
