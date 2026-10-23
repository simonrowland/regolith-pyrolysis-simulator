"""Render-grounded checks for Gaillard et al. (2003)."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / (
    "data/literature/extracts/gaillard-2003-hydrogen-iron-redox-rate.yaml"
)
SOURCE_ID = "gaillard-2003-hydrogen-iron-redox-rate"

# Independent transcription of Table 2 from the 600 dpi physical-page-3 render.
# Fields: run, printed fH2, log fO2, delta IW, duration (min), front (μm).
TABLE2_RENDER = """
1 0 -19.15 -0.36 4320 0
2 0 -19.15 -0.36 10080 0
3 0.02 -19.15 -0.36 1920 85
4 0.02 -19.15 -0.36 3288 110
5 0.25 -18.20 +0.6 2886 240
6 0.25 -18.20 +0.6 6060 320
7 0.5 -19.15 -0.36 180 125
8 0.5 -19.15 -0.36 390 205
9 0.5 -19.15 -0.36 2340 450
10 0.5 -19.15 -0.36 4020 605
11 0.5 -19.15 -0.36 7020 800
12 0.7/0.5 -22.10 -0.70 180 105
13 0.7/0.5 -22.10 -0.70 360 170
14 0.7/0.5 -22.07 -0.67 1320 310
15 0.7/0.5 -22.07 -0.67 4444 560
16 0.7/0.5 -21.17 -0.77 7295 730
17 0.67/0.5 -24.85 -0.25 360 140
18 0.67/0.5 -24.85 -0.25 1350 240
19 0.67/0.5 -24.60 0 4020 440
20 0.67/0.5 -24.60 0 5778 530
21 0.67/0.5 -26.4 +0.1 1200 170
22 0.67/0.5 -26.4 +0.1 2610 265
23 0.65/0.5 -28.5 +0.14 2400 185
24 0.65/0.5 -28.5 +0.14 4800 280
25 1 ? ? 4320 60
"""


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _render_table2() -> dict[int, tuple[str, str, str, int, int]]:
    return {
        int(fields[0]): (fields[1], fields[2], fields[3], int(fields[4]), int(fields[5]))
        for line in TABLE2_RENDER.strip().splitlines()
        if (fields := line.split())
    }


def _table2_from_extract(document: dict) -> dict:
    by_run = {}
    for observation in document["species"]["H2"]["observations"]:
        locator = observation["locator"]
        if locator.get("table") != "2":
            continue
        run = int(locator["row"])
        values = observation["values"]
        condition = observation["point_conditions"]["fO2_log"]["state"]
        delta_iw = document["species"]["Fe"]["context"][1]["values"]["rows"][run - 1]["fO2_delta_IW_as_printed"]
        by_run[run] = (
            values["value_as_published"],
            str(condition.get("value", "?")),
            "?" if delta_iw is None else str(delta_iw),
            int(document["species"]["Fe"]["context"][1]["values"]["rows"][run - 1]["duration_min"]),
            int(document["species"]["Fe"]["context"][1]["values"]["rows"][run - 1]["front_position_um"]),
        )
        if condition["tag"] == "unknown":
            by_run[run] = (by_run[run][0], "?", *by_run[run][2:])
    return by_run


def _assert_table2_matches_render(document: dict, expected: dict) -> None:
    assert len(expected) == 25
    assert _table2_from_extract(document) == expected
    for observation in document["species"]["H2"]["observations"]:
        if observation["locator"].get("table") != "2":
            continue
        run = int(observation["locator"]["row"])
        printed = expected[run][0]
        value = observation["values"]["value"]
        if "/" in printed:
            assert value == {
                "kind": "interval",
                "interval_low": "0.5",
                "interval_high": printed.split("/")[0],
            }
        else:
            assert str(value) == printed


def test_extract_validates_and_table2_matches_independent_render() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=SOURCE_ID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    assert check_all_fidelity_samples_match(document, label=SOURCE_ID) == []
    assert _table2_from_extract(document) == _render_table2()

    assert len(document["species"]["H2"]["observations"]) == 38
    assert len(document["species"]["Ar"]["observations"]) == 13
    assert len(document["species"]["Fe"]["observations"]) == 1
    assert len(document["species"]["Fe"]["context"][1]["values"]["rows"]) == 25
    assert len(document["species"]["Fe"]["context"][2]["values"]["rows"]) == 13
    assert len(document["species"]["Fe"]["context"][3]["values"]["rows"]) == 12

    run12 = next(
        row for row in document["species"]["H2"]["observations"]
        if row["observation_id"] == "gaillard_t2_run12_h2_fugacity"
    )
    assert run12["values"]["value"] == {
        "kind": "interval", "interval_low": "0.5", "interval_high": "0.7"
    }
    run3 = next(row for row in document["species"]["H2"]["observations"] if row["locator"].get("row") == "3")
    assert run3["locator"]["page"] == run3["locator"]["pdf_page_index"] + 1 == 3
    assert run3["point_conditions"]["fO2_log"]["state"]["value"] == "-19.15"
    assert run3["point_conditions"]["fO2_log"]["locator"]["note"].startswith(
        "log fO2 printed without a stated reference; read with the bar convention "
        "(atm would differ by 0.0057 dex)"
    )
    starting_glass = document["species"]["Fe"]["observations"][0]
    assert starting_glass["point_conditions"]["quench_state"]["state"] == {
        "tag": "value", "value": "not_stated"
    }

    figure_records = [
        row
        for body in document["species"].values()
        for row in body.get("context", [])
        if row.get("type") == "figure_record"
    ]
    assert len(figure_records) == 10
    assert all(row["values"]["numeric_points"] == "none" for row in figure_records)
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figure_records)
    assert all(
        isinstance(series["marker_count"], int)
        for record in figure_records
        for series in record["values"]["series"]
    )


def test_render_table_and_extract_cell_mutations_fail() -> None:
    document = _document()
    expected = _render_table2()
    _assert_table2_matches_render(document, expected)

    mutated_render = deepcopy(expected)
    mutated_render[3] = ("0.02", "-19.14", "-0.36", 1920, 85)
    with pytest.raises(AssertionError):
        _assert_table2_matches_render(document, mutated_render)

    mutated_extract = deepcopy(document)
    row3 = next(
        row for row in mutated_extract["species"]["H2"]["observations"]
        if row["observation_id"] == "gaillard_t2_run03_h2_fugacity"
    )
    row3["values"]["value"] = "0.03"
    with pytest.raises(AssertionError):
        _assert_table2_matches_render(mutated_extract, expected)


def test_isolated_migration_keeps_species_and_temperature_conditions() -> None:
    document = _document()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    assert len(migrator.result.observations) == 52

    species_by_id = {
        observation["observation_id"]: formula
        for formula, body in document["species"].items()
        for observation in body.get("observations", [])
    }
    for key, record in migrator.result.observations.items():
        observation_id = key.split("::", 1)[1].split("::", 1)[0]
        source = next(
            observation
            for body in document["species"].values()
            for observation in body.get("observations", [])
            if observation["observation_id"] == observation_id
        )
        assert record.identity.species.formula == species_by_id[observation_id]
        assert record.identity.temperature_K.is_value or record.identity.temperature_K.is_unknown
        assert record.point_conditions is not None
        if observation_id == "gaillard_2003_table1_fe3_over_fetot":
            assert record.point_conditions["quench_state"].state.value.value == "not_stated"
        assert all(
            isinstance(condition, dict)
            and set(("state", "locator")).issubset(condition)
            for condition in source["point_conditions"].values()
        )
        if source["type"] == "partial_pressure":
            assert record.value.kind.value == "point"
        if "T_C" not in source["values"]:
            temperature = source["point_conditions"]["temperature_K"]
            assert temperature["locator"]["page"] == temperature["locator"]["pdf_page_index"] + 1
