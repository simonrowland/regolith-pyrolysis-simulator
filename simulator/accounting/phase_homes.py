"""Locked crystal cohorts and the one crystallization transition.

Accounts are ``process.crystal.<binding>.<phase>.<cohort>``. Cohort 0 is
the oldest shell. A new shell is the next index. Species inside an
account are oxide tokens. The provider wires the engine; this module
turns the current-temperature oxide vector into one proposal.

Default is locked. Dissolution walks the youngest cohort first and
returns that cohort's stored composition. Growth opens one new cohort
at the accessible solve's composition, taking only the accepted mass.
The probe's compositions are not a commit target.

``holds_positive_crystal_moles`` is the one predicate the scalar-F
sites call. An account whose oxide mass is at or below the phase-row
absolute floor is not a cohort: a finished remelt is unsplit again.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from simulator.accounting.exceptions import AccountingError, UnknownSpeciesError
from simulator.accounting.formulas import parse_formula
from simulator.accounting.oxide_assignment import (
    PHASE_OXIDE_MASS_ABS_TOLERANCE_KG,
)
from simulator.chemistry.kernel.dto import LedgerTransitionProposal
from simulator.melt_backend.base import (
    ENGINE_MAGEMIN_IG,
    ENGINE_PETTHERMOTOOLS,
    engine_phase_role,
)


# Post-commit kilogram band of AtomLedger. Not imported: the ledger
# module sits above this one. This is not the phase-row weight-percent
# band. A shortfall inside this band is the same dust a commit already
# accepts; anything larger is a refusal, not a clip.
_LEDGER_BALANCE_TOLERANCE_KG = 1e-12

# Element totals of a move that only relocates oxide moles. Wider than
# the kilogram band so a matched debit and credit is not refused for
# formula roundoff, and tighter than a missing oxide.
_ELEMENT_CLOSURE_TOLERANCE_MOL = 1e-8

MELTS_BINDING = "melts"
MAGEMIN_BINDING = "magemin"

# The crystallization commit runs on the PetThermoTools path. A MAGEMin
# binding uses the ig table. An unlisted binding matches no phase.
_BINDING_ENGINE = {
    MELTS_BINDING: ENGINE_PETTHERMOTOOLS,
    MAGEMIN_BINDING: ENGINE_MAGEMIN_IG,
}
CRYSTAL_ACCOUNT_PREFIX = "process.crystal."
LIQUID_ACCOUNT = "process.cleaned_melt"

REASON_LIQUID_INSUFFICIENT = "phase_home_liquid_insufficient"
REASON_ELEMENT_UNCLOSED = "phase_home_element_unclosed"
REASON_UNPARSED_SPECIES = "phase_home_unparsed_species"
REASON_PHASE_MASS_INVALID = "phase_home_phase_mass_invalid"
REASON_PHASE_TOKEN_INVALID = "phase_home_phase_token_invalid"
REASON_COHORT_MOLES_INVALID = "phase_home_cohort_moles_invalid"
REASON_DISSOLUTION_SHORT = "phase_home_dissolution_short"
REASON_GROWTH_WITHOUT_COMPOSITION = "phase_home_growth_without_composition"
REASON_NON_SILICATE = "engine_reported_non_silicate_phase"

_ACCOUNT_RE = re.compile(
    r"^process\.crystal\."
    r"(?P<binding>[A-Za-z0-9_]+)\."
    r"(?P<phase>[A-Za-z0-9_]+)\."
    r"(?P<cohort>[0-9]+)$"
)
_TOKEN_RE = re.compile(r"[^A-Za-z0-9_]+")


@dataclass(frozen=True)
class LockedCohort:
    """One stored shell. Mass is the oxide-mole projection, in kilograms."""

    account: str
    binding: str
    phase: str
    cohort: int
    oxide_mol: dict[str, float]
    mass_kg: float


@dataclass(frozen=True)
class PhaseHomeUpdate:
    """A proposal, or a typed refusal of the whole commit."""

    proposal: LedgerTransitionProposal | None
    refusal_reason: str | None = None
    refusal_detail: str = ""
    touched_crystal_accounts: tuple[str, ...] = ()
    phases: tuple[dict[str, float | str], ...] = ()


def sanitise_phase_token(phase_name: str) -> str:
    """Engine phase name as an account token. No mineral crosswalk."""
    cleaned = _TOKEN_RE.sub("_", str(phase_name).strip())
    return re.sub(r"_+", "_", cleaned).strip("_")


def parse_crystal_account(account: str) -> tuple[str, str, int] | None:
    """Return ``(binding, phase, cohort)`` or None when the name is not one."""
    match = _ACCOUNT_RE.fullmatch(str(account).strip())
    if match is None:
        return None
    return (
        match.group("binding"),
        match.group("phase"),
        int(match.group("cohort")),
    )


def crystal_account(binding: str, phase_token: str, cohort: int) -> str:
    """Build one cohort account. ``cohort`` is the growth index."""
    binding_name = str(binding).strip()
    token = sanitise_phase_token(phase_token)
    index = int(cohort)
    if index < 0 or not token or not _ACCOUNT_RE.fullmatch(
        f"process.crystal.{binding_name}.{token}.{index}"
    ):
        raise AccountingError(
            f"{REASON_PHASE_TOKEN_INVALID}: binding {binding_name!r} "
            f"phase {phase_token!r} cohort {cohort!r}"
        )
    return f"process.crystal.{binding_name}.{token}.{index}"


def crystal_accounts_for_binding(
    mol_by_account: Mapping[str, Mapping[str, float]],
    binding: str,
) -> tuple[str, ...]:
    """Every well-formed cohort account of ``binding``, empty ones included."""
    names = []
    for name in mol_by_account:
        parsed = parse_crystal_account(str(name))
        if parsed is not None and parsed[0] == binding:
            names.append(str(name))
    return tuple(sorted(names))


def oxide_mass_kg(oxide_mol: Mapping[str, float]) -> float:
    """Project oxide moles to kilograms.

    Unit check: mol * (M_i g/mol / 1000) = kg. ``M_i`` is
    ``parse_formula``, not a second molar-mass table.
    """
    total = 0.0
    for species, raw in oxide_mol.items():
        moles = float(raw)
        if moles == 0.0:
            continue
        total += moles * parse_formula(str(species)).molar_mass_kg_per_mol()
    return total


def holds_positive_crystal_moles(
    mol_by_account: Mapping[str, Mapping[str, float]],
) -> bool:
    """True when any crystal account still holds a cohort.

    Mass at or below the phase-row absolute floor is the remainder of a
    finished remelt, not a cohort. The scalar-F sites stay on the
    unsplit path in that case.
    """
    if not isinstance(mol_by_account, Mapping):
        return False
    for account, species_mol in mol_by_account.items():
        if not str(account).startswith(CRYSTAL_ACCOUNT_PREFIX):
            continue
        if not isinstance(species_mol, Mapping):
            continue
        try:
            mass_kg = oxide_mass_kg(species_mol)
        except (AccountingError, UnknownSpeciesError, TypeError, ValueError):
            for raw in species_mol.values():
                try:
                    if float(raw) > 0.0:
                        return True
                except (TypeError, ValueError):
                    continue
            continue
        if mass_kg > PHASE_OXIDE_MASS_ABS_TOLERANCE_KG:
            return True
    return False


def signed_locked_masses(
    m_locked_kg: float,
    m_growth_kg: float,
    m_eq_kg: float,
) -> tuple[float, float]:
    """Return ``(dissolve_kg, m_new_kg)``. The two are never both positive."""
    dissolve = max(0.0, float(m_locked_kg) - float(m_eq_kg))
    m_new = max(
        0.0,
        min(float(m_growth_kg), float(m_eq_kg) - float(m_locked_kg)),
    )
    return dissolve, m_new


def locked_cohorts(
    accounts: Mapping[str, Mapping[str, float]],
    binding: str,
) -> tuple[LockedCohort, ...] | str:
    """Material cohorts of ``binding``. A bad mole or token is a reason string."""
    found: list[LockedCohort] = []
    for name, species_mol in accounts.items():
        parsed = parse_crystal_account(str(name))
        if parsed is None or parsed[0] != binding:
            continue
        oxides: dict[str, float] = {}
        for species, raw in dict(species_mol or {}).items():
            try:
                moles = float(raw)
            except (TypeError, ValueError):
                return (
                    f"{REASON_COHORT_MOLES_INVALID}: account {name!r} "
                    f"species {species!r}"
                )
            if moles != moles or moles in (float("inf"), float("-inf")):
                return (
                    f"{REASON_COHORT_MOLES_INVALID}: account {name!r} "
                    f"species {species!r}"
                )
            if moles < 0.0:
                return (
                    f"{REASON_COHORT_MOLES_INVALID}: account {name!r} "
                    f"species {species!r}"
                )
            if moles > 0.0:
                oxides[str(species)] = moles
        if not oxides:
            continue
        try:
            mass_kg = oxide_mass_kg(oxides)
        except (AccountingError, UnknownSpeciesError) as exc:
            return f"{REASON_UNPARSED_SPECIES}: account {name!r}: {exc}"
        if mass_kg <= PHASE_OXIDE_MASS_ABS_TOLERANCE_KG:
            continue
        _binding, phase, cohort = parsed
        found.append(LockedCohort(
            account=str(name),
            binding=_binding,
            phase=phase,
            cohort=cohort,
            oxide_mol=oxides,
            mass_kg=mass_kg,
        ))
    return tuple(found)


def locked_cohort_update(
    *,
    binding: str,
    liquid_oxide_mol: Mapping[str, float],
    accessible_phases: Sequence[Mapping[str, object]],
    probe_phases: Sequence[Mapping[str, object]] | None,
    locked: Sequence[LockedCohort],
    liquid_account: str = LIQUID_ACCOUNT,
    engine: str | None = None,
) -> PhaseHomeUpdate:
    """Build the one proposal, or refuse the whole commit.

    ``probe_phases is None`` means no locked cohort was in the
    inventory, so each phase's equilibrium mass is its accessible
    growth. An empty probe sequence means the probe ran and reported
    no solid: equilibrium mass is zero.
    """
    role_engine = _role_engine(binding, engine)
    try:
        accessible = _phase_table(accessible_phases, role_engine)
        probe = (
            None if probe_phases is None
            else _phase_table(probe_phases, role_engine)
        )
    except _PhaseRowError as exc:
        return _refused(exc.reason, exc.detail)
    liquid = _positive_oxides(liquid_oxide_mol)
    if liquid is None:
        return _refused(
            REASON_COHORT_MOLES_INVALID,
            "accessible liquid moles are not finite",
        )

    by_phase: dict[str, list[LockedCohort]] = defaultdict(list)
    for cohort in locked:
        if cohort.binding != binding:
            continue
        by_phase[cohort.phase].append(cohort)

    phases = set(accessible) | set(by_phase)
    if probe is not None:
        phases |= set(probe)

    debits: dict[str, dict[str, float]] = {}
    credits: dict[str, dict[str, float]] = {}
    phase_notes: list[dict[str, float | str]] = []
    for phase in sorted(phases):
        if not phase:
            return _refused(REASON_PHASE_TOKEN_INVALID, "empty phase token")
        growth_mass, growth_oxides = accessible.get(phase, (0.0, {}))
        if probe is None:
            eq_mass = growth_mass
        else:
            eq_mass = probe.get(phase, (0.0, {}))[0]
        cohorts = sorted(
            by_phase.get(phase, ()),
            key=lambda cohort: cohort.cohort,
            reverse=True,
        )
        m_locked = sum(cohort.mass_kg for cohort in cohorts)
        dissolve, m_new = signed_locked_masses(m_locked, growth_mass, eq_mass)
        note: dict[str, float | str] = {
            "phase": phase,
            "m_locked_kg": m_locked,
            "m_growth_kg": growth_mass,
            "m_eq_kg": eq_mass,
            "dissolve_kg": dissolve,
            "m_new_kg": m_new,
        }
        remaining = dissolve
        for cohort in cohorts:
            if remaining <= PHASE_OXIDE_MASS_ABS_TOLERANCE_KG:
                break
            take = min(cohort.mass_kg, remaining)
            whole = take >= cohort.mass_kg - _LEDGER_BALANCE_TOLERANCE_KG
            fraction = 1.0 if whole else take / cohort.mass_kg
            for species, moles in cohort.oxide_mol.items():
                moved = moles if fraction == 1.0 else moles * fraction
                _add(debits, cohort.account, species, moved)
                _add(credits, liquid_account, species, moved)
            remaining -= cohort.mass_kg if whole else take
        if remaining > PHASE_OXIDE_MASS_ABS_TOLERANCE_KG:
            return _refused(
                REASON_DISSOLUTION_SHORT,
                f"phase {phase!r} still owed {remaining:.6g} kg",
            )
        if m_new > PHASE_OXIDE_MASS_ABS_TOLERANCE_KG:
            if growth_mass <= PHASE_OXIDE_MASS_ABS_TOLERANCE_KG or not growth_oxides:
                return _refused(
                    REASON_GROWTH_WITHOUT_COMPOSITION,
                    f"phase {phase!r} has accepted mass and no accessible vector",
                )
            whole_growth = m_new >= growth_mass - _LEDGER_BALANCE_TOLERANCE_KG
            fraction = 1.0 if whole_growth else m_new / growth_mass
            next_index = (
                max(cohort.cohort for cohort in cohorts) + 1
                if cohorts else 0
            )
            try:
                account = crystal_account(binding, phase, next_index)
            except AccountingError as exc:
                return _refused(REASON_PHASE_TOKEN_INVALID, str(exc))
            for species, moles in growth_oxides.items():
                moved = moles if fraction == 1.0 else moles * fraction
                _add(debits, liquid_account, species, moved)
                _add(credits, account, species, moved)
            note["new_cohort"] = float(next_index)
        phase_notes.append(note)

    if not debits and not credits:
        return PhaseHomeUpdate(proposal=None, phases=tuple(phase_notes))

    shortfall = _liquid_shortfall_kg(
        liquid,
        debits.get(liquid_account, {}),
        credits.get(liquid_account, {}),
    )
    if isinstance(shortfall, str):
        return _refused(REASON_UNPARSED_SPECIES, shortfall)
    if shortfall is not None:
        species, kilograms = shortfall
        return _refused(
            REASON_LIQUID_INSUFFICIENT,
            f"liquid is short {kilograms:.6g} kg of {species}",
        )
    if not _elements_close(liquid, locked, debits, credits, liquid_account):
        return _refused(
            REASON_ELEMENT_UNCLOSED,
            "moved oxides do not preserve the element total",
        )
    touched = tuple(sorted(
        account
        for account in set(debits) | set(credits)
        if account != liquid_account
    ))
    return PhaseHomeUpdate(
        proposal=LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason="phase_home",
        ),
        touched_crystal_accounts=touched,
        phases=tuple(phase_notes),
    )


class _PhaseRowError(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(detail)


def _role_engine(binding: str, engine: str | None) -> str:
    if engine is not None and str(engine).strip():
        return str(engine).strip()
    return _BINDING_ENGINE.get(str(binding).strip(), "")


def _phase_table(
    rows: Sequence[Mapping[str, object]],
    engine: str,
) -> dict[str, tuple[float, dict[str, float]]]:
    table: dict[str, tuple[float, dict[str, float]]] = {}
    liquid_names: set[str] = set()
    for row in rows:
        phase_name = str(row.get("phase") or "").strip()
        if not phase_name:
            continue
        try:
            mass_kg = float(row.get("mass_kg"))
        except (TypeError, ValueError):
            raise _PhaseRowError(
                REASON_PHASE_MASS_INVALID,
                f"phase {phase_name!r} mass is not a number",
            ) from None
        if mass_kg != mass_kg or mass_kg in (float("inf"), float("-inf")) or mass_kg < 0.0:
            raise _PhaseRowError(
                REASON_PHASE_MASS_INVALID,
                f"phase {phase_name!r} mass {mass_kg!r}",
            )
        if mass_kg <= PHASE_OXIDE_MASS_ABS_TOLERANCE_KG:
            continue
        role = engine_phase_role(phase_name, engine)
        if role == "silicate_liquid":
            liquid_names.add(phase_name)
            continue
        if role == "non_silicate":
            raise _PhaseRowError(
                REASON_NON_SILICATE,
                f"phase {phase_name!r} is not a silicate solid",
            )
        token = sanitise_phase_token(phase_name)
        if not token:
            raise _PhaseRowError(
                REASON_PHASE_TOKEN_INVALID,
                f"phase {phase_name!r} has no account token",
            )
        oxides = _positive_oxides(row.get("oxide_mol") or {})
        if oxides is None:
            raise _PhaseRowError(
                REASON_PHASE_MASS_INVALID,
                f"phase {phase_name!r} oxide moles are not finite",
            )
        previous = table.get(token)
        if previous is None:
            table[token] = (mass_kg, oxides)
            continue
        merged = dict(previous[1])
        for species, moles in oxides.items():
            merged[species] = merged.get(species, 0.0) + moles
        table[token] = (previous[0] + mass_kg, merged)
    if len(liquid_names) > 1:
        names = ", ".join(sorted(liquid_names))
        raise _PhaseRowError(
            REASON_NON_SILICATE,
            f"engine reported more than one silicate liquid: {names}",
        )
    return table


def _positive_oxides(oxide_mol: object) -> dict[str, float] | None:
    if not isinstance(oxide_mol, Mapping):
        return None
    oxides: dict[str, float] = {}
    for species, raw in oxide_mol.items():
        try:
            moles = float(raw)
        except (TypeError, ValueError):
            return None
        if moles != moles or moles in (float("inf"), float("-inf")) or moles < 0.0:
            return None
        if moles > 0.0:
            oxides[str(species)] = oxides.get(str(species), 0.0) + moles
    return oxides


def _add(
    side: dict[str, dict[str, float]],
    account: str,
    species: str,
    moles: float,
) -> None:
    if moles <= 0.0:
        return
    bucket = side.setdefault(account, {})
    bucket[species] = bucket.get(species, 0.0) + moles


def _liquid_shortfall_kg(
    liquid: Mapping[str, float],
    debits: Mapping[str, float],
    credits: Mapping[str, float],
) -> tuple[str, float] | str | None:
    species_names = set(debits) | set(credits)
    for species in sorted(species_names):
        net = float(debits.get(species, 0.0)) - float(credits.get(species, 0.0))
        if net <= 0.0:
            continue
        shortfall_mol = net - float(liquid.get(species, 0.0))
        if shortfall_mol <= 0.0:
            continue
        try:
            kilograms = (
                shortfall_mol
                * parse_formula(species).molar_mass_kg_per_mol()
            )
        except (AccountingError, UnknownSpeciesError) as exc:
            return f"{species}: {exc}"
        if kilograms > _LEDGER_BALANCE_TOLERANCE_KG:
            return species, kilograms
    return None


def _elements_close(
    liquid: Mapping[str, float],
    locked: Sequence[LockedCohort],
    debits: Mapping[str, Mapping[str, float]],
    credits: Mapping[str, Mapping[str, float]],
    liquid_account: str,
) -> bool:
    before: dict[str, dict[str, float]] = {liquid_account: dict(liquid)}
    for cohort in locked:
        before[cohort.account] = dict(cohort.oxide_mol)
    after: dict[str, dict[str, float]] = {
        account: dict(species_mol) for account, species_mol in before.items()
    }
    for account, species_mol in debits.items():
        bucket = after.setdefault(account, {})
        for species, moles in species_mol.items():
            bucket[species] = bucket.get(species, 0.0) - moles
    for account, species_mol in credits.items():
        bucket = after.setdefault(account, {})
        for species, moles in species_mol.items():
            bucket[species] = bucket.get(species, 0.0) + moles
    try:
        before_atoms = _element_moles(before)
        after_atoms = _element_moles(after)
    except (AccountingError, UnknownSpeciesError):
        return False
    elements = set(before_atoms) | set(after_atoms)
    return all(
        abs(after_atoms.get(element, 0.0) - before_atoms.get(element, 0.0))
        <= _ELEMENT_CLOSURE_TOLERANCE_MOL
        for element in elements
    )


def _element_moles(
    oxide_mol_by_account: Mapping[str, Mapping[str, float]],
) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for species_mol in oxide_mol_by_account.values():
        for species, moles in species_mol.items():
            if float(moles) == 0.0:
                continue
            formula = parse_formula(str(species))
            for element, count in formula.elements.items():
                totals[element] += float(count) * float(moles)
    return totals


def _refused(reason: str, detail: str) -> PhaseHomeUpdate:
    return PhaseHomeUpdate(
        proposal=None,
        refusal_reason=reason,
        refusal_detail=detail,
    )
