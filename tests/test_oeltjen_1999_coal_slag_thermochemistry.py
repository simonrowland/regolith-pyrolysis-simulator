from __future__ import annotations

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

_EXPECTED_FIGURE_CONTEXT_IDS = {
    "oeltjen_1999_figure_6_1",
    "oeltjen_1999_figure_6_2",
    "oeltjen_1999_figure_6_3",
    "oeltjen_1999_figure_6_4",
    "oeltjen_1999_figure_6_6",
    "oeltjen_1999_figure_6_7",
    "oeltjen_1999_figure_6_8",
    "oeltjen_1999_figure_6_18",
    "oeltjen_1999_figure_6_19",
    "oeltjen_1999_figure_6_20",
    "oeltjen_1999_figure_6_21",
    "oeltjen_1999_figure_6_22",
    "oeltjen_1999_figure_6_23",
    "oeltjen_1999_figure_6_24",
    "oeltjen_1999_figure_6_25",
    "oeltjen_1999_figure_6_26",
    "oeltjen_1999_figure_6_27",
    "oeltjen_1999_figure_6_28",
    "oeltjen_1999_figure_6_29",
    "oeltjen_1999_figure_6_30",
    "oeltjen_1999_figure_6_31",
    "oeltjen_1999_figure_6_32",
    "oeltjen_1999_figure_6_33",
    "oeltjen_1999_figure_6_34",
    "oeltjen_1999_figure_6_35",
    "oeltjen_1999_figure_6_36",
    "oeltjen_1999_figure_6_37",
    "oeltjen_1999_figure_6_38",
    "oeltjen_1999_figure_6_39",
    "oeltjen_1999_figure_6_40",
}
_EXPECTED_MODEL_CONTEXT_IDS = {
    "oeltjen_1999_table4_1_sk1",
    "oeltjen_1999_table4_1_sk2",
    "oeltjen_1999_table4_1_sk3",
    "oeltjen_1999_table4_1_bk1",
    "oeltjen_1999_table4_1_bk2",
    "oeltjen_1999_table4_1_bk3",
}
_EXPECTED_CONTEXT_IDS = (
    _EXPECTED_FIGURE_CONTEXT_IDS
    | _EXPECTED_MODEL_CONTEXT_IDS
    | {
        "oeltjen_1999_figure_6_21_marker_legend",
        "oeltjen_1999_figure_6_22_marker_legend",
    }
)
_TABLE_4_1_MODEL_VALUES = {
    "SK1": {
        "SiO2": "75.2", "Al2O3": "22.6", "CaO": "-",
        "FeO": "-", "Na2O": "-", "K2O": "2.2",
    },
    "SK2": {
        "SiO2": "69.6", "Al2O3": "21.0", "CaO": "7.3",
        "FeO": "-", "Na2O": "-", "K2O": "2.1",
    },
    "SK3": {
        "SiO2": "64.8", "Al2O3": "19.5", "CaO": "-",
        "FeO": "13.7", "Na2O": "-", "K2O": "1.9",
    },
    "BK1": {
        "SiO2": "85.4", "Al2O3": "12.5", "CaO": "-",
        "FeO": "-", "Na2O": "2.1", "K2O": "-",
    },
    "BK2": {
        "SiO2": "61.3", "Al2O3": "9.0", "CaO": "28.2",
        "FeO": "-", "Na2O": "1.5", "K2O": "-",
    },
    "BK3": {
        "SiO2": "74.9", "Al2O3": "10.9", "CaO": "-",
        "FeO": "12.4", "Na2O": "1.8", "K2O": "-",
    },
}
_DIGITIZED_POINT_LABELS = {
    "oeltjen_1999_fig621_s1": (
        "p01", "p02", "p03", "p04", "p05", "p06", "p07"
    ),
    "oeltjen_1999_fig621_s2": ("p01", "p02", "p03", "p04", "p05", "p06"),
    "oeltjen_1999_fig621_s3": (
        "p01", "p02", "p03", "p04", "p05", "p06", "p07", "p08"
    ),
    "oeltjen_1999_fig622_s4": ("p01", "p02", "p03", "p04", "p05"),
    "oeltjen_1999_fig622_s5": ("p01", "p02", "p03", "p04", "p05"),
    "oeltjen_1999_fig622_s6": (
        "p01", "p02", "p03", "p04", "p05", "p06", "p07", "p08"
    ),
}
_EXPECTED_DIGITIZED_POINT_IDS = {
    f"{series}_{point}"
    for series, point_labels in _DIGITIZED_POINT_LABELS.items()
    for point in point_labels
}


def _load_extract() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_expected_context_ids(contexts: list) -> None:
    assert tuple(sorted(row["observation_id"] for row in contexts)) == tuple(
        sorted(_EXPECTED_CONTEXT_IDS)
    )


def _assert_expected_figure_ids(figure_rows: list) -> None:
    assert tuple(sorted(row["observation_id"] for row in figure_rows)) == tuple(
        sorted(_EXPECTED_FIGURE_CONTEXT_IDS)
    )


def _assert_expected_digitized_point_ids(source_rows: list) -> None:
    assert tuple(sorted(row["observation_id"] for row in source_rows)) == tuple(
        sorted(_EXPECTED_DIGITIZED_POINT_IDS)
    )


def _assert_model_system_printed_values(model_rows: list) -> None:
    assert {
        row["values"]["model_system"]: row["values"]["printed_values"]
        for row in model_rows
    } == _TABLE_4_1_MODEL_VALUES
    assert all(
        (row["locator"]["page"], row["locator"]["table"]) == (29, "4.1")
        for row in model_rows
    )


def test_oeltjen_tables_and_kems_registry_migrate_with_render_pins() -> None:
    document = _load_extract()
    migrator = battery_migrate.Migrator(ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert all(
        observation.value.kind.value != "unavailable"
        for observation in result.observations.values()
    )

    experiments = {
        item.experiment_id.rsplit("::", 1)[-1]: item
        for item in result.experiments.values()
    }
    assert set(experiments) == {
        f"oeltjen_1999_kems_sample_{number}"
        for number in _TABLE_5_1_MOLE_PERCENT
    }
    expected_temperatures = {
        1: ("1790", "1890"),
        2: ("1700", "1840"),
        3: ("1760", "2050"),
        4: ("1720", "1890"),
        5: ("1530", "1640"),
        6: ("1620", "1810"),
    }
    for number, interval in expected_temperatures.items():
        experiment = experiments[f"oeltjen_1999_kems_sample_{number}"]
        assert experiment.method.value is battery_migrate.MethodToken.KNUDSEN_EFFUSION
        composition = experiment.sample.printed_composition.state.value
        assert set(composition) == {"SiO2", "Al2O3", "CaO", "FeO", "Na2O", "K2O"}
        assert composition == {
            component: _TABLE_5_1_MOLE_PERCENT[str(number)].get(component, "-")
            for component in composition
        }
        temperature = experiment.conditions["temperature_K"]
        assert temperature.locator.page == 71
        assert temperature.state.value.interval_low == Decimal(interval[0])
        assert temperature.state.value.interval_high == Decimal(interval[1])
        assert set(experiment.conditions) == {"temperature_K"}
        assert experiment.pressure_environment.total_pressure_Pa.state.is_unknown
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
    _assert_expected_context_ids(contexts)
    assert tuple(
        sorted(source_id for work in result.works.values() for source_id in work.source_ids)
    ) == (SOURCE_ID,)
    assert tuple(
        sorted(bench.id.rsplit("::bench::", 1)[-1] for bench in result.benches.values())
    ) == ("oeltjen_1999_kems_bench",)
    model_rows = [
        row for row in contexts if row.get("observation_id", "").startswith("oeltjen_1999_table4_1_")
    ]
    _assert_model_system_printed_values(model_rows)

    # The cell and condition locator pairs are checked after isolated migration.
    bench = next(iter(result.benches.values()))
    assert bench.geometry.orifice_diameter_m.state.value.point == Decimal("0.0003")
    assert bench.geometry.orifice_diameter_m.locator.page == 42
    assert bench.geometry.cell_internal_dimensions["height_m"].locator.page == 42
    figure_rows = [row for row in contexts if row.get("type") == "figure_record"]
    _assert_expected_figure_ids(figure_rows)
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
    _assert_expected_digitized_point_ids(source_rows)
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
    for figure, figure_row in figures.items():
        assert figure_row["values"]["experimental_point_count"] == sum(
            row["locator"]["figure"] == figure for row in source_rows
        )
    for figure, expected in _CALIBRATION_TICKS.items():
        record = figures[figure]["values"]
        assert record["render_dpi"] == 600
        assert record["read_uncertainty_rule"] == "half the marker bounding box in data units"
        assert record["model_curves_digitized"] is False
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
    assert all(
        row["values"]["marker_glyph"] in {"circle", "square", "triangle"}
        for row in source_rows
        if row["locator"]["figure"] == "6.21"
    )
    assert figures["6.22"]["values"]["excluded_non_data_markers"][0]["glyph"] == "square"

    migrated = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in result.observations.values()
    }
    assert set(migrated) == _EXPECTED_DIGITIZED_POINT_IDS
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
    sample5 = [
        observation.identity.temperature_K.value
        for observation in migrated.values()
        if observation.experiment_id.endswith("oeltjen_1999_kems_sample_5")
    ]
    assert min(sample5) < Decimal("1530")
    assert min(sample5) > Decimal("1520")


# Centres re-read on the 600 dpi Figure 6.21 render. The opened-core centroid is the
# same centre used for the other stored points. Equality is the rounded calibration,
# which is tighter than the half-box read uncertainty.
_MARKER_CENTRE_PX = {
    "oeltjen_1999_fig621_s1_p01": (2316.31, 2046.99),
    "oeltjen_1999_fig621_s2_p03": (3123.50, 2889.50),
    "oeltjen_1999_fig621_s2_p04": (3367.03, 2991.66),
    "oeltjen_1999_fig621_s3_p04": (2012.07, 2543.19),
    "oeltjen_1999_fig621_s3_p08": (3366.12, 3035.00),
}


def test_stored_activities_match_marker_pixel_reads() -> None:
    document = _load_extract()
    figure = next(
        row
        for row in document["species"]["K2O"]["context"]
        if row["observation_id"] == "oeltjen_1999_figure_6_21"
    )
    values = figure["values"]
    slope_x = float(values["x_fit_slope_per_px"])
    intercept_x = float(values["x_fit_intercept"])
    slope_y = float(values["y_fit_slope_log10_per_px"])
    intercept_y = float(values["y_fit_intercept_log10"])
    rows = {
        row["observation_id"]: row
        for row in document["species"]["K2O"]["observations"]
    }
    for observation_id, (centre_x, centre_y) in _MARKER_CENTRE_PX.items():
        row = rows[observation_id]
        axis = f"{slope_x * centre_x + intercept_x:.5f}"
        log10 = f"{slope_y * centre_y + intercept_y:.4f}"
        assert row["values"]["axis_1000_per_K"] == axis
        assert row["values"]["log10_activity"] == log10
        half_box = Decimal(row["values"]["read_uncertainty_log10_activity"])
        assert abs(Decimal(row["values"]["log10_activity"]) - Decimal(log10)) < half_box
