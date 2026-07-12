"""Builtin OXYGEN_BUBBLER provider."""

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
RESERVOIR_FO2_BUFFER_ACCOUNT = "reservoir.fo2_buffer"
OXYGEN_SPECIES = "O2"
TRANSITION_NAME = "oxygen_bubbler_passthrough"


def _finite_nonnegative_control(controls: dict, key: str) -> float:
    raw_value = controls.get(key, 0.0)
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be numeric, got {raw_value!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"{key} must be finite, got {raw_value!r}")
    return max(0.0, value)


class BuiltinOxygenBubblerProvider(ChemistryProvider):
    """Authoritative pure O2 move for unabsorbed bubbler pass-through."""

    name = "builtin-oxygen-bubbler"
    DECLARED_ACCOUNTS = frozenset({
        PROCESS_OVERHEAD_GAS_ACCOUNT,
        RESERVOIR_FO2_BUFFER_ACCOUNT,
    })

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.OXYGEN_BUBBLER}),
            is_authoritative_for=frozenset({ChemistryIntent.OXYGEN_BUBBLER}),
            declared_accounts=self.DECLARED_ACCOUNTS,
            consumes_fO2=False,
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        from simulator.accounting.formulas import resolve_species_formula

        wrong_intent = reject_wrong_intent(request, ChemistryIntent.OXYGEN_BUBBLER)
        if wrong_intent is not None:
            return wrong_intent

        control_audit = diagnostic_control_audit(request, include_fO2=False)
        controls = unpack_controls(request)
        injected_mol = _finite_nonnegative_control(controls, "injected_mol")
        absorbed_mol = _finite_nonnegative_control(controls, "absorbed_mol")
        passthrough_mol = _finite_nonnegative_control(controls, "passthrough_mol")
        source = str(controls.get("source") or "oxygen_bubbler")

        diagnostic = {
            "source": source,
            "injected_mol": injected_mol,
            "absorbed_mol": absorbed_mol,
            "passthrough_mol": passthrough_mol,
        }
        if passthrough_mol == 0.0:
            diagnostic["transition"] = "none:zero_passthrough"
            return IntentResult(
                intent=ChemistryIntent.OXYGEN_BUBBLER,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic=diagnostic,
            )

        debits = {RESERVOIR_FO2_BUFFER_ACCOUNT: {OXYGEN_SPECIES: passthrough_mol}}
        credits = {PROCESS_OVERHEAD_GAS_ACCOUNT: {OXYGEN_SPECIES: passthrough_mol}}
        atom_proof = build_atom_balance_proof(
            debits,
            credits,
            request.account_view.species_formula_registry,
            resolve_species_formula,
        )
        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason=TRANSITION_NAME,
            atom_balance_proof=atom_proof,
        )
        diagnostic["transition"] = TRANSITION_NAME
        return IntentResult(
            intent=ChemistryIntent.OXYGEN_BUBBLER,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic=diagnostic,
        )
