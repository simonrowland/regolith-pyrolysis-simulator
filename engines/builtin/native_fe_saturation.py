"""Builtin native-Fe saturation provider.

Routes the pre-0.6 storm FeO -> Fe + 0.5 O2 split through the chemistry
kernel without changing the saturation physics that computes the extent.
Native Fe vapor is only budgeted here; the Fe vapor ledger leg is routed
through EVAPORATION_TRANSITION -> CONDENSATION_ROUTE with every other metal
vapor. Its optional per-oxide extent map also commits the FeO/NiO metal buffer
selected by the simulator's redox update.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

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

    CLEANED_MELT_ACCOUNT = "process.cleaned_melt"
    OVERHEAD_GAS_ACCOUNT = "process.overhead_gas"
    SUPPORTED_OXIDES = frozenset({"FeO", "NiO"})

    def capability_profile(self) -> CapabilityProfile:
        from simulator.account_ids import (
            OXYGEN_RESERVOIR_FO2_BUFFER_ACCOUNT,
            TERMINAL_DRAIN_TAP_ACCOUNT,
        )

        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.NATIVE_FE_SATURATION}),
            is_authoritative_for=frozenset(
                {ChemistryIntent.NATIVE_FE_SATURATION}
            ),
            declared_accounts=frozenset({
                self.CLEANED_MELT_ACCOUNT,
                TERMINAL_DRAIN_TAP_ACCOUNT,
                self.OVERHEAD_GAS_ACCOUNT,
                OXYGEN_RESERVOIR_FO2_BUFFER_ACCOUNT,
            }),
            consumes_fO2=False,
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        from simulator.accounting.formulas import resolve_species_formula
        from simulator.chemistry.ellingham_graph import (
            ellingham_oxide_stoichiometry_for_formula,
        )
        from simulator.account_ids import (
            OXYGEN_RESERVOIR_FO2_BUFFER_ACCOUNT as FO2_BUFFER_ACCOUNT,
            TERMINAL_DRAIN_TAP_ACCOUNT as DRAIN_TAP_ACCOUNT,
        )
        from simulator.scalar_boundary import is_declared_real_scalar

        wrong_intent = reject_wrong_intent(
            request, ChemistryIntent.NATIVE_FE_SATURATION
        )
        if wrong_intent is not None:
            return wrong_intent

        controls = unpack_controls(request)
        control_audit = diagnostic_control_audit(request, include_fO2=False)

        def refused(reason: str, **details: object) -> IntentResult:
            return IntentResult(
                intent=ChemistryIntent.NATIVE_FE_SATURATION,
                status="refused",
                diagnostic={"reason": reason, **details},
                control_audit=control_audit,
            )

        def finite_mol(value: object) -> float | None:
            if not is_declared_real_scalar(value, allow_numeric_str=True):
                return None
            try:
                amount = float(value)
            except (TypeError, ValueError, OverflowError):
                return None
            return amount if math.isfinite(amount) else None

        has_extent_map = "metal_extent_mol_by_oxide" in controls
        if has_extent_map and "native_fe_mol" in controls:
            return refused("ambiguous_metal_extent_inputs")
        if has_extent_map:
            raw_extents = controls.get("metal_extent_mol_by_oxide")
            if not isinstance(raw_extents, Mapping):
                return refused("invalid_metal_extent_map")
            extent_by_oxide = dict(raw_extents)
            vapor_raw = controls.get("native_fe_vapor_mol", 0.0)
            vapor_amount = finite_mol(vapor_raw)
            if vapor_amount is None or vapor_amount != 0.0:
                return refused("native_fe_vapor_not_supported_with_extent_map")
            native_fe_vapor_mol = 0.0
        else:
            native_fe_raw = controls.get("native_fe_mol", 0.0)
            vapor_raw = controls.get("native_fe_vapor_mol", 0.0)
            native_fe_mol = finite_mol(native_fe_raw)
            if native_fe_mol is None:
                return refused("invalid_native_fe_extent")
            native_fe_vapor_mol = finite_mol(vapor_raw)
            if native_fe_vapor_mol is None:
                return refused("invalid_native_fe_vapor_extent")
            if native_fe_mol < 0.0 or native_fe_vapor_mol < 0.0:
                return refused(
                    "negative_metal_extent",
                    native_fe_mol=native_fe_mol,
                    native_fe_vapor_mol=native_fe_vapor_mol,
                )
            if native_fe_vapor_mol > native_fe_mol + 1.0e-12:
                return refused(
                    "native_fe_vapor_exceeds_native_fe_extent",
                    native_fe_mol=native_fe_mol,
                    native_fe_vapor_mol=native_fe_vapor_mol,
                )
            native_fe_vapor_mol = min(native_fe_mol, native_fe_vapor_mol)
            extent_by_oxide = {
                "FeO": native_fe_mol - native_fe_vapor_mol,
            }

        raw_oxygen_account = controls.get(
            "oxygen_account",
            self.OVERHEAD_GAS_ACCOUNT,
        )
        if not isinstance(raw_oxygen_account, str):
            return refused("invalid_oxygen_account")
        oxygen_account = raw_oxygen_account
        if oxygen_account not in {
            self.OVERHEAD_GAS_ACCOUNT,
            FO2_BUFFER_ACCOUNT,
        }:
            return refused(
                "invalid_oxygen_account",
                oxygen_account=oxygen_account,
            )

        cleaned_melt = dict(
            request.account_view.accounts.get(self.CLEANED_MELT_ACCOUNT, {}) or {}
        )
        debit_oxides: dict[str, float] = {}
        tap_metals: dict[str, float] = {}
        oxygen_mol = 0.0
        metal_extent_by_oxide: dict[str, float] = {}
        for raw_oxide, raw_extent in extent_by_oxide.items():
            oxide = str(raw_oxide)
            if oxide not in self.SUPPORTED_OXIDES:
                return refused("unsupported_oxide", oxide=oxide)
            metal_extent_mol = finite_mol(raw_extent)
            if metal_extent_mol is None:
                return refused("invalid_metal_extent", oxide=oxide)
            if metal_extent_mol < 0.0:
                return refused(
                    "negative_metal_extent",
                    oxide=oxide,
                    metal_extent_mol=metal_extent_mol,
                )
            if metal_extent_mol <= 1.0e-12:
                continue
            formula_entry = request.account_view.species_formula_registry.get(
                oxide
            )
            if formula_entry is None:
                return refused("unsupported_oxide_formula", oxide=oxide)
            elements = resolve_species_formula(
                oxide,
                request.account_view.species_formula_registry,
            ).elements
            metal = oxide.removesuffix("O")
            formula_metal_mol = float(elements.get(metal, 0.0) or 0.0)
            formula_oxygen_mol = float(elements.get("O", 0.0) or 0.0)
            if (
                set(elements) != {metal, "O"}
                or not math.isclose(
                    formula_metal_mol, 1.0, rel_tol=0.0, abs_tol=1.0e-9
                )
                or not math.isclose(
                    formula_oxygen_mol, 1.0, rel_tol=0.0, abs_tol=1.0e-9
                )
            ):
                return refused("oxide_formula_mismatch", oxide=oxide)
            oxide_stoich = (metal, formula_metal_mol, formula_oxygen_mol)
            ell_stoich = ellingham_oxide_stoichiometry_for_formula(
                oxide,
                oxide_to_metal={oxide: oxide_stoich},
            )
            if ell_stoich is None:
                return refused("unsupported_oxide_formula", oxide=oxide)

            n_metal_per_o2, n_oxide_per_o2 = ell_stoich
            oxygen_for_metal_mol = metal_extent_mol / n_metal_per_o2
            oxide_debit_mol = oxygen_for_metal_mol * n_oxide_per_o2
            oxide_available_mol = max(
                0.0,
                float(cleaned_melt.get(oxide, 0.0) or 0.0),
            )
            if oxide_debit_mol > oxide_available_mol + 1.0e-12:
                return refused(
                    "oxide_inventory_exceeded",
                    oxide=oxide,
                    metal_extent_mol=metal_extent_mol,
                    oxide_debit_mol=oxide_debit_mol,
                    oxide_available_mol=oxide_available_mol,
                )
            debit_oxides[oxide] = oxide_debit_mol
            tap_metals[metal] = tap_metals.get(metal, 0.0) + metal_extent_mol
            metal_extent_by_oxide[oxide] = metal_extent_mol
            oxygen_mol += oxygen_for_metal_mol

        debits = (
            {self.CLEANED_MELT_ACCOUNT: debit_oxides}
            if debit_oxides else {}
        )
        credits: dict[str, dict[str, float]] = {}
        if tap_metals:
            credits[DRAIN_TAP_ACCOUNT] = tap_metals
        if oxygen_mol > 1.0e-12:
            credits[oxygen_account] = {"O2": oxygen_mol}
        if not debits and not credits:
            return IntentResult(
                intent=ChemistryIntent.NATIVE_FE_SATURATION,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "native_fe_mol": 0.0,
                    "native_fe_vapor_mol": native_fe_vapor_mol,
                    "native_fe_tap_mol": 0.0,
                    "feo_debit_mol": 0.0,
                    "tap_fe_credit_mol": 0.0,
                    "overhead_fe_credit_mol": 0.0,
                    "routed_fe_vapor_mol": native_fe_vapor_mol,
                    "overhead_o2_credit_mol": 0.0,
                    "buffer_o2_credit_mol": 0.0,
                    "oxygen_account": oxygen_account,
                    "metal_extent_mol_by_oxide": metal_extent_by_oxide,
                    "native_fe_saturation_split": "no_op",
                },
            )
        registry = request.account_view.species_formula_registry
        transition_reason = (
            "melt_metal_saturation_buffer"
            if has_extent_map
            else "native_fe_saturation_split"
        )
        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason=transition_reason,
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
            control_audit=control_audit,
            diagnostic={
                "metal_extent_mol_by_oxide": metal_extent_by_oxide,
                "native_fe_mol": metal_extent_by_oxide.get("FeO", 0.0),
                "feo_debit_mol": debit_oxides.get("FeO", 0.0),
                "tap_fe_credit_mol": tap_metals.get("Fe", 0.0),
                "overhead_fe_credit_mol": 0.0,
                "routed_fe_vapor_mol": native_fe_vapor_mol,
                "oxygen_account": oxygen_account,
                "overhead_o2_credit_mol": (
                    oxygen_mol
                    if oxygen_account == self.OVERHEAD_GAS_ACCOUNT else 0.0
                ),
                "buffer_o2_credit_mol": (
                    oxygen_mol
                    if oxygen_account == FO2_BUFFER_ACCOUNT else 0.0
                ),
            },
        )
