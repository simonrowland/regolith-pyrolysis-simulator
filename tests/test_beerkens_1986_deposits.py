from __future__ import annotations

from copy import deepcopy
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


def _assert_beerkens_render_pins(doc: dict) -> None:
    rows = _rows(doc)
    sulfur = rows["beerkens_1986_table1_5_s_volume_ppm"]
    assert len(sulfur["values"]["points"]) == 6
    assert sulfur["values"]["points"][0]["value"] == 1200
    assert sulfur["values"]["points"][0]["locator"] == {
        "page": 28,
        "published_page": 19,
        "table": "1.5",
        "row": 2,
        "column": 3,
        "table_row_number": 2,
        "table_column_number": 3,
        "note": "furnace A",
    }

    carryover = rows["beerkens_1986_table2_4_water_cooled_paddle_carryover"]
    assert len(carryover["values"]["points"]) == 19
    assert carryover["values"]["points"][0]["value"] == 3.3
    assert carryover["values"]["points"][0]["locator"]["page"] == 73

    rate = rows["beerkens_1986_table4_2_na2so4_max_deposition_rate_experiment"]
    assert len(rate["values"]["points"]) == 4
    assert rate["values"]["points"][3]["value"] == 1.60
    assert rate["values"]["points"][3]["locator"] == {
        "page": 137,
        "published_page": 128,
        "table": "4.2",
        "row": 5,
        "column": 2,
        "table_row_number": 5,
        "table_column_number": 2,
    }

    dew = rows["beerkens_1986_table4_3_na2so4_dew_point_experiment"]
    assert len(dew["values"]["points"]) == 4
    assert dew["values"]["points"][0]["T_K"] == 1120
    assert dew["values"]["points"][0]["locator"]["page"] == 139

    pb = rows["beerkens_1986_section4_5_6_pbo_measured_maximum_deposition_rate"]
    assert pb["values"]["points"][0]["value"] == 3.0
    assert pb["values"]["points"][0]["T_K"] == 1000
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
    assert len(result.observations) == 66

    rows = list(result.observations.values())
    unknown_quantity = [row for row in rows if row.identity.quantity.is_unknown]
    expected_unknown_prefix_counts = {
        "beerkens_1986_table1_5_s_volume_ppm": 6,
        "beerkens_1986_table1_5_na_volume_ppm": 7,
        "beerkens_1986_table1_5_k_volume_ppm": 6,
        "beerkens_1986_table2_4_water_cooled_paddle_carryover": 19,
        "beerkens_1986_table4_2_na2so4_max_deposition_rate_experiment": 4,
        "beerkens_1986_table4_2_na2so4_max_deposition_rate_cfbl": 4,
        "beerkens_1986_section4_5_5_k2so4_measured_maximum_deposition_rate": 1,
        "beerkens_1986_section4_5_5_na2so4_measured_comparator_rate": 1,
        "beerkens_1986_section4_5_6_pbo_measured_maximum_deposition_rate": 1,
    }
    actual_unknown_prefix_counts: dict[str, int] = {}
    for row in unknown_quantity:
        base_id = next(
            (
                base_id
                for base_id in expected_unknown_prefix_counts
                if f"::{base_id}::" in row.observation_id
            ),
            None,
        )
        assert base_id is not None, row.observation_id
        actual_unknown_prefix_counts[base_id] = (
            actual_unknown_prefix_counts.get(base_id, 0) + 1
        )
    assert actual_unknown_prefix_counts == expected_unknown_prefix_counts

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
