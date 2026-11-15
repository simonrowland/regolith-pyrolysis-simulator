"""Pin that q6 conversion follows the source-stated confidence level."""

from decimal import Decimal
from types import SimpleNamespace

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


def test_redox_printed_text_keeps_its_native_unit_and_stated_confidence() -> None:
    def observation(field: str):
        return SimpleNamespace(
            identity=None,
            locator=None,
            derivation=SimpleNamespace(
                output_unit="dimensionless",
                parameters=((f"original_{field}", None),),
            ),
        )

    cases = (
        (
            {
                "Fe3_over_FeT": (
                    "±0.018 (as printed; confidence level not specified in Table S-1)"
                )
            },
            "Fe3_over_FeT",
            "printed_unstated",
        ),
        (
            {"Fe3_over_FeT": "±0.015 (as printed; SD per Fig. 2 caption)"},
            "Fe3_over_FeT",
            "printed_1sigma",
        ),
        (
            {
                "kind": "printed",
                "verbatim": (
                    "The error of determination is approximately ±0.08 for all "
                    "glasses; statistic and confidence level are not stated."
                ),
            },
            None,
            "printed_unstated",
        ),
    )

    for verbatim, field, expected_rule in cases:
        uncertainty = Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim=verbatim,
        )
        resolved = _printed_uncertainty_band(
            Quantity.FE3_FE2_RATIO,
            uncertainty,
            Decimal("0.5"),
            source_observation=(
                observation(field) if field is not None else observation("Fe3_over_FeT")
            ),
        )

        assert resolved.refusal is None
        assert resolved.band is not None
        assert resolved.band.rule.startswith(f"{expected_rule}:")
        if expected_rule == "printed_1sigma":
            assert resolved.notice is None
        else:
            assert resolved.notice is not None
            assert resolved.notice.reason == "confidence_level_not_stated"


def test_activity_uncertainty_source_fields_use_existing_band_parser() -> None:
    cases = (
        ({"sigma": "0.07"}, "dimensionless activity, as printed"),
        (
            {
                "kind": "printed",
                "verbatim": "± 0.09",
                "value": "0.09",
                "basis": "absolute dimensionless activity",
            },
            "dimensionless",
        ),
        ({"absolute": "0.23", "note": "Printed as 0.51 ± 0.23."}, "dimensionless activity"),
        (
            {
                "absolute_activity": "0.0008",
                "units": "dimensionless",
                "verbatim": "(2.60 ± 0.08) × 10^-2",
            },
            "dimensionless",
        ),
        (
            {
                "log10_a_SiO2": "0.15",
                "note": "Authors estimate ±0.15 in log a_SiO2.",
            },
            "dimensionless",
        ),
    )

    for verbatim, output_unit in cases:
        basis = verbatim.get("basis") if isinstance(verbatim, dict) else None
        uncertainty = Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim=verbatim,
            basis=basis,
        )
        source = SimpleNamespace(
            identity=None,
            locator=None,
            derivation=SimpleNamespace(output_unit=output_unit, parameters=()),
        )

        resolved = _printed_uncertainty_band(
            Quantity.ACTIVITY,
            uncertainty,
            Decimal("0.5"),
            source_observation=source,
        )

        assert resolved.refusal is None
        assert resolved.band is not None
        assert resolved.band.band_basis == "row_printed"
        assert resolved.notice is not None
        assert resolved.notice.reason == "confidence_level_not_stated"

    log_activity = _printed_uncertainty_band(
        Quantity.ACTIVITY,
        Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim={
                "log10_a_SiO2": "0.15",
                "note": "Authors estimate ±0.15 in log a_SiO2.",
            },
        ),
        Decimal("0.00601"),
        source_observation=SimpleNamespace(
            identity=None,
            locator=None,
            derivation=SimpleNamespace(output_unit="dimensionless", parameters=()),
        ),
    )
    assert log_activity.band is not None
    assert log_activity.band.value == Decimal("0.15")


def test_nested_verbatim_without_a_printed_width_remains_unparseable() -> None:
    resolved = _printed_uncertainty_band(
        Quantity.FE3_FE2_RATIO,
        Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim={"kind": "printed", "verbatim": "All glasses are 0.08 Fe3 fraction."},
        ),
        Decimal("0.07"),
    )

    assert resolved.band is None
    assert resolved.refusal is not None
    assert resolved.refusal["reason"] == "unparseable_printed_uncertainty"
