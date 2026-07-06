from __future__ import annotations

import math
from collections.abc import MutableMapping
from enum import Enum
from typing import Any, Literal


MELT_REGIME_EPSILON = 1.0e-12


class MeltRegime(str, Enum):
    FROZEN = "frozen"
    PARTIAL = "partial"
    MOLTEN = "molten"


def legacy_raw_liquid_fraction_is_zero(liquid_fraction: Any) -> bool:
    """Preserve legacy raw ``liquid_fraction == 0.0`` comparisons."""
    return liquid_fraction == 0.0


def melt_regime(
    *,
    temperature_K: float | None = None,
    solidus_K: float | None = None,
    liquid_fraction: float | None = None,
    epsilon: float = MELT_REGIME_EPSILON,
    solidus_boundary: Literal["frozen", "liquid"] = "frozen",
    invalid_liquid_fraction_regime: MeltRegime | None = None,
    diagnostic: MutableMapping[str, Any] | None = None,
    diagnostic_site: str | None = None,
    legacy_predicate: str | None = None,
) -> MeltRegime:
    """Classify melt regime with the simulator-wide boundary tolerance.

    ``MELT_REGIME_EPSILON`` is the one regime-membership tolerance. Legacy call
    sites that intentionally keep exact-zero or strict-solidus behavior pass
    ``epsilon=0.0``; when ``diagnostic`` is provided, this helper records cases
    where that preserved predicate diverges from the canonical default.

    ``solidus_boundary='frozen'`` treats the solidus as still frozen
    (``temperature <= solidus + epsilon``). ``'liquid'`` treats the solidus as
    the first liquid-bearing point (``temperature >= solidus - epsilon``), which
    preserves legacy ``temperature >= threshold`` gates.

    ``invalid_liquid_fraction_regime`` is only for legacy exact-zero routes
    whose old predicate treated numeric invalid liquid fractions as not-frozen
    by falling through. Non-numeric values still raise if legacy ``float()``
    would have raised. Canonical callers leave it unset and get strict
    validation.
    """

    effective = _classify_melt_regime(
        temperature_K=temperature_K,
        solidus_K=solidus_K,
        liquid_fraction=liquid_fraction,
        epsilon=epsilon,
        solidus_boundary=solidus_boundary,
        invalid_liquid_fraction_regime=invalid_liquid_fraction_regime,
    )
    if diagnostic is not None and diagnostic_site:
        canonical_error = None
        try:
            canonical = _classify_melt_regime(
                temperature_K=temperature_K,
                solidus_K=solidus_K,
                liquid_fraction=liquid_fraction,
                epsilon=MELT_REGIME_EPSILON,
                solidus_boundary="frozen",
            )
        except ValueError as exc:
            canonical = None
            canonical_error = str(exc)
        if canonical_error is not None or canonical != effective:
            divergence = {
                "site": diagnostic_site,
                "effective_regime": effective.value,
                "canonical_epsilon": MELT_REGIME_EPSILON,
                "effective_epsilon": float(epsilon),
            }
            if canonical is not None:
                divergence["canonical_regime"] = canonical.value
            if canonical_error is not None:
                divergence["canonical_error"] = canonical_error
            if legacy_predicate:
                divergence["legacy_predicate"] = legacy_predicate
            if liquid_fraction is not None:
                invalidity = _liquid_fraction_invalidity(liquid_fraction)
                if invalidity is None:
                    divergence["liquid_fraction"] = float(liquid_fraction)
                else:
                    divergence["liquid_fraction_invalid"] = invalidity
                    divergence["liquid_fraction_repr"] = repr(liquid_fraction)
            if temperature_K is not None:
                divergence["temperature_K"] = float(temperature_K)
            if solidus_K is not None:
                divergence["solidus_K"] = float(solidus_K)
            diagnostic.setdefault(
                "melt_regime_predicate_divergences",
                [],
            ).append(divergence)
    return effective


def _classify_melt_regime(
    *,
    temperature_K: float | None,
    solidus_K: float | None,
    liquid_fraction: float | None,
    epsilon: float,
    solidus_boundary: Literal["frozen", "liquid"],
    invalid_liquid_fraction_regime: MeltRegime | None = None,
) -> MeltRegime:
    eps = _finite_nonnegative(epsilon, "epsilon")
    if liquid_fraction is not None:
        try:
            fraction = _finite_float(liquid_fraction, "liquid_fraction")
        except (TypeError, ValueError):
            invalidity = _liquid_fraction_invalidity(liquid_fraction)
            if (
                invalid_liquid_fraction_regime is not None
                and invalidity != "not_numeric"
            ):
                return invalid_liquid_fraction_regime
            raise
        if fraction < 0.0 or fraction > 1.0:
            if invalid_liquid_fraction_regime is not None:
                return invalid_liquid_fraction_regime
            raise ValueError(
                "liquid_fraction must be within [0, 1], "
                f"got {liquid_fraction!r}"
            )
        if fraction <= eps:
            return MeltRegime.FROZEN
        if fraction >= 1.0 - eps:
            return MeltRegime.MOLTEN
        return MeltRegime.PARTIAL

    if temperature_K is None or solidus_K is None:
        raise ValueError(
            "melt_regime requires liquid_fraction or temperature_K+solidus_K"
        )
    temperature = _finite_float(temperature_K, "temperature_K")
    solidus = _finite_float(solidus_K, "solidus_K")
    if solidus_boundary == "frozen":
        return (
            MeltRegime.FROZEN
            if temperature <= solidus + eps
            else MeltRegime.PARTIAL
        )
    if solidus_boundary == "liquid":
        return (
            MeltRegime.FROZEN
            if temperature < solidus - eps
            else MeltRegime.PARTIAL
        )
    raise ValueError(f"unsupported solidus_boundary {solidus_boundary!r}")


def _finite_float(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return number


def _finite_nonnegative(value: float, name: str) -> float:
    number = _finite_float(value, name)
    if number < 0.0:
        raise ValueError(f"{name} must be nonnegative, got {value!r}")
    return number


def _liquid_fraction_invalidity(value: float) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "not_numeric"
    if not math.isfinite(number):
        return "non_finite"
    if number < 0.0 or number > 1.0:
        return "out_of_range"
    return None
