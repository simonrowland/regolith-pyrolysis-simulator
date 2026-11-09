from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery import migrate as battery_migrate

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = REPO_ROOT / "data/literature/extracts/watne-1977-potassium-vapor-pressure-furnace.yaml"

# Independent transcriptions from the rendered source cells, each pinned to its PDF page.
RENDER_ANCHORS = (
    ("watne_1977_table12_row_02_absorbance", ".008±.0007", 112, "12", 4, "Absorbance"),
    ("watne_1977_table14_row_01_absorbance", "1.32*", 135, "14", 2, "Absorbance"),
    ("watne_1977_table15_row_20_absorbance", ">1.32*", 136, "15", 21, "Absorbance"),
    ("watne_1977_table16_row_23_absorbance", ".24", 141, "16", 24, "Absorbance"),
    ("watne_1977_table17_row_01_absorbance", ".0128", 142, "17", 2, "Absorbance"),
)
EXPECTED_TABLE_COUNTS = {"12": 7, "14": 22, "15": 26, "16": 26, "17": 15}


def _load_document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _source_observations(document):
    return {
        row["observation_id"]: row
        for row in document["species"]["K"]["observations"]
    }


def _render_anchor_grid(document):
    observations = _source_observations(document)
    return tuple(
        (
            obs_id,
            observations[obs_id]["values"]["value_as_printed"],
            observations[obs_id]["locator"]["page"],
            observations[obs_id]["locator"]["table"],
            observations[obs_id]["locator"]["table_row_number"],
            observations[obs_id]["locator"]["column"],
        )
        for obs_id, _value, _page, _table, _row, _column in RENDER_ANCHORS
    )


@pytest.fixture(scope="module")
def migrated():
    migrator = battery_migrate.Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator.result


def test_all_printed_table_rows_and_render_cells_are_pinned():
    document = _load_document()
    observations = _source_observations(document)
    table_counts = Counter(row["locator"]["table"] for row in observations.values())

    assert len(observations) == 96
    assert table_counts == EXPECTED_TABLE_COUNTS
    assert _render_anchor_grid(document) == RENDER_ANCHORS

    mutated = deepcopy(document)
    _source_observations(mutated)["watne_1977_table14_row_01_absorbance"]["values"][
        "value_as_printed"
    ] = "1.31*"
    assert _render_anchor_grid(mutated) != RENDER_ANCHORS


def test_isolated_migration_preserves_conditions_and_refuses_absorbance_as_pressure(migrated):
    result = migrated
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    rows = {row.observation_id.rsplit("::", 1)[-1]: row for row in result.observations.values()}
    source = _source_observations(_load_document())

    assert len(rows) == 96
    assert Counter(source[obs_id]["locator"]["table"] for obs_id in rows) == EXPECTED_TABLE_COUNTS
    unavailable_ids = {
        obs_id for obs_id, row in rows.items() if row.value.kind.value == "unavailable"
    }
    assert unavailable_ids == set(rows)

    quantity_unknown_ids = {
        obs_id
        for obs_id, row in rows.items()
        if row.identity.quantity is None or row.identity.quantity.is_unknown
    }
    assert quantity_unknown_ids == set(rows)
    phase_unknown_ids = {
        obs_id
        for obs_id, row in rows.items()
        if row.identity.species.phase is None or row.identity.species.phase.is_unknown
    }
    assert phase_unknown_ids == set()

    for obs_id, row in rows.items():
        table = source[obs_id]["locator"]["table"]
        conditions = row.point_conditions
        temperature_c = conditions["temperature_C"]
        assert temperature_c.state.is_value
        assert temperature_c.locator.page == source[obs_id]["locator"]["page"]
        assert temperature_c.locator.table == table

        composition = conditions["composition"]
        assert composition.state.is_value
        assert composition.locator.page == source[obs_id]["locator"]["page"]
        assert row.identity.composition is not None
        assert row.identity.composition.is_value

        source_conditions = source[obs_id]["point_conditions"]
        assert source_conditions["total_pressure_Pa"]["state"]["tag"] == "unknown"
        assert source_conditions["total_pressure_Pa"]["locator"]["page"] == source[obs_id]["locator"]["page"]
        assert source_conditions["fO2_log"]["state"]["tag"] == "unknown"
        assert source_conditions["fO2_log"]["locator"]["page"] == source[obs_id]["locator"]["page"]
        assert "total_pressure_Pa" not in conditions
        assert conditions["fO2_log"].state.is_unknown
        assert not row.identity.total_pressure_Pa or row.identity.total_pressure_Pa.is_unknown
        assert not row.identity.fO2_Pa or row.identity.fO2_Pa.is_unknown

        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value == battery_migrate.PHASE_MAP["gas"]
        if table == "12":
            assert "gas_mix" not in conditions
            assert conditions["temperature_K"].state.is_value
        else:
            assert conditions["gas_mix"].state.is_value
            assert conditions["gas_mix"].state.value == "Ar-4%H2"

    for obs_id in (
        "watne_1977_table12_row_02_absorbance",
        "watne_1977_table14_row_01_absorbance",
        "watne_1977_table16_row_23_absorbance",
    ):
        row = rows[obs_id]
        assert row.identity.composition.is_value
        assert row.identity.quantity.is_unknown
        assert row.identity.temperature_K is not None


def test_printed_calibration_and_fit_context_retains_sources_and_lineage():
    context = _load_document()["species"]["K"]["context"]
    calibration = next(row for row in context if row["observation_id"].endswith("equation_2"))
    assert calibration["values"]["slope_as_printed"] == "-21453"
    assert calibration["values"]["intercept_as_printed"] == "7.4866"
    assert calibration["values"]["pressure_unit_as_printed"] == "not stated"
    assert calibration["values"]["evidence_class"] == "quoted_attributed"

    equation_fits = next(
        row for row in context if row["observation_id"].endswith("equations_3_to_5_temperature_fits")
    )["values"]["equations"]
    assert [(row["equation"], row["slope_as_printed"], row["intercept_as_printed"]) for row in equation_fits] == [
        ("3", "-7950", "3.78"),
        ("4", "-22400", "14.1"),
        ("5", "-21453", "7.4866"),
    ]
    assert equation_fits[0]["parent_observation_ids"]
    assert equation_fits[2]["evidence_class"] == "quoted_attributed"
    assert equation_fits[1]["parent_observation_ids"] == [
        f"watne_1977_table12_row_{row:02d}_absorbance" for row in (2, 3, 4)
    ]

    fits = {
        row["locator"]["figure"]: row
        for row in context
        if row["type"] == "calibration_fit_summary" and "figure" in row["locator"]
    }
    assert fits["18"]["values"]["fits"][0]["slope_as_printed"] == "-21400"
    excluded_fit_rows = {
        "watne_1977_table14_row_01_absorbance",
        "watne_1977_table14_row_14_absorbance",
        "watne_1977_table15_row_20_absorbance",
        "watne_1977_table12_row_01_absorbance",
    }
    assert not excluded_fit_rows.intersection(
        fits["15"]["values"]["fits"][1]["parent_observation_ids"]
    )
    assert fits["18"]["values"]["fits"][0]["parent_observation_ids"] == [
        f"watne_1977_table14_row_{row:02d}_absorbance" for row in range(2, 14)
    ] + [f"watne_1977_table14_row_{row:02d}_absorbance" for row in range(15, 23)]
    assert not excluded_fit_rows.intersection(
        fits["18"]["values"]["fits"][0]["parent_observation_ids"]
    )
    table15_fit = fits["18"]["values"]["fits"][1]
    assert table15_fit["intercept_as_printed"] == "-5.42"
    assert table15_fit["intercept_caption_text_as_printed"] == "Intercept - 5.42"
    assert not excluded_fit_rows.intersection(table15_fit["parent_observation_ids"])
    assert fits["19"]["values"]["fits"][1]["intercept_as_printed"] == "9.26"
    assert len(fits["19"]["values"]["fits"][1]["parent_observation_ids"]) == 15
