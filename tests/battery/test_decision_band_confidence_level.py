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
