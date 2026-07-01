"""Builtin CA_ALUMINOTHERMIC_STEP provider for optional C7 Ca recovery."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from engines.builtin._common import (
    build_atom_balance_proof,
    diagnostic_control_audit,
    reject_wrong_intent,
    unpack_controls,
)
from simulator.account_ids import C7_AL_CREDIT_ACCOUNT
from simulator.chemistry.ellingham_thermo import ELLINGHAM_THERMO
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
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET


REACTION_FAMILY_C7_CA_ALUMINOTHERMIC = "c7_ca_aluminothermic"
C7_CAMPAIGN_NAME = "C7_CA_ALUMINOTHERMIC"
C7_DECISION_YES = "yes"
C7_MIN_TOTAL_PRESSURE_MBAR = 0.01
C7_MAX_TOTAL_PRESSURE_MBAR = 0.1
C7_DEFAULT_CONDENSER_TEMPERATURE_C = 780.0
C7_MIN_HOLD_TEMP_C = 1100.0
C7_MAX_HOLD_TEMP_C = 1300.0
C7_OBJECTIVES = frozenset(
    {
        "ree_enrichment",
        "ca_mass",
        "ca_reductant_recycle",
        "ceramic_target",
        "ree_export_purity",
    }
)
C7_ALUMINATE_MODES = frozenset({"C3A", "C12A7"})
C7_AL_SOURCE_ACCOUNTS = frozenset(
    {"process.metal_phase", C7_AL_CREDIT_ACCOUNT}
)


class BuiltinCaAluminothermicStepProvider(ChemistryProvider):
    """Authoritative provider for C7 Ca aluminothermic proposals."""

    name = "builtin-ca-aluminothermic-step"

    DECLARED_ACCOUNTS = frozenset(
        {
            "process.cleaned_melt",
            "process.metal_phase",
            C7_AL_CREDIT_ACCOUNT,
            "process.overhead_gas",
            "process.condensation_train",
            "process.wall_deposit",
            "terminal.slag",
        }
    )

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.CA_ALUMINOTHERMIC_STEP}),
            is_authoritative_for=frozenset(
                {ChemistryIntent.CA_ALUMINOTHERMIC_STEP}
            ),
            declared_accounts=self.DECLARED_ACCOUNTS,
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        from simulator.accounting.formulas import resolve_species_formula

        wrong_intent = reject_wrong_intent(
            request, ChemistryIntent.CA_ALUMINOTHERMIC_STEP
        )
        if wrong_intent is not None:
            return wrong_intent

        controls = unpack_controls(request)
        control_audit = diagnostic_control_audit(request, include_fO2=False)
        registry = request.account_view.species_formula_registry

        common_refusal = self._common_refusal(request, controls, control_audit)
        if common_refusal is not None:
            return common_refusal

        try:
            c3a_formula = resolve_species_formula("Ca3Al2O6", registry)
            c12a7_formula = resolve_species_formula("Ca12Al14O33", registry)
            ca_formula = resolve_species_formula("Ca", registry)
            al_formula = resolve_species_formula("Al", registry)
            cao_formula = resolve_species_formula("CaO", registry)
            al2o3_formula = resolve_species_formula("Al2O3", registry)
        except Exception as exc:  # noqa: BLE001
            return self._refused(
                "c7_formula_registry_missing",
                control_audit=control_audit,
                detail=str(exc),
            )

        operation = str(controls.get("operation") or "reduction")
        if operation == "ca_capture":
            return self._dispatch_capture(
                request,
                controls,
                control_audit,
                registry,
                resolve_species_formula,
                ca_formula,
            )
        if operation == "ca_shuttle_alumina_feedback":
            return self._dispatch_ca_shuttle_alumina_feedback(
                request,
                controls,
                control_audit,
                registry,
                resolve_species_formula,
                ca_formula,
                al_formula,
                cao_formula,
                al2o3_formula,
            )
        if operation != "reduction":
            return self._refused(
                "c7_unsupported_operation",
                control_audit=control_audit,
                operation=operation,
            )

        mode = str(controls.get("aluminate_mode") or "C3A").upper()
        if mode not in C7_ALUMINATE_MODES:
            return self._refused(
                "c7_unsupported_aluminate_mode",
                control_audit=control_audit,
                aluminate_mode=mode,
            )
        source_account = str(controls.get("al_source_account") or "")
        if source_account not in C7_AL_SOURCE_ACCOUNTS:
            return self._refused(
                "c7_invalid_al_source_account",
                control_audit=control_audit,
                al_source_account=source_account,
            )

        stoich = self._stoich(mode)
        extent_fraction_raw = _finite_float(
            controls.get("extent_fraction", 1.0), 1.0
        )
        extent_fraction = _clamp(extent_fraction_raw, 0.0, 1.0)
        r_objective = _finite_float(controls.get("objective_extent_mol"), 0.0)
        r_transport = _finite_float(controls.get("transport_extent_mol"), 0.0)
        if r_objective <= 0.0:
            return self._refused(
                "c7_objective_extent_not_positive",
                control_audit=control_audit,
                objective_extent_mol=r_objective,
            )
        if r_transport <= 0.0:
            return self._refused(
                "c7_transport_extent_not_positive",
                control_audit=control_audit,
                r_transport=r_transport,
            )

        accounts = request.account_view.accounts
        cao_available_by_account = {
            account: max(0.0, float(accounts.get(account, {}).get("CaO", 0.0)))
            for account in ("process.cleaned_melt", "terminal.slag")
        }
        cao_available = sum(cao_available_by_account.values())
        al_available = max(
            0.0, float(accounts.get(source_account, {}).get("Al", 0.0))
        )
        r_stoich_available = min(
            cao_available / stoich["CaO"],
            al_available / stoich["Al"],
        )
        r_recipe = extent_fraction * r_objective
        candidates = {
            "stoich": r_stoich_available,
            "transport": r_transport,
            "objective": r_objective,
            "recipe": r_recipe,
        }
        limiting_cap = min(candidates, key=candidates.get)
        r_c7 = max(0.0, min(candidates.values()))
        if r_c7 <= 0.0:
            return self._refused(
                "c7_no_positive_extent",
                control_audit=control_audit,
                limiting_cap=limiting_cap,
                **self._extent_diag(candidates, 0.0),
            )
        allow_partial = bool(controls.get("allow_partial_extent") or False)
        if not allow_partial and r_c7 + 1e-12 < r_objective:
            return self._refused(
                "c7_extent_below_objective",
                control_audit=control_audit,
                limiting_cap=limiting_cap,
                **self._extent_diag(candidates, r_c7),
            )

        cao_needed = stoich["CaO"] * r_c7
        al_needed = stoich["Al"] * r_c7
        cao_debits = self._cao_debits(cao_available_by_account, cao_needed)
        if sum(cao_debits.values()) + 1e-12 < cao_needed:
            return self._refused(
                "c7_cao_budget_below_extent",
                control_audit=control_audit,
                cao_required_mol=cao_needed,
                cao_available_mol=cao_available,
            )
        if al_needed > al_available + 1e-12:
            return self._refused(
                "c7_al_budget_below_extent",
                control_audit=control_audit,
                al_required_mol=al_needed,
                al_available_mol=al_available,
            )

        debits: dict[str, dict[str, float]] = {
            account: {"CaO": mol}
            for account, mol in cao_debits.items()
            if mol > 0.0
        }
        debits[source_account] = {"Al": al_needed}
        aluminate_species = stoich["aluminate_species"]
        ca_produced_mol = stoich["Ca"] * r_c7
        credits: dict[str, dict[str, float]] = {
            "process.overhead_gas": {"Ca": ca_produced_mol},
            "terminal.slag": {aluminate_species: r_c7},
        }
        atom_proof = build_atom_balance_proof(
            debits, credits, registry, resolve_species_formula
        )
        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason=f"ca_aluminothermic_{mode.lower()}_{self._source_label(source_account)}",
            atom_balance_proof=atom_proof,
        )
        aluminate_formula = (
            c3a_formula if aluminate_species == "Ca3Al2O6" else c12a7_formula
        )
        diagnostic = {
            "reaction_family": REACTION_FAMILY_C7_CA_ALUMINOTHERMIC,
            "operation": "reduction",
            "aluminate_mode": mode,
            "al_source_account": source_account,
            "limiting_cap": limiting_cap,
            "r_c7": r_c7,
            "cao_debits_mol": dict(cao_debits),
            "ca_overhead_mol": ca_produced_mol,
            "aluminate_species": aluminate_species,
            "calcium_aluminate_slag_kg": (
                r_c7 * aluminate_formula.molar_mass_kg_per_mol()
            ),
            "ca_metal_kg": ca_produced_mol * ca_formula.molar_mass_kg_per_mol(),
            "al_spend_kg": al_needed * al_formula.molar_mass_kg_per_mol(),
            "cao_removed_kg": cao_needed * cao_formula.molar_mass_kg_per_mol(),
            "c7_al_in_situ_drawn_mol": (
                al_needed if source_account == "process.metal_phase" else 0.0
            ),
            "c7_al_credit_drawn_mol": (
                al_needed if source_account == C7_AL_CREDIT_ACCOUNT else 0.0
            ),
            "c7_knob_saturation": self._knob_saturation(
                "extent_fraction", extent_fraction_raw, extent_fraction
            ),
        }
        diagnostic.update(self._extent_diag(candidates, r_c7))
        return IntentResult(
            intent=ChemistryIntent.CA_ALUMINOTHERMIC_STEP,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic=diagnostic,
        )

    def _common_refusal(
        self,
        request: IntentRequest,
        controls: Mapping[str, Any],
        control_audit: Any,
    ) -> IntentResult | None:
        family = str(controls.get("reaction_family") or "")
        if family != REACTION_FAMILY_C7_CA_ALUMINOTHERMIC:
            return self._refused(
                "c7_invalid_reaction_family",
                control_audit=control_audit,
                reaction_family=family,
            )
        campaign = str(controls.get("campaign") or "")
        if campaign != C7_CAMPAIGN_NAME:
            return self._refused(
                "c7_wrong_campaign",
                control_audit=control_audit,
                campaign=campaign,
            )
        decision = str(controls.get("decision") or "").lower()
        if decision not in {C7_DECISION_YES, "true", "1", "c7_proceed"}:
            return self._refused(
                "c7_decision_not_proceed",
                control_audit=control_audit,
                decision=decision,
            )
        operation = str(controls.get("operation") or "reduction")
        expected_reductant = "Ca" if operation == "ca_shuttle_alumina_feedback" else "Al"
        if str(controls.get("reductant_species") or "") != expected_reductant:
            return self._refused(
                (
                    "c7_ca_shuttle_reductant_not_ca"
                    if operation == "ca_shuttle_alumina_feedback"
                    else "c7_reductant_not_al"
                ),
                control_audit=control_audit,
                reductant_species=str(controls.get("reductant_species") or ""),
            )
        objective = str(controls.get("objective") or "ree_enrichment")
        if objective not in C7_OBJECTIVES:
            return self._refused(
                "c7_unsupported_objective",
                control_audit=control_audit,
                objective=objective,
            )
        hold_temp_C = _finite_float(
            controls.get("hold_temp_C"), float(request.temperature_C)
        )
        if hold_temp_C < C7_MIN_HOLD_TEMP_C or hold_temp_C > C7_MAX_HOLD_TEMP_C:
            return self._refused(
                "c7_hold_temperature_outside_envelope",
                control_audit=control_audit,
                hold_temp_C=hold_temp_C,
            )
        p_total_mbar = _finite_float(
            controls.get("p_total_mbar"), float(request.pressure_bar) * 1000.0
        )
        if (
            p_total_mbar < C7_MIN_TOTAL_PRESSURE_MBAR
            or p_total_mbar > C7_MAX_TOTAL_PRESSURE_MBAR
        ):
            return self._refused(
                "c7_total_pressure_outside_vacuum_envelope",
                control_audit=control_audit,
                p_total_mbar=p_total_mbar,
            )
        pO2_mbar = _finite_float(controls.get("pO2_mbar"), 0.0)
        if pO2_mbar < 0.0 or pO2_mbar >= p_total_mbar:
            return self._refused(
                "c7_po2_outside_vacuum_envelope",
                control_audit=control_audit,
                pO2_mbar=pO2_mbar,
                p_total_mbar=p_total_mbar,
            )
        if not self._has_dedicated_ca_route(controls):
            return self._refused(
                "c7_no_active_dedicated_ca_condensation_route",
                control_audit=control_audit,
            )
        configured_thermo_margin = _finite_float(
            controls.get("thermo_margin_kj_per_mol_o2"), float("nan")
        )
        computed_thermo_margin = self._computed_thermo_margin_kj_per_mol_o2(
            hold_temp_C
        )
        if not (
            math.isfinite(computed_thermo_margin)
            and computed_thermo_margin > 0.0
        ):
            return self._refused(
                "c7_vacuum_shifted_thermo_margin_unfavorable",
                control_audit=control_audit,
                thermo_margin_kj_per_mol_o2=computed_thermo_margin,
                computed_thermo_margin_kj_per_mol_o2=computed_thermo_margin,
                configured_thermo_margin_kj_per_mol_o2=configured_thermo_margin,
                configured_thermo_margin_favorable=bool(
                    controls.get("thermo_margin_favorable") or False
                ),
                thermo_margin_source="builtin_janaf_ellingham_al_ca",
            )
        return None

    @classmethod
    def _computed_thermo_margin_kj_per_mol_o2(
        cls,
        hold_temp_C: float,
    ) -> float:
        return cls._ellingham_delta_g_kj_per_mol_o2(
            "Ca", hold_temp_C
        ) - cls._ellingham_delta_g_kj_per_mol_o2("Al", hold_temp_C)

    @staticmethod
    def _ellingham_delta_g_kj_per_mol_o2(
        metal: str,
        temperature_C: float,
    ) -> float:
        # ELLINGHAM_THERMO is the existing JANAF high-T refit table; keep the
        # C7 gate on computed table values, not caller-provided scalar signs.
        dH_f, dS_f, _n_M, _n_ox = ELLINGHAM_THERMO[metal]
        return dH_f - (
            (float(temperature_C) + CELSIUS_TO_KELVIN_OFFSET) * dS_f
        )

    def _dispatch_capture(
        self,
        request: IntentRequest,
        controls: Mapping[str, Any],
        control_audit: Any,
        registry: Mapping[str, Any],
        resolve_species_formula,
        ca_formula: Any,
    ) -> IntentResult:
        accounts = request.account_view.accounts
        available_ca_mol = max(
            0.0, float(accounts.get("process.overhead_gas", {}).get("Ca", 0.0))
        )
        requested_capture_mol = _finite_float(
            controls.get("capture_mol"), available_ca_mol
        )
        capture_fraction_raw = _finite_float(
            controls.get("capture_fraction", 1.0), 1.0
        )
        capture_fraction = _clamp(capture_fraction_raw, 0.0, 1.0)
        capture_capacity_mol = min(
            available_ca_mol, max(0.0, requested_capture_mol) * capture_fraction
        )
        wall_deposit_mol = 0.0
        if bool(controls.get("route_uncaptured_to_wall") or False):
            wall_deposit_mol = max(0.0, available_ca_mol - capture_capacity_mol)
        debit_mol = capture_capacity_mol + wall_deposit_mol
        if debit_mol <= 0.0:
            return self._refused(
                "c7_no_ca_overhead_to_capture",
                control_audit=control_audit,
                available_ca_mol=available_ca_mol,
            )
        debits = {"process.overhead_gas": {"Ca": debit_mol}}
        credits: dict[str, dict[str, float]] = {}
        if capture_capacity_mol > 0.0:
            credits["process.condensation_train"] = {"Ca": capture_capacity_mol}
        if wall_deposit_mol > 0.0:
            credits["process.wall_deposit"] = {"Ca": wall_deposit_mol}
        atom_proof = build_atom_balance_proof(
            debits, credits, registry, resolve_species_formula
        )
        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason="ca_condenser_capture",
            atom_balance_proof=atom_proof,
        )
        return IntentResult(
            intent=ChemistryIntent.CA_ALUMINOTHERMIC_STEP,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic={
                "reaction_family": REACTION_FAMILY_C7_CA_ALUMINOTHERMIC,
                "operation": "ca_capture",
                "available_ca_mol": available_ca_mol,
                "captured_ca_mol": capture_capacity_mol,
                "wall_deposit_ca_mol": wall_deposit_mol,
                "ca_metal_captured_kg": (
                    capture_capacity_mol * ca_formula.molar_mass_kg_per_mol()
                ),
                "ca_uncaptured_wall_deposit_kg": (
                    wall_deposit_mol * ca_formula.molar_mass_kg_per_mol()
                ),
                "ca_condenser_temperature_C": _finite_float(
                    controls.get("ca_condenser_temperature_C"),
                    C7_DEFAULT_CONDENSER_TEMPERATURE_C,
                ),
                "c7_knob_saturation": self._knob_saturation(
                    "capture_fraction", capture_fraction_raw, capture_fraction
                ),
            },
        )

    def _dispatch_ca_shuttle_alumina_feedback(
        self,
        request: IntentRequest,
        controls: Mapping[str, Any],
        control_audit: Any,
        registry: Mapping[str, Any],
        resolve_species_formula,
        ca_formula: Any,
        al_formula: Any,
        cao_formula: Any,
        al2o3_formula: Any,
    ) -> IntentResult:
        targets = controls.get("ca_shuttle_targets") or ("Al2O3",)
        if isinstance(targets, str):
            targets = (targets,)
        supported_targets = tuple(str(target) for target in targets)
        if "Al2O3" not in supported_targets:
            return self._refused(
                "c7_ca_shuttle_no_supported_targets",
                control_audit=control_audit,
                ca_shuttle_targets=supported_targets,
            )
        accounts = request.account_view.accounts
        available_ca_mol = max(
            0.0, float(accounts.get("process.condensation_train", {}).get("Ca", 0.0))
        )
        captured_ca_mol = _finite_float(
            controls.get("captured_ca_mol"), available_ca_mol
        )
        reserve_raw = _finite_float(
            controls.get("ca_shuttle_reserve_ca_product_fraction"), 1.0
        )
        reserve_fraction = _clamp(reserve_raw, 0.0, 1.0)
        rate_raw = _finite_float(controls.get("ca_shuttle_rate_fraction"), 0.0)
        rate_fraction = _clamp(rate_raw, 0.0, 1.0)
        reserved_ca_mol = min(available_ca_mol, max(0.0, captured_ca_mol) * reserve_fraction)
        surplus_ca_mol = max(0.0, available_ca_mol - reserved_ca_mol)
        rate_capped_ca_mol = surplus_ca_mol * rate_fraction
        al2o3_available_by_account = {
            account: max(0.0, float(accounts.get(account, {}).get("Al2O3", 0.0)))
            for account in ("process.cleaned_melt", "terminal.slag")
        }
        al2o3_available = sum(al2o3_available_by_account.values())
        extent = min(rate_capped_ca_mol / 3.0, al2o3_available)
        if extent <= 0.0:
            return self._refused(
                "c7_ca_shuttle_no_surplus_extent",
                control_audit=control_audit,
                available_ca_mol=available_ca_mol,
                reserved_product_ca_mol=reserved_ca_mol,
                surplus_ca_mol=surplus_ca_mol,
                ca_shuttle_rate_fraction=rate_fraction,
                al2o3_available_mol=al2o3_available,
            )
        ca_drawn_mol = 3.0 * extent
        al2o3_debits = self._al2o3_debits(al2o3_available_by_account, extent)
        if sum(al2o3_debits.values()) + 1e-12 < extent:
            return self._refused(
                "c7_ca_shuttle_alumina_budget_below_extent",
                control_audit=control_audit,
                al2o3_required_mol=extent,
                al2o3_available_mol=al2o3_available,
            )
        debits: dict[str, dict[str, float]] = {
            "process.condensation_train": {"Ca": ca_drawn_mol}
        }
        for account, mol in al2o3_debits.items():
            debits[account] = {"Al2O3": mol}
        credits = {
            "process.cleaned_melt": {"CaO": ca_drawn_mol},
            "process.metal_phase": {"Al": 2.0 * extent},
        }
        atom_proof = build_atom_balance_proof(
            debits, credits, registry, resolve_species_formula
        )
        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason="ca_shuttle_alumina_feedback",
            atom_balance_proof=atom_proof,
        )
        saturation = []
        saturation.extend(
            self._knob_saturation(
                "ca_shuttle.reserve_ca_product_fraction",
                reserve_raw,
                reserve_fraction,
            )
        )
        saturation.extend(
            self._knob_saturation(
                "ca_shuttle.rate_fraction",
                rate_raw,
                rate_fraction,
            )
        )
        return IntentResult(
            intent=ChemistryIntent.CA_ALUMINOTHERMIC_STEP,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic={
                "reaction_family": REACTION_FAMILY_C7_CA_ALUMINOTHERMIC,
                "operation": "ca_shuttle_alumina_feedback",
                "ca_shuttle_targets": supported_targets,
                "available_ca_mol": available_ca_mol,
                "reserved_product_ca_mol": reserved_ca_mol,
                "shuttle_drawn_ca_mol": ca_drawn_mol,
                "unused_surplus_ca_mol": max(0.0, surplus_ca_mol - ca_drawn_mol),
                "al2o3_debits_mol": dict(al2o3_debits),
                "al_recovered_mol": 2.0 * extent,
                "al_recovered_kg": 2.0 * extent * al_formula.molar_mass_kg_per_mol(),
                "cao_returned_kg": ca_drawn_mol * cao_formula.molar_mass_kg_per_mol(),
                "ca_shuttle_drawn_kg": ca_drawn_mol * ca_formula.molar_mass_kg_per_mol(),
                "alumina_consumed_kg": extent * al2o3_formula.molar_mass_kg_per_mol(),
                "ca_shuttle_rate_fraction": rate_fraction,
                "ca_shuttle_reserve_ca_product_fraction": reserve_fraction,
                "c7_knob_saturation": saturation,
            },
        )

    @staticmethod
    def _stoich(mode: str) -> dict[str, Any]:
        if mode == "C12A7":
            return {
                "CaO": 33.0,
                "Al": 14.0,
                "Ca": 21.0,
                "aluminate_species": "Ca12Al14O33",
            }
        return {
            "CaO": 6.0,
            "Al": 2.0,
            "Ca": 3.0,
            "aluminate_species": "Ca3Al2O6",
        }

    @staticmethod
    def _cao_debits(
        available_by_account: Mapping[str, float],
        required_mol: float,
    ) -> dict[str, float]:
        remaining = max(0.0, float(required_mol))
        debits: dict[str, float] = {}
        for account in ("process.cleaned_melt", "terminal.slag"):
            draw = min(max(0.0, float(available_by_account.get(account, 0.0))), remaining)
            if draw > 0.0:
                debits[account] = draw
                remaining -= draw
            if remaining <= 1e-12:
                break
        return debits

    @staticmethod
    def _al2o3_debits(
        available_by_account: Mapping[str, float],
        required_mol: float,
    ) -> dict[str, float]:
        remaining = max(0.0, float(required_mol))
        debits: dict[str, float] = {}
        for account in ("process.cleaned_melt", "terminal.slag"):
            draw = min(max(0.0, float(available_by_account.get(account, 0.0))), remaining)
            if draw > 0.0:
                debits[account] = draw
                remaining -= draw
            if remaining <= 1e-12:
                break
        return debits

    @staticmethod
    def _has_dedicated_ca_route(controls: Mapping[str, Any]) -> bool:
        if not bool(controls.get("active_ca_condensation_route") or False):
            return False
        if str(controls.get("ca_condensation_species") or "Ca") != "Ca":
            return False
        if not bool(controls.get("dedicated_ca_condenser") or False):
            return False
        condenser_temp = _finite_float(
            controls.get("ca_condenser_temperature_C"),
            C7_DEFAULT_CONDENSER_TEMPERATURE_C,
        )
        return abs(condenser_temp - C7_DEFAULT_CONDENSER_TEMPERATURE_C) <= 50.0

    @staticmethod
    def _source_label(account: str) -> str:
        return "credit_al" if account == C7_AL_CREDIT_ACCOUNT else "in_situ_al"

    @staticmethod
    def _extent_diag(candidates: Mapping[str, float], r_c7: float) -> dict[str, float]:
        return {
            "r_stoich_available": float(candidates.get("stoich", 0.0)),
            "r_transport": float(candidates.get("transport", 0.0)),
            "r_objective": float(candidates.get("objective", 0.0)),
            "r_recipe": float(candidates.get("recipe", 0.0)),
            "r_c7": float(r_c7),
        }

    @staticmethod
    def _knob_saturation(path: str, requested: float, applied: float) -> list[dict[str, Any]]:
        return [
            {
                "path": f"campaigns.C7.{path}",
                "requested": requested,
                "applied": applied,
                "reason": "clamped_to_supported_envelope",
                "saturated": not math.isclose(
                    float(requested),
                    float(applied),
                    rel_tol=0.0,
                    abs_tol=1e-15,
                ),
            }
        ]

    @staticmethod
    def _refused(
        reason: str,
        *,
        control_audit: Any,
        **diagnostic: Any,
    ) -> IntentResult:
        payload = {
            "reason": reason,
            "reason_refused": reason,
            "reaction_family": REACTION_FAMILY_C7_CA_ALUMINOTHERMIC,
        }
        payload.update(diagnostic)
        return IntentResult(
            intent=ChemistryIntent.CA_ALUMINOTHERMIC_STEP,
            status="refused",
            transition=None,
            control_audit=control_audit,
            diagnostic=payload,
        )


def _finite_float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))
