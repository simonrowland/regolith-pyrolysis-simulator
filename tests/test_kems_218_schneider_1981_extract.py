from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT, wt_pct_to_mole_fraction
from tests.literature_extract_test_helpers import (
    assert_no_unavailable_observation_values,
    iter_locators,
)


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/kems-218-schneider-1981-nbsir-81-2306.yaml"
)

# Independent transcription from the 600 dpi render of Table 1, physical PDF
# page 9. Initial compositions are sample metadata; final compositions and the
# initial mineral proportions are retained in the table context record.
TABLE1_RENDER = (
    (
        "KCAS-VP-1",
        (("K2O", "14.8"), ("CaO", "27.5"), ("Al2O3", "22.2"), ("SiO2", "35.5")),
        (("K2O", "1.6"), ("CaO", "31.8"), ("Al2O3", "25.7"), ("SiO2", "40.9")),
        (("KAlSiO4", "49.8"), ("Ca2SiO4", "16.5"), ("Ca2Al2SiO7", "16.5"), ("Ca3Si2O7", "17.2")),
    ),
    (
        "KCAS-VP-2",
        (("K2O", "11.5"), ("CaO", "17.6"), ("Al2O3", "33.7"), ("SiO2", "37.2")),
        (("K2O", "6.1"), ("CaO", "18.7"), ("Al2O3", "35.7"), ("SiO2", "39.5")),
        (("KAlSiO4", "28.6"), ("KAlSi2O6", "13.8"), ("Ca2Al2SiO7", "28.9"), ("CaAl2Si2O8", "28.7")),
    ),
    (
        "KCAS-CP-3",
        (("K2O", "12.5"), ("CaO", "28.0"), ("Al2O3", "13.5"), ("SiO2", "46.0")),
        (("K2O", "1.8"), ("CaO", "31.4"), ("Al2O3", "15.2"), ("SiO2", "51.6")),
        (("KAlSiO4", "42.0"), ("CaSiO3", "58.0")),
    ),
    (
        "KCAS-VP-4",
        (("K2O", "19.9"), ("CaO", "21.6"), ("Al2O3", "21.6"), ("SiO2", "37.0")),
        (("K2O", "2.7"), ("CaO", "26.2"), ("Al2O3", "26.2"), ("SiO2", "44.9")),
        (("KAlSiO4", "66.9"), ("Ca2SiO4", "33.1")),
    ),
)

# Independent transcription from the 600 dpi render of Table 2, physical PDF
# page 10. A's final positive sign is as printed in the rendered table.
TABLE2_RENDER = (
    ("KCAS-VP-1", "3/13/80", "14.8–12.4", "-20047", "391", "8.062", ".255"),
    ("KCAS-VP-1", "3/14/80", "12.4–9.3", "-19071", "524", "7.196", ".334"),
    ("KCAS-VP-1", "3/18/80", "9.3–7.2", "-17429", "452", "5.976", ".289"),
    ("KCAS-VP-1", "3/18/80", "6.2–4.8", "-16372", "210", "4.986", ".129"),
    ("KCAS-VP-1", "3/21/80", "4.8–4.0", "-16896", "279", "5.207", ".178"),
    ("KCAS-VP-2", "5/14/80", "11.4–10.7", "-14632", "434", "4.200", ".278"),
    ("KCAS-VP-2", "5/15/80", "10.7–8.6", "-16366", "180", "5.097", ".112"),
    ("KCAS-VP-2", "5/16/80", "8.6–6.5", "-17571", "257", "5.656", ".158"),
    ("KCAS-VP-2", "5/19/80", "6.5–6.2", "-16606", "526", "4.992", ".321"),
    ("KCAS-VP-3", "8/13/80", "12.5–10.8", "-15052", "775", "4.983", ".512"),
    ("KCAS-VP-3", "8/14/80", "10.8–10.3", "-17512", "453", "6.282", ".298"),
    ("KCAS-VP-3", "8/14/80", "10.3–7.4", "-15441", "612", "4.732", ".374"),
    ("KCAS-VP-3", "8/15/80", "7.4–6.0", "-16006", "581", "5.178", ".362"),
    ("KCAS-VP-3", "8/15/80", "6.0–3.9", "-15390", "320", "4.544", ".197"),
    ("KCAS-VP-3", "8/18/80", "3.9–3.2", "16948", "801", "5.417", ".505"),
)

TABLE3_RENDER = (
    ("A", "-16794", "176"),
    ("B", "5.228", "0.173"),
    ("C", "7.887 E-2", "0.309 E-2"),
    ("D", "1.741 E-3", "3.318 E-3"),
    ("E", "-1.825 E-2", "0.209 E-2"),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _table1_grid(document: dict) -> tuple:
    experiment_by_row = {
        experiment["locator"]["row"]: experiment
        for experiment in document["experiments"]
    }
    table_rows = document["species"]["K"]["context"][0]["values"]["rows"]
    grid = []
    for row in table_rows:
        experiment = experiment_by_row[row["sample_id"]]
        sample_composition = experiment["sample"]["initial_composition"]["state"]["value"]
        grid.append(
            (
                row["sample_id"],
                tuple((name, str(value)) for name, value in sample_composition["components"]),
                tuple((name, str(value)) for name, value in row["final_composition_wt_pct"].items()),
                tuple((name, str(value)) for name, value in row["mineral_constituents_wt_pct"].items()),
            )
        )
    return tuple(grid)


def _table2_rows(document: dict) -> tuple:
    context = next(
        item
        for item in document["species"]["K"]["context"]
        if item["observation_id"] == "schneider_1981_table2_k_pressure_fit_groups"
    )
    return tuple(
        (
            row["experiment"],
            row["date"],
            row["K2O_wt_pct_range"],
            row["A_as_printed"],
            row["SD_A_as_printed"],
            row["B_as_printed"],
            row["SD_B_as_printed"],
        )
        for row in context["values"]["rows"]
    )


def _assert_table1_matches(expected: tuple, actual: tuple) -> None:
    assert actual == expected


def test_table1_render_transcription_binds_each_sample_and_mutations_fail() -> None:
    document = _document()
    actual = _table1_grid(document)
    _assert_table1_matches(TABLE1_RENDER, actual)

    changed_render_pin = list(TABLE1_RENDER)
    changed_render_pin[0] = (
        "KCAS-VP-1",
        (("K2O", "14.9"), *TABLE1_RENDER[0][1][1:]),
        *TABLE1_RENDER[0][2:],
    )
    with pytest.raises(AssertionError):
        _assert_table1_matches(tuple(changed_render_pin), actual)

    changed_extract = deepcopy(document)
    changed_extract["experiments"][0]["sample"]["initial_composition"]["state"]["value"][
        "components"
    ][0][1] = "14.9"
    with pytest.raises(AssertionError):
        _assert_table1_matches(TABLE1_RENDER, _table1_grid(changed_extract))


def test_fitted_tables_match_rendered_rows_and_coefficients() -> None:
    document = _document()
    table2 = next(
        item
        for item in document["species"]["K"]["context"]
        if item["observation_id"] == "schneider_1981_table2_k_pressure_fit_groups"
    )
    assert _table2_rows(document) == TABLE2_RENDER
    assert table2["values"]["formula"] == "K"
    table3 = next(
        item
        for item in document["species"]["K"]["context"]
        if item["observation_id"] == "schneider_1981_table3_multivariable_k_pressure_fit"
    )
    coefficients = table3["values"]["coefficients"]
    assert table3["values"]["formula"] == "K"
    assert tuple(
        (term, values["value_as_printed"], values["standard_deviation_as_printed"])
        for term, values in coefficients.items()
    ) == TABLE3_RENDER
    assert len(_table2_rows(document)) == 15


def test_figures_are_deferred_and_migration_has_no_pressure_points() -> None:
    document = _document()
    figures = [
        item
        for item in document["species"]["K"]["context"]
        if item["type"] == "figure_record"
    ]
    assert len(figures) == 2
    expected_marker_counts = {"1": 17, "2": 13}
    for figure in figures:
        values = figure["values"]
        assert values["quantity"] == "partial_pressure"
        assert values["gas_species"] == "K"
        assert values["formula"] == "K"
        assert values["phase"] == "gas"
        assert values["temperature_as_printed"]
        assert values["axes_as_printed"]
        assert values["caption_as_printed"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert "points" not in values
        assert values["series"][0]["marker_count"] == expected_marker_counts[
            figure["locator"]["figure"]
        ]
        assert all("marker_count" in series for series in values["series"])

    locators = list(iter_locators(document))
    assert locators
    assert all(locator["page"] == locator["pdf_page_index"] + 1 for locator in locators)

    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == 0
    assert_no_unavailable_observation_values(result.observations.values())
    assert len(result.experiments) == 4
    experiment_ids = (
        "schneider_1981_kcas_vp_1",
        "schneider_1981_kcas_vp_2",
        "schneider_1981_kcas_cp_3",
        "schneider_1981_kcas_vp_4",
    )
    experiments = {
        key.split("::experiment::", 1)[1]: value
        for key, value in result.experiments.items()
    }
    for experiment_id, row in zip(experiment_ids, TABLE1_RENDER, strict=True):
        composition = experiments[experiment_id].sample.initial_composition
        assert composition is not None and composition.state.is_value
        printed_wt_pct = {
            name: Decimal(value) for name, value in row[1]
        }
        assert composition.state.value == wt_pct_to_mole_fraction(printed_wt_pct)
    assert sum(len(rows) for rows in result.context_by_work.values()) == 5
    assert not result.validation.hard_issues
    assert not result.registry_issues
