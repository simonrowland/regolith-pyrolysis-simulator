from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/tachibana-2002-enstatite-evaporation-kinetics.yaml"
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import validate_literature_extracts as vle  # noqa: E402


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_tachibana_2002_table3_values_match_600_dpi_render_pins() -> None:
    document = _document()
    assert vle.validate_extract_document(
        document,
        path=EXTRACT,
        expected_source_id="tachibana-2002-enstatite-evaporation-kinetics",
        check_fidelity_match=True,
    ) == []

    species = document["species"]["enstatite"]
    assert len(species["observations"]) == 6
    for observation in species["observations"]:
        assert len(observation["values"]["points"]) == 9
        assert all("T_C" in point for point in observation["values"]["points"])
        assert all(
            set(point["point_conditions"])
            == {"temperature_K", "duration_h", "hydrogen_partial_pressure_bar"}
            for point in observation["values"]["points"]
        )

    table3 = next(
        row
        for row in species["context"]
        if row["observation_id"] == "tachibana_2002_table3_hydrogen_conditions_and_raw_results"
    )
    assert len(table3["values"]["rows_as_printed"]) == 27
    assert table3["values"]["rows_as_printed"][22][7] == "24.2"
    assert table3["values"]["rows_as_printed"][22][8] == "0.01561 (436)"


def test_tachibana_2002_migration_preserves_conditions_for_every_row() -> None:
    document = _document()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert len(result.observations) == 54

    table3 = next(
        row
        for row in document["species"]["enstatite"]["context"]
        if row["observation_id"] == "tachibana_2002_table3_hydrogen_conditions_and_raw_results"
    )
    raw_rows = table3["values"]["rows_as_printed"]
    pressures = [
        Decimal(value.replace(" × 10^", "E"))
        for value in (
            table3["values"]["pressure_group_1_bar_as_printed"],
            table3["values"]["pressure_group_2_bar_as_printed"],
            table3["values"]["pressure_group_3_bar_as_printed"],
        )
    ]

    for observation in result.observations.values():
        conditions = observation.point_conditions
        assert conditions is not None
        assert set(conditions) == {
            "temperature_K",
            "duration_h",
            "hydrogen_partial_pressure_bar",
        }
        expected_row = observation.locator.note.removeprefix("row=")
        assert observation.locator.note == f"row={expected_row}"
        raw_row = raw_rows[int(expected_row) - 1]
        for key in conditions:
            assert conditions[key].state.is_value
            assert conditions[key].locator is not None
            assert conditions[key].locator.published_page == 721
            assert conditions[key].locator.table == "3"
        temperature = conditions["temperature_K"]
        assert temperature.state.value == observation.identity.temperature_K.value
        assert observation.identity.temperature_K.value == (
            Decimal(raw_row[0]) + Decimal("273.15")
        )
        assert conditions["duration_h"].state.value == Decimal(raw_row[1])
        pressure_index = (int(expected_row) - 1) // 9
        assert conditions["hydrogen_partial_pressure_bar"].state.value == pressures[
            pressure_index
        ]


def test_tachibana_2002_deferred_figures_are_context_only() -> None:
    document = _document()
    figures = [
        row
        for row in document["species"]["enstatite"]["context"]
        if row.get("type") == "figure_record"
    ]
    assert len(figures) == 10
    for figure in figures:
        values = figure["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["axes_as_printed"]
        assert values["series"]
        assert all("marker_count" in series for series in values["series"])
        assert "points" not in values


def test_tachibana_2002_fidelity_pin_rejects_a_mutated_table3_value() -> None:
    document = _document()
    assert vle.check_all_fidelity_samples_match(document, label="tachibana-2002") == []

    document["species"]["enstatite"]["observations"][0]["values"]["points"][0][
        "mass_loss_pct"
    ] = "7.8"
    assert vle.check_all_fidelity_samples_match(
        document, label="tachibana-2002-mutated"
    )
