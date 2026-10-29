from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import re

import pytest
import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract

EXTRACT = REPO_ROOT / "data/literature/extracts/miao-li-yan-2023-fe-v-spinel-gibbs.yaml"
SOURCE_ID = "miao-li-yan-2023-fe-v-spinel-gibbs"

# Independent transcription of Table 3 from the 600 dpi physical page 6 render.
TABLE3_RENDER_GRID = """sample|Fe/V molar ratio|pO2 / atm|Fe mass % in Cu|xFe(Cu)|µFe − µθFe (J/mol)
1-1|1.6|2.39 × 10^-12|0.52|0.0059|−23,861.3
1-2|1.4|2.39 × 10^-12|0.45|0.0051|−25,630.8
1-3|1.2|2.39 × 10^-12|0.38|0.0043|−27,700.2
1-4|1.0|2.39 × 10^-12|0.35|0.0040|−28,706.8
1-5|0.8|2.39 × 10^-12|0.24|0.0027|−33,325.5
1-6|0.6|2.39 × 10^-12|0.18|0.0020|−36,847.6
2-1|1.8|9.57 × 10^-12|0.41|0.0047|−26,770.1
2-2|1.6|9.57 × 10^-12|0.36|0.0041|−28,362.0
2-3|1.4|9.57 × 10^-12|0.31|0.0035|−30,192.4
2-4|1.2|9.57 × 10^-12|0.24|0.0027|−33,325.5
2-5|1.0|9.57 × 10^-12|0.15|0.0017|−39,079.9
2-6|0.8|9.57 × 10^-12|0.08|0.0009|−46,777.0
3-1|2.8|3.83 × 10^-11|0.31|0.0035|−30,192.4
3-2|2.6|3.83 × 10^-11|0.28|0.0032|−31,438.4
3-3|2.2|3.83 × 10^-11|0.20|0.0023|−35,557.6
3-4|2.0|3.83 × 10^-11|0.19|0.0022|−36,185.6
3-5|1.6|3.83 × 10^-11|0.17|0.0019|−37,547.4
3-6|1.0|3.83 × 10^-11|0.08|0.0009|−46,777.0"""

TABLE5_MU_V_AS_PRINTED = [
    "-200496.5", "-197958.8", "-195406.8", "-192835.2", "-190234.6", "-187586.3",
    "-237836.0", "-234273.1", "-230479.1", "-226331.9", "-221577.7", "-215523.2",
    "-249849.2", "-247033.0", "-241291.1", "-238348.9", "-232253.9", "-222086.1",
]
TABLE6_DELTA_FG_AS_PRINTED = [
    "-930820.9", "-947729.6", "-967218.6", "-987740.2", "-1016918.9", "-1048605.1",
    "-927899.3", "-944122.7", "-962382.6", "-984614.1", "-1012433.4", "-1043021.7",
    "-851470.1", "-861457.0", "-887027.6", "-898199.8", "-924783.2", "-990774.3",
]


def _document() -> dict[str, object]:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _table_context(document: dict[str, object], observation_id: str) -> dict[str, object]:
    body = document["species"]["Fe"]
    matches = [row for row in body["context"] if row["observation_id"] == observation_id]
    assert len(matches) == 1
    return matches[0]


def _actual_table3_grid(document: dict[str, object]) -> list[list[str]]:
    experiments = {row["experiment_id"]: row for row in document["experiments"]}
    observations = {
        (row["values"]["sample_id_as_printed"], row["values"]["basis"]): row
        for row in document["species"]["Fe"]["observations"]
        if row["values"].get("quantity") == "equilibrium_phase_composition"
    }
    mu_fe = dict(_table_context(document, "miao_2023_table3_mu_fe_context")["values"]["rows_as_printed"])
    rows: list[list[str]] = []
    for sample in sorted({key[0] for key in observations}, key=lambda item: (int(item.split("-")[0]), int(item.split("-")[1]))):
        mass = observations[(sample, "wt_percent")]
        mole = observations[(sample, "mole_fraction")]
        sample_text = experiments[mass["experiment"]]["sample"]["printed_composition"]["state"]["value"]
        y = re.search(r"y = ([0-9.]+)", sample_text)
        assert y is not None
        pO2 = mass["values"]["pO2_as_published"].removesuffix(" atm")
        rows.append([
            sample,
            y.group(1),
            pO2,
            str(mass["values"]["component_amount"]),
            str(mole["values"]["component_amount"]),
            str(mu_fe[sample]),
        ])
    return rows


def _grid_lines(grid: str) -> list[list[str]]:
    rows = [line.split("|") for line in grid.strip().splitlines()]
    return [[cell.replace(",", "").replace("−", "-") for cell in row] for row in rows[1:]]


def _assert_table3_grid(document: dict[str, object], grid: str) -> None:
    assert _actual_table3_grid(document) == _grid_lines(grid)


def test_table3_complete_render_grid_and_mutations_fail() -> None:
    document = _document()
    _assert_table3_grid(document, TABLE3_RENDER_GRID)

    changed_grid = TABLE3_RENDER_GRID.replace("0.52", "0.51", 1)
    with pytest.raises(AssertionError):
        _assert_table3_grid(document, changed_grid)

    mutated = deepcopy(document)
    row = next(
        item for item in mutated["species"]["Fe"]["observations"]
        if item["observation_id"] == "miao_2023_table3_1_1_fe_mass_percent"
    )
    row["values"]["component_amount"] = "0.51"
    with pytest.raises(AssertionError):
        _assert_table3_grid(mutated, TABLE3_RENDER_GRID)


def test_all_typed_values_migrate_as_points_with_species_temperature_and_conditions() -> None:
    document = _document()
    source_rows = [
        (container, row)
        for container, body in document["species"].items()
        for row in body.get("observations", [])
    ]
    assert len(source_rows) == 39

    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index=None, aliases={})
    migrator.finalize()
    assert not migrator.result.registry_issues
    assert migrator.result.validation is not None
    assert not migrator.result.validation.hard_issues
    migrated = migrator.result.observations
    assert len(migrated) == len(source_rows)

    pO2_by_group = {
        "1": Decimal("2.4216675e-7"),
        "2": Decimal("9.6968025e-7"),
        "3": Decimal("3.8807475e-6"),
    }
    gas_mix_by_group = {"1": "2", "2": "1", "3": "0.5"}
    for container, raw in source_rows:
        values = raw["values"]
        observation = migrated[f"{SOURCE_ID}::{raw['observation_id']}"]
        assert observation.value.kind is ValueKind.POINT, raw["observation_id"]
        assert not observation.notices, raw["observation_id"]
        assert observation.identity.species.formula == str(values.get("formula") or container)
        if values["quantity"] == "equilibrium_phase_composition":
            assert observation.value.point == Decimal(str(values["component_amount"]))
            assert observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
            assert observation.identity.phase_role.value == "metal"
            assert raw["phase"] == "l"
            assert observation.identity.subtype.value == values["basis"]
            assert observation.identity.temperature_K.value == Decimal("1473")
            assert observation.point_conditions["temperature_K"].state.value == Decimal("1473")
            assert observation.point_conditions["pO2_Pa"].state.value == pO2_by_group[values["sample_id_as_printed"].split("-")[0]]
            group = values["sample_id_as_printed"].split("-")[0]
            assert observation.point_conditions["gas_mix"].state.value == f"CO/CO2 volume ratio {gas_mix_by_group[group]}"
            assert "fO2_log" not in values["point_conditions"]
        else:
            assert observation.identity.quantity.value is Quantity.DELTA_FG
            assert observation.value.point == Decimal(str(values["delta_fG"])) / Decimal("1000")
            assert observation.identity.temperature_K.value == Decimal("1473")


def test_context_tables_and_figure_records_preserve_untyped_numbers_and_discrepancy() -> None:
    document = _document()
    table2 = _table_context(document, "miao_2023_table2_single_phase_limits")
    assert table2["values"]["rows_as_printed"] == [
        ["2.39 × 10^-12", "0.524", "1.604"],
        ["9.57 × 10^-12", "0.565", "2.300"],
        ["3.83 × 10^-11", "0.567", "3.098"],
    ]
    table3 = _table_context(document, "miao_2023_table3_mu_fe_context")
    assert len(table3["values"]["rows_as_printed"]) == 18
    assert table3["values"]["rows_as_printed"][0] == ["1-1", "-23861.3"]
    table4 = _table_context(document, "miao_2023_table4_mu_fe_fits")
    assert table4["values"]["rows_as_printed"][1][1] == "µFe = µθFe + RT ln − 0.0488 + 0.092y"
    table5 = [
        row for row in document["species"]["Fe"]["context"]
        if row["observation_id"].startswith("miao_2023_table5_mu_v_page_")
    ]
    assert sum(len(row["values"]["rows_as_printed"]) for row in table5) == 18
    assert [cell[1] for page in table5 for cell in page["values"]["rows_as_printed"]] == TABLE5_MU_V_AS_PRINTED
    table6 = [
        row for row in document["species"]["Fe"]["context"]
        if row["observation_id"].startswith("miao_2023_table6_delta_fg_page_")
    ]
    assert sum(len(row["values"]["rows_as_printed"]) for row in table6) == 18
    assert [cell[1] for page in table6 for cell in page["values"]["rows_as_printed"]] == TABLE6_DELTA_FG_AS_PRINTED
    assert all("pending a corpus table asset" in row["values"]["schema_gap_or_disposition"] for row in table6)
    assert table6[0]["values"]["rows_as_printed"][0] == ["1-1", "-930820.9"]
    assert table6[-1]["values"]["rows_as_printed"][-1] == ["3-6", "-990774.3"]

    figures = [row for row in document["species"]["Fe"]["context"] if row["type"] == "figure_context"]
    assert {row["locator"]["figure"] for row in figures} == {"1", "2", "3"}
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("numeric_points" not in row["values"] for row in figures)
    fig2 = next(row for row in figures if row["locator"]["figure"] == "2")
    assert "2.83 × 10^-11 atm" in fig2["values"]["printed_discrepancy"]
    assert "3.83 × 10^-11 atm" in fig2["values"]["printed_discrepancy"]


def test_source_test_runs_without_corpus_files() -> None:
    # This file reads only the committed extract; migration's corpus lookup is optional.
    assert EXTRACT.is_file()
