from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest
import yaml

from simulator.battery.enums import Phase, PhaseRole, Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract


EXTRACT = REPO_ROOT / "data/literature/extracts/vickerfalt-2024-al-si-v-phase-diagram.yaml"
SOURCE_ID = "vickerfalt-2024-al-si-v-phase-diagram"

# Independent transcription from the 600 dpi renders of physical page 3.
# The sample column is vertically merged in the printed table; each expanded
# row below retains that group binding for comparison.
TABLE1_RENDER_GRID = """sample|Al2O3_mix|SiO2_mix|V2O3_mix|pO2_atm|phase|Al2O3|SiO2|V2O3
1|80|10|10|3.3 × 10^-10 atm|Mullite|64.6|27.2|8.3
1|80|10|10|3.3 × 10^-10 atm|Al2O3-corundum|87.5|0.1|12.5
2|82|13|5|3.3 × 10^-10 atm|Mullite|69.2|26.1|4.7
2|82|13|5|3.3 × 10^-10 atm|Al2O3-corundum|93.9|0.0|6.1
3|50|45|5|3.3 × 10^-10 atm|Mullite|64.5|28.5|7.0
3|50|45|5|3.3 × 10^-10 atm|Liquid|7.6|91.4|1.1
4|54|6|40|3.4 × 10^-11 atm|Mullite|63.7|24.8|11.5
4|54|6|40|3.4 × 10^-11 atm|Al2O3-corundum|73.6|0.1|26.3
4|54|6|40|3.4 × 10^-11 atm|V2O3-corundum|21.4|0.1|78.5
5|64|16|20|3.4 × 10^-11 atm|Mullite|63.2|25.7|11.1
5|64|16|20|3.4 × 10^-11 atm|Al2O3-corundum|75.1|0.1|24.8
5|64|16|20|3.4 × 10^-11 atm|V2O3-corundum|21.9|0.1|78.0
6|64|16|20|3.3 × 10^-10 atm|Mullite|61.7|26.9|11.4
6|64|16|20|3.3 × 10^-10 atm|Al2O3-corundum|75.0|0.1|24.9
6|64|16|20|3.3 × 10^-10 atm|V2O3-corundum|21.2|0.1|78.7
7|64|16|20|3.4 × 10^-9 atm|Mullite|62.7|26.1|11.2
7|64|16|20|3.4 × 10^-9 atm|Al2O3-corundum|75.6|0.1|24.4
7|64|16|20|3.4 × 10^-9 atm|V2O3-corundum|18.3|0|81.7
8|60|10|30|3.4 × 10^-9 atm|Mullite|62.3|26.6|11.2
8|60|10|30|3.4 × 10^-9 atm|Al2O3-corundum|74.9|0.1|25.1
8|60|10|30|3.4 × 10^-9 atm|V2O3-corundum|19.4|0|80.6
9|40|35|25|3.4 × 10^-11 atm|Mullite|61.9|25.7|12.4
9|40|35|25|3.4 × 10^-11 atm|V2O3-corundum|14.1|0.1|85.8
9|40|35|25|3.4 × 10^-11 atm|Liquid|24.1|72.3|3.5
10|28|32|40|3.4 × 10^-11 atm|Mullite|61.2|25.9|12.9
10|28|32|40|3.4 × 10^-11 atm|V2O3-corundum|14.1|0.1|85.8
10|28|32|40|3.4 × 10^-11 atm|Liquid|24.0|72.4|3.6
11|28|32|40|3.3 × 10^-10 atm|Mullite|57.6|28.6|13.9
11|28|32|40|3.3 × 10^-10 atm|V2O3-corundum|11.4|0.2|88.4
11|28|32|40|3.3 × 10^-10 atm|Liquid|14.3|83.0|2.8
12|28|32|40|3.3 × 10^-10 atm|Mullite|57.8|27.8|14.4
12|28|32|40|3.3 × 10^-10 atm|V2O3-corundum|11.1|0.1|88.7
12|28|32|40|3.3 × 10^-10 atm|Liquid|14.3|82.8|2.8
13|28|32|40|3.4 × 10^-9 atm|Mullite|57.0|28.3|14.7
13|28|32|40|3.4 × 10^-9 atm|V2O3-corundum|10.5|0.1|89.3
13|28|32|40|3.4 × 10^-9 atm|Liquid|6.3|92.0|1.7
14|40|35|25|3.4 × 10^-9 atm|Mullite|59.0|26.5|14.5
14|40|35|25|3.4 × 10^-9 atm|V2O3-corundum|10.3|0.1|89.6
14|40|35|25|3.4 × 10^-9 atm|Liquid|7.2|91.0|1.8
15|10|65|25|3.3 × 10^-10 atm|V2O3-corundum|10.5|0.4|89.1
15|10|65|25|3.3 × 10^-10 atm|Liquid|7.0|91.1|1.9
16|2|48|50|3.4 × 10^-11 atm|V2O3-corundum|4.1|0.2|95.7
16|2|48|50|3.4 × 10^-11 atm|Cristobalite|0.3|98.7|1.0
17|4|66|30|3.4 × 10^-11 atm|V2O3-corundum|7.7|0.3|92.0
17|4|66|30|3.4 × 10^-11 atm|Liquid|5.2|93.1|1.7
17|4|66|30|3.4 × 10^-11 atm|Cristobalite|0.2|99.3|0.5
18|5|55|40|3.3 × 10^-10 atm|V2O3-corundum|7.7|0.4|91.9
18|5|55|40|3.3 × 10^-10 atm|Liquid|5.0|93.6|1.4
18|5|55|40|3.3 × 10^-10 atm|Cristobalite|0.3|99.3|0.4
19|6|44|50|3.3 × 10^-10 atm|V2O3-corundum|8.6|0.2|91.3
19|6|44|50|3.3 × 10^-10 atm|Liquid|5.3|93.0|1.7
19|6|44|50|3.3 × 10^-10 atm|Cristobalite|0.1|99.2|0.6
20|6|54|40|3.4 × 10^-9 atm|V2O3-corundum|8.0|0.2|91.8
20|6|54|40|3.4 × 10^-9 atm|Liquid|5.5|93.1|1.4
20|6|54|40|3.4 × 10^-9 atm|Cristobalite|0.1|99.4|0.5
21|6|44|50|3.4 × 10^-9 atm|V2O3-corundum|8.4|0.3|91.3
21|6|44|50|3.4 × 10^-9 atm|Liquid|5.2|93.1|1.8
21|6|44|50|3.4 × 10^-9 atm|Cristobalite|0.1|99.2|0.7"""

PO2_ATM_BY_PA = {
    Decimal("0.00003343725"): "3.3 × 10^-10 atm",
    Decimal("0.00000344505"): "3.4 × 10^-11 atm",
    Decimal("0.000344505"): "3.4 × 10^-9 atm",
}


def _expected_rows(grid: str = TABLE1_RENDER_GRID) -> dict[tuple[str, str], dict[str, str]]:
    lines = [line.split("|") for line in grid.strip().splitlines()]
    assert lines[0] == [
        "sample", "Al2O3_mix", "SiO2_mix", "V2O3_mix", "pO2_atm", "phase", "Al2O3", "SiO2", "V2O3"
    ]
    return {
        (fields[0], fields[5]): dict(zip(lines[0], fields))
        for fields in lines[1:]
    }


def _actual_rows(document: dict[str, object]) -> dict[tuple[str, str], dict[str, str]]:
    result: dict[tuple[str, str], dict[str, str]] = {}
    species = document["species"]
    assert isinstance(species, dict)
    for body in species.values():
        assert isinstance(body, dict)
        for context in body.get("context", []):
            if context.get("type") != "table_context" or context["locator"].get("table") != "1":
                continue
            for row in context["values"]["rows_as_printed"]:
                key = (str(row["sample_id_as_printed"]), str(row["phase_as_printed"]))
                assert key not in result
                result[key] = {
                    "sample": key[0],
                    "Al2O3_mix": str(row["mixed_Al2O3_wt_percent"]),
                    "SiO2_mix": str(row["mixed_SiO2_wt_percent"]),
                    "V2O3_mix": str(row["mixed_V2O3_wt_percent"]),
                    "pO2_atm": str(row["pO2_atm_as_printed"]),
                    "phase": key[1],
                    "Al2O3": str(row["Al2O3_wt_percent"]),
                    "SiO2": str(row["SiO2_wt_percent"]),
                    "V2O3": str(row["V2O3_wt_percent"]),
                }

    liquids: dict[str, dict[str, str]] = {}
    for body in species.values():
        for observation in body.get("observations", []):
            if observation["locator"].get("table") != "1":
                continue
            sample = str(observation["experiment"]).rsplit("_", 1)[-1].lstrip("0")
            values = observation["values"]
            conditions = values["point_conditions"]
            charge = conditions["composition"]["state"]["value"]["components"]
            composition = {str(formula): str(amount) for formula, amount in charge}
            oxygen = Decimal(str(conditions["pO2_Pa"]["state"]["value"]))
            row = liquids.setdefault(sample, {
                "sample": sample,
                "Al2O3_mix": composition["Al2O3"],
                "SiO2_mix": composition["SiO2"],
                "V2O3_mix": composition["V2O3"],
                "pO2_atm": PO2_ATM_BY_PA[oxygen],
                "phase": "Liquid",
            })
            row[str(values["formula"])] = str(values["component_amount"])
    for sample, row in liquids.items():
        key = (sample, "Liquid")
        assert key not in result
        assert {"Al2O3", "SiO2", "V2O3"} <= row.keys()
        result[key] = row
    return result


def _assert_table1_matches_render(document: dict[str, object], grid: str = TABLE1_RENDER_GRID) -> None:
    expected = _expected_rows(grid)
    actual = _actual_rows(document)
    assert len(expected) == len(actual) == 58
    assert set(expected) == set(actual)
    for key in expected:
        assert actual[key] == expected[key]


def test_table1_full_grid_matches_independent_render_transcription() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_table1_matches_render(document)


def test_table1_render_grid_and_extract_cell_mutations_fail() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    changed_grid = TABLE1_RENDER_GRID.replace("64.6", "64.7", 1)
    with pytest.raises(AssertionError):
        _assert_table1_matches_render(document, changed_grid)

    mutated = deepcopy(document)
    row = next(
        observation
        for body in mutated["species"].values()
        for observation in body.get("observations", [])
        if observation["observation_id"] == "vickerfalt_2024_table1_sample_03_liquid_al2o3"
    )
    row["values"]["component_amount"] = "7.7"
    with pytest.raises(AssertionError):
        _assert_table1_matches_render(mutated)


def test_every_liquid_component_migrates_with_printed_identity_and_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_rows = [
        (container, row)
        for container, body in document["species"].items()
        for row in body.get("observations", [])
    ]
    assert len(source_rows) == 39

    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index=None, aliases={})
    migrator.finalize()
    assert not migrator.result.registry_issues
    assert len(migrator.result.observations) == 39

    experiment_ids_by_sample: dict[str, set[str]] = {}
    for container, raw in source_rows:
        values = raw["values"]
        observation = migrator.result.observations[f"{SOURCE_ID}::{raw['observation_id']}"]
        conditions = values["point_conditions"]
        sample = str(raw["experiment"]).rsplit("_", 1)[-1].lstrip("0")
        formula = str(values["formula"])
        expected_pressure = Decimal(str(conditions["pO2_Pa"]["state"]["value"]))

        assert observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert observation.identity.species.formula == formula
        assert observation.identity.species.phase.value is Phase.L
        assert observation.identity.phase_role.value is PhaseRole.MELT
        assert observation.identity.subtype.value == "wt_percent"
        assert observation.identity.temperature_K.value == Decimal("1873")
        assert observation.identity.fO2_Pa.value == expected_pressure
        assert observation.point_conditions["temperature_K"].state.value == Decimal("1873")
        assert observation.point_conditions["temperature_K"].locator.page == 2
        assert observation.point_conditions["pO2_Pa"].state.value == expected_pressure
        assert observation.point_conditions["pO2_Pa"].locator.page == 3
        assert observation.point_conditions["composition"].locator.page == 3
        assert observation.identity.composition is not None
        assert observation.identity.composition.is_value
        assert observation.locator.page == 3
        assert observation.locator.table == "1"
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == Decimal(str(values["component_amount"]))
        assert not observation.notices
        assert "fO2_log" not in observation.point_conditions
        assert container in {"Al2O3", "SiO2", "V2O3"}
        experiment_ids_by_sample.setdefault(sample, set()).add(observation.experiment_id)

    assert len(experiment_ids_by_sample) == 13
    assert all(len(ids) == 1 for ids in experiment_ids_by_sample.values())


def test_table2_context_retains_all_replicates_and_printed_means() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = [
        row
        for row in document["species"]["Al2O3"]["context"]
        if row["locator"].get("table") == "2"
    ]
    assert len(rows) == 1
    printed = rows[0]["values"]["rows_as_printed"]
    assert len(printed) == 6
    assert [row["analyzed_Al2O3_wt_percent"] for row in printed] == [
        "62.3", "62.7", "63.2", "62.4", "62.5", "61.9"
    ]
    assert printed[0]["analysis_average_Al2O3_wt_percent"] == "62.7"
    assert printed[3]["analysis_average_SiO2_wt_percent"] == "26.6"
    assert all(row["average_repeated_in_table1"] for row in printed)


def test_six_figure_records_are_deferred_and_keep_series_metadata() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    figures = [
        row
        for body in document["species"].values()
        for row in body.get("context", [])
        if row.get("type") == "figure_context"
    ]
    assert len(figures) == 6
    assert len({row["observation_id"] for row in figures}) == 6
    assert {row["locator"]["figure"] for row in figures} == {"1", "2", "3", "4", "5", "6"}
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all(row["values"]["species"] and row["values"]["phase"] for row in figures)
    assert all(row["values"]["panels"] is not None for row in figures)
    assert all(row["values"]["series"] == list(row["values"]["marker_count_per_series"]) for row in figures)
    assert all(row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1 for row in figures)
