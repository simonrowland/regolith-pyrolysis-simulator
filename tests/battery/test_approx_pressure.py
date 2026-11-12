"""Approximate printed pressure is a band, not a decimal equality key.

The half-decade factor is the 50-significant-figure expansion of √10. Tests
check the stored edges against that expansion. They do not re-call the
constructor to obtain the expected edges.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, localcontext

import pytest

from simulator.battery.enums import (
    AmountBasis,
    IdentityEqualKind,
    Phase,
    Quantity,
    RefusalReason,
    ValueKind,
)
from simulator.battery.identity import (
    Exposure,
    Identity,
    IdentityRefusal,
    _activity_pressure_above_limit,
    _same_row_knudsen_residue_identity_equal,
    _values_compare,
    identity_equal,
    profile_for,
)
from simulator.battery.records import (
    ApproxPressure,
    ApproxPressureRefusal,
    BandBasis,
    Composition,
    Species,
    State,
    Value,
    approximate_pressure_from_printed_point,
    as_decimal,
)
from tests.battery.factories import activity_identity

# 50 significant figures of √10. Independent of the constructor.
_PUBLISHED_SQRT10_50 = Decimal(
    "3.1622776601683793319988935444327185337195551393252"
)
_ACTIVITY_LIMIT_PA = Decimal("999726.4829")
_HASHIMOTO_PA = Decimal("0.01333223684210526315789473684")
_RICHTER_PA = Decimal("0.0001333223684210526315789473684")


def _point(amount: str | Decimal, *, approximate: bool) -> Value:
    return Value(ValueKind.POINT, point=Decimal(amount), approximate=approximate)


def _ulp_step(value: Decimal, sign: int) -> Decimal:
    """One unit in the last place, kept. Default precision would round it away."""

    exponent = value.as_tuple().exponent
    assert isinstance(exponent, int)
    with localcontext() as ctx:
        ctx.prec = max(80, len(value.as_tuple().digits) + 2)
        return value + (sign * Decimal(1).scaleb(exponent))


def _band_bypassing_validation(
    point: str, low: str, high: str
) -> ApproxPressure:
    """A band the constructor refuses. The comparator must still not use ``==``."""

    band = ApproxPressure.__new__(ApproxPressure)
    object.__setattr__(band, "point", Decimal(point))
    object.__setattr__(band, "low", Decimal(low))
    object.__setattr__(band, "high", Decimal(high))
    object.__setattr__(band, "band_basis", BandBasis.PRINTED)
    object.__setattr__(band, "kind", "approximate_pressure")
    return band


def _residue(pressure: Decimal | ApproxPressure) -> Identity:
    composition = Composition(
        "oxides",
        (("SiO2", Decimal("0.5")), ("MgO", Decimal("0.5"))),
        AmountBasis.MOLE_FRACTION,
    )
    return Identity(
        quantity=Quantity.RESIDUE_COMPONENT_COMPOSITION,
        species=Species("SiO2", Phase.L),
        subtype=State.of("oxide_wt_percent"),
        temperature_K=State.of(Decimal("1973.15")),
        composition=State.of(composition),
        exposure=State.of(
            Exposure(
                area_m2=State.of(Decimal("0.0001")),
                duration_s=State.of(Decimal("60")),
            )
        ),
        sample_mass_kg=State.of(Decimal("0.001")),
        total_pressure_Pa=State.of(pressure),
        fO2_Pa=State.not_applicable("oxygen_unbuffered_vacuum"),
    )


def test_half_decade_ratio_matches_published_sqrt10() -> None:
    for point in (_HASHIMOTO_PA, _RICHTER_PA, Decimal("0.0001")):
        band = approximate_pressure_from_printed_point(
            _point(point, approximate=True)
        )
        assert band.point == point
        assert band.band_basis is BandBasis.HALF_DECADE_ORDER_OF_MAGNITUDE
        assert band.kind == "approximate_pressure"
        with localcontext() as ctx:
            ctx.prec = 50
            assert band.high / band.point == _PUBLISHED_SQRT10_50
            assert band.point / band.low == _PUBLISHED_SQRT10_50
        assert band.low < band.point < band.high


def test_printed_edges_stay_the_stated_interval() -> None:
    band = ApproxPressure(
        Decimal("10"),
        Decimal("9"),
        Decimal("11"),
        BandBasis.PRINTED,
    )
    assert band.low == Decimal("9")
    assert band.high == Decimal("11")
    assert band.band_basis is BandBasis.PRINTED
    assert band.point == Decimal("10")


def test_constructor_refuses_exact_bound_interval_and_non_point() -> None:
    with pytest.raises(ApproxPressureRefusal) as exact:
        approximate_pressure_from_printed_point(_point("1", approximate=False))
    assert exact.value.reason == "not_approximate"

    bound = Value(
        ValueKind.BOUND,
        bound_operator="<",
        bound_value=Decimal("0.0001"),
        approximate=True,
    )
    with pytest.raises(ApproxPressureRefusal) as bounds:
        approximate_pressure_from_printed_point(bound)
    assert bounds.value.reason == "not_a_point"

    interval = Value(
        ValueKind.INTERVAL,
        interval_low=Decimal("1"),
        interval_high=Decimal("2"),
        approximate=True,
    )
    with pytest.raises(ApproxPressureRefusal) as intervals:
        approximate_pressure_from_printed_point(interval)
    assert intervals.value.reason == "not_a_point"

    with pytest.raises(ApproxPressureRefusal) as scalar:
        approximate_pressure_from_printed_point(Decimal("1"))
    assert scalar.value.reason == "not_a_printed_value"


@pytest.mark.parametrize(
    "kwargs, reason",
    (
        (
            {
                "point": "0",
                "low": "0",
                "high": "1",
                "band_basis": BandBasis.PRINTED,
            },
            "non_positive_low",
        ),
        (
            {
                "point": "-1",
                "low": "-2",
                "high": "1",
                "band_basis": BandBasis.PRINTED,
            },
            "non_positive_low",
        ),
        (
            {
                "point": "1",
                "low": "2",
                "high": "3",
                "band_basis": BandBasis.PRINTED,
            },
            "point_outside_band",
        ),
        (
            {
                "point": "1",
                "low": "2",
                "high": "0.5",
                "band_basis": BandBasis.PRINTED,
            },
            "inverted_band",
        ),
        (
            {
                "point": "1",
                "low": "1",
                "high": "1",
                "band_basis": "guessed",
            },
            "unknown_band_basis",
        ),
        (
            {
                "point": "NaN",
                "low": "1",
                "high": "1",
                "band_basis": BandBasis.PRINTED,
            },
            "non_finite_edge",
        ),
        (
            {
                "point": "1",
                "low": "1",
                "high": "Infinity",
                "band_basis": BandBasis.PRINTED,
            },
            "non_finite_edge",
        ),
    ),
)
def test_band_construction_refuses(kwargs: dict[str, str], reason: str) -> None:
    with pytest.raises(ApproxPressureRefusal) as exc:
        ApproxPressure(
            Decimal(kwargs["point"]),
            Decimal(kwargs["low"]),
            Decimal(kwargs["high"]),
            kwargs["band_basis"],
        )
    assert exc.value.reason == reason


def test_as_decimal_refuses_a_band_and_identity_keeps_it() -> None:
    band = approximate_pressure_from_printed_point(
        _point(_HASHIMOTO_PA, approximate=True)
    )
    with pytest.raises(ApproxPressureRefusal) as exc:
        as_decimal(band)
    assert exc.value.reason == "approximate_pressure_not_a_decimal"

    kept = Identity(
        quantity=Quantity.VISCOSITY,
        species=None,
        total_pressure_Pa=State.of(band),
    )
    assert kept.total_pressure_Pa is not None
    assert kept.total_pressure_Pa.value == band

    coerced = Identity(
        quantity=Quantity.VISCOSITY,
        species=None,
        total_pressure_Pa=State.of("101325"),
    )
    assert coerced.total_pressure_Pa is not None
    assert coerced.total_pressure_Pa.value == Decimal("101325")
    assert type(coerced.total_pressure_Pa.value) is Decimal


def test_approximate_value_stuffed_into_identity_is_refused() -> None:
    with pytest.raises(IdentityRefusal) as exc:
        Identity(
            quantity=Quantity.VISCOSITY,
            species=None,
            total_pressure_Pa=State.of(_point("1", approximate=True)),
        )
    assert exc.value.reason is RefusalReason.INVALID_IDENTITY
    assert exc.value.field == "total_pressure_Pa"

    with pytest.raises(IdentityRefusal) as exact:
        Identity(
            quantity=Quantity.VISCOSITY,
            species=None,
            total_pressure_Pa=State.of(_point("1", approximate=False)),
        )
    assert exact.value.reason is RefusalReason.INVALID_IDENTITY


def test_band_on_another_axis_is_not_stripped_to_a_decimal() -> None:
    band = approximate_pressure_from_printed_point(
        _point("1", approximate=True)
    )
    with pytest.raises(ApproxPressureRefusal) as exc:
        Identity(
            quantity=Quantity.VISCOSITY,
            species=None,
            temperature_K=State.of(band),
        )
    assert exc.value.reason == "approximate_pressure_not_a_decimal"


def test_same_nominal_disjoint_bands_are_not_equal() -> None:
    """Fails if the predicate is ``point == point``."""

    left = _band_bypassing_validation("1", "0.1", "0.4")
    right = _band_bypassing_validation("1", "2", "4")
    with pytest.raises(ApproxPressureRefusal):
        ApproxPressure(Decimal("1"), Decimal("0.1"), Decimal("0.4"), BandBasis.PRINTED)
    outcome = _values_compare("total_pressure_Pa", left, right)
    assert outcome.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert outcome.fields == ("total_pressure_Pa",)


def test_overlapping_different_nominals_are_equal_including_the_endpoint() -> None:
    """Fails if the predicate is ``point == point``."""

    left = ApproxPressure(
        Decimal("1"), Decimal("0.5"), Decimal("1.5"), BandBasis.PRINTED
    )
    right = ApproxPressure(
        Decimal("2"), Decimal("1.5"), Decimal("3"), BandBasis.HALF_DECADE_ORDER_OF_MAGNITUDE
    )
    assert _values_compare("total_pressure_Pa", left, right).kind is (
        IdentityEqualKind.EQUAL
    )
    gap = ApproxPressure(
        Decimal("3"), Decimal("2"), Decimal("4"), BandBasis.PRINTED
    )
    assert _values_compare("total_pressure_Pa", left, gap).kind is (
        IdentityEqualKind.IDENTITY_MISMATCH
    )


def test_containment_is_closed_and_one_ulp_kills_decimal_equality() -> None:
    band = approximate_pressure_from_printed_point(
        _point(_HASHIMOTO_PA, approximate=True)
    )
    assert _values_compare("total_pressure_Pa", band, band.point).equal
    assert _values_compare("total_pressure_Pa", band.low, band).equal
    assert _values_compare("total_pressure_Pa", band, band.high).equal
    inside = _ulp_step(band.low, 1)
    outside = _ulp_step(band.low, -1)
    assert inside > band.low
    assert outside < band.low
    assert _values_compare("total_pressure_Pa", inside, band).equal
    outside_outcome = _values_compare("total_pressure_Pa", outside, band)
    assert outside_outcome.kind is IdentityEqualKind.IDENTITY_MISMATCH
    # Bare decimals do not grow a band.
    assert not _values_compare(
        "temperature_K", Decimal("1643"), Decimal("1643.15")
    ).equal
    assert _values_compare(
        "temperature_K", Decimal("1e5"), Decimal("100000")
    ).equal


def test_activity_omits_a_band_only_when_its_high_edge_is_at_or_below_the_limit() -> None:
    vacuum = approximate_pressure_from_printed_point(
        _point(_HASHIMOTO_PA, approximate=True)
    )
    assert vacuum.high <= _ACTIVITY_LIMIT_PA
    vacuum_identity = replace(
        activity_identity(), total_pressure_Pa=State.of(vacuum)
    )
    atmosphere = replace(
        activity_identity(), total_pressure_Pa=State.of(Decimal("101325"))
    )
    assert "total_pressure_Pa" not in profile_for(vacuum_identity).required
    assert not _activity_pressure_above_limit(vacuum_identity)
    # 101325 Pa is outside the Hashimoto band. Omission, not overlap, makes
    # these equal. A nominal ``==`` would mismatch.
    assert identity_equal(vacuum_identity, atmosphere).equal

    at_limit = ApproxPressure(
        _ACTIVITY_LIMIT_PA,
        _ACTIVITY_LIMIT_PA,
        _ACTIVITY_LIMIT_PA,
        BandBasis.PRINTED,
    )
    at_limit_identity = replace(
        activity_identity(), total_pressure_Pa=State.of(at_limit)
    )
    assert "total_pressure_Pa" not in profile_for(at_limit_identity).required

    above = ApproxPressure(
        _ACTIVITY_LIMIT_PA + Decimal("1"),
        _ACTIVITY_LIMIT_PA,
        _ACTIVITY_LIMIT_PA + Decimal("2"),
        BandBasis.PRINTED,
    )
    above_identity = replace(
        activity_identity(), total_pressure_Pa=State.of(above)
    )
    assert "total_pressure_Pa" in profile_for(above_identity).required
    assert _activity_pressure_above_limit(above_identity)
    outside = replace(
        activity_identity(),
        total_pressure_Pa=State.of(_ACTIVITY_LIMIT_PA - Decimal("1")),
    )
    mismatch = identity_equal(above_identity, outside)
    assert mismatch.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert "total_pressure_Pa" in mismatch.fields
    inside = replace(
        activity_identity(),
        total_pressure_Pa=State.of(_ACTIVITY_LIMIT_PA + Decimal("1")),
    )
    assert identity_equal(above_identity, inside).equal


def test_knudsen_residue_omits_the_band_and_ordinary_equality_does_not() -> None:
    band = approximate_pressure_from_printed_point(
        _point(_RICHTER_PA, approximate=True)
    )
    measured = _residue(band)
    other = _residue(Decimal("101325"))
    ordinary = identity_equal(measured, other)
    assert ordinary.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert "total_pressure_Pa" in ordinary.fields
    omitted = _same_row_knudsen_residue_identity_equal(measured, other)
    assert omitted.kind is IdentityEqualKind.EQUAL
