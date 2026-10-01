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
from simulator.fe_redox import OXYGEN_RESERVOIR_NOOP_MOL


PROCESS_OVERHEAD_GAS_ACCOUNT = "process.overhead_gas"
RESERVOIR_FO2_BUFFER_ACCOUNT = "reservoir.fo2_buffer"
OXYGEN_SPECIES = "O2"
TRANSITION_NAME = "oxygen_reservoir_exchange"


class BuiltinOxygenReservoirExchangeProvider(ChemistryProvider):
    """Authoritative pure O2 move between melt redox buffer and headspace."""

    name = "builtin-oxygen-reservoir-exchange"
    DECLARED_ACCOUNTS = frozenset({
        "process.cleaned_melt",
        "process.metal_phase",
        PROCESS_OVERHEAD_GAS_ACCOUNT,
        RESERVOIR_FO2_BUFFER_ACCOUNT,
    })

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.OXYGEN_RESERVOIR_EXCHANGE}),
            is_authoritative_for=frozenset({
                ChemistryIntent.OXYGEN_RESERVOIR_EXCHANGE,
            }),
            declared_accounts=self.DECLARED_ACCOUNTS,
            consumes_fO2=False,
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        from simulator.accounting.formulas import resolve_species_formula

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
                RESERVOIR_FO2_BUFFER_ACCOUNT: {OXYGEN_SPECIES: amount_mol}
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
                RESERVOIR_FO2_BUFFER_ACCOUNT: {OXYGEN_SPECIES: amount_mol}
            }
            direction = "headspace_to_melt"

        cleaned_melt = dict(
            request.account_view.accounts.get("process.cleaned_melt", {}) or {}
        )
        metal_phase = dict(
            request.account_view.accounts.get("process.metal_phase", {}) or {}
        )
        feo_mol = max(0.0, float(cleaned_melt.get("FeO", 0.0) or 0.0))
        metal_fe_mol = max(0.0, float(metal_phase.get("Fe", 0.0) or 0.0))
        if (
            bool(controls.get("m2_metal_reaction", False))
            and feo_mol > OXYGEN_RESERVOIR_NOOP_MOL
            and metal_fe_mol > OXYGEN_RESERVOIR_NOOP_MOL
        ):
            # On the retained Fe + FeO buffer, the exchanged gas oxygen is
            # the metal reaction.  Keep this transition atomically balanced:
            # FeO -> Fe + 1/2 O2 for d > 0, and its reverse for d < 0.
            # The ferric inventory and the ferric oxygen buffer are spectators.
            fe_extent_mol = 2.0 * amount_mol
            if dn_to_headspace_mol > 0.0:
                if fe_extent_mol > feo_mol:
                    return IntentResult(
                        intent=ChemistryIntent.OXYGEN_RESERVOIR_EXCHANGE,
                        status="refused",
                        control_audit=control_audit,
                        diagnostic={
                            "reason": "metal_reaction_exceeds_feo_inventory",
                            "requested_fe_mol": fe_extent_mol,
                            "available_feo_mol": feo_mol,
                        },
                    )
                debits = {"process.cleaned_melt": {"FeO": fe_extent_mol}}
                credits = {
                    "process.metal_phase": {"Fe": fe_extent_mol},
                    PROCESS_OVERHEAD_GAS_ACCOUNT: {
                        OXYGEN_SPECIES: amount_mol
                    },
                }
            else:
                overhead_o2_mol = max(
                    0.0,
                    float(
                        request.account_view.accounts.get(
                            PROCESS_OVERHEAD_GAS_ACCOUNT, {}
                        ).get(OXYGEN_SPECIES, 0.0)
                        or 0.0
                    ),
                )
                if fe_extent_mol > metal_fe_mol or amount_mol > overhead_o2_mol:
                    return IntentResult(
                        intent=ChemistryIntent.OXYGEN_RESERVOIR_EXCHANGE,
                        status="refused",
                        control_audit=control_audit,
                        diagnostic={
                            "reason": "metal_reaction_exceeds_inventory",
                            "requested_fe_mol": fe_extent_mol,
                            "available_metal_fe_mol": metal_fe_mol,
                            "requested_o2_mol": amount_mol,
                            "available_headspace_o2_mol": overhead_o2_mol,
                        },
                    )
                debits = {
                    "process.metal_phase": {"Fe": fe_extent_mol},
                    PROCESS_OVERHEAD_GAS_ACCOUNT: {
                        OXYGEN_SPECIES: amount_mol
                    },
                }
                credits = {"process.cleaned_melt": {"FeO": fe_extent_mol}}
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
                    "m2_metal_reaction": True,
                    "metal_fe_mol_delta": 2.0 * dn_to_headspace_mol,
                    "feo_mol_delta": -2.0 * dn_to_headspace_mol,
                    "fe2o3_mol_delta": 0.0,
                },
            )

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
