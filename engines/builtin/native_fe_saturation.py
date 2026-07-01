"""Builtin native-Fe saturation provider.

Routes the pre-0.6 storm FeO -> Fe + 0.5 O2 split through the chemistry
kernel without changing the saturation physics that computes the extent.
"""

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


class BuiltinNativeFeSaturationProvider(ChemistryProvider):
    """Authoritative ``NATIVE_FE_SATURATION`` provider."""

    name = "builtin-native-fe-saturation"

    DECLARED_ACCOUNTS = frozenset({
        "process.cleaned_melt",
        "terminal.drain_tap_material",
        "process.overhead_gas",
    })

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.NATIVE_FE_SATURATION}),
            is_authoritative_for=frozenset(
                {ChemistryIntent.NATIVE_FE_SATURATION}
            ),
            declared_accounts=self.DECLARED_ACCOUNTS,
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        from simulator.accounting.formulas import resolve_species_formula

        wrong_intent = reject_wrong_intent(
            request, ChemistryIntent.NATIVE_FE_SATURATION
        )
        if wrong_intent is not None:
            return wrong_intent

        controls = unpack_controls(request)
        native_fe_mol = max(
            0.0,
            float(controls.get("native_fe_mol", 0.0) or 0.0),
        )
        if native_fe_mol <= 1.0e-12:
            return IntentResult(
                intent=ChemistryIntent.NATIVE_FE_SATURATION,
                status="ok",
                diagnostic={
                    "native_fe_mol": native_fe_mol,
                    "native_fe_saturation_split": "no_op",
                },
                control_audit=diagnostic_control_audit(
                    request, include_fO2=False
                ),
            )

        cleaned_melt = dict(
            request.account_view.accounts.get("process.cleaned_melt", {}) or {}
        )
        feo_available_mol = max(
            0.0,
            float(cleaned_melt.get("FeO", 0.0) or 0.0),
        )
        if native_fe_mol > feo_available_mol + 1.0e-12:
            return IntentResult(
                intent=ChemistryIntent.NATIVE_FE_SATURATION,
                status="refused",
                diagnostic={
                    "reason": "native_fe_mol exceeds process.cleaned_melt FeO",
                    "native_fe_mol": native_fe_mol,
                    "feo_available_mol": feo_available_mol,
                },
                control_audit=diagnostic_control_audit(
                    request, include_fO2=False
                ),
            )

        debits = {"process.cleaned_melt": {"FeO": native_fe_mol}}
        credits = {
            "terminal.drain_tap_material": {"Fe": native_fe_mol},
            "process.overhead_gas": {"O2": 0.5 * native_fe_mol},
        }
        registry = request.account_view.species_formula_registry
        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason="native_fe_saturation_split",
            atom_balance_proof=build_atom_balance_proof(
                debits,
                credits,
                registry,
                resolve_species_formula,
            ),
        )
        return IntentResult(
            intent=ChemistryIntent.NATIVE_FE_SATURATION,
            status="ok",
            transition=proposal,
            control_audit=diagnostic_control_audit(request, include_fO2=False),
            diagnostic={
                "native_fe_mol": native_fe_mol,
                "feo_debit_mol": native_fe_mol,
                "tap_fe_credit_mol": native_fe_mol,
                "overhead_o2_credit_mol": 0.5 * native_fe_mol,
            },
        )
