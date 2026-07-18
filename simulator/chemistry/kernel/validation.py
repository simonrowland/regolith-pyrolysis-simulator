"""Pre-commit validators that gate every transition proposal.

The kernel never commits a proposal without running these four checks in
order:

1. :func:`validate_intent_authority` -- the proposing provider must have
   the intent in its ``ledger_transition_authority_for`` set.
2. :func:`validate_proposal_accounts` -- every account touched on either
   side of the proposal must be in the provider's
   ``declared_accounts``.
3. :func:`validate_atom_balance` -- the proposal's debits and credits
   must conserve atoms element-by-element.
4. :func:`validate_control_audit` -- the engine's reported applied T /
   P / fO2 must match the requested values within tolerance, or the
   audit must carry an explanatory note.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from simulator.accounting.exceptions import AccountingError, UnbalancedTransitionError
from simulator.accounting.formulas import resolve_species_formula
from simulator.accounting.ledger import LedgerTransition
from simulator.chemistry.kernel.capabilities import CapabilityProfile, ChemistryIntent
from simulator.chemistry.kernel.dto import (
    ControlAudit,
    IntentRequest,
    LedgerTransitionProposal,
)
from simulator.chemistry.kernel.errors import (
    AccountFilterViolation,
    AtomBalanceError,
    ControlAuditMismatch,
    UnauthorizedIntentError,
)


_CONTROL_TOLERANCE_ABS = {
    "temperature_C": 1e-6,
    "pressure_bar": 1e-9,
    "fO2_log": 1e-6,
}
_CONTROL_TOLERANCE_REL = 1e-9

# Proof cross-check tolerance is intentionally tight (five orders of
# magnitude below the conservation gate at
# :data:`simulator.accounting.ledger.DEFAULT_ATOM_TOLERANCE_MOL` =
# 1e-6).  The provider's ``atom_balance_proof`` is a self-declared
# bookkeeping claim; allow only float64 mol-materialization jitter between
# that proof and the kernel's element-by-element recount.
PROOF_CROSSCHECK_TOLERANCE_MOL = 1e-11


def validate_intent_authority(
    intent: ChemistryIntent, profile: CapabilityProfile
) -> None:
    """Reject if the provider is not authoritative for ``intent``.

    Only providers that have declared an intent in
    :attr:`CapabilityProfile.ledger_transition_authority_for` may emit a
    :class:`LedgerTransitionProposal` for it.  Shadow / diagnostic
    providers may return :class:`IntentResult` with ``transition=None``
    only.
    """

    if not profile.may_emit_ledger_transition(intent):
        raise UnauthorizedIntentError(
            f"provider {profile.provider_id!r} lacks ledger-transition authority "
            f"for intent {intent.value!r}; declared ledger authority: "
            f"{sorted(i.value for i in profile.ledger_transition_authority_for)}"
        )


def validate_proposal_accounts(
    proposal: LedgerTransitionProposal, declared: frozenset[str]
) -> None:
    """Reject if a proposal touches an account outside ``declared``."""

    declared_set = frozenset(declared or ())
    touched = proposal.accounts_touched()
    illegal = touched - declared_set
    if illegal:
        raise AccountFilterViolation(
            f"proposal touches undeclared accounts: {sorted(illegal)}; "
            f"declared: {sorted(declared_set)}"
        )


def validate_atom_balance(
    proposal: LedgerTransitionProposal,
    species_formula_registry: Mapping[str, Any] | None,
) -> None:
    """Run the AtomLedger atom-conservation check against a proposal.

    The check is delegated to
    :meth:`LedgerTransition.validate_conservation` so the kernel never
    holds its own conservation logic -- the existing ledger is the
    canonical authority.  Any
    :class:`~simulator.accounting.exceptions.UnbalancedTransitionError`
    or :class:`~simulator.accounting.exceptions.AccountingError` is
    re-raised as :class:`AtomBalanceError` with the original message.
    """

    registry = dict(species_formula_registry or {})
    try:
        transition = _proposal_to_ledger_transition(proposal, registry)
    except AccountingError as exc:
        raise AtomBalanceError(str(exc)) from exc
    try:
        transition.validate_conservation(registry)
    except UnbalancedTransitionError as exc:
        raise AtomBalanceError(str(exc)) from exc

    # Also check the provider's own ``atom_balance_proof`` (if any)
    # against the computed atom counts.  This is a sanity check: a
    # provider that does its own bookkeeping should agree with the
    # ledger's bookkeeping element-by-element.  Tighter than the
    # broader :data:`DEFAULT_ATOM_TOLERANCE_MOL` conservation gate --
    # the proof claim is a provider self-check, not a numerical
    # estimate, so it should match the kernel's computed atoms within
    # floating-point round-off (mirrors
    # :data:`DEFAULT_BALANCE_TOLERANCE_KG`).
    if proposal.atom_balance_proof:
        debit_atoms = transition.debit_atom_moles(registry)
        credit_atoms = transition.credit_atom_moles(registry)
        for element, claimed in proposal.atom_balance_proof.items():
            actual = credit_atoms.get(element, 0.0) - debit_atoms.get(element, 0.0)
            if not math.isclose(
                float(claimed),
                actual,
                abs_tol=PROOF_CROSSCHECK_TOLERANCE_MOL,
                rel_tol=1e-9,
            ):
                raise AtomBalanceError(
                    f"provider atom_balance_proof[{element!r}]={claimed:.12g} "
                    f"disagrees with computed {actual:.12g}"
                )


def validate_control_audit(audit: ControlAudit, request: IntentRequest) -> None:
    """Reject if applied controls drift from the request without explanation.

    For each of ``temperature_C``, ``pressure_bar``, ``fO2_log`` the
    applied value must equal the requested value within
    :data:`_CONTROL_TOLERANCE_ABS` (absolute) or
    :data:`_CONTROL_TOLERANCE_REL` (relative).  If any control disagrees
    AND ``audit.notes`` is empty, raise :class:`ControlAuditMismatch`.
    """

    requested = {
        "temperature_C": request.temperature_C,
        "pressure_bar": request.pressure_bar,
        "fO2_log": request.fO2_log,
    }
    drift: list[str] = []
    missing_applied: list[str] = []
    for key, requested_value in requested.items():
        if requested_value is None:
            # Caller did not specify this control; engine response is
            # informational only.
            continue
        if key not in audit.applied or audit.applied.get(key) is None:
            missing_applied.append(key)
            continue
        applied_value = audit.applied[key]
        tol_abs = _CONTROL_TOLERANCE_ABS.get(key, 1e-9)
        if not math.isclose(
            float(applied_value),
            float(requested_value),
            abs_tol=tol_abs,
            rel_tol=_CONTROL_TOLERANCE_REL,
        ):
            drift.append(
                f"{key}: applied={applied_value!r} requested={requested_value!r}"
            )
    if missing_applied:
        raise ControlAuditMismatch(
            "missing applied-control evidence for requested controls: "
            + ", ".join(missing_applied)
        )
    if drift and not audit.notes:
        raise ControlAuditMismatch(
            "applied controls drift from requested without notes: " + "; ".join(drift)
        )


def _proposal_to_ledger_transition(
    proposal: LedgerTransitionProposal,
    registry: Mapping[str, Any],
    *,
    lot_source: str = "",
    lot_meta: Mapping[str, Any] | None = None,
) -> LedgerTransition:
    """Translate a mol-native proposal into a kg-native :class:`LedgerTransition`.

    Reuses the registry-driven mol -> kg projection that the rest of the
    accounting layer uses while retaining the exact mol-native input in lot
    metadata. Empty species entries are dropped (matches
    :class:`MaterialLot.without_empty`).
    """

    debits = _build_lots(
        proposal.debits,
        registry,
        kind="debit",
        source=lot_source,
        meta=lot_meta,
    )
    credits = _build_lots(
        proposal.credits,
        registry,
        kind="credit",
        source=lot_source,
        meta=lot_meta,
    )
    return LedgerTransition(
        name=proposal.reason or "chemistry_kernel_proposal",
        debits=tuple(debits),
        credits=tuple(credits),
        reason=proposal.reason,
    )


def _build_lots(
    side: Mapping[str, Mapping[str, float]],
    registry: Mapping[str, Any],
    *,
    kind: str,
    source: str = "",
    meta: Mapping[str, Any] | None = None,
) -> list:
    from simulator.accounting.lots import MaterialLot

    lots: list[MaterialLot] = []
    for account, species_mol in dict(side or {}).items():
        species_kg: dict[str, float] = {}
        retained_species_mol: dict[str, float] = {}
        for species, mol in dict(species_mol or {}).items():
            value = float(mol)
            if not math.isfinite(value):
                raise AccountingError(
                    f"proposal {kind} amount for species {species!r} must be finite"
                )
            if value < 0.0:
                raise AccountingError(
                    f"proposal {kind} amount for species {species!r} must be non-negative "
                    f"(got {value:.12g}; sign is conveyed by the debits/credits side)"
                )
            if value == 0.0:
                continue
            formula = resolve_species_formula(str(species), registry)
            species_kg[str(species)] = value * formula.molar_mass_kg_per_mol()
            retained_species_mol[str(species)] = value
        if species_kg:
            lot_meta = dict(meta or {})
            lot_meta["amount_basis"] = "mol"
            lot_meta["species_mol"] = retained_species_mol
            lots.append(
                MaterialLot(
                    str(account),
                    species_kg,
                    source=source,
                    meta=lot_meta,
                )
            )
    return lots
