from __future__ import annotations

import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import MethodToken, Quantity
from simulator.battery.validity import effusion_regime_unverified
from tests.helpers.corpus_paths import regolith_corpus_root

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/lopatin-2023-na-barium-borate-kems.yaml"
SID = "lopatin-2023-na-barium-borate-kems"
CORPUS = regolith_corpus_root()
TEXT = CORPUS / "text" / SID / "pdftotext-layout.txt"

TABLE_COLUMNS = {
    ("NaBO2", "activity"): "activity",
    ("B2O3", "activity"): "activity",
    ("BaB2O4", "activity"): "activity",
    ("NaBO2", "activity_coefficient"): "gamma",
    ("B2O3", "activity_coefficient"): "gamma",
    ("BaB2O4", "activity_coefficient"): "gamma",
}
UNPRINTED_PRESSURE_RECORDS = (
    "lopatin_2023_sample13_NaBO2_activity_coefficient",
    "lopatin_2023_sample13_B2O3_activity_coefficient",
    "lopatin_2023_table2_NaBO2_activity_coefficient",
    "lopatin_2023_table2_B2O3_activity_coefficient",
    "lopatin_2023_table2_BaB2O4_activity_coefficient",
)
UNPRINTED_PRESSURE_SAMPLE_PINS = (
    ("lopatin_2023_sample13_NaBO2_activity_coefficient", "13"),
    ("lopatin_2023_sample13_B2O3_activity_coefficient", "13"),
    ("lopatin_2023_table2_NaBO2_activity_coefficient", "1"),
    ("lopatin_2023_table2_B2O3_activity_coefficient", "1"),
    ("lopatin_2023_table2_BaB2O4_activity_coefficient", "38"),
)


def _assert_in_cell_pressure_is_typed_unknown(document: dict) -> None:
    records = {
        record["observation_id"]: record
        for species in document["species"].values()
        for record in species["observations"]
    }
    for record_id, sample in UNPRINTED_PRESSURE_SAMPLE_PINS:
        assert record_id in records
        point = next(
            point
            for point in records[record_id]["values"]["points"]
            if point["sample"] == sample
        )
        condition = point.get("point_conditions", {}).get("total_pressure_Pa")
        assert condition is not None
        assert condition["state"] == {"tag": "unknown", "reason": "not_published"}
        assert condition["locator"]["table"] == "2"
        assert condition["locator"]["published_page"] == 5
        assert (
            "without numerical sample-side partial pressures"
            in condition["locator"]["note"]
        )


def _migrated_named_row(observations: list, record_id: str, sample: str):
    prefix = f"{SID}::{record_id}::"
    return next(
        row
        for row in observations
        if row.observation_id.startswith(prefix)
        and row.locator is not None
        and f"row {sample};" in row.locator.record
    )


def _table2_from_pdf_text(text: str) -> dict[str, tuple[str, ...]]:
    table = text.split("Table 2\n", 1)[1]
    table = re.sub(r"(\d+(?:\.\d+)?)\s*×\s*10−\s*(\d+)", r"\1e-\2", table)
    rows = {}
    for line in table.splitlines():
        match = re.match(r"\s*(\d{1,2})\s+(.+?)\s*$", line)
        if not match:
            continue
        cells = tuple(match.group(2).split())
        if len(cells) == 8 and match.group(1) not in rows:
            rows[match.group(1)] = cells
            if len(rows) == 38:
                break
    assert len(rows) == 38
    return rows


def _assert_points_match_table(document: dict, table: dict[str, tuple[str, ...]]) -> None:
    cells = {}
    for row_num, row in table.items():
        cells[row_num] = dict(zip(
            ("a_NaBO2", "a_B2O3", "a_BaB2O4", "g_NaBO2", "g_B2O3", "g_BaB2O4", "minus_G", "minus_GE"),
            row,
        ))
    all_points = []
    for formula, species in document["species"].items():
        for observation in species["observations"]:
            quantity = observation["values"]["quantity"]
            field = TABLE_COLUMNS[(formula, quantity)]
            column = f"{'a' if quantity == 'activity' else 'g'}_{formula}"
            for point in observation["values"]["points"]:
                sample = point["sample"]
                printed = cells[sample][column]
                assert Decimal(point[field]) == Decimal(printed.replace("−", "-"))
                all_points.append((observation, point, field, printed))
    assert len(all_points) == 186
    # Independent render anchors from Table 2, physical PDF page 5.
    assert cells["1"]["a_NaBO2"] == "8.0e-3"
    assert cells["10"]["a_BaB2O4"] == "7.7e-4"
    assert cells["38"]["g_BaB2O4"] == "1.3e-2"


@pytest.mark.skipif(not TEXT.is_file(), reason="corpus source is absent")
def test_table2_points_migrate_with_compositions_and_deferred_figures(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_in_cell_pressure_is_typed_unknown(document)
    table = _table2_from_pdf_text(TEXT.read_text(encoding="utf-8"))
    _assert_points_match_table(document, table)

    # A changed PDF transcription or extract cell must fail the independent table audit.
    bad_table = dict(table)
    bad_table["1"] = ("8.1e-3", *table["1"][1:])
    with pytest.raises(AssertionError):
        _assert_points_match_table(document, bad_table)
    bad_document = deepcopy(document)
    bad_document["species"]["NaBO2"]["observations"][0]["values"]["points"][0]["activity"] = "0.009"
    with pytest.raises(AssertionError):
        _assert_points_match_table(bad_document, table)
    bad_pressure_document = deepcopy(document)
    next(
        record
        for record in bad_pressure_document["species"]["NaBO2"]["observations"]
        if record["observation_id"] == "lopatin_2023_table2_NaBO2_activity_coefficient"
    )["values"]["points"][0]["point_conditions"].pop("total_pressure_Pa")
    with pytest.raises(AssertionError):
        _assert_in_cell_pressure_is_typed_unknown(bad_pressure_document)

    from tests.battery.test_migrate import _migrate_real_extract

    migrated = _migrate_real_extract(tmp_path, EXTRACT.name)
    assert migrated.validation is not None
    assert not migrated.validation.hard_issues
    observations = [row for row in migrated.observations.values() if row.source_id == SID]
    assert len(observations) == 186
    assert all(row.value.kind.value == "point" for row in observations)
    assert all(row.identity.temperature_K.is_value and row.identity.temperature_K.value == Decimal("1330") for row in observations)
    for row in observations:
        assert "composition" in row.point_conditions
        assert row.point_conditions["composition"].state.is_value

    worklist_observations = [
        row
        for row in observations
        if any(
            row.observation_id.startswith(f"{SID}::{record_id}::")
            for record_id in UNPRINTED_PRESSURE_RECORDS
        )
    ]
    for row in worklist_observations:
        assert row.identity.total_pressure_Pa.is_unknown
        assert "total_pressure_Pa" not in (row.point_conditions or {})
        experiment = migrated.experiments[row.experiment_id]
        assert experiment.method.is_value
        assert experiment.method.value is MethodToken.KNUDSEN_EFFUSION
        gate = effusion_regime_unverified(
            experiment,
            Quantity.ACTIVITY_COEFFICIENT,
            observation=row,
            point_observations=observations,
        )
        assert not gate.passed
        assert gate.primary_check == "in_cell_partial_pressure_sum"
        pressure_check = next(
            check
            for check in gate.checks
            if check.name == "in_cell_partial_pressure_sum"
        )
        assert pressure_check.detail["reason"] == (
            "no admitted printed partial pressures at this exact point"
        )

    for record_id, sample in (
        ("lopatin_2023_table2_NaBO2_activity_coefficient", "1"),
        ("lopatin_2023_sample13_NaBO2_activity_coefficient", "13"),
        ("lopatin_2023_table2_B2O3_activity_coefficient", "1"),
        ("lopatin_2023_sample13_B2O3_activity_coefficient", "13"),
        ("lopatin_2023_table2_BaB2O4_activity_coefficient", "38"),
    ):
        row = _migrated_named_row(observations, record_id, sample)
        assert row.identity.total_pressure_Pa.is_unknown
        assert "total_pressure_Pa" not in (row.point_conditions or {})
    column_positions = {
        "a_NaBO2": 0, "a_B2O3": 1, "a_BaB2O4": 2,
        "g_NaBO2": 3, "g_B2O3": 4, "g_BaB2O4": 5,
    }
    for formula, species in document["species"].items():
        for source_observation in species["observations"]:
            quantity = source_observation["values"]["quantity"]
            field = TABLE_COLUMNS[(formula, quantity)]
            column = f"{'a' if quantity == 'activity' else 'g'}_{formula}"
            for point in source_observation["values"]["points"]:
                matches = [
                    row for row in observations
                    if row.observation_id.startswith(f"{SID}::{source_observation['observation_id']}::")
                    and row.locator is not None
                    and f"row {point['sample']};" in row.locator.record
                ]
                assert len(matches) == 1
                migrated_point = matches[0]
                expected = table[point["sample"]][column_positions[column]].replace("−", "-")
                assert migrated_point.value.point == Decimal(expected)
                assert migrated_point.identity.species.formula == formula

    sample13_rows = [
        (source_observation, point)
        for species in document["species"].values()
        for source_observation in species["observations"]
        for point in source_observation["values"]["points"]
        if point.get("sample") == "13"
    ]
    assert len(sample13_rows) == 4
    for source_observation, point in sample13_rows:
        assert source_observation["phase"] == "l"
        assert "phase_at_1330K_as_printed" not in point
        matches = [
            row for row in observations
            if row.observation_id.startswith(f"{SID}::{source_observation['observation_id']}::")
            and row.locator is not None
            and "row 13;" in row.locator.record
        ]
        assert len(matches) == 1
        assert matches[0].identity.species.phase.value.value == "l"

    figures = [record for species in document["species"].values() for record in species["context"] if record.get("type") == "figure_record"]
    assert {record["locator"]["figure"] for record in figures} == {str(i) for i in range(1, 8)}
    assert all(record["values"]["digitisation"] == "deferred to a figure pass" for record in figures)
    activity_figures = {record["locator"]["figure"]: record for record in figures if record["locator"]["figure"] in {"6", "7"}}
    assert {key: record["values"]["quantity"] for key, record in activity_figures.items()} == {"6": "activity", "7": "activity"}
