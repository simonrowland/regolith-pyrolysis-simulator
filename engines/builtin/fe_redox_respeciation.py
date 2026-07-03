"""Builtin FeO/Fe2O3 re-speciation provider."""

from __future__ import annotations

import math

from engines.builtin._common import (
    build_atom_balance_proof,
    composition_wt_pct_from_account_view,
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
from simulator.fe_redox import (
    floor_vacuum_pressure_bar,
    kress91_fe3_over_sigma_fe,
    melt_mol_fractions_for_kress91,
)


CLEANED_MELT_ACCOUNT = "process.cleaned_melt"
OXYGEN_ACCOUNT = "process.overhead_gas"
FO2_BUFFER_ACCOUNT = "reservoir.fo2_buffer"
OXYGEN_SPECIES = "O2"
OXYGEN_SOURCE_OVERHEAD = "overhead_gas"
OXYGEN_SOURCE_INTERNAL_EVAPORATIVE_METAL_LOSS = (
    "evaporative_metal_loss_internal"
)
NOOP_MOL = 1.0e-12
TRANSITION_NAME = "fe_redox_respeciation"


class BuiltinFeRedoxRespeciationProvider(ChemistryProvider):
    """Authoritative ``FE_REDOX_RESPECIATION`` provider."""

    name = "builtin-fe-redox-respeciation"
    DECLARED_ACCOUNTS = frozenset({
        CLEANED_MELT_ACCOUNT,
        OXYGEN_ACCOUNT,
        FO2_BUFFER_ACCOUNT,
    })

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.FE_REDOX_RESPECIATION}),
            is_authoritative_for=frozenset({
                ChemistryIntent.FE_REDOX_RESPECIATION,
            }),
            declared_accounts=self.DECLARED_ACCOUNTS,
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        """Dispatch FeO/Fe2O3 re-speciation.

        Partial funding returns ``status='ok'`` with ``unfunded_o2_mol``;
        consumers must key on ``unfunded_o2_mol``, not ``status`` alone.
        """

        from simulator.accounting.formulas import resolve_species_formula

        wrong_intent = reject_wrong_intent(
            request,
            ChemistryIntent.FE_REDOX_RESPECIATION,
        )
        if wrong_intent is not None:
            return wrong_intent

        control_audit = diagnostic_control_audit(request, include_fO2=True)
        controls = unpack_controls(request)
        oxygen_source = str(controls.get("oxygen_source") or OXYGEN_SOURCE_OVERHEAD)
        if oxygen_source not in {
            OXYGEN_SOURCE_OVERHEAD,
            OXYGEN_SOURCE_INTERNAL_EVAPORATIVE_METAL_LOSS,
        }:
            raise ValueError(f"unsupported Fe redox oxygen_source {oxygen_source!r}")
        try:
            internal_o2_capacity_mol = max(
                0.0,
                float(controls.get("internal_o2_capacity_mol", 0.0) or 0.0),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("internal_o2_capacity_mol must be finite") from exc
        if not math.isfinite(internal_o2_capacity_mol):
            raise ValueError("internal_o2_capacity_mol must be finite")
        o2_account = (
            FO2_BUFFER_ACCOUNT
            if oxygen_source == OXYGEN_SOURCE_INTERNAL_EVAPORATIVE_METAL_LOSS
            else OXYGEN_ACCOUNT
        )
        if request.fO2_log is None:
            raise ValueError("FE_REDOX_RESPECIATION requires fO2_log")
        fO2_log = float(request.fO2_log)
        T_K = float(request.temperature_C) + 273.15
        pressure_bar = floor_vacuum_pressure_bar(float(request.pressure_bar))
        if not math.isfinite(fO2_log):
            raise ValueError(f"fO2_log must be finite, got {request.fO2_log!r}")

        accounts = request.account_view.accounts
        cleaned_melt = dict(accounts.get(CLEANED_MELT_ACCOUNT, {}) or {})
        feo_mol = max(0.0, float(cleaned_melt.get("FeO", 0.0) or 0.0))
        fe2o3_mol = max(0.0, float(cleaned_melt.get("Fe2O3", 0.0) or 0.0))
        total_fe_mol = feo_mol + 2.0 * fe2o3_mol
        if total_fe_mol <= NOOP_MOL:
            return IntentResult(
                intent=ChemistryIntent.FE_REDOX_RESPECIATION,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "respeciation_status": "no_oxidized_iron",
                    "direction": "none",
                    "current_ferric_fraction": 0.0,
                    "target_ferric_fraction": 0.0,
                    "o2_account": o2_account,
                    "oxygen_source": oxygen_source,
                },
            )

        comp_wt = composition_wt_pct_from_account_view(
            request.account_view,
            CLEANED_MELT_ACCOUNT,
        )
        mol_fractions = melt_mol_fractions_for_kress91(comp_wt)
        if not mol_fractions:
            return IntentResult(
                intent=ChemistryIntent.FE_REDOX_RESPECIATION,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "respeciation_status": "no_kress91_basis",
                    "direction": "none",
                    "current_ferric_fraction": (
                        2.0 * fe2o3_mol / total_fe_mol
                    ),
                    "target_ferric_fraction": 0.0,
                    "o2_account": o2_account,
                    "oxygen_source": oxygen_source,
                },
            )

        target_ferric = max(
            0.0,
            min(
                1.0,
                kress91_fe3_over_sigma_fe(
                    fO2_log=fO2_log,
                    mol_fractions=mol_fractions,
                    T_K=T_K,
                    pressure_bar=pressure_bar,
                ),
            ),
        )
        current_ferric = 2.0 * fe2o3_mol / total_fe_mol
        target_fe2o3_mol = 0.5 * target_ferric * total_fe_mol
        delta_fe2o3_mol = target_fe2o3_mol - fe2o3_mol
        diagnostic = {
            "respeciation_status": "ok",
            "current_ferric_fraction": current_ferric,
            "target_ferric_fraction": target_ferric,
            "current_feo_mol": feo_mol,
            "current_fe2o3_mol": fe2o3_mol,
            "target_feo_mol": max(0.0, total_fe_mol - 2.0 * target_fe2o3_mol),
            "target_fe2o3_mol": target_fe2o3_mol,
            "delta_fe2o3_mol": delta_fe2o3_mol,
            "o2_account": o2_account,
            "oxygen_source": oxygen_source,
            "internal_o2_capacity_mol": internal_o2_capacity_mol,
            "source": str(
                controls.get("source") or "Kress91 scalar fO2 ledger re-speciation"
            ),
        }
        if abs(delta_fe2o3_mol) <= NOOP_MOL:
            return IntentResult(
                intent=ChemistryIntent.FE_REDOX_RESPECIATION,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic={**diagnostic, "direction": "none"},
            )

        registry = request.account_view.species_formula_registry
        if delta_fe2o3_mol > 0.0:
            required_o2_mol = 0.5 * delta_fe2o3_mol
            if oxygen_source == OXYGEN_SOURCE_INTERNAL_EVAPORATIVE_METAL_LOSS:
                available_o2_mol = internal_o2_capacity_mol
                applied_delta_fe2o3_mol = min(
                    delta_fe2o3_mol,
                    0.5 * feo_mol,
                    2.0 * available_o2_mol,
                )
            else:
                available_o2_mol = max(
                    0.0,
                    float(
                        (accounts.get(OXYGEN_ACCOUNT, {}) or {}).get(
                            OXYGEN_SPECIES,
                            0.0,
                        )
                        or 0.0
                    ),
                )
                applied_delta_fe2o3_mol = delta_fe2o3_mol
            feo_debit_mol = 2.0 * applied_delta_fe2o3_mol
            o2_debit_mol = 0.5 * applied_delta_fe2o3_mol
            if (
                applied_delta_fe2o3_mol <= NOOP_MOL
                or feo_debit_mol > feo_mol + NOOP_MOL
                or o2_debit_mol > available_o2_mol + NOOP_MOL
            ):
                return IntentResult(
                    intent=ChemistryIntent.FE_REDOX_RESPECIATION,
                    status="refused",
                    transition=None,
                    control_audit=control_audit,
                    diagnostic={
                        **diagnostic,
                        "respeciation_status": "refused",
                        "direction": "oxidizing",
                        "reason": (
                            (
                                "fe_redox_respeciation_internal_o_unavailable"
                                if (
                                    oxygen_source
                                    == OXYGEN_SOURCE_INTERNAL_EVAPORATIVE_METAL_LOSS
                                )
                                else "fe_redox_respeciation_o2_unavailable"
                            )
                            if (
                                applied_delta_fe2o3_mol <= NOOP_MOL
                                or o2_debit_mol > available_o2_mol + NOOP_MOL
                            )
                            else "fe_redox_respeciation_feo_unavailable"
                        ),
                        "required_o2_mol": required_o2_mol,
                        "available_o2_mol": available_o2_mol,
                        "applied_o2_mol": 0.0,
                        "unfunded_o2_mol": required_o2_mol,
                    },
                )
            partial = applied_delta_fe2o3_mol < delta_fe2o3_mol - NOOP_MOL
            debits = {
                CLEANED_MELT_ACCOUNT: {"FeO": feo_debit_mol},
                o2_account: {OXYGEN_SPECIES: o2_debit_mol},
            }
            credits = {
                CLEANED_MELT_ACCOUNT: {"Fe2O3": applied_delta_fe2o3_mol},
            }
            direction = "oxidizing"
            o2_debit = o2_debit_mol
            o2_credit = 0.0
            diagnostic.update({
                "respeciation_status": "partial" if partial else "ok",
                "applied_delta_fe2o3_mol": applied_delta_fe2o3_mol,
                "applied_o2_mol": o2_debit_mol,
                "required_o2_mol": required_o2_mol,
                "available_o2_mol": available_o2_mol,
                "unfunded_o2_mol": max(0.0, required_o2_mol - o2_debit_mol),
            })
        else:
            fe2o3_debit_mol = -delta_fe2o3_mol
            if fe2o3_debit_mol > fe2o3_mol + NOOP_MOL:
                return IntentResult(
                    intent=ChemistryIntent.FE_REDOX_RESPECIATION,
                    status="refused",
                    transition=None,
                    control_audit=control_audit,
                    diagnostic={
                        **diagnostic,
                        "respeciation_status": "refused",
                        "direction": "reducing",
                        "reason": "fe_redox_respeciation_fe2o3_unavailable",
                    },
                )
            debits = {CLEANED_MELT_ACCOUNT: {"Fe2O3": fe2o3_debit_mol}}
            credits = {
                CLEANED_MELT_ACCOUNT: {"FeO": 2.0 * fe2o3_debit_mol},
                o2_account: {OXYGEN_SPECIES: 0.5 * fe2o3_debit_mol},
            }
            direction = "reducing"
            o2_debit = 0.0
            o2_credit = 0.5 * fe2o3_debit_mol

        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason=TRANSITION_NAME,
            atom_balance_proof=build_atom_balance_proof(
                debits,
                credits,
                registry,
                resolve_species_formula,
            ),
        )
        return IntentResult(
            intent=ChemistryIntent.FE_REDOX_RESPECIATION,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic={
                **diagnostic,
            "direction": direction,
            "o2_debit_mol": o2_debit,
            "o2_credit_mol": o2_credit,
            },
        )
