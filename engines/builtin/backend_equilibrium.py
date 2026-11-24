"""Authoritative BACKEND_EQUILIBRIUM commit authority marker."""

from __future__ import annotations

from simulator.chemistry.kernel.capabilities import (
    CapabilityProfile,
    ChemistryIntent,
)
from simulator.chemistry.kernel.dto import IntentRequest, IntentResult
from simulator.chemistry.kernel.provider import ChemistryProvider


class BuiltinBackendEquilibriumProvider(ChemistryProvider):
    """Declares account authority for validated backend equilibrium commits."""

    name = "builtin-backend-equilibrium"
    def capability_profile(self) -> CapabilityProfile:
        from simulator.account_ids import OXYGEN_RESERVOIR_FO2_BUFFER_ACCOUNT

        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.BACKEND_EQUILIBRIUM}),
            is_authoritative_for=frozenset({
                ChemistryIntent.BACKEND_EQUILIBRIUM
            }),
            declared_accounts=frozenset({
                "process.cleaned_melt",
                "process.metal_phase",
                "process.overhead_gas",
                OXYGEN_RESERVOIR_FO2_BUFFER_ACCOUNT,
            }),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        return IntentResult(
            intent=request.intent,
            status="unsupported",
            diagnostic={
                "reason": "BACKEND_EQUILIBRIUM commits existing backend "
                "LedgerTransition objects through ChemistryKernel",
            },
        )
