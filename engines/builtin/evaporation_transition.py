"""Builtin EVAPORATION_TRANSITION provider (melt -> vapor ledger update).

Kernel-registered provider that owns the ``EVAPORATION_TRANSITION``
intent. Mirrors the per-species debit/credit pattern in
:meth:`simulator.evaporation.EvaporationMixin._credit_evaporation_transition`
exactly -- this is a refactor of where the LedgerTransition is built, not
a re-derivation of the stoich math (which still routes through
``_evaporation_stoich`` at the caller). The provider:

- reads ``process.cleaned_melt``, ``process.overhead_gas``, and
  ``process.condensation_train`` from the account view -- the three
  accounts the legacy transition touches (see "Account declaration"
  below for the rationale on all three),
- reads T from ``request.temperature_C``,
- reads the per-species flux + condensation outcome from
  ``request.control_inputs``:

  * ``rate_kg_hr`` -- the species evaporation rate from
    :class:`EvaporationFlux` (same units as legacy, integrated over the
    1-hour tick implicit in ``dt_hr``),
  * ``remaining_kg_hr`` -- the vapor mass the condensation route
    decided NOT to condense (this is what flows on to
    ``process.overhead_gas``),
  * ``stoich`` -- the pre-validated stoich dict from
    :meth:`_evaporation_stoich` carrying ``parent_oxide``,
    ``oxide_per_product_kg``, ``O2_per_product_kg``,
  * ``species`` -- the vapor species name,
  * ``sp_data`` -- the raw vapor_pressures.yaml metadata for the
    species (used only to look up
    ``condensation_products_mol_per_mol_vapor`` for the disproportionation
    branch; the provider re-derives the same product map the legacy path
    builds in :meth:`_condensed_products_for_vapor`),
  * ``dt_hr`` -- the tick duration in hours (always 1.0 in the current
    simulator; passed through explicitly so the provider stays unit-
    correct if the simulator's tick step ever changes),
  * ``available_kg`` -- the parent-oxide kg currently held in
    ``process.cleaned_melt`` (the same value
    ``atom_ledger.kg_by_account('process.cleaned_melt')[parent_oxide]``
    returns at the time of dispatch; passed in to keep the provider
    stateless about ledger projections).

Returns an :class:`IntentResult` with ``transition`` populated by a
:class:`LedgerTransitionProposal` (per-species debit/credit pair) and a
``credited_condensed_kg`` diagnostic so the caller can drive
``_project_condensed_stage_collection`` after the kernel commits.

Authority: authoritative for ``EVAPORATION_TRANSITION`` per binding spec
§3 (Builtin authoritative). This is the first intent in the migration
where ``ChemistryKernel.commit_batch`` actually engages -- atom-balance
validation runs both at dispatch time (in ``validate_atom_balance``) and
again at commit time (re-validated by ``commit_batch``).

Account declaration: ``process.cleaned_melt``, ``process.overhead_gas``,
``process.condensation_train``. The legacy
:meth:`_credit_evaporation_transition` builds a single
:class:`LedgerTransition` with these three accounts on its debit/credit
sides; the provider must declare every account the proposal touches
(``validate_proposal_accounts`` enforces this with
:class:`AccountFilterViolation`). The cleaned-melt is the debit; the
condensation_train is credited with the condensed fraction (in mol if
the species disproportionates, in kg otherwise -- the kernel's
proposal layer is mol-native, so the provider converts); the overhead_gas
is credited with the uncondensed vapor + the O2 coproduct.
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


class BuiltinEvaporationTransitionProvider(ChemistryProvider):
    """Authoritative ``EVAPORATION_TRANSITION`` provider.

    See module docstring. Stateless -- per-call inputs arrive through
    :class:`IntentRequest.control_inputs`; the same instance serves
    every species in every tick without holding simulator references.
    """

    name = "builtin-evaporation-transition"

    DECLARED_ACCOUNTS = frozenset({
        "process.cleaned_melt",
        "process.overhead_gas",
        "process.condensation_train",
    })

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="builtin-evaporation-transition",
            intents=frozenset({ChemistryIntent.EVAPORATION_TRANSITION}),
            is_authoritative_for=frozenset(
                {ChemistryIntent.EVAPORATION_TRANSITION}
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
            request, ChemistryIntent.EVAPORATION_TRANSITION
        )
        if wrong_intent is not None:
            return wrong_intent

        # The evaporation transition is pure kg/mol bookkeeping against the
        # caller's flux + stoich -- the engine has no independent feedback
        # on T/P/fO2, so applied == requested verbatim with the
        # diagnostic-only note.
        control_audit = diagnostic_control_audit(request)

        controls = unpack_controls(request)
        species = str(controls.get("species") or "")
        if not species:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_TRANSITION,
                status="unsupported",
                control_audit=control_audit,
                diagnostic={"reason": "missing 'species' control input"},
            )

        stoich = dict(controls.get("stoich") or {})
        parent_oxide = str(stoich.get("parent_oxide") or "")
        oxide_per_product_kg = float(stoich.get("oxide_per_product_kg") or 0.0)
        O2_per_product_kg = float(stoich.get("O2_per_product_kg") or 0.0)
        if not parent_oxide or oxide_per_product_kg <= 0.0:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_TRANSITION,
                status="unsupported",
                control_audit=control_audit,
                diagnostic={
                    "reason": (
                        "stoich must carry parent_oxide and positive "
                        "oxide_per_product_kg"
                    ),
                },
            )

        rate_kg_hr = float(controls.get("rate_kg_hr") or 0.0)
        remaining_kg_hr = float(controls.get("remaining_kg_hr") or 0.0)
        dt_hr = float(controls.get("dt_hr", 1.0))
        sp_data = dict(controls.get("sp_data") or {})
        available_kg = float(controls.get("available_kg") or 0.0)

        # Mirror legacy line-for-line. Negative dt_hr or species the
        # caller flagged should never reach here; if they do, surface as
        # 'ok' with no transition (matches legacy short-circuit returns).
        oxide_removed = rate_kg_hr * dt_hr * oxide_per_product_kg
        product_kg = rate_kg_hr * dt_hr
        O2_kg = rate_kg_hr * dt_hr * O2_per_product_kg

        if oxide_removed <= 1e-12 or available_kg <= 1e-12:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_TRANSITION,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "credited_condensed_kg": 0.0,
                    "reason_skipped": (
                        "below numerical floor or no parent-oxide stock"
                    ),
                },
            )

        scale = min(1.0, available_kg / oxide_removed)
        oxide_removed *= scale
        product_kg *= scale
        O2_kg *= scale

        # Sanity-check the condensation route's verdict. The caller
        # already raises AccountingError on the same conditions before
        # dispatch; this is defence in depth that returns an
        # ``unsupported`` IntentResult if a future caller bypasses the
        # pre-validation.
        if remaining_kg_hr < -1e-12 or remaining_kg_hr > rate_kg_hr + 1e-12:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_TRANSITION,
                status="unsupported",
                control_audit=control_audit,
                diagnostic={
                    "reason": (
                        f"condensation route for {species!r} returned "
                        "unphysical remaining vapor mass"
                    ),
                },
            )
        remaining_kg = max(0.0, remaining_kg_hr) * dt_hr * scale
        if remaining_kg > product_kg + 1e-12:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_TRANSITION,
                status="unsupported",
                control_audit=control_audit,
                diagnostic={
                    "reason": (
                        f"condensation route for {species!r} exceeds "
                        "credited vapor"
                    ),
                },
            )
        condensed_kg = max(0.0, product_kg - remaining_kg)

        # Disproportionation branch: if sp_data declares a per-mol-vapor
        # product map, the condensation train is credited in mol with
        # those product species. Otherwise the train is credited with
        # the vapor species itself in mol (registry-driven kg -> mol
        # conversion happens in the kernel's proposal->transition step;
        # the provider speaks mol natively here to match the kernel API).
        registry = request.account_view.species_formula_registry

        condensed_product_mol = self._condensed_product_mol(
            species, condensed_kg, sp_data, registry,
            resolve_species_formula,
        )

        # ------------------------------------------------------------------
        # Build the mol-native proposal. Per-account species_mol dicts:
        #   debits:  process.cleaned_melt -> {parent_oxide: mol}
        #   credits: process.condensation_train -> {product: mol, ...}
        #            process.overhead_gas        -> {species: mol, O2: mol}
        # ------------------------------------------------------------------
        debits: dict[str, dict[str, float]] = {}
        credits: dict[str, dict[str, float]] = {}

        parent_oxide_formula = resolve_species_formula(parent_oxide, registry)
        oxide_mol = oxide_removed / parent_oxide_formula.molar_mass_kg_per_mol()
        if oxide_mol > 0.0:
            debits["process.cleaned_melt"] = {parent_oxide: oxide_mol}

        if condensed_kg > 1e-12 and condensed_product_mol:
            credits["process.condensation_train"] = dict(condensed_product_mol)

        overhead_credit: dict[str, float] = {}
        if remaining_kg > 1e-12:
            vapor_formula = resolve_species_formula(species, registry)
            overhead_credit[species] = (
                remaining_kg / vapor_formula.molar_mass_kg_per_mol()
            )
        if O2_kg > 1e-12:
            o2_formula = resolve_species_formula("O2", registry)
            overhead_credit["O2"] = (
                O2_kg / o2_formula.molar_mass_kg_per_mol()
            )
        if overhead_credit:
            credits["process.overhead_gas"] = overhead_credit

        if not debits and not credits:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_TRANSITION,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic={"credited_condensed_kg": 0.0},
            )

        # Atom-balance proof: element-by-element net (credit - debit).
        # Must be zero element-by-element (the kernel re-checks this
        # at commit time -- this is the provider's own bookkeeping
        # surface, matched against the kernel's authoritative count).
        # Mirrors _validate_evaporation_stoich_atoms which the legacy
        # caller runs against the same numbers.
        atom_proof: dict[str, float] = build_atom_balance_proof(
            debits, credits, registry, resolve_species_formula,
        )

        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason=f"evaporate_{species}",
            atom_balance_proof=atom_proof,
        )

        return IntentResult(
            intent=ChemistryIntent.EVAPORATION_TRANSITION,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic={
                "credited_condensed_kg": float(condensed_kg),
                "applied_scale": float(scale),
            },
        )

    # ------------------------------------------------------------------
    # Helpers (mirror legacy _condensed_products_for_vapor /
    # _condensation_product_mol_ratios path exactly).
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
        ``AccountingError`` lives at the caller (legacy path) and at the
        kernel's atom-balance check. Provider-side we trust the
        sp_data the caller hands us -- it's the same dict the legacy
        path already validated upstream.
        """

        if condensed_kg <= 0.0:
            return {}

        vapor_formula = resolve_species_formula(species, registry)
        vapor_mol = condensed_kg / vapor_formula.molar_mass_kg_per_mol()

        ratios = sp_data.get("condensation_products_mol_per_mol_vapor")
        if ratios is None:
            # Non-disproportionation: condensed product == vapor species.
            # The kernel materializes kg via ``mol * MW`` at commit time;
            # 1 ULP per species round-trip is absorbed by the
            # simulator-level mass-balance tolerance.
            return {species: vapor_mol} if vapor_mol > 0.0 else {}

        # Disproportionation branch: legacy used ``credit_mol`` with the
        # ratio * vapor_mol values directly (a mol-native path even
        # pre-kernel); the kernel reproduces that semantics, materializing
        # kg per product via ``mol * MW``.
        product_mol: dict[str, float] = {}
        for product, ratio in dict(ratios).items():
            r = float(ratio)
            if not math.isfinite(r) or r <= 0.0:
                continue
            mol = r * vapor_mol
            if mol > 0.0:
                product_mol[str(product)] = mol
        return product_mol

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
