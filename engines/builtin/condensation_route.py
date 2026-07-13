"""Builtin CONDENSATION_ROUTE provider (overhead vapor -> condensation train).

Kernel-registered provider that owns the ``CONDENSATION_ROUTE`` intent
per binding spec §2 ("Stage-by-stage deposition of vapor species") and
§3 (Builtin authoritative). Mirrors the deposit-routing math in
:meth:`simulator.condensation.CondensationModel.route` exactly -- this
is a refactor of where the LedgerTransitionProposal is built, not a
re-derivation of the stage-by-stage condensation efficiency model
(which still runs in :class:`CondensationModel` at the caller; the
provider receives the per-species condensed-mass projection via
``request.control_inputs``).

The provider:

- reads ``process.overhead_gas``,
  ``process.condensation_retained_holdup``,
  ``process.condensation_train``, ``process.wall_deposit``, and
  declared product bins from the account view -- the accounts the
  deposition leg touches (debit vapor from overhead, credit deposits).
  ``process.cleaned_melt`` is NOT in the declared set: the
  EVAPORATION_TRANSITION provider already owns the melt -> overhead
  leg in the same tick; this provider works on the overhead vapor
  AFTER that transition has been committed.
- reads T from ``request.temperature_C``,
- reads the per-species condensed mass + sp_data via
  ``request.control_inputs``:

  * ``species`` -- the vapor species name (e.g. ``"Na"``, ``"SiO"``),
  * ``condensed_kg`` -- the kg of vapor projected to deposit this tick
    (caller computes ``rate_kg_hr - remaining_kg_hr``, then applies the
    same ``available_kg / oxide_removed`` scale the EVAPORATION_TRANSITION
    caller applies, so the two transitions stay numerically consistent
    on parent-oxide-limited ticks),
  * ``sp_data`` -- the raw ``vapor_pressures.yaml`` metadata for the
    species (used only to look up
    ``condensation_products_mol_per_mol_vapor`` for the disproportionation
    branch; the provider re-uses the same product map the legacy path builds in
    :meth:`_condensed_products_for_vapor` -- the canonical SiO -> Si +
    SiO2 split lives here),
  * ``wall_deposit_fraction`` and ``wall_deposit_account_fractions`` -- the
    same-tick wall split carried from ``CondensationRouteResult``,
  * ``dt_hr`` -- the tick duration in hours (always 1.0 in the current
    simulator; passed through explicitly so the provider stays unit-
    correct if the simulator's tick step ever changes).

Returns an :class:`IntentResult` with ``transition`` populated by a
:class:`LedgerTransitionProposal` (per-species debit/credit pair) and a
``credited_condensed_kg`` diagnostic for the baffle/product mass so the
caller can drive ``_project_condensed_stage_collection`` after the kernel
commits. ``credited_wall_deposit_kg`` carries the fouling mass credited to
``process.wall_deposit`` in the same mol-native proposal.

Authority: authoritative for ``CONDENSATION_ROUTE`` per binding spec
§3. This is the SECOND authoritative ledger-mutating intent in the
migration (after EVAPORATION_TRANSITION) -- ``ChemistryKernel.commit_batch``
engages atom-balance validation at dispatch time AND again at commit
time.

Account declaration: ``process.overhead_gas``,
``process.condensation_retained_holdup``, ``process.condensation_train``,
and ``process.wall_deposit``. The
deposition leg is strictly an overhead -> destination transfer; declaring
``process.cleaned_melt`` here would be an account-scope leak (the melt is
the EVAPORATION_TRANSITION provider's responsibility, not ours). The
provider must declare every account the proposal touches
(``validate_proposal_accounts`` enforces this with
:class:`AccountFilterViolation`).
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Iterable, Mapping
from typing import Any

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
from simulator.account_ids import CONDENSATION_RETAINED_HOLDUP_ACCOUNT
from simulator.condensation import (
    C4B_WALL_ROUTE_ORDER,
    WALL_DEPOSIT_ACCOUNT,
    WALL_REACTIVITY_MATRIX,
    WALL_REACTIVITY_MATRIX_PATH,
)
from simulator.state import (
    declared_wall_deposit_accounts,
)


class BuiltinCondensationRouteProvider(ChemistryProvider):
    """Authoritative ``CONDENSATION_ROUTE`` provider.

    See module docstring. Per-call inputs arrive through
    :class:`IntentRequest.control_inputs`; account authority is scoped to
    the configured wall-deposit accounts passed to this provider instance.
    """

    name = "builtin-condensation-route"
    CHROMIUM_CONDENSED_ACCOUNT = "terminal.chromium_condensed_oxide_stored"
    GASEOUS_CONDENSATION_COPRODUCTS = frozenset({"O2"})
    RETAINED_HOLDUP_ACCOUNT = CONDENSATION_RETAINED_HOLDUP_ACCOUNT

    BASE_DECLARED_ACCOUNTS = frozenset({
        "process.overhead_gas",
        "process.condensation_train",
        RETAINED_HOLDUP_ACCOUNT,
        WALL_DEPOSIT_ACCOUNT,
        CHROMIUM_CONDENSED_ACCOUNT,
    })

    def __init__(self, wall_deposit_accounts: Iterable[str] = ()):
        self._declared_wall_deposit_accounts = frozenset(
            declared_wall_deposit_accounts(wall_deposit_accounts)
        )

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="builtin-condensation-route",
            intents=frozenset({ChemistryIntent.CONDENSATION_ROUTE}),
            is_authoritative_for=frozenset(
                {ChemistryIntent.CONDENSATION_ROUTE}
            ),
            declared_accounts=self._declared_accounts(),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        # Lazy import: simulator.accounting.formulas pulls in
        # simulator/__init__ which re-enters this module during package
        # init -- see engines/builtin/__init__.py for the cycle
        # description.
        from simulator.accounting.formulas import resolve_species_formula
        from simulator.accounting.lots import EMPTY_KG_TOLERANCE

        wrong_intent = reject_wrong_intent(
            request, ChemistryIntent.CONDENSATION_ROUTE
        )
        if wrong_intent is not None:
            return wrong_intent

        # Condensation routing is pure mol bookkeeping; the engine has no
        # independent T/P/fO2 control loop. Applied == requested verbatim
        # with the diagnostic-only note.
        control_audit = diagnostic_control_audit(request)

        controls = unpack_controls(request)
        species = str(controls.get("species") or "")
        if not species:
            return IntentResult(
                intent=ChemistryIntent.CONDENSATION_ROUTE,
                status="unsupported",
                control_audit=control_audit,
                diagnostic={"reason": "missing 'species' control input"},
            )

        current_condensed_kg = float(controls.get("condensed_kg") or 0.0)
        sp_data = dict(controls.get("sp_data") or {})
        dt_hr = float(controls.get("dt_hr", 1.0))  # noqa: F841 -- kept for unit symmetry
        registry = request.account_view.species_formula_registry
        vapor_formula = resolve_species_formula(species, registry)
        vapor_molar_mass = vapor_formula.molar_mass_kg_per_mol()
        prior_holdup_mol = float(
            request.account_view.accounts.get(
                self.RETAINED_HOLDUP_ACCOUNT, {}
            ).get(species, 0.0)
        )
        retained_holdup_kg = prior_holdup_mol * vapor_molar_mass

        if current_condensed_kg <= 1e-12:
            return IntentResult(
                intent=ChemistryIntent.CONDENSATION_ROUTE,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "credited_condensed_kg": 0.0,
                    "reason_skipped": "below numerical floor",
                    "retained_holdup_account": "process.overhead_gas",
                    "retained_holdup_kg": float(current_condensed_kg),
                    "retained_holdup_lifecycle": (
                        "nonretryable_overhead_holdup_pending_typed_bleed"
                    ),
                    "typed_retry_holdup_kg": float(retained_holdup_kg),
                },
            )

        invalid_route = self._invalid_route_input_refusal(
            species,
            sp_data,
            controls,
            control_audit,
        )
        if invalid_route is not None:
            return invalid_route

        condensed_kg = current_condensed_kg
        declared_accounts = self._declared_accounts()
        refused_account = self._undeclared_wall_deposit_account(
            controls, declared_accounts)
        if refused_account is not None:
            return IntentResult(
                intent=ChemistryIntent.CONDENSATION_ROUTE,
                status="refused",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "reason": "undeclared_wall_deposit_account",
                    "reason_refused": "undeclared_wall_deposit_account",
                    "account": refused_account,
                },
            )
        wall_account_fractions = self._wall_deposit_account_fractions(
            controls, declared_accounts)

        # Build the per-product mol map. Disproportionation branch
        # (SiO -> Si + SiO2) is the canonical case; non-disproportionation
        # species deposit as themselves. Mirrors
        # ``_condensation_product_mol_ratios`` / ``_condensed_products_for_vapor``
        # in simulator/evaporation.py exactly -- this is a refactor of
        # where the math lives, not a re-derivation.
        wall_fraction = self._wall_deposit_fraction(controls)
        wall_deposit_kg = condensed_kg * wall_fraction
        baffle_condensed_kg = max(0.0, condensed_kg - wall_deposit_kg)
        condensed_product_mol = self._condensed_product_mol(
            species, baffle_condensed_kg, sp_data, registry,
            resolve_species_formula,
        )
        wall_deposit_mol = self._wall_deposit_mol(
            species, wall_deposit_kg, registry, resolve_species_formula,
        )
        wall_deposit_mol_by_account = self._wall_deposit_mol_by_account(
            wall_deposit_mol,
            wall_account_fractions,
        )

        alkali_refusal = self._alkali_forbidden_control_refusal(
            species,
            controls,
            control_audit,
        )
        if alkali_refusal is not None:
            return alkali_refusal

        wall_plan = self._wall_reaction_plan(
            species,
            wall_deposit_mol_by_account,
            request.account_view.accounts,
            controls,
        )

        # MaterialLot applies its floor independently to every account/species
        # component on both sides.  Roll back only wall parcels whose coupled
        # reaction cannot materialize exactly; the arriving vapor then lands
        # unchanged on that same wall account and the substrate stays put.
        # This preserves the exact invariant, per species/form and therefore
        # per element: sum(debits) == sum(credits).  Removing a coupled
        # reaction removes all of its substrate debits and product credits,
        # while replacing the parcel's vapor debit with an equal-mol credit of
        # the unchanged vapor species.
        wall_plan, retained_wall_mol_by_account = (
            self._materialization_safe_wall_plan(
                species,
                wall_deposit_mol_by_account,
                wall_plan,
                self._copy_alkali_state(
                    controls.get(
                        "wall_alkali_binding_diagnostic_state_by_account"
                    )
                ),
                registry,
                resolve_species_formula,
                EMPTY_KG_TOLERANCE,
            )
        )

        credits = self._credits_by_product_account(condensed_product_mol, sp_data)
        # Prior retained vapor retries only through stable baffle chemistry;
        # newly arriving vapor keeps this tick's physical wall split. Merging
        # identical product/account components lets prior+current cross the
        # MaterialLot floor without rerouting current wall deposition.
        prior_product_mol = self._condensed_product_mol(
            species,
            retained_holdup_kg,
            sp_data,
            registry,
            resolve_species_formula,
        )
        prior_credits = self._credits_by_product_account(
            prior_product_mol, sp_data
        )
        prior_credits_materializable = bool(prior_credits) and all(
            float(mol)
            * resolve_species_formula(
                product_species, registry
            ).molar_mass_kg_per_mol()
            > EMPTY_KG_TOLERANCE
            for product_mol in prior_credits.values()
            for product_species, mol in product_mol.items()
        )
        for account, product_mol in prior_credits.items():
            account_credits = credits.setdefault(account, {})
            for product_species, mol in product_mol.items():
                account_credits[product_species] = (
                    account_credits.get(product_species, 0.0) + float(mol)
                )
        (
            subfloor_baffle_credits_kg,
            folded_baffle_kg_by_wall_account,
        ) = self._fold_subfloor_baffle_credits(
            credits,
            wall_plan["product_mol_by_account"],
            registry,
            resolve_species_formula,
            EMPTY_KG_TOLERANCE,
        )
        retained_baffle_kg = 0.0
        if subfloor_baffle_credits_kg:
            # A remaining sub-floor component has no active destination with
            # identical species/form.  Keep the whole coupled baffle parcel in
            # overhead (subtract it from the proposed vapor debit) rather than
            # alter chemistry or discard only one product.  Other wall parcels
            # remain computable and commit normally.
            credits = {}
            retained_baffle_kg = baffle_condensed_kg
            baffle_condensed_kg = 0.0

        retained_wall_kg_by_account = {
            account: mol
            * resolve_species_formula(
                species, registry
            ).molar_mass_kg_per_mol()
            for account, mol in retained_wall_mol_by_account.items()
        }
        retained_wall_kg = sum(retained_wall_kg_by_account.values())
        credited_wall_deposit_kg = max(0.0, wall_deposit_kg - retained_wall_kg)

        retry_failed = bool(subfloor_baffle_credits_kg) or retained_wall_kg > 0.0
        if retry_failed:
            # Retained-holdup derivation (t-054 pO2-hold -> pN2-sweep
            # analogue): D_overhead(current) + optional D_holdup(prior)
            # equals C_holdup(current) + prior baffle-route credits exactly,
            # atom-for-atom. Until prior baffle products materialize, prior is
            # untouched and current joins it. Once they materialize, prior
            # drains even if current wall chemistry still fails; current alone
            # replaces the hold. Thus new vapor keeps current wall physics and
            # stable, validated positive baffle product ratios bound the hold
            # by floor/min(product mass fraction) plus one current parcel. A
            # finite value with no later arrival is reported terminal holdup.
            current_mol = current_condensed_kg / vapor_molar_mass
            retain_debits = {
                "process.overhead_gas": {species: current_mol},
            }
            retain_credits = {
                self.RETAINED_HOLDUP_ACCOUNT: {species: current_mol},
            }
            drained_prior_kg = 0.0
            if prior_holdup_mol > 0.0 and prior_credits_materializable:
                retain_debits[self.RETAINED_HOLDUP_ACCOUNT] = {
                    species: prior_holdup_mol
                }
                for account, product_mol in prior_credits.items():
                    account_credits = retain_credits.setdefault(account, {})
                    for product_species, mol in product_mol.items():
                        account_credits[product_species] = (
                            account_credits.get(product_species, 0.0)
                            + float(mol)
                        )
                drained_prior_kg = retained_holdup_kg
            retain_proposal = LedgerTransitionProposal(
                debits=retain_debits,
                credits=retain_credits,
                reason=f"retain_condensation_holdup_{species}",
                atom_balance_proof=build_atom_balance_proof(
                    retain_debits,
                    retain_credits,
                    registry,
                    resolve_species_formula,
                ),
            )
            return IntentResult(
                intent=ChemistryIntent.CONDENSATION_ROUTE,
                status="ok",
                transition=retain_proposal,
                control_audit=control_audit,
                diagnostic={
                    "credited_condensed_kg": float(drained_prior_kg),
                    "credited_wall_deposit_kg": 0.0,
                    "retained_holdup_account": self.RETAINED_HOLDUP_ACCOUNT,
                    "retained_holdup_kg": float(
                        current_condensed_kg
                        + (0.0 if drained_prior_kg else retained_holdup_kg)
                    ),
                    "retained_holdup_added_kg": float(current_condensed_kg),
                    "retained_holdup_drained_kg": float(drained_prior_kg),
                    "retained_holdup_retry_route": "stable_baffle_chemistry",
                    "reason_skipped": "coupled product below numerical floor",
                    "wall_alkali_binding_diagnostic_state_by_account": (
                        self._copy_alkali_state(
                            controls.get(
                                "wall_alkali_binding_diagnostic_state_by_account"
                            )
                        )
                    ),
                },
            )

        if not credits and not wall_plan["product_mol_by_account"]:
            return IntentResult(
                intent=ChemistryIntent.CONDENSATION_ROUTE,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "credited_condensed_kg": 0.0,
                    "reason_skipped": "empty condensation product credits",
                },
            )

        baffle_condensed_kg += retained_holdup_kg

        # ------------------------------------------------------------------
        # Build the mol-native proposal. Per-account species_mol dicts:
        #   debits:  process.overhead_gas         -> {species: mol}
        #   credits: process.condensation_train   -> {product: mol, ...}
        #            or product-specific accounts declared in sp_data
        # ------------------------------------------------------------------
        debits: dict[str, dict[str, float]] = {
            "process.overhead_gas": {
                species: current_condensed_kg / vapor_molar_mass
            },
        }
        if prior_holdup_mol > 0.0:
            debits[self.RETAINED_HOLDUP_ACCOUNT] = {
                species: prior_holdup_mol
            }
        for account, species_mol in wall_plan[
            "substrate_debit_mol_by_account"
        ].items():
            account_debits = debits.setdefault(account, {})
            for debit_species, mol in species_mol.items():
                account_debits[debit_species] = (
                    account_debits.get(debit_species, 0.0) + mol
                )
        for account, product_mol in wall_plan["product_mol_by_account"].items():
            species_mol = credits.setdefault(account, {})
            for product_species, mol in product_mol.items():
                species_mol[product_species] = (
                    species_mol.get(product_species, 0.0) + mol
                )

        # Atom-balance proof: element-by-element net (credit - debit).
        # Must be zero element-by-element (the kernel re-checks this
        # at commit time -- this is the provider's own bookkeeping
        # surface, matched against the kernel's authoritative count).
        # For SiO disproportionation: -1 SiO (1 Si, 1 O) + 0.5 Si + 0.5
        # SiO2 (0.5 Si, 1 O) -> net zero per element.
        atom_proof: dict[str, float] = build_atom_balance_proof(
            debits, credits, registry, resolve_species_formula,
        )

        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason=f"condense_{species}",
            atom_balance_proof=atom_proof,
        )

        product_kg_by_account = self._species_kg_by_account(
            wall_plan["product_mol_by_account"],
            registry,
            resolve_species_formula,
        )
        substrate_kg_by_account = self._species_kg_by_account(
            wall_plan["substrate_debit_mol_by_account"],
            registry,
            resolve_species_formula,
        )
        wall_delta_kg_by_account = self._wall_delta_kg_by_account(
            product_kg_by_account,
            substrate_kg_by_account,
        )

        return IntentResult(
            intent=ChemistryIntent.CONDENSATION_ROUTE,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic={
                "credited_condensed_kg": float(baffle_condensed_kg),
                "credited_wall_deposit_kg": float(credited_wall_deposit_kg),
                "numerical_floor_baffle_to_wall_kg": float(
                    sum(folded_baffle_kg_by_wall_account.values())
                ),
                "numerical_floor_retained_overhead_kg": float(
                    retained_baffle_kg + retained_wall_kg
                ),
                "retained_holdup_account": self.RETAINED_HOLDUP_ACCOUNT,
                "retained_holdup_kg": float(
                    retained_baffle_kg + retained_wall_kg
                ),
                "retained_holdup_drained_kg": float(retained_holdup_kg),
                "retained_holdup_retry_route": "stable_baffle_chemistry",
                "numerical_floor_retained_baffle_kg": float(retained_baffle_kg),
                "numerical_floor_retained_wall_kg_by_account": (
                    retained_wall_kg_by_account
                ),
                "subfloor_baffle_credits_kg": subfloor_baffle_credits_kg,
                "credited_wall_deposit_accounts_kg": {
                    account: max(
                        0.0,
                        float(wall_deposit_mol_by_account.get(account, 0.0))
                        * vapor_formula.molar_mass_kg_per_mol()
                        - retained_wall_kg_by_account.get(account, 0.0),
                    )
                    for account in wall_deposit_mol_by_account
                },
                "wall_deposit_accounts_kg_by_species": product_kg_by_account,
                "wall_substrate_debit_accounts_kg_by_species": (
                    substrate_kg_by_account
                ),
                "wall_deposit_accounts_kg_delta_by_species": (
                    wall_delta_kg_by_account
                ),
                "wall_reaction_products_by_account_species_mol": (
                    wall_plan["product_mol_by_account"]
                ),
                "wall_reaction_substrate_debits_by_account_species_mol": (
                    wall_plan["substrate_debit_mol_by_account"]
                ),
                "wall_reaction_diagnostics_by_account": (
                    wall_plan["diagnostics_by_account"]
                ),
                "wall_alkali_binding_diagnostic_state_by_account": (
                    wall_plan["alkali_state_by_account"]
                ),
            },
        )

    # ------------------------------------------------------------------
    # Helpers (mirror legacy _condensed_products_for_vapor /
    # _condensation_product_mol_ratios exactly).
    # ------------------------------------------------------------------

    def _invalid_route_input_refusal(
        self,
        species: str,
        sp_data: Mapping[str, Any],
        controls: Mapping[str, Any],
        control_audit: Any,
    ) -> IntentResult | None:
        ratios = sp_data.get("condensation_products_mol_per_mol_vapor")
        if ratios is not None:
            if not isinstance(ratios, Mapping) or not ratios:
                return self._route_refusal(
                    "invalid_condensation_product_ratios",
                    control_audit,
                    field="condensation_products_mol_per_mol_vapor",
                )
            for product, raw_ratio in ratios.items():
                try:
                    ratio = float(raw_ratio)
                except (TypeError, ValueError):
                    ratio = math.nan
                if not str(product) or not math.isfinite(ratio) or ratio <= 0.0:
                    return self._route_refusal(
                        "invalid_condensation_product_ratios",
                        control_audit,
                        field="condensation_products_mol_per_mol_vapor",
                        product=str(product),
                    )

        raw_product_accounts = sp_data.get("condensation_product_accounts")
        if raw_product_accounts is not None:
            if not isinstance(raw_product_accounts, Mapping):
                return self._route_refusal(
                    "invalid_condensation_product_accounts",
                    control_audit,
                    field="condensation_product_accounts",
                )
            for product, raw_account in raw_product_accounts.items():
                product_name = str(product)
                account = str(raw_account or "")
                if account not in self._declared_accounts():
                    return self._route_refusal(
                        "undeclared_condensation_product_account",
                        control_audit,
                        field="condensation_product_accounts",
                        product=product_name,
                        account=account,
                    )
                if (
                    account == "process.overhead_gas"
                    and (
                        product_name == species
                        or product_name not in self.GASEOUS_CONDENSATION_COPRODUCTS
                    )
                ):
                    return self._route_refusal(
                        "invalid_gaseous_condensation_coproduct",
                        control_audit,
                        field="condensation_product_accounts",
                        product=product_name,
                        account=account,
                    )

        raw_wall_fraction = controls.get("wall_deposit_fraction", 0.0)
        try:
            wall_fraction = float(raw_wall_fraction)
        except (TypeError, ValueError):
            wall_fraction = math.nan
        if not math.isfinite(wall_fraction) or not 0.0 <= wall_fraction <= 1.0:
            return self._route_refusal(
                "invalid_wall_deposit_fraction",
                control_audit,
                field="wall_deposit_fraction",
            )

        raw_account_fractions = controls.get("wall_deposit_account_fractions")
        if raw_account_fractions is None or raw_account_fractions == {}:
            if wall_fraction > 0.0:
                return self._route_refusal(
                    "invalid_wall_deposit_account_fractions",
                    control_audit,
                    field="wall_deposit_account_fractions",
                )
            return None
        if not isinstance(raw_account_fractions, Mapping):
            return self._route_refusal(
                "invalid_wall_deposit_account_fractions",
                control_audit,
                field="wall_deposit_account_fractions",
            )
        allowed_wall_accounts = frozenset({WALL_DEPOSIT_ACCOUNT}) | (
            self._declared_wall_deposit_accounts
        )
        total = 0.0
        for account, raw_fraction in raw_account_fractions.items():
            account_name = str(account)
            try:
                fraction = float(raw_fraction)
            except (TypeError, ValueError):
                fraction = math.nan
            if (
                account_name not in allowed_wall_accounts
                or not math.isfinite(fraction)
                or fraction <= 0.0
            ):
                return self._route_refusal(
                    "invalid_wall_deposit_account_fractions",
                    control_audit,
                    field="wall_deposit_account_fractions",
                    account=account_name,
                )
            total += fraction
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1.0e-9):
            return self._route_refusal(
                "invalid_wall_deposit_account_fractions",
                control_audit,
                field="wall_deposit_account_fractions",
            )
        return None

    @staticmethod
    def _route_refusal(
        reason: str,
        control_audit: Any,
        **diagnostic: Any,
    ) -> IntentResult:
        return IntentResult(
            intent=ChemistryIntent.CONDENSATION_ROUTE,
            status="refused",
            transition=None,
            control_audit=control_audit,
            diagnostic={
                "reason": reason,
                "reason_refused": reason,
                **diagnostic,
            },
        )

    def _wall_reaction_plan(
        self,
        species: str,
        wall_deposit_mol_by_account: Mapping[str, float],
        account_view: Mapping[str, Mapping[str, float]],
        controls: Mapping[str, Any],
    ) -> dict[str, Any]:
        product_mol_by_account: dict[str, dict[str, float]] = {}
        substrate_debit_mol_by_account: dict[str, dict[str, float]] = {}
        diagnostics_by_account: dict[str, dict[str, Any]] = {}
        alkali_state_by_account = self._copy_alkali_state(
            controls.get("wall_alkali_binding_diagnostic_state_by_account")
        )

        for account, arrival_mol in wall_deposit_mol_by_account.items():
            if arrival_mol <= 0.0:
                continue
            account_state = dict(account_view.get(account, {}) or {})
            products: dict[str, float] = {}
            debits: dict[str, float] = {}
            diagnostic: dict[str, Any] = {
                "route_order": C4B_WALL_ROUTE_ORDER,
                "source": str(WALL_REACTIVITY_MATRIX_PATH),
            }

            if species == "SiO":
                reaction = self._matrix_reaction("SiO_disproportionation")
                for product, ratio in dict(reaction["product_credits"]).items():
                    products[str(product)] = arrival_mol * float(ratio)
                diagnostic.update({
                    "mechanism": reaction.get("mechanism"),
                    "status": reaction.get("status"),
                })
            elif species == "Mg":
                reaction = self._matrix_reaction("Mg_silica_reduction")
                available_sio2_mol = max(
                    0.0, float(account_state.get("SiO2", 0.0))
                )
                reactive_mg_mol = min(arrival_mol, 2.0 * available_sio2_mol)
                residual_mg_mol = max(0.0, arrival_mol - reactive_mg_mol)
                if reactive_mg_mol > 0.0:
                    debits["SiO2"] = 0.5 * reactive_mg_mol
                    products["MgO"] = reactive_mg_mol
                    products["Si"] = 0.5 * reactive_mg_mol
                if residual_mg_mol > 0.0:
                    products["Mg"] = residual_mg_mol
                diagnostic.update({
                    "mechanism": reaction.get("mechanism"),
                    "status": reaction.get("status"),
                    "available_sio2_mol": available_sio2_mol,
                    "reactive_mol": reactive_mg_mol,
                    "residual_physisorbing_mol": residual_mg_mol,
                    "gaps": (
                        "Mg_passivation",
                        "MgO_vs_Mg2Si_competition",
                    ),
                })
            elif species == "Fe":
                reaction = self._matrix_reaction("Fe_silicide")
                available_si_mol = max(0.0, float(account_state.get("Si", 0.0)))
                silicide_mol = min(arrival_mol, available_si_mol)
                residual_fe_mol = max(0.0, arrival_mol - silicide_mol)
                if silicide_mol > 0.0:
                    debits["Si"] = silicide_mol
                    products["FeSi"] = silicide_mol
                if residual_fe_mol > 0.0:
                    products["Fe"] = residual_fe_mol
                diagnostic.update({
                    "mechanism": reaction.get("mechanism"),
                    "status": reaction.get("status"),
                    "available_si_mol": available_si_mol,
                    "reactive_mol": silicide_mol,
                    "residual_physisorbing_mol": residual_fe_mol,
                    "deferred_products": tuple(
                        reaction.get("deferred_products") or ()
                    ),
                })
            elif species in {"Na", "K"}:
                products[species] = arrival_mol
                diagnostic, alkali_state_by_account[account] = (
                    self._alkali_diagnostic_update(
                        species,
                        account,
                        arrival_mol,
                        account_state,
                        alkali_state_by_account.get(account),
                        self._wall_temperature_K(controls, account),
                    )
                )
            else:
                products[species] = arrival_mol
                diagnostic.update({
                    "mechanism": "physisorbing_non_reactive_c4b",
                    "status": "NON_REACTIVE_C4B",
                })

            if products:
                product_mol_by_account[account] = products
            if debits:
                substrate_debit_mol_by_account[account] = debits
            diagnostics_by_account[account] = diagnostic

        return {
            "product_mol_by_account": product_mol_by_account,
            "substrate_debit_mol_by_account": substrate_debit_mol_by_account,
            "diagnostics_by_account": diagnostics_by_account,
            "alkali_state_by_account": alkali_state_by_account,
        }

    @staticmethod
    def _materialization_safe_wall_plan(
        species: str,
        arrival_mol_by_account: Mapping[str, float],
        wall_plan: Mapping[str, Any],
        prior_alkali_state_by_account: Mapping[str, Any],
        registry: Mapping[str, Any],
        resolve_species_formula,
        floor_kg: float,
    ) -> tuple[dict[str, Any], dict[str, float]]:
        products_by_account = {
            account: dict(species_mol)
            for account, species_mol in dict(
                wall_plan["product_mol_by_account"]
            ).items()
        }
        debits_by_account = {
            account: dict(species_mol)
            for account, species_mol in dict(
                wall_plan["substrate_debit_mol_by_account"]
            ).items()
        }
        diagnostics_by_account = {
            account: dict(diagnostic)
            for account, diagnostic in dict(
                wall_plan["diagnostics_by_account"]
            ).items()
        }
        retained_by_account: dict[str, float] = {}
        alkali_state_by_account = dict(wall_plan["alkali_state_by_account"])

        def has_subfloor_component(side: Mapping[str, float]) -> bool:
            return any(
                0.0
                < float(mol)
                * resolve_species_formula(
                    component_species, registry
                ).molar_mass_kg_per_mol()
                <= floor_kg
                for component_species, mol in side.items()
            )

        vapor_molar_mass = resolve_species_formula(
            species, registry
        ).molar_mass_kg_per_mol()
        for account, arrival_mol in arrival_mol_by_account.items():
            products = products_by_account.get(account, {})
            debits = debits_by_account.get(account, {})
            if not (
                has_subfloor_component(products)
                or has_subfloor_component(debits)
            ):
                continue

            diagnostics_by_account.setdefault(account, {}).update({
                "materialization_adjustment": (
                    "rollback_coupled_reaction_to_unchanged_arrival"
                ),
                "pre_adjustment_products_mol": dict(products),
                "pre_adjustment_substrate_debits_mol": dict(debits),
            })
            debits_by_account.pop(account, None)
            if account in prior_alkali_state_by_account:
                alkali_state_by_account[account] = prior_alkali_state_by_account[
                    account
                ]
            else:
                alkali_state_by_account.pop(account, None)
            if float(arrival_mol) * vapor_molar_mass > floor_kg:
                products_by_account[account] = {species: float(arrival_mol)}
            else:
                products_by_account.pop(account, None)
                retained_by_account[account] = float(arrival_mol)
                diagnostics_by_account[account]["materialization_adjustment"] = (
                    "retain_unmaterializable_arrival_in_overhead"
                )

        return ({
            "product_mol_by_account": products_by_account,
            "substrate_debit_mol_by_account": debits_by_account,
            "diagnostics_by_account": diagnostics_by_account,
            "alkali_state_by_account": alkali_state_by_account,
        }, retained_by_account)

    @staticmethod
    def _fold_subfloor_baffle_credits(
        baffle_credits: dict[str, dict[str, float]],
        wall_products_by_account: dict[str, dict[str, float]],
        registry: Mapping[str, Any],
        resolve_species_formula,
        floor_kg: float,
    ) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
        """Fold only identical species into an active materializable wall lot."""
        unresolved: dict[str, dict[str, float]] = {}
        folds: list[tuple[str, str, str, float, float]] = []
        for source_account, species_mol in baffle_credits.items():
            for product_species, mol in species_mol.items():
                molar_mass = resolve_species_formula(
                    product_species, registry
                ).molar_mass_kg_per_mol()
                product_kg = float(mol) * molar_mass
                if not (0.0 < product_kg <= floor_kg):
                    continue
                destination = next((
                    account
                    for account in sorted(wall_products_by_account)
                    if product_species in wall_products_by_account[account]
                    and float(
                        wall_products_by_account[account][product_species]
                    ) * molar_mass > floor_kg
                ), None)
                if destination is None:
                    unresolved.setdefault(source_account, {})[
                        product_species
                    ] = product_kg
                    continue
                folds.append((
                    source_account,
                    product_species,
                    destination,
                    float(mol),
                    product_kg,
                ))

        # A coupled baffle parcel is residualized as a whole when any one of
        # its products lacks a same-species destination.  Apply planned folds
        # only after proving all sub-floor products have destinations, so a
        # partial mutation cannot survive that rollback.
        if unresolved:
            return unresolved, {}

        folded_kg_by_account: dict[str, float] = {}
        for (
            source_account,
            product_species,
            destination,
            mol,
            product_kg,
        ) in folds:
            wall_products_by_account[destination][product_species] += mol
            del baffle_credits[source_account][product_species]
            if not baffle_credits[source_account]:
                del baffle_credits[source_account]
            folded_kg_by_account[destination] = (
                folded_kg_by_account.get(destination, 0.0) + product_kg
            )
        return unresolved, folded_kg_by_account

    @staticmethod
    def _matrix_reaction(name: str) -> Mapping[str, Any]:
        reactions = WALL_REACTIVITY_MATRIX.get("reactions")
        if not isinstance(reactions, Mapping) or name not in reactions:
            raise ValueError(f"wall reactivity matrix missing reaction {name!r}")
        entry = reactions[name]
        if not isinstance(entry, Mapping) or not entry.get("status"):
            raise ValueError(f"wall reactivity matrix reaction {name!r} lacks status")
        if not entry.get("source_refs"):
            raise ValueError(f"wall reactivity matrix reaction {name!r} lacks sources")
        return entry

    @staticmethod
    def _alkali_entry(species: str) -> Mapping[str, Any]:
        alkali = WALL_REACTIVITY_MATRIX.get("alkali_activity_depression")
        if not isinstance(alkali, Mapping) or species not in alkali:
            raise ValueError(
                f"wall reactivity matrix missing alkali entry for {species!r}"
            )
        entry = alkali[species]
        if not isinstance(entry, Mapping) or not entry.get("status"):
            raise ValueError(f"wall reactivity alkali entry {species!r} lacks status")
        saturation = entry.get("saturation")
        if not isinstance(saturation, Mapping):
            raise ValueError(
                f"wall reactivity alkali entry {species!r} lacks saturation"
            )
        anchor = saturation.get("primary_anchor")
        if not isinstance(anchor, Mapping) or not anchor.get("citation"):
            raise ValueError(
                f"wall reactivity alkali entry {species!r} lacks cited source"
            )
        return entry

    @classmethod
    def _alkali_diagnostic_update(
        cls,
        species: str,
        account: str,
        arrival_mol: float,
        account_state: Mapping[str, float],
        prior_state: Mapping[str, Any] | None,
        wall_temperature_K: float | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        entry = cls._alkali_entry(species)
        saturation = entry["saturation"]
        ratio, ratio_context = cls._alkali_saturation_ratio(
            species,
            saturation,
            wall_temperature_K,
        )
        equivalent = str(entry["diagnostic_equivalent"])
        prior = dict(prior_state or {})
        prior_bound = dict(prior.get("bound_alkali_equiv_mol") or {})
        previous_equiv_mol = max(0.0, float(prior_bound.get(equivalent, 0.0)))
        sio2_basis_mol = max(0.0, float(account_state.get("SiO2", 0.0)))
        capacity_equiv_mol = max(
            0.0, ratio * sio2_basis_mol - previous_equiv_mol
        )
        arriving_equiv_mol = 0.5 * arrival_mol
        new_bound_equiv_mol = min(arriving_equiv_mol, capacity_equiv_mol)
        activity_depressed_mol = 2.0 * new_bound_equiv_mol
        physisorbing_mol = max(0.0, arrival_mol - activity_depressed_mol)
        prior_bound[equivalent] = previous_equiv_mol + new_bound_equiv_mol
        source = (
            f"{WALL_REACTIVITY_MATRIX_PATH}::"
            f"alkali_activity_depression.{species}.saturation"
        )
        ratio_extrapolated = bool(ratio_context.get("extrapolated"))
        if ratio_extrapolated:
            warning = str(ratio_context["warning"])
            warnings.warn(warning, RuntimeWarning, stacklevel=2)
        updated_state = {
            "account": account,
            "segment_name": cls._segment_name(account),
            "state_epoch": "transient_c4b",
            "bound_alkali_equiv_mol": prior_bound,
            "capacity_basis_mol": {"SiO2": sio2_basis_mol},
            "saturation_ratio_source": {species: source},
            "saturation_ratio_context": {species: ratio_context},
            "saturation_ratio_extrapolated": {species: ratio_extrapolated},
            "authoritative": False,
        }
        diagnostic = {
            "mechanism": entry.get("mechanism"),
            "status": entry.get("status"),
            "authoritative": False,
            "ledger_credit_species": species,
            "diagnostic_equivalent": equivalent,
            "saturation_ratio": ratio,
            "saturation_ratio_source": source,
            "saturation_ratio_context": ratio_context,
            "saturation_ratio_extrapolated": ratio_extrapolated,
            "wall_temperature_K": (
                float(wall_temperature_K)
                if wall_temperature_K is not None else None
            ),
            "capacity_equiv_mol_before": capacity_equiv_mol,
            "new_bound_equiv_mol": new_bound_equiv_mol,
            "activity_depressed_mol": activity_depressed_mol,
            "physisorbing_mol": physisorbing_mol,
            "retention_tier": (
                "activity_depression_capacity"
                if new_bound_equiv_mol > 0.0
                else "physisorbing_only_saturation_full_or_clean_wall"
            ),
            "rate_law_status": "GAP_NOT_AUTHORITY",
            "ledger_forbidden": tuple(entry.get("ledger_forbidden") or ()),
        }
        return diagnostic, updated_state

    @staticmethod
    def _wall_temperature_K(
        controls: Mapping[str, Any],
        account: str,
    ) -> float | None:
        account_temperatures = controls.get("wall_deposit_account_temperatures_K")
        if isinstance(account_temperatures, Mapping) and account in account_temperatures:
            value = account_temperatures[account]
        else:
            value = controls.get("wall_temperature_K")
        try:
            temperature_K = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(temperature_K):
            return None
        return temperature_K

    @classmethod
    def _alkali_saturation_ratio(
        cls,
        species: str,
        saturation: Mapping[str, Any],
        wall_temperature_K: float | None,
    ) -> tuple[float, dict[str, Any]]:
        if species != "Na":
            return float(saturation["nominal_cold_wall"]), {
                "mode": "fixed_nominal_cold_wall",
                "proxy_gap": saturation.get("temperature_band_status"),
            }

        anchors = cls._na_temperature_band_anchors(saturation)
        if wall_temperature_K is None:
            raise ValueError(
                "Na alkali saturation interpolation requires wall_temperature_K"
            )
        low, high = anchors
        temperature_K = float(wall_temperature_K)
        lo = low["T_K"]
        hi = high["T_K"]
        out_of_band: str | None = None
        if temperature_K <= lo:
            ratio = low["ratio"]
            mode = "clamped_low_temperature_cold_wall"
            if temperature_K < lo:
                out_of_band = "below"
        elif temperature_K >= hi:
            ratio = high["ratio"]
            mode = "clamped_high_temperature_liquidus"
            if temperature_K > hi:
                out_of_band = "above"
        else:
            span = hi - lo
            fraction = (temperature_K - lo) / span
            ratio = low["ratio"] + fraction * (high["ratio"] - low["ratio"])
            mode = "linear_temperature_band"
        ratio_context: dict[str, Any] = {
            "mode": mode,
            "wall_temperature_K": temperature_K,
            "anchors": anchors,
            "validated_band_K": [lo, hi],
            "extrapolated": out_of_band is not None,
        }
        if out_of_band is not None:
            reason = f"wall_T_{out_of_band}_validated_disilicate_band"
            ratio_context.update({
                "out_of_band": out_of_band,
                "reason": reason,
                "warning": (
                    f"{species} alkali saturation_ratio extrapolated beyond "
                    f"validated_disilicate_band_K [{lo:g}, {hi:g}] at "
                    f"{temperature_K:.2f} K; using clamped endpoint ratio "
                    f"{ratio:g}"
                ),
            })
        return ratio, ratio_context

    @staticmethod
    def _na_temperature_band_anchors(
        saturation: Mapping[str, Any],
    ) -> tuple[dict[str, float], dict[str, float]]:
        band = saturation.get("temperature_band")
        if not isinstance(band, Iterable):
            raise ValueError("Na saturation temperature_band is missing")
        anchors: list[dict[str, float]] = []
        for entry in band:
            if not isinstance(entry, Mapping):
                continue
            if "T_K" not in entry or "ratio" not in entry:
                continue
            temperature_K = float(entry["T_K"])
            ratio = float(entry["ratio"])
            if not math.isfinite(temperature_K) or not math.isfinite(ratio):
                raise ValueError("Na saturation temperature_band has non-finite values")
            anchors.append({"T_K": temperature_K, "ratio": ratio})
        if len(anchors) != 2:
            raise ValueError(
                "Na saturation temperature_band requires exactly two T_K anchors"
            )
        anchors.sort(key=lambda anchor: anchor["T_K"])
        return anchors[0], anchors[1]

    @staticmethod
    def _copy_alkali_state(value: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(value, Mapping):
            return {}
        copied: dict[str, dict[str, Any]] = {}
        for account, state in value.items():
            if isinstance(state, Mapping):
                copied[str(account)] = dict(state)
        return copied

    @staticmethod
    def _segment_name(account: str) -> str:
        prefix = "process.wall_deposit_segment_"
        if account.startswith(prefix):
            return account[len(prefix):]
        return account

    @staticmethod
    def _alkali_forbidden_control_refusal(
        species: str,
        controls: Mapping[str, Any],
        control_audit: Any,
    ) -> IntentResult | None:
        if species not in {"Na", "K"}:
            return None
        substrate_debits = controls.get("wall_substrate_debit_mol_by_account")
        if isinstance(substrate_debits, Mapping) and any(substrate_debits.values()):
            reason = "alkali_activity_depression_forbids_wall_substrate_debits"
        else:
            reason = ""
        product_map = controls.get("wall_product_mol_by_account")
        if not reason and isinstance(product_map, Mapping):
            for species_mol in product_map.values():
                if not isinstance(species_mol, Mapping):
                    continue
                forbidden_products = set(species_mol) - {species}
                if forbidden_products:
                    reason = (
                        "alkali_activity_depression_forbids_non_elemental_products"
                    )
                    break
        if not reason and controls.get("wall_oxidant_source"):
            reason = "alkali_activity_depression_forbids_wall_oxidant"
        if not reason:
            return None
        return IntentResult(
            intent=ChemistryIntent.CONDENSATION_ROUTE,
            status="refused",
            transition=None,
            control_audit=control_audit,
            diagnostic={
                "reason": reason,
                "reason_refused": reason,
                "species": species,
            },
        )

    def _condensed_product_mol(
        self,
        species: str,
        condensed_kg: float,
        sp_data: Mapping[str, Any],
        registry: Mapping[str, Any],
        resolve_species_formula,
    ) -> dict[str, float]:
        """Return ``{product_species: mol}`` to credit the condensation train.

        Branches:

        * If ``sp_data`` declares ``condensation_products_mol_per_mol_vapor``
          (disproportionation), build the per-mol-vapor product map.
        * Else, credit a single mol entry for the vapor species itself.

        Mirrors :meth:`_condensed_products_for_vapor` /
        :meth:`_condensation_product_mol_ratios` from
        ``simulator/evaporation.py`` exactly; the validation against
        :class:`AccountingError` lives at the caller (legacy path) and at
        the kernel's atom-balance check. Provider-side we trust the
        sp_data the caller hands us -- it's the same dict the legacy
        path already validated upstream.
        """

        if condensed_kg <= 0.0:
            return {}

        vapor_formula = resolve_species_formula(species, registry)
        vapor_mol = condensed_kg / vapor_formula.molar_mass_kg_per_mol()
        if vapor_mol <= 0.0:
            return {}

        ratios = sp_data.get("condensation_products_mol_per_mol_vapor")
        if ratios is None:
            # Non-disproportionation: condensed product == vapor species.
            return {species: vapor_mol}

        # Disproportionation branch: legacy used the ratio * vapor_mol
        # values directly (a mol-native path even pre-kernel); the kernel
        # reproduces that semantics, materializing kg per product via
        # ``mol * MW`` at commit time.
        product_mol: dict[str, float] = {}
        for product, ratio in dict(ratios).items():
            r = float(ratio)
            if not math.isfinite(r) or r <= 0.0:
                continue
            mol = r * vapor_mol
            if mol > 0.0:
                product_mol[str(product)] = mol
        return product_mol

    def _credits_by_product_account(
        self,
        condensed_product_mol: Mapping[str, float],
        sp_data: Mapping[str, Any],
    ) -> dict[str, dict[str, float]]:
        product_accounts = dict(
            sp_data.get("condensation_product_accounts") or {}
        )
        credits: dict[str, dict[str, float]] = {}
        for product, mol in condensed_product_mol.items():
            account = str(
                product_accounts.get(product) or "process.condensation_train"
            )
            if account not in self._declared_accounts():
                raise ValueError(
                    f"undeclared condensation product account {account!r}"
                )
            species_mol = credits.setdefault(account, {})
            species_mol[str(product)] = species_mol.get(str(product), 0.0) + mol
        return credits

    def _declared_accounts(self) -> frozenset[str]:
        return self.BASE_DECLARED_ACCOUNTS | self._declared_wall_deposit_accounts

    @staticmethod
    def _undeclared_wall_deposit_account(
        controls: Mapping[str, Any],
        declared_accounts: frozenset[str],
    ) -> str | None:
        raw_segment_fractions = controls.get(
            "wall_deposit_account_fractions", {})
        if not isinstance(raw_segment_fractions, Mapping):
            return None
        allowed_wall_accounts = frozenset({WALL_DEPOSIT_ACCOUNT}) | frozenset(
            account
            for account in declared_accounts
            if account.startswith("process.wall_deposit_segment_")
        )
        for account in raw_segment_fractions:
            account_name = str(account)
            if account_name not in allowed_wall_accounts:
                return account_name
        return None

    @staticmethod
    def _wall_deposit_fraction(controls: Mapping[str, Any]) -> float:
        value = controls.get("wall_deposit_fraction", 0.0)
        try:
            fraction = float(value)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(fraction):
            return 0.0
        return max(0.0, min(1.0, fraction))

    @classmethod
    def _wall_deposit_account_fractions(
        cls,
        controls: Mapping[str, Any],
        declared_accounts: frozenset[str],
    ) -> dict[str, float]:
        raw_segment_fractions = controls.get(
            "wall_deposit_account_fractions", {})
        if isinstance(raw_segment_fractions, Mapping):
            fractions: dict[str, float] = {}
            for account, raw_fraction in raw_segment_fractions.items():
                account_name = str(account)
                if account_name not in declared_accounts:
                    continue
                try:
                    fraction = float(raw_fraction)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(fraction) and fraction > 0.0:
                    fractions[account_name] = fraction
            total = sum(fractions.values())
            if total > 0.0:
                return fractions

        return {WALL_DEPOSIT_ACCOUNT: 1.0}

    @classmethod
    def _wall_deposit_mol_by_account(
        cls,
        wall_deposit_mol: float,
        fractions: Mapping[str, float],
    ) -> dict[str, float]:
        if wall_deposit_mol <= 0.0 or not fractions:
            return {}
        credited: dict[str, float] = {}
        running_mol = 0.0
        items = list(fractions.items())
        for account, fraction in items[:-1]:
            account_mol = wall_deposit_mol * fraction
            credited[account] = account_mol
            running_mol += account_mol
        credited[items[-1][0]] = max(0.0, wall_deposit_mol - running_mol)
        return credited

    @staticmethod
    def _species_kg_by_account(
        species_mol_by_account: Mapping[str, Mapping[str, float]],
        registry: Mapping[str, Any],
        resolve_species_formula,
    ) -> dict[str, dict[str, float]]:
        result: dict[str, dict[str, float]] = {}
        for account, species_mol in species_mol_by_account.items():
            account_kg: dict[str, float] = {}
            for species, mol in species_mol.items():
                if mol == 0.0:
                    continue
                formula = resolve_species_formula(species, registry)
                account_kg[species] = (
                    account_kg.get(species, 0.0)
                    + float(mol) * formula.molar_mass_kg_per_mol()
                )
            if account_kg:
                result[account] = account_kg
        return result

    @staticmethod
    def _wall_delta_kg_by_account(
        product_kg_by_account: Mapping[str, Mapping[str, float]],
        substrate_kg_by_account: Mapping[str, Mapping[str, float]],
    ) -> dict[str, dict[str, float]]:
        result: dict[str, dict[str, float]] = {}
        accounts = set(product_kg_by_account) | set(substrate_kg_by_account)
        for account in accounts:
            species_delta: dict[str, float] = {}
            for species, kg in dict(product_kg_by_account.get(account, {})).items():
                species_delta[species] = species_delta.get(species, 0.0) + float(kg)
            for species, kg in dict(substrate_kg_by_account.get(account, {})).items():
                species_delta[species] = species_delta.get(species, 0.0) - float(kg)
            if species_delta:
                result[account] = species_delta
        return result

    @staticmethod
    def _wall_deposit_mol(
        species: str,
        wall_deposit_kg: float,
        registry: Mapping[str, Any],
        resolve_species_formula,
    ) -> float:
        if wall_deposit_kg <= 0.0:
            return 0.0
        vapor_formula = resolve_species_formula(species, registry)
        vapor_mol = wall_deposit_kg / vapor_formula.molar_mass_kg_per_mol()
        return max(0.0, vapor_mol)

    @staticmethod
    def _build_atom_balance_proof(
        debits: Mapping[str, Mapping[str, float]],
        credits: Mapping[str, Mapping[str, float]],
        registry: Mapping[str, Any],
        resolve_species_formula,
    ) -> dict[str, float]:
        """Delegate to the shared :func:`build_atom_balance_proof` helper."""

        return build_atom_balance_proof(
            debits, credits, registry, resolve_species_formula
        )
