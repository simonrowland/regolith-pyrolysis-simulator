"""Material lots used by atom ledger transitions."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from simulator.accounting.exceptions import AccountingError
from simulator.accounting.formulas import resolve_species_formula

EMPTY_KG_TOLERANCE = 1e-12


@dataclass(frozen=True)
class MaterialLot:
    """Species masses associated with one account."""

    account: str
    species_kg: Mapping[str, float]
    source: str = ""
    meta: Mapping[str, Any] = field(default_factory=dict)

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
        object.__setattr__(self, "meta", MappingProxyType(dict(self.meta or {})))

    def without_empty(self, tolerance_kg: float = EMPTY_KG_TOLERANCE) -> "MaterialLot":
        kept = {species: kg for species, kg in self.species_kg.items() if abs(kg) > tolerance_kg}
        return MaterialLot(self.account, kept, source=self.source, meta=self.meta)

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
