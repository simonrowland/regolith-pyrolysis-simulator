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
PROCESS_CLEANED_MELT_ACCOUNT = "process.cleaned_melt"
RESERVOIR_FO2_BUFFER_ACCOUNT = "reservoir.fo2_buffer"
OXYGEN_SPECIES = "O2"
FERRIC_OXIDE_SPECIES = "Fe2O3"
FERROUS_OXIDE_SPECIES = "FeO"
TRANSITION_NAME = "oxygen_reservoir_exchange"


class BuiltinOxygenReservoirExchangeProvider(ChemistryProvider):
    """Melt-to-headspace O2 from the melt's own ferric inventory.

    The reverse leg is still a pure O2 move from overhead into
    ``reservoir.fo2_buffer``. The buffer is not an oxygen source for
    the forward leg.
    """

    name = "builtin-oxygen-reservoir-exchange"
    DECLARED_ACCOUNTS = frozenset({
        PROCESS_CLEANED_MELT_ACCOUNT,
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
            # Premise: melt-to-headspace O2 is the melt's own ferric iron,
            # Fe2O3 -> 2 FeO + 1/2 O2. dn mol O2 therefore removes 2*dn mol
            # Fe2O3 and adds 4*dn mol FeO in process.cleaned_melt, and
            # credits dn mol O2 to process.overhead_gas. It is not drawn
            # from reservoir.fo2_buffer.
            #
            # Atom check. Fe2O3 carries 2 Fe and 3 O; FeO carries 1 Fe and
            # 1 O; O2 carries 2 O.
            #   Fe: 2 * (2*dn) = 4*dn out, 4*dn in.
            #   O:  3 * (2*dn) = 6*dn out; 4*dn into FeO; 2*dn into O2.
            #
            # Sanity, dn = 1 mol. Remove 2 mol Fe2O3
            # (2 * 159.687 = 319.374 g) and add 4 mol FeO
            # (4 * 71.844 = 287.376 g) plus 1 mol O2 (31.998 g).
            # 287.376 + 31.998 = 319.374 g.
            #
            # Campaign sanity. N_Fe = 2505 mol and Δ(Fe3+/FeT) = 0.19558
            # give dn = N_Fe * Δf / 4 = 2505 * 0.19558 / 4 = 122.48 mol O2,
            # which removes 244.96 mol Fe2O3 and adds 489.93 mol FeO.
            # Δ(Fe3+/FeT) = -4*dn/N_Fe, the same oxygen the scalar step
            # Δ ln fO2 = -dn / C_m spends when C_m is the local derivative.
            fe2o3_mol = 2.0 * amount_mol
            feo_mol = 4.0 * amount_mol
            debits = {
                PROCESS_CLEANED_MELT_ACCOUNT: {FERRIC_OXIDE_SPECIES: fe2o3_mol}
            }
            credits = {
                PROCESS_CLEANED_MELT_ACCOUNT: {FERROUS_OXIDE_SPECIES: feo_mol},
                PROCESS_OVERHEAD_GAS_ACCOUNT: {OXYGEN_SPECIES: amount_mol},
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
