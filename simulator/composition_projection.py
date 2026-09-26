"""Shared classification for projected composition inputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
import numbers
from typing import Any


# The 1.0 wt% boundary is the shared composition-basis rule from
# ``docs-private/research/2026-09-24-schema-amendments/99-RULING-2026-09-24.md``
# §"Composition basis" and its MAGEMin-specific addendum
# ``99-RULING-2026-09-25-addenda.md`` §"MAGEMin projected bulk".
# Runtime Stage 0 refuses non-oxides first, so this denominator is the whole oxide-only charge.
# This differs from the battery composition-basis rule (99-RULING-2026-09-24 ~line 49),
# which omits/flags non-oxides <=1.0 wt% for printed melts without Stage 0; do not unify (99-RULING-2026-09-25-addenda).
PROJECTED_BULK_MAX_DROPPED_WT_PCT = 1.0
PROJECTED_BULK_CLASSIFICATION_KEY = 'composition_projection_classification'
# MAGEMin treats strictly positive projected amounts as present. The adapter
# and this classifier import the same boundary so a component cannot be
# dropped by one and retained by the other.
PROJECTED_BULK_COMPONENT_MIN_WT_PCT = 0.0


@dataclass(frozen=True)
class ProjectedBulkComponent:
    """One dropped component; ``mol`` is mol per kg of source bulk."""

    component: str
    wt_pct: float
    mol: float | None


@dataclass(frozen=True)
class ProjectedBulkClassification:
    """Immutable normalized projection record shared by gate and adapter."""

    components: tuple[ProjectedBulkComponent, ...]
    dropped_total_wt_pct: float
    threshold_wt_pct: float
    source_sum_wt_pct: float
    verdict: str
    _dropped_component_mol_unavailable_reasons: Mapping[str, str] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    invalid_reason: str | None = None

    @property
    def dropped_components(self) -> tuple[str, ...]:
        return tuple(item.component for item in self.components)

    @property
    def dropped_component_wt_pct(self) -> tuple[tuple[str, float], ...]:
        return tuple(
            (item.component, item.wt_pct) for item in self.components
        )

    @property
    def dropped_component_mol_per_kg(
        self,
    ) -> tuple[tuple[str, float | None], ...]:
        return tuple(
            (item.component, item.mol) for item in self.components
        )

    def dropped_component_mol_unavailable_reason(
        self,
        component: str,
    ) -> str | None:
        return self._dropped_component_mol_unavailable_reasons.get(component)

    @property
    def within_threshold(self) -> bool:
        return self.verdict == "within_threshold"

    def as_diagnostics(self) -> dict[str, object]:
        """Return only plain data for result diagnostics transport."""
        return {
            'components': [
                {
                    'component': item.component,
                    'wt_pct': item.wt_pct,
                    'mol': item.mol,
                }
                for item in self.components
            ],
            'dropped_components': list(self.dropped_components),
            'dropped_component_wt_pct': dict(self.dropped_component_wt_pct),
            'dropped_component_mol_per_kg': dict(
                self.dropped_component_mol_per_kg
            ),
            'dropped_total_wt_pct': self.dropped_total_wt_pct,
            'threshold_wt_pct': self.threshold_wt_pct,
            'source_sum_wt_pct': self.source_sum_wt_pct,
            'projection_verdict': self.verdict,
            'invalid_reason': self.invalid_reason,
        }


def classify_projected_bulk(
    dropped_component_wt_pct: Mapping[str, float],
    *,
    source_sum_wt_pct: float,
    dropped_component_mol_per_kg: Mapping[str, float | None] | None = None,
    dropped_component_mol_unavailable_reasons: Mapping[str, str] | None = None,
) -> ProjectedBulkClassification:
    """Classify projected mass against the shared 1.0 wt% boundary.

    ``dropped_component_wt_pct`` contains component amounts on the same
    unnormalised wt% scale as ``source_sum_wt_pct``. The returned component
    values and total are normalized to the full input bulk (100 wt%).
    """
    invalid_reason: str | None = None
    if (
        not isinstance(source_sum_wt_pct, numbers.Real)
        or isinstance(source_sum_wt_pct, bool)
    ):
        source_sum = 0.0
        invalid_reason = 'source_sum_not_positive_finite'
    else:
        try:
            source_sum = float(source_sum_wt_pct)
        except (OverflowError, TypeError, ValueError):
            source_sum = 0.0
            invalid_reason = 'source_sum_not_positive_finite'
    if source_sum <= 0.0 or not math.isfinite(source_sum):
        source_sum = 0.0
        invalid_reason = 'source_sum_not_positive_finite'
    positive: dict[str, float] = {}
    # First invalid reason wins: the source sum is checked before the container.
    if not isinstance(dropped_component_wt_pct, Mapping):
        if invalid_reason is None:
            invalid_reason = 'dropped_components_not_mapping'
    else:
        for component, value in dropped_component_wt_pct.items():
            name = str(component)
            if (
                not isinstance(value, numbers.Real)
                or isinstance(value, bool)
            ):
                if invalid_reason is None:
                    invalid_reason = f'component_not_numeric:{name}'
                continue
            # Check the sign on the exact value: a tiny negative Fraction
            # underflows to -0.0 in float() and would pass as "absent".
            if value < 0:
                if invalid_reason is None:
                    invalid_reason = f'component_negative:{name}'
                continue
            try:
                numeric = float(value)
            except (OverflowError, TypeError, ValueError):
                if invalid_reason is None:
                    invalid_reason = f'component_not_numeric:{name}'
                continue
            if not math.isfinite(numeric):
                if invalid_reason is None:
                    invalid_reason = f'component_not_finite:{name}'
                continue
            if numeric < PROJECTED_BULK_COMPONENT_MIN_WT_PCT:
                if invalid_reason is None:
                    invalid_reason = f'component_negative:{name}'
                continue
            if numeric > PROJECTED_BULK_COMPONENT_MIN_WT_PCT:
                positive[name] = numeric
    if invalid_reason is None:
        scale = 100.0 / source_sum
    else:
        scale = 0.0
    normalized = {
        component: value * scale
        for component, value in sorted(positive.items())
    }
    if any(not math.isfinite(value) for value in normalized.values()):
        normalized = {}
        dropped_total = 0.0
        invalid_reason = 'normalized_value_not_finite'
    else:
        try:
            dropped_total = math.fsum(normalized.values())
        except OverflowError:
            normalized = {}
            dropped_total = 0.0
            invalid_reason = 'normalized_value_not_finite'
        if not math.isfinite(dropped_total):
            normalized = {}
            dropped_total = 0.0
            invalid_reason = 'normalized_value_not_finite'
    if invalid_reason is not None:
        verdict = 'invalid_input'
    else:
        verdict = (
            "within_threshold"
            if dropped_total <= PROJECTED_BULK_MAX_DROPPED_WT_PCT
            else "over_threshold"
        )
    mol_by_component = dropped_component_mol_per_kg or {}
    components = tuple(
        ProjectedBulkComponent(
            component=component,
            wt_pct=wt_pct,
            mol=_finite_optional_float(mol_by_component.get(component)),
        )
        for component, wt_pct in normalized.items()
    )
    return ProjectedBulkClassification(
        components=components,
        dropped_total_wt_pct=dropped_total,
        threshold_wt_pct=PROJECTED_BULK_MAX_DROPPED_WT_PCT,
        source_sum_wt_pct=source_sum,
        verdict=verdict,
        invalid_reason=invalid_reason,
        _dropped_component_mol_unavailable_reasons=dict(
            dropped_component_mol_unavailable_reasons or {}
        ),
    )


def projected_component_moles_per_kg(
    dropped_component_wt_pct: Mapping[str, float],
    *,
    source_sum_wt_pct: float,
    species_formula_registry: Mapping[str, Any] | None = None,
) -> tuple[dict[str, float | None], dict[str, str]]:
    """Compute dropped-component mol/kg and retain formula-resolution reasons.

    Premise: the dropped wt% values are on the source-bulk basis. For one kg of
    source bulk, ``n_i/kg = (w_i/100) / M_i`` over that bulk, with the
    source-sum normalization used by the projection: ``w_i/100`` becomes
    ``w_i/source_sum_wt_pct``. The unit check is ``(kg/kg)/(kg/mol) = mol/kg``.
    Sanity check: test basalt MnO: 0.2009 wt% → 0.0283 mol/kg.
    """
    try:
        source_sum = float(source_sum_wt_pct)
    except (TypeError, ValueError):
        return {}, {}
    # Invalid source sums return empty mappings; the classifier's invalid_input
    # verdict guards this helper from being consumed for those inputs.
    if source_sum <= 0.0 or not math.isfinite(source_sum):
        return {}, {}

    from simulator.accounting.formulas import resolve_species_formula

    moles_per_kg: dict[str, float | None] = {}
    unavailable_reasons: dict[str, str] = {}
    for component, amount in dropped_component_wt_pct.items():
        name = str(component)
        try:
            mass_fraction = float(amount) / source_sum
            molar_mass = resolve_species_formula(
                name,
                species_formula_registry,
            ).molar_mass_kg_per_mol()
            if not math.isfinite(molar_mass) or molar_mass <= 0.0:
                raise ValueError(f'invalid molar mass for {name!r}')
            moles_per_kg[name] = mass_fraction / molar_mass
        except Exception as exc:  # Formula failure is unavailable, never zero.
            moles_per_kg[name] = None
            unavailable_reasons[name] = str(exc) or type(exc).__name__
    return moles_per_kg, unavailable_reasons


def format_projected_component_mol(
    classification: ProjectedBulkClassification,
    component: str,
) -> str:
    """Format one component's mol/kg value for a projection notice."""
    mol_by_component = dict(classification.dropped_component_mol_per_kg)
    mol = mol_by_component.get(component)
    if mol is None:
        reason = (
            classification.dropped_component_mol_unavailable_reason(component)
            or 'formula unavailable'
        )
        return f'mol/kg unavailable ({reason})'
    return f'{mol:.12g} mol/kg'


def _finite_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


__all__ = (
    "PROJECTED_BULK_MAX_DROPPED_WT_PCT",
    "PROJECTED_BULK_CLASSIFICATION_KEY",
    "PROJECTED_BULK_COMPONENT_MIN_WT_PCT",
    "ProjectedBulkComponent",
    "ProjectedBulkClassification",
    "classify_projected_bulk",
    "format_projected_component_mol",
    "projected_component_moles_per_kg",
)
