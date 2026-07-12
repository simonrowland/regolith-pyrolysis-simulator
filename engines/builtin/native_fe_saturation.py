"""Builtin native-Fe saturation provider.

Routes the pre-0.6 storm FeO -> Fe + 0.5 O2 split through the chemistry
kernel without changing the saturation physics that computes the extent.
Native Fe vapor is only budgeted here; the Fe vapor ledger leg is routed
through EVAPORATION_TRANSITION -> CONDENSATION_ROUTE with every other metal
vapor.
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
            consumes_fO2=False,
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
        native_fe_vapor_mol = max(
            0.0,
            float(controls.get("native_fe_vapor_mol", 0.0) or 0.0),
        )
        if native_fe_mol <= 1.0e-12:
            return IntentResult(
                intent=ChemistryIntent.NATIVE_FE_SATURATION,
                status="ok",
                diagnostic={
                    "native_fe_mol": native_fe_mol,
                    "native_fe_vapor_mol": 0.0,
                    "native_fe_tap_mol": 0.0,
                    "native_fe_saturation_split": "no_op",
                },
                control_audit=diagnostic_control_audit(
                    request, include_fO2=False
                ),
            )
        if native_fe_vapor_mol > native_fe_mol + 1.0e-12:
            return IntentResult(
                intent=ChemistryIntent.NATIVE_FE_SATURATION,
                status="refused",
                diagnostic={
                    "reason": (
                        "native_fe_vapor_mol exceeds native_fe_mol"
                    ),
                    "native_fe_mol": native_fe_mol,
                    "native_fe_vapor_mol": native_fe_vapor_mol,
                },
                control_audit=diagnostic_control_audit(
                    request, include_fO2=False
                ),
            )
        native_fe_vapor_mol = min(native_fe_mol, native_fe_vapor_mol)
        native_fe_tap_mol = native_fe_mol - native_fe_vapor_mol

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

        debits = {}
        if native_fe_tap_mol > 1.0e-12:
            debits["process.cleaned_melt"] = {"FeO": native_fe_tap_mol}
        overhead_gas = {}
        tap_o2_mol = 0.5 * native_fe_tap_mol
        if tap_o2_mol > 1.0e-12:
            overhead_gas["O2"] = tap_o2_mol
        credits = {}
        if overhead_gas:
            credits["process.overhead_gas"] = overhead_gas
        if native_fe_tap_mol > 1.0e-12:
            credits["terminal.drain_tap_material"] = {"Fe": native_fe_tap_mol}
        registry = request.account_view.species_formula_registry
        if not debits and not credits:
            return IntentResult(
                intent=ChemistryIntent.NATIVE_FE_SATURATION,
                status="ok",
                transition=None,
                control_audit=diagnostic_control_audit(request, include_fO2=False),
                diagnostic={
                    "native_fe_mol": native_fe_mol,
                    "feo_debit_mol": 0.0,
                    "tap_fe_credit_mol": 0.0,
                    "overhead_fe_credit_mol": 0.0,
                    "routed_fe_vapor_mol": native_fe_vapor_mol,
                    "overhead_o2_credit_mol": 0.0,
                },
            )
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
                "feo_debit_mol": native_fe_tap_mol,
                "tap_fe_credit_mol": native_fe_tap_mol,
                "overhead_fe_credit_mol": 0.0,
                "routed_fe_vapor_mol": native_fe_vapor_mol,
                "overhead_o2_credit_mol": tap_o2_mol,
            },
        )
