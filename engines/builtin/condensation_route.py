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

- reads ``process.overhead_gas``, ``process.condensation_train``, and
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
    branch; the provider re-uses the same product map the legacy path
    builds in :meth:`_condensed_products_for_vapor` -- the canonical
    SiO -> Si + SiO2 split lives here),
  * ``dt_hr`` -- the tick duration in hours (always 1.0 in the current
    simulator; passed through explicitly so the provider stays unit-
    correct if the simulator's tick step ever changes).

Returns an :class:`IntentResult` with ``transition`` populated by a
:class:`LedgerTransitionProposal` (per-species debit/credit pair) and a
``credited_condensed_kg`` diagnostic so the caller can drive
``_project_condensed_stage_collection`` after the kernel commits.

Authority: authoritative for ``CONDENSATION_ROUTE`` per binding spec
§3. This is the SECOND authoritative ledger-mutating intent in the
migration (after EVAPORATION_TRANSITION) -- ``ChemistryKernel.commit_batch``
engages atom-balance validation at dispatch time AND again at commit
time.

Account declaration: ``process.overhead_gas``,
``process.condensation_train``. The deposition leg is strictly an
overhead -> train transfer; declaring ``process.cleaned_melt`` here
would be an account-scope leak (the melt is the EVAPORATION_TRANSITION
provider's responsibility, not ours). The provider must declare every
account the proposal touches (``validate_proposal_accounts`` enforces
this with :class:`AccountFilterViolation`).
"""

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


class BuiltinCondensationRouteProvider(ChemistryProvider):
    """Authoritative ``CONDENSATION_ROUTE`` provider.

    See module docstring. Stateless -- per-call inputs arrive through
    :class:`IntentRequest.control_inputs`; the same instance serves
    every species in every tick without holding simulator references.
    """

    name = "builtin-condensation-route"
    CHROMIUM_CONDENSED_ACCOUNT = "terminal.chromium_condensed_oxide_stored"

    DECLARED_ACCOUNTS = frozenset({
        "process.overhead_gas",
        "process.condensation_train",
        CHROMIUM_CONDENSED_ACCOUNT,
    })

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="builtin-condensation-route",
            intents=frozenset({ChemistryIntent.CONDENSATION_ROUTE}),
            is_authoritative_for=frozenset(
                {ChemistryIntent.CONDENSATION_ROUTE}
            ),
            declared_accounts=self.DECLARED_ACCOUNTS,
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        # Lazy import: simulator.accounting.formulas pulls in
        # simulator/__init__ which re-enters this module during package
        # init -- see engines/builtin/__init__.py for the cycle
        # description.
        from simulator.accounting.formulas import resolve_species_formula

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

        condensed_kg = float(controls.get("condensed_kg") or 0.0)
        sp_data = dict(controls.get("sp_data") or {})
        dt_hr = float(controls.get("dt_hr", 1.0))  # noqa: F841 -- kept for unit symmetry

        # Below the numerical floor: emit an ok-no-op so the caller's
        # downstream stage-projection skip path stays unambiguous. The
        # legacy `CondensationModel.route` short-circuits on the same
        # 1e-15 kg / 1e-12 kg thresholds; we use 1e-12 here matching
        # `_credit_evaporation_transition` for cross-provider consistency.
        if condensed_kg <= 1e-12:
            return IntentResult(
                intent=ChemistryIntent.CONDENSATION_ROUTE,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "credited_condensed_kg": 0.0,
                    "reason_skipped": "below numerical floor",
                },
            )

        registry = request.account_view.species_formula_registry

        # Build the per-product mol map. Disproportionation branch
        # (SiO -> Si + SiO2) is the canonical case; non-disproportionation
        # species deposit as themselves. Mirrors
        # ``_condensation_product_mol_ratios`` / ``_condensed_products_for_vapor``
        # in simulator/evaporation.py exactly -- this is a refactor of
        # where the math lives, not a re-derivation.
        condensed_product_mol = self._condensed_product_mol(
            species, condensed_kg, sp_data, registry,
            resolve_species_formula,
        )

        if not condensed_product_mol:
            return IntentResult(
                intent=ChemistryIntent.CONDENSATION_ROUTE,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic={"credited_condensed_kg": 0.0},
            )

        # ------------------------------------------------------------------
        # Build the mol-native proposal. Per-account species_mol dicts:
        #   debits:  process.overhead_gas         -> {species: mol}
        #   credits: process.condensation_train   -> {product: mol, ...}
        #            or product-specific accounts declared in sp_data
        # ------------------------------------------------------------------
        vapor_formula = resolve_species_formula(species, registry)
        vapor_mol = condensed_kg / vapor_formula.molar_mass_kg_per_mol()
        debits: dict[str, dict[str, float]] = {
            "process.overhead_gas": {species: vapor_mol},
        }
        credits = self._credits_by_product_account(condensed_product_mol, sp_data)

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

        return IntentResult(
            intent=ChemistryIntent.CONDENSATION_ROUTE,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic={
                "credited_condensed_kg": float(condensed_kg),
            },
        )

    # ------------------------------------------------------------------
    # Helpers (mirror legacy _condensed_products_for_vapor /
    # _condensation_product_mol_ratios exactly).
    # ------------------------------------------------------------------

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
            if account not in self.DECLARED_ACCOUNTS:
                account = "process.condensation_train"
            species_mol = credits.setdefault(account, {})
            species_mol[str(product)] = species_mol.get(str(product), 0.0) + mol
        return credits

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
