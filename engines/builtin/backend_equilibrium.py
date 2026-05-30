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
    DECLARED_ACCOUNTS = frozenset({
        "process.cleaned_melt",
        "process.metal_phase",
        "process.overhead_gas",
        "reservoir.fo2_buffer",
    })

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.BACKEND_EQUILIBRIUM}),
            is_authoritative_for=frozenset({
                ChemistryIntent.BACKEND_EQUILIBRIUM
            }),
            declared_accounts=self.DECLARED_ACCOUNTS,
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
