from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "sugawara-1999-fe-na-loss-gas-mixing-furnace"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
AUDIT = ROOT / "data/literature/extracts/audits" / f"{SOURCE_ID}.audit.csv"


def test_sugawara_1999_table6_audit_count_provenance() -> None:
    with AUDIT.open(encoding="utf-8", newline="") as audit_file:
        rows = list(csv.DictReader(audit_file))

    assert not any(
        "MinerU omitted empty n° body cell" in row["verification"]
        for row in rows
    )
    counts = [row for row in rows if row["column_header"] == "n° (number of analyses)"]
    assert len(counts) == 18
    mineru_counts = [row for row in counts if row["mineru_text"]]
    render_transcribed_counts = [row for row in counts if not row["mineru_text"]]
    assert len(mineru_counts) == 13
    assert all(
        "MinerU draft column-7 value is printed n° count" in row["verification"]
        and "render-checked" in row["verification"]
        for row in mineru_counts
    )
    assert len(render_transcribed_counts) == 5
    assert all(
        "printed n° count absent from MinerU draft; transcribed from render"
        in row["verification"]
        for row in render_transcribed_counts
    )


def _assert_table6_phase_render_pins(document: dict) -> None:
    context = next(
        row
        for species in document["species"].values()
        for row in species.get("context", [])
        if row.get("observation_id") == "sugawara_1999_table6_phase_proportions_totals_and_starting_compositions"
    )
    rows = {row["run_id"]: row for row in context["values"]["rows"]}
    expected = {
        "m52701": ({"glass": "100"}, "5"),
        "m52705": ({"glass": "100"}, "7"),
        "m57101": ({"glass": "100"}, "6"),
        "m57105": ({"glass": "100"}, "6"),
        "m52702": ({"glass": "100"}, "6"),
        "m52706": ({"glass": "100"}, "6"),
        "m57106": ({"glass": "100"}, "6"),
        "m55801": ({"glass": "100"}, "4"),
        "m57102": ({"glass": "100"}, "6"),
        "m52703": ({"glass": "98", "olivine": "2"}, "5"),
        "m52707": ({"glass": "100"}, "3"),
        "m57107": ({"glass": "100"}, "6"),
        "m55802": ({"glass": "100"}, "7"),
        "m57103": ({"glass": "100"}, "6"),
        "m52708": ({"glass": "96", "olivine": "4"}, "4"),
        "m57104": ({"glass": "95", "olivine": "5"}, "6"),
        "m55803": ({"glass": "99", "olivine": "1"}, "7"),
        "m57108": ({"glass": "97", "olivine": "3"}, "6"),
    }
    assert len(rows) == len(expected) == 18
    assert {
        run_id: (row["phase_proportions_pct"], row["analysis_count"])
        for run_id, row in rows.items()
    } == expected
    # Independent pins read from the PDF p. 12 render: one all-glass row and
    # one mixed glass/olivine row.
    assert rows["m52701"]["phase_proportions_pct"] == {"glass": "100"}
    assert rows["m52701"]["analysis_count"] == "5"
    assert rows["m52703"]["phase_proportions_pct"] == {"glass": "98", "olivine": "2"}
    assert rows["m52703"]["analysis_count"] == "5"


def _assert_table6_locator_pins(source_rows: dict) -> None:
    glass_rows = [
        row
        for observation_id, row in source_rows.items()
        if "table6_" in observation_id
        and row["values"].get("quantity") == "residue_component_composition"
    ]
    assert len(glass_rows) == 108
    for row in glass_rows:
        locator = row["locator"]
        assert locator["page"] == 12
        assert locator["table"] == "6"
        assert locator["column_header"] == row["values"]["analyte_as_published"]
        assert "table_column_number" not in locator

    # Printed p. 436 row 4 places this value under SiO2 (physical column 9);
    # the draft's column 8 is retained only in the audit crosswalk.
    silica = source_rows["sugawara_1999_table6_m52701_sio2_quenched_glass"]["locator"]
    assert silica["table_row_number"] == 4
    assert silica["row_label"] == "m52701"
    assert silica["column_header"] == "SiO2"
    assert "table_column_number" not in silica


def test_sugawara_1999_render_pins_and_isolated_migration(tmp_path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_table6_phase_render_pins(document)
    source_rows = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["observations"]
    }
    _assert_table6_locator_pins(source_rows)

    # Render pins: Table 2 p. 429, Table 3 p. 430, and Table 6 p. 436.
    pins = (
        ("sugawara_1999_table2_m48413_feo_loss", "mass_loss_pct", "32", 5),
        ("sugawara_1999_table3_m48313_average_glass_feot", "FeO_wt_pct", "12.59", 6),
        ("sugawara_1999_table6_m52701_sio2_quenched_glass", "SiO2_wt_pct", "45.62", 12),
        ("sugawara_1999_table6_m57105_na2o_loss", "mass_loss_pct", "-233", 12),
    )
    for observation_id, field, expected, page in pins:
        row = source_rows[observation_id]
        assert row["values"][field] == expected
        assert row["locator"]["page"] == page

    table2_loss_count = sum("table2_" in row_id and "_loss" in row_id for row_id in source_rows)
    table3_glass_count = sum("table3_" in row_id for row_id in source_rows)
    table6_loss_count = sum(
        "table6_" in row_id and row["values"].get("quantity") == "mass_loss_fraction"
        for row_id, row in source_rows.items()
    )
    table6_glass_count = sum(
        "table6_" in row_id and row["values"].get("quantity") == "residue_component_composition"
        for row_id, row in source_rows.items()
    )
    assert (table2_loss_count, table3_glass_count, table6_loss_count, table6_glass_count) == (
        80,
        60,
        36,
        108,
    )
    assert len(source_rows) == 284

    # Printed run conditions reach every migrated row with source locators.
    for row in source_rows.values():
        conditions = row["point_conditions"]
        for key in ("T_C", "fO2_log", "duration_h", "total_pressure_Pa", "composition", "gas_environment"):
            assert conditions[key]["state"]["tag"] == "value"
            assert conditions[key].get("locator")
        assert conditions["total_pressure_Pa"]["locator"]["section"] == "Title"
        assert conditions["total_pressure_Pa"]["state"]["value"] == "101325"
        assert "sample_mass_kg" not in conditions
        assert "gas_mixing_ratio" not in conditions
        assert row["values"]["gas_mixing_ratio_state"]["tag"] == "unknown"
        assert row["values"].get("sample_mass_kg") is None

    glass_rows = [row for row in source_rows.values() if row.get("phase") == "glass"]
    assert len(glass_rows) == 168
    for row in glass_rows:
        assert row["quench_state"]["state"] == {"tag": "value", "value": "not_stated"}
        assert row["quench_state"]["locator"]["page"] == 4

    from tests.battery.test_migrate import _migrate_real_extract

    result = _migrate_real_extract(tmp_path, f"{SOURCE_ID}.yaml")
    migrated = {
        row.observation_id.split("::", 1)[-1]: row
        for row in result.observations.values()
        if row.source_id == SOURCE_ID
    }
    assert len(migrated) == 284
    assert result.validation.hard_issues == ()
    assert all(row.value.kind.value == "point" for row in migrated.values())

    loss = migrated[pins[0][0]]
    glass_fe = migrated[pins[1][0]]
    glass_silica = migrated[pins[2][0]]
    assert loss.value.point == Decimal("0.32")
    assert glass_fe.value.point == Decimal("12.59")
    assert glass_silica.value.point == Decimal("45.62")
    for row in (loss, glass_fe, glass_silica):
        assert row.identity.composition.is_value
        assert row.identity.total_pressure_Pa.value == Decimal("101325")
        assert row.identity.temperature_K.is_value
        for key in ("T_C", "fO2_log", "duration_h", "total_pressure_Pa", "composition", "gas_environment"):
            condition = row.point_conditions[key]
            assert condition.state.is_value
            assert condition.locator is not None

    # Current migration limitation: fill_identity's residue-component profile
    # permits fO2_Pa as N/A, so it drops these printed-log identity values even
    # though their point_conditions are retained. This is the outstanding
    # acceptance blocker; changing the migrator is outside this extract task.
    assert loss.identity.fO2_Pa.is_value
    assert glass_fe.identity.fO2_Pa.is_unknown
    assert glass_silica.identity.fO2_Pa.is_unknown
