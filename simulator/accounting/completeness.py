"""Shared extraction-completeness math."""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any, Mapping

from simulator.accounting.exceptions import AccountingError
from simulator.accounting.formulas import parse_formula
from simulator.state import MOLAR_MASS

_EPS = 1.0e-12

DEFAULT_RESIDUAL_SPECIES_BY_TARGET: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "SiO": ("SiO2", "SiO"),
    "Fe": ("FeO", "Fe"),
    "CrO2": ("Cr2O3", "CrO2", "Cr"),
    "Mg": ("MgO", "Mg"),
    "Na": ("Na2O", "Na"),
    "K": ("K2O", "K"),
})


@dataclass(frozen=True)
class TargetExtractionCompleteness:
    target_species: str
    completeness_fraction: float | None
    product_target_equiv_mol: float
    residual_target_equiv_mol: float
    denominator_target_equiv_mol: float
    reason: str = ""

    @property
    def detail(self) -> str:
        if self.completeness_fraction is None:
            return f"{self.target_species}: {self.reason}"
        return (
            f"{self.target_species}: "
            f"product_target_equiv_mol={self.product_target_equiv_mol:.6g}, "
            f"residual_target_equiv_mol={self.residual_target_equiv_mol:.6g}, "
            f"denominator_target_equiv_mol={self.denominator_target_equiv_mol:.6g}"
        )


def extraction_completeness_by_target(
    target_species: tuple[str, ...],
    residual_species_by_target: Mapping[str, tuple[str, ...]],
    product_ledger_kg: Mapping[str, Any],
    terminal_rump_kg: Mapping[str, Any],
    *,
    require_residual_species: bool = False,
) -> dict[str, TargetExtractionCompleteness]:
    residual_map = {
        str(target): tuple(str(species) for species in residuals)
        for target, residuals in residual_species_by_target.items()
    }
    products = {str(species): kg for species, kg in product_ledger_kg.items()}
    rump = {str(species): kg for species, kg in terminal_rump_kg.items()}
    results: dict[str, TargetExtractionCompleteness] = {}
    for raw_target in target_species:
        target = str(raw_target)
        if require_residual_species and target not in residual_map:
            results[target] = TargetExtractionCompleteness(
                target,
                None,
                0.0,
                0.0,
                0.0,
                "unknown: no residual species map for target",
            )
            continue
        try:
            product_mol = _target_equivalent_mol(
                target, target, products.get(target, 0.0))
            residual_mol = 0.0
            for residual in residual_map.get(target, (target,)):
                residual_mol += _target_equivalent_mol(
                    target,
                    residual,
                    rump.get(residual, 0.0),
                )
            denom = product_mol + residual_mol
            if denom <= _EPS:
                results[target] = TargetExtractionCompleteness(
                    target,
                    None,
                    product_mol,
                    residual_mol,
                    denom,
                    "no target-equivalent mol evidence",
                )
                continue
            results[target] = TargetExtractionCompleteness(
                target,
                product_mol / denom,
                product_mol,
                residual_mol,
                denom,
            )
        except (AccountingError, KeyError, TypeError, ValueError) as exc:
            results[target] = TargetExtractionCompleteness(
                target,
                None,
                0.0,
                0.0,
                0.0,
                f"unknown: {exc}",
            )
    return results


def extraction_completeness_pct(
    target_species: tuple[str, ...],
    residual_species_by_target: Mapping[str, tuple[str, ...]],
    product_ledger_kg: Mapping[str, Any],
    terminal_rump_kg: Mapping[str, Any],
) -> float:
    """Return the worst target completeness fraction across target species."""

    results = extraction_completeness_by_target(
        target_species,
        residual_species_by_target,
        product_ledger_kg,
        terminal_rump_kg,
    )
    if not results:
        raise ValueError("target_species must be non-empty")
    fractions: list[float] = []
    for result in results.values():
        if result.completeness_fraction is None:
            raise ValueError(result.reason)
        fractions.append(result.completeness_fraction)
    return min(fractions)


def _target_equivalent_mol(target: str, species: str, kg: Any) -> float:
    species_mol = _species_mol(species, kg)
    if species_mol <= _EPS:
        return 0.0
    target_element = _target_element(target)
    species_formula = parse_formula(species, species=species)
    element_count = species_formula.elements.get(target_element, 0.0)
    if element_count <= 0.0:
        raise ValueError(f"{species} contains no {target_element} for target {target}")
    return species_mol * element_count


def _target_element(target: str) -> str:
    formula = parse_formula(target, species=target)
    if len(formula.elements) == 1:
        return next(iter(formula.elements))
    non_oxygen = [element for element in formula.elements if element != "O"]
    if len(non_oxygen) == 1:
        return non_oxygen[0]
    raise ValueError(f"target {target} does not identify one target element")


def _species_mol(species: str, kg: Any) -> float:
    amount = _non_negative_number(kg, f"{species} kg")
    if amount <= _EPS:
        return 0.0
    molar_mass = MOLAR_MASS.get(species)
    if molar_mass is None:
        raise KeyError(f"missing molar mass for {species}")
    return amount * 1000.0 / float(molar_mass)


def _non_negative_number(value: Any, name: str) -> float:
    amount = _finite_number(value, name)
    if amount < -_EPS:
        raise ValueError(f"{name} must be non-negative")
    return max(0.0, amount)


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be numeric") from exc
    if not math.isfinite(amount):
        raise ValueError(f"{name} must be finite")
    return amount
