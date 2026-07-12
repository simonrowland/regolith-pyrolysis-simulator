"""Builtin provider for partitioning existing native Fe metal."""

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


class BuiltinNativeFeMetallicTapProvider(ChemistryProvider):
    """Authoritative ``NATIVE_FE_METALLIC_TAP`` provider."""

    name = "builtin-native-fe-metallic-tap"

    DECLARED_ACCOUNTS = frozenset({
        "process.metal_phase",
        "terminal.drain_tap_material",
        "process.overhead_gas",
    })

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.NATIVE_FE_METALLIC_TAP}),
            is_authoritative_for=frozenset(
                {ChemistryIntent.NATIVE_FE_METALLIC_TAP}
            ),
            declared_accounts=self.DECLARED_ACCOUNTS,
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        from simulator.accounting.formulas import resolve_species_formula

        wrong_intent = reject_wrong_intent(
            request, ChemistryIntent.NATIVE_FE_METALLIC_TAP
        )
        if wrong_intent is not None:
            return wrong_intent

        controls = unpack_controls(request)
        native_fe_mol = max(
            0.0,
            float(controls.get("native_fe_mol", 0.0) or 0.0),
        )
        native_fe_vapor_mol = max(
            0.0,
            float(controls.get("native_fe_vapor_mol", 0.0) or 0.0),
        )
        if native_fe_mol <= 1.0e-12:
            return IntentResult(
                intent=ChemistryIntent.NATIVE_FE_METALLIC_TAP,
                status="ok",
                diagnostic={
                    "native_fe_mol": native_fe_mol,
                    "native_fe_vapor_mol": 0.0,
                    "native_fe_tap_mol": 0.0,
                    "native_fe_metallic_tap": "no_op",
                },
                control_audit=diagnostic_control_audit(
                    request, include_fO2=False
                ),
            )
        if native_fe_vapor_mol > native_fe_mol + 1.0e-12:
            return IntentResult(
                intent=ChemistryIntent.NATIVE_FE_METALLIC_TAP,
                status="refused",
                diagnostic={
                    "reason": "native_fe_vapor_mol exceeds native_fe_mol",
                    "native_fe_mol": native_fe_mol,
                    "native_fe_vapor_mol": native_fe_vapor_mol,
                },
                control_audit=diagnostic_control_audit(
                    request, include_fO2=False
                ),
            )

        metal_phase = dict(
            request.account_view.accounts.get("process.metal_phase", {}) or {}
        )
        available_fe_mol = max(
            0.0,
            float(metal_phase.get("Fe", 0.0) or 0.0),
        )
        if native_fe_mol > available_fe_mol + 1.0e-12:
            return IntentResult(
                intent=ChemistryIntent.NATIVE_FE_METALLIC_TAP,
                status="refused",
                diagnostic={
                    "reason": "native_fe_mol exceeds process.metal_phase Fe",
                    "native_fe_mol": native_fe_mol,
                    "metal_fe_available_mol": available_fe_mol,
                },
                control_audit=diagnostic_control_audit(
                    request, include_fO2=False
                ),
            )

        native_fe_vapor_mol = min(native_fe_mol, native_fe_vapor_mol)
        native_fe_tap_mol = native_fe_mol - native_fe_vapor_mol
        # All metal_phase Fe is debited; refine source tagging if multi-source
        # sharing becomes real.
        debits = {"process.metal_phase": {"Fe": native_fe_mol}}
        credits = {}
        if native_fe_vapor_mol > 1.0e-12:
            credits["process.overhead_gas"] = {"Fe": native_fe_vapor_mol}
        if native_fe_tap_mol > 1.0e-12:
            credits["terminal.drain_tap_material"] = {"Fe": native_fe_tap_mol}
        registry = request.account_view.species_formula_registry
        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason="native_fe_metal_partition",
            atom_balance_proof=build_atom_balance_proof(
                debits,
                credits,
                registry,
                resolve_species_formula,
            ),
        )
        return IntentResult(
            intent=ChemistryIntent.NATIVE_FE_METALLIC_TAP,
            status="ok",
            transition=proposal,
            control_audit=diagnostic_control_audit(
                request, include_fO2=False
            ),
            diagnostic={
                "native_fe_mol": native_fe_mol,
                "native_fe_source_account": "process.metal_phase",
                "metal_fe_debit_mol": native_fe_mol,
                "tap_fe_credit_mol": native_fe_tap_mol,
                "overhead_fe_credit_mol": native_fe_vapor_mol,
                "routed_fe_vapor_mol": native_fe_vapor_mol,
                "overhead_o2_credit_mol": 0.0,
            },
        )
