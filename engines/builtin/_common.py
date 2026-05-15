"""Shared helpers for the builtin chemistry providers.

These helpers live OUTSIDE ``simulator/chemistry/kernel/`` on purpose:
they encode builtin-provider conventions (oxide-weight projection of
``process.cleaned_melt``, control-input idioms) that other engines
(AlphaMELTS, FactSAGE) must not be silently coerced into.

``simulator.accounting.formulas`` and ``simulator.state`` are imported
lazily inside the function bodies that need them. The package-init
cycle that prevents top-level imports is documented in
``engines/builtin/__init__.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.dto import (
    IntentRequest,
    IntentResult,
    ProviderAccountView,
)


def reject_wrong_intent(
    request: IntentRequest, served_intent: ChemistryIntent
) -> IntentResult | None:
    """Defence-in-depth gate against an out-of-band intent.

    The kernel registry is meant to route only the served intent to a
    given provider; if a future caller bypasses that filter, returning
    an ``unsupported`` :class:`IntentResult` surfaces the mismatch at
    the planner layer instead of producing a silent mis-answer.

    Returns ``None`` when the request matches the provider's served
    intent, otherwise an ``unsupported`` :class:`IntentResult` ready to
    be returned from ``dispatch``.
    """

    if request.intent is served_intent:
        return None
    return IntentResult(
        intent=request.intent,
        status="unsupported",
        diagnostic={
            "reason": f"provider only serves {served_intent.value!r}",
        },
    )


def unpack_controls(request: IntentRequest) -> dict[str, Any]:
    """Return ``request.control_inputs`` as a mutable dict.

    Centralises the ``request.control_inputs or {}`` unpack idiom shared
    by every builtin provider so a future change (e.g. tightening
    None-vs-empty semantics) lands in one place.
    """

    return dict(request.control_inputs or {})


def composition_wt_pct_from_account_view(
    view: ProviderAccountView,
    account: str,
    *,
    registry: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    """Convert a cleaned-melt mol view into weight-percent oxides.

    Mirrors :meth:`MeltState.composition_wt_pct` so the activity proxy
    (``a_oxide = wt fraction``) matches the legacy
    :meth:`EquilibriumMixin._stub_equilibrium` exactly.

    Fail-closed on unregistered species: if any species in the account
    cannot resolve a formula, raises :class:`AccountingError` to match
    :meth:`PyrolysisSimulator._load_ledger_account`. The legacy provider
    used to ``continue`` past unresolved species, which silently biased
    the activity proxy by dropping mass from ``total_kg``. Fail-open is
    inconsistent with the rest of the simulator, where Stage 0 already
    rejects unregistered species; aligning the provider here keeps the
    invariant uniform.

    ``registry`` defaults to ``view.species_formula_registry`` when not
    explicitly supplied -- the kwarg exists so a future caller can pass
    a wider registry (e.g. for diagnostic projections that include
    species the provider's filtered view does not).
    """

    # Lazy imports to break the package-init cycle documented in
    # engines/builtin/__init__.py.
    from simulator.accounting.formulas import resolve_species_formula
    from simulator.state import OXIDE_SPECIES

    species_mol = dict(view.accounts.get(account, {}) or {})
    species_registry = (
        view.species_formula_registry if registry is None else registry
    )
    kg_by_species: dict[str, float] = {}
    total_kg = 0.0
    for species, mol in species_mol.items():
        # Raise -- not continue. Matches _load_ledger_account's
        # AccountingError surface so an unregistered species in
        # process.cleaned_melt is a loud failure here too.
        formula = resolve_species_formula(species, species_registry)
        mass_kg = float(mol) * formula.molar_mass_kg_per_mol()
        if mass_kg <= 0.0:
            continue
        kg_by_species[species] = kg_by_species.get(species, 0.0) + mass_kg
        total_kg += mass_kg

    comp_wt: dict[str, float] = {sp: 0.0 for sp in OXIDE_SPECIES}
    if total_kg <= 0.0:
        return comp_wt
    for species, kg in kg_by_species.items():
        # Only OXIDE_SPECIES end up in composition_wt_pct(). Other
        # ledger material in cleaned_melt (rare) contributes to total_kg
        # but not to the per-oxide activity column.
        if species in OXIDE_SPECIES:
            comp_wt[species] = (kg / total_kg) * 100.0
    return comp_wt
