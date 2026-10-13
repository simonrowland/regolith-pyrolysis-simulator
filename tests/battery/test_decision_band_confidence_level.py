"""Pin that q6 conversion follows the source-stated confidence level."""

from decimal import Decimal

from simulator.battery.enums import Quantity, UncertaintyKind
from simulator.battery.records import Uncertainty
from simulator.battery.score import _printed_uncertainty_band


def test_sigma_field_name_does_not_supply_confidence_level() -> None:
    resolved = _printed_uncertainty_band(
        Quantity.DELTA_FG,
        Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim={"sigma_x": "0.1", "confidence_level": "not_stated"},
        ),
        Decimal("1"),
    )

    assert resolved.band is not None
    assert resolved.band.value == Decimal("0.1")
    assert resolved.notice is not None
    assert resolved.notice.reason == "confidence_level_not_stated"


def test_confidence_metadata_preserves_units_printed_in_scalar_value() -> None:
    resolved = _printed_uncertainty_band(
        Quantity.P_PARTIAL,
        Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim={"value": "1 kPa", "confidence_level": "two_sigma"},
        ),
        Decimal("100000"),
    )

    assert resolved.band is not None
    assert resolved.band.value == Decimal("0.004364805402450084659744222203")
    assert resolved.band.unit == "dimensionless"


def test_confidence_metadata_does_not_hide_an_unmapped_printed_unit() -> None:
    resolved = _printed_uncertainty_band(
        Quantity.P_PARTIAL,
        Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim={"value": "1 furlong", "confidence_level": "two_sigma"},
        ),
        Decimal("100000"),
    )

    assert resolved.band is None
    assert resolved.refusal is not None
    assert resolved.refusal["reason"] == "printed_uncertainty_unit_unmapped"
