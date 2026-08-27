"""Atom-conserving ledger primitives."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from copy import copy as shallow_copy
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from simulator.accounting.exceptions import (
    AccountingError,
    MaterialOriginError,
    OriginUnresolvedError,
    OverdraftError,
    PoolWithdrawalError,
    UnbalancedTransitionError,
)
from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL, resolve_species_formula
from simulator.accounting.lots import (
    ATTRIBUTION_METHODS,
    MATERIAL_ORIGINS,
    MaterialLot,
    allocate_pool_withdrawal,
)
from simulator.account_ids import (
    CONDENSATION_RETAINED_HOLDUP_ACCOUNT,
    OXYGEN_CISTERN_LIQUID_INVENTORY_ACCOUNT,
    SOLID_CHAR_CARBON_ACCOUNT,
)

# Per-transition mass-closure slack. The element gate below is the binding
# conservation check for known formulas; this mass gate gives unknown-species
# or formula-registry failures a readable first diagnostic. It bounds ONE
# transition only -- cumulative drift across a many-hundred-transition batch
# is guarded separately by
# tests/test_mass_balance.py::test_cumulative_transition_mass_closure_bounded,
# which sums abs(debit-credit) over a full C0->C6 run against a tight bound.
DEFAULT_MASS_TOLERANCE_KG = 2e-2
DEFAULT_ATOM_TOLERANCE_MOL = 1e-6
DEFAULT_RELATIVE_TOLERANCE = 1e-9
DEFAULT_BALANCE_TOLERANCE_KG = 1e-12
DEFAULT_BALANCE_RELATIVE_TOLERANCE = 1e-12
DEFAULT_BALANCE_ABSOLUTE_FLOOR_KG = 1e-15
DEFAULT_SCOPE = "batch"
POLICY_SCOPES = {"batch", "campaign", "external"}
TERMINAL_DEBIT_EXCEPTIONS = {
    (
        "terminal.oxygen_melt_offgas_stored",
        "terminal.oxygen_melt_offgas_vented_to_vacuum",
    ): frozenset({"O2"}),
}
C7_TERMINAL_SLAG_DEBIT_PREFIXES = (
    "ca_aluminothermic_c3a_",
    "ca_aluminothermic_c12a7_",
)
_C7_TERMINAL_SLAG_REWORK_CAPABILITY = object()
TERMINAL_ACCOUNT_ALLOWED_SPECIES = {
    OXYGEN_CISTERN_LIQUID_INVENTORY_ACCOUNT: frozenset({"O2"}),
    "terminal.oxygen_stage0_stored": frozenset({"O2"}),
    "terminal.oxygen_melt_offgas_stored": frozenset({"O2"}),
    "terminal.oxygen_melt_offgas_vented_to_vacuum": frozenset({"O2"}),
    "terminal.oxygen_bubbler_external_vented_to_vacuum": frozenset({"O2"}),
    "terminal.oxygen_melt_offgas_captured": frozenset({"O2"}),
    "terminal.oxygen_mre_anode_stored": frozenset({"O2"}),
    "terminal.chromium_condensed_oxide_stored": frozenset({"Cr2O3"}),
}
KNOWN_LEDGER_ACCOUNTS: frozenset[str] = frozenset({
    "process.cleaned_melt",
    "process.c7_al_credit",
    "process.condensation_train",
    CONDENSATION_RETAINED_HOLDUP_ACCOUNT,
    "process.metal_phase",
    "process.metal_phase_bottom_pool",
    "process.metal_phase_float_layer",
    "process.overhead_gas",
    "process.raw_feedstock",
    "process.reagent_inventory",
    "process.spent_reductant_residue",
    SOLID_CHAR_CARBON_ACCOUNT,
    "process.stage0_carbonate_feed",
    "process.stage0_foulant",
    "process.stage0_perchlorate_feed",
    "process.stage0_salt_feed",
    "process.stage0_volatile_feed",
    "process.wall_deposit",
    "reservoir.fo2_buffer",
    OXYGEN_CISTERN_LIQUID_INVENTORY_ACCOUNT,
    "reservoir.stage0_oxidant",
    "reservoir.stage0_process_gas",
    "terminal.slag",
    "terminal.drain_tap_material",
    "terminal.offgas",
    "terminal.chromium_condensed_oxide_stored",
    "terminal.oxygen_stage0_stored",
    "terminal.oxygen_mre_anode_stored",
    "terminal.oxygen_melt_offgas_stored",
    "terminal.oxygen_melt_offgas_captured",
    "terminal.oxygen_melt_offgas_vented_to_vacuum",
    "terminal.oxygen_bubbler_external_vented_to_vacuum",
    "terminal.stage0_chloride_salt_phase",
    "terminal.stage0_residual_carbonate_carbon",
    "terminal.stage0_residual_refractory_carbon",
    "terminal.stage0_salt_phase",
    "terminal.stage0_sulfide_matte",
    "vent",
})
KNOWN_LEDGER_ACCOUNT_PREFIXES: tuple[str, ...] = (
    "process.wall_deposit_segment_",
    "reservoir.reagent.",
)


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
        material_origin: str | None = None,
    ) -> "LedgerTransition":
        return cls(
            name=name,
            debits=(
                MaterialLot(
                    debit_account,
                    species_kg,
                    source=source,
                    material_origin=material_origin,
                ),
            ),
            credits=(
                MaterialLot(
                    credit_account,
                    species_kg if credit_species_kg is None else credit_species_kg,
                    source=source,
                    material_origin=material_origin,
                ),
            ),
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
        allowed_accounts: frozenset[str] | None = None,
        allowed_account_prefixes: tuple[str, ...] = (),
    ) -> None:
        self.registry = dict(registry or {})
        self.mass_tolerance_kg = _finite_nonnegative_tolerance(
            "mass_tolerance_kg", mass_tolerance_kg
        )
        self.atom_tolerance_mol = _finite_nonnegative_tolerance(
            "atom_tolerance_mol", atom_tolerance_mol
        )
        self.relative_tolerance = _finite_nonnegative_tolerance(
            "relative_tolerance", relative_tolerance
        )
        self.balance_tolerance_kg = DEFAULT_BALANCE_TOLERANCE_KG
        self.balance_relative_tolerance = DEFAULT_BALANCE_RELATIVE_TOLERANCE
        self.balance_absolute_floor_kg = DEFAULT_BALANCE_ABSOLUTE_FLOOR_KG
        # Canonical balances are species mol. Public kg accessors are
        # projections at the simulator boundary.
        self._balances: dict[str, dict[str, float]] = {}
        self._movement_scale_kg: dict[str, dict[str, float]] = {}
        self._policies: dict[str, AccountPolicy] = {}
        self._transitions: list[LedgerTransition] = []
        self._terminal_debit_authorized_transition_ids: set[int] = set()
        self._external_loads: list[MaterialLot] = []
        self._origin_atom_moles: dict[str, dict[str, dict[str, float]]] = {}
        self._unresolved_origin_atom_moles: dict[str, dict[str, float]] = {}
        self._origin_attribution_methods: dict[str, dict[str, str]] = {}
        self._external_origin_atom_moles: dict[str, dict[str, float]] = {
            origin: {} for origin in MATERIAL_ORIGINS
        }
        self._gross_account_flows: dict[str, dict[str, Any]] = {
            direction: {
                "species_mol_by_account": {},
                "material_origin_atom_moles_by_account": {},
                "origin_unattributed_atom_moles_by_account": {},
            }
            for direction in ("inputs", "withdrawals")
        }
        self._gross_account_flow_events: list[dict[str, Any]] = []
        self._origin_unattributed_debt_atom_moles_by_account: dict[
            str,
            dict[str, float],
        ] = {}
        self._cumulative_origin_unattributed_atom_moles: dict[str, float] = {}
        self._amalgamated_pool_elements: set[tuple[str, str]] = set()
        self._allowed_accounts = (
            None
            if allowed_accounts is None
            else frozenset(str(account) for account in allowed_accounts)
        )
        self._allowed_account_prefixes = tuple(str(prefix) for prefix in allowed_account_prefixes)

        self._load_account_policies(account_policies)
        for account, species_kg in dict(initial_balances or {}).items():
            cleaned_kg = _clean_species_kg(species_kg, self.balance_tolerance_kg)
            self._balances[str(account)] = _species_kg_to_mol(
                cleaned_kg,
                self.registry,
                tolerance_kg=self.balance_tolerance_kg,
            )
            self._record_movement_scale(
                self._movement_scale_kg,
                str(account),
                cleaned_kg,
            )
        self._initial_balances = _copy_balances(self._balances)
        for account, species_mol in self._balances.items():
            atoms = _element_atom_moles_from_species_mol(
                species_mol,
                self.registry,
            )
            if atoms:
                self._unresolved_origin_atom_moles[str(account)] = atoms
                self._origin_unattributed_debt_atom_moles_by_account[
                    str(account)
                ] = dict(atoms)
                _merge_element_amounts(
                    self._cumulative_origin_unattributed_atom_moles,
                    atoms,
                )
        self.assert_balanced()

    @property
    def transitions(self) -> tuple[LedgerTransition, ...]:
        return tuple(self._transitions)

    @property
    def external_loads(self) -> tuple[MaterialLot, ...]:
        return tuple(self._external_loads)

    def origin_atom_moles_by_account(
        self,
    ) -> dict[str, dict[str, dict[str, float]]]:
        return _copy_origin_balances(self._origin_atom_moles)

    def unresolved_origin_atom_moles_by_account(
        self,
    ) -> dict[str, dict[str, float]]:
        return _copy_balances(self._unresolved_origin_atom_moles)

    def origin_attribution_methods_by_account(
        self,
    ) -> dict[str, dict[str, str]]:
        return {
            account: dict(elements)
            for account, elements in self._origin_attribution_methods.items()
        }

    def external_origin_atom_moles(self) -> dict[str, dict[str, float]]:
        return {
            origin: dict(elements)
            for origin, elements in self._external_origin_atom_moles.items()
        }

    def initial_unattributed_atom_moles(self) -> dict[str, float]:
        result: defaultdict[str, float] = defaultdict(float)
        for species_mol in self._initial_balances.values():
            _merge_element_amounts(
                result,
                _element_atom_moles_from_species_mol(
                    species_mol,
                    self.registry,
                ),
            )
        return dict(result)

    def gross_account_flows(self) -> dict[str, dict[str, Any]]:
        return {
            direction: {
                "species_mol_by_account": _copy_balances(
                    values["species_mol_by_account"]
                ),
                "material_origin_atom_moles_by_account": _copy_origin_balances(
                    values["material_origin_atom_moles_by_account"]
                ),
                "origin_unattributed_atom_moles_by_account": _copy_balances(
                    values["origin_unattributed_atom_moles_by_account"]
                ),
            }
            for direction, values in self._gross_account_flows.items()
        }

    def gross_account_flow_events(self) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(self._gross_account_flow_events))

    def cumulative_origin_unattributed_atom_moles(self) -> dict[str, float]:
        return dict(self._cumulative_origin_unattributed_atom_moles)

    def mark_amalgamated_pool(
        self,
        account: str,
        elements: Iterable[str],
    ) -> None:
        account_name = str(account).strip()
        if not account_name:
            raise AccountingError("amalgamated pool account is required")
        for raw_element in elements:
            element = str(raw_element).strip()
            if not element:
                raise AccountingError("amalgamated pool element is required")
            self._amalgamated_pool_elements.add((account_name, element))
            origin_amounts = self._origin_atom_moles.get(account_name, {}).get(
                element,
                {},
            )
            if sum(value > 0.0 for value in origin_amounts.values()) > 1:
                self._origin_attribution_methods.setdefault(
                    account_name,
                    {},
                )[element] = "pool_ratio"

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
        *,
        material_origin: str | None = None,
    ) -> MaterialLot:
        origin = _require_material_origin(material_origin)
        lot = MaterialLot(
            account,
            species_kg,
            source=source,
            material_origin=origin,
        )
        lot.total_mass_kg(self.registry)
        lot = _lot_with_origin_atoms(
            lot,
            {origin: lot.atom_moles_for(self.registry)},
            {
                element: "tracked"
                for element in lot.atom_moles_for(self.registry)
            },
        )
        projected = _copy_balances(self._balances)
        movement_scale = _copy_balances(self._movement_scale_kg)
        _apply_lot(
            projected,
            lot,
            sign=1.0,
            tolerance_kg=self.balance_tolerance_kg,
            registry=self.registry,
        )
        self._record_lot_movement_scale(movement_scale, lot)
        self._validate_account_policies(projected, movement_scale)
        self._balances = projected
        self._movement_scale_kg = movement_scale
        self._external_loads.append(lot)
        self._credit_origin_lot(lot)
        self._record_gross_lot("inputs", lot)
        _merge_element_amounts(
            self._external_origin_atom_moles[origin],
            lot.atom_moles_for(self.registry),
        )
        return lot

    def load_external_mol(
        self,
        account: str,
        species_mol: Mapping[str, float],
        source: str = "",
        *,
        material_origin: str | None = None,
    ) -> MaterialLot:
        origin = _require_material_origin(material_origin)
        lot = self.credit_mol(account, species_mol, source=source)
        atoms = lot.atom_moles_for(self.registry)
        lot = _lot_with_origin_atoms(
            lot,
            {origin: atoms},
            {element: "tracked" for element in atoms},
            material_origin=origin,
        )
        lot.total_mass_kg(self.registry)
        projected = _copy_balances(self._balances)
        movement_scale = _copy_balances(self._movement_scale_kg)
        _apply_lot(
            projected,
            lot,
            sign=1.0,
            tolerance_kg=self.balance_tolerance_kg,
            registry=self.registry,
        )
        self._record_lot_movement_scale(movement_scale, lot)
        self._validate_account_policies(projected, movement_scale)
        self._balances = projected
        self._movement_scale_kg = movement_scale
        self._external_loads.append(lot)
        self._credit_origin_lot(lot)
        self._record_gross_lot("inputs", lot)
        _merge_element_amounts(
            self._external_origin_atom_moles[origin],
            atoms,
        )
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

    def apply(
        self,
        transition: LedgerTransition,
        *,
        _terminal_debit_capability: object | None = None,
    ) -> LedgerTransition:
        original_transition = transition
        self._validate_terminal_debits(
            transition,
            terminal_debit_capability=_terminal_debit_capability,
        )
        (
            transition,
            projected_origin_atoms,
            projected_unresolved_atoms,
            projected_origin_methods,
            projected_external_origin_atoms,
        ) = self._project_origin_transition(transition)
        transition.validate_conservation(
            self.registry,
            mass_tolerance_kg=self.mass_tolerance_kg,
            atom_tolerance_mol=self.atom_tolerance_mol,
            relative_tolerance=self.relative_tolerance,
        )
        projected = self.project(transition)
        movement_scale = _copy_balances(self._movement_scale_kg)
        for lot in (*transition.debits, *transition.credits):
            self._record_lot_movement_scale(movement_scale, lot)
        self._validate_account_policies(projected, movement_scale)
        _reconcile_origin_projection(
            projected_origin_atoms,
            projected_unresolved_atoms,
            projected_origin_methods,
            projected,
            self.registry,
        )
        (
            projected_unattributed_debt,
            newly_unattributed,
        ) = _project_origin_unattributed_debt(
            transition,
            self._origin_unattributed_debt_atom_moles_by_account,
            self.registry,
        )
        # Preserve the public commit identity only after every validation has
        # succeeded; failed applies must not mutate the caller's transition.
        object.__setattr__(
            original_transition,
            "debits",
            transition.debits,
        )
        object.__setattr__(
            original_transition,
            "credits",
            transition.credits,
        )
        transition = original_transition
        self._balances = projected
        self._movement_scale_kg = movement_scale
        self._origin_atom_moles = projected_origin_atoms
        self._unresolved_origin_atom_moles = projected_unresolved_atoms
        self._origin_attribution_methods = projected_origin_methods
        self._external_origin_atom_moles = projected_external_origin_atoms
        self._origin_unattributed_debt_atom_moles_by_account = (
            projected_unattributed_debt
        )
        for lot in transition.debits:
            self._record_gross_lot("withdrawals", lot)
        for lot in transition.credits:
            self._record_gross_lot("inputs", lot)
        _merge_element_amounts(
            self._cumulative_origin_unattributed_atom_moles,
            newly_unattributed,
        )
        self._transitions.append(transition)
        if _terminal_debit_capability is _C7_TERMINAL_SLAG_REWORK_CAPABILITY:
            self._terminal_debit_authorized_transition_ids.add(id(transition))
        return transition

    def _record_gross_lot(self, direction: str, lot: MaterialLot) -> None:
        flows = self._gross_account_flows[direction]
        account = str(lot.account)
        event_species = lot.species_moles_for(self.registry)
        species = flows["species_mol_by_account"].setdefault(account, {})
        for name, amount in event_species.items():
            species[str(name)] = (
                float(species.get(str(name), 0.0)) + float(amount)
            )

        event_origins: dict[str, dict[str, float]] = {}
        account_origins = flows[
            "material_origin_atom_moles_by_account"
        ].setdefault(account, {})
        for origin, elements in lot.origin_atom_moles.items():
            for element, raw_amount in elements.items():
                amount = float(raw_amount)
                if amount <= 0.0:
                    continue
                account_origins.setdefault(str(element), {})[str(origin)] = (
                    float(
                        account_origins.get(str(element), {}).get(
                            str(origin),
                            0.0,
                        )
                    )
                    + amount
                )
                event_origins.setdefault(str(element), {})[str(origin)] = amount

        event_unattributed = _lot_unattributed_atom_moles(lot, self.registry)
        unattributed = flows[
            "origin_unattributed_atom_moles_by_account"
        ].setdefault(account, {})
        for element, amount in event_unattributed.items():
            unattributed[str(element)] = (
                float(unattributed.get(str(element), 0.0)) + amount
            )
        self._gross_account_flow_events.append(
            {
                "direction": direction,
                "account": account,
                "species_mol": dict(event_species),
                "material_origin_atom_moles": event_origins,
                "origin_unattributed_atom_moles": event_unattributed,
            }
        )

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
        material_origin: str | None = None,
        amalgamated_pool: bool = False,
    ) -> LedgerTransition:
        transition = LedgerTransition.move(
            name,
            debit_account,
            credit_account,
            species_kg,
            credit_species_kg=credit_species_kg,
            reason=reason,
            source=source,
            material_origin=material_origin,
        )
        applied = self.apply(transition)
        if amalgamated_pool:
            elements = applied.credits[0].atom_moles_for(self.registry)
            self.mark_amalgamated_pool(credit_account, elements)
        return applied

    def _credit_origin_lot(self, lot: MaterialLot) -> None:
        account = str(lot.account)
        physical_atoms = lot.atom_moles_for(self.registry)
        credited_by_element: defaultdict[str, float] = defaultdict(float)
        for origin, elements in lot.origin_atom_moles.items():
            for element, amount in elements.items():
                value = float(amount)
                if value <= 0.0:
                    continue
                self._origin_atom_moles.setdefault(account, {}).setdefault(
                    str(element),
                    {},
                )[str(origin)] = (
                    self._origin_atom_moles.get(account, {})
                    .get(str(element), {})
                    .get(str(origin), 0.0)
                    + value
                )
                credited_by_element[str(element)] += value
        for element, physical in physical_atoms.items():
            unresolved = max(
                0.0,
                float(physical) - credited_by_element.get(str(element), 0.0),
            )
            if unresolved > _origin_atom_tolerance(float(physical)):
                self._unresolved_origin_atom_moles.setdefault(account, {})[
                    str(element)
                ] = (
                    self._unresolved_origin_atom_moles.get(account, {}).get(
                        str(element),
                        0.0,
                    )
                    + unresolved
                )
                continue
            method = lot.attribution_method_by_element.get(str(element))
            if method in ATTRIBUTION_METHODS:
                self._origin_attribution_methods.setdefault(account, {})[
                    str(element)
                ] = str(method)

    def _project_origin_transition(
        self,
        transition: LedgerTransition,
    ) -> tuple[
        LedgerTransition,
        dict[str, dict[str, dict[str, float]]],
        dict[str, dict[str, float]],
        dict[str, dict[str, str]],
        dict[str, dict[str, float]],
    ]:
        origin_balances = _copy_origin_balances(self._origin_atom_moles)
        unresolved_balances = _copy_balances(self._unresolved_origin_atom_moles)
        origin_methods = {
            account: dict(elements)
            for account, elements in self._origin_attribution_methods.items()
        }
        external_origins = {
            origin: dict(elements)
            for origin, elements in self._external_origin_atom_moles.items()
        }
        debit_origin_totals: dict[str, defaultdict[str, float]] = {
            origin: defaultdict(float) for origin in MATERIAL_ORIGINS
        }
        debit_unresolved_totals: defaultdict[str, float] = defaultdict(float)
        debit_methods: defaultdict[str, set[str]] = defaultdict(set)
        enriched_debits: list[MaterialLot] = []

        for lot in transition.debits:
            account = str(lot.account)
            physical_atoms = lot.atom_moles_for(self.registry)
            origin_allocation: dict[str, dict[str, float]] = {
                origin: {} for origin in MATERIAL_ORIGINS
            }
            unresolved_allocation: dict[str, float] = {}
            methods: dict[str, str] = {}
            explicit = bool(lot.origin_atom_moles) or lot.material_origin is not None

            if lot.origin_atom_moles:
                for origin, elements in lot.origin_atom_moles.items():
                    for element, amount in elements.items():
                        origin_allocation[str(origin)][str(element)] = float(amount)
            elif lot.material_origin is not None:
                origin_allocation[str(lot.material_origin)] = {
                    str(element): float(amount)
                    for element, amount in physical_atoms.items()
                }

            for element, physical_amount in physical_atoms.items():
                element = str(element)
                amount = float(physical_amount)
                tolerance = _origin_atom_tolerance(amount)
                if explicit:
                    allocated = math.fsum(
                        origin_allocation[origin].get(element, 0.0)
                        for origin in MATERIAL_ORIGINS
                    )
                    if not _close_enough(
                        allocated,
                        amount,
                        tolerance,
                        self.relative_tolerance,
                    ):
                        raise OriginUnresolvedError(
                            f"typed debit origin does not cover "
                            f"{account}.{element}: origin={allocated:.12g}, "
                            f"physical={amount:.12g} mol-atoms"
                        )
                    method = lot.attribution_method_by_element.get(
                        element,
                        "tracked",
                    )
                else:
                    account_origins = origin_balances.get(account, {}).get(
                        element,
                        {},
                    )
                    account_unresolved = max(
                        0.0,
                        float(
                            unresolved_balances.get(account, {}).get(
                                element,
                                0.0,
                            )
                        ),
                    )
                    available = math.fsum(account_origins.values()) + account_unresolved
                    withdrawal_tolerance = max(
                        tolerance,
                        _atom_tolerance_for_element(
                            element,
                            self.atom_tolerance_mol,
                            self.mass_tolerance_kg,
                        ),
                        available * self.balance_relative_tolerance,
                    )
                    live_origins = sum(
                        float(account_origins.get(origin, 0.0)) > tolerance
                        for origin in MATERIAL_ORIGINS
                    )
                    pool_ratio = (
                        (account, element) in self._amalgamated_pool_elements
                        and live_origins > 1
                    )
                    if available <= tolerance:
                        unresolved_allocation[element] = amount
                    else:
                        full_withdrawal = _close_enough(
                            amount,
                            available,
                            withdrawal_tolerance,
                            self.relative_tolerance,
                        )
                        if live_origins > 1 and not pool_ratio and not full_withdrawal:
                            raise OriginUnresolvedError(
                                f"partial withdrawal from mixed-origin "
                                f"{account}.{element} requires an explicitly "
                                f"amalgamated pool"
                            )
                        origin_unattributed_shortfall = (
                            max(0.0, amount - available)
                            if full_withdrawal
                            else 0.0
                        )
                        try:
                            pool_withdrawal = allocate_pool_withdrawal(
                                {
                                    **{
                                        origin: account_origins.get(origin, 0.0)
                                        for origin in MATERIAL_ORIGINS
                                    },
                                    "unresolved": account_unresolved,
                                },
                                amount - origin_unattributed_shortfall,
                                absolute_tolerance=withdrawal_tolerance,
                            )
                        except PoolWithdrawalError as exc:
                            raise OverdraftError(
                                f"account {account!r} overdrawn for {element}: "
                                f"debit={amount:.12g}, "
                                f"available={available:.12g} mol-atoms"
                            ) from exc
                        for origin in MATERIAL_ORIGINS:
                            origin_amount = float(pool_withdrawal.get(origin, 0.0))
                            if origin_amount > 0.0:
                                origin_allocation[origin][element] = origin_amount
                        unresolved_allocation[element] = (
                            float(pool_withdrawal.get("unresolved", 0.0))
                            + origin_unattributed_shortfall
                        )
                        allocated = (
                            math.fsum(
                                origin_allocation[origin].get(element, 0.0)
                                for origin in MATERIAL_ORIGINS
                            )
                            + unresolved_allocation[element]
                        )
                        if allocated < amount - tolerance:
                            unresolved_allocation[element] += amount - allocated
                    method = (
                        "pool_ratio"
                        if pool_ratio
                        else origin_methods.get(account, {}).get(
                            element,
                            "tracked",
                        )
                    )

                methods[element] = str(method)
                debit_methods[element].add(str(method))
                for origin in MATERIAL_ORIGINS:
                    debited = max(
                        0.0,
                        float(origin_allocation[origin].get(element, 0.0)),
                    )
                    if debited <= 0.0:
                        continue
                    available_origin = max(
                        0.0,
                        float(
                            origin_balances.get(account, {})
                            .get(element, {})
                            .get(origin, 0.0)
                        ),
                    )
                    deficit = max(0.0, debited - available_origin)
                    if deficit > tolerance:
                        if not explicit or not self.account_policy(account).allows_negative:
                            raise OriginUnresolvedError(
                                f"typed debit origin unavailable for "
                                f"{account}.{element}: origin={origin}, "
                                f"debit={debited:.12g}, "
                                f"available={available_origin:.12g} mol-atoms"
                            )
                        external_origins.setdefault(origin, {})[element] = (
                            external_origins.get(origin, {}).get(element, 0.0)
                            + deficit
                        )
                    remaining = max(0.0, available_origin - debited)
                    _set_origin_balance(
                        origin_balances,
                        account,
                        element,
                        origin,
                        remaining,
                        0.0,
                    )
                    debit_origin_totals[origin][element] += debited

                unresolved_debit = max(
                    0.0,
                    float(unresolved_allocation.get(element, 0.0)),
                )
                if unresolved_debit > 0.0:
                    available_unresolved = max(
                        0.0,
                        float(
                            unresolved_balances.get(account, {}).get(
                                element,
                                0.0,
                            )
                        ),
                    )
                    remaining_unresolved = max(
                        0.0,
                        available_unresolved - unresolved_debit,
                    )
                    _set_element_balance(
                        unresolved_balances,
                        account,
                        element,
                        remaining_unresolved,
                        0.0,
                    )
                    debit_unresolved_totals[element] += unresolved_debit
                _clean_origin_method_if_empty(
                    origin_methods,
                    origin_balances,
                    unresolved_balances,
                    account,
                    element,
                    0.0,
                )

            compact_allocation = {
                origin: elements
                for origin, elements in origin_allocation.items()
                if elements
            }
            enriched_debits.append(
                _lot_with_origin_atoms(
                    lot,
                    compact_allocation,
                    {
                        element: method
                        for element, method in methods.items()
                        if unresolved_allocation.get(element, 0.0)
                        <= _origin_atom_tolerance(
                            float(physical_atoms.get(element, 0.0))
                        )
                    },
                    material_origin=_uniform_material_origin(
                        physical_atoms,
                        compact_allocation,
                        unresolved_allocation,
                    ),
                )
            )

        credit_atoms = [
            lot.atom_moles_for(self.registry)
            for lot in transition.credits
        ]
        total_credit_atoms: defaultdict[str, float] = defaultdict(float)
        for atoms in credit_atoms:
            _merge_element_amounts(total_credit_atoms, atoms)
        enriched_credits: list[MaterialLot] = []
        for lot, physical_atoms in zip(transition.credits, credit_atoms):
            account = str(lot.account)
            allocation: dict[str, dict[str, float]] = {
                origin: {} for origin in MATERIAL_ORIGINS
            }
            unresolved_allocation: dict[str, float] = {}
            methods: dict[str, str] = {}
            for element, amount in physical_atoms.items():
                element = str(element)
                total_credit = float(total_credit_atoms.get(element, 0.0))
                fraction = float(amount) / total_credit if total_credit > 0.0 else 0.0
                for origin in MATERIAL_ORIGINS:
                    origin_amount = float(
                        debit_origin_totals[origin].get(element, 0.0)
                    ) * fraction
                    if origin_amount > 0.0:
                        allocation[origin][element] = origin_amount
                        current = (
                            origin_balances.setdefault(account, {})
                            .setdefault(element, {})
                            .get(origin, 0.0)
                        )
                        origin_balances[account][element][origin] = (
                            current + origin_amount
                        )
                unresolved_amount = float(
                    debit_unresolved_totals.get(element, 0.0)
                ) * fraction
                if unresolved_amount > 0.0:
                    unresolved_allocation[element] = unresolved_amount
                    unresolved_balances.setdefault(account, {})[element] = (
                        unresolved_balances.get(account, {}).get(element, 0.0)
                        + unresolved_amount
                    )
                tolerance = _origin_atom_tolerance(float(amount))
                if unresolved_amount <= tolerance:
                    method = (
                        "pool_ratio"
                        if "pool_ratio" in debit_methods.get(element, set())
                        else "tracked"
                    )
                    if (
                        (account, element) in self._amalgamated_pool_elements
                        and sum(
                            float(
                                origin_balances.get(account, {})
                                .get(element, {})
                                .get(origin, 0.0)
                            )
                            > tolerance
                            for origin in MATERIAL_ORIGINS
                        )
                        > 1
                    ):
                        method = "pool_ratio"
                    methods[element] = method
                    prior_method = origin_methods.setdefault(account, {}).get(
                        element,
                    )
                    origin_methods[account][element] = (
                        "pool_ratio"
                        if method == "pool_ratio" or prior_method == "pool_ratio"
                        else "tracked"
                    )
            compact_allocation = {
                origin: elements
                for origin, elements in allocation.items()
                if elements
            }
            enriched_credits.append(
                _lot_with_origin_atoms(
                    lot,
                    compact_allocation,
                    methods,
                    material_origin=_uniform_material_origin(
                        physical_atoms,
                        compact_allocation,
                        unresolved_allocation,
                    ),
                )
            )

        enriched = LedgerTransition(
            name=transition.name,
            debits=tuple(enriched_debits),
            credits=tuple(enriched_credits),
            reason=transition.reason,
        )
        return (
            enriched,
            origin_balances,
            unresolved_balances,
            origin_methods,
            external_origins,
        )

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
        self._assert_balances_finite()
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
        self._assert_balances_finite(balances)
        return balances

    def set_account_policy(
        self,
        account: str,
        policy: AccountPolicy | Mapping[str, Any] | str | None = None,
    ) -> None:
        name = str(account)
        self._validate_account_known(name)
        replacement = _coerce_account_policy(name, policy)
        previous = self._policies.get(name)
        had_previous = name in self._policies
        self._policies[name] = replacement
        try:
            self.assert_balanced()
        except Exception:
            if had_previous:
                self._policies[name] = previous
            else:
                self._policies.pop(name, None)
            raise

    def account_policy(self, account: str) -> AccountPolicy:
        name = str(account)
        return self._policies.get(name, AccountPolicy.normal(name))

    def kg_by_account(self, account: str | None = None) -> dict[str, dict[str, float]] | dict[str, float]:
        """Return the exact signed kg projection of canonical mol balances."""
        self._assert_balances_finite()
        if account is not None:
            return _species_mol_to_kg(
                self._balances.get(str(account), {}),
                self.registry,
                tolerance_kg=0.0,
            )
        return {
            name: _species_mol_to_kg(
                species,
                self.registry,
                tolerance_kg=0.0,
            )
            for name, species in sorted(self._balances.items())
        }

    def project_account_kg(self, account: str) -> dict[str, float]:
        """Project one account for reports, products, and terminal summaries."""
        name = str(account)
        species_kg = self.kg_by_account(name)
        if self.account_policy(name).allow_negative:
            return species_kg
        projected: dict[str, float] = {}
        for species, kg in species_kg.items():
            value = float(kg)
            tolerance_kg = self._projection_tolerance_kg(name, species)
            if value < -tolerance_kg:
                # This is not display dust: a normal account below policy
                # tolerance is corrupt state and must refuse, not be clamped.
                raise AccountingError(
                    "negative outward mass from normal account: "
                    f"account={name!r} species={species!r} kg={value:.12g}"
                )
            if value < 0.0:
                # Display-only clamp. Canonical signed mol/kg remains untouched,
                # preserving exact ledger closure for audit and validation.
                continue
            if value != 0.0:
                projected[species] = value
        return projected

    def project_account_mol(self, account: str) -> dict[str, float]:
        """Project one account to outward-policy mol without changing closure."""
        return _species_kg_to_mol(
            self.project_account_kg(account),
            self.registry,
            tolerance_kg=0.0,
        )

    def _projection_tolerance_kg(
        self,
        account: str,
        species: str,
        movement_scale_kg: Mapping[str, Mapping[str, float]] | None = None,
    ) -> float:
        scales = (
            self._movement_scale_kg
            if movement_scale_kg is None
            else movement_scale_kg
        )
        scale_kg = abs(float(scales.get(account, {}).get(species, 0.0)))
        # Subtraction/conversion roundoff scales with the largest movement for
        # this account/species, so the relative term preserves ~1e-12 kg dust
        # at a 1 kg scale without granting that absolute band to tiny accounts.
        # The 1e-15 kg floor covers near-zero representation noise only.
        return max(
            self.balance_absolute_floor_kg,
            self.balance_relative_tolerance * scale_kg,
        )

    @staticmethod
    def _record_movement_scale(
        movement_scale_kg: dict[str, dict[str, float]],
        account: str,
        species_kg: Mapping[str, float],
    ) -> None:
        account_scale = movement_scale_kg.setdefault(str(account), {})
        for species, kg in species_kg.items():
            value = abs(float(kg))
            account_scale[str(species)] = max(
                value,
                account_scale.get(str(species), 0.0),
            )

    def _record_lot_movement_scale(
        self,
        movement_scale_kg: dict[str, dict[str, float]],
        lot: MaterialLot,
    ) -> None:
        self._record_movement_scale(
            movement_scale_kg,
            lot.account,
            lot.species_kg,
        )

    def projected_total_kg_by_account(self, account: str) -> float:
        return sum(self.project_account_kg(account).values())

    def mol_by_account(self, account: str | None = None) -> dict[str, dict[str, float]] | dict[str, float]:
        self._assert_balances_finite()
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
        self._assert_balances_finite()
        if account is not None:
            return sum(self._balances.get(str(account), {}).values())
        return {name: sum(species.values()) for name, species in sorted(self._balances.items())}

    def kg_by_species(self, account: str | None = None) -> dict[str, float]:
        if account is not None:
            return self.project_account_kg(str(account))
        totals: defaultdict[str, float] = defaultdict(float)
        for name in sorted(self._balances):
            species_kg = self.project_account_kg(name)
            for species, kg in species_kg.items():
                totals[species] += kg
        return dict(sorted((species, kg) for species, kg in totals.items() if kg != 0.0))

    def mol_by_species(self, account: str | None = None) -> dict[str, float]:
        self._assert_balances_finite()
        if account is not None:
            return dict(self._balances.get(str(account), {}))
        totals: defaultdict[str, float] = defaultdict(float)
        for species_mol in self._balances.values():
            for species, mol in species_mol.items():
                totals[species] += mol
        return dict(sorted((species, mol) for species, mol in totals.items() if mol != 0.0))

    def atom_moles_by_account(self, account: str) -> dict[str, float]:
        self._assert_balances_finite()
        return _signed_atom_moles_from_species_mol(
            self._balances.get(str(account), {}), self.registry)

    def element_atom_drift_report(self) -> dict[str, Any]:
        """Return report-only cumulative element residuals in mol-atoms."""
        transition_terms: defaultdict[str, list[float]] = defaultdict(list)
        for transition in self._transitions:
            debits = transition.debit_atom_moles(self.registry)
            credits = transition.credit_atom_moles(self.registry)
            # Sorted for the same reason as _reconcile_origin_projection
            # (b-302): this builds a dict whose key order would otherwise be
            # hash-dependent. Not serialised into run artifacts today, so this
            # is hygiene rather than a fix -- but it is the same latent shape.
            for element in sorted(set(debits) | set(credits)):
                transition_terms[element].append(
                    credits.get(element, 0.0) - debits.get(element, 0.0)
                )

        input_terms: defaultdict[str, list[float]] = defaultdict(list)
        for species_mol in self._initial_balances.values():
            for element, mol_atoms in _signed_atom_moles_from_species_mol(
                species_mol, self.registry
            ).items():
                input_terms[element].append(mol_atoms)
        for element, mol_atoms in _sum_lot_atom_moles(
            self._external_loads, self.registry
        ).items():
            input_terms[element].append(mol_atoms)

        final_terms: defaultdict[str, list[float]] = defaultdict(list)
        for species_mol in self._balances.values():
            for element, mol_atoms in _signed_atom_moles_from_species_mol(
                species_mol, self.registry
            ).items():
                final_terms[element].append(mol_atoms)

        elements = sorted(
            set(transition_terms) | set(input_terms) | set(final_terms)
        )
        return {
            "unit": "mol-atoms",
            "sign_convention": "final_minus_input",
            "accepted_transition_residual_mol_atoms": {
                element: math.fsum(transition_terms[element])
                for element in elements
            },
            "whole_run_boundary_residual_mol_atoms": {
                element: math.fsum(
                    [
                        *final_terms[element],
                        *(-value for value in input_terms[element]),
                    ]
                )
                for element in elements
            },
        }

    def reservoir_balances(self) -> dict[str, dict[str, Any]]:
        self._assert_balances_finite()
        reservoir_accounts = set(self._policies) | set(self._balances)
        report: dict[str, dict[str, Any]] = {}
        for account in sorted(name for name in reservoir_accounts if name.startswith("reservoir.")):
            policy = self.account_policy(account)
            species_mol = dict(self._balances.get(account, {}))
            species_kg = self.project_account_kg(account)
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
        kg_by_account = {
            account: self.project_account_kg(account)
            for account in sorted(self._balances)
        }
        mol_by_account = {
            account: self.project_account_mol(account)
            for account in sorted(self._balances)
        }
        total_kg_by_account = {
            account: sum(species_kg.values())
            for account, species_kg in kg_by_account.items()
        }
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
            "element_atom_drift": self.element_atom_drift_report(),
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
        self._assert_balances_finite()
        for transition in self._transitions:
            capability = (
                _C7_TERMINAL_SLAG_REWORK_CAPABILITY
                if id(transition) in self._terminal_debit_authorized_transition_ids
                else None
            )
            self._validate_terminal_debits(
                transition,
                terminal_debit_capability=capability,
            )
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
        return {
            name: self.project_account_kg(name)
            for name in sorted(self._balances)
        }

    def account_species_mol(self, account: str | None = None) -> dict[str, dict[str, float]] | dict[str, float]:
        if account is not None:
            return self.mol_by_species(account)
        return self.mol_by_account()

    def account_kg(self, account: str | None = None) -> dict[str, float] | float:
        if account is not None:
            return self.projected_total_kg_by_account(str(account))
        return {
            name: self.projected_total_kg_by_account(name)
            for name in sorted(self._balances)
        }

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
                name = str(account)
                self._validate_account_known(name)
                self._policies[name] = _coerce_account_policy(name, policy)
            return
        for policy in account_policies:
            if not isinstance(policy, AccountPolicy):
                raise AccountingError("account policy iterables must contain AccountPolicy objects")
            self._validate_account_known(policy.account)
            self._policies[policy.account] = policy

    def _assert_balances_finite(
        self,
        balances: Mapping[str, Mapping[str, float]] | None = None,
    ) -> None:
        checked = self._balances if balances is None else balances
        for account, species_mol in checked.items():
            for species, mol in species_mol.items():
                try:
                    value = float(mol)
                except (TypeError, ValueError) as exc:
                    raise AccountingError(
                        "ledger_balance_nonfinite: "
                        f"account={account!r} species={species!r} mol={mol!r}"
                    ) from exc
                if not math.isfinite(value):
                    raise AccountingError(
                        "ledger_balance_nonfinite: "
                        f"account={account!r} species={species!r} mol={mol!r}"
                    )

    def _validate_account_policies(
        self,
        balances: Mapping[str, Mapping[str, float]] | None = None,
        movement_scale_kg: Mapping[str, Mapping[str, float]] | None = None,
    ) -> None:
        checked = balances if balances is not None else self._balances
        self._assert_balances_finite(checked)
        for account, species_mol in checked.items():
            self._validate_account_known(account)
            policy = self.account_policy(account)
            species_kg = _species_mol_to_kg(
                species_mol,
                self.registry,
                # Policy applies the movement-scaled tolerance below; do not
                # erase a small signed balance before that comparison.
                tolerance_kg=0.0,
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
                tolerance_kg = self._projection_tolerance_kg(
                    account,
                    species,
                    movement_scale_kg,
                )
                if kg >= -tolerance_kg:
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
                if kg < -limit - tolerance_kg:
                    raise OverdraftError(
                        f"reservoir account {account!r} exceeded {species!r} credit: "
                        f"balance={kg:.12g} kg limit={limit:.12g} kg"
                    )

    def _validate_account_known(self, account: str) -> None:
        """Reject any account outside the allowlist when strict (opt-in).

        Scope: this guards every PUBLIC balance-writer (apply / transfer /
        move / load_external[_mol] / set_account_policy / __init__ seeding),
        all of which route through it. Direct mutation of the private
        ``self._balances`` dict bypasses it by construction; no production
        path does that (test fakes only). ``allowed_accounts is None`` =
        permissive (default), preserving legacy behaviour for ad-hoc tests.
        """
        if self._allowed_accounts is None:
            return
        name = str(account)
        if name in self._allowed_accounts:
            return
        if any(name.startswith(prefix) for prefix in self._allowed_account_prefixes):
            return
        raise AccountingError(
            f"unknown ledger account {name!r}: not in the production allowlist "
            "(typo? add to KNOWN_LEDGER_ACCOUNTS)"
        )

    def _validate_terminal_debits(
        self,
        transition: LedgerTransition,
        *,
        terminal_debit_capability: object | None = None,
    ) -> None:
        credit_accounts = {lot.account for lot in transition.credits}
        for lot in transition.debits:
            if not self.account_policy(lot.account).terminal:
                continue
            if (
                terminal_debit_capability is _C7_TERMINAL_SLAG_REWORK_CAPABILITY
                and _is_c7_terminal_slag_rework(transition, lot)
            ):
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


def snapshot_atom_ledger(ledger: Any) -> Any:
    """Return an independently owned copy of every mutable ledger container."""

    snapshot = shallow_copy(ledger)
    snapshot.__dict__ = _clone_owned_ledger_state(ledger.__dict__, {})
    return snapshot


def _clone_owned_ledger_state(value: Any, memo: dict[int, Any]) -> Any:
    identity = id(value)
    if identity in memo:
        return memo[identity]
    if isinstance(value, dict):
        cloned_dict: dict[Any, Any] = {}
        memo[identity] = cloned_dict
        for key, item in value.items():
            cloned_dict[key] = _clone_owned_ledger_state(item, memo)
        return cloned_dict
    if isinstance(value, list):
        cloned_list: list[Any] = []
        memo[identity] = cloned_list
        cloned_list.extend(_clone_owned_ledger_state(item, memo) for item in value)
        return cloned_list
    if isinstance(value, set):
        cloned_set: set[Any] = set()
        memo[identity] = cloned_set
        cloned_set.update(value)
        return cloned_set
    if isinstance(value, tuple):
        cloned_tuple = tuple(
            _clone_owned_ledger_state(item, memo) for item in value
        )
        memo[identity] = cloned_tuple
        return cloned_tuple
    return value


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
        expected_account = str(account).strip()
        embedded_account = str(data.get("account", expected_account)).strip()
        if embedded_account != expected_account:
            raise AccountingError(
                f"policy account {embedded_account!r} does not match key "
                f"{expected_account!r}"
            )
        data["account"] = expected_account
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


_KEEP_MATERIAL_ORIGIN = object()


def _require_material_origin(value: str | None) -> str:
    if value is None or not str(value).strip():
        raise MaterialOriginError(
            "external material load requires material_origin='feedstock' or 'reagent'"
        )
    origin = str(value).strip()
    if origin not in MATERIAL_ORIGINS:
        raise MaterialOriginError(
            f"external material origin must be one of {sorted(MATERIAL_ORIGINS)}, "
            f"got {origin!r}"
        )
    return origin


def _lot_with_origin_atoms(
    lot: MaterialLot,
    origin_atom_moles: Mapping[str, Mapping[str, float]],
    attribution_methods: Mapping[str, str],
    *,
    material_origin: str | None | object = _KEEP_MATERIAL_ORIGIN,
) -> MaterialLot:
    return MaterialLot(
        account=lot.account,
        species_kg=lot.species_kg,
        source=lot.source,
        meta=lot.meta,
        material_origin=(
            lot.material_origin
            if material_origin is _KEEP_MATERIAL_ORIGIN
            else material_origin
        ),
        origin_atom_moles=origin_atom_moles,
        attribution_method_by_element=attribution_methods,
    )


def _uniform_material_origin(
    physical_atoms: Mapping[str, float],
    origin_atom_moles: Mapping[str, Mapping[str, float]],
    unresolved_atoms: Mapping[str, float],
) -> str | None:
    candidate: str | None = None
    for element, physical in physical_atoms.items():
        if float(unresolved_atoms.get(str(element), 0.0)) > 0.0:
            return None
        carriers = [
            origin
            for origin, elements in origin_atom_moles.items()
            if float(elements.get(str(element), 0.0)) > 0.0
        ]
        if len(carriers) != 1:
            return None
        origin = carriers[0]
        if not math.isclose(
            float(origin_atom_moles[origin].get(str(element), 0.0)),
            float(physical),
            rel_tol=1.0e-9,
            abs_tol=1.0e-12,
        ):
            return None
        if candidate is None:
            candidate = origin
        elif candidate != origin:
            return None
    return candidate


def _copy_origin_balances(
    balances: Mapping[str, Mapping[str, Mapping[str, float]]],
) -> dict[str, dict[str, dict[str, float]]]:
    return {
        str(account): {
            str(element): {
                str(origin): float(amount)
                for origin, amount in origins.items()
            }
            for element, origins in elements.items()
        }
        for account, elements in balances.items()
    }


def _element_atom_moles_from_species_mol(
    species_mol: Mapping[str, float],
    registry: Mapping[str, Any],
) -> dict[str, float]:
    atoms: defaultdict[str, float] = defaultdict(float)
    for species, amount in species_mol.items():
        formula = resolve_species_formula(str(species), registry)
        for element, atom_moles in formula.atom_moles(float(amount)).items():
            atoms[str(element)] += float(atom_moles)
    return dict(atoms)


def _lot_unattributed_atom_moles(
    lot: MaterialLot,
    registry: Mapping[str, Any],
) -> dict[str, float]:
    known_by_element: defaultdict[str, float] = defaultdict(float)
    for elements in lot.origin_atom_moles.values():
        _merge_element_amounts(known_by_element, elements)
    return {
        str(element): amount
        for element, physical in lot.atom_moles_for(registry).items()
        if (
            amount := max(
                0.0,
                float(physical) - known_by_element.get(str(element), 0.0),
            )
        )
        > 0.0
    }


def _project_origin_unattributed_debt(
    transition: LedgerTransition,
    current: Mapping[str, Mapping[str, float]],
    registry: Mapping[str, Any],
) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    projected = _copy_balances(current)
    transition_debt: defaultdict[str, float] = defaultdict(float)
    newly_unattributed: defaultdict[str, float] = defaultdict(float)
    for lot in transition.debits:
        account = str(lot.account)
        for element, amount in _lot_unattributed_atom_moles(
            lot,
            registry,
        ).items():
            available = float(projected.get(account, {}).get(element, 0.0))
            backed = min(amount, available)
            remaining = available - backed
            if remaining > 0.0:
                projected.setdefault(account, {})[element] = remaining
            else:
                projected.get(account, {}).pop(element, None)
                if not projected.get(account, {}):
                    projected.pop(account, None)
            newly_unattributed[element] += amount - backed
            transition_debt[element] += amount

    for lot in transition.credits:
        account = str(lot.account)
        for element, amount in _lot_unattributed_atom_moles(
            lot,
            registry,
        ).items():
            transferred = min(amount, transition_debt.get(element, 0.0))
            transition_debt[element] -= transferred
            newly_unattributed[element] += amount - transferred
            projected.setdefault(account, {})[element] = (
                float(projected.get(account, {}).get(element, 0.0))
                + amount
            )

    return (
        projected,
        {
            str(element): amount
            for element, amount in newly_unattributed.items()
            if amount > 0.0
        },
    )


def _merge_element_amounts(
    destination: dict[str, float] | defaultdict[str, float],
    source: Mapping[str, float],
) -> None:
    for element, amount in source.items():
        destination[str(element)] = (
            float(destination.get(str(element), 0.0)) + float(amount)
        )


def _set_origin_balance(
    balances: dict[str, dict[str, dict[str, float]]],
    account: str,
    element: str,
    origin: str,
    amount: float,
    tolerance: float,
) -> None:
    if amount <= tolerance:
        origins = balances.get(account, {}).get(element, {})
        origins.pop(origin, None)
        if not origins:
            balances.get(account, {}).pop(element, None)
        if not balances.get(account, {}):
            balances.pop(account, None)
        return
    balances.setdefault(account, {}).setdefault(element, {})[origin] = amount


def _set_element_balance(
    balances: dict[str, dict[str, float]],
    account: str,
    element: str,
    amount: float,
    tolerance: float,
) -> None:
    if amount <= tolerance:
        balances.get(account, {}).pop(element, None)
        if not balances.get(account, {}):
            balances.pop(account, None)
        return
    balances.setdefault(account, {})[element] = amount


def _clean_origin_method_if_empty(
    methods: dict[str, dict[str, str]],
    origins: Mapping[str, Mapping[str, Mapping[str, float]]],
    unresolved: Mapping[str, Mapping[str, float]],
    account: str,
    element: str,
    tolerance: float,
) -> None:
    known = math.fsum(origins.get(account, {}).get(element, {}).values())
    unknown = float(unresolved.get(account, {}).get(element, 0.0))
    if known > tolerance or unknown > tolerance:
        return
    methods.get(account, {}).pop(element, None)
    if not methods.get(account, {}):
        methods.pop(account, None)


def _reconcile_origin_projection(
    origins: dict[str, dict[str, dict[str, float]]],
    unresolved: dict[str, dict[str, float]],
    methods: dict[str, dict[str, str]],
    physical_species_mol: Mapping[str, Mapping[str, float]],
    registry: Mapping[str, Any],
) -> None:
    # ★ SORTED, and the sort is load-bearing rather than cosmetic (b-302).
    # This function mutates the projection dicts IN PLACE before they are
    # committed, and it writes through _set_element_balance /
    # _set_origin_balance, which pop dead keys and setdefault-append new ones
    # in whatever order this loop yields. A bare set of element/account
    # STRINGS iterates in hash order: stable within a process, variable
    # across processes. Every other insertion path into these dicts is
    # already sorted (MaterialLot.atom_moles_for does dict(sorted(...))), so
    # this was the single unordered writer, and it made the serialised
    # yield_disposition non-reproducible run to run -- two elements in one
    # account admit two orderings, which is exactly the bistable digest
    # observed.
    #
    # VALUES WERE NEVER AT RISK: each element's reconcile math is an fsum
    # over its own origins with a per-element scale, independent of iteration
    # order, which is why two captured documents were value-identical across
    # both orderings. This fixes SERIALISATION determinism, not arithmetic.
    #
    # Sorting here rather than passing sort_keys=True at the one test that
    # digests the artifact is deliberate: it makes every present and future
    # consumer deterministic -- artifact diffs, run stores, precompute
    # comparisons -- instead of greening a single assertion.
    accounts = sorted(set(physical_species_mol) | set(origins) | set(unresolved))
    for account in accounts:
        physical_atoms = _signed_atom_moles_from_species_mol(
            physical_species_mol.get(account, {}),
            registry,
        )
        elements = sorted(
            set(physical_atoms)
            | set(origins.get(account, {}))
            | set(unresolved.get(account, {}))
        )
        for element in elements:
            physical = max(0.0, float(physical_atoms.get(element, 0.0)))
            origin_amounts = origins.get(account, {}).get(element, {})
            unknown = max(
                0.0,
                float(unresolved.get(account, {}).get(element, 0.0)),
            )
            tracked_total = math.fsum(origin_amounts.values()) + unknown
            if tracked_total <= 0.0:
                _set_element_balance(
                    unresolved,
                    account,
                    element,
                    physical,
                    0.0,
                )
                continue
            # Numerical reconciliation scales typed buckets together; it never
            # invents one origin as the complement of another.
            scale = physical / tracked_total
            for origin in tuple(origin_amounts):
                _set_origin_balance(
                    origins,
                    account,
                    element,
                    origin,
                    max(0.0, float(origin_amounts[origin]) * scale),
                    0.0,
                )
            _set_element_balance(
                unresolved,
                account,
                element,
                unknown * scale,
                0.0,
            )
            _clean_origin_method_if_empty(
                methods,
                origins,
                unresolved,
                account,
                element,
                0.0,
            )


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
        account_balances[species] = value
    # Keep sub-tolerance signed dust rather than pruning it. A prune of r kg
    # changes whole-ledger closure by exactly -r without a counterpart lot;
    # repeated same-sign near-depletions can therefore bias closure by as much
    # as N*tolerance_kg. Retention makes that policy contribution exactly zero:
    # signed IEEE-754 remainders stay in their account, so only ordinary
    # floating summation error remains (bounded by O(N*eps*sum(|movement|))),
    # with no systematic N*tolerance_kg deletion term.
    balances[lot.account] = {
        species: mol
        for species, mol in sorted(account_balances.items())
        if mol != 0.0
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
        "species_mol": _thaw_report_value(lot.meta.get("species_mol", {})),
        "source": lot.source,
        "meta": _thaw_report_value(lot.meta),
        "material_origin": lot.material_origin,
        "origin_atom_moles": _thaw_report_value(lot.origin_atom_moles),
        "attribution_method_by_element": _thaw_report_value(
            lot.attribution_method_by_element
        ),
    }


def _thaw_report_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_report_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_report_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_thaw_report_value(item) for item in sorted(value, key=repr)]
    return value


def _is_terminal_account(account: str) -> bool:
    return str(account).startswith("terminal.") or str(account) == "vent"


def _finite_nonnegative_tolerance(name: str, value: float) -> float:
    tolerance = float(value)
    if not math.isfinite(tolerance) or tolerance < 0.0:
        raise AccountingError(f"{name} must be finite and non-negative")
    return tolerance


def _is_c7_terminal_slag_rework(
    transition: LedgerTransition,
    debit_lot: MaterialLot,
) -> bool:
    if debit_lot.account != "terminal.slag":
        return False
    if not transition.name.startswith(C7_TERMINAL_SLAG_DEBIT_PREFIXES):
        return False
    if set(debit_lot.species_kg) != {"CaO"}:
        return False

    debit_accounts = {lot.account for lot in transition.debits}
    if not debit_accounts <= {
        "process.cleaned_melt",
        "process.metal_phase",
        "process.c7_al_credit",
        "terminal.slag",
    }:
        return False

    credit_species: defaultdict[str, set[str]] = defaultdict(set)
    for lot in transition.credits:
        credit_species[lot.account].update(lot.species_kg)
    expected_slag_species = (
        {"Ca3Al2O6"}
        if transition.name.startswith("ca_aluminothermic_c3a_")
        else {"Ca12Al14O33"}
    )
    # C7 reworks CaO already classified as slag: Ca leaves through overhead
    # while the residual Al-Ca-O product returns to the same terminal bucket.
    return dict(credit_species) == {
        "process.overhead_gas": {"Ca"},
        "terminal.slag": expected_slag_species,
    }


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


def _origin_atom_tolerance(amount: float) -> float:
    return max(1.0e-18, abs(float(amount)) * 5.0e-14)
