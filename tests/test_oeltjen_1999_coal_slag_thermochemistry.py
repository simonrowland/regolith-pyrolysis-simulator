from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery import migrate as battery_migrate
from tools import validate_literature_extracts as vle


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/oeltjen-1999-coal-slag-thermochemistry.yaml"
SOURCE_ID = "oeltjen-1999-coal-slag-thermochemistry"


def _load_extract() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_oeltjen_tables_and_kems_registry_migrate_with_render_pins() -> None:
    document = _load_extract()
    migrator = battery_migrate.Migrator(ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.works) == 1
    assert len(result.benches) == 1
    assert len(result.experiments) == 6
    assert len(result.observations) == 0
    assert sum(
        observation.value.kind.value == "unavailable"
        for observation in result.observations.values()
    ) == 0
    assert sum(map(len, result.context_by_work.values())) == 36

    experiments = {
        item.experiment_id.rsplit("::", 1)[-1]: item
        for item in result.experiments.values()
    }
    assert set(experiments) == {
        f"oeltjen_1999_kems_sample_{number}" for number in range(1, 7)
    }
    expected_temperatures = {
        1: ("1790", "1890"),
        2: ("1700", "1840"),
        3: ("1760", "2050"),
        4: ("1720", "1890"),
        5: ("1530", "1640"),
        6: ("1620", "1810"),
    }
    numeric_cells = 0
    dash_cells = 0
    for number, interval in expected_temperatures.items():
        experiment = experiments[f"oeltjen_1999_kems_sample_{number}"]
        assert experiment.method.value is battery_migrate.MethodToken.KNUDSEN_EFFUSION
        composition = experiment.sample.printed_composition.state.value
        assert set(composition) == {"SiO2", "Al2O3", "CaO", "FeO", "Na2O", "K2O"}
        numeric_cells += sum(value != "-" for value in composition.values())
        dash_cells += sum(value == "-" for value in composition.values())
        temperature = experiment.conditions["temperature_K"]
        assert temperature.locator.page == 71
        assert temperature.state.value.interval_low == Decimal(interval[0])
        assert temperature.state.value.interval_high == Decimal(interval[1])
        assert set(experiment.conditions) == {"temperature_K"}
        assert experiment.pressure_environment.total_pressure_Pa.state.is_unknown
    assert numeric_cells == 22
    assert dash_cells == 14

    # Render pins from Table 5.1, PDF page 41, Table 5.1.
    sample1 = experiments["oeltjen_1999_kems_sample_1"].sample.printed_composition
    sample5 = experiments["oeltjen_1999_kems_sample_5"].sample.printed_composition
    sample6 = experiments["oeltjen_1999_kems_sample_6"].sample.printed_composition
    assert (sample1.locator.page, sample1.locator.table) == (41, "5.1")
    assert sample1.state.value["SiO2"] == "72.9"
    assert sample1.state.value["K2O"] == "9.7"
    assert sample5.state.value["CaO"] == "24.3"
    assert sample6.state.value["Al2O3"] == "12.7"

    contexts = [row for rows in result.context_by_work.values() for row in rows]
    model_rows = [
        row for row in contexts if row.get("observation_id", "").startswith("oeltjen_1999_table4_1_")
    ]
    assert len(model_rows) == 6
    assert all((row["locator"]["page"], row["locator"]["table"]) == (29, "4.1") for row in model_rows)
    model_cells = [
        value
        for row in model_rows
        for value in row["values"]["printed_values"].values()
    ]
    assert sum(isinstance(value, str) and value != "-" for value in model_cells) == 22
    assert sum(value == "-" for value in model_cells) == 14
    assert next(
        row["values"]["printed_values"]["SiO2"]
        for row in model_rows
        if row["values"]["model_system"] == "SK1"
    ) == "75.2"

    # The cell and condition locator pairs are checked after isolated migration.
    bench = next(iter(result.benches.values()))
    assert bench.geometry.orifice_diameter_m.state.value.point == Decimal("0.0003")
    assert bench.geometry.orifice_diameter_m.locator.page == 42
    assert bench.geometry.cell_internal_dimensions["height_m"].locator.page == 42
    figure_rows = [row for row in contexts if row.get("type") == "figure_record"]
    assert len(figure_rows) == 30
    assert all(row["values"]["numeric_values_digitized"] is False for row in figure_rows)
    own_na_figure = next(row for row in figure_rows if row["locator"].get("figure") == "6.22")
    assert own_na_figure["locator"]["page"] == 73
    assert "0.05Na2O-0.725SiO2" in " ".join(own_na_figure["values"]["printed_sample_labels"])

    assert vle.check_all_fidelity_samples_match(document, label=SOURCE_ID) == []
    mutated = deepcopy(document)
    mutated["species"]["K2O"]["context"][0]["values"]["printed_values"]["SiO2"] = "75.3"
    assert vle.check_all_fidelity_samples_match(mutated, label=f"{SOURCE_ID}-mutated")
