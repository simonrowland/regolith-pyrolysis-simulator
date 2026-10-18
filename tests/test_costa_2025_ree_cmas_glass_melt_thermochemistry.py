from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.test_migrate import _migrate_real_extract


EXTRACT_NAME = "costa-2025-ree-cmas-glass-melt-thermochemistry.yaml"
SOURCE_ID = "costa-2025-ree-cmas-glass-melt-thermochemistry"


def test_costa_2025_table3_values_compositions_and_migration(tmp_path: Path) -> None:
    extract_path = Path("data/literature/extracts") / EXTRACT_NAME
    extract = yaml.safe_load(extract_path.read_text(encoding="utf-8"))
    source_rows = {
        row["observation_id"]: row
        for row in extract["species"]["CaO"]["observations"]
    }

    # Table 3, PDF page 11: these literal values were confirmed against its 600-dpi render.
    rendered_values = {
        "costa_2025_cmas_tg_table3": ("T_K", Decimal("1021")),
        "costa_2025_y_h_tc_table3": ("T_K", Decimal("1246")),
        "costa_2025_gd_h_heat_of_fusion_table3": (
            "delta_H_fus_kJ_mol-1",
            Decimal("16.36"),
        ),
    }
    for observation_id, (field, expected) in rendered_values.items():
        row = source_rows[observation_id]
        actual = Decimal(str(row["values"][field]))
        assert actual == expected
        assert row["locator"]["pdf_page_index"] == 10
        assert row["locator"]["table"] == "3"

    assert len(source_rows) == 28
    for row in source_rows.values():
        composition = row["values"]["composition_mol"]
        assert composition["basis"] == "printed_mole_percent"
        assert row["values"]["composition_as_published"]
        assert "temperature_K" in row["point_conditions"]

    # Table 1, PDF page 11: the full measured compositions identify each glass sample.
    composition_rows = {
        row["sample"]: row
        for row in extract["species"]["CaO"]["context"][0]["values"]["rows"]
    }
    assert len(composition_rows) == 7
    assert composition_rows["CMAS"]["composition_mol"] == {
        "basis": "printed_mole_percent",
        "CaO": 32.34,
        "MgO": 10.71,
        "Al2O3": 7.62,
        "SiO2": 49.33,
    }
    assert composition_rows["Y-H"]["composition_mol"]["Y2O3"] == 4.93
    assert composition_rows["Gd-H"]["composition_mol"]["Gd2O3"] == 5.02

    context = {
        row["observation_id"]: row
        for row in extract["species"]["CaO"]["context"]
    }
    table4_rows = context["costa_2025_table4_drop_solution_enthalpies"]["values"]["rows"]
    assert len(table4_rows) == 14
    cmas_drop_solution = next(row for row in table4_rows if row["sample"] == "CMAS")
    assert cmas_drop_solution["value_kJ_mol-1"] == 45.76
    assert cmas_drop_solution["uncertainty_kJ_mol-1"] == 1.60
    assert cmas_drop_solution["experiment_count"] == 5
    assert next(row for row in table4_rows if row["sample"] == "CaO")["attribution"] == "[28]"

    table7_rows = context["costa_2025_table7_formation_enthalpies"]["values"]["rows"]
    assert len(table7_rows) == 7
    assert table7_rows[-1]["delta_Hf_oxide_kJ_mol-1"] == -13.81
    figures = [row for row in context.values() if row["type"] == "figure_record"]
    assert len(figures) == 5
    assert all(row["values"]["numeric_points"] == [] for row in figures)
    assert all(
        row["values"]["digitisation"] == "deferred to a figure pass"
        for row in figures
    )

    result = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    migrated_rows = {
        row.observation_id.split("::", 1)[-1]: row
        for row in result.observations.values()
        if row.source_id == SOURCE_ID
    }
    assert len(migrated_rows) == 28
    assert not result.validation.hard_issues

    # Transition temperature is the value point; fusion temperature is its identity condition.
    for observation_id, (field, expected) in rendered_values.items():
        migrated = migrated_rows[observation_id]
        assert migrated.value.point == expected
        if field != "T_K":
            assert migrated.identity.temperature_K.value == Decimal("1475")

    for observation_id, row in source_rows.items():
        migrated = migrated_rows[observation_id]
        if observation_id.endswith("heat_of_fusion_table3"):
            temperature = row["values"]["T_fus_K"]["value"]
            assert migrated.identity.temperature_K.value == Decimal(str(temperature))
        else:
            assert migrated.value.point == Decimal(str(row["values"]["T_K"]))
            assert migrated.identity.total_pressure_Pa.is_unknown
            assert migrated.identity.total_pressure_Pa.reason == "not reported"
