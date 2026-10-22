"""Migration pins for DeCapitani and Kirschen (1998), Table 1."""

from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "decapitani-kirschen-1998-excess-function"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"
THESIS_EXTRACT = ROOT / "data/literature/extracts/kirschen-1998-silicate-miscibility-thesis.yaml"
OBSERVATION_PREFIX = "decapitani_kirschen_1998_t1_"
OBSERVATION_SLUGS = {
    "STC 2-1a": "stc_2_1",
    "STC 3-3": "stc_3_3",
    "STC 3-4": "stc_3_4",
    "STC 3-6": "stc_3_6",
    "STC 4-11": "stc_4_11",
    "STC 5-11b": "stc_5_11",
    "STC 10-4": "stc_10_4",
    "STC 10-5": "stc_10_5",
    "STC 12-2": "stc_12_2",
    "STC 12-3": "stc_12_3",
    "STC 13-1": "stc_13_1",
    "STC 13-3": "stc_13_3",
    "STC 13-4": "stc_13_4",
    "STC 14-1": "stc_14_1",
    "STC 14-2": "stc_14_2",
}


# Independent transcription of the 600 dpi render of physical PDF page 6.
# Values are (printed total, n, {oxide: (wt%, printed 1σ)}), by liquid phase.
GRID = {
    "STC 2-1a": {
        "temperature": ("1599", "4"),
        "phases": [
            ("99.92", "15", {"CaO": ("0.82", "0.25"), "SiO2": ("92.51", "1.56"), "TiO2": ("6.59", "1.15")}),
            ("99.81", "45", {"CaO": ("18.80", "2.61"), "SiO2": ("44.66", "6.73"), "TiO2": ("36.36", "4.06")}),
        ],
    },
    "STC 3-3": {
        "temperature": ("1596", "8"),
        "phases": [
            ("99.75", "50", {"CaO": ("0.50", "0.04"), "SiO2": ("90.15", "0.57"), "TiO2": ("9.10", "0.25")}),
            ("100.77", "32", {"CaO": ("17.36", "2.07"), "SiO2": ("38.01", "2.51"), "TiO2": ("45.40", "3.82")}),
        ],
    },
    "STC 3-4": {
        "temperature": ("1603", "5"),
        "phases": [
            ("100.16", "39", {"CaO": ("0.49", "0.07"), "SiO2": ("90.99", "0.71"), "TiO2": ("8.67", "0.48")}),
            ("100.47", "35", {"CaO": ("16.67", "0.83"), "SiO2": ("36.89", "1.65"), "TiO2": ("46.91", "2.09")}),
        ],
    },
    "STC 3-6": {
        "temperature": ("1607", "3"),
        "phases": [
            ("100.14", "53", {"CaO": ("0.45", "0.11"), "SiO2": ("91.42", "1.46"), "TiO2": ("8.28", "1.02")}),
            ("100.02", "49", {"CaO": ("16.69", "1.95"), "SiO2": ("38.11", "2.94"), "TiO2": ("45.22", "3.30")}),
        ],
    },
    "STC 4-11": {
        "temperature": ("1610", "6"),
        "phases": [
            ("99.65", "47", {"CaO": ("0.48", "0.08"), "SiO2": ("90.09", "0.98"), "TiO2": ("9.09", "0.78")}),
            ("100.48", "50", {"CaO": ("14.69", "1.05"), "SiO2": ("34.55", "2.10"), "TiO2": ("51.24", "2.44")}),
        ],
    },
    "STC 5-11b": {
        "temperature": ("1600", "5"),
        "phases": [
            ("100.80", "50", {"CaO": ("0.27", "0.05"), "SiO2": ("90.32", "1.18"), "TiO2": ("10.22", "0.76")}),
            ("100.89", "53", {"CaO": ("13.71", "0.66"), "SiO2": ("28.96", "1.46"), "TiO2": ("58.22", "1.42")}),
        ],
    },
    "STC 10-4": {
        "temperature": ("1604", "9"),
        "phases": [
            ("99.91", "52", {"CaO": ("0.44", "0.07"), "SiO2": ("90.87", "1.06"), "TiO2": ("8.60", "0.77")}),
            ("100.75", "70", {"CaO": ("17.24", "1.18"), "SiO2": ("38.21", "3.05"), "TiO2": ("45.3", "3.49")}),
        ],
    },
    "STC 10-5": {
        "temperature": ("1583", "7"),
        "phases": [
            ("99.63", "48", {"CaO": ("0.46", "0.05"), "SiO2": ("90.54", "0.71"), "TiO2": ("8.63", "0.39")}),
            ("100.09", "72", {"CaO": ("16.94", "0.78"), "SiO2": ("38.44", "3.01"), "TiO2": ("44.72", "2.73")}),
        ],
    },
    "STC 12-2": {
        "temperature": ("1600", "7"),
        "phases": [
            ("98.39", "21", {"CaO": ("0.43", "0.07"), "SiO2": ("88.71", "0.87"), "TiO2": ("9.25", "0.84")}),
            ("100.19", "36", {"CaO": ("15.88", "1.01"), "SiO2": ("34.26", "2.70"), "TiO2": ("50.06", "3.40")}),
        ],
    },
    "STC 12-3": {
        "temperature": ("1596", "5"),
        "phases": [
            ("100.92", "43", {"CaO": ("0.38", "0.05"), "SiO2": ("90.86", "0.69"), "TiO2": ("9.68", "0.47")}),
            ("101.37", "22", {"CaO": ("15.67", "0.67"), "SiO2": ("34.19", "2.01"), "TiO2": ("51.51", "2.16")}),
        ],
    },
    "STC 13-1": {
        "temperature": ("1604", "10"),
        "phases": [
            ("100.73", "34", {"CaO": ("0.57", "0.10"), "SiO2": ("92.99", "1.09"), "TiO2": ("7.17", "0.97")}),
            ("100.22", "47", {"CaO": ("17.93", "0.95"), "SiO2": ("43.63", "2.85"), "TiO2": ("38.66", "1.88")}),
        ],
    },
    "STC 13-3": {
        "temperature": ("1604", "4"),
        "phases": [
            ("99.46", "46", {"CaO": ("0.55", "0.15"), "SiO2": ("91.83", "1.85"), "TiO2": ("7.08", "1.31")}),
            ("100.75", "45", {"CaO": ("18.99", "1.34"), "SiO2": ("41.41", "3.75"), "TiO2": ("40.36", "2.83")}),
        ],
    },
    "STC 13-4": {
        "temperature": ("1602", "7"),
        "phases": [
            ("99.69", "51", {"CaO": ("0.61", "0.05"), "SiO2": ("91.12", "0.87"), "TiO2": ("7.96", "0.24")}),
            ("100.07", "51", {"CaO": ("18.06", "2.95"), "SiO2": ("43.28", "7.70"), "TiO2": ("38.74", "5.18")}),
        ],
    },
    "STC 14-1": {
        "temperature": ("1602", "4"),
        "phases": [
            ("99.14", "44", {"CaO": ("0.33", "0.06"), "SiO2": ("88.95", "0.76"), "TiO2": ("9.87", "0.68")}),
            ("100.8", "50", {"CaO": ("14.19", "1.52"), "SiO2": ("29.75", "2.30"), "TiO2": ("56.86", "3.29")}),
        ],
    },
    "STC 14-2": {
        "temperature": ("1602", "5"),
        "phases": [
            ("99.70", "58", {"CaO": ("0.30", "0.05"), "SiO2": ("89.49", "1.35"), "TiO2": ("9.91", "0.63")}),
            ("100.67", "58", {"CaO": ("14.53", "1.35"), "SiO2": ("30.60", "3.28"), "TiO2": ("55.53", "3.57")}),
        ],
    },
}


def _migrated_rows():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues
    rows = {
        f"{SOURCE_ID}::{row['observation_id']}": (formula, row)
        for formula, species in document["species"].items()
        for row in species.get("observations", [])
    }
    assert len(rows) == len(result.observations) == 90
    assert len(document["experiments"]) == len(result.experiments) == 15
    return document, rows, result


def test_table_1_render_grid_migrates_all_components_and_conditions():
    document, rows, result = _migrated_rows()
    points = 0
    for sample_id, sample in GRID.items():
        temperature, temperature_sigma = sample["temperature"]
        slug = OBSERVATION_SLUGS[sample_id]
        for phase_order, (printed_sum, n_measurements, components) in enumerate(sample["phases"], 1):
            for formula, (amount, sigma) in components.items():
                observation_id = (
                    f"{OBSERVATION_PREFIX}{slug}_phase{phase_order}_{formula.lower()}"
                )
                raw_formula, raw = rows[f"{SOURCE_ID}::{observation_id}"]
                values = raw["values"]
                observation = result.observations[f"{SOURCE_ID}::{observation_id}"]
                assert raw_formula == "SiO2"  # The filing container carries the ternary system.
                assert values["formula"] == formula
                assert observation.identity.species.formula == formula
                assert observation.identity.quantity.value.value == "equilibrium_phase_composition"
                assert observation.identity.species.phase.value.value == "glass"
                assert observation.identity.phase_role.value.value == "glass"
                assert observation.identity.subtype.value == "wt_percent"
                assert observation.identity.temperature_K.value == Decimal(temperature) + Decimal("273.15")
                assert values["T_C"] == {
                    "value": temperature,
                    "units": "C",
                    "sigma": temperature_sigma,
                }
                assert values["temperature_as_published"] == f"{temperature} ± {temperature_sigma} °C"
                assert values["sample_id_as_printed"] == sample_id
                assert values["phase_order"] == phase_order
                assert observation.identity.total_pressure_Pa.value == Decimal("100000")
                assert observation.point_conditions["gas_mix"].state.value == "air"
                assert observation.point_conditions["printed_sum"].state.value == Decimal(printed_sum)
                assert values["printed_sum"] == printed_sum
                assert values["n_measurements"] == n_measurements
                assert values["component_amount"] == {"value": amount, "sigma": sigma}
                assert observation.value.kind.value == "point"
                assert Decimal(str(observation.value.point)) == Decimal(amount)
                assert sigma not in {"0", "0.0"}
                assert not observation.notices
                points += 1
    assert points == 90

    thesis = yaml.safe_load(THESIS_EXTRACT.read_text(encoding="utf-8"))
    linked_ids = {
        observation_id
        for row in thesis["species"]["SiO2"]["context"]
        if row.get("values", {}).get("published_source_id") == SOURCE_ID
        for observation_id in row["values"].get("published_observation_ids", [])
    }
    assert linked_ids == {row["observation_id"] for _formula, row in rows.values()}


def test_model_tables_and_deferred_figure_records_keep_printed_context():
    document, _rows, _result = _migrated_rows()
    contexts = document["species"]["SiO2"]["context"]
    by_id = {item["observation_id"]: item for item in contexts}
    table2 = by_id[f"{SOURCE_ID.replace('-', '_')}_table2_context"]["values"]
    table3 = by_id[f"{SOURCE_ID.replace('-', '_')}_table3_context"]["values"]
    table4 = by_id[f"{SOURCE_ID.replace('-', '_')}_table4_context"]["values"]
    assert len(table2["rows_as_printed"]) == 22
    assert table2["rows_as_printed"][0][3:5] == ["-907753.35", "43.3943"]
    assert table2["rows_as_printed"][2][3:5] == ["-4025841.41", "219.2922"]
    assert table2["rows_as_printed"][-3:] == [
        ["SiO2 liq", "", "SiO2", "-923136.00", "11.6534", "This study"],
        ["TiO2 liq", "", "TiO2", "-938809.39", "18.9283", "This study"],
        ["CaO liq", "", "CaO", "-542440.56", "50.3207", "This study"],
    ]
    assert table2["candidate_tokens_considered"] == ["delta_fH", "S"]
    assert len(table3["rows_as_printed"]) == 22
    assert table3["rows_as_printed"][2][1:5] == ["332.0488", "-401.712", "-10177120", "1114817696"]
    assert table3["rows_as_printed"][-3:] == [
        ["SiO2 liq", "85.7720", "0", "0", "0", "Chase et al. (1985)"],
        ["TiO2 liq", "100.4160", "0", "0", "0", "Chase et al. (1985)"],
        ["CaO liq", "62.7600", "0", "0", "0", "Chase et al. (1985)"],
    ]
    assert len(table4["rows_as_printed"]) == 11
    assert table4["rows_as_printed"][1:3] == [
        ["112", "-719241.09800", "-162.78172", "0"],
        ["122", "119289.99150", "99.23210", "0"],
    ]
    assert table4["rows_as_printed"][-1] == ["123", "0", "0", "0"]
    figures = [item for item in contexts if item.get("type") == "figure_context"]
    assert {item["locator"]["figure"] for item in figures} == {"1", "2", "3", "4", "5", "6"}
    assert len(figures) == 6
    for item in figures:
        assert item["values"]["quantity"] in {"equilibrium_phase_composition", "activity_coefficient"}
        assert item["values"].get("species_as_printed")
        assert item["values"].get("phase_role")
        assert item["values"].get("temperature_as_published")
        assert item["values"].get("axes_as_printed")
        assert item["values"]["digitisation"] == "deferred to a figure pass"
        assert all("marker_count" in series for series in item["values"]["series"])
    fig6 = next(item for item in figures if item["locator"]["figure"] == "6")
    assert [series["marker_count"] for series in fig6["values"]["series"]] == [26, 4, 6, 0]
    assert fig6["values"]["eutectic_temperatures_as_printed"]["values_C"] == {
        "L": "1365", "K": "1365", "J": "1318", "H": "1348", "F": "1398", "B": "1650"
    }
    duration_context = by_id[f"{SOURCE_ID.replace('-', '_')}_table1_duration_context"]["values"]
    assert duration_context["rows_as_printed"][0] == ["STC 2-1a", "0.5"]
    assert duration_context["rows_as_printed"][-1] == ["STC 14-2", "1.0"]
