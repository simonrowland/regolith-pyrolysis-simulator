"""Pin that q6 conversion follows the source-stated confidence level."""

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity, UncertaintyKind
from simulator.battery.records import Uncertainty
from simulator.battery.score import _printed_uncertainty_band


def test_d443_table_preserves_cited_precision_and_unstated_confidence() -> None:
    from simulator.battery.enums import MetricOperation
    from simulator.battery.score import _metric_uncertainty_width

    path = Path(__file__).resolve().parents[2] / "data/battery/method_class_bands.yaml"
    table = yaml.safe_load(path.read_text())
    classes = table["classes"]
    expected = {
        "kems_absolute_pressure_weight_loss_calibrated": ("0.20", "720", "0.096910013"),
        "kems_direct_comparison_activity": ("0.10", "724", "0.045757491"),
        "knudsen_weight_loss_collection_torsion_pressure": ("0.20", "720", "0.096910013"),
        "transpiration_mass_spectrometry_pressure": ("0.20", "721", "0.096910013"),
        "mossbauer_fe_glass_ferric_fraction": ("0.01", "117", None),
        "xanes_fe_glass_ferric_fraction": ("0.01", "114", None),
        "drop_calorimetry_heat_content_copper_block": ("0.003", "486", None),
    }
    assert table["ruling"] == "d-443"
    assert set(classes) == set(expected)
    for class_id, (precision, page, dex_width) in expected.items():
        entry = classes[class_id]
        assert entry["precision"]["value"] == precision
        assert entry["precision"]["confidence"] == "unstated"
        assert entry["citation"]["printed_page"] == page
        assert entry["citation"]["pdf_page"] > 0
        assert entry["applicability"]
        if dex_width is not None:
            width = _metric_uncertainty_width(
                MetricOperation.DEX, Quantity(entry["quantities"][0]),
                Decimal(1), Decimal(precision), "fraction",
            )
            assert width.quantize(Decimal("0.000000001")) == Decimal(dex_width)


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
    assert resolved.band.value.quantize(Decimal("1e-24")) == Decimal(
        "0.004364805402450084659744"
    )
    assert resolved.band.unit == "dimensionless"


def test_denial_that_uncertainty_is_not_printed_is_not_a_width() -> None:
    # Kress & Carmichael 1988 Table 1. The sentence names 0.8 and 8.0% as
    # the spread of major-element analyses and says the row uncertainty
    # itself was not printed. The first number is not the cell width.
    resolved = _printed_uncertainty_band(
        Quantity.FE3_FE2_RATIO,
        Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim=(
                "Row-specific uncertainties are not printed. The paper reports "
                "standard deviations of major-element analyses between 0.8 and "
                "8.0% relative."
            ),
        ),
        Decimal("0.1"),
    )

    assert resolved.band is None
    assert resolved.refusal is None
    assert resolved.notice is None


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
