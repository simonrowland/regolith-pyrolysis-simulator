"""Builtin METALLOTHERMIC_STEP provider (Na/K shuttle + Mg thermite).

Kernel-registered provider that owns the ``METALLOTHERMIC_STEP`` intent
per binding spec §2 ("Na/K shuttle, Mg thermite") and §3 (Builtin
authoritative). Mirrors the alkali-shuttle and Mg-thermite stoichiometry
in :class:`ExtractionMixin._shuttle_inject_K`,
:class:`ExtractionMixin._shuttle_inject_Na`, and
:class:`ExtractionMixin._step_thermite` line-for-line -- this is a
refactor of where the :class:`LedgerTransitionProposal` is built, not a
re-derivation of the metallothermic physics. The same solubility-limit /
kinetic / accessibility constants flow through verbatim:

* C3 K-shuttle: ``2 K + FeO -> K2O + Fe`` (K2O saturated at 10 wt% melt,
  K injected up to 1/3 of inventory per hour).
* C3 Na-shuttle: stage-aware targets.  The Cr stage preserves the legacy
  ``6 Na + Cr2O3 -> 3 Na2O + 2 Cr`` first, then
  ``4 Na + TiO2 -> 2 Na2O + Ti`` with 0.75 accessibility factor
  (highest-priority experimental question -- legacy comment
  ``[THERMO-10]``).  Cool Fe-cleanup may instead request
  ``2 Na + FeO -> Na2O + Fe``. Na2O saturated at 10 wt%.
* C6 Mg thermite primary: ``3 Mg + Al2O3 -> 3 MgO + 2 Al``. Mg consumed
  per Arrhenius-style rate factor ``0.20 * exp(-0.05 * wt%MgO)`` (clamped
  to ``[0.01, 0.25]``).
* C6 Al back-reduction cascade: ``4 Al + 3 SiO2 -> 2 Al2O3 + 3 Si``,
  applied to 30 % of freshly produced Al when ``SiO2 > 0.1 kg`` (legacy
  ``BACK_REDUCTION_FRACTION`` constant). Re-credits Al2O3 to the melt
  and credits Si to metal phase.

Per binding spec §2 the intent is a single METALLOTHERMIC_STEP that
covers the two reaction families.  The provider receives a
``reaction_family`` discriminator in ``control_inputs`` -- valid values:

* ``'c3_k_shuttle'`` -- C3 K-shuttle injection (single primary reaction).
* ``'c3_na_shuttle'`` -- C3 Na-shuttle injection (stage-aware target set).
* ``'c6_mg_thermite'`` -- C6 Mg thermite primary + Al-SiO2 back-reduction
  cascade in one call.

The legacy code did two distinct chemical-physics passes for C6 (primary
thermite + back-reduction), which the legacy ledger recorded as two
separate :class:`LedgerTransition`s.  The provider preserves that two-
transition shape inside a single intent dispatch by emitting two
sequential commits from the caller; see ``simulator/extraction.py``
:meth:`ExtractionMixin._step_thermite` for the orchestration -- the
provider only ever returns a single proposal per ``dispatch`` so each
chemical reaction is a single atom-balanced :class:`LedgerTransition`.

The provider:

- reads ``process.cleaned_melt`` (the oxide source / coproduct sink),
  ``process.metal_phase`` (cathode-metal credit + Al back-reduction
  debit), and ``process.reagent_inventory`` (alkali / Mg reagent debit)
  via the filtered :class:`ProviderAccountView`.  Reagent inventory is
  a normal process account (debiting is permitted); only terminal
  accounts have the special "credit-only via canonical commit path"
  rule.  This provider touches no terminal accounts -- alkali-shuttle
  bakeout vapor goes through the EVAPORATION_TRANSITION /
  CONDENSATION_ROUTE intents (already kernel-authoritative).
- reads T from ``request.temperature_C`` (currently informational only
  -- the solubility limits + rate factor are functions of melt
  composition + reagent inventory, not T; the legacy did not consult T
  inside ``_shuttle_inject_K`` / ``_shuttle_inject_Na`` / ``_step_thermite``
  either, so neither does the provider).
- reads the per-tick inputs from ``request.control_inputs``:

  * ``reaction_family`` -- discriminator (see above).
  * ``reagent_available_kg`` -- snapshot of the K / Na / Mg
    ``shuttle_K_inventory_kg`` / ``shuttle_Na_inventory_kg`` /
    ``thermite_Mg_inventory_kg`` simulator counter taken at the moment
    of dispatch.  Passed in so the provider stays stateless about the
    simulator's per-batch counters (the legacy ledger account
    ``process.reagent_inventory`` already holds the same kg, but the
    legacy code reads the counter, so we pass it through to keep
    parity bit-for-bit).
  * ``dt_hr`` -- tick duration in hours (always 1.0 in the current
    simulator; passed explicitly so the provider stays unit-correct if
    the simulator's tick ever changes).
  * ``back_reduction`` (C6 only) -- optional flag.  When present and
    truthy, the provider emits the back-reduction proposal instead of
    the primary thermite proposal; the caller orchestrates the two
    dispatches in sequence to preserve the legacy two-transition shape.
    Defaults to ``False`` -> primary thermite.
  * ``mol_Al_produced`` (C6 back-reduction only) -- the mol Al credited
    in the matched primary thermite call.  The back-reduction consumes
    ``BACK_REDUCTION_FRACTION = 0.30`` of this Al; passed in to keep the
    provider stateless across the two dispatches.
  * ``liquid_fraction`` (C3 K/Na shuttle + C6 primary) -- optional freeze-gate
    signal.  Exact ``0.0`` means no liquid phase, so the step is refused with
    zero yield; ``None`` means unknown and preserves legacy behavior.

Returns an :class:`IntentResult` with ``transition`` populated by a
single :class:`LedgerTransitionProposal` and a ``diagnostic`` dict with
per-species progress (``reagent_consumed_kg``, ``oxide_reduced_kg``,
``metal_produced_kg``, ``coproduct_kg``).  These mirror the legacy
``_shuttle_*_this_hr`` counter shapes so the caller's snapshot wiring
needs no shape changes.

Authority: authoritative for ``METALLOTHERMIC_STEP`` per binding spec
§3.  This is the FOURTH authoritative ledger-mutating intent in the
migration (after EVAPORATION_TRANSITION, CONDENSATION_ROUTE, and
ELECTROLYSIS_STEP) -- :meth:`ChemistryKernel.commit_batch` engages
atom-balance validation at dispatch time AND again at commit time.

Account declaration: ``process.cleaned_melt`` (debit oxides being
reduced + credit K2O / MgO / regenerated Al2O3 coproducts),
``process.spent_reductant_residue`` (credit melt-resident Na2O from the
spent Na shuttle), ``process.metal_phase`` (credit Fe/Cr/Ti/Al/Si metals
+ debit Al on back-reduction), ``process.reagent_inventory`` (debit
K/Na/Mg consumed).
Every account named in the legacy ``_record_atom_transition`` calls
inside ``_shuttle_inject_K``, ``_shuttle_inject_Na``, and
``_step_thermite`` lands in this set.  The hardened kernel account-
filter (since commit ``a259f80``) will raise
:class:`AccountFilterViolation` if a future refactor adds a fourth
account; the declared set is the first-line gate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import math
from typing import Any

from engines.builtin._common import (
    build_atom_balance_proof,
    composition_kg_from_account_view,
    diagnostic_control_audit,
    dispatch_reaction_family,
    reject_wrong_intent,
    unpack_controls,
)
from engines.builtin.vapor_pressure import (
    _ELLINGHAM_THERMO,
)
from simulator.chemistry.ellingham_thermo import (
    ELLINGHAM_RECONSTRUCTED_AUTHORITY_FLAG,
    ellingham_authority_diagnostic,
    ellingham_authority_limit,
    ellingham_delta_g_kj_per_mol_o2,
    ellingham_fit_range_K,
    ellingham_fit_segments,
)
from simulator.chemistry.melt_activity import (
    MELT_OXIDE_ACTIVITY_COEFFICIENTS,
    MELT_OXIDE_ACTIVITY_LIMITATION,
    melt_oxide_activity,
    na_reductant_activity_shift_kj_per_mol_o2,
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
from simulator.account_ids import SPENT_REDUCTANT_RESIDUE_ACCOUNT
from simulator.melt_regime import MeltRegime, melt_regime
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET


# Reaction-family discriminators (string-literal contract with the
# caller). The provider switches on these inside dispatch; any other
# value is rejected as unsupported.
REACTION_FAMILY_C3_K = "c3_k_shuttle"
REACTION_FAMILY_C3_NA = "c3_na_shuttle"
REACTION_FAMILY_C6_MG = "c6_mg_thermite"
VALID_REACTION_FAMILIES = frozenset({
    REACTION_FAMILY_C3_K,
    REACTION_FAMILY_C3_NA,
    REACTION_FAMILY_C6_MG,
})

NA_TARGET_FEO_CLEANUP = "feo_cleanup"
NA_TARGET_CR_TI = "cr_ti"
NA_STAGE_TARGETS = {
    NA_TARGET_FEO_CLEANUP: ("FeO",),
    NA_TARGET_CR_TI: ("Cr2O3", "TiO2"),
}
TARGET_OXIDE_TO_METAL = {
    "FeO": "Fe",
    "MnO": "Mn",
    "Cr2O3": "Cr",
    "TiO2": "Ti",
    "Al2O3": "Al",
}
NA_TARGET_TO_METAL = {
    oxide: TARGET_OXIDE_TO_METAL[oxide]
    for oxide in ("FeO", "Cr2O3", "TiO2")
}


class BuiltinMetallothermicStepProvider(ChemistryProvider):
    """Authoritative ``METALLOTHERMIC_STEP`` provider.

    See module docstring.  Stateless -- per-call inputs (T, P,
    reagent-available-kg, dt_hr, reaction_family, back_reduction flag)
    arrive through :class:`IntentRequest`; the same instance serves
    every C3 / C6 tick without holding simulator references.
    """

    name = "builtin-metallothermic-step"

    # Accounts the legacy _shuttle_inject_K / _shuttle_inject_Na /
    # _step_thermite collectively touch on debit or credit side.  Na
    # spent-reductant oxide is melt-resident, but provenance-isolated
    # from feedstock/recovered reagent accounting.
    DECLARED_ACCOUNTS = frozenset({
        "process.cleaned_melt",
        "process.metal_phase",
        "process.reagent_inventory",
        SPENT_REDUCTANT_RESIDUE_ACCOUNT,
    })

    # Legacy constants reproduced verbatim from simulator/extraction.py
    # for the C3 / C6 reactions.
    K2O_SOLUBILITY_WT_PCT = 10.0
    NA2O_SOLUBILITY_WT_PCT = 10.0
    TI_ACCESSIBILITY = 0.75
    BACK_REDUCTION_FRACTION = 0.30
    C6_ABOVE_CROSSOVER_REFUSAL = (
        "c6_mg_thermite_above_crossover_requires_local_support"
    )

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="builtin-metallothermic-step",
            intents=frozenset({ChemistryIntent.METALLOTHERMIC_STEP}),
            is_authoritative_for=frozenset(
                {ChemistryIntent.METALLOTHERMIC_STEP}
            ),
            declared_accounts=self.DECLARED_ACCOUNTS,
            consumes_fO2=False,
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        # Lazy imports: simulator.accounting.formulas / simulator.state
        # pull in simulator/__init__ which re-enters this module during
        # package init -- see engines/builtin/__init__.py for the cycle
        # description.
        from simulator.accounting.formulas import resolve_species_formula
        from simulator.state import MOLAR_MASS

        wrong_intent = reject_wrong_intent(
            request, ChemistryIntent.METALLOTHERMIC_STEP
        )
        if wrong_intent is not None:
            return wrong_intent

        controls = dict(unpack_controls(request))
        controls["temperature_C"] = float(request.temperature_C)
        if not math.isfinite(controls["temperature_C"]):
            return IntentResult(
                intent=ChemistryIntent.METALLOTHERMIC_STEP,
                status="unsupported",
                transition=None,
                control_audit=diagnostic_control_audit(
                    request, include_fO2=False
                ),
                diagnostic={"reason": "temperature_C must be finite"},
            )

        # Reaction-family early-exit: shared with stage0_pretreatment.py.
        # The metallothermic shuttles run solubility-limit + reagent-mass
        # arithmetic behind a temperature-dependent Ellingham acceptance gate.
        # No fO2 dependency: legacy _shuttle_inject_K / _shuttle_inject_Na /
        # _step_thermite do not consult fO2 either.
        family_reject = dispatch_reaction_family(
            ChemistryIntent.METALLOTHERMIC_STEP,
            controls,
            VALID_REACTION_FAMILIES,
        )
        if family_reject is not None:
            return family_reject
        reaction_family = str(controls["reaction_family"])
        control_audit = diagnostic_control_audit(request, include_fO2=False)

        registry = request.account_view.species_formula_registry
        metal_mol = dict(
            request.account_view.accounts.get("process.metal_phase", {})
            or {}
        )

        # Project the melt's kg view from the mol accounts (the legacy
        # path reads self.melt.composition_kg, which is the simulator's
        # projection of process.cleaned_melt plus melt-resident
        # spent-reductant residue).  The kg dict + total_kg pair is the
        # shape the C3 solubility-limit math reads.
        composition_kg, total_kg = composition_kg_from_account_view(
            request.account_view,
            "process.cleaned_melt",
        )
        spent_residue_kg, spent_residue_total_kg = (
            composition_kg_from_account_view(
                request.account_view,
                SPENT_REDUCTANT_RESIDUE_ACCOUNT,
            )
        )
        for species, kg in spent_residue_kg.items():
            composition_kg[species] = composition_kg.get(species, 0.0) + kg
        total_kg += spent_residue_total_kg
        composition_mol = dict(
            request.account_view.accounts.get("process.cleaned_melt", {}) or {}
        )
        for species, mol in (
            request.account_view.accounts.get(
                SPENT_REDUCTANT_RESIDUE_ACCOUNT,
                {},
            )
            or {}
        ).items():
            composition_mol[species] = composition_mol.get(species, 0.0) + mol
        composition_wt_pct = self._wt_pct_from_kg(composition_kg, total_kg)
        true_available_mol = self._true_available_mol_by_species(controls)

        if reaction_family == REACTION_FAMILY_C3_K:
            result = self._dispatch_c3_k(
                composition_kg,
                composition_wt_pct,
                total_kg,
                true_available_mol,
                request.temperature_C,
                controls,
                MOLAR_MASS,
                registry,
                resolve_species_formula,
                control_audit,
            )
        elif reaction_family == REACTION_FAMILY_C3_NA:
            result = self._dispatch_c3_na(
                composition_kg,
                composition_mol,
                composition_wt_pct,
                total_kg,
                true_available_mol,
                request.temperature_C,
                controls,
                MOLAR_MASS,
                registry,
                resolve_species_formula,
                control_audit,
            )
        else:
            # reaction_family == REACTION_FAMILY_C6_MG
            back_reduction = bool(controls.get("back_reduction") or False)
            if back_reduction:
                result = self._dispatch_c6_back_reduction(
                    composition_kg,
                    true_available_mol,
                    metal_mol,
                    controls,
                    request.temperature_C,
                    MOLAR_MASS,
                    registry,
                    resolve_species_formula,
                    control_audit,
                )
            else:
                result = self._dispatch_c6_mg_primary(
                    composition_kg,
                    composition_wt_pct,
                    true_available_mol,
                    controls,
                    MOLAR_MASS,
                    registry,
                    resolve_species_formula,
                    control_audit,
                )
        return self._with_melt_regime_diagnostic(result, controls)

    # ------------------------------------------------------------------
    # C3 K-shuttle dispatch.  Mirrors _shuttle_inject_K line-for-line
    # (see simulator/extraction.py).
    # ------------------------------------------------------------------

    def _dispatch_c3_k(
        self,
        composition_kg: Mapping[str, float],
        composition_wt_pct: Mapping[str, float],
        total_kg: float,
        true_available_mol: Mapping[str, float],
        temperature_C: float,
        controls: Mapping[str, Any],
        molar_mass: Mapping[str, float],
        registry: Mapping[str, Any],
        resolve_species_formula,
        control_audit,
    ) -> IntentResult:
        if self._liquid_fraction_blocks_metallothermic(controls):
            return self._no_liquid_phase_refusal(
                REACTION_FAMILY_C3_K,
                control_audit=control_audit,
            )

        K_available_kg = float(controls.get("reagent_available_kg") or 0.0)
        if K_available_kg <= 0.01:
            return self._empty_result(
                "c3_k_shuttle skipped: K reagent below 0.01 kg threshold",
                control_audit=control_audit,
            )

        K2O_current_pct = composition_wt_pct.get("K2O", 0.0)
        if K2O_current_pct >= self.K2O_SOLUBILITY_WT_PCT:
            return self._empty_result(
                "c3_k_shuttle skipped: K2O above 10 wt% solubility limit",
                control_audit=control_audit,
            )

        # Solubility headroom: K2O_max_kg = total_melt * 10% - K2O_current
        K2O_max_kg = max(
            0.0,
            total_kg * self.K2O_SOLUBILITY_WT_PCT / 100.0
            - composition_kg.get("K2O", 0.0),
        )
        # Convert K2O capacity to K capacity: 2K / K2O molar ratio.
        K_for_K2O_limit_kg = (
            K2O_max_kg * (2 * molar_mass["K"] / molar_mass["K2O"])
        )

        mol_FeO_available = self._available_mol(
            "FeO",
            composition_kg,
            true_available_mol,
            molar_mass,
        )
        FeO_available_kg = mol_FeO_available * molar_mass["FeO"] / 1000.0
        # 1 kg K reduces (M_FeO / (2 * M_K)) kg FeO -- inverse of the
        # 2K + FeO -> K2O + Fe stoichiometry.  Same expression as legacy.
        K_for_FeO_kg = (
            FeO_available_kg / (molar_mass["FeO"] / (2 * molar_mass["K"]))
            if molar_mass["FeO"] > 0.0
            else 0.0
        )

        # Spread injection over 3 hours per cycle (legacy comment).
        K_available_this_hr = K_available_kg / 3.0
        K_inject_kg = max(
            0.0,
            min(K_available_this_hr, K_for_K2O_limit_kg, K_for_FeO_kg),
        )
        if K_inject_kg < 0.001:
            return self._empty_result(
                "c3_k_shuttle skipped: injection floor (<0.001 kg K)",
                control_audit=control_audit,
            )

        margin = self._reduction_margin_kj_per_mol_o2(
            "K",
            "FeO",
            temperature_C,
        )
        fit_extrapolations = self._ellingham_pair_fit_extrapolations(
            "K",
            ("FeO",),
            temperature_C,
        )
        fit_warnings = self._ellingham_fit_warnings(fit_extrapolations)
        if margin <= 0.0:
            return self._refused_result(
                "thermodynamic_margin_nonpositive",
                reductant="K",
                target_oxide="FeO",
                temperature_C=temperature_C,
                margin_kJ_per_mol_O2=margin,
                crossover_temperature_C=self._crossover_temperature_C("K", "Fe"),
                control_audit=control_audit,
                extra_diagnostic=self._ellingham_fit_diagnostic(
                    fit_extrapolations
                ),
                warnings=fit_warnings,
            )

        # Stoichiometric integration in mol space, line-for-line with the
        # legacy ``_shuttle_inject_K``: legacy uses ``MOLAR_MASS`` in
        # g/mol so it scales mass_kg -> mol via ``kg / (g/mol) * 1000``
        # (== ``kg * 1000 g/kg / (g/mol)`` == mol).  Provider keeps the
        # same expressions so the worst-case parity delta is bounded by
        # IEEE-754 round-off on the same operand sequence the legacy
        # already pinned in the smoke run.
        mol_K = K_inject_kg / molar_mass["K"] * 1000.0
        mol_FeO_reduced = min(mol_K / 2.0, mol_FeO_available)
        mol_K_used = mol_FeO_reduced * 2.0
        if mol_FeO_reduced <= 0.0:
            return self._empty_result(
                "c3_k_shuttle skipped: no FeO reducible after stoich cap",
                control_audit=control_audit,
            )

        # Reaction 2 K + FeO -> K2O + Fe.  Per mol:
        # debits: 2 mol K (reagent_inventory) + 1 mol FeO (cleaned_melt).
        # credits: 1 mol K2O (cleaned_melt) + 1 mol Fe (metal_phase).
        debits: dict[str, dict[str, float]] = {
            "process.reagent_inventory": {"K": mol_K_used},
            "process.cleaned_melt": {"FeO": mol_FeO_reduced},
        }
        credits: dict[str, dict[str, float]] = {
            "process.cleaned_melt": {"K2O": mol_FeO_reduced},
            "process.metal_phase": {"Fe": mol_FeO_reduced},
        }

        # Diagnostic dict in kg-native form for legacy parity.  Convert
        # mol back to kg via (mol * M_gmol / 1000.0) -- same shape as
        # the legacy ``_shuttle_*_this_hr`` counters.
        K_used_kg = mol_K_used * molar_mass["K"] / 1000.0
        FeO_removed_kg = mol_FeO_reduced * molar_mass["FeO"] / 1000.0
        Fe_produced_kg = mol_FeO_reduced * molar_mass["Fe"] / 1000.0

        atom_proof = build_atom_balance_proof(
            debits, credits, registry, resolve_species_formula,
        )
        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason="c3_k_shuttle_fe_reduction",
            atom_balance_proof=atom_proof,
        )
        diagnostic = {
            "reaction_family": REACTION_FAMILY_C3_K,
            "reagent_consumed_kg": K_used_kg,
            "oxide_reduced_kg": FeO_removed_kg,
            "metal_produced_kg": Fe_produced_kg,
            "metal_species": "Fe",
        }
        diagnostic.update(self._ellingham_fit_diagnostic(fit_extrapolations))
        return IntentResult(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic=diagnostic,
            warnings=tuple(fit_warnings),
        )

    # ------------------------------------------------------------------
    # C3 Na-shuttle dispatch.  Mirrors _shuttle_inject_Na line-for-line
    # for the default Cr/Ti stage; cool Fe-cleanup can request FeO as
    # the only target.
    # The legacy code split this into TWO _record_atom_transition calls,
    # one per oxide.  The provider emits a SINGLE proposal that bundles
    # the (possibly-two) reactions atom-balanced; this matches the
    # legacy ledger-state outcome (same debits / credits across the
    # tick) but collapses two LedgerTransitions into one.  The
    # diagnostic exposes per-oxide totals so the simulator snapshot
    # still tracks the per-species splits.
    # ------------------------------------------------------------------

    def _dispatch_c3_na(
        self,
        composition_kg: Mapping[str, float],
        composition_mol: Mapping[str, float],
        composition_wt_pct: Mapping[str, float],
        total_kg: float,
        true_available_mol: Mapping[str, float],
        temperature_C: float,
        controls: Mapping[str, Any],
        molar_mass: Mapping[str, float],
        registry: Mapping[str, Any],
        resolve_species_formula,
        control_audit,
    ) -> IntentResult:
        if self._liquid_fraction_blocks_metallothermic(controls):
            return self._no_liquid_phase_refusal(
                REACTION_FAMILY_C3_NA,
                control_audit=control_audit,
            )

        Na_available_kg = float(controls.get("reagent_available_kg") or 0.0)
        if Na_available_kg <= 0.01:
            return self._empty_result(
                "c3_na_shuttle skipped: Na reagent below 0.01 kg threshold",
                control_audit=control_audit,
            )

        Na2O_current_pct = composition_wt_pct.get("Na2O", 0.0)
        if Na2O_current_pct >= self.NA2O_SOLUBILITY_WT_PCT:
            return self._empty_result(
                "c3_na_shuttle skipped: Na2O above 10 wt% solubility limit",
                control_audit=control_audit,
            )

        Na2O_current_kg = composition_kg.get("Na2O", 0.0)
        initial_Na2O_headroom_kg = self._product_cap_kg_for_solubility(
            total_kg=total_kg,
            current_product_kg=Na2O_current_kg,
            removed_kg=0.0,
            added_product_kg=0.0,
            removed_kg_per_product_kg=0.0,
            solubility_wt_pct=self.NA2O_SOLUBILITY_WT_PCT,
        )

        target_stage, target_priority, thermo_audit = (
            self._resolve_na_target_priority(
                controls,
                temperature_C,
                composition_mol,
            )
        )
        if not target_priority:
            if controls.get("target_oxides") is not None:
                return self._empty_result(
                    "c3_na_shuttle skipped: explicit target set is empty",
                    control_audit=control_audit,
                    diagnostic={"target_stage": target_stage},
                )
            return IntentResult(
                intent=ChemistryIntent.METALLOTHERMIC_STEP,
                status="refused",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "reason": "unknown_na_target_stage",
                    "reason_refused": "unknown_na_target_stage",
                    "target_stage": target_stage,
                },
            )
        fit_extrapolations = dict(
            thermo_audit.get("ellingham_extrapolated_beyond_fit_range_K") or {}
        )
        fit_warnings = self._ellingham_fit_warnings(fit_extrapolations)

        mol_FeO_available = self._available_mol(
            "FeO",
            composition_kg,
            true_available_mol,
            molar_mass,
        )
        mol_TiO2_available = self._available_mol(
            "TiO2",
            composition_kg,
            true_available_mol,
            molar_mass,
        )
        mol_Cr2O3_available = self._available_mol(
            "Cr2O3",
            composition_kg,
            true_available_mol,
            molar_mass,
        )
        FeO_available_kg = mol_FeO_available * molar_mass["FeO"] / 1000.0
        TiO2_available_kg = mol_TiO2_available * molar_mass["TiO2"] / 1000.0
        Cr2O3_available_kg = mol_Cr2O3_available * molar_mass["Cr2O3"] / 1000.0

        Na_available_this_hr = Na_available_kg / 3.0
        Na_inject_kg = max(0.0, Na_available_this_hr)
        if Na_inject_kg < 0.001:
            return self._empty_result(
                "c3_na_shuttle skipped: injection floor (<0.001 kg Na)",
                control_audit=control_audit,
                diagnostic=self._ellingham_fit_diagnostic(fit_extrapolations),
                warnings=fit_warnings,
            )

        mol_Na = Na_inject_kg / molar_mass["Na"] * 1000.0

        # Accumulators -- both reactions write into a single proposal.
        total_Na_used_mol = 0.0
        total_Na2O_added_mol = 0.0
        total_FeO_removed_mol = 0.0
        total_Cr2O3_removed_mol = 0.0
        total_TiO2_removed_mol = 0.0
        total_Fe_produced_mol = 0.0
        total_Cr_produced_mol = 0.0
        total_Ti_produced_mol = 0.0
        total_melt_oxide_removed_kg = 0.0
        accepted_targets: list[str] = []
        refused_targets: dict[str, dict[str, Any]] = {}
        activity_audit = {
            key: value
            for key, value in thermo_audit.items()
            if key
            in {
                "standard_deltaG",
                "Na2O_activity_gamma",
                "Na2O_activity_component",
                "Na2O_activity_X_single_cation",
                "Na2O_activity",
                "Na2O_activity_shift_kJ_per_mol_O2",
                "Na2O_activity_limitation",
                "na_activity_shifted_margin_kJ_per_mol_O2",
            }
        }

        for target in target_priority:
            if target == "FeO":
                if FeO_available_kg <= 0.01 or mol_Na <= 0.1:
                    continue
                margin = float(thermo_audit["margin"].get(target, 0.0))
                if margin <= 0.0:
                    refused_targets[target] = {
                        "margin_kJ_per_mol_O2": margin,
                        "crossover_temperature_C": self._crossover_temperature_C(
                            "Na",
                            TARGET_OXIDE_TO_METAL[target],
                        ),
                    }
                    continue

                na2o_cap_mol = self._product_cap_kg_for_solubility(
                    total_kg=total_kg,
                    current_product_kg=Na2O_current_kg,
                    removed_kg=total_melt_oxide_removed_kg,
                    added_product_kg=(
                        total_Na2O_added_mol * molar_mass["Na2O"] / 1000.0
                    ),
                    removed_kg_per_product_kg=(
                        molar_mass["FeO"] / molar_mass["Na2O"]
                    ),
                    solubility_wt_pct=self.NA2O_SOLUBILITY_WT_PCT,
                ) / (molar_mass["Na2O"] / 1000.0)
                mol_FeO_reduced = min(
                    mol_Na / 2.0,
                    mol_FeO_available,
                    na2o_cap_mol,
                )
                if mol_FeO_reduced <= 0.0:
                    continue
                mol_Na_for_Fe = mol_FeO_reduced * 2.0
                mol_Na2O_from_Fe = mol_FeO_reduced
                mol_Fe_produced = mol_FeO_reduced

                total_Na_used_mol += mol_Na_for_Fe
                total_Na2O_added_mol += mol_Na2O_from_Fe
                total_FeO_removed_mol += mol_FeO_reduced
                total_Fe_produced_mol += mol_Fe_produced
                total_melt_oxide_removed_kg += (
                    mol_FeO_reduced * molar_mass["FeO"] / 1000.0
                )
                mol_Na -= mol_Na_for_Fe
                accepted_targets.append(target)
            elif target == "Cr2O3":
                if Cr2O3_available_kg <= 0.01 or mol_Na <= 0.1:
                    continue
                margin = float(thermo_audit["margin"].get(target, 0.0))
                if margin <= 0.0:
                    refused_targets[target] = {
                        "margin_kJ_per_mol_O2": margin,
                        "crossover_temperature_C": self._crossover_temperature_C(
                            "Na",
                            TARGET_OXIDE_TO_METAL[target],
                        ),
                    }
                    continue

                na2o_cap_mol = self._product_cap_kg_for_solubility(
                    total_kg=total_kg,
                    current_product_kg=Na2O_current_kg,
                    removed_kg=total_melt_oxide_removed_kg,
                    added_product_kg=(
                        total_Na2O_added_mol * molar_mass["Na2O"] / 1000.0
                    ),
                    removed_kg_per_product_kg=(
                        molar_mass["Cr2O3"] / 3.0 / molar_mass["Na2O"]
                    ),
                    solubility_wt_pct=self.NA2O_SOLUBILITY_WT_PCT,
                ) / (molar_mass["Na2O"] / 1000.0)
                mol_Cr2O3_reduced = min(
                    mol_Na / 6.0,
                    mol_Cr2O3_available,
                    na2o_cap_mol / 3.0,
                )
                if mol_Cr2O3_reduced <= 0.0:
                    continue
                mol_Na_for_Cr = mol_Cr2O3_reduced * 6.0
                mol_Na2O_from_Cr = mol_Cr2O3_reduced * 3.0
                mol_Cr_produced = mol_Cr2O3_reduced * 2.0

                total_Na_used_mol += mol_Na_for_Cr
                total_Na2O_added_mol += mol_Na2O_from_Cr
                total_Cr2O3_removed_mol += mol_Cr2O3_reduced
                total_Cr_produced_mol += mol_Cr_produced
                total_melt_oxide_removed_kg += (
                    mol_Cr2O3_reduced * molar_mass["Cr2O3"] / 1000.0
                )
                mol_Na -= mol_Na_for_Cr
                accepted_targets.append(target)
            elif target == "TiO2":
                if TiO2_available_kg <= 0.01 or mol_Na <= 0.1:
                    continue
                margin = float(thermo_audit["margin"].get(target, 0.0))
                if margin <= 0.0:
                    refused_targets[target] = {
                        "margin_kJ_per_mol_O2": margin,
                        "crossover_temperature_C": self._crossover_temperature_C(
                            "Na",
                            TARGET_OXIDE_TO_METAL[target],
                        ),
                    }
                    continue

                na2o_cap_mol = self._product_cap_kg_for_solubility(
                    total_kg=total_kg,
                    current_product_kg=Na2O_current_kg,
                    removed_kg=total_melt_oxide_removed_kg,
                    added_product_kg=(
                        total_Na2O_added_mol * molar_mass["Na2O"] / 1000.0
                    ),
                    removed_kg_per_product_kg=(
                        molar_mass["TiO2"] / 2.0 / molar_mass["Na2O"]
                    ),
                    solubility_wt_pct=self.NA2O_SOLUBILITY_WT_PCT,
                ) / (molar_mass["Na2O"] / 1000.0)
                mol_TiO2_accessible = mol_TiO2_available * self.TI_ACCESSIBILITY
                mol_TiO2_reduced = min(
                    mol_Na / 4.0,
                    mol_TiO2_accessible,
                    na2o_cap_mol / 2.0,
                )
                if mol_TiO2_reduced <= 0.0:
                    continue
                mol_Na_for_Ti = mol_TiO2_reduced * 4.0
                mol_Na2O_from_Ti = mol_TiO2_reduced * 2.0
                mol_Ti_produced = mol_TiO2_reduced

                total_Na_used_mol += mol_Na_for_Ti
                total_Na2O_added_mol += mol_Na2O_from_Ti
                total_TiO2_removed_mol += mol_TiO2_reduced
                total_Ti_produced_mol += mol_Ti_produced
                total_melt_oxide_removed_kg += (
                    mol_TiO2_reduced * molar_mass["TiO2"] / 1000.0
                )
                mol_Na -= mol_Na_for_Ti
                accepted_targets.append(target)

        if total_Na_used_mol <= 0.0:
            if refused_targets:
                diagnostic = {
                    "reason_refused": "thermodynamic_margin_nonpositive",
                    "reaction_family": REACTION_FAMILY_C3_NA,
                    "target_stage": target_stage,
                    "target_priority": list(target_priority),
                    "thermo_priority": list(thermo_audit["priority"]),
                    "thermo_deltaG_kJ_per_mol_O2": thermo_audit["deltaG"],
                    "na_reduction_margin_kJ_per_mol_O2": thermo_audit["margin"],
                    "accepted_targets": accepted_targets,
                    "refused_targets": refused_targets,
                }
                diagnostic.update(activity_audit)
                diagnostic.update(
                    self._ellingham_fit_diagnostic(fit_extrapolations)
                )
                return IntentResult(
                    intent=ChemistryIntent.METALLOTHERMIC_STEP,
                    status="refused",
                    transition=None,
                    control_audit=control_audit,
                    diagnostic=diagnostic,
                    warnings=tuple(fit_warnings),
                )
            return self._empty_result(
                "c3_na_shuttle skipped: no oxide accepted Na reduction",
                control_audit=control_audit,
                diagnostic=self._ellingham_fit_diagnostic(fit_extrapolations),
                warnings=fit_warnings,
            )

        # Build mol-native proposal.  Both reactions converge on the
        # same three accounts; the per-oxide split lives in the
        # diagnostic dict so callers can replay it.
        debits: dict[str, dict[str, float]] = {
            "process.reagent_inventory": {"Na": total_Na_used_mol},
            "process.cleaned_melt": {},
        }
        if total_FeO_removed_mol > 0.0:
            debits["process.cleaned_melt"]["FeO"] = total_FeO_removed_mol
        if total_Cr2O3_removed_mol > 0.0:
            debits["process.cleaned_melt"]["Cr2O3"] = total_Cr2O3_removed_mol
        if total_TiO2_removed_mol > 0.0:
            debits["process.cleaned_melt"]["TiO2"] = total_TiO2_removed_mol
        if not debits["process.cleaned_melt"]:
            del debits["process.cleaned_melt"]

        credits: dict[str, dict[str, float]] = {
            SPENT_REDUCTANT_RESIDUE_ACCOUNT: {
                "Na2O": total_Na2O_added_mol
            },
            "process.metal_phase": {},
        }
        if total_Fe_produced_mol > 0.0:
            credits["process.metal_phase"]["Fe"] = total_Fe_produced_mol
        if total_Cr_produced_mol > 0.0:
            credits["process.metal_phase"]["Cr"] = total_Cr_produced_mol
        if total_Ti_produced_mol > 0.0:
            credits["process.metal_phase"]["Ti"] = total_Ti_produced_mol
        if not credits["process.metal_phase"]:
            del credits["process.metal_phase"]

        # kg-native diagnostic projection for snapshot parity. Convert
        # mol back to kg via (mol * M_gmol / 1000.0) -- same shape as
        # the legacy ``_shuttle_*_this_hr`` counters.
        Na_used_kg = total_Na_used_mol * molar_mass["Na"] / 1000.0
        FeO_removed_kg = (
            total_FeO_removed_mol * molar_mass["FeO"] / 1000.0
        )
        Cr2O3_removed_kg = (
            total_Cr2O3_removed_mol * molar_mass["Cr2O3"] / 1000.0
        )
        TiO2_removed_kg = (
            total_TiO2_removed_mol * molar_mass["TiO2"] / 1000.0
        )
        Na2O_added_kg = total_Na2O_added_mol * molar_mass["Na2O"] / 1000.0
        Fe_produced_kg = total_Fe_produced_mol * molar_mass["Fe"] / 1000.0
        Cr_produced_kg = total_Cr_produced_mol * molar_mass["Cr"] / 1000.0
        Ti_produced_kg = total_Ti_produced_mol * molar_mass["Ti"] / 1000.0

        atom_proof = build_atom_balance_proof(
            debits, credits, registry, resolve_species_formula,
        )
        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason="c3_na_shuttle_reduction",
            atom_balance_proof=atom_proof,
        )
        diagnostic = {
            "reaction_family": REACTION_FAMILY_C3_NA,
            "target_stage": target_stage,
            "target_priority": list(target_priority),
            "thermo_priority": list(thermo_audit["priority"]),
            "thermo_deltaG_kJ_per_mol_O2": thermo_audit["deltaG"],
            "na_reduction_margin_kJ_per_mol_O2": thermo_audit["margin"],
            "accepted_targets": accepted_targets,
            "refused_targets": refused_targets,
            "reagent_consumed_kg": Na_used_kg,
            "oxide_reduced_kg": (
                FeO_removed_kg + Cr2O3_removed_kg + TiO2_removed_kg
            ),
            "metal_produced_kg": (
                Fe_produced_kg + Cr_produced_kg + Ti_produced_kg
            ),
            "per_oxide_reduced_kg": {
                "FeO": FeO_removed_kg,
                "Cr2O3": Cr2O3_removed_kg,
                "TiO2": TiO2_removed_kg,
            },
            "per_metal_produced_kg": {
                "Fe": Fe_produced_kg,
                "Cr": Cr_produced_kg,
                "Ti": Ti_produced_kg,
            },
            "spent_reductant_residue_account": SPENT_REDUCTANT_RESIDUE_ACCOUNT,
            "spent_reductant_residue_kg": {
                "Na2O": Na2O_added_kg,
            },
            "na2o_melt_kg_for_solubility_cap": Na2O_current_kg,
            "na2o_solubility_headroom_kg": initial_Na2O_headroom_kg,
            "na2o_post_reaction_wt_pct": (
                (Na2O_current_kg + Na2O_added_kg)
                / (
                    total_kg
                    - total_melt_oxide_removed_kg
                    + Na2O_added_kg
                )
                * 100.0
            ),
        }
        diagnostic.update(activity_audit)
        diagnostic.update(self._ellingham_fit_diagnostic(fit_extrapolations))
        return IntentResult(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic=diagnostic,
            warnings=tuple(fit_warnings),
        )

    # ------------------------------------------------------------------
    # C6 Mg thermite -- primary reaction. Mirrors _step_thermite up
    # through the primary _record_atom_transition call. The back-
    # reduction lives in its own _dispatch_c6_back_reduction (the
    # caller orchestrates the two-call sequence with the mol_Al_produced
    # value carried between them).
    # ------------------------------------------------------------------

    @staticmethod
    def _truthy_control(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)

    @classmethod
    def _c6_mg_al_support_gate(
        cls,
        controls: Mapping[str, Any],
        temperature_C: float,
    ) -> tuple[bool, dict[str, Any]]:
        margin_block = (
            controls.get("JANAF_4th_multiphase_margin_kJ_per_mol_O2") or {}
        )
        if not isinstance(margin_block, Mapping):
            margin_block = {}
        crossover_raw = margin_block.get("Mg_Al_crossover_C")
        try:
            crossover_C = float(crossover_raw)
        except (TypeError, ValueError):
            crossover_C = float(cls._crossover_temperature_C("Mg", "Al") or 0.0)
        margin = cls._reduction_margin_kj_per_mol_o2(
            "Mg",
            "Al2O3",
            temperature_C,
        )
        local_support = any(
            cls._truthy_control(controls.get(key))
            for key in (
                "kinetic_driven_above_crossover",
                "local_thermite_exotherm_supported",
                "c6_local_thermite_support",
            )
        )
        above_crossover = (
            math.isfinite(crossover_C)
            and crossover_C > 0.0
            and float(temperature_C) > crossover_C
        )
        diagnostic = {
            "c6_mg_al_margin_kJ_per_mol_O2": float(margin),
            "c6_mg_al_crossover_C": float(crossover_C),
            "c6_above_mg_al_crossover": bool(above_crossover),
            "c6_local_thermite_support": bool(local_support),
            "c6_local_thermite_support_note": str(
                controls.get("kinetic_note")
                or controls.get("local_thermite_support_note")
                or ""
            ),
            "JANAF_4th_multiphase_margin_kJ_per_mol_O2": dict(margin_block),
        }
        return (not above_crossover or local_support), diagnostic

    def _dispatch_c6_mg_primary(
        self,
        composition_kg: Mapping[str, float],
        composition_wt_pct: Mapping[str, float],
        true_available_mol: Mapping[str, float],
        controls: Mapping[str, Any],
        molar_mass: Mapping[str, float],
        registry: Mapping[str, Any],
        resolve_species_formula,
        control_audit,
    ) -> IntentResult:
        import math

        if self._liquid_fraction_blocks_metallothermic(controls):
            return IntentResult(
                intent=ChemistryIntent.METALLOTHERMIC_STEP,
                status="refused",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "reason": "no_liquid_phase",
                    "reason_refused": "no_liquid_phase",
                    "reaction_family": REACTION_FAMILY_C6_MG,
                    "back_reduction": False,
                    "liquid_fraction": 0.0,
                    "reagent_consumed_kg": 0.0,
                    "oxide_reduced_kg": 0.0,
                    "coproduct_kg": 0.0,
                    "metal_produced_kg": 0.0,
                    "mol_Al_produced": 0.0,
                },
            )

        support_ok, support_diagnostic = self._c6_mg_al_support_gate(
            controls,
            float(controls.get("temperature_C", 0.0) or 0.0),
        )
        if not support_ok:
            return IntentResult(
                intent=ChemistryIntent.METALLOTHERMIC_STEP,
                status="refused",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "reason": self.C6_ABOVE_CROSSOVER_REFUSAL,
                    "reason_refused": self.C6_ABOVE_CROSSOVER_REFUSAL,
                    "reaction_family": REACTION_FAMILY_C6_MG,
                    "back_reduction": False,
                    "reagent_consumed_kg": 0.0,
                    "oxide_reduced_kg": 0.0,
                    "coproduct_kg": 0.0,
                    "metal_produced_kg": 0.0,
                    "mol_Al_produced": 0.0,
                    **support_diagnostic,
                },
            )

        Mg_available_kg = float(controls.get("reagent_available_kg") or 0.0)
        if Mg_available_kg <= 0.01:
            return self._empty_result(
                "c6_mg_thermite skipped: Mg reagent below 0.01 kg threshold",
                control_audit=control_audit,
            )

        mol_Al2O3_available = self._available_mol(
            "Al2O3",
            composition_kg,
            true_available_mol,
            molar_mass,
        )
        Al2O3_available_kg = mol_Al2O3_available * molar_mass["Al2O3"] / 1000.0
        if Al2O3_available_kg < 0.01:
            return self._empty_result(
                "c6_mg_thermite skipped: Al2O3 below 0.01 kg threshold",
                control_audit=control_audit,
            )

        # Mg injection rate factor: 0.20 * exp(-0.05 * wt%MgO), clamped
        # to [0.01, 0.25].  Mirrors legacy line-for-line.
        MgO_pct = composition_wt_pct.get("MgO", 0.0)
        rate_factor = 0.20 * math.exp(-0.05 * MgO_pct)
        rate_factor = max(0.01, min(0.25, rate_factor))
        Mg_available_this_hr = Mg_available_kg * rate_factor

        # 3 Mg + Al2O3 -> 3 MgO + 2 Al.  Mg is the limiting reagent.
        mol_Mg = Mg_available_this_hr / molar_mass["Mg"] * 1000.0
        mol_Al2O3_reduced = min(mol_Mg / 3.0, mol_Al2O3_available)
        if mol_Al2O3_reduced < 0.001:
            return self._empty_result(
                "c6_mg_thermite skipped: <0.001 mol Al2O3 reducible",
                control_audit=control_audit,
            )
        mol_Mg_used = mol_Al2O3_reduced * 3.0
        mol_MgO_produced = mol_Al2O3_reduced * 3.0
        mol_Al_produced = mol_Al2O3_reduced * 2.0

        debits = {
            "process.reagent_inventory": {"Mg": mol_Mg_used},
            "process.cleaned_melt": {"Al2O3": mol_Al2O3_reduced},
        }
        credits = {
            "process.cleaned_melt": {"MgO": mol_MgO_produced},
            "process.metal_phase": {"Al": mol_Al_produced},
        }

        # kg-native diagnostic for snapshot parity.
        Mg_consumed_kg = mol_Mg_used * molar_mass["Mg"] / 1000.0
        Al2O3_removed_kg = mol_Al2O3_reduced * molar_mass["Al2O3"] / 1000.0
        MgO_produced_kg = mol_MgO_produced * molar_mass["MgO"] / 1000.0
        Al_produced_kg = mol_Al_produced * molar_mass["Al"] / 1000.0

        atom_proof = build_atom_balance_proof(
            debits, credits, registry, resolve_species_formula,
        )
        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason="c6_mg_thermite_primary",
            atom_balance_proof=atom_proof,
        )
        return IntentResult(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic={
                "reaction_family": REACTION_FAMILY_C6_MG,
                "back_reduction": False,
                "reagent_consumed_kg": Mg_consumed_kg,
                "oxide_reduced_kg": Al2O3_removed_kg,
                "coproduct_kg": MgO_produced_kg,
                "metal_produced_kg": Al_produced_kg,
                "mol_Al_produced": mol_Al_produced,
                **support_diagnostic,
            },
        )

    # ------------------------------------------------------------------
    # C6 back-reduction. 4 Al + 3 SiO2 -> 2 Al2O3 + 3 Si.  Consumes a
    # fraction (BACK_REDUCTION_FRACTION = 0.30) of the matched primary
    # call's mol_Al_produced.  Mirrors the legacy ``if SiO2_available >
    # 0.1 and Al_produced_kg > 0.01`` branch.
    # ------------------------------------------------------------------

    def _dispatch_c6_back_reduction(
        self,
        composition_kg: Mapping[str, float],
        true_available_mol: Mapping[str, float],
        metal_mol: Mapping[str, float],
        controls: Mapping[str, Any],
        temperature_C: float,
        molar_mass: Mapping[str, float],
        registry: Mapping[str, Any],
        resolve_species_formula,
        control_audit,
    ) -> IntentResult:
        if self._liquid_fraction_blocks_metallothermic(controls):
            return IntentResult(
                intent=ChemistryIntent.METALLOTHERMIC_STEP,
                status="refused",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "reason": "no_liquid_phase",
                    "reason_refused": "no_liquid_phase",
                    "reaction_family": REACTION_FAMILY_C6_MG,
                    "back_reduction": True,
                    "liquid_fraction": 0.0,
                },
            )
        support_ok, support_diagnostic = self._c6_mg_al_support_gate(
            controls,
            float(temperature_C),
        )
        if not support_ok:
            return IntentResult(
                intent=ChemistryIntent.METALLOTHERMIC_STEP,
                status="refused",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "reason": self.C6_ABOVE_CROSSOVER_REFUSAL,
                    "reason_refused": self.C6_ABOVE_CROSSOVER_REFUSAL,
                    "reaction_family": REACTION_FAMILY_C6_MG,
                    "back_reduction": True,
                    **support_diagnostic,
                },
            )
        mol_Al_control = float(controls.get("mol_Al_produced") or 0.0)
        if not math.isfinite(mol_Al_control):
            mol_Al_control = 0.0
        mol_Al_available = max(0.0, float(metal_mol.get("Al", 0.0) or 0.0))
        mol_Al_produced = min(mol_Al_control, mol_Al_available)
        mol_SiO2_available = self._available_mol(
            "SiO2",
            composition_kg,
            true_available_mol,
            molar_mass,
        )
        SiO2_available_kg = mol_SiO2_available * molar_mass["SiO2"] / 1000.0

        # kg view of the freshly produced Al for the legacy gate
        # (Al_produced_kg > 0.01).
        Al_produced_kg = mol_Al_produced * molar_mass["Al"] / 1000.0
        if SiO2_available_kg <= 0.1 or Al_produced_kg <= 0.01:
            return self._empty_result(
                "c6_back_reduction skipped: SiO2 <= 0.1 kg or Al <= 0.01 kg",
                control_audit=control_audit,
                diagnostic={
                    "reaction_family": REACTION_FAMILY_C6_MG,
                    "back_reduction": True,
                    "mol_Al_control": mol_Al_control,
                    "mol_Al_available": mol_Al_available,
                },
            )

        # Legacy:
        #   mol_Al_for_back = (Al_produced_kg * 0.30 / M_Al * 1000)
        #   mol_SiO2_available = SiO2_available_kg / M_SiO2 * 1000
        #   mol_SiO2_consumed = min(mol_Al_for_back * 3/4, mol_SiO2_avail)
        mol_Al_for_back = (
            Al_produced_kg
            * self.BACK_REDUCTION_FRACTION
            / molar_mass["Al"]
            * 1000.0
        )
        mol_SiO2_consumed = min(
            mol_Al_for_back * 3.0 / 4.0, mol_SiO2_available
        )
        mol_Al_consumed = mol_SiO2_consumed * 4.0 / 3.0
        mol_Al2O3_regenerated = mol_SiO2_consumed * 2.0 / 3.0
        mol_Si_produced = mol_SiO2_consumed
        if mol_SiO2_consumed <= 0.0:
            return self._empty_result(
                "c6_back_reduction skipped: SiO2 consumed = 0 after stoich cap",
                control_audit=control_audit,
            )

        debits = {
            "process.metal_phase": {"Al": mol_Al_consumed},
            "process.cleaned_melt": {"SiO2": mol_SiO2_consumed},
        }
        credits = {
            "process.cleaned_melt": {"Al2O3": mol_Al2O3_regenerated},
            "process.metal_phase": {"Si": mol_Si_produced},
        }

        # kg-native diagnostic for snapshot parity.
        Al_consumed_kg = mol_Al_consumed * molar_mass["Al"] / 1000.0
        SiO2_consumed_kg = mol_SiO2_consumed * molar_mass["SiO2"] / 1000.0
        Al2O3_regenerated_kg = (
            mol_Al2O3_regenerated * molar_mass["Al2O3"] / 1000.0
        )
        Si_produced_kg = mol_Si_produced * molar_mass["Si"] / 1000.0

        atom_proof = build_atom_balance_proof(
            debits, credits, registry, resolve_species_formula,
        )
        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason="c6_al_si_back_reduction",
            atom_balance_proof=atom_proof,
        )
        return IntentResult(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic={
                "reaction_family": REACTION_FAMILY_C6_MG,
                "back_reduction": True,
                "mol_Al_control": mol_Al_control,
                "mol_Al_available": mol_Al_available,
                "Al_consumed_kg": Al_consumed_kg,
                "oxide_consumed_kg": SiO2_consumed_kg,
                "Al2O3_regenerated_kg": Al2O3_regenerated_kg,
                "Si_produced_kg": Si_produced_kg,
            },
        )

    # ------------------------------------------------------------------
    # Helpers shared with the other authoritative providers.
    # ------------------------------------------------------------------

    @staticmethod
    def _wt_pct_from_kg(
        composition_kg: Mapping[str, float], total_kg: float,
    ) -> dict[str, float]:
        """Return weight-percent dict from kg view.

        Mirrors :meth:`MeltState.composition_wt_pct` semantics for the
        keys the C3 / C6 paths read (``K2O``, ``Na2O``, ``MgO``).
        """

        if total_kg <= 0.0:
            return {}
        return {sp: (kg / total_kg) * 100.0 for sp, kg in composition_kg.items()}

    @staticmethod
    def _product_cap_kg_for_solubility(
        *,
        total_kg: float,
        current_product_kg: float,
        removed_kg: float,
        added_product_kg: float,
        removed_kg_per_product_kg: float,
        solubility_wt_pct: float,
    ) -> float:
        limit = float(solubility_wt_pct) / 100.0
        if limit <= 0.0 or limit >= 1.0:
            raise ValueError("solubility_wt_pct must be between 0 and 100")
        denominator = 1.0 - limit * (1.0 - float(removed_kg_per_product_kg))
        if denominator <= 0.0:
            return 0.0
        numerator = (
            limit * (float(total_kg) - float(removed_kg) + float(added_product_kg))
            - (float(current_product_kg) + float(added_product_kg))
        )
        return max(0.0, numerator / denominator)

    def _true_available_mol_by_species(
        self,
        controls: Mapping[str, Any],
    ) -> Mapping[str, float]:
        raw = controls.get("true_available_mol_by_species")
        if raw is None:
            return {}
        if not isinstance(raw, Mapping):
            raise TypeError("true_available_mol_by_species must be a mapping")
        available: dict[str, float] = {}
        for species, value in raw.items():
            mol = float(value)
            if not math.isfinite(mol):
                raise ValueError(
                    f"true_available_mol_by_species[{species!r}] is non-finite"
                )
            available[str(species)] = max(0.0, mol)
        return available

    def _available_mol(
        self,
        species: str,
        composition_kg: Mapping[str, float],
        true_available_mol: Mapping[str, float],
        molar_mass: Mapping[str, float],
    ) -> float:
        if species in true_available_mol:
            return float(true_available_mol[species])
        mass_g_per_mol = float(molar_mass[species])
        if mass_g_per_mol <= 0.0:
            return 0.0
        return (
            max(0.0, float(composition_kg.get(species, 0.0)))
            / mass_g_per_mol
            * 1000.0
        )

    @staticmethod
    def _liquid_fraction_blocks_metallothermic(
        controls: Mapping[str, Any],
    ) -> bool:
        liquid_fraction = controls.get("liquid_fraction")
        return (
            liquid_fraction is not None
            and melt_regime(
                liquid_fraction=liquid_fraction,
                epsilon=0.0,
                invalid_liquid_fraction_regime=MeltRegime.PARTIAL,
            )
            == MeltRegime.FROZEN
        )

    @staticmethod
    def _with_melt_regime_diagnostic(
        result: IntentResult,
        controls: Mapping[str, Any],
    ) -> IntentResult:
        liquid_fraction = controls.get("liquid_fraction")
        if liquid_fraction is None:
            return result
        diagnostic: dict[str, Any] = {}
        melt_regime(
            liquid_fraction=liquid_fraction,
            epsilon=0.0,
            invalid_liquid_fraction_regime=MeltRegime.PARTIAL,
            diagnostic=diagnostic,
            diagnostic_site='engines.builtin.metallothermic_step.liquid_fraction',
            legacy_predicate='liquid_fraction == 0.0',
        )
        if not diagnostic:
            return result
        payload = dict(result.diagnostic or {})
        existing = payload.get("melt_regime_predicate_divergences")
        if existing:
            diagnostic["melt_regime_predicate_divergences"] = [
                *tuple(existing),
                *diagnostic["melt_regime_predicate_divergences"],
            ]
        payload.update(diagnostic)
        return replace(result, diagnostic=payload)

    @staticmethod
    def _no_liquid_phase_refusal(
        reaction_family: str,
        *,
        control_audit=None,
    ) -> IntentResult:
        return IntentResult(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            status="refused",
            transition=None,
            control_audit=control_audit,
            diagnostic={
                "reason": "no_liquid_phase",
                "reason_refused": "no_liquid_phase",
                "reaction_family": reaction_family,
                "liquid_fraction": 0.0,
                "reagent_consumed_kg": 0.0,
                "oxide_reduced_kg": 0.0,
                "metal_produced_kg": 0.0,
            },
        )

    @staticmethod
    def _empty_result(
        reason: str,
        *,
        control_audit=None,
        diagnostic: Mapping[str, Any] | None = None,
        warnings: tuple[str, ...] = (),
    ) -> IntentResult:
        payload = {"reason_skipped": reason}
        if diagnostic:
            payload.update(dict(diagnostic))
        return IntentResult(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            status="ok",
            transition=None,
            control_audit=control_audit,
            diagnostic=payload,
            warnings=tuple(warnings),
        )

    @classmethod
    def _reduction_margin_kj_per_mol_o2(
        cls,
        reductant: str,
        target_oxide: str,
        temperature_C: float,
    ) -> float:
        target_metal = TARGET_OXIDE_TO_METAL[target_oxide]
        return cls._delta_g_kj_per_mol_o2(
            target_metal,
            temperature_C,
        ) - cls._delta_g_kj_per_mol_o2(reductant, temperature_C)

    @staticmethod
    def _refused_result(
        reason: str,
        *,
        reductant: str,
        target_oxide: str,
        temperature_C: float,
        margin_kJ_per_mol_O2: float,
        crossover_temperature_C: float | None,
        control_audit=None,
        extra_diagnostic: Mapping[str, Any] | None = None,
        warnings: tuple[str, ...] = (),
    ) -> IntentResult:
        diagnostic = {
            "reason_refused": reason,
            "reductant": reductant,
            "target_oxide": target_oxide,
            "temperature_C": float(temperature_C),
            "margin_kJ_per_mol_O2": float(margin_kJ_per_mol_O2),
            "crossover_temperature_C": crossover_temperature_C,
        }
        if extra_diagnostic:
            diagnostic.update(dict(extra_diagnostic))
        return IntentResult(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            status="refused",
            transition=None,
            control_audit=control_audit,
            diagnostic=diagnostic,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _build_atom_balance_proof(
        debits: Mapping[str, Mapping[str, float]],
        credits: Mapping[str, Mapping[str, float]],
        registry: Mapping[str, Any],
        resolve_species_formula,
    ) -> dict[str, float]:
        """Delegate to the shared :func:`build_atom_balance_proof` helper.

        Atom balance for the six reactions:

        * ``2 K + FeO -> K2O + Fe`` -- K: -2 + 2 = 0; Fe: -1 + 1 = 0;
          O: -1 + 1 = 0.
        * ``2 Na + FeO -> Na2O + Fe`` -- Na: -2 + 2 = 0; Fe: -1 + 1 = 0;
          O: -1 + 1 = 0.
        * ``6 Na + Cr2O3 -> 3 Na2O + 2 Cr`` -- Na: -6 + 6 = 0; Cr:
          -2 + 2 = 0; O: -3 + 3 = 0.
        * ``4 Na + TiO2 -> 2 Na2O + Ti`` -- Na: -4 + 4 = 0; Ti:
          -1 + 1 = 0; O: -2 + 2 = 0.
        * ``3 Mg + Al2O3 -> 3 MgO + 2 Al`` -- Mg: -3 + 3 = 0; Al:
          -2 + 2 = 0; O: -3 + 3 = 0.
        * ``4 Al + 3 SiO2 -> 2 Al2O3 + 3 Si`` -- Al: -4 + 4 = 0; Si:
          -3 + 3 = 0; O: -6 + 6 = 0.

        Net per element: 0 for every reaction. The combined
        C3 Na-shuttle bundle sums each side independently so the net
        stays 0 as well.
        """

        return build_atom_balance_proof(
            debits, credits, registry, resolve_species_formula
        )

    @classmethod
    def _resolve_na_target_priority(
        cls,
        controls: Mapping[str, Any],
        temperature_C: float,
        composition_mol: Mapping[str, float],
    ) -> tuple[str, tuple[str, ...], dict[str, Any]]:
        requested_targets = controls.get("target_oxides")
        if requested_targets is not None:
            target_set = cls._normalize_na_targets(requested_targets)
            target_stage = str(
                controls.get("na_target_stage") or "explicit"
            )
        else:
            target_stage = str(
                controls.get("na_target_stage")
                or controls.get("target_stage")
                or NA_TARGET_CR_TI
            )
            target_set = NA_STAGE_TARGETS.get(target_stage, ())

        thermo_priority, thermo_audit = cls._na_thermo_priority(
            target_set,
            temperature_C,
            composition_mol,
        )
        return target_stage, thermo_priority, thermo_audit

    @staticmethod
    def _normalize_na_targets(raw_targets: Any) -> tuple[str, ...]:
        if isinstance(raw_targets, str):
            candidates = [part.strip() for part in raw_targets.split(",")]
        else:
            candidates = [str(part).strip() for part in raw_targets]
        targets: list[str] = []
        seen: set[str] = set()
        for target in candidates:
            if target not in NA_TARGET_TO_METAL or target in seen:
                continue
            targets.append(target)
            seen.add(target)
        # SC-47: an explicitly-provided target_oxides list must NOT silently
        # widen to the default Cr/Ti set when it normalises to empty (an
        # empty list, or every entry unrecognised). Mirroring the BUG-140
        # contract, an explicit-but-empty selectivity filter means "reduce
        # nothing" (empty priority -> no Na reduction downstream), not "fall
        # back to the default targets" -- silently reducing the WRONG oxides
        # is a fallback the mandate forbids. The None case (no target_oxides
        # provided at all) is handled by the caller via `is not None`, which
        # selects the stage default set; that path is unchanged.
        return tuple(targets)

    @staticmethod
    def _ellingham_fit_diagnostic(
        extrapolations: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        if not extrapolations:
            return {}
        actual_extrapolations = {
            str(pair): dict(data)
            for pair, data in extrapolations.items()
            if data.get("authority_status") == "extrapolation_limited"
        }
        diagnostic = {
            "ellingham_authority_limits": {
                str(pair): dict(data)
                for pair, data in extrapolations.items()
            },
            "ellingham_authority": ellingham_authority_diagnostic(
                {
                    str(pair): dict(data)
                    for pair, data in extrapolations.items()
                },
                consumer="builtin-metallothermic-step",
            ),
        }
        if actual_extrapolations:
            diagnostic["ellingham_extrapolated_beyond_fit_range_K"] = (
                actual_extrapolations
            )
        return diagnostic

    @staticmethod
    def _ellingham_fit_warnings(
        extrapolations: Mapping[str, Mapping[str, Any]],
    ) -> tuple[str, ...]:
        warnings: list[str] = []
        for pair, data in extrapolations.items():
            if data.get("authority_status") != "extrapolation_limited":
                continue
            valid_low, valid_high = data["fit_range_K"]
            warnings.append(
                f"{pair} Ellingham JANAF high-T fit extrapolated beyond "
                f"fit_range_K [{valid_low:g}, {valid_high:g}] at "
                f"{float(data['temperature_K']):.2f} K"
            )
        return tuple(warnings)

    @classmethod
    def _ellingham_pair_fit_extrapolations(
        cls,
        reductant: str,
        target_oxides: tuple[str, ...],
        temperature_C: float,
    ) -> dict[str, dict[str, Any]]:
        temperature_K = float(temperature_C) + CELSIUS_TO_KELVIN_OFFSET
        flagged: dict[str, dict[str, Any]] = {}
        for target_oxide in target_oxides:
            target_metal = TARGET_OXIDE_TO_METAL[target_oxide]
            pair = f"{reductant}/{target_oxide}"
            limited_species: dict[str, dict[str, Any]] = {}
            for species in (reductant, target_metal):
                extrapolation = ellingham_authority_limit(
                    temperature_K,
                    species=species,
                    consumer="builtin-metallothermic-step",
                )
                if extrapolation is not None:
                    limited_species[species] = extrapolation
            if not limited_species:
                continue
            extrapolation_limited = any(
                data["authority_status"] == "extrapolation_limited"
                for data in limited_species.values()
            )
            pair_limit = {
                "temperature_K": temperature_K,
                "reductant": reductant,
                "target_oxide": target_oxide,
                "target_metal": target_metal,
                "limited_species": limited_species,
                "consumer": "builtin-metallothermic-step",
                "authority_status": (
                    "extrapolation_limited"
                    if extrapolation_limited
                    else "reconstructed_limited"
                ),
                ELLINGHAM_RECONSTRUCTED_AUTHORITY_FLAG: any(
                    data.get(ELLINGHAM_RECONSTRUCTED_AUTHORITY_FLAG) is True
                    for data in limited_species.values()
                ),
            }
            if extrapolation_limited:
                fit_lows_highs = [
                    ellingham_fit_range_K(species)
                    for species in (reductant, target_metal)
                ]
                pair_limit["fit_range_K"] = (
                    max(bounds[0] for bounds in fit_lows_highs),
                    min(bounds[1] for bounds in fit_lows_highs),
                )
            flagged[pair] = pair_limit
        return flagged

    @staticmethod
    def _delta_g_kj_per_mol_o2(metal: str, temperature_C: float) -> float:
        return ellingham_delta_g_kj_per_mol_o2(
            metal,
            float(temperature_C) + CELSIUS_TO_KELVIN_OFFSET,
        )

    @staticmethod
    def _crossover_temperature_C(
        reagent_metal: str,
        target_metal: str,
    ) -> float | None:
        """Return the oxide-stability crossover temperature in Celsius.

        V1c JANAF refit crossovers used by the alkali-shuttle diagnostic:
        K/Fe = 836.25 C, Na/Fe = 1181.5 C. Above a pair's crossover, the
        reagent oxide is less stable than the target oxide, so reduction is
        thermodynamically disfavored; current recipe gates remain handled
        outside this helper. Returns None when the algebraic root falls
        outside the shared phase-valid JANAF fit segment.
        """
        for reagent_segment in ellingham_fit_segments(reagent_metal):
            for target_segment in ellingham_fit_segments(target_metal):
                low_K = max(
                    reagent_segment.range_K[0],
                    target_segment.range_K[0],
                )
                high_K = min(
                    reagent_segment.range_K[1],
                    target_segment.range_K[1],
                )
                if low_K > high_K:
                    continue
                dS_delta = (
                    reagent_segment.dS_f_kJ_per_mol_K_per_mol_O2
                    - target_segment.dS_f_kJ_per_mol_K_per_mol_O2
                )
                if abs(dS_delta) < 1e-15:
                    continue
                root_K = (
                    reagent_segment.dH_f_kJ_per_mol_O2
                    - target_segment.dH_f_kJ_per_mol_O2
                ) / dS_delta
                if low_K <= root_K <= high_K:
                    return root_K - CELSIUS_TO_KELVIN_OFFSET
        return None

    @classmethod
    def _na_thermo_priority(
        cls,
        targets: tuple[str, ...],
        temperature_C: float,
        composition_mol: Mapping[str, float],
    ) -> tuple[tuple[str, ...], dict[str, Any]]:
        temperature_K = float(temperature_C) + CELSIUS_TO_KELVIN_OFFSET
        na_standard_delta_g = cls._delta_g_kj_per_mol_o2(
            "Na",
            temperature_C,
        )
        na_activity_shift = na_reductant_activity_shift_kj_per_mol_o2(
            temperature_K,
            composition_mol,
        )
        na_activity = melt_oxide_activity("Na2O", composition_mol)
        na_activity_value = (
            MELT_OXIDE_ACTIVITY_COEFFICIENTS["Na2O"].gamma
            if na_activity is None
            else na_activity.activity
        )
        na_x_single_cation = (
            0.0 if na_activity is None else na_activity.x_single_cation
        )
        delta_g: dict[str, float] = {
            "Na2O": na_standard_delta_g + na_activity_shift
        }
        activity_shifted_margin: dict[str, float] = {}
        margin: dict[str, float] = {}
        for oxide in targets:
            metal = NA_TARGET_TO_METAL[oxide]
            delta_g[oxide] = cls._delta_g_kj_per_mol_o2(
                metal,
                temperature_C,
            )
            activity_shifted_margin[oxide] = delta_g[oxide] - delta_g["Na2O"]
            margin[oxide] = activity_shifted_margin[oxide]

        # Executable acceptance is keyed on the reduction margin, not on
        # Ellingham fit authority.  A target X is reducible only when
        # ΔG_X - ΔG_reductant is positive per mol O2; if the margin is <= 0,
        # X's oxide is more stable and must be refused even when its fit is
        # in-band.  TiO2 is the C3 stop case exposed by the 2026-07-09
        # re-ground: NaO0.5 activity can make the shifted Na/Ti diagnostic
        # near zero, but the raw Na/Ti and Cr/Ti ladder margins remain
        # strongly negative, so C3 may accept Cr2O3 while refusing TiO2.
        # FeO likewise keeps the raw Na/Fe crossover executable: the
        # NaO0.5 activity diagnostic must not turn a standard-state-negative
        # dispatch above 1181.5 C into an accepted reduction.
        if "FeO" in margin:
            margin["FeO"] = min(
                margin["FeO"],
                delta_g["FeO"] - na_standard_delta_g,
            )
        if "TiO2" in margin:
            ti_margins = [
                margin["TiO2"],
                delta_g["TiO2"] - na_standard_delta_g,
            ]
            if "Cr2O3" in delta_g:
                ti_margins.append(delta_g["TiO2"] - delta_g["Cr2O3"])
            margin["TiO2"] = min(ti_margins)

        index = {oxide: idx for idx, oxide in enumerate(targets)}
        priority = tuple(
            sorted(
                targets,
                key=lambda oxide: (delta_g[oxide], -index[oxide]),
                reverse=True,
            )
        )
        return priority, {
            "priority": priority,
            "deltaG": delta_g,
            "standard_deltaG": {"Na2O": na_standard_delta_g},
            "Na2O_activity_gamma": (
                MELT_OXIDE_ACTIVITY_COEFFICIENTS["Na2O"].gamma
            ),
            "Na2O_activity_component": (
                MELT_OXIDE_ACTIVITY_COEFFICIENTS[
                    "Na2O"
                ].single_cation_component
            ),
            "Na2O_activity_X_single_cation": na_x_single_cation,
            "Na2O_activity": na_activity_value,
            "Na2O_activity_shift_kJ_per_mol_O2": na_activity_shift,
            "Na2O_activity_limitation": MELT_OXIDE_ACTIVITY_LIMITATION,
            "na_activity_shifted_margin_kJ_per_mol_O2": activity_shifted_margin,
            "margin": margin,
            "ellingham_extrapolated_beyond_fit_range_K": (
                cls._ellingham_pair_fit_extrapolations(
                    "Na",
                    targets,
                    temperature_C,
                )
            ),
        }
