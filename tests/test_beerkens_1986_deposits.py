from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT_NAME = "related-beerkens-1986-deposits-condensation-thesis.yaml"
EXTRACT = ROOT / "data/literature/extracts" / EXTRACT_NAME


def _rows(doc: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for block in doc["species"].values()
        for row in block.get("observations", [])
    }


_TABLE_SERIES_PRINTED_POINTS = {
    "beerkens_1986_table1_5_s_volume_ppm": (
        "volume ppm as printed",
        (
            ("A", "1200", 28, "1.5", 2, 3),
            ("B", "160", 28, "1.5", 3, 3),
            ("C", "190", 28, "1.5", 4, 3),
            ("D", "1200", 28, "1.5", 5, 3),
            ("E", "1340", 28, "1.5", 6, 3),
            ("G", "160", 28, "1.5", 8, 3),
        ),
    ),
    "beerkens_1986_table1_5_na_volume_ppm": (
        "volume ppm as printed",
        (
            ("A", "100", 28, "1.5", 2, 5),
            ("B", "100", 28, "1.5", 3, 5),
            ("C", "100", 28, "1.5", 4, 5),
            ("D", "30", 28, "1.5", 5, 5),
            ("F", "16", 28, "1.5", 7, 5),
            ("G", "500", 28, "1.5", 8, 5),
            ("H", "5", 28, "1.5", 9, 5),
        ),
    ),
    "beerkens_1986_table1_5_k_volume_ppm": (
        "volume ppm as printed",
        (
            ("A", "10", 28, "1.5", 2, 6),
            ("B", "10", 28, "1.5", 3, 6),
            ("D", "80", 28, "1.5", 5, 6),
            ("F", "25", 28, "1.5", 7, 6),
            ("G", "100", 28, "1.5", 8, 6),
            ("H", "40", 28, "1.5", 9, 6),
        ),
    ),
    "beerkens_1986_table2_4_water_cooled_paddle_carryover": (
        "g/24 h as printed",
        (
            ("A", "3.3", 73, "2.4", 4, 2),
            ("B", "4.0", 73, "2.4", 4, 3),
            ("C", "3.8", 73, "2.4", 4, 4),
            ("E", "2.7", 73, "2.4", 4, 5),
            ("F", "8.1", 73, "2.4", 4, 6),
            ("G", "3.8", 73, "2.4", 4, 7),
            ("H", "5.1", 73, "2.4", 4, 8),
            ("I", "8.9", 73, "2.4", 4, 9),
            ("J", "7.7", 73, "2.4", 4, 10),
            ("K", "12.6", 73, "2.4", 4, 11),
            ("L", "2.7", 73, "2.4", 4, 12),
            ("M", "7.1", 73, "2.4", 4, 13),
            ("H first port", "5.1", 73, "2.4", 16, 2),
            ("H second port", "4.5", 73, "2.4", 16, 3),
            ("H third port", "4.8", 73, "2.4", 16, 4),
            ("I first port", "8.9", 73, "2.4", 16, 5),
            ("I second port", "4.8", 73, "2.4", 16, 6),
            ("I third port", "4.0", 73, "2.4", 16, 7),
            ("I fourth port", "3.2", 73, "2.4", 16, 8),
        ),
    ),
    "beerkens_1986_table4_2_na2so4_max_deposition_rate_experiment": (
        "mg/cm2·hr as printed",
        (
            ((20, 20), "0.38", 137, "4.2", 2, 2),
            ((50, 50), "1.04", 137, "4.2", 3, 2),
            ((75, 75), "1.17", 137, "4.2", 4, 2),
            ((100, 100), "1.60", 137, "4.2", 5, 2),
        ),
    ),
    "beerkens_1986_table4_2_na2so4_max_deposition_rate_cfbl": (
        "mg/cm2·hr as printed",
        (
            ((20, 20, "CFBL"), "0.28", 137, "4.2", 2, 3),
            ((50, 50, "CFBL"), "0.70", 137, "4.2", 3, 3),
            ((75, 75, "CFBL"), "1.04", 137, "4.2", 4, 3),
            ((100, 100, "CFBL"), "1.40", 137, "4.2", 5, 3),
        ),
    ),
    "beerkens_1986_table4_3_na2so4_dew_point_experiment": (
        "K",
        (
            ((20, 20), "1120", 139, "4.3", 2, 5),
            ((50, 50), "1190", 139, "4.3", 3, 5),
            ((75, 75), "1237", 139, "4.3", 4, 5),
            ((100, 100), "1263", 139, "4.3", 5, 5),
        ),
    ),
}
_TABLE_SERIES_LOCATORS = {
    "beerkens_1986_table1_5_s_volume_ppm": (28, 19, "1.5"),
    "beerkens_1986_table1_5_na_volume_ppm": (28, 19, "1.5"),
    "beerkens_1986_table1_5_k_volume_ppm": (28, 19, "1.5"),
    "beerkens_1986_table2_4_water_cooled_paddle_carryover": (73, 64, "2.4"),
    "beerkens_1986_table4_2_na2so4_max_deposition_rate_experiment": (137, 128, "4.2"),
    "beerkens_1986_table4_2_na2so4_max_deposition_rate_cfbl": (137, 128, "4.2"),
    "beerkens_1986_table4_3_na2so4_dew_point_experiment": (139, 130, "4.3"),
}


def _assert_table_series_printed_points(rows: dict[str, dict]) -> None:
    for observation_id, (expected_units, expected_points) in _TABLE_SERIES_PRINTED_POINTS.items():
        series = rows[observation_id]
        assert series["units"] == expected_units
        assert (
            series["locator"]["page"],
            series["locator"]["published_page"],
            series["locator"]["table"],
        ) == _TABLE_SERIES_LOCATORS[observation_id]
        actual_points = []
        for point in series["values"]["points"]:
            locator = point["locator"]
            if "furnace_as_printed" in point:
                label = point["furnace_as_printed"]
            elif "port_as_printed" in point:
                label = point["port_as_printed"]
            else:
                label = (point["na_volume_ppm"], point["s_volume_ppm"])
                if "model_as_printed" in point:
                    label += (point["model_as_printed"],)
            printed_value = point.get("value", point.get("T_K"))
            actual_points.append(
                (
                    label,
                    Decimal(str(printed_value)),
                    locator["page"],
                    locator.get("table", series["locator"].get("table")),
                    locator["table_row_number"],
                    locator["table_column_number"],
                )
            )
        assert tuple(actual_points) == tuple(
            (label, Decimal(value), page, table, row, column)
            for label, value, page, table, row, column in expected_points
        )


def _assert_beerkens_render_pins(doc: dict) -> None:
    rows = _rows(doc)
    _assert_table_series_printed_points(rows)

    pb = rows["beerkens_1986_section4_5_6_pbo_measured_maximum_deposition_rate"]
    assert pb["values"]["points"][0]["value"] == 3.0
    assert pb["values"]["points"][0]["T_K"] == 1000
    pb_onset = rows["beerkens_1986_section4_5_6_pbo_measured_dew_point"]
    assert pb_onset["values"]["points"][0]["T_K"] == 1090
    assert pb_onset["values"]["points"][0]["locator"] == {
        "page": 168,
        "published_page": 159,
        "section": "4.5.6",
        "note": "experimental PbO deposition onset; render p168 confirms page 159",
    }
    k2so4 = rows["beerkens_1986_section4_5_5_k2so4_measured_maximum_deposition_rate"]
    k2so4_point = k2so4["values"]["points"][0]
    assert k2so4_point["T_K"] == 1100
    assert k2so4_point["temperature_as_printed"] == "1100 K"
    assert "approximate" not in k2so4_point
    nacl = rows["beerkens_1986_literature_jackson_duffin_34_nacl_dew_point"]
    assert nacl["values"]["points"][0]["locator"] == {
        "page": 70,
        "published_page": 61,
        "section": "2.4",
        "note": "Jackson and Duffin [26] measured sodium-chloride dew point",
    }


def test_beerkens_1986_render_pins_and_isolated_migration(tmp_path: Path) -> None:
    from simulator.battery.migrate import to_plain
    from tests.battery.test_migrate import _migrate_real_extract
    from tools import validate_literature_extracts as vle

    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_beerkens_render_pins(doc)
    assert vle.check_all_fidelity_samples_match(doc, label=EXTRACT.stem) == []

    mutated = deepcopy(doc)
    mutated_rate = next(
        row
        for row in mutated["species"]["Na2SO4"]["observations"]
        if row["observation_id"]
        == "beerkens_1986_table4_2_na2so4_max_deposition_rate_experiment"
    )
    mutated_rate["values"]["points"][3]["value"] = 1.61
    assert any(
        "mismatch" in error
        for error in vle.check_all_fidelity_samples_match(
            mutated, label=f"{EXTRACT.stem}-mutated"
        )
    )

    result = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    assert result.validation is not None
    assert not result.validation.hard_issues

    rows = list(result.observations.values())
    from simulator.battery import migrate as battery_migrate
    from simulator.battery.stable_ids import series_row_extra

    expected_observation_ids = set()
    for source_row in _rows(doc).values():
        parent_id = f"{doc['source_id']}::{source_row['observation_id']}"
        for point in source_row["values"]["points"]:
            temperature = battery_migrate.select_declared_source(
                battery_migrate.AXIS_TEMPERATURE_K, None, point
            ).amount
            row_extra = series_row_extra(point, include_locator=True)
            expected_observation_ids.add(
                battery_migrate._exploded_point_id(
                    parent_id,
                    {"container": "points"},
                    temperature,
                    point,
                    row_extra,
                )
            )
    assert {row.observation_id for row in rows} == expected_observation_ids
    expected_unknown_prefixes = (
        "beerkens_1986_table1_5_s_volume_ppm",
        "beerkens_1986_table1_5_na_volume_ppm",
        "beerkens_1986_table1_5_k_volume_ppm",
        "beerkens_1986_table2_4_water_cooled_paddle_carryover",
        "beerkens_1986_table4_2_na2so4_max_deposition_rate_experiment",
        "beerkens_1986_table4_2_na2so4_max_deposition_rate_cfbl",
        "beerkens_1986_section4_5_5_k2so4_measured_maximum_deposition_rate",
        "beerkens_1986_section4_5_5_na2so4_model_comparator_rate",
        "beerkens_1986_section4_5_6_pbo_measured_maximum_deposition_rate",
    )
    assert {
        row.observation_id.split("::", 1)[1].split("::", 1)[0]
        for row in rows
        if row.identity.quantity.is_unknown
    } == set(expected_unknown_prefixes)
    for base_id in expected_unknown_prefixes:
        matching_rows = [row for row in rows if f"::{base_id}::" in row.observation_id]
        assert matching_rows, base_id
        assert all(row.identity.quantity.is_unknown for row in matching_rows)
        assert all(row.value.kind.value == "unavailable" for row in matching_rows)
    assert {
        row.value.unavailable_reason
        for row in rows
        if row.value.kind.value == "unavailable"
    } == {"declared quantity is unknown; refusing to pick a number"}

    model_comparator = next(
        row
        for row in rows
        if "beerkens_1986_section4_5_5_na2so4_model_comparator_rate"
        in row.observation_id
    )
    from simulator.battery.enums import EvidenceClass

    assert model_comparator.evidence.class_.value is EvidenceClass.AUTHOR_ESTIMATE

    for row in rows:
        assert row.identity.species is not None
        assert row.identity.species.phase.is_value
        assert row.point_conditions is not None
        assert "temperature_K" in row.point_conditions
        if row.identity.total_pressure_Pa is not None:
            assert row.identity.total_pressure_Pa.is_unknown or (
                row.identity.total_pressure_Pa.is_not_applicable
            )
        if row.identity.fO2_Pa is not None:
            assert row.identity.fO2_Pa.is_unknown or row.identity.fO2_Pa.is_not_applicable

    transition = next(
        row
        for row in rows
        if "beerkens_1986_section4_5_6_pbo_measured_dew_point" in row.observation_id
    )
    transition_plain = to_plain(transition)
    assert transition.identity.quantity.is_value
    assert transition.identity.quantity.value.value == "transition_temperature"
    assert transition.identity.total_pressure_Pa.is_unknown
    assert transition_plain["value"]["point"] == "1090"

    from simulator.battery.identity import profile_for

    identity_blockers = (
        "beerkens_1986_section3_3_2_na2so4_experimental_dew_point_gas_i",
        "beerkens_1986_table4_3_na2so4_dew_point_experiment",
        "beerkens_1986_literature_kohl_na2so4_dew_point",
    )
    for source_id in identity_blockers:
        row = next(item for item in rows if source_id in item.observation_id)
        profile = profile_for(row.identity)
        assert {"composition", "total_pressure_Pa"} <= profile.required
        assert row.identity.composition.is_unknown
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa.is_not_applicable
        assert row.identity.temperature_K.is_not_applicable

    for block in doc["species"].values():
        for source_row in block.get("observations", []):
            for point in source_row.get("values", {}).get("points", []):
                conditions = point.get("point_conditions") or {}
                for key in ("total_pressure_Pa", "fO2_Pa"):
                    if key in conditions:
                        assert conditions[key]["state"]["tag"] == "unknown"
