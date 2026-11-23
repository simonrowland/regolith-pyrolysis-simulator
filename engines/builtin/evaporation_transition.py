"""Builtin EVAPORATION_TRANSITION provider (melt -> vapor ledger update).

Kernel-registered provider that owns the ``EVAPORATION_TRANSITION``
intent. Mirrors the per-species debit/credit pattern in
:meth:`simulator.evaporation.EvaporationMixin._credit_evaporation_transition`
exactly -- this is a refactor of where the LedgerTransition is built, not
a re-derivation of the stoich math (which still routes through
``_evaporation_stoich`` at the caller). The provider:

- reads ``process.cleaned_melt``, ``process.overhead_gas``,
  ``process.condensation_train``, and ``reservoir.fo2_buffer`` from the
  account view -- the accounts the evaporation transition touches (see
  "Account declaration" below for the rationale),
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
    ``process.cleaned_melt``. Grouped analytic depletion already applied
    before dispatch; this only preserves the no-stock short-circuit.
  * ``post_subfloor_coupled_legs`` -- optional bool. Absent or false
    keeps the numerical floor: a coupled leg in ``(0, floor]`` returns
    no transition, and a parent debit at or below the floor returns
    no transition. True is the headspace hour. That hour posts the
    vapour and its stoichiometric legs when the melt stock covers
    the parent debit, even when a leg or the debit itself is below
    the floor. A debit the melt does not cover still returns nothing.

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
``process.condensation_train``, ``reservoir.fo2_buffer``. The legacy
:meth:`_credit_evaporation_transition` builds a single
:class:`LedgerTransition` with these accounts on its debit/credit
sides; the provider must declare every account the proposal touches
(``validate_proposal_accounts`` enforces this with
:class:`AccountFilterViolation`). The forward posting debits the
cleaned melt and credits uncondensed vapour to overhead gas. A
negative rate is the same legs with debit and credit swapped:
overhead loses the vapour and the melt gains the parent oxide
(d-468). The condensation train is credited with the condensed
fraction of a forward parcel only (in mol if the species
disproportionates, in kg otherwise -- the kernel's proposal layer is
mol-native, so the provider converts). Stoichiometric O2 is posted by
``_stoich_o2_is_overhead`` and ``_stoich_o2_debit_account``, using the
stoich sign. The swap reverses that leg against the same account.
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
from simulator.scalar_boundary import is_declared_real_scalar


_NUMERICAL_FLOOR_KG = 1.0e-12


def evaporation_parent_is_below_floor(
    oxide_removed_kg: float,
    available_kg: float,
    floor_kg: float = _NUMERICAL_FLOOR_KG,
) -> bool:
    """The parent debit or the melt stock is at or below the floor.

    The provider and the evaporation caller both refuse the parcel on
    this comparison. A stock read that is exactly the floor posts nothing.
    """

    return (
        float(oxide_removed_kg) <= floor_kg
        or float(available_kg) <= floor_kg
    )


def evaporation_stock_covers_parent_debit(
    oxide_removed_kg: float,
    available_kg: float,
) -> bool:
    """The melt holds this parent debit, numerical floor or not.

    Premise: the debit is ``oxide_removed_kg`` of the parent oxide.
    Stock covers it when ``available_kg >= oxide_removed_kg > 0``.
    A non-positive debit is not a parcel. A shortfall is not posted
    in part: the caller skips the species.
    Unit check: both arguments are kilograms.
    Limit: equal stock and debit posts the whole stock. Zero stock
    posts nothing.
    """

    removed = float(oxide_removed_kg)
    available = float(available_kg)
    return (
        math.isfinite(removed)
        and math.isfinite(available)
        and removed > 0.0
        and available >= removed
    )


def evaporation_parent_debit_is_withheld(
    oxide_removed_kg: float,
    available_kg: float,
    *,
    post_subfloor_coupled_legs: bool = False,
    floor_kg: float = _NUMERICAL_FLOOR_KG,
) -> bool:
    """True when this parent debit must not be posted.

    Absent the headspace flag, the kilogram floor withholds the
    debit. The headspace hour withholds only a debit the melt does
    not cover. A covered debit below the floor is posted: the floor
    is not a second gas state.
    """

    if post_subfloor_coupled_legs:
        return not evaporation_stock_covers_parent_debit(
            oxide_removed_kg,
            available_kg,
        )
    return evaporation_parent_is_below_floor(
        oxide_removed_kg,
        available_kg,
        floor_kg,
    )


def evaporation_net_direction(rate_kg_hr: float) -> str:
    """``evaporate``, ``recondense``, or ``none``.

    Positive mass leaves the melt. Negative mass is vapour returning
    to the melt. Zero is not a parcel. The lot builder posts the
    positive magnitudes once, then swaps debit and credit for the
    reverse. Callers do not grow a second leg table.
    """

    rate = float(rate_kg_hr)
    if not math.isfinite(rate) or rate == 0.0:
        return "none"
    if rate > 0.0:
        return "evaporate"
    return "recondense"


def evaporation_remaining_is_unphysical(
    rate_kg_hr: float,
    remaining_kg_hr: float,
    *,
    tol_kg_hr: float = _NUMERICAL_FLOOR_KG,
) -> bool:
    """The condensation remainder is not a share of this parcel.

    Forward: remaining is in ``[-tol, rate + tol]``. The vapour that
    stays overhead cannot exceed the vapour just credited, and a
    negative remainder is not a condenser result.
    Reverse: the condenser takes nothing. Remaining equals the rate,
    both negative, within ``tol``. A share would post the vapour twice.
    Unit check: both arguments are kg/h. Limit: a non-finite value is
    unphysical. A zero rate is not a parcel; a remainder beside zero
    is unphysical.
    """

    rate = float(rate_kg_hr)
    remaining = float(remaining_kg_hr)
    if not math.isfinite(rate) or not math.isfinite(remaining):
        return True
    if rate > 0.0:
        return remaining < -tol_kg_hr or remaining > rate + tol_kg_hr
    if rate < 0.0:
        return abs(remaining - rate) > tol_kg_hr
    return abs(remaining) > tol_kg_hr


def evaporation_parcel_is_withheld(
    oxide_kg_signed: float,
    available_kg: float,
    *,
    post_subfloor_coupled_legs: bool = False,
    floor_kg: float = _NUMERICAL_FLOOR_KG,
) -> bool:
    """True when this signed parent leg must not be posted.

    A positive leg is the forward debit and uses
    ``evaporation_parent_debit_is_withheld``. A negative leg creates
    the parent oxide. The melt does not have to hold it already.
    The headspace hour posts that credit even below the floor: the
    floor is not a second gas state (d-455). Off that hour, a credit
    at or below the floor is withheld with the forward debit.
    """

    oxide = float(oxide_kg_signed)
    if oxide < 0.0:
        if post_subfloor_coupled_legs:
            return False
        return abs(oxide) <= floor_kg
    return evaporation_parent_debit_is_withheld(
        oxide,
        available_kg,
        post_subfloor_coupled_legs=post_subfloor_coupled_legs,
        floor_kg=floor_kg,
    )


def nonnegative_evaporation_view(flux):
    """The condenser view of a flux. The same object when nothing returns.

    Condensation rejects a negative species rate. Recondensation is
    posted by the transition, not by the train. Returning the caller's
    object when every rate is nonnegative keeps the dilute path on
    that object.
    """

    rates = getattr(flux, "species_kg_hr", None) or {}
    if not any(float(rate) < 0.0 for rate in rates.values()):
        return flux
    from simulator.state import EvaporationFlux

    positive = {
        str(species): float(rate)
        for species, rate in rates.items()
        if float(rate) >= 0.0
    }
    view = EvaporationFlux(
        species_kg_hr=positive,
        alpha_authority_status_by_species={
            str(species): status
            for species, status in dict(
                getattr(flux, "alpha_authority_status_by_species", {}) or {}
            ).items()
            if str(species) in positive
        },
        carrier_authority_by_species=dict(
            getattr(flux, "carrier_authority_by_species", {}) or {}
        ),
    )
    view.update_totals()
    return view


def coupled_evaporation_legs_kg(
    *,
    oxide_removed_kg: float,
    remaining_vapor_kg: float,
    oxygen_coproduct_kg: float,
    condensed_product_kg: Mapping[str, float],
) -> dict[str, float]:
    """Kg of each coupled leg the evaporation transition posts together.

    MaterialLot floors a component on its own. A leg in (0, floor] makes
    the provider skip the whole parcel so one side cannot land alone.
    The headspace source uses the same legs. The hour's commit passes
    remaining equal to the rate, so the condensed-product map is empty
    on that call.
    """

    legs = {
        "parent_oxide": float(oxide_removed_kg),
        "remaining_vapor": float(remaining_vapor_kg),
        "oxygen_coproduct": abs(float(oxygen_coproduct_kg)),
    }
    for product_species, kg in condensed_product_kg.items():
        legs[f"condensed:{product_species}"] = float(kg)
    return legs


def subfloor_legs_kg(
    legs: Mapping[str, float],
    floor_kg: float = _NUMERICAL_FLOOR_KG,
) -> dict[str, float]:
    """Legs strictly above zero and at or below the floor."""

    return {
        name: kg
        for name, kg in legs.items()
        if 0.0 < kg <= floor_kg
    }


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
        "reservoir.fo2_buffer",
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
        oxide_raw = stoich.get("oxide_per_product_kg", 0.0)
        o2_raw = stoich.get("O2_per_product_kg", 0.0)
        if not is_declared_real_scalar(
            oxide_raw,
            allow_numeric_str=True,
        ) or not is_declared_real_scalar(o2_raw, allow_numeric_str=True):
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_TRANSITION,
                status="unsupported",
                control_audit=control_audit,
                diagnostic={"reason": "stoich numeric input is missing"},
            )
        oxide_per_product_kg = float(oxide_raw)
        O2_per_product_kg = float(o2_raw)
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
        if "post_subfloor_coupled_legs" in controls:
            post_subfloor_coupled_legs = controls["post_subfloor_coupled_legs"]
            if post_subfloor_coupled_legs is not True and (
                post_subfloor_coupled_legs is not False
            ):
                return IntentResult(
                    intent=ChemistryIntent.EVAPORATION_TRANSITION,
                    status="unsupported",
                    control_audit=control_audit,
                    diagnostic={
                        "reason": (
                            "post_subfloor_coupled_legs must be a bool"
                        ),
                    },
                )
        else:
            post_subfloor_coupled_legs = False
        oxygen_destination = str(sp_data.get("oxygen_destination") or "")
        if oxygen_destination and oxygen_destination not in {
            "process.overhead_gas",
            "reservoir.fo2_buffer",
        }:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_TRANSITION,
                status="unsupported",
                control_audit=control_audit,
                diagnostic={
                    "reason": (
                        f"unsupported oxygen_destination {oxygen_destination!r}"
                    ),
                },
            )

        # Negative dt_hr or species the caller flagged should never reach
        # here; if they do, surface as 'ok' with no transition (matches
        # legacy short-circuit returns). The signed oxide is the stock
        # test. The lot builder below uses the positive magnitude, then
        # one swap turns a recondensation into a vapour debit and a
        # parent credit. The stoich sign of O2, not the rate sign, picks
        # the coproduct account.
        direction = evaporation_net_direction(rate_kg_hr)
        oxide_signed = rate_kg_hr * dt_hr * oxide_per_product_kg
        if evaporation_parcel_is_withheld(
            oxide_signed,
            available_kg,
            post_subfloor_coupled_legs=post_subfloor_coupled_legs,
        ):
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

        scale = 1.0

        # Sanity-check the condensation route's verdict. The caller
        # already raises AccountingError on the same conditions before
        # dispatch; this is defence in depth that returns an
        # ``unsupported`` IntentResult if a future caller bypasses the
        # pre-validation. The predicate is the owner of both signs.
        if evaporation_remaining_is_unphysical(rate_kg_hr, remaining_kg_hr):
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
        magnitude_kg_hr = abs(rate_kg_hr)
        oxide_removed = magnitude_kg_hr * dt_hr * oxide_per_product_kg
        product_kg = magnitude_kg_hr * dt_hr
        O2_kg = magnitude_kg_hr * dt_hr * O2_per_product_kg
        if direction == "recondense":
            # The whole vapour returns. Condensed is zero, so the swap
            # debits overhead for that vapour and does not credit the train.
            remaining_kg = product_kg
            condensed_kg = 0.0
        else:
            remaining_kg = max(0.0, remaining_kg_hr) * dt_hr
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

        vapor_formula = resolve_species_formula(species, registry)
        vapor_oxygen_atoms = float(vapor_formula.elements.get("O", 0.0) or 0.0)
        condensed_product_mol = self._condensed_product_mol(
            species, condensed_kg, sp_data, registry,
            resolve_species_formula,
        )
        condensed_product_kg = {
            product_species: (
                float(product_mol)
                * resolve_species_formula(
                    product_species,
                    registry,
                ).molar_mass_kg_per_mol()
            )
            for product_species, product_mol in condensed_product_mol.items()
        }
        coupled_leg_kg = coupled_evaporation_legs_kg(
            oxide_removed_kg=oxide_removed,
            remaining_vapor_kg=remaining_kg,
            oxygen_coproduct_kg=O2_kg,
            condensed_product_kg=condensed_product_kg,
        )
        subfloor_legs = subfloor_legs_kg(coupled_leg_kg)
        # Absent or false: a leg in (0, floor] posts nothing, so one
        # side cannot land alone. The headspace hour sets the control
        # and posts every positive leg. The stock-cover return above
        # is what still applies: a debit the melt does not hold is
        # not posted.
        if subfloor_legs and not post_subfloor_coupled_legs:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_TRANSITION,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "credited_condensed_kg": 0.0,
                    "reason_skipped": "coupled stoichiometric leg below numerical floor",
                    "subfloor_legs_kg": subfloor_legs,
                },
            )

        # ------------------------------------------------------------------
        # Build the mol-native proposal. Per-account species_mol dicts:
        #   debits:  process.cleaned_melt -> {parent_oxide: mol}
        #   credits: process.condensation_train -> {product: mol, ...}
        #            process.overhead_gas        -> {species: mol, O2: mol}
        #            reservoir.fo2_buffer        -> {O2: mol}
        # ------------------------------------------------------------------
        debits: dict[str, dict[str, float]] = {}
        credits: dict[str, dict[str, float]] = {}

        parent_oxide_formula = resolve_species_formula(parent_oxide, registry)
        oxide_mol = oxide_removed / parent_oxide_formula.molar_mass_kg_per_mol()
        if oxide_mol > 0.0:
            debits["process.cleaned_melt"] = {parent_oxide: oxide_mol}

        # Once the parent debit clears the transition floor, retain every
        # positive stoichiometric leg. Dropping a smaller coproduct here makes
        # individually admitted carrier transitions lose atoms cumulatively.
        if condensed_kg > 0.0 and condensed_product_mol:
            credits["process.condensation_train"] = dict(condensed_product_mol)

        overhead_credit: dict[str, float] = {}
        if remaining_kg > 0.0:
            overhead_credit[species] = (
                remaining_kg / vapor_formula.molar_mass_kg_per_mol()
            )
        # The account is the stoich owner. A positive coproduct is a
        # credit. A negative leg is a debit. This block does not
        # restate the destination test.
        from simulator.evaporation import (
            _stoich_o2_debit_account,
            _stoich_o2_is_overhead,
        )

        if O2_kg > 0.0:
            o2_formula = resolve_species_formula("O2", registry)
            o2_mol = O2_kg / o2_formula.molar_mass_kg_per_mol()
            if _stoich_o2_is_overhead(
                O2_kg,
                oxygen_destination,
                vapor_oxygen_atoms,
            ):
                overhead_credit["O2"] = o2_mol
            else:
                credits["reservoir.fo2_buffer"] = {"O2": o2_mol}
        if overhead_credit:
            credits["process.overhead_gas"] = overhead_credit
        if O2_kg < 0.0:
            o2_formula = resolve_species_formula("O2", registry)
            debit_account = _stoich_o2_debit_account(
                O2_kg,
                oxygen_destination,
                vapor_oxygen_atoms,
            )
            o2_debit = debits.setdefault(debit_account, {})
            o2_debit["O2"] = (
                abs(O2_kg) / o2_formula.molar_mass_kg_per_mol()
            )

        # One swap. The legs above are the forward posting at the
        # positive magnitude. Recondensation debits the vapour from
        # overhead and credits the parent oxide to the melt, and the
        # O2 coproduct reverses against the account that received it.
        if direction == "recondense":
            debits, credits = credits, debits
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
        # caller runs against the same numbers. The swap negates a
        # zero proof, which stays zero.
        atom_proof: dict[str, float] = build_atom_balance_proof(
            debits, credits, registry, resolve_species_formula,
        )
        reason = (
            f"recondense_{species}"
            if direction == "recondense"
            else f"evaporate_{species}"
        )

        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason=reason,
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
