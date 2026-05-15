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
* C3 Na-shuttle: ``6 Na + Cr2O3 -> 3 Na2O + 2 Cr`` (Cr2O3 reduced first;
  ``ΔG°f -500`` vs Na2O ``-320``); then ``4 Na + TiO2 -> 2 Na2O + Ti``
  with 0.75 accessibility factor (highest-priority experimental
  question -- legacy comment ``[THERMO-10]``). Na2O saturated at 10 wt%.
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
* ``'c3_na_shuttle'`` -- C3 Na-shuttle injection (Cr2O3 first, then
  TiO2 with accessibility factor).
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
reduced + credit alkali-oxide / MgO / regenerated Al2O3 coproducts),
``process.metal_phase`` (credit Fe/Cr/Ti/Al/Si metals + debit Al on
back-reduction), ``process.reagent_inventory`` (debit K/Na/Mg consumed).
Every account named in any of the five legacy ``_record_atom_transition``
calls inside ``_shuttle_inject_K``, ``_shuttle_inject_Na``, and
``_step_thermite`` lands in this set.  The hardened kernel account-
filter (since commit ``a259f80``) will raise
:class:`AccountFilterViolation` if a future refactor adds a fourth
account; the declared set is the first-line gate.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from engines.builtin._common import (
    build_atom_balance_proof,
    composition_kg_from_account_view,
    diagnostic_control_audit,
    dispatch_reaction_family,
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


class BuiltinMetallothermicStepProvider(ChemistryProvider):
    """Authoritative ``METALLOTHERMIC_STEP`` provider.

    See module docstring.  Stateless -- per-call inputs (T, P,
    reagent-available-kg, dt_hr, reaction_family, back_reduction flag)
    arrive through :class:`IntentRequest`; the same instance serves
    every C3 / C6 tick without holding simulator references.
    """

    name = "builtin-metallothermic-step"

    # The three accounts the legacy _shuttle_inject_K / _shuttle_inject_Na
    # / _step_thermite collectively touch on debit or credit side
    # across the five legacy _record_atom_transition calls. The kernel's
    # account-filter gate will reject any proposal that names a fourth
    # account here.
    DECLARED_ACCOUNTS = frozenset({
        "process.cleaned_melt",
        "process.metal_phase",
        "process.reagent_inventory",
    })

    # Legacy constants reproduced verbatim from simulator/extraction.py
    # for the C3 / C6 reactions.
    K2O_SOLUBILITY_WT_PCT = 10.0
    NA2O_SOLUBILITY_WT_PCT = 10.0
    TI_ACCESSIBILITY = 0.75
    BACK_REDUCTION_FRACTION = 0.30

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="builtin-metallothermic-step",
            intents=frozenset({ChemistryIntent.METALLOTHERMIC_STEP}),
            is_authoritative_for=frozenset(
                {ChemistryIntent.METALLOTHERMIC_STEP}
            ),
            declared_accounts=self.DECLARED_ACCOUNTS,
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

        controls = unpack_controls(request)

        # Reaction-family early-exit: shared with stage0_pretreatment.py.
        # The metallothermic shuttles run solubility-limit + reagent-mass
        # arithmetic only; no fO2 dependency (legacy _shuttle_inject_K /
        # _shuttle_inject_Na / _step_thermite do not consult fO2 either).
        # Audit reports T/P verbatim with the diagnostic-only note.
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

        # Project the melt's kg view from the mol account (the legacy
        # path reads self.melt.composition_kg, which is the simulator's
        # projection of process.cleaned_melt).  Same projection the
        # _common.composition_wt_pct_from_account_view helper produces
        # internally; the kg dict + total_kg pair is the shape the C3
        # solubility-limit math reads.
        composition_kg, total_kg = composition_kg_from_account_view(
            request.account_view,
            "process.cleaned_melt",
        )
        composition_wt_pct = self._wt_pct_from_kg(composition_kg, total_kg)

        if reaction_family == REACTION_FAMILY_C3_K:
            return self._dispatch_c3_k(
                composition_kg,
                composition_wt_pct,
                total_kg,
                controls,
                MOLAR_MASS,
                registry,
                resolve_species_formula,
                control_audit,
            )
        if reaction_family == REACTION_FAMILY_C3_NA:
            return self._dispatch_c3_na(
                composition_kg,
                composition_wt_pct,
                total_kg,
                controls,
                MOLAR_MASS,
                registry,
                resolve_species_formula,
                control_audit,
            )
        # reaction_family == REACTION_FAMILY_C6_MG
        back_reduction = bool(controls.get("back_reduction") or False)
        if back_reduction:
            return self._dispatch_c6_back_reduction(
                composition_kg,
                metal_mol,
                controls,
                MOLAR_MASS,
                registry,
                resolve_species_formula,
                control_audit,
            )
        return self._dispatch_c6_mg_primary(
            composition_kg,
            composition_wt_pct,
            controls,
            MOLAR_MASS,
            registry,
            resolve_species_formula,
            control_audit,
        )

    # ------------------------------------------------------------------
    # C3 K-shuttle dispatch.  Mirrors _shuttle_inject_K line-for-line
    # (see simulator/extraction.py).
    # ------------------------------------------------------------------

    def _dispatch_c3_k(
        self,
        composition_kg: Mapping[str, float],
        composition_wt_pct: Mapping[str, float],
        total_kg: float,
        controls: Mapping[str, Any],
        molar_mass: Mapping[str, float],
        registry: Mapping[str, Any],
        resolve_species_formula,
        control_audit,
    ) -> IntentResult:
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

        FeO_available_kg = composition_kg.get("FeO", 0.0)
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

        # Stoichiometric integration in mol space, line-for-line with the
        # legacy ``_shuttle_inject_K``: legacy uses ``MOLAR_MASS`` in
        # g/mol so it scales mass_kg -> mol via ``kg / (g/mol) * 1000``
        # (== ``kg * 1000 g/kg / (g/mol)`` == mol).  Provider keeps the
        # same expressions so the worst-case parity delta is bounded by
        # IEEE-754 round-off on the same operand sequence the legacy
        # already pinned in the smoke run.
        mol_K = K_inject_kg / molar_mass["K"] * 1000.0
        mol_FeO_available = (
            FeO_available_kg / molar_mass["FeO"] * 1000.0
            if molar_mass["FeO"] > 0.0
            else 0.0
        )
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
        return IntentResult(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic={
                "reaction_family": REACTION_FAMILY_C3_K,
                "reagent_consumed_kg": K_used_kg,
                "oxide_reduced_kg": FeO_removed_kg,
                "metal_produced_kg": Fe_produced_kg,
                "metal_species": "Fe",
            },
        )

    # ------------------------------------------------------------------
    # C3 Na-shuttle dispatch.  Mirrors _shuttle_inject_Na line-for-line
    # (see simulator/extraction.py); reduces Cr2O3 first, then TiO2.
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
        composition_wt_pct: Mapping[str, float],
        total_kg: float,
        controls: Mapping[str, Any],
        molar_mass: Mapping[str, float],
        registry: Mapping[str, Any],
        resolve_species_formula,
        control_audit,
    ) -> IntentResult:
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

        Na2O_max_kg = max(
            0.0,
            total_kg * self.NA2O_SOLUBILITY_WT_PCT / 100.0
            - composition_kg.get("Na2O", 0.0),
        )
        Na_for_Na2O_limit_kg = (
            Na2O_max_kg * (2 * molar_mass["Na"] / molar_mass["Na2O"])
        )

        TiO2_available_kg = composition_kg.get("TiO2", 0.0)
        Cr2O3_available_kg = composition_kg.get("Cr2O3", 0.0)

        Na_available_this_hr = Na_available_kg / 3.0
        Na_inject_kg = max(
            0.0, min(Na_available_this_hr, Na_for_Na2O_limit_kg)
        )
        if Na_inject_kg < 0.001:
            return self._empty_result(
                "c3_na_shuttle skipped: injection floor (<0.001 kg Na)",
                control_audit=control_audit,
            )

        mol_Na = Na_inject_kg / molar_mass["Na"] * 1000.0

        # Accumulators -- both reactions write into a single proposal.
        total_Na_used_mol = 0.0
        total_Na2O_added_mol = 0.0
        total_Cr2O3_removed_mol = 0.0
        total_TiO2_removed_mol = 0.0
        total_Cr_produced_mol = 0.0
        total_Ti_produced_mol = 0.0

        # --- Reaction 1: 6 Na + Cr2O3 -> 3 Na2O + 2 Cr ------------------
        if Cr2O3_available_kg > 0.01 and mol_Na > 0.1:
            mol_Cr2O3 = (
                Cr2O3_available_kg / molar_mass["Cr2O3"] * 1000.0
            )
            mol_Cr2O3_reduced = min(mol_Na / 6.0, mol_Cr2O3)
            mol_Na_for_Cr = mol_Cr2O3_reduced * 6.0
            mol_Na2O_from_Cr = mol_Cr2O3_reduced * 3.0
            mol_Cr_produced = mol_Cr2O3_reduced * 2.0

            total_Na_used_mol += mol_Na_for_Cr
            total_Na2O_added_mol += mol_Na2O_from_Cr
            total_Cr2O3_removed_mol += mol_Cr2O3_reduced
            total_Cr_produced_mol += mol_Cr_produced
            mol_Na -= mol_Na_for_Cr

        # --- Reaction 2: 4 Na + TiO2 -> 2 Na2O + Ti  (75 % accessibility)
        if TiO2_available_kg > 0.01 and mol_Na > 0.1:
            mol_TiO2 = (
                TiO2_available_kg / molar_mass["TiO2"] * 1000.0
            )
            mol_TiO2_accessible = mol_TiO2 * self.TI_ACCESSIBILITY
            mol_TiO2_reduced = min(mol_Na / 4.0, mol_TiO2_accessible)
            mol_Na_for_Ti = mol_TiO2_reduced * 4.0
            mol_Na2O_from_Ti = mol_TiO2_reduced * 2.0
            mol_Ti_produced = mol_TiO2_reduced

            total_Na_used_mol += mol_Na_for_Ti
            total_Na2O_added_mol += mol_Na2O_from_Ti
            total_TiO2_removed_mol += mol_TiO2_reduced
            total_Ti_produced_mol += mol_Ti_produced

        if total_Na_used_mol <= 0.0:
            return self._empty_result(
                "c3_na_shuttle skipped: no oxide accepted Na reduction",
                control_audit=control_audit,
            )

        # Build mol-native proposal.  Both reactions converge on the
        # same three accounts; the per-oxide split lives in the
        # diagnostic dict so callers can replay it.
        debits: dict[str, dict[str, float]] = {
            "process.reagent_inventory": {"Na": total_Na_used_mol},
            "process.cleaned_melt": {},
        }
        if total_Cr2O3_removed_mol > 0.0:
            debits["process.cleaned_melt"]["Cr2O3"] = total_Cr2O3_removed_mol
        if total_TiO2_removed_mol > 0.0:
            debits["process.cleaned_melt"]["TiO2"] = total_TiO2_removed_mol
        if not debits["process.cleaned_melt"]:
            del debits["process.cleaned_melt"]

        credits: dict[str, dict[str, float]] = {
            "process.cleaned_melt": {"Na2O": total_Na2O_added_mol},
            "process.metal_phase": {},
        }
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
        Cr2O3_removed_kg = (
            total_Cr2O3_removed_mol * molar_mass["Cr2O3"] / 1000.0
        )
        TiO2_removed_kg = (
            total_TiO2_removed_mol * molar_mass["TiO2"] / 1000.0
        )
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
        return IntentResult(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic={
                "reaction_family": REACTION_FAMILY_C3_NA,
                "reagent_consumed_kg": Na_used_kg,
                "oxide_reduced_kg": Cr2O3_removed_kg + TiO2_removed_kg,
                "metal_produced_kg": Cr_produced_kg + Ti_produced_kg,
                "per_oxide_reduced_kg": {
                    "Cr2O3": Cr2O3_removed_kg,
                    "TiO2": TiO2_removed_kg,
                },
                "per_metal_produced_kg": {
                    "Cr": Cr_produced_kg,
                    "Ti": Ti_produced_kg,
                },
            },
        )

    # ------------------------------------------------------------------
    # C6 Mg thermite -- primary reaction. Mirrors _step_thermite up
    # through the primary _record_atom_transition call. The back-
    # reduction lives in its own _dispatch_c6_back_reduction (the
    # caller orchestrates the two-call sequence with the mol_Al_produced
    # value carried between them).
    # ------------------------------------------------------------------

    def _dispatch_c6_mg_primary(
        self,
        composition_kg: Mapping[str, float],
        composition_wt_pct: Mapping[str, float],
        controls: Mapping[str, Any],
        molar_mass: Mapping[str, float],
        registry: Mapping[str, Any],
        resolve_species_formula,
        control_audit,
    ) -> IntentResult:
        import math

        Mg_available_kg = float(controls.get("reagent_available_kg") or 0.0)
        if Mg_available_kg <= 0.01:
            return self._empty_result(
                "c6_mg_thermite skipped: Mg reagent below 0.01 kg threshold",
                control_audit=control_audit,
            )

        Al2O3_available_kg = composition_kg.get("Al2O3", 0.0)
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
        mol_Al2O3_available = (
            Al2O3_available_kg / molar_mass["Al2O3"] * 1000.0
        )
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
        metal_mol: Mapping[str, float],
        controls: Mapping[str, Any],
        molar_mass: Mapping[str, float],
        registry: Mapping[str, Any],
        resolve_species_formula,
        control_audit,
    ) -> IntentResult:
        mol_Al_produced = float(controls.get("mol_Al_produced") or 0.0)
        SiO2_available_kg = composition_kg.get("SiO2", 0.0)

        # kg view of the freshly produced Al for the legacy gate
        # (Al_produced_kg > 0.01).
        Al_produced_kg = mol_Al_produced * molar_mass["Al"] / 1000.0
        if SiO2_available_kg <= 0.1 or Al_produced_kg <= 0.01:
            return self._empty_result(
                "c6_back_reduction skipped: SiO2 <= 0.1 kg or Al <= 0.01 kg",
                control_audit=control_audit,
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
        mol_SiO2_available = (
            SiO2_available_kg / molar_mass["SiO2"] * 1000.0
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
    def _empty_result(reason: str, *, control_audit=None) -> IntentResult:
        return IntentResult(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            status="ok",
            transition=None,
            control_audit=control_audit,
            diagnostic={"reason_skipped": reason},
        )

    @staticmethod
    def _build_atom_balance_proof(
        debits: Mapping[str, Mapping[str, float]],
        credits: Mapping[str, Mapping[str, float]],
        registry: Mapping[str, Any],
        resolve_species_formula,
    ) -> dict[str, float]:
        """Delegate to the shared :func:`build_atom_balance_proof` helper.

        Atom balance for the five reactions:

        * ``2 K + FeO -> K2O + Fe`` -- K: -2 + 2 = 0; Fe: -1 + 1 = 0;
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
        C3 Na-shuttle (Cr2O3 + TiO2) bundle sums each side
        independently so the net stays 0 as well.
        """

        return build_atom_balance_proof(
            debits, credits, registry, resolve_species_formula
        )
