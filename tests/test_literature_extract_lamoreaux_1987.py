from __future__ import annotations

from pathlib import Path
from decimal import Decimal

import yaml

from simulator.battery.enums import AdmissionStatus, ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/lamoreaux-hildenbrand-hildenbrand-1987.yaml"


def test_lamoreaux_1987_table1_render_values_and_selected_row_count():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    selected = {
        species: [
            row for row in block.get("observations", [])
            if row.get("observation_id", "").endswith("_fusion")
        ]
        for species, block in document["species"].items()
    }
    selected = {species: rows for species, rows in selected.items() if rows}

    assert set(selected) == {"BeO", "MgO", "CaO", "B2O3", "Al2O3", "SiO2"}
    assert sum(map(len, selected.values())) == 7
    by_id = {
        row["observation_id"]: row
        for rows in selected.values()
        for row in rows
    }

    assert by_id["LH87_BeO_fusion"]["locator"]["published_page"] == 421
    assert by_id["LH87_BeO_fusion"]["values"]["T_m_K"] == 2851
    assert by_id["LH87_BeO_fusion"]["values"]["Delta_fus_H_over_R_kK"] == 10.3
    assert by_id["LH87_CaO_fusion"]["values"]["T_m_uncertainty_as_printed_K"] == "(-50,+300)"
    assert by_id["LH87_CaO_fusion"]["values"]["status"] == "typed_refusal"
    assert by_id["LH87_CaO_fusion"]["point_conditions"]["temperature_K"]["state"]["value"]["point"] == "2900"
    assert by_id["LH87_SiO2_cristobalite_fusion"]["locator"]["published_page"] == 421
    assert by_id["LH87_SiO2_cristobalite_fusion"]["values"]["Delta_fus_H_over_R_kK"] == 1.15

    assert all(
        row.get("values", {}).get("method_class") == "quoted_attributed"
        for rows in selected.values()
        for row in rows
    )
    assert all(
        "temperature_K" in row.get("point_conditions", {})
        and "total_pressure_Pa" not in row.get("point_conditions", {})
        and "total_pressure_Pa" not in row.get("values", {})
        for rows in selected.values()
        for row in rows
    )


def test_lamoreaux_1987_vapor_summaries_keep_printed_species_and_unknown_conditions():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block.get("observations", [])
        if row.get("observation_id", "").endswith("_vapor_speciation")
    }

    assert set(rows) == {
        "LH87_BeO_vapor_speciation",
        "LH87_MgO_vapor_speciation",
        "LH87_CaO_vapor_speciation",
        "LH87_B2O3_vapor_speciation",
        "LH87_Al2O3_vapor_speciation",
        "LH87_SiO2_vapor_speciation",
    }
    assert len(rows) == 6
    assert rows["LH87_BeO_vapor_speciation"]["locator"]["published_page"] == 422
    assert rows["LH87_BeO_vapor_speciation"]["values"]["species_by_condition_as_printed"]["oxidizing"] == [
        "BeO(g)", "Be2O2(g)", "Be3O3(g)", "Be4O4(g)", "Be5O5(g)"
    ]
    assert rows["LH87_SiO2_vapor_speciation"]["locator"]["published_page"] == 435
    assert rows["LH87_SiO2_vapor_speciation"]["values"]["species_by_condition_as_printed"]["oxidizing_below_about_2500_K"] == "SiO2(g) is favored"
    for row in rows.values():
        assert row["values"]["method_class"] == "quoted_attributed"
        assert row["values"]["status"] == "typed_refusal"
        assert row["point_conditions"]["temperature_K"]["state"]["tag"] == "unknown"
        assert row["point_conditions"]["atmosphere"]["state"]["tag"] == "unknown"
        assert "total_pressure_Pa" not in row["point_conditions"]
        assert "fO2_Pa" not in row["point_conditions"]


def test_lamoreaux_1987_table3_render_pins_and_selected_row_count():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block.get("observations", [])
        if row.get("observation_id", "").endswith("_table3")
    }

    assert len(rows) == 30
    for row in rows.values():
        point = row["point_conditions"]["temperature_K"]
        assert point["state"]["value"] == "298"
        assert point["locator"]["published_page"] == 424
        assert "total_pressure_Pa" not in row["point_conditions"]
        assert row["values"]["status"] == "typed_refusal"
    for observation_id, value_key, expected in (
        ("LH87_BeO_s_table3", "Delta_f_H_298_over_R_kK", -73.29),
        ("LH87_MgO_s_table3", "Delta_f_H_298_over_R_kK", -72.34),
        ("LH87_B2O3_g_table3", "S_298_over_R", 34.371),
        ("LH87_AlO2_g_table3", "Delta_f_H_298_over_R_kK", -10.4),
        ("LH87_Be5O5_g_table3", "S_298_over_R", 38.67),
        ("LH87_SiO2_quartz_table3", "Delta_f_H_298_over_R_kK", -109.53),
    ):
        row = rows[observation_id]
        assert row["locator"]["published_page"] == 424
        assert row["values"][value_key] == expected
        assert row["values"]["method_class"] == "quoted_attributed"
        point = row["point_conditions"]["temperature_K"]
        assert point["state"]["value"] == "298"
        assert "total_pressure_Pa" not in row["point_conditions"]

    assert rows["LH87_Si2O2_g_table3"]["values"]["status"] == "typed_refusal"

    coefficient_refusals = [
        row
        for block in document["species"].values()
        for row in block.get("observations", [])
        if row.get("observation_id", "").endswith("_table2_GEF_refusal")
    ]
    assert len(coefficient_refusals) == 6
    assert all(row["values"]["status"] == "typed_refusal" for row in coefficient_refusals)
    assert all("total_pressure_Pa" not in row.get("point_conditions", {}) for row in coefficient_refusals)


def test_lamoreaux_1987_isolated_migration_keeps_conditions_and_refusals():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    migrated = [row for row in result.observations.values() if row.source_id == document["source_id"]]

    assert len(migrated) == 55
    assert not result.registry_issues
    assert not result.validation.hard_issues
    by_id = {row.observation_id.rsplit("::", 1)[-1]: row for row in migrated}
    for block in document["species"].values():
        for observation in block.get("observations", []):
            observation_id = observation["observation_id"]
            if not observation_id.endswith("_fusion"):
                continue
            row = by_id[observation_id]
            point = row.point_conditions["temperature_K"]
            assert point.state.is_value
            assert point.locator is not None
            condition_value = point.state.value
            condition_point = getattr(condition_value, "point", condition_value)
            assert Decimal(str(condition_point)) == Decimal(str(observation["values"]["T_m_K"]))

    for observation_id, temperature in (
        ("LH87_BeO_fusion", "2851"),
        ("LH87_CaO_fusion", "2900"),
        ("LH87_SiO2_cristobalite_fusion", "1996"),
    ):
        row = by_id[observation_id]
        point = row.point_conditions["temperature_K"]
        assert point.state.is_value
        assert point.locator is not None
        condition_value = point.state.value
        condition_point = getattr(condition_value, "point", condition_value)
        assert Decimal(str(condition_point)) == Decimal(temperature)
        assert row.identity.temperature_K.is_not_applicable
        assert row.identity.total_pressure_Pa.is_unknown

    for observation_id in (
        "LH87_BeO_vapor_speciation",
        "LH87_MgO_vapor_speciation",
        "LH87_CaO_vapor_speciation",
        "LH87_B2O3_vapor_speciation",
        "LH87_Al2O3_vapor_speciation",
        "LH87_SiO2_vapor_speciation",
    ):
        row = by_id[observation_id]
        assert row.value.kind is ValueKind.UNAVAILABLE
        assert row.admission.status is AdmissionStatus.REJECTED
        assert row.point_conditions["temperature_K"].state.is_unknown
        assert row.point_conditions["temperature_K"].locator is not None
        assert row.identity.total_pressure_Pa is None
        assert "total_pressure_Pa" not in row.point_conditions

    for row in migrated:
        if row.observation_id.endswith("_table2_GEF_refusal"):
            assert row.value.kind is ValueKind.UNAVAILABLE
            assert row.admission.status is AdmissionStatus.REJECTED
            assert row.point_conditions["temperature_K"].state.is_unknown
            assert row.point_conditions["temperature_K"].locator is not None
            assert "total_pressure_Pa" not in row.point_conditions

    for row in migrated:
        if row.observation_id.endswith("_table3"):
            point = row.point_conditions["temperature_K"]
            assert point.state.is_value and point.locator is not None
            condition_value = point.state.value
            condition_point = getattr(condition_value, "point", condition_value)
            assert Decimal(str(condition_point)) == Decimal("298")
            assert row.identity.total_pressure_Pa is None
            assert "total_pressure_Pa" not in row.point_conditions

    unavailable = sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated)
    assert unavailable == 48
    print(
        "isolated migration:",
        f"observations={len(migrated)}",
        f"hard={len(result.validation.hard_issues)}",
        f"unavailable={unavailable}",
    )

    for observation_id in (
        "LH87_BeO_s_table3",
        "LH87_MgO_g_table3",
        "LH87_B2O3_g_table3",
        "LH87_AlO2_g_table3",
        "LH87_SiO2_quartz_table3",
    ):
        row = by_id[observation_id]
        point = row.point_conditions["temperature_K"]
        assert point.state.is_value and point.locator is not None
        condition_value = point.state.value
        condition_point = getattr(condition_value, "point", condition_value)
        assert Decimal(str(condition_point)) == Decimal("298")
        assert row.identity.total_pressure_Pa is None

    for observation_id in (
        "LH87_BeO_s_table3",
        "LH87_MgO_g_table3",
        "LH87_SiO2_quartz_table3",
    ):
        row = by_id[observation_id]
        print(
            observation_id,
            "composition=", row.identity.composition,
            "total_pressure_Pa=", row.identity.total_pressure_Pa,
            "fO2_Pa=", row.identity.fO2_Pa,
            "temperature_K=", row.identity.temperature_K,
        )
