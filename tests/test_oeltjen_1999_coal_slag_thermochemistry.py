from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery import migrate as battery_migrate
from simulator.battery.enums import (
    AmountBasis,
    EvidenceClass,
    Phase,
    Quantity,
    ReferenceStateConvention,
)
from simulator.battery.identity import celsius_to_kelvin
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
    assert len(result.observations) == 37
    assert sum(
        observation.value.kind.value == "unavailable"
        for observation in result.observations.values()
    ) == 0
    assert sum(map(len, result.context_by_work.values())) == 38

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
    digitized_figures = {
        row["locator"].get("figure")
        for row in figure_rows
        if row["values"]["numeric_values_digitized"] is True
    }
    assert digitized_figures == {"6.21", "6.22"}
    own_na_figure = next(row for row in figure_rows if row["locator"].get("figure") == "6.22")
    assert own_na_figure["locator"]["page"] == 73
    assert "0.05Na2O-0.725SiO2" in " ".join(own_na_figure["values"]["printed_sample_labels"])
    _assert_digitized_activities(document, contexts, result)

    assert vle.check_all_fidelity_samples_match(document, label=SOURCE_ID) == []
    mutated = deepcopy(document)
    mutated["species"]["K2O"]["context"][0]["values"]["printed_values"]["SiO2"] = "75.3"
    assert vle.check_all_fidelity_samples_match(mutated, label=f"{SOURCE_ID}-mutated")
    legend_mutated = deepcopy(document)
    legend_figure = next(
        row for row in legend_mutated["species"]["K2O"]["context"]
        if row["observation_id"] == "oeltjen_1999_figure_6_21"
    )
    legend_figure["values"]["printed_sample_labels"][0] = (
        "0.097K2O-0.729SiO2-0.174Al2O3 (9)"
    )
    assert vle.check_all_fidelity_samples_match(
        legend_mutated, label=f"{SOURCE_ID}-legend-mutated"
    )


_TABLE_5_1_MOLE_PERCENT = {
    "1": {"SiO2": "72.9", "Al2O3": "17.4", "K2O": "9.7"},
    "2": {"SiO2": "69.6", "Al2O3": "18.6", "CaO": "6.3", "K2O": "5.5"},
    "3": {"SiO2": "62.9", "Al2O3": "19.7", "FeO": "14.2", "K2O": "3.2"},
    "4": {"SiO2": "73.5", "Al2O3": "18.0", "Na2O": "8.5"},
    "5": {"SiO2": "59.6", "Al2O3": "11.6", "CaO": "24.3", "Na2O": "4.5"},
    "6": {"SiO2": "72.5", "Al2O3": "12.7", "FeO": "9.8", "Na2O": "5.0"},
}
_SPECIES_FOR_SAMPLE = {
    "1": "K2O",
    "2": "K2O",
    "3": "K2O",
    "4": "Na2O",
    "5": "Na2O",
    "6": "Na2O",
}
_FIGURE_FOR_SAMPLE = {"1": "6.21", "2": "6.21", "3": "6.21", "4": "6.22", "5": "6.22", "6": "6.22"}
_CALIBRATION_TICKS = {
    "6.21": {
        "x": (
            ("976.0", "0.48"),
            ("1520.0", "0.5"),
            ("2063.0", "0.52"),
            ("2606.0", "0.54"),
            ("3149.0", "0.56"),
            ("3689.0", "0.58"),
            ("4234.0", "0.6"),
        ),
        "y": (
            ("1478.5", "10^-11", "-11"),
            ("1887.0", "10^-12", "-12"),
            ("2300.0", "10^-13", "-13"),
            ("2711.0", "10^-14", "-14"),
            ("3125.0", "10^-15", "-15"),
            ("3535.5", "10^-16", "-16"),
        ),
    },
    "6.22": {
        "x": (
            ("888.0", "0.52"),
            ("1350.5", "0.54"),
            ("1809.0", "0.56"),
            ("2278.0", "0.58"),
            ("2737.5", "0.6"),
            ("3205.0", "0.62"),
            ("3664.0", "0.64"),
            ("4128.5", "0.66"),
        ),
        "y": (
            ("1490.5", "10^-10", "-10"),
            ("1999.5", "10^-11", "-11"),
            ("2509.0", "10^-12", "-12"),
            ("3022.0", "10^-13", "-13"),
            ("3530.0", "10^-14", "-14"),
        ),
    },
}


def _assert_digitized_activities(document: dict, contexts: list, result) -> None:
    source_rows = [
        row
        for formula in ("K2O", "Na2O")
        for row in document["species"][formula]["observations"]
    ]
    assert len(source_rows) == 37
    assert Counter(row["values"]["printed_series_numeral"] for row in source_rows) == Counter(
        {"1": 7, "2": 5, "3": 7, "4": 5, "5": 5, "6": 8}
    )
    figures = {
        row["locator"]["figure"]: row
        for row in contexts
        if row.get("type") == "figure_record" and row["locator"].get("figure") in {"6.21", "6.22"}
    }
    legends = {
        row["locator"]["figure"]: row
        for row in contexts
        if row.get("type") == "figure_marker_legend"
    }
    assert set(legends) == {"6.21", "6.22"}
    for figure, expected in _CALIBRATION_TICKS.items():
        record = figures[figure]["values"]
        assert record["render_dpi"] == 600
        assert record["read_uncertainty_rule"] == "half the marker bounding box in data units"
        assert record["model_curves_digitized"] is False
        assert len(record["x_calibration_ticks"]) >= 2
        assert len(record["y_calibration_ticks"]) >= 2
        assert [
            (tick["pixel"], tick["printed"]) for tick in record["x_calibration_ticks"]
        ] == list(expected["x"])
        assert [
            (tick["pixel"], tick["printed"], tick["log10"])
            for tick in record["y_calibration_ticks"]
        ] == list(expected["y"])
        pairings = {
            (item["marker_glyph"], item["printed_numeral"]): item["legend_label"]
            for item in legends[figure]["values"]["pairings"]
        }
        assert set(pairings.values()) == set(record["printed_sample_labels"])
    assert figures["6.22"]["values"]["excluded_non_data_markers"][0]["glyph"] == "square"

    migrated = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in result.observations.values()
    }
    assert set(migrated) == {row["observation_id"] for row in source_rows}
    by_sample: Counter[str] = Counter()
    for row in source_rows:
        numeral = row["values"]["printed_series_numeral"]
        figure = _FIGURE_FOR_SAMPLE[numeral]
        label = row["values"]["legend_label"]
        assert label in figures[figure]["values"]["printed_sample_labels"]
        assert label.endswith(f"({numeral})")
        pairings = legends[figure]["values"]["pairings"]
        match = [
            item for item in pairings
            if item["printed_numeral"] == numeral and item["marker_glyph"] == row["values"]["marker_glyph"]
        ]
        assert match == [
            item for item in pairings if item["legend_label"] == label
        ]
        assert row["values"]["figure_digitized"] is True
        assert row["values"]["quantity"] == "activity"
        assert row["values"]["method_class"] == "measured_reduced"
        assert row["standard_state"] == f"pure liquid {_SPECIES_FOR_SAMPLE[numeral]}"
        assert "stöchiometrischer Verdampfung" in row["derivation"]["relation"]
        assert "(5.1)" in row["derivation"]["relation"]
        assert "(5.4)" in row["derivation"]["relation"]
        assert "(5.6)" in row["derivation"]["relation"]
        assert "inputs" not in row["derivation"]
        assert row["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"
        assert row["derivation"]["raw_inputs"]["locator"]["equation"] == "5.6"
        read_log = Decimal(row["values"]["read_uncertainty_log10_activity"])
        assert Decimal("0.04") < read_log < Decimal("0.07")
        components = row["point_conditions"]["composition"]["state"]["value"]["components"]
        assert components == _TABLE_5_1_MOLE_PERCENT[numeral]
        observation = migrated[row["observation_id"]]
        by_sample[numeral] += 1
        assert observation.value.kind.value == "point"
        assert observation.value.point == Decimal(row["values"]["activity"])
        assert observation.identity.quantity.value is Quantity.ACTIVITY
        assert observation.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
        assert observation.identity.temperature_K.is_value
        temperature_c = Decimal(row["point_conditions"]["T_C"]["state"]["value"]["point"])
        assert observation.identity.temperature_K.value == celsius_to_kelvin(temperature_c)
        assert observation.point_conditions["temperature_K"].state.is_value
        composition = observation.point_conditions["composition"]
        assert composition.state.is_value
        assert composition.locator.page == 41
        assert composition.locator.table == "5.1"
        assert observation.identity.composition.is_value
        assert composition.state.value.amount_basis is AmountBasis.MOLE_FRACTION
        assert observation.identity.composition.value.as_map() == {
            name: Decimal(amount) / Decimal("100")
            for name, amount in _TABLE_5_1_MOLE_PERCENT[numeral].items()
        }
        reference = observation.identity.reference_state
        assert reference.is_value
        assert reference.value.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
        assert reference.value.endmember.formula == _SPECIES_FOR_SAMPLE[numeral]
        assert reference.value.endmember.phase.value is Phase.L
        assert observation.derivation is not None
        assert "stöchiometrischer Verdampfung" in observation.derivation.relation
    assert dict(by_sample) == {"1": 7, "2": 5, "3": 7, "4": 5, "5": 5, "6": 8}
    sample5 = [
        observation.identity.temperature_K.value
        for observation in migrated.values()
        if observation.experiment_id.endswith("oeltjen_1999_kems_sample_5")
    ]
    assert min(sample5) < Decimal("1530")
    assert min(sample5) > Decimal("1520")
