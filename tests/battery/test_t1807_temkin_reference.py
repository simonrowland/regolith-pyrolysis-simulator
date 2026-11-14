"""Regression pins for Distin's iron-saturated activity reference frame."""

from decimal import Decimal

from simulator.battery.enums import ReferenceStateConvention
from simulator.battery.records import StandardState
from simulator.battery.score import fusion_comparison_reference
from tests.battery.test_t1243_feo_reference import (
    _DISTIN,
    _conversion_notice,
    _feo_rows,
    _independent_factor,
)


def test_distin_current_reference_conversion_and_band_are_pinned(migration) -> None:
    """Pin t-1631's converter output before typing the convention as Temkin."""
    rows = tuple(row for row in _feo_rows(migration) if row.source_id == _DISTIN)
    assert len(rows) == 24
    row = next(
        item
        for item in rows
        if item.observation_id.endswith("distin1971_t1_1880_1_4")
    )
    standard = row.identity.reference_state.value
    assert isinstance(standard, StandardState)
    assert standard.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER

    converted = fusion_comparison_reference(row)
    notice = _conversion_notice(converted.notices)
    source, conversion = notice.reason.split("source_value=", 1)[1].split(
        "; converted_value=", 1
    )
    converted_value, _details = conversion.split("; factor=", 1)
    expected = Decimal(source) * _independent_factor(Decimal("2153.15"))
    assert Decimal(converted_value) == expected
    assert converted.value.point == expected

    saturation = standard.iron_saturation_composition
    assert saturation is not None
    assert saturation.n_fe2_plus == Decimal(
        "0.9861097841165973149618546920"
    )
    assert saturation.band_dex == Decimal(
        "0.007436620909554859980899190015"
    )
