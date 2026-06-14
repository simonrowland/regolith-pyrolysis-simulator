"""Builtin STAGE0_PRETREATMENT provider (volatile / salt / sulfide / halide cleanup).

Kernel-registered provider that owns the ``STAGE0_PRETREATMENT`` intent
per binding spec §2 ("Volatile/salt/sulfide/halide cleanup, separation
into BatchInventory buckets") and §3 (Builtin authoritative). Mirrors
the Stage 0 cleanup stoichiometry in
:meth:`PyrolysisSimulator._record_stage0_oxidation_transitions`,
:meth:`PyrolysisSimulator._record_stage0_carbon_cleanup_transitions`,
and :meth:`PyrolysisSimulator._record_stage0_perchlorate_cleanup_transitions`
line-for-line -- this is a refactor of where the
:class:`LedgerTransitionProposal` is built, not a re-derivation of the
Stage 0 cleanup physics.  The spec-driven product / oxidant tables are
already built by the legacy
``_apply_stage0_carbon_reductant_reactions`` /
``_apply_stage0_perchlorate_reactions`` /
``_stage0_oxidation_transition_specs`` helpers (operator-explicit,
feedstock-local per binding spec); the provider receives the
already-computed ``products_kg`` / ``oxidant_kg`` / ``salt_products_kg``
/ ``oxygen_products_kg`` payloads via ``control_inputs`` and projects
them onto atom-balanced mol-native debit / credit dicts.

Per binding spec §2 the intent is a single ``STAGE0_PRETREATMENT`` that
covers several reaction families.  The provider receives a
``reaction_family`` discriminator in ``control_inputs`` -- valid values:

* ``'complete_oxidation'`` -- C/H/N-bearing volatile combusted with
  controlled O2; the legacy
  ``_record_stage0_oxidation_transitions`` records one transition per
  feedstock-declared volatile species (CH4, NH3, organic_macromolecule,
  etc.) with the ``_oxidized_stage0_products`` projection
  (CO2 / H2O / N2 + O2 oxidant or coproduct). Each provider dispatch
  handles ONE species (the caller loops over the legacy specs).
* ``'sulfate_carbon'`` -- ``2 SO3 + C -> 2 SO2 + CO`` (sulfate-carbon
  reductive cleanup; the legacy
  ``_apply_stage0_sulfate_carbon_reaction`` builds the spec with
  ``products_kg = {'SO2': extent*M_SO2, 'CO': extent*M_CO}`` and
  ``debits = (('process.stage0_salt_feed', {'SO3': so3_consumed_kg}),
              ('process.reagent_inventory', {'C': c_consumed_kg}))``).
* ``'boudouard'`` -- ``C + CO2 -> 2 CO`` (Boudouard back-reduction; the
  legacy ``_apply_stage0_boudouard_reaction`` builds the spec with
  ``products_kg = {'CO': 2*extent*M_CO}`` and
  ``debits = (('process.reagent_inventory', {'C': c_consumed_kg}),
              ('reservoir.stage0_process_gas', {'CO2': co2_input_kg}))``).
* ``'perchlorate'`` -- ``ClO4 -> Cl + 2 O2`` (Mars perchlorate thermal
  decomposition; the legacy
  ``_apply_stage0_perchlorate_reactions`` builds the spec with
  ``salt_products_kg = {'Cl': extent*M_Cl}`` and
  ``oxygen_products_kg = {'O2': 2*extent*M_O2}`` and
  ``debits = (('process.stage0_perchlorate_feed', {'ClO4': clo4_kg}),)``).

The provider:

- reads its declared Stage 0 accounts via the filtered
  :class:`ProviderAccountView`, although for Stage 0 the view is largely
  empty at dispatch time -- the source accounts (feed buckets) are
  loaded via ``atom_ledger.load_external`` by the caller IMMEDIATELY
  before dispatch (the load is the legacy ``feedstock seeding``
  semantics, not a transition).  Atom-balance is computed entirely from
  the provider's debit/credit dicts.
- reads the spec from ``request.control_inputs``:

  * ``reaction_family`` -- discriminator (see above).
  * ``species`` (complete_oxidation only) -- the volatile species feed
    name (e.g. ``CH4``, ``NH3``, ``organic_macromolecule``); the
    provider expects matching ``feed_kg``, ``products_kg``,
    ``oxidant_kg`` from the legacy
    ``_stage0_oxidation_transition_specs`` projection.
  * ``feed_kg`` (complete_oxidation only) -- kg of feed species
    debited from ``process.stage0_volatile_feed``.
  * ``products_kg`` -- dict of credit species (CO2, H2O, N2 for
    complete_oxidation; SO2, CO for sulfate_carbon; CO for boudouard)
    in kg.  The provider translates kg to mol using the
    ``species_formula_registry`` from the request's account view.
  * ``oxidant_kg`` (complete_oxidation only, optional) -- kg of O2
    consumed from ``reservoir.stage0_oxidant`` when the feed is
    O2-deficient.
  * ``debits`` (sulfate_carbon / boudouard / perchlorate) -- the
    legacy ``spec['debits']`` tuple of
    ``(account, {species: kg})`` entries.  Used verbatim.
  * ``salt_products_kg`` (perchlorate only) -- credit to
    ``terminal.stage0_chloride_salt_phase``.
  * ``oxygen_products_kg`` (perchlorate only) -- credit to
    ``terminal.oxygen_stage0_stored``.

Returns an :class:`IntentResult` with ``transition`` populated by a
single :class:`LedgerTransitionProposal` and a ``diagnostic`` dict
carrying ``reaction_family`` + per-species kg / mol totals.

Out-of-domain handling: if the spec is empty (e.g. a feedstock whose
Stage 0 profile does not match the requested reaction_family -- a
lunar feedstock requested under ``boudouard``), the provider returns
``status='out_of_domain'`` with a warning instead of fabricating a
proposal.  Status ``'ok'`` with ``transition=None`` covers benign
no-op skips (zero-mass spec from the legacy projection).

Authority: authoritative for ``STAGE0_PRETREATMENT`` per binding spec
§3.  This is the FIFTH (and final builtin) authoritative ledger-
mutating intent in the migration (after EVAPORATION_TRANSITION,
CONDENSATION_ROUTE, ELECTROLYSIS_STEP, METALLOTHERMIC_STEP) --
:meth:`ChemistryKernel.commit_batch` engages atom-balance validation
at dispatch time AND again at commit time.

Account declaration (exhaustive, re-grepped against
``simulator/core.py`` Stage 0 legacy paths immediately before this
write):

* ``process.stage0_volatile_feed`` -- debit; loaded from the feedstock
  volatile inventory via ``atom_ledger.load_external`` before dispatch
  in :meth:`_record_stage0_oxidation_transitions`.
* ``process.stage0_salt_feed`` -- debit; loaded from
  ``inventory.salt_phase_kg['SO3']`` slice before dispatch.
* ``process.reagent_inventory`` -- debit; C reductant drawn from
  ``reservoir.reagent.C`` before dispatch.
* ``process.stage0_perchlorate_feed`` -- debit; loaded from
  ``inventory.salt_phase_kg['ClO4']`` slice before dispatch.
* ``reservoir.stage0_oxidant`` -- debit; loaded with the computed O2
  oxidant kg before dispatch.
* ``reservoir.stage0_process_gas`` -- debit; loaded with the Boudouard
  CO2 atmospheric source kg before dispatch.
* ``terminal.offgas`` -- credit; carbonaceous CO2/H2O/N2 + sulfate
  SO2/CO + Boudouard CO go here.
* ``terminal.stage0_chloride_salt_phase`` -- credit; perchlorate Cl-
  product lands here (separated chloride salt at fouling risk).
* ``terminal.oxygen_stage0_stored`` -- credit; perchlorate O2 product
  AND the carbonaceous-degas O2 coproduct (when feed is oxygen-rich)
  land here.

The hardened kernel account-filter (since commit ``a259f80``) will
raise :class:`AccountFilterViolation` if a future refactor adds a
tenth account; the declared set is the first-line gate.  Every legacy
``_record_stage0_*_transitions`` call lands inside this set.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from engines.builtin._common import (
    build_atom_balance_proof,
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
# value is rejected as unsupported.  These match the legacy reaction-id
# vocabulary in simulator/core.py / data/feedstocks.yaml verbatim:
#   - complete_oxidation: maps to _stage0_oxidation_transition_specs
#     entries (one dispatch per species).
#   - sulfate_carbon: maps to feedstock 'sulfate_so3_to_so2_co'.
#   - boudouard: maps to feedstock 'co2_boudouard_to_co'.
#   - perchlorate: maps to feedstock 'perchlorate_to_chloride_o2'.
REACTION_FAMILY_COMPLETE_OXIDATION = "complete_oxidation"
REACTION_FAMILY_SULFATE_CARBON = "sulfate_carbon"
REACTION_FAMILY_CATION_SULFATE_CARBON = "cation_sulfate_carbon"
REACTION_FAMILY_CARBONATE_DECOMPOSITION = "carbonate_decomposition"
REACTION_FAMILY_BOUDOUARD = "boudouard"
REACTION_FAMILY_PERCHLORATE = "perchlorate"
VALID_REACTION_FAMILIES = frozenset({
    REACTION_FAMILY_COMPLETE_OXIDATION,
    REACTION_FAMILY_SULFATE_CARBON,
    REACTION_FAMILY_CATION_SULFATE_CARBON,
    REACTION_FAMILY_CARBONATE_DECOMPOSITION,
    REACTION_FAMILY_BOUDOUARD,
    REACTION_FAMILY_PERCHLORATE,
})

OXYGEN_SPECIES = "O2"
OXYGEN_STAGE0_ACCOUNT = "terminal.oxygen_stage0_stored"


class BuiltinStage0PretreatmentProvider(ChemistryProvider):
    """Authoritative ``STAGE0_PRETREATMENT`` provider.

    See module docstring.  Stateless -- per-call inputs (reaction_family,
    species, feed_kg / products_kg / oxidant_kg / debits /
    salt_products_kg / oxygen_products_kg) arrive through
    :class:`IntentRequest`; the same instance serves every Stage 0 spec
    on every batch load without holding simulator references.
    """

    name = "builtin-stage0-pretreatment"

    # The nine accounts the legacy
    # _record_stage0_oxidation_transitions /
    # _record_stage0_carbon_cleanup_transitions /
    # _record_stage0_perchlorate_cleanup_transitions paths collectively
    # touch on debit or credit side. The kernel's account-filter gate
    # will reject any proposal that names a tenth account here.
    #
    # Process feed buckets (debited; loaded from feedstock inventory via
    # atom_ledger.load_external by the caller immediately before each
    # dispatch -- the load is the legacy seeding semantics, not a
    # transition the provider needs to author):
    #   process.stage0_volatile_feed  (carbonaceous organics feed)
    #   process.stage0_salt_feed       (sulfate-bearing salt feed)
    #   process.reagent_inventory (C reductant from reservoir.reagent.C)
    #   process.stage0_perchlorate_feed (perchlorate-bearing salt feed)
    # Reservoir feed buckets (debited; same load_external seeding):
    #   reservoir.stage0_oxidant       (controlled O2 oxidant)
    #   reservoir.stage0_process_gas   (atmospheric CO2 carrier gas)
    # Terminal sinks (credited; canonical commit path supports terminal
    # credits per AtomLedger._validate_terminal_debits):
    #   terminal.offgas                (CO2/H2O/N2/SO2/CO offgas)
    #   terminal.stage0_chloride_salt_phase (perchlorate Cl- product;
    #       separated chloride salt at re-condensation/fouling risk)
    #   terminal.oxygen_stage0_stored  (perchlorate + carbonaceous O2)
    DECLARED_ACCOUNTS = frozenset({
        "process.stage0_volatile_feed",
        "process.stage0_salt_feed",
        "process.stage0_carbonate_feed",
        "process.reagent_inventory",
        "process.stage0_perchlorate_feed",
        "process.cleaned_melt",
        "reservoir.stage0_oxidant",
        "reservoir.stage0_process_gas",
        "terminal.offgas",
        "terminal.stage0_salt_phase",
        "terminal.stage0_chloride_salt_phase",
        "terminal.stage0_sulfide_matte",
        "terminal.oxygen_stage0_stored",
    })

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="builtin-stage0-pretreatment",
            intents=frozenset({ChemistryIntent.STAGE0_PRETREATMENT}),
            is_authoritative_for=frozenset(
                {ChemistryIntent.STAGE0_PRETREATMENT}
            ),
            declared_accounts=self.DECLARED_ACCOUNTS,
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        # Lazy imports: simulator.accounting.formulas pulls in
        # simulator/__init__ which re-enters this module during package
        # init -- see engines/builtin/__init__.py for the cycle
        # description.
        from simulator.accounting.formulas import resolve_species_formula

        wrong_intent = reject_wrong_intent(
            request, ChemistryIntent.STAGE0_PRETREATMENT
        )
        if wrong_intent is not None:
            return wrong_intent

        controls = unpack_controls(request)

        # Reaction-family early-exit shared with metallothermic_step.
        # Stage 0 cleanup is stoich-only -- the legacy
        # _record_stage0_oxidation_transitions / _record_stage0_carbon_*
        # / _record_stage0_perchlorate_* paths do not consult fO2 either.
        family_reject = dispatch_reaction_family(
            ChemistryIntent.STAGE0_PRETREATMENT,
            controls,
            VALID_REACTION_FAMILIES,
        )
        if family_reject is not None:
            return family_reject
        reaction_family = str(controls["reaction_family"])
        control_audit = diagnostic_control_audit(request, include_fO2=False)

        registry = request.account_view.species_formula_registry

        if reaction_family == REACTION_FAMILY_COMPLETE_OXIDATION:
            return self._dispatch_complete_oxidation(
                controls, registry, resolve_species_formula, control_audit,
            )
        if reaction_family == REACTION_FAMILY_SULFATE_CARBON:
            return self._dispatch_sulfate_carbon(
                controls, registry, resolve_species_formula, control_audit,
            )
        if reaction_family == REACTION_FAMILY_CATION_SULFATE_CARBON:
            return self._dispatch_cation_sulfate_carbon(
                controls, registry, resolve_species_formula, control_audit,
            )
        if reaction_family == REACTION_FAMILY_CARBONATE_DECOMPOSITION:
            return self._dispatch_carbonate_decomposition(
                controls, registry, resolve_species_formula, control_audit,
            )
        if reaction_family == REACTION_FAMILY_BOUDOUARD:
            return self._dispatch_boudouard(
                controls, registry, resolve_species_formula, control_audit,
            )
        # reaction_family == REACTION_FAMILY_PERCHLORATE
        return self._dispatch_perchlorate(
            controls, registry, resolve_species_formula, control_audit,
        )

    # ------------------------------------------------------------------
    # complete_oxidation: one dispatch per carbonaceous-degas feed
    # species (CH4, NH3, organic_macromolecule, ...).  Mirrors
    # _record_stage0_oxidation_transitions line-for-line per legacy
    # spec entry: debit feed (and oxidant when O2-deficient) + credit
    # terminal.offgas (CO2/H2O/N2) + credit
    # terminal.oxygen_stage0_stored (O2 coproduct when feed is
    # O2-surplus).
    # ------------------------------------------------------------------

    def _dispatch_complete_oxidation(
        self,
        controls: Mapping[str, Any],
        registry: Mapping[str, Any],
        resolve_species_formula,
        control_audit,
    ) -> IntentResult:
        species = str(controls.get("species") or "")
        feed_kg = float(controls.get("feed_kg") or 0.0)
        products_kg = dict(controls.get("products_kg") or {})
        oxidant_kg = float(controls.get("oxidant_kg") or 0.0)

        if not species:
            return self._out_of_domain(
                "complete_oxidation requires a species name",
                control_audit=control_audit,
            )
        if feed_kg <= 1e-12 and not products_kg:
            # Legacy ``_stage0_oxidation_transition_specs`` filters out
            # entries with feed_kg <= 1e-12, so an empty payload here
            # means the caller is asking the provider to handle a
            # species that has no Stage 0 oxidation profile -- a
            # mismatched lunar/Mars dispatch.
            return self._out_of_domain(
                f"complete_oxidation has no spec for species {species!r}",
                control_audit=control_audit,
            )

        # Convert kg payloads to mol via the same registry the kernel
        # uses for validation (resolve_species_formula).  Legacy uses
        # kg-native MaterialLot; the proposal layer is mol-native and
        # the kernel re-projects mol -> kg via the same registry, so
        # the worst-case parity delta is bounded by IEEE-754 round-off
        # on the same operand sequence.
        feed_formula = resolve_species_formula(species, registry)
        mol_feed = feed_kg / feed_formula.molar_mass_kg_per_mol()
        if mol_feed <= 0.0:
            return self._out_of_domain(
                f"complete_oxidation feed_kg {feed_kg!r} non-positive",
                control_audit=control_audit,
            )

        # Separate O2 from the products: the legacy
        # _record_stage0_oxidation_transitions credits O2 to
        # terminal.oxygen_stage0_stored and the remaining products
        # (CO2/H2O/N2) to terminal.offgas.
        o2_credit_kg = max(0.0, float(products_kg.pop(OXYGEN_SPECIES, 0.0)))

        debits: dict[str, dict[str, float]] = {
            "process.stage0_volatile_feed": {species: mol_feed},
        }
        if oxidant_kg > 1e-12:
            o2_formula = resolve_species_formula(OXYGEN_SPECIES, registry)
            mol_oxidant = oxidant_kg / o2_formula.molar_mass_kg_per_mol()
            if mol_oxidant > 0.0:
                debits["reservoir.stage0_oxidant"] = {
                    OXYGEN_SPECIES: mol_oxidant,
                }

        credits: dict[str, dict[str, float]] = {}
        offgas_mol: dict[str, float] = {}
        for product_species, product_kg in products_kg.items():
            kg_val = float(product_kg)
            if kg_val <= 1e-12:
                continue
            product_formula = resolve_species_formula(
                str(product_species), registry,
            )
            offgas_mol[str(product_species)] = (
                kg_val / product_formula.molar_mass_kg_per_mol()
            )
        if offgas_mol:
            credits["terminal.offgas"] = offgas_mol

        if o2_credit_kg > 1e-12:
            o2_formula = resolve_species_formula(OXYGEN_SPECIES, registry)
            mol_o2 = o2_credit_kg / o2_formula.molar_mass_kg_per_mol()
            if mol_o2 > 0.0:
                credits[OXYGEN_STAGE0_ACCOUNT] = {OXYGEN_SPECIES: mol_o2}

        if not credits:
            return self._empty_result(
                f"complete_oxidation skipped: no positive products for "
                f"{species!r}",
                control_audit=control_audit,
            )

        atom_proof = build_atom_balance_proof(
            debits, credits, registry, resolve_species_formula,
        )
        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason=f"stage0_complete_oxidation_{species}",
            atom_balance_proof=atom_proof,
        )
        return IntentResult(
            intent=ChemistryIntent.STAGE0_PRETREATMENT,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic={
                "reaction_family": REACTION_FAMILY_COMPLETE_OXIDATION,
                "species": species,
                "feed_kg": feed_kg,
                "oxidant_kg": oxidant_kg,
                "offgas_kg": dict(products_kg),  # post-O2-pop
                "oxygen_stage0_kg": o2_credit_kg,
            },
        )

    # ------------------------------------------------------------------
    # sulfate_carbon: 2 SO3 + C -> 2 SO2 + CO  (sulfate reductive
    # cleanup).  Mirrors the legacy spec built by
    # _apply_stage0_sulfate_carbon_reaction line-for-line.
    # ------------------------------------------------------------------

    def _dispatch_sulfate_carbon(
        self,
        controls: Mapping[str, Any],
        registry: Mapping[str, Any],
        resolve_species_formula,
        control_audit,
    ) -> IntentResult:
        return self._dispatch_offgas_reaction(
            controls,
            registry,
            resolve_species_formula,
            control_audit,
            family=REACTION_FAMILY_SULFATE_CARBON,
            reason="stage0_sulfate_carbon_cleanup",
        )

    def _dispatch_cation_sulfate_carbon(
        self,
        controls: Mapping[str, Any],
        registry: Mapping[str, Any],
        resolve_species_formula,
        control_audit,
    ) -> IntentResult:
        debits_payload = controls.get("debits") or ()
        products_kg = dict(controls.get("products_kg") or {})
        oxide_products_kg = dict(controls.get("oxide_products_kg") or {})
        sulfide_products_kg = dict(controls.get("sulfide_products_kg") or {})

        if not debits_payload or not products_kg:
            return self._out_of_domain(
                "cation_sulfate_carbon has no spec (empty debits or products)",
                control_audit=control_audit,
            )
        if not oxide_products_kg and not sulfide_products_kg:
            return self._out_of_domain(
                "cation_sulfate_carbon requires oxide or sulfide products",
                control_audit=control_audit,
            )

        debits_mol, debits_kg = self._kg_payload_to_mol_accounts(
            debits_payload, registry, resolve_species_formula,
        )
        if not debits_mol:
            return self._empty_result(
                "cation_sulfate_carbon skipped: all debits zero after threshold",
                control_audit=control_audit,
            )

        credits_mol: dict[str, dict[str, float]] = {}
        offgas_mol = self._kg_dict_to_mol(products_kg, registry, resolve_species_formula)
        if offgas_mol:
            credits_mol["terminal.offgas"] = offgas_mol

        melt_mol = self._kg_dict_to_mol(
            oxide_products_kg, registry, resolve_species_formula,
        )
        if melt_mol:
            credits_mol["process.cleaned_melt"] = melt_mol

        sulfide_mol = self._kg_dict_to_mol(
            sulfide_products_kg, registry, resolve_species_formula,
        )
        if sulfide_mol:
            credits_mol["terminal.stage0_sulfide_matte"] = sulfide_mol

        if not credits_mol:
            return self._empty_result(
                "cation_sulfate_carbon skipped: no positive products",
                control_audit=control_audit,
            )

        atom_proof = build_atom_balance_proof(
            debits_mol, credits_mol, registry, resolve_species_formula,
        )
        proposal = LedgerTransitionProposal(
            debits=debits_mol,
            credits=credits_mol,
            reason="stage0_cation_sulfate_carbon_cleanup",
            atom_balance_proof=atom_proof,
        )
        reagent_consumed_kg = float(
            (debits_kg.get("process.reagent_inventory") or {}).get("C", 0.0)
        )
        return IntentResult(
            intent=ChemistryIntent.STAGE0_PRETREATMENT,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic={
                "reaction_family": REACTION_FAMILY_CATION_SULFATE_CARBON,
                "debits_kg": debits_kg,
                "products_kg": dict(products_kg),
                "oxide_products_kg": dict(oxide_products_kg),
                "sulfide_products_kg": dict(sulfide_products_kg),
                "reagent_consumed_kg": reagent_consumed_kg,
            },
        )

    def _dispatch_carbonate_decomposition(
        self,
        controls: Mapping[str, Any],
        registry: Mapping[str, Any],
        resolve_species_formula,
        control_audit,
    ) -> IntentResult:
        species = str(controls.get("species") or "")
        feed_kg = float(controls.get("feed_kg") or 0.0)
        oxide_products_kg = dict(controls.get("oxide_products_kg") or {})
        offgas_products_kg = dict(controls.get("offgas_products_kg") or {})

        if not species or feed_kg <= 1e-12:
            return self._out_of_domain(
                "carbonate_decomposition requires species and positive feed_kg",
                control_audit=control_audit,
            )
        if not oxide_products_kg and not offgas_products_kg:
            return self._out_of_domain(
                "carbonate_decomposition requires oxide and/or offgas products",
                control_audit=control_audit,
            )

        feed_formula = resolve_species_formula(species, registry)
        mol_feed = feed_kg / feed_formula.molar_mass_kg_per_mol()
        if mol_feed <= 0.0:
            return self._out_of_domain(
                f"carbonate_decomposition feed_kg {feed_kg!r} non-positive",
                control_audit=control_audit,
            )

        debits = {
            "process.stage0_carbonate_feed": {species: mol_feed},
        }
        credits: dict[str, dict[str, float]] = {}
        offgas_mol = self._kg_dict_to_mol(
            offgas_products_kg, registry, resolve_species_formula,
        )
        if offgas_mol:
            credits["terminal.offgas"] = offgas_mol
        melt_mol = self._kg_dict_to_mol(
            oxide_products_kg, registry, resolve_species_formula,
        )
        if melt_mol:
            credits["process.cleaned_melt"] = melt_mol
        if not credits:
            return self._empty_result(
                "carbonate_decomposition skipped: no positive products",
                control_audit=control_audit,
            )

        atom_proof = build_atom_balance_proof(
            debits, credits, registry, resolve_species_formula,
        )
        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason=f"stage0_carbonate_decomposition_{species}",
            atom_balance_proof=atom_proof,
        )
        return IntentResult(
            intent=ChemistryIntent.STAGE0_PRETREATMENT,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic={
                "reaction_family": REACTION_FAMILY_CARBONATE_DECOMPOSITION,
                "species": species,
                "feed_kg": feed_kg,
                "oxide_products_kg": dict(oxide_products_kg),
                "offgas_products_kg": dict(offgas_products_kg),
            },
        )

    # ------------------------------------------------------------------
    # boudouard: C + CO2 -> 2 CO  (carbon-CO2 cleanup).  Mirrors the
    # legacy spec built by _apply_stage0_boudouard_reaction.
    # ------------------------------------------------------------------

    def _dispatch_boudouard(
        self,
        controls: Mapping[str, Any],
        registry: Mapping[str, Any],
        resolve_species_formula,
        control_audit,
    ) -> IntentResult:
        return self._dispatch_offgas_reaction(
            controls,
            registry,
            resolve_species_formula,
            control_audit,
            family=REACTION_FAMILY_BOUDOUARD,
            reason="stage0_boudouard_carbon_cleanup",
        )

    @staticmethod
    def _kg_dict_to_mol(
        products_kg: Mapping[str, Any],
        registry: Mapping[str, Any],
        resolve_species_formula,
    ) -> dict[str, float]:
        mol_by_species: dict[str, float] = {}
        for species, kg in products_kg.items():
            kg_val = float(kg)
            if kg_val <= 1e-12:
                continue
            formula = resolve_species_formula(str(species), registry)
            mol_by_species[str(species)] = (
                kg_val / formula.molar_mass_kg_per_mol()
            )
        return mol_by_species

    @staticmethod
    def _kg_payload_to_mol_accounts(
        debits_payload,
        registry: Mapping[str, Any],
        resolve_species_formula,
    ) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
        debits_mol: dict[str, dict[str, float]] = {}
        debits_kg: dict[str, dict[str, float]] = {}
        for entry in debits_payload:
            account, species_kg = entry
            account = str(account)
            mol_for_account: dict[str, float] = {}
            kg_for_account: dict[str, float] = {}
            for species, kg in dict(species_kg or {}).items():
                kg_val = float(kg)
                if kg_val <= 1e-12:
                    continue
                formula = resolve_species_formula(str(species), registry)
                mol_for_account[str(species)] = (
                    kg_val / formula.molar_mass_kg_per_mol()
                )
                kg_for_account[str(species)] = kg_val
            if mol_for_account:
                debits_mol[account] = mol_for_account
                debits_kg[account] = kg_for_account
        return debits_mol, debits_kg

    def _dispatch_offgas_reaction(
        self,
        controls: Mapping[str, Any],
        registry: Mapping[str, Any],
        resolve_species_formula,
        control_audit,
        *,
        family: str,
        reason: str,
    ) -> IntentResult:
        """Shared body for sulfate_carbon + boudouard.

        Both families share the legacy
        ``_record_stage0_carbon_cleanup_transitions`` shape: a list of
        (account, {species: kg}) debits + a single credit to
        ``terminal.offgas`` carrying ``products_kg`` (SO2+CO for
        sulfate_carbon; CO for boudouard).  The control payload is the
        legacy ``spec`` dict (``debits`` tuple + ``products_kg``).
        """
        debits_payload = controls.get("debits") or ()
        products_kg = dict(controls.get("products_kg") or {})

        if not debits_payload or not products_kg:
            return self._out_of_domain(
                f"{family} has no spec (empty debits or products)",
                control_audit=control_audit,
            )

        debits_mol: dict[str, dict[str, float]] = {}
        debits_kg: dict[str, dict[str, float]] = {}
        for entry in debits_payload:
            try:
                account, species_kg = entry
            except (TypeError, ValueError):
                return self._out_of_domain(
                    f"{family} debit entry malformed: {entry!r}",
                    control_audit=control_audit,
                )
            account = str(account)
            mol_for_account: dict[str, float] = {}
            kg_for_account: dict[str, float] = {}
            for species, kg in dict(species_kg or {}).items():
                kg_val = float(kg)
                if kg_val <= 1e-12:
                    continue
                formula = resolve_species_formula(str(species), registry)
                mol_for_account[str(species)] = (
                    kg_val / formula.molar_mass_kg_per_mol()
                )
                kg_for_account[str(species)] = kg_val
            if mol_for_account:
                debits_mol[account] = mol_for_account
                debits_kg[account] = kg_for_account

        if not debits_mol:
            return self._empty_result(
                f"{family} skipped: all debits zero after threshold",
                control_audit=control_audit,
            )

        credits_mol: dict[str, dict[str, float]] = {}
        offgas_mol: dict[str, float] = {}
        for species, kg in products_kg.items():
            kg_val = float(kg)
            if kg_val <= 1e-12:
                continue
            formula = resolve_species_formula(str(species), registry)
            offgas_mol[str(species)] = (
                kg_val / formula.molar_mass_kg_per_mol()
            )
        if offgas_mol:
            credits_mol["terminal.offgas"] = offgas_mol

        if not credits_mol:
            return self._empty_result(
                f"{family} skipped: no positive offgas products",
                control_audit=control_audit,
            )

        atom_proof = build_atom_balance_proof(
            debits_mol, credits_mol, registry, resolve_species_formula,
        )
        proposal = LedgerTransitionProposal(
            debits=debits_mol,
            credits=credits_mol,
            reason=reason,
            atom_balance_proof=atom_proof,
        )
        reagent_consumed_kg = float(
            (debits_kg.get("process.reagent_inventory") or {}).get("C", 0.0)
        )
        return IntentResult(
            intent=ChemistryIntent.STAGE0_PRETREATMENT,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic={
                "reaction_family": family,
                "debits_kg": debits_kg,
                "products_kg": dict(products_kg),
                "reagent_consumed_kg": reagent_consumed_kg,
            },
        )

    # ------------------------------------------------------------------
    # perchlorate: ClO4 -> Cl + 2 O2.  Mirrors the legacy spec built by
    # _apply_stage0_perchlorate_reactions: debit
    # process.stage0_perchlorate_feed (ClO4) + credit
    # terminal.stage0_chloride_salt_phase (Cl) + credit
    # terminal.oxygen_stage0_stored (O2).
    # ------------------------------------------------------------------

    def _dispatch_perchlorate(
        self,
        controls: Mapping[str, Any],
        registry: Mapping[str, Any],
        resolve_species_formula,
        control_audit,
    ) -> IntentResult:
        debits_payload = controls.get("debits") or ()
        salt_products_kg = dict(controls.get("salt_products_kg") or {})
        oxygen_products_kg = dict(controls.get("oxygen_products_kg") or {})

        if not debits_payload or (not salt_products_kg and not oxygen_products_kg):
            return self._out_of_domain(
                "perchlorate has no spec (empty debits or products)",
                control_audit=control_audit,
            )

        debits_mol: dict[str, dict[str, float]] = {}
        debits_kg: dict[str, dict[str, float]] = {}
        for entry in debits_payload:
            try:
                account, species_kg = entry
            except (TypeError, ValueError):
                return self._out_of_domain(
                    f"perchlorate debit entry malformed: {entry!r}",
                    control_audit=control_audit,
                )
            account = str(account)
            mol_for_account: dict[str, float] = {}
            kg_for_account: dict[str, float] = {}
            for species, kg in dict(species_kg or {}).items():
                kg_val = float(kg)
                if kg_val <= 1e-12:
                    continue
                formula = resolve_species_formula(str(species), registry)
                mol_for_account[str(species)] = (
                    kg_val / formula.molar_mass_kg_per_mol()
                )
                kg_for_account[str(species)] = kg_val
            if mol_for_account:
                debits_mol[account] = mol_for_account
                debits_kg[account] = kg_for_account

        if not debits_mol:
            return self._empty_result(
                "perchlorate skipped: all debits zero after threshold",
                control_audit=control_audit,
            )

        credits_mol: dict[str, dict[str, float]] = {}
        salt_mol: dict[str, float] = {}
        for species, kg in salt_products_kg.items():
            kg_val = float(kg)
            if kg_val <= 1e-12:
                continue
            formula = resolve_species_formula(str(species), registry)
            salt_mol[str(species)] = kg_val / formula.molar_mass_kg_per_mol()
        if salt_mol:
            credits_mol["terminal.stage0_chloride_salt_phase"] = salt_mol

        oxygen_mol: dict[str, float] = {}
        for species, kg in oxygen_products_kg.items():
            kg_val = float(kg)
            if kg_val <= 1e-12:
                continue
            formula = resolve_species_formula(str(species), registry)
            oxygen_mol[str(species)] = (
                kg_val / formula.molar_mass_kg_per_mol()
            )
        if oxygen_mol:
            credits_mol[OXYGEN_STAGE0_ACCOUNT] = oxygen_mol

        if not credits_mol:
            return self._empty_result(
                "perchlorate skipped: no positive products",
                control_audit=control_audit,
            )

        atom_proof = build_atom_balance_proof(
            debits_mol, credits_mol, registry, resolve_species_formula,
        )
        proposal = LedgerTransitionProposal(
            debits=debits_mol,
            credits=credits_mol,
            reason="stage0_perchlorate_cleanup",
            atom_balance_proof=atom_proof,
        )
        return IntentResult(
            intent=ChemistryIntent.STAGE0_PRETREATMENT,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic={
                "reaction_family": REACTION_FAMILY_PERCHLORATE,
                "debits_kg": debits_kg,
                "salt_products_kg": dict(salt_products_kg),
                "oxygen_products_kg": dict(oxygen_products_kg),
            },
        )

    # ------------------------------------------------------------------
    # Helpers shared with the other authoritative providers.
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_result(reason: str, *, control_audit=None) -> IntentResult:
        """Benign no-op skip (zero-mass spec from legacy projection)."""
        return IntentResult(
            intent=ChemistryIntent.STAGE0_PRETREATMENT,
            status="ok",
            transition=None,
            control_audit=control_audit,
            diagnostic={"reason_skipped": reason},
        )

    @staticmethod
    def _out_of_domain(reason: str, *, control_audit=None) -> IntentResult:
        """Feedstock has no Stage 0 profile for the requested family.

        Distinct from a benign skip: the spec is structurally absent
        (missing species, missing debits, mismatched feedstock).  The
        caller is expected to log the warning and not commit anything.
        """
        return IntentResult(
            intent=ChemistryIntent.STAGE0_PRETREATMENT,
            status="out_of_domain",
            transition=None,
            control_audit=control_audit,
            diagnostic={"reason_out_of_domain": reason},
            warnings=(reason,),
        )

    @staticmethod
    def _build_atom_balance_proof(
        debits: Mapping[str, Mapping[str, float]],
        credits: Mapping[str, Mapping[str, float]],
        registry: Mapping[str, Any],
        resolve_species_formula,
    ) -> dict[str, float]:
        """Delegate to the shared :func:`build_atom_balance_proof` helper.

        Atom balance for the Stage 0 reaction families (independent
        re-derivation per binding spec):

        * ``complete_oxidation`` (CH4 example: ``CH4 + 2 O2 ->
          CO2 + 2 H2O``) -- C: -1 + 1 = 0; H: -4 + 4 = 0;
          O: -4 + 4 = 0.  Other organics (NH3 -> N2/H2O, etc.) close
          by the same elemental accounting.
        * ``sulfate_carbon`` (``2 SO3 + C -> 2 SO2 + CO``) -- S:
          -2 + 2 = 0; C: -1 + 1 = 0; O: -7 + 7 = 0.
        * ``boudouard`` (``C + CO2 -> 2 CO``) -- C: -2 + 2 = 0;
          O: -2 + 2 = 0.
        * ``perchlorate`` (``ClO4 -> Cl + 2 O2``) -- Cl:
          -1 + 1 = 0; O: -4 + 4 = 0.

        Net per element: 0 for every reaction.
        """

        return build_atom_balance_proof(
            debits, credits, registry, resolve_species_formula
        )
