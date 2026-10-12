"""Golden pin for the pre-d-128 printed uncertainty conversion."""

from decimal import Decimal

from simulator.battery.enums import Quantity, UncertaintyKind
from simulator.battery.records import Uncertainty
from simulator.battery.score import _printed_uncertainty_band


def test_sigma_named_printed_width_uses_the_existing_q6_conversion() -> None:
    resolved = _printed_uncertainty_band(
        Quantity.DELTA_FG,
        Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim={"sigma_x": "0.1"},
        ),
        Decimal("1"),
    )

    assert resolved.band is not None
    assert resolved.band.value == Decimal("0.1") * Decimal("1.959963984540054")
