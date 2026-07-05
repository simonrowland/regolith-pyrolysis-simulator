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
        if bool(controls.get("force_drain_all", False)):
            return dict(holdup_mol)

        conductance = max(
            0.0, float(controls.get("bleed_conductance_kg_s_per_bar") or 0.0)
        )
        p_total = max(0.0, float(controls.get("p_total_bar") or 0.0))
        p_downstream = max(
            0.0, float(controls.get("p_downstream_bar") or 0.0)
        )
        dt_hr = max(0.0, float(controls.get("dt_hr") or 1.0))
        bleed_kg = conductance * max(0.0, p_total - p_downstream) * dt_hr * 3600.0
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
        dt_hr = max(0.0, float(controls.get("dt_hr") or 1.0))
        max_stored_kg = max_o2_flow_kg_hr * dt_hr
        return max(0.0, bled_o2_kg - max_stored_kg)
