from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator, REPO_ROOT
from tests.literature_extract_test_helpers import (
    assert_no_unavailable_observation_values,
    iter_locators,
)


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/kems-214-hastie-1980-nbsir-80-2178.yaml"
)

# Independently transcribed from the 600 dpi render of Table 3, physical PDF
# page 107. The three unreadable MgOH exponents stay as printed fragments.
TABLE3_RENDER = (
    ("2077", "1.48e-5", "7.56e-6", "7.42e-7", "1.35e-8"),
    ("2077", "1.66e-5", "1.01e-5", "4.84e-7", "1.53e-8"),
    ("2022", "6.10e-6", "1.47e-6", "9.73e-7", "3.34- ["),
    ("2022", "5.14e-6", "2.64e-7", "1.19e-6", "1.09- ["),
    ("2029", "6.92e-6", "1.63e-6", "1.07e-6", "4.50e-9"),
    ("2054", "9.73e-6", "1.47e-6", "1.75e-6", "6.16- ["),
    ("2054", "1.32e-5", "6.16e-6", "1.10e-6", "1.44e-8"),
    ("2054", "1.81e-5", "1.10e-5", "7.12e-7", "2.40e-8"),
    ("2054", "2.99e-5", "2.00e-5", "3.84e-7", "4.10e-8"),
    ("2054", "2.79e-5", "2.17e-5", "2.60e-7", "3.62e-8"),
    ("2107", "2.26e-5", "3.72e-6", "4.18e-6", "1.72e-8"),
    ("2107", "2.95e-5", "1.34e-5", "2.77e-6", "4.75e-8"),
    ("2098", "3.07e-5", "1.76e-5", "1.78e-6", "4.08e-8"),
    ("2167", "5.33e-5", "1.77e-5", "7.74e-6", "7.43e-8"),
    ("2167", "5.98e-5", "3.39e-5", "5.96e-6", "1.09e-7"),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _table3_grid(document: dict) -> tuple[tuple[str | None, ...], ...]:
    rows: list[list[str | None]] = [
        [None, None, None, None, None] for _ in TABLE3_RENDER
    ]
    species_columns = {"Mg": 1, "H2O": 2, "MgOH": 4}
    for species, column in species_columns.items():
        observation = next(
            item
            for item in document["species"][species]["observations"]
            if item["observation_id"] == f"hastie_1980_table3_{species}_partial_pressure"
        )
        for point in observation["values"]["points"]:
            row_index = int(point["locator"]["row"]) - 1
            rows[row_index][0] = str(point["T_K_as_printed"])
            rows[row_index][column] = str(point["pressure_atm"])
            if rows[row_index][3] is None:
                rows[row_index][3] = str(
                    point["point_conditions"]["oxygen_partial_pressure"]["value"]
                )

    for item in document["species"]["MgOH"]["context"]:
        if not item["observation_id"].startswith(
            "hastie_1980_appb_table3_MgOH_pressure_unreadable_"
        ):
            continue
        row_index = int(item["locator"]["row"]) - 1
        rows[row_index][0] = str(item["values"]["temperature_K_as_printed"])
        rows[row_index][4] = str(item["values"]["pressure_as_printed"])

    return tuple(tuple(row) for row in rows)


def _assert_table3_matches(expected, actual) -> None:
    assert actual == expected


def test_table3_transcription_and_both_cell_mutations_are_detected() -> None:
    document = _document()
    actual = _table3_grid(document)
    _assert_table3_matches(TABLE3_RENDER, actual)

    changed_render_pin = list(TABLE3_RENDER)
    changed_render_pin[0] = ("2077", "1.49e-5", *TABLE3_RENDER[0][2:])
    with pytest.raises(AssertionError):
        _assert_table3_matches(tuple(changed_render_pin), actual)

    changed_extract = deepcopy(document)
    changed_extract["species"]["Mg"]["observations"][0]["values"]["points"][0][
        "pressure_atm"
    ] = "1.49e-5"
    with pytest.raises(AssertionError):
        _assert_table3_matches(TABLE3_RENDER, _table3_grid(changed_extract))


def test_isolated_migration_pins_species_temperature_and_oxygen_conditions() -> None:
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    observations = tuple(result.observations.values())
    assert len(observations) == 43
    assert_no_unavailable_observation_values(observations)
    assert not result.validation.hard_issues
    assert not result.registry_issues

    expected_counts = {"K": 1, "Mg": 15, "H2O": 15, "MgOH": 12}
    for formula, expected_count in expected_counts.items():
        rows = [row for row in observations if row.identity.species.formula == formula]
        assert len(rows) == expected_count
        assert all(row.identity.quantity.value is Quantity.P_PARTIAL for row in rows)
        assert all(row.identity.species.phase.value is Phase.G for row in rows)
        assert all(row.identity.temperature_K.is_value for row in rows)
        assert all(row.evidence.class_.value is EvidenceClass.MEASURED_TABULATED for row in rows)
        assert all(row.point_conditions is not None for row in rows)
        assert all("temperature_K" in row.point_conditions for row in rows)
        assert all("oxygen_partial_pressure" in row.point_conditions for row in rows)
        assert all(
            row.point_conditions["oxygen_partial_pressure"].state.value.kind
            is ValueKind.POINT
            for row in rows
        )

    k_point = next(row for row in observations if row.identity.species.formula == "K")
    assert k_point.identity.temperature_K.value == Decimal("1466")
    assert k_point.point_conditions["oxygen_partial_pressure"].state.value.approximate


def test_deferred_figures_have_metadata_but_no_numeric_points() -> None:
    document = _document()
    figure_records = [
        record
        for block in document["species"].values()
        for record in block.get("context", [])
        if record.get("type") == "figure_record"
    ]
    assert len(figure_records) == 17
    for record in figure_records:
        values = record["values"]
        assert values["quantity"]
        assert values["species"]
        assert values["phase"]
        assert values["temperature_as_printed"]
        assert values["axes_as_printed"]
        assert values["caption_as_printed"]
        assert values["panel"]
        assert values["series"]
        assert values["attribution"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert "points" not in values

    locators = list(iter_locators(document))
    assert locators
    assert all(loc["page"] == loc["pdf_page_index"] + 1 for loc in locators)
    secondary = [
        point["also_reported_at"]
        for species in ("Mg", "H2O", "MgOH")
        for observation in document["species"][species]["observations"]
        for point in observation["values"]["points"]
        if "also_reported_at" in point
    ]
    assert len(secondary) == 3
    assert all(locator["table"] == "2" and locator["page"] == 106 for locator in secondary)


def test_k2o_activity_coefficient_uses_inferred_liquid_reference() -> None:
    document = _document()
    record = next(
        item
        for item in document["species"]["K2O"]["context"]
        if item["observation_id"] == "hastie_1980_appb_fig7_K2O_activity_coefficient"
    )
    standard_state = record["values"]["standard_state"]
    assert standard_state["phase"] == "liquid"
    assert standard_state["inferred"] is True
    assert "919 K" in standard_state["basis"]
    assert "Natola and Touzain" in standard_state["basis"]
    assert "1200–1900 K" in standard_state["basis"]
    assert "281 K" in standard_state["transition_notice"]
