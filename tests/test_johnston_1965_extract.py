from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator, to_plain


REPO = Path(__file__).resolve().parents[1]
EXTRACT = REPO / "data/literature/extracts/johnston-1965-sodium-disilicate-redox.yaml"

RENDER_PINS = {
    "johnston_1965_t1_ti_r07": (0, 0.218),
    "johnston_1965_t1_sn_r06": (1, 1.32),
    "johnston_1965_t1_ce_r02": (1, 3.20),
    "johnston_1965_t1_v_r04": (1, 0.14),
}


def _load_extract() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(doc: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for block in doc["species"].values()
        for row in block.get("observations", [])
    }


def _assert_render_pins(doc: dict) -> None:
    observations = _observations(doc)
    for observation_id, (point_index, expected) in RENDER_PINS.items():
        row = observations[observation_id]
        point = row["values"]["points"][point_index]
        assert point["value"] == expected
        assert row["locator"] == {
            "page": 3,
            "pdf_page_index": 2,
            "published_page": 186,
            "table": "I",
            "row": int(observation_id.rsplit("r", 1)[1])
            + {
                "ti": 0,
                "sn": 7,
                "ce": 13,
                "sb": 20,
                "mn": 25,
                "co": 32,
                "ni": 38,
                "v": 40,
            }[observation_id.split("_t1_", 1)[1].split("_", 1)[0]],
        }


def test_johnston_table_i_rows_conditions_and_render_pins() -> None:
    doc = _load_extract()
    observations = _observations(doc)
    table_i = [row for row in observations.values() if row["locator"].get("table") == "I"]
    table_ii = [row for row in observations.values() if row["locator"].get("table") == "II"]

    assert len(table_i) == 47
    assert len(table_ii) == 7
    assert sorted(row["locator"]["row"] for row in table_i) == list(range(1, 48))
    assert sum(len(row["values"]["points"]) for row in table_i) == 94
    for row in table_i:
        assert row["phase"] == "glass"
        assert len(row["values"]["points"]) == 2
        conditions = row["point_conditions"]
        assert {"T_C", "atmosphere", "fO2_Pa", "total_pressure_Pa", "composition"} <= set(conditions)
        assert all("state" in condition and "locator" in condition for condition in conditions.values())
        for point in row["values"]["points"]:
            assert point["T_C"] == row["values"]["T_C"]
            assert point["point_conditions"] == conditions

    for row in table_ii:
        assert len(row["values"]["points"]) == 1
        assert row["values"]["points"][0]["point_conditions"] == row["point_conditions"]

    assert sum(row["values"]["method_class"] == "model_derived" for row in table_ii) == 6
    assert sum(row["values"]["method_class"] == "quoted_attributed" for row in table_ii) == 1
    _assert_render_pins(doc)


def test_johnston_render_pin_fails_after_value_mutation() -> None:
    doc = _load_extract()
    observations = _observations(doc)
    observations["johnston_1965_t1_ti_r07"]["values"]["points"][0]["value"] = 0.219

    with pytest.raises(AssertionError):
        _assert_render_pins(doc)


def test_johnston_isolated_migration_preserves_point_conditions() -> None:
    migrator = Migrator(root=REPO)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    observations = [
        to_plain(row)
        for row in result.observations.values()
        if row.source_id == "johnston-1965-sodium-disilicate-redox"
    ]

    assert len(observations) == 101
    assert len(result.validation.hard_issues) == 0
    assert sum(row["value"]["kind"] == "unavailable" for row in observations) == 101
    table_i = [row for row in observations if row["locator"]["table"] == "I"]
    table_ii = [row for row in observations if row["locator"]["table"] == "II"]
    assert len(table_i) == 94
    assert len(table_ii) == 7
    expected_i = {
        "T_C",
        "atmosphere",
        "fO2_Pa",
        "total_pressure_Pa",
        "composition",
        "temperature_K",
    }
    expected_ii = expected_i - {"temperature_K"}
    for row in table_i:
        assert expected_i <= set(row["point_conditions"])
    for row in table_ii:
        assert expected_ii <= set(row["point_conditions"])

    sample_ids = (
        "johnston_1965_t1_ti_r01",
        "johnston_1965_t1_sn_r06",
        "johnston_1965_t1_ce_r02",
    )
    for sample_id in sample_ids:
        row = next(row for row in table_i if sample_id in row["observation_id"])
        identity = row["identity"]
        assert identity["composition"]["tag"] == "value"
        assert identity["temperature_K"]["tag"] == "value"
        assert identity.get("total_pressure_Pa") is None
        assert identity.get("fO2_Pa") is None
