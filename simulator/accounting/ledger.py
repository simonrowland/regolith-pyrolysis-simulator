"""Atom-conserving ledger primitives."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from simulator.accounting.exceptions import (
    AccountingError,
    OverdraftError,
    UnbalancedTransitionError,
)
from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL, resolve_species_formula
from simulator.accounting.lots import MaterialLot

DEFAULT_MASS_TOLERANCE_KG = 2e-2
DEFAULT_ATOM_TOLERANCE_MOL = 1e-6
DEFAULT_RELATIVE_TOLERANCE = 1e-9
DEFAULT_BALANCE_TOLERANCE_KG = 1e-12
DEFAULT_SCOPE = "batch"
POLICY_SCOPES = {"batch", "campaign", "external"}
TERMINAL_DEBIT_EXCEPTIONS = {
    (
        "terminal.oxygen_melt_offgas_stored",
        "terminal.oxygen_melt_offgas_vented_to_vacuum",
    ): frozenset({"O2"}),
}
TERMINAL_ACCOUNT_ALLOWED_SPECIES = {
    "terminal.oxygen_melt_offgas_stored": frozenset({"O2"}),
    "terminal.oxygen_melt_offgas_vented_to_vacuum": frozenset({"O2"}),
    "terminal.oxygen_mre_anode_stored": frozenset({"O2"}),
}


@dataclass(frozen=True)
class AccountPolicy:
    """Negative-balance policy for one account."""

    account: str
    allow_negative: bool = False
    credit_limit_kg_by_species: Mapping[str, float] = field(default_factory=dict)
    scope: str = DEFAULT_SCOPE
    terminal: bool = False

    def __post_init__(self) -> None:
        account = str(self.account).strip()
        if not account:
            raise AccountingError("account policy requires account")
        scope = str(self.scope).strip() or DEFAULT_SCOPE
        if scope not in POLICY_SCOPES:
            raise AccountingError(f"unsupported account policy scope {scope!r}")

        limits: dict[str, float] = {}
        for species, limit in dict(self.credit_limit_kg_by_species).items():
            name = str(species).strip()
            value = float(limit)
            if not name:
                raise AccountingError("credit limit species is required")
            if not math.isfinite(value) or value < 0.0:
                raise AccountingError(f"credit limit for {name!r} must be finite and non-negative")
            limits[name] = value

        allow_negative = bool(self.allow_negative)
        if allow_negative and not account.startswith("reservoir."):
            raise AccountingError("only reservoir.* accounts may allow negative balances")
        if limits and not allow_negative:
            raise AccountingError("credit limits require allow_negative=True")

        object.__setattr__(self, "account", account)
        object.__setattr__(self, "allow_negative", allow_negative)
        object.__setattr__(self, "credit_limit_kg_by_species", MappingProxyType(dict(sorted(limits.items()))))
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "terminal", bool(self.terminal) or _is_terminal_account(account))

    @classmethod
    def normal(
        cls,
        account: str,
        *,
        scope: str = DEFAULT_SCOPE,
        terminal: bool | None = None,
    ) -> "AccountPolicy":
        return cls(
            account=account,
            allow_negative=False,
            scope=scope,
            terminal=_is_terminal_account(account) if terminal is None else terminal,
        )

    @classmethod
    def reservoir(
        cls,
        account: str,
        credit_limit_kg_by_species: Mapping[str, float] | None = None,
        *,
        scope: str = DEFAULT_SCOPE,
    ) -> "AccountPolicy":
        return cls(
            account=account,
            allow_negative=True,
            credit_limit_kg_by_species=credit_limit_kg_by_species or {},
            scope=scope,
        )

    @property
    def allows_negative(self) -> bool:
        return self.allow_negative


@dataclass(frozen=True)
class LedgerTransition:
    """Balanced debit and credit lots for one ledger event."""

    name: str
    debits: tuple[MaterialLot, ...]
    credits: tuple[MaterialLot, ...]
    reason: str = ""

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise AccountingError("transition name is required")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "debits", _coerce_lots(self.debits))
        object.__setattr__(self, "credits", _coerce_lots(self.credits))
        object.__setattr__(self, "reason", str(self.reason or ""))

    @classmethod
    def move(
        cls,
        name: str,
        debit_account: str,
        credit_account: str,
        species_kg: Mapping[str, float],
        *,
        credit_species_kg: Mapping[str, float] | None = None,
        reason: str = "",
        source: str = "",
    ) -> "LedgerTransition":
        return cls(
            name=name,
            debits=(MaterialLot(debit_account, species_kg, source=source),),
            credits=(MaterialLot(credit_account, credit_species_kg or species_kg, source=source),),
            reason=reason,
        )

    def debit_mass_kg(self, registry: Mapping[str, Any] | None = None) -> float:
        return sum(lot.total_mass_kg(registry) for lot in self.debits)

    def credit_mass_kg(self, registry: Mapping[str, Any] | None = None) -> float:
        return sum(lot.total_mass_kg(registry) for lot in self.credits)

    def debit_atom_moles(self, registry: Mapping[str, Any] | None = None) -> dict[str, float]:
        return _sum_lot_atom_moles(self.debits, registry)

    def credit_atom_moles(self, registry: Mapping[str, Any] | None = None) -> dict[str, float]:
        return _sum_lot_atom_moles(self.credits, registry)

    def validate_conservation(
        self,
        registry: Mapping[str, Any] | None = None,
        *,
        mass_tolerance_kg: float = DEFAULT_MASS_TOLERANCE_KG,
        atom_tolerance_mol: float = DEFAULT_ATOM_TOLERANCE_MOL,
        relative_tolerance: float = DEFAULT_RELATIVE_TOLERANCE,
    ) -> None:
        debit_mass = self.debit_mass_kg(registry)
        credit_mass = self.credit_mass_kg(registry)
        if not _close_enough(debit_mass, credit_mass, mass_tolerance_kg, relative_tolerance):
            raise UnbalancedTransitionError(
                f"transition {self.name!r} does not conserve mass: "
                f"debit={debit_mass:.12g} kg credit={credit_mass:.12g} kg"
            )

        debit_atoms = self.debit_atom_moles(registry)
        credit_atoms = self.credit_atom_moles(registry)
        bad: dict[str, float] = {}
        for element in set(debit_atoms) | set(credit_atoms):
            debit = debit_atoms.get(element, 0.0)
            credit = credit_atoms.get(element, 0.0)
            tolerance_mol = _atom_tolerance_for_element(
                element,
                atom_tolerance_mol,
                mass_tolerance_kg,
            )
            if not _close_enough(debit, credit, tolerance_mol, relative_tolerance):
                bad[element] = credit - debit

        if bad:
            details = ", ".join(f"{element}={diff:.12g} mol" for element, diff in sorted(bad.items()))
            raise UnbalancedTransitionError(
                f"transition {self.name!r} does not conserve atoms: {details}"
            )


class AtomLedger:
    """Mutable account ledger with atom-conserving transitions."""

    def __init__(
        self,
        registry: Mapping[str, Any] | None = None,
        *,
        account_policies: Mapping[str, AccountPolicy | Mapping[str, Any] | str] | Iterable[AccountPolicy] | None = None,
        initial_balances: Mapping[str, Mapping[str, float]] | None = None,
        mass_tolerance_kg: float = DEFAULT_MASS_TOLERANCE_KG,
        atom_tolerance_mol: float = DEFAULT_ATOM_TOLERANCE_MOL,
        relative_tolerance: float = DEFAULT_RELATIVE_TOLERANCE,
    ) -> None:
        self.registry = dict(registry or {})
        self.mass_tolerance_kg = float(mass_tolerance_kg)
        self.atom_tolerance_mol = float(atom_tolerance_mol)
        self.relative_tolerance = float(relative_tolerance)
        self.balance_tolerance_kg = DEFAULT_BALANCE_TOLERANCE_KG
        # Canonical balances are species mol. Public kg accessors are
        # projections at the simulator boundary.
        self._balances: dict[str, dict[str, float]] = {}
        self._policies: dict[str, AccountPolicy] = {}
        self._transitions: list[LedgerTransition] = []
        self._external_loads: list[MaterialLot] = []

        self._load_account_policies(account_policies)
        for account, species_kg in dict(initial_balances or {}).items():
            cleaned_kg = _clean_species_kg(species_kg, self.balance_tolerance_kg)
            self._balances[str(account)] = _species_kg_to_mol(
                cleaned_kg,
                self.registry,
                tolerance_kg=self.balance_tolerance_kg,
            )
        self.assert_balanced()

    @property
    def transitions(self) -> tuple[LedgerTransition, ...]:
        return tuple(self._transitions)

    @property
    def external_loads(self) -> tuple[MaterialLot, ...]:
        return tuple(self._external_loads)

    def debit(
        self,
        account: str,
        species_kg: Mapping[str, float],
        *,
        source: str = "",
        meta: Mapping[str, Any] | None = None,
    ) -> MaterialLot:
        return MaterialLot(account, species_kg, source=source, meta=meta or {})

    def credit(
        self,
        account: str,
        species_kg: Mapping[str, float],
        *,
        source: str = "",
        meta: Mapping[str, Any] | None = None,
    ) -> MaterialLot:
        return MaterialLot(account, species_kg, source=source, meta=meta or {})

    def debit_mol(
        self,
        account: str,
        species_mol: Mapping[str, float],
        *,
        source: str = "",
        meta: Mapping[str, Any] | None = None,
    ) -> MaterialLot:
        payload = _species_mol_to_kg(
            species_mol,
            self.registry,
            tolerance_kg=self.balance_tolerance_kg,
        )
        lot_meta = dict(meta or {})
        lot_meta.setdefault("amount_basis", "mol")
        lot_meta["species_mol"] = dict(_clean_species_mol(
            species_mol,
            self.registry,
            tolerance_kg=self.balance_tolerance_kg,
        ))
        return MaterialLot(account, payload, source=source, meta=lot_meta)

    def credit_mol(
        self,
        account: str,
        species_mol: Mapping[str, float],
        *,
        source: str = "",
        meta: Mapping[str, Any] | None = None,
    ) -> MaterialLot:
        payload = _species_mol_to_kg(
            species_mol,
            self.registry,
            tolerance_kg=self.balance_tolerance_kg,
        )
        lot_meta = dict(meta or {})
        lot_meta.setdefault("amount_basis", "mol")
        lot_meta["species_mol"] = dict(_clean_species_mol(
            species_mol,
            self.registry,
            tolerance_kg=self.balance_tolerance_kg,
        ))
        return MaterialLot(account, payload, source=source, meta=lot_meta)

    def load_external(
        self,
        account: str,
        species_kg: Mapping[str, float],
        source: str = "",
    ) -> MaterialLot:
        lot = MaterialLot(account, species_kg, source=source)
        lot.total_mass_kg(self.registry)
        projected = _copy_balances(self._balances)
        _apply_lot(
            projected,
            lot,
            sign=1.0,
            tolerance_kg=self.balance_tolerance_kg,
            registry=self.registry,
        )
        self._validate_account_policies(projected)
        self._balances = projected
        self._external_loads.append(lot)
        return lot

    def load_external_mol(
        self,
        account: str,
        species_mol: Mapping[str, float],
        source: str = "",
    ) -> MaterialLot:
        lot = self.credit_mol(account, species_mol, source=source)
        lot.total_mass_kg(self.registry)
        projected = _copy_balances(self._balances)
        _apply_lot(
            projected,
            lot,
            sign=1.0,
            tolerance_kg=self.balance_tolerance_kg,
            registry=self.registry,
        )
        self._validate_account_policies(projected)
        self._balances = projected
        self._external_loads.append(lot)
        return lot

    def transfer(
        self,
        name: str,
        debits: Iterable[MaterialLot],
        credits: Iterable[MaterialLot],
        reason: str = "",
    ) -> LedgerTransition:
        transition = LedgerTransition(name=name, debits=tuple(debits), credits=tuple(credits), reason=reason)
        return self.apply(transition)

    def apply(self, transition: LedgerTransition) -> LedgerTransition:
        self._validate_terminal_debits(transition)
        transition.validate_conservation(
            self.registry,
            mass_tolerance_kg=self.mass_tolerance_kg,
            atom_tolerance_mol=self.atom_tolerance_mol,
            relative_tolerance=self.relative_tolerance,
        )
        projected = self.project(transition)
        self._validate_account_policies(projected)
        self._balances = projected
        self._transitions.append(transition)
        return transition

    def move(
        self,
        name: str,
        debit_account: str,
        credit_account: str,
        species_kg: Mapping[str, float],
        *,
        credit_species_kg: Mapping[str, float] | None = None,
        reason: str = "",
        source: str = "",
    ) -> LedgerTransition:
        transition = LedgerTransition.move(
            name,
            debit_account,
            credit_account,
            species_kg,
            credit_species_kg=credit_species_kg,
            reason=reason,
            source=source,
        )
        return self.apply(transition)

    def record(
        self,
        name: str,
        *,
        debits: Iterable[MaterialLot],
        credits: Iterable[MaterialLot],
        reason: str = "",
    ) -> LedgerTransition:
        return self.transfer(name, debits, credits, reason=reason)

    def project(self, transition: LedgerTransition) -> dict[str, dict[str, float]]:
        balances = _copy_balances(self._balances)
        for lot in transition.debits:
            _apply_lot(
                balances,
                lot,
                sign=-1.0,
                tolerance_kg=self.balance_tolerance_kg,
                registry=self.registry,
            )
        for lot in transition.credits:
            _apply_lot(
                balances,
                lot,
                sign=1.0,
                tolerance_kg=self.balance_tolerance_kg,
                registry=self.registry,
            )
        return balances

    def set_account_policy(
        self,
        account: str,
        policy: AccountPolicy | Mapping[str, Any] | str | None = None,
    ) -> None:
        self._policies[str(account)] = _coerce_account_policy(account, policy)
        self.assert_balanced()

    def account_policy(self, account: str) -> AccountPolicy:
        name = str(account)
        return self._policies.get(name, AccountPolicy.normal(name))

    def kg_by_account(self, account: str | None = None) -> dict[str, dict[str, float]] | dict[str, float]:
        if account is not None:
            return _species_mol_to_kg(
                self._balances.get(str(account), {}),
                self.registry,
                tolerance_kg=self.balance_tolerance_kg,
            )
        return {
            name: _species_mol_to_kg(
                species,
                self.registry,
                tolerance_kg=self.balance_tolerance_kg,
            )
            for name, species in sorted(self._balances.items())
        }

    def mol_by_account(self, account: str | None = None) -> dict[str, dict[str, float]] | dict[str, float]:
        if account is not None:
            return dict(self._balances.get(str(account), {}))
        return _copy_balances(self._balances)

    def total_kg_by_account(self, account: str | None = None) -> dict[str, float] | float:
        if account is not None:
            return sum(self.kg_by_account(str(account)).values())
        return {
            name: sum(species_kg.values())
            for name, species_kg in self.kg_by_account().items()
        }

    def total_mol_by_account(self, account: str | None = None) -> dict[str, float] | float:
        if account is not None:
            return sum(self._balances.get(str(account), {}).values())
        return {name: sum(species.values()) for name, species in sorted(self._balances.items())}

    def kg_by_species(self, account: str | None = None) -> dict[str, float]:
        if account is not None:
            return self.kg_by_account(str(account))
        totals: defaultdict[str, float] = defaultdict(float)
        for species_kg in self.kg_by_account().values():
            for species, kg in species_kg.items():
                totals[species] += kg
        return dict(sorted((species, kg) for species, kg in totals.items() if abs(kg) > self.balance_tolerance_kg))

    def mol_by_species(self, account: str | None = None) -> dict[str, float]:
        if account is not None:
            return dict(self._balances.get(str(account), {}))
        totals: defaultdict[str, float] = defaultdict(float)
        for species_mol in self._balances.values():
            for species, mol in species_mol.items():
                totals[species] += mol
        return dict(sorted((species, mol) for species, mol in totals.items() if mol != 0.0))

    def atom_moles_by_account(self, account: str) -> dict[str, float]:
        return _signed_atom_moles_from_species_mol(
            self._balances.get(str(account), {}), self.registry)

    def reservoir_balances(self) -> dict[str, dict[str, Any]]:
        reservoir_accounts = set(self._policies) | set(self._balances)
        report: dict[str, dict[str, Any]] = {}
        for account in sorted(name for name in reservoir_accounts if name.startswith("reservoir.")):
            policy = self.account_policy(account)
            species_mol = dict(self._balances.get(account, {}))
            species_kg = _species_mol_to_kg(
                species_mol,
                self.registry,
                tolerance_kg=self.balance_tolerance_kg,
            )
            remaining = {
                species: limit + species_kg.get(species, 0.0)
                for species, limit in policy.credit_limit_kg_by_species.items()
            }
            report[account] = {
                "allow_negative": policy.allow_negative,
                "scope": policy.scope,
                "kg_by_species": species_kg,
                "mol_by_species": species_mol,
                "credit_limit_kg_by_species": dict(policy.credit_limit_kg_by_species),
                "credit_remaining_kg_by_species": remaining,
                "total_kg": sum(species_kg.values()),
            }
            for species, kg in species_kg.items():
                report[account][species] = kg
        return report

    def close_report(self) -> dict[str, Any]:
        self.assert_balanced()
        kg_by_account = self.kg_by_account()
        mol_by_account = self.mol_by_account()
        total_kg_by_account = self.total_kg_by_account()
        kg_by_species = self.kg_by_species()
        account_species = {account: self.kg_by_species(account) for account in sorted(self._balances)}
        terminal_accounts = {
            account: self.kg_by_species(account)
            for account in sorted(self._balances)
            if self.account_policy(account).terminal
        }
        atom_moles_by_account = {
            account: self.atom_moles_by_account(account) for account in sorted(self._balances)
        }
        return {
            "balanced": True,
            "transition_count": len(self._transitions),
            "external_load_count": len(self._external_loads),
            "kg_by_account": kg_by_account,
            "mol_by_account": mol_by_account,
            "total_kg_by_account": total_kg_by_account,
            "kg_by_species": kg_by_species,
            "account_species_kg": account_species,
            "atom_moles_by_account": atom_moles_by_account,
            "reservoir_balances": self.reservoir_balances(),
            "terminal_accounts": terminal_accounts,
            "external_loads": [
                {"account": lot.account, "species_kg": dict(lot.species_kg), "source": lot.source}
                for lot in self._external_loads
            ],
            "transitions": [
                {
                    "name": transition.name,
                    "reason": transition.reason,
                    "debits": [_lot_report(lot) for lot in transition.debits],
                    "credits": [_lot_report(lot) for lot in transition.credits],
                }
                for transition in self._transitions
            ],
        }

    def assert_balanced(self) -> bool:
        for transition in self._transitions:
            self._validate_terminal_debits(transition)
            transition.validate_conservation(
                self.registry,
                mass_tolerance_kg=self.mass_tolerance_kg,
                atom_tolerance_mol=self.atom_tolerance_mol,
                relative_tolerance=self.relative_tolerance,
            )
        self._validate_account_policies()
        return True

    def account_species_kg(self, account: str | None = None) -> dict[str, dict[str, float]] | dict[str, float]:
        if account is not None:
            return self.kg_by_species(account)
        return self.kg_by_account()

    def account_species_mol(self, account: str | None = None) -> dict[str, dict[str, float]] | dict[str, float]:
        if account is not None:
            return self.mol_by_species(account)
        return self.mol_by_account()

    def account_kg(self, account: str | None = None) -> dict[str, float] | float:
        return self.total_kg_by_account(account)

    def account_atom_moles(self, account: str) -> dict[str, float]:
        return self.atom_moles_by_account(account)

    def _load_account_policies(
        self,
        account_policies: Mapping[str, AccountPolicy | Mapping[str, Any] | str] | Iterable[AccountPolicy] | None,
    ) -> None:
        if account_policies is None:
            return
        if isinstance(account_policies, Mapping):
            for account, policy in account_policies.items():
                self._policies[str(account)] = _coerce_account_policy(str(account), policy)
            return
        for policy in account_policies:
            if not isinstance(policy, AccountPolicy):
                raise AccountingError("account policy iterables must contain AccountPolicy objects")
            self._policies[policy.account] = policy

    def _validate_account_policies(
        self, balances: Mapping[str, Mapping[str, float]] | None = None
    ) -> None:
        checked = balances if balances is not None else self._balances
        for account, species_mol in checked.items():
            policy = self.account_policy(account)
            species_kg = _species_mol_to_kg(
                species_mol,
                self.registry,
                tolerance_kg=self.balance_tolerance_kg,
            )
            allowed_species = TERMINAL_ACCOUNT_ALLOWED_SPECIES.get(account)
            for species, kg in species_kg.items():
                resolve_species_formula(species, self.registry)
                if allowed_species is not None and species not in allowed_species:
                    allowed = ", ".join(sorted(allowed_species))
                    raise AccountingError(
                        f"account {account!r} only accepts species: {allowed}; "
                        f"got {species!r}"
                    )
                if kg >= -self.balance_tolerance_kg:
                    continue
                if not policy.allow_negative:
                    raise OverdraftError(
                        f"insufficient available {species!r} in normal account {account!r}: "
                        f"balance would be {kg:.12g} kg"
                    )
                limit = policy.credit_limit_kg_by_species.get(species)
                if limit is None:
                    raise OverdraftError(
                        f"reservoir account {account!r} has no credit limit for {species!r}"
                    )
                if kg < -limit - self.balance_tolerance_kg:
                    raise OverdraftError(
                        f"reservoir account {account!r} exceeded {species!r} credit: "
                        f"balance={kg:.12g} kg limit={limit:.12g} kg"
                    )

    def _validate_terminal_debits(self, transition: LedgerTransition) -> None:
        credit_accounts = {lot.account for lot in transition.credits}
        for lot in transition.debits:
            if not self.account_policy(lot.account).terminal:
                continue
            allowed_accounts = {
                credit
                for debit, credit in TERMINAL_DEBIT_EXCEPTIONS
                if debit == lot.account
            }
            if credit_accounts and credit_accounts <= allowed_accounts:
                allowed_species = set()
                for credit_account in credit_accounts:
                    allowed_species.update(
                        TERMINAL_DEBIT_EXCEPTIONS.get(
                            (lot.account, credit_account), frozenset()
                        )
                    )
                disallowed_species = set(lot.species_kg) - allowed_species
                if disallowed_species:
                    allowed = ", ".join(sorted(allowed_species)) or "no species"
                    got = ", ".join(sorted(disallowed_species))
                    raise AccountingError(
                        f"terminal account {lot.account!r} cannot debit "
                        f"{got} in transition {transition.name!r}; "
                        f"allowed species: {allowed}"
                    )
                continue
            allowed = ", ".join(sorted(allowed_accounts)) or "no accounts"
            raise AccountingError(
                f"terminal account {lot.account!r} cannot be debited by "
                f"transition {transition.name!r}; allowed destination: {allowed}"
            )


def _coerce_lots(lots: Iterable[MaterialLot]) -> tuple[MaterialLot, ...]:
    coerced: list[MaterialLot] = []
    for lot in lots:
        if not isinstance(lot, MaterialLot):
            raise AccountingError("transition lots must be MaterialLot instances")
        cleaned = lot.without_empty()
        if cleaned.species_kg:
            coerced.append(cleaned)
    return tuple(coerced)


def _coerce_account_policy(
    account: str,
    policy: AccountPolicy | Mapping[str, Any] | str | None,
) -> AccountPolicy:
    if policy is None:
        return AccountPolicy.normal(account)
    if isinstance(policy, AccountPolicy):
        if policy.account != str(account):
            raise AccountingError(
                f"policy account {policy.account!r} does not match key {str(account)!r}"
            )
        return policy
    if isinstance(policy, str):
        if policy.lower() == "normal":
            return AccountPolicy.normal(account)
        if policy.lower() == "reservoir":
            return AccountPolicy.reservoir(account)
        raise AccountingError(f"unknown account policy {policy!r}")
    if isinstance(policy, Mapping):
        data = dict(policy)
        data.setdefault("account", str(account))
        return AccountPolicy(**data)
    raise AccountingError("account policy must be AccountPolicy, mapping, string, or None")


def _sum_lot_atom_moles(
    lots: Iterable[MaterialLot], registry: Mapping[str, Any] | None
) -> dict[str, float]:
    atoms: defaultdict[str, float] = defaultdict(float)
    for lot in lots:
        for element, moles in lot.atom_moles(registry).items():
            atoms[element] += moles
    return dict(sorted(atoms.items()))


def _signed_atom_moles(
    species_kg: Mapping[str, float], registry: Mapping[str, Any] | None
) -> dict[str, float]:
    atoms: defaultdict[str, float] = defaultdict(float)
    for species, kg in species_kg.items():
        formula = resolve_species_formula(species, registry)
        species_moles = kg / formula.molar_mass_kg_per_mol()
        for element, count in formula.elements.items():
            atoms[element] += species_moles * count
    return dict(sorted((element, value) for element, value in atoms.items() if value != 0.0))


def _signed_atom_moles_from_species_mol(
    species_mol: Mapping[str, float], registry: Mapping[str, Any] | None
) -> dict[str, float]:
    atoms: defaultdict[str, float] = defaultdict(float)
    for species, mol in species_mol.items():
        formula = resolve_species_formula(species, registry)
        for element, count in formula.elements.items():
            atoms[element] += float(mol) * count
    return dict(sorted((element, value) for element, value in atoms.items() if value != 0.0))


def _copy_balances(balances: Mapping[str, Mapping[str, float]]) -> dict[str, dict[str, float]]:
    return {account: dict(species) for account, species in balances.items()}


def _apply_lot(
    balances: dict[str, dict[str, float]],
    lot: MaterialLot,
    *,
    sign: float,
    tolerance_kg: float,
    registry: Mapping[str, Any] | None,
) -> None:
    account_balances = balances.setdefault(lot.account, {})
    for species, mol in lot.species_moles_for(registry).items():
        value = account_balances.get(species, 0.0) + sign * mol
        if _species_mol_abs_kg(species, value, registry) <= tolerance_kg:
            value = 0.0
        account_balances[species] = value
    balances[lot.account] = {
        species: mol
        for species, mol in sorted(account_balances.items())
        if _species_mol_abs_kg(species, mol, registry) > tolerance_kg
    }


def _clean_species_kg(species_kg: Mapping[str, float], tolerance_kg: float) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for species, kg in species_kg.items():
        value = float(kg)
        if not math.isfinite(value):
            raise AccountingError(f"balance for species {species!r} must be finite")
        if abs(value) > tolerance_kg:
            cleaned[str(species)] = value
    return dict(sorted(cleaned.items()))


def _clean_species_mol(
    species_mol: Mapping[str, float],
    registry: Mapping[str, Any] | None,
    *,
    tolerance_kg: float,
) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for species, mol in species_mol.items():
        name = str(species)
        value = float(mol)
        if not math.isfinite(value):
            raise AccountingError(f"balance for species {name!r} must be finite")
        if _species_mol_abs_kg(name, value, registry) > tolerance_kg:
            cleaned[name] = value
    return dict(sorted(cleaned.items()))


def _species_kg_to_mol(
    species_kg: Mapping[str, float],
    registry: Mapping[str, Any] | None,
    *,
    tolerance_kg: float,
) -> dict[str, float]:
    converted: dict[str, float] = {}
    for species, kg in species_kg.items():
        name = str(species)
        value = float(kg)
        if not math.isfinite(value):
            raise AccountingError(f"balance for species {name!r} must be finite")
        if abs(value) <= tolerance_kg:
            continue
        formula = resolve_species_formula(name, registry)
        converted[name] = value / formula.molar_mass_kg_per_mol()
    return dict(sorted(converted.items()))


def _species_mol_to_kg(
    species_mol: Mapping[str, float],
    registry: Mapping[str, Any] | None,
    *,
    tolerance_kg: float = 0.0,
) -> dict[str, float]:
    converted: dict[str, float] = {}
    for species, mol in species_mol.items():
        name = str(species)
        value = float(mol)
        if not math.isfinite(value):
            raise AccountingError(f"balance for species {name!r} must be finite")
        kg = _species_mol_to_kg_value(name, value, registry)
        if abs(kg) > tolerance_kg:
            converted[name] = kg
    return dict(sorted(converted.items()))


def _species_mol_abs_kg(
    species: str, species_mol: float, registry: Mapping[str, Any] | None
) -> float:
    return abs(_species_mol_to_kg_value(species, species_mol, registry))


def _species_mol_to_kg_value(
    species: str, species_mol: float, registry: Mapping[str, Any] | None
) -> float:
    formula = resolve_species_formula(str(species), registry)
    return float(species_mol) * formula.molar_mass_kg_per_mol()


def _lot_report(lot: MaterialLot) -> dict[str, Any]:
    return {
        "account": lot.account,
        "species_kg": dict(lot.species_kg),
        "species_mol": dict(lot.meta.get("species_mol", {})),
        "source": lot.source,
        "meta": dict(lot.meta),
    }


def _is_terminal_account(account: str) -> bool:
    return str(account).startswith("terminal.") or str(account) == "vent"


def _close_enough(left: float, right: float, absolute: float, relative: float) -> bool:
    return abs(left - right) <= max(float(absolute), float(relative) * max(abs(left), abs(right), 1.0))


def _atom_tolerance_for_element(
    element: str, configured_mol: float, mass_tolerance_kg: float
) -> float:
    configured = max(0.0, float(configured_mol))
    atomic_weight_g_mol = ATOMIC_WEIGHTS_G_PER_MOL.get(str(element))
    if atomic_weight_g_mol is None or atomic_weight_g_mol <= 0.0:
        return configured
    mass_limited_mol = float(mass_tolerance_kg) / (atomic_weight_g_mol / 1000.0)
    return min(configured, mass_limited_mol)
