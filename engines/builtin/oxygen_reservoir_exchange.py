"""Builtin OXYGEN_RESERVOIR_EXCHANGE provider."""

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
OXYGEN_SPECIES = "O2"
TRANSITION_NAME = "oxygen_reservoir_exchange"


class BuiltinOxygenReservoirExchangeProvider(ChemistryProvider):
    """Authoritative pure O2 move between melt redox buffer and headspace."""

    name = "builtin-oxygen-reservoir-exchange"

    def capability_profile(self) -> CapabilityProfile:
        from simulator.account_ids import OXYGEN_RESERVOIR_FO2_BUFFER_ACCOUNT

        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.OXYGEN_RESERVOIR_EXCHANGE}),
            is_authoritative_for=frozenset({
                ChemistryIntent.OXYGEN_RESERVOIR_EXCHANGE,
            }),
            declared_accounts=frozenset({
                PROCESS_OVERHEAD_GAS_ACCOUNT,
                OXYGEN_RESERVOIR_FO2_BUFFER_ACCOUNT,
            }),
            consumes_fO2=False,
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        from simulator.accounting.formulas import resolve_species_formula
        from simulator.account_ids import OXYGEN_RESERVOIR_FO2_BUFFER_ACCOUNT

        wrong_intent = reject_wrong_intent(
            request, ChemistryIntent.OXYGEN_RESERVOIR_EXCHANGE
        )
        if wrong_intent is not None:
            return wrong_intent

        control_audit = diagnostic_control_audit(request, include_fO2=False)
        controls = unpack_controls(request)
        raw_dn = controls.get("dn_to_headspace_mol", 0.0)
        try:
            dn_to_headspace_mol = float(raw_dn)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"dn_to_headspace_mol must be numeric, got {raw_dn!r}"
            ) from exc
        if not math.isfinite(dn_to_headspace_mol):
            raise ValueError(
                "dn_to_headspace_mol must be finite, "
                f"got {raw_dn!r}"
            )

        if dn_to_headspace_mol == 0.0:
            return IntentResult(
                intent=ChemistryIntent.OXYGEN_RESERVOIR_EXCHANGE,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic={"exchange_o2_mol": 0.0},
            )

        amount_mol = abs(dn_to_headspace_mol)
        if dn_to_headspace_mol > 0.0:
            debits = {
                OXYGEN_RESERVOIR_FO2_BUFFER_ACCOUNT: {
                    OXYGEN_SPECIES: amount_mol
                }
            }
            credits = {
                PROCESS_OVERHEAD_GAS_ACCOUNT: {OXYGEN_SPECIES: amount_mol}
            }
            direction = "melt_to_headspace"
        else:
            debits = {
                PROCESS_OVERHEAD_GAS_ACCOUNT: {OXYGEN_SPECIES: amount_mol}
            }
            credits = {
                OXYGEN_RESERVOIR_FO2_BUFFER_ACCOUNT: {
                    OXYGEN_SPECIES: amount_mol
                }
            }
            direction = "headspace_to_melt"

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
        return IntentResult(
            intent=ChemistryIntent.OXYGEN_RESERVOIR_EXCHANGE,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic={
                "exchange_o2_mol": dn_to_headspace_mol,
                "exchange_direction": direction,
            },
        )
