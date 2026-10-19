from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator
from tests.literature_extract_test_helpers import iter_locators


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "majzlan-2021-magnesium-silicate-glass-cp"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"

# Table 2 values transcribed independently from the 600 dpi render of physical
# page 4; the formula column is cross-bound to the sample names in Table 1 on
# physical page 3.
TABLE_2_RENDER = [
    ("MG50", "Mg0.996SiO2.996", 80.08, 72.9, 0.9),
    ("MG54", "Mg1.174SiO3.174", 87.31, 78.5, 0.9),
    ("MG58", "Mg1.364SiO3.364", 94.36, 85.1, 1.0),
    ("MG62", "Mg1.611SiO3.611", 101.89, 91.4, 1.1),
    ("MG67", "Mg1.907SiO3.907", 115.68, 102.8, 1.2),
    ("enstatite", "MgSiO3", 82.05, 66.3, 0.1),
    ("forsterite", "Mg2SiO4", 118.59, 94.1, 0.1),
]
TABLE_1_RENDER = [
    ("MG50", 40.6, 0.3, 60.9, 0.9, 101.5, 1.0, 49.9, "Mg0.996SiO2.996", 100.2278),
    ("MG54", 44.6, 0.3, 56.7, 0.5, 101.4, 0.4, 54.0, "Mg1.174SiO3.174", 107.3982),
    ("MG58", 48.6, 0.4, 53.1, 0.7, 101.7, 0.6, 57.7, "Mg1.364SiO3.364", 115.0622),
    ("MG62", 52.6, 0.3, 48.7, 0.8, 101.3, 0.8, 61.7, "Mg1.611SiO3.611", 125.013),
    ("MG67", 56.7, 1.0, 44.3, 0.6, 101.1, 0.5, 65.6, "Mg1.907SiO3.907", 136.944),
]
TABLE_3_RENDER = [
    ("Forsterite", "Mg2SiO4", (271.3, -3379, 13.26, -28.72, 298, 1850), (-403.2, 74.34, 8.773, -2.597, 2.546, 298, 1850)),
    ("Orthoenstatite", "MgSiO3", (177.0, -1586, 0.2388, -1.574, 298, 1300), (102.4, 6.626, -2.455, 0.3839, -0.5085, 298, 1300)),
    ("Protoenstatite", "MgSiO3", None, (41.95, 12.24, -0.4960, 0, 0, 1300, 1810)),
    ("MG67 glass", "Mg1.907SiO3.907", (230.7, -1637, -4.108, 6.165, 298, 980), (-790.4, 123.7, 15.00, -4.136, 4.233, 298, 980)),
    ("MG50 glass", "Mg0.996SiO2.996", (160.7, -1011, -4.321, 6.661, 298, 980), (-437.7, 71.93, 9.230, -2.922, 3.335, 298, 980)),
]


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text())


def _table2_grid(document: dict) -> list[tuple[str, str, float, float, float]]:
    table1_formulas = {
        context["values"]["sample"]: context["values"]["formula"]
        for context in document["species"]["MgSiO3"]["context"]
        if context["locator"].get("table") == "1"
    }
    rows: dict[str, dict] = {}
    for body in document["species"].values():
        for observation in body.get("observations", []):
            if observation["locator"].get("table") != "2":
                continue
            values = observation["values"]
            if "sample" not in values:
                continue
            sample = values["sample"]
            if sample in table1_formulas:
                assert values.get("formula") == table1_formulas[sample]
            row = rows.setdefault(
                sample,
                {
                    "formula": values.get("formula", table1_formulas.get(sample)),
                    "page": observation["locator"]["page"],
                    "pdf_page_index": observation["locator"]["pdf_page_index"],
                },
            )
            if "cp_298_15_k" in values:
                row["cp"] = values["cp_298_15_k"]
            if "entropy" in values:
                row["entropy"] = values["entropy"]
                row["entropy_uncertainty"] = values["uncertainty"]
            assert row["formula"] == values.get("formula", table1_formulas.get(sample))
            assert row["page"] == 4 and row["pdf_page_index"] == 3

    assert set(rows) == {row[0] for row in TABLE_2_RENDER}
    return [
        (
            sample,
            rows[sample]["formula"],
            rows[sample]["cp"],
            rows[sample]["entropy"],
            rows[sample]["entropy_uncertainty"],
        )
        for sample, *_ in TABLE_2_RENDER
    ]


def _context_rows(document: dict, table_number: str) -> list[dict]:
    return [
        context
        for body in document["species"].values()
        for context in body.get("context", [])
        if context["locator"].get("table") == table_number
    ]


def _table1_grid(document: dict) -> list[tuple]:
    rows = {
        context["values"]["sample"]: context
        for context in _context_rows(document, "1")
    }
    result = []
    for sample, *_ in TABLE_1_RENDER:
        context = rows[sample]
        values = context["values"]
        assert context["locator"]["page"] == 3
        assert context["locator"]["pdf_page_index"] == 2
        result.append(
            (
                sample,
                values["MgO_wt_pct"]["value"],
                values["MgO_wt_pct"]["two_standard_deviations"],
                values["SiO2_wt_pct"]["value"],
                values["SiO2_wt_pct"]["two_standard_deviations"],
                values["sum_wt_pct"]["value"],
                values["sum_wt_pct"]["two_standard_deviations"],
                values["100Mg_over_Mg_plus_Si_molar_ratio"],
                values["formula"],
                values["formula_weight_g_per_mol"],
            )
        )
    return result


def _table3_grid(document: dict) -> list[tuple]:
    rows = {
        context["values"]["sample"]: context
        for context in _context_rows(document, "3")
    }

    def coefficients(values: dict, key: str, columns: tuple[str, ...]) -> tuple | None:
        group = values.get(key)
        if group is None:
            return None
        return tuple(group[column] for column in columns[:-1]) + tuple(group["T_range_K"])

    result = []
    for sample, *_ in TABLE_3_RENDER:
        context = rows[sample]
        values = context["values"]
        assert context["locator"]["page"] == 5
        assert context["locator"]["pdf_page_index"] == 4
        result.append(
            (
                sample,
                values["formula"],
                coefficients(
                    values,
                    "Berman_Brown_1985_equation_1",
                    ("k0", "k1", "k2_times_1e_minus_6", "k3_times_1e_minus_8", "T_range_K"),
                ),
                coefficients(
                    values,
                    "Richet_Fiquet_1991_equation_2",
                    ("a", "b", "c_times_1e_minus_4", "d_times_1e_minus_7", "e_times_1e_minus_9", "T_range_K"),
                ),
            )
        )
    return result


def _migrate() -> Migrator:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator


def test_render_transcribed_tables_bind_every_row_to_its_printed_sample() -> None:
    document = _document()
    assert _table1_grid(document) == TABLE_1_RENDER
    assert _table2_grid(document) == TABLE_2_RENDER
    assert _table3_grid(document) == TABLE_3_RENDER

    for locator in iter_locators(document):
        assert locator["page"] == locator["pdf_page_index"] + 1


def test_mutating_a_render_cell_or_extract_cell_breaks_table_binding() -> None:
    document = _document()
    changed_render = deepcopy(TABLE_2_RENDER)
    changed_render[0] = (*changed_render[0][:-1], 0.8)
    with pytest.raises(AssertionError):
        assert _table2_grid(document) == changed_render

    changed_extract = deepcopy(document)
    glass_cp = next(
        observation
        for observation in changed_extract["species"]["MgSiO3"]["observations"]
        if observation["observation_id"] == "majzlan_2021_mg50_table2_cp_29815"
    )
    glass_cp["values"]["cp_298_15_k"] = 80.09
    with pytest.raises(AssertionError):
        assert _table2_grid(changed_extract) == TABLE_2_RENDER

    changed_table1_render = deepcopy(TABLE_1_RENDER)
    changed_table1_render[0] = (*changed_table1_render[0][:-1], 100.0)
    with pytest.raises(AssertionError):
        assert _table1_grid(document) == changed_table1_render
    changed_table1_extract = deepcopy(document)
    mg50_assay = next(
        context
        for context in changed_table1_extract["species"]["MgSiO3"]["context"]
        if context["observation_id"] == "majzlan_2021_mg50_table1_glass_composition_context"
    )
    mg50_assay["values"]["MgO_wt_pct"]["value"] = 40.7
    with pytest.raises(AssertionError):
        assert _table1_grid(changed_table1_extract) == TABLE_1_RENDER

    changed_table3_render = deepcopy(TABLE_3_RENDER)
    changed_table3_render[0] = (
        changed_table3_render[0][0],
        changed_table3_render[0][1],
        (271.4, *changed_table3_render[0][2][1:]),
        changed_table3_render[0][3],
    )
    with pytest.raises(AssertionError):
        assert _table3_grid(document) == changed_table3_render
    changed_table3_extract = deepcopy(document)
    forsterite_fit = next(
        context
        for context in changed_table3_extract["species"]["Mg2SiO4"]["context"]
        if context["observation_id"] == "majzlan_2021_table3_forsterite_heat_capacity_fit_context"
    )
    forsterite_fit["values"]["Berman_Brown_1985_equation_1"]["k0"] = 271.4
    with pytest.raises(AssertionError):
        assert _table3_grid(changed_table3_extract) == TABLE_3_RENDER


def test_isolated_migration_preserves_species_and_temperature_conditions() -> None:
    document = _document()
    migrator = _migrate()
    rows = migrator.result.observations
    assert len(rows) == 23
    assert not migrator.result.validation.hard_issues

    expected_formulas = {
        observation["observation_id"]: values.get("formula", container)
        for container, body in document["species"].items()
        for observation in body.get("observations", [])
        for values in (observation["values"],)
    }
    source_observations = {
        observation["observation_id"]: observation
        for body in document["species"].values()
        for observation in body.get("observations", [])
    }
    for observation_id, migrated in rows.items():
        source_id = observation_id.split("::", maxsplit=1)[1]
        source = source_observations[source_id]
        values = source["values"]
        assert migrated.identity.species.formula == expected_formulas[source_id]

        conditions = migrated.point_conditions or {}
        assert "temperature_K" in conditions
        temperature = conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.locator is not None
        assert temperature.state.value == Decimal(
            str(
                source.get("point_conditions", {})
                .get("temperature_K", {})
                .get("state", {})
                .get("value", values.get("T_K", values.get("T_fus_K", {}).get("value")))
            )
        )

    for migrated in rows.values():
        if migrated.observation_id.split("::", maxsplit=1)[1].startswith("majzlan_2021_"):
            if "table2" in migrated.observation_id:
                assert migrated.identity.temperature_K.is_value
                assert migrated.identity.temperature_K.value == Decimal("298.15")
