"""Material lots used by atom ledger transitions."""

from __future__ import annotations

import math
from copy import deepcopy
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from simulator.accounting.exceptions import AccountingError, PoolWithdrawalError
from simulator.accounting.formulas import resolve_species_formula
from simulator.scalar_boundary import is_declared_real_scalar

EMPTY_KG_TOLERANCE = 1e-12
MATERIAL_ORIGINS = frozenset({"feedstock", "reagent"})
ATTRIBUTION_METHODS = frozenset({"tracked", "pool_ratio"})
POOL_WITHDRAWAL_ABSOLUTE_TOLERANCE = 1.0e-18
POOL_WITHDRAWAL_RELATIVE_TOLERANCE = 5.0e-14


def allocate_pool_withdrawal(
    balances: Mapping[str, float],
    withdrawal: float,
    *,
    absolute_tolerance: float = POOL_WITHDRAWAL_ABSOLUTE_TOLERANCE,
) -> dict[str, float]:
    """Allocate one withdrawal over a declared well-mixed pool."""

    available_by_origin: dict[str, float] = {}
    for origin, raw_amount in balances.items():
        amount = _pool_number(raw_amount, f"pool balance {origin}")
        if amount < 0.0:
            raise PoolWithdrawalError(
                f"invalid pool balance {origin}={raw_amount!r}"
            )
        if amount > 0.0:
            available_by_origin[str(origin)] = amount
    try:
        available = math.fsum(available_by_origin.values())
    except OverflowError as exc:
        raise PoolWithdrawalError("pool available balance is non-finite") from exc
    if not math.isfinite(available):
        raise PoolWithdrawalError("pool available balance is non-finite")
    amount = _pool_number(withdrawal, "pool withdrawal")
    absolute = _pool_number(absolute_tolerance, "pool absolute tolerance")
    if absolute < 0.0:
        raise PoolWithdrawalError(
            f"pool absolute tolerance must be non-negative, got {absolute:.12g}"
        )
    tolerance = max(
        absolute,
        available * POOL_WITHDRAWAL_RELATIVE_TOLERANCE,
    )
    if amount < -tolerance:
        raise PoolWithdrawalError(
            f"pool withdrawal must be non-negative, got {amount:.12g}"
        )
    if amount > available and amount - available > tolerance:
        raise PoolWithdrawalError(
            f"pool withdrawal exceeds available balance: "
            f"withdrawal={amount:.12g}, available={available:.12g}"
        )
    amount = min(max(0.0, amount), available)
    if amount <= 0.0 or available <= 0.0:
        return {}
    result: dict[str, float] = {}
    ordered = sorted(available_by_origin)
    for origin in ordered[:-1]:
        share = amount * (available_by_origin[origin] / available)
        if not math.isfinite(share):
            raise PoolWithdrawalError(
                f"pool withdrawal share for {origin} is non-finite"
            )
        result[origin] = share
    # Pool closure: sum_i(W * I_i / sum(I)) = W; the last share absorbs float dust.
    try:
        allocated = math.fsum(result.values())
    except OverflowError as exc:
        raise PoolWithdrawalError("pool allocated total is non-finite") from exc
    final_share = amount - allocated
    if not math.isfinite(final_share):
        raise PoolWithdrawalError(
            f"pool withdrawal share for {ordered[-1]} is non-finite"
        )
    result[ordered[-1]] = final_share
    try:
        allocated_total = math.fsum(result.values())
    except OverflowError as exc:
        raise PoolWithdrawalError("pool allocated total is non-finite") from exc
    if not math.isfinite(allocated_total):
        raise PoolWithdrawalError("pool allocated total is non-finite")
    return result


def _pool_number(value: Any, label: str) -> float:
    try:
        if not is_declared_real_scalar(value, allow_numeric_str=True):
            raise TypeError
        amount = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PoolWithdrawalError(f"invalid {label}") from exc
    if not math.isfinite(amount):
        raise PoolWithdrawalError(f"invalid {label}")
    return amount


class _FrozenMapping(Mapping[Any, Any]):
    def __init__(self, data: Mapping[Any, Any]) -> None:
        self._data = MappingProxyType(dict(data))

    def __getitem__(self, key: Any) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return repr(dict(self._data))

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Mapping):
            return dict(self._data) == dict(other)
        return False

    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("material lot metadata is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


def _freeze_meta(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _FrozenMapping(
            {key: _freeze_meta(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_meta(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_meta(item) for item in value)
    return deepcopy(value)


def _thaw_meta(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_meta(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_thaw_meta(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(_thaw_meta(item) for item in value)
    return value


@dataclass(frozen=True)
class MaterialLot:
    """Species masses associated with one account."""

    account: str
    species_kg: Mapping[str, float]
    source: str = ""
    meta: Mapping[str, Any] = field(default_factory=dict)
    material_origin: str | None = None
    origin_atom_moles: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    attribution_method_by_element: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        account = str(self.account).strip()
        if not account:
            raise AccountingError("account is required")

        normalized: dict[str, float] = {}
        for species, kg in dict(self.species_kg).items():
            name = str(species).strip()
            if not name:
                raise AccountingError("species name is required")
            value = float(kg)
            if not math.isfinite(value):
                raise AccountingError(f"mass for species {name!r} must be finite")
            if value < -EMPTY_KG_TOLERANCE:
                raise AccountingError(f"lot mass for species {name!r} must be non-negative")
            if abs(value) <= EMPTY_KG_TOLERANCE:
                value = 0.0
            normalized[name] = normalized.get(name, 0.0) + value

        object.__setattr__(self, "account", account)
        object.__setattr__(self, "species_kg", MappingProxyType(dict(sorted(normalized.items()))))
        object.__setattr__(self, "source", str(self.source or ""))
        object.__setattr__(self, "meta", _freeze_meta(dict(self.meta or {})))
        origin = None if self.material_origin is None else str(self.material_origin).strip()
        if origin not in MATERIAL_ORIGINS | {None}:
            raise AccountingError(
                f"material_origin must be one of {sorted(MATERIAL_ORIGINS)}, got {origin!r}"
            )
        origin_atoms: dict[str, dict[str, float]] = {}
        for raw_origin, raw_elements in dict(self.origin_atom_moles or {}).items():
            typed_origin = str(raw_origin).strip()
            if typed_origin not in MATERIAL_ORIGINS:
                raise AccountingError(f"unsupported material origin {typed_origin!r}")
            if not isinstance(raw_elements, Mapping):
                raise AccountingError(
                    f"origin atom allocation for {typed_origin!r} must be a mapping"
                )
            elements: dict[str, float] = {}
            for raw_element, raw_amount in raw_elements.items():
                element = str(raw_element).strip()
                amount = float(raw_amount)
                if not element:
                    raise AccountingError("origin atom allocation requires an element")
                if not math.isfinite(amount) or amount < 0.0:
                    raise AccountingError(
                        f"origin atom allocation {typed_origin}.{element} "
                        "must be finite and non-negative"
                    )
                if amount > 0.0:
                    elements[element] = amount
            if elements:
                origin_atoms[typed_origin] = dict(sorted(elements.items()))
        methods: dict[str, str] = {}
        for raw_element, raw_method in dict(
            self.attribution_method_by_element or {}
        ).items():
            element = str(raw_element).strip()
            method = str(raw_method).strip()
            if not element:
                raise AccountingError("origin attribution requires an element")
            if method not in ATTRIBUTION_METHODS:
                raise AccountingError(
                    f"unsupported origin attribution method {method!r}"
                )
            methods[element] = method
        object.__setattr__(self, "material_origin", origin)
        object.__setattr__(self, "origin_atom_moles", _freeze_meta(origin_atoms))
        object.__setattr__(
            self,
            "attribution_method_by_element",
            _freeze_meta(dict(sorted(methods.items()))),
        )

    def without_empty(self, tolerance_kg: float = EMPTY_KG_TOLERANCE) -> "MaterialLot":
        kept = {species: kg for species, kg in self.species_kg.items() if abs(kg) > tolerance_kg}
        return MaterialLot(
            self.account,
            kept,
            source=self.source,
            meta=self.meta,
            material_origin=self.material_origin,
            origin_atom_moles=self.origin_atom_moles,
            attribution_method_by_element=self.attribution_method_by_element,
        )

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        """Rebuild immutable views after crossing a pickle boundary."""
        return (
            type(self),
            (
                self.account,
                dict(self.species_kg),
                self.source,
                _thaw_meta(self.meta),
                self.material_origin,
                _thaw_meta(self.origin_atom_moles),
                _thaw_meta(self.attribution_method_by_element),
            ),
        )

    def total_mass_kg(self, registry: Mapping[str, Any] | None = None) -> float:
        if registry is not None:
            for species in self.species_kg:
                resolve_species_formula(species, registry)
        return sum(self.species_kg.values())

    @property
    def kg_total(self) -> float:
        return self.total_mass_kg()

    @property
    def species_moles(self) -> "_SpeciesMolesView":
        return _SpeciesMolesView(self)

    @property
    def atom_moles(self) -> "_AtomMolesView":
        return _AtomMolesView(self)

    def species_moles_for(self, registry: Mapping[str, Any] | None = None) -> dict[str, float]:
        moles: dict[str, float] = {}
        for species, kg in self.species_kg.items():
            formula = resolve_species_formula(species, registry)
            moles[species] = kg / formula.molar_mass_kg_per_mol()
        return moles

    def atom_moles_for(self, registry: Mapping[str, Any] | None = None) -> dict[str, float]:
        atoms: defaultdict[str, float] = defaultdict(float)
        for species, moles in self.species_moles_for(registry).items():
            formula = resolve_species_formula(species, registry)
            for element, atom_moles in formula.atom_moles(moles).items():
                atoms[element] += atom_moles
        return dict(sorted(atoms.items()))


class _DerivedMolesView(Mapping[str, float]):
    def __init__(self, lot: MaterialLot) -> None:
        self._lot = lot

    def __call__(self, registry: Mapping[str, Any] | None = None) -> dict[str, float]:
        return self._data(registry)

    def __getitem__(self, key: str) -> float:
        return self._data(None)[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data(None))

    def __len__(self) -> int:
        return len(self._data(None))

    def __repr__(self) -> str:
        return repr(self._data(None))

    def _data(self, registry: Mapping[str, Any] | None) -> dict[str, float]:
        raise NotImplementedError


class _SpeciesMolesView(_DerivedMolesView):
    def _data(self, registry: Mapping[str, Any] | None) -> dict[str, float]:
        return self._lot.species_moles_for(registry)


class _AtomMolesView(_DerivedMolesView):
    def _data(self, registry: Mapping[str, Any] | None) -> dict[str, float]:
        return self._lot.atom_moles_for(registry)
