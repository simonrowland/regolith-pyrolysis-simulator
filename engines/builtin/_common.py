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

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.dto import (
    ControlAudit,
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


def composition_kg_from_account_view(
    view: ProviderAccountView,
    account: str,
    *,
    registry: Mapping[str, Any] | None = None,
) -> tuple[dict[str, float], float]:
    """Project a mol account view onto ``(kg_by_species, total_kg)``.

    Mirrors :meth:`MeltState.composition_kg`: the per-species kg dict and
    the running total used by every provider whose math needs both a kg
    projection AND a total mass (e.g. the C3/C6 metallothermic solubility
    limits in ``metallothermic_step.py``).

    Fail-closed on unregistered species (raises :class:`AccountingError`
    via :func:`resolve_species_formula`); zero or negative entries are
    skipped silently, matching the legacy
    :meth:`MeltState.composition_kg` projection. Pair with
    :func:`composition_wt_pct_from_account_view` when a provider needs
    both views (the wt-percent helper above re-derives the kg view; the
    factoring of the kg view through this function lets a caller that
    needs ``total_kg`` directly skip the wt% pass).
    """

    # Lazy import: mirrors composition_wt_pct_from_account_view.
    from simulator.accounting.formulas import resolve_species_formula

    species_mol = dict(view.accounts.get(account, {}) or {})
    species_registry = (
        view.species_formula_registry if registry is None else registry
    )
    composition_kg: dict[str, float] = {}
    total_kg = 0.0
    for species, mol in species_mol.items():
        mol_val = float(mol)
        if mol_val <= 0.0:
            continue
        formula = resolve_species_formula(str(species), species_registry)
        mass_kg = mol_val * formula.molar_mass_kg_per_mol()
        if mass_kg <= 0.0:
            continue
        composition_kg[str(species)] = (
            composition_kg.get(str(species), 0.0) + mass_kg
        )
        total_kg += mass_kg
    return composition_kg, total_kg


def build_atom_balance_proof(
    debits: Mapping[str, Mapping[str, float]],
    credits: Mapping[str, Mapping[str, float]],
    registry: Mapping[str, Any],
    resolve_species_formula,
) -> dict[str, float]:
    """Element-by-element ``credit - debit`` atom moles for a proposal.

    Single canonical source-of-truth for the per-provider proof: every
    authoritative provider (EVAPORATION_TRANSITION, CONDENSATION_ROUTE,
    ELECTROLYSIS_STEP, METALLOTHERMIC_STEP, STAGE0_PRETREATMENT)
    delegates to this helper so the proof shape stays uniform and
    cross-checks reliably against the kernel's
    :func:`validate_atom_balance` (which re-computes the same sum from
    its kg-native :class:`LedgerTransition` projection inside
    :data:`PROOF_CROSSCHECK_TOLERANCE_MOL`).

    The function takes ``resolve_species_formula`` as a parameter (not
    an import) to preserve the lazy-import discipline the providers
    follow -- the package-init cycle that motivates lazy imports is
    described in ``engines/builtin/__init__.py``. Callers pass in the
    same callable they already import inside their ``dispatch`` body.

    Each entry should be ~0 (within the kernel's atom-tolerance) for a
    balanced proposal; any non-zero entry surfaces as
    :class:`AtomBalanceError` at commit time.
    """

    net: dict[str, float] = defaultdict(float)
    for side, sign in ((debits, -1.0), (credits, +1.0)):
        for _account, species_mol in dict(side or {}).items():
            for sp, mol in dict(species_mol or {}).items():
                if mol <= 0.0:
                    continue
                formula = resolve_species_formula(str(sp), registry)
                for element, atoms in formula.atom_moles(
                    float(mol)
                ).items():
                    net[str(element)] += sign * float(atoms)
    return dict(net)


def dispatch_reaction_family(
    intent: ChemistryIntent,
    controls: Mapping[str, Any],
    valid_families: Iterable[str],
) -> IntentResult | None:
    """Early-exit guard for providers that branch on a ``reaction_family``.

    Returns ``None`` when the ``controls['reaction_family']`` value is in
    ``valid_families``; otherwise returns an ``unsupported``
    :class:`IntentResult` ready to be returned from ``dispatch``.

    Centralises the boilerplate shared by ``metallothermic_step.py`` and
    ``stage0_pretreatment.py`` so adding a new reaction family touches
    one place and stays a string-literal contract with the caller.
    """

    family = str(controls.get("reaction_family") or "")
    valid_set = frozenset(valid_families)
    if family in valid_set:
        return None
    return IntentResult(
        intent=intent,
        status="unsupported",
        diagnostic={
            "reason": (
                f"reaction_family {family!r} not in {sorted(valid_set)}"
            ),
        },
    )


def diagnostic_control_audit(
    request: IntentRequest,
    *,
    include_fO2: bool = True,
    note: str = (
        "diagnostic only -- engine has no independent T/P/fO2 feedback"
    ),
) -> ControlAudit:
    """Build a :class:`ControlAudit` whose ``applied`` mirrors ``requested``.

    For providers that run pure math against the request's T/P/fO2 with
    no independent control loop (every builtin provider except
    ELECTROLYSIS_STEP, which has its own anode-oxygen activity), the
    applied controls exactly equal the requested controls. The note
    documents that the audit is informational, not feedback.

    Setting ``include_fO2=False`` is appropriate for providers that have
    no fO2 dependency at all (e.g. the metallothermic shuttles read
    solubility limits + reagent mass; they ignore fO2 entirely). In that
    case ``fO2_log`` is omitted from the audit dicts.
    """

    requested: dict[str, Any] = {
        "temperature_C": float(request.temperature_C),
        "pressure_bar": float(request.pressure_bar),
    }
    if include_fO2:
        requested["fO2_log"] = (
            float(request.fO2_log)
            if request.fO2_log is not None
            else None
        )
    return ControlAudit(
        requested=requested,
        applied=dict(requested),
        notes=(note,),
    )
