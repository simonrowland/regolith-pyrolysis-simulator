"""Pins for Tatsumi et al. (2004), LPSC XXXV abstract 2013."""

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/tatsumi-2004-iron-condensation.yaml"
SOURCE = "tatsumi-2004-iron-condensation"

CONDITION_KEYS = (
    "substrate_distance_mm",
    "chamber_pressure_as_printed",
    "duration_as_printed",
    "substrate",
    "source_temperature_C",
    "condensation_area_mm2",
    "source_size_as_printed",
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> dict:
    rows = document["species"]["Fe"]["observations"]
    return {row["observation_id"]: row for row in rows}


def _assert_printed_coefficients_and_figure_reads(document: dict) -> None:
    observations = _observations(document)
    high = observations["tatsumi_2004_fe_condensation_coefficient_1020c"]
    low = observations["tatsumi_2004_fe_condensation_coefficient_415c"]
    assert high["values"]["value_as_printed"] == "0.5~0.6"
    assert low["values"]["value_as_printed"] == "~0.8"
    assert high["values"]["quantity"] == "condensation_coefficient"
    assert low["values"]["quantity"] == "condensation_coefficient"
    assert "alpha" not in high["values"]
    assert "alpha" not in low["values"]
    assert high["T_C"] == 1020
    assert low["T_C"] == 415
    assert high["point_conditions"]["substrate_distance_mm"]["state"]["value"] == 54
    assert low["point_conditions"]["substrate_distance_mm"]["state"]["value"] == 88
    assert high["point_conditions"]["chamber_pressure_as_printed"]["state"]["value"] == (
        "10^-5~10^-4 Pa"
    )

    context = document["species"]["Fe"]["context"]
    figures = [row for row in context if row["type"] == "figure_record"]
    assert [row["observation_id"] for row in figures] == [
        "tatsumi_2004_figure_1_weight_change",
        "tatsumi_2004_figure_2_sem",
        "tatsumi_2004_figure_3_grain_size",
    ]
    assert figures[1]["locator"]["page"] == 2
    assert figures[1]["locator"]["pdf_page_index"] == 1
    assert figures[2]["values"]["axes_as_printed"]["x_ticks"] == [0, 50, 100, 150]
    figure_1 = figures[0]["values"]["series"]
    circle = figure_1[0]["points"][0]
    assert circle["reading"] == "figure_digitised"
    assert circle["duration_h"] == 47.9
    assert circle["change_1em3_g"] == 5.56
    frame = figure_1[0]["points"][-1]
    assert frame["frame_contact"] is True
    assert frame["duration_h"] == 1.5
    assert figures[1]["values"]["digitisation"].startswith("Not digitised")
    grain = figures[2]["values"]["series"][0]["points"][0]
    assert grain["reading"] == "figure_digitised"
    assert grain["duration_h"] == 144.2
    assert grain["average_grain_size_um"] == 0.95
    shielded = next(
        row for row in context
        if row["observation_id"] == "tatsumi_2004_flux_fraction_and_shielded_run"
    )
    assert "wait gain" in shielded["values"]["shielded_run_as_printed"]
    assert shielded["values"]["direct_flux_fraction_as_printed"].startswith("1.8%")


def test_printed_coefficients_and_calibrated_figure_reads() -> None:
    _assert_printed_coefficients_and_figure_reads(_document())


def test_printed_coefficient_mutation_fails_the_pin() -> None:
    document = _document()
    document["species"]["Fe"]["observations"][0]["values"]["value_as_printed"] = "0.55"
    with pytest.raises(AssertionError):
        _assert_printed_coefficients_and_figure_reads(document)


def test_isolated_migration_refuses_coefficients_and_keeps_conditions() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues

    observations = {
        observation.observation_id: observation
        for observation in result.observations.values()
    }
    assert set(observations) == {
        f"{SOURCE}::tatsumi_2004_fe_condensation_coefficient_1020c",
        f"{SOURCE}::tatsumi_2004_fe_condensation_coefficient_415c",
    }
    expected_temperature = {
        f"{SOURCE}::tatsumi_2004_fe_condensation_coefficient_1020c": Decimal("1293.15"),
        f"{SOURCE}::tatsumi_2004_fe_condensation_coefficient_415c": Decimal("688.15"),
    }
    for observation_id, temperature_K in expected_temperature.items():
        observation = observations[observation_id]
        quantity = observation.identity.quantity
        assert quantity.is_unknown
        assert "condensation_coefficient" in (quantity.reason or "")
        assert observation.value.kind is ValueKind.UNAVAILABLE
        assert observation.evidence.class_.is_value
        assert observation.evidence.class_.value is EvidenceClass.AUTHOR_ESTIMATE
        assert observation.identity.temperature_K.is_value
        assert observation.identity.temperature_K.value == temperature_K
        conditions = observation.point_conditions or {}
        located_temperature = conditions["temperature_K"]
        assert located_temperature.state.is_value
        assert located_temperature.state.value == temperature_K
        assert located_temperature.locator is not None
        for key in CONDITION_KEYS:
            located = conditions[key]
            assert located.state.is_value, key
            assert located.locator is not None, key

    contexts = {
        row["observation_id"]: row
        for rows in result.context_by_work.values()
        for row in rows
    }
    assert len(contexts) == 6
    assert sum(row["type"] == "figure_record" for row in contexts.values()) == 3
    assert contexts["tatsumi_2004_figure_1_weight_change"]["values"]["series"][0][
        "points"
    ][0]["duration_h"] == 47.9
