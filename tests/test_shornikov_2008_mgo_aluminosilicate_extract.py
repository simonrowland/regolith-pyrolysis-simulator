"""Render pins and isolated migration checks for Shornikov (2008)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import AdmissionStatus, ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2008-mgo-aluminosilicate-melts.yaml"


def test_render_pins_and_isolated_migration_preserve_every_temperature():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = [
        (formula, row)
        for formula, block in document["species"].items()
        for row in block.get("observations", [])
    ]
    table_rows = [
        row
        for _, row in observations
        if "_p002_t03_" in row["observation_id"]
    ]
    marker_rows = [
        row
        for _, row in observations
        if row["locator"].get("figure") == "1"
        and row["locator"].get("marker_symbol") is not None
    ]
    curve_rows = [
        row
        for _, row in observations
        if row["locator"].get("figure") == "1"
        and row["locator"].get("curve") is not None
    ]
    assert len(table_rows) == 42
    assert len(marker_rows) == 23
    assert len(curve_rows) == 4

    rendered_table_cells = (
        (
            "Al2O3",
            "shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r03_c02",
            "-1017.742",
            "cr",
        ),
        (
            "MgO",
            "shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r12_c02",
            "-370.531",
            "cr",
        ),
        (
            "SiO2",
            "shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r15_c02",
            "-540.626",
            "cr",
        ),
    )
    for formula, observation_id, value, phase in rendered_table_cells:
        row = next(
            row
            for candidate, row in observations
            if candidate == formula and row["observation_id"] == observation_id
        )
        assert row["values"]["value"] == value
        assert row["phase"] == phase
        assert row["locator"]["page"] == 2
        assert row["locator"]["published_page"] == 148
        assert "table" not in row["locator"]
        assert "unnumbered 'Table'" in row["locator"]["note"]

    # Literal page-render reads pin all three data shapes: activity log axes,
    # a different activity series, and the linearly plotted -delta_fG ordinate.
    rendered_figure_reads = (
        (
            "shornikov_2008_fig1_a_marker_3",
            "activity",
            0.19,
            52,
            0.000830,
            [1053, 352],
            [36, 64],
            [1166, 689],
            "lmax-r2-a3",
        ),
        (
            "shornikov_2008_fig1_b_marker_1",
            "activity",
            "0.19",
            2.0,
            0.000832,
            [1579, 346],
            [32, 60],
            [1697, 686],
            "lmax-r2-b1",
        ),
        (
            "shornikov_2008_fig1_b_marker_2",
            "activity",
            "0.20",
            19,
            0.000832,
            [1756, 341],
            [51, 34],
            [1859, 707],
            "lmax-r2-b2",
        ),
        (
            "shornikov_2008_fig1_d_marker_3",
            "value",
            "-27",
            52,
            0.002671,
            [2119, 1277],
            [43, 59],
            [2227, 1617],
            "lmax-r2-d3",
        ),
    )
    for (
        observation_id,
        value_key,
        value,
        x_siO2_mol_pct,
        uncertainty,
        pixel_1,
        pixel_2,
        crop_origin,
        crop_id,
    ) in rendered_figure_reads:
        row = next(row for _, row in observations if row["observation_id"] == observation_id)
        values = row["values"]
        assert values["T_K"] == "2050"
        assert values[value_key] == value
        assert values["x_SiO2_mol_pct_as_read"] == x_siO2_mol_pct
        uncertainty_key = (
            "read_uncertainty_kJ_mol" if value_key == "value" else "read_uncertainty_dex"
        )
        assert values[uncertainty_key] == uncertainty
        assert values[uncertainty_key] > 0
        assert values["read_1"]["pixel_xy_crop"] == pixel_1
        assert values["read_2"]["pixel_xy_crop"] == pixel_2
        assert values["read_2"]["crop_origin_xy_page_px"] == crop_origin
        assert values["read_2"]["crop_id"] == crop_id

    fresh_crop_ids = [row["values"]["read_2"]["crop_id"] for row in marker_rows]
    assert len(set(fresh_crop_ids)) == 23
    assert all(
        row["values"].get("read_uncertainty_dex", row["values"].get("read_uncertainty_kJ_mol", 0)) > 0
        for row in marker_rows
    )
    # Read 1 was taken in the page crop at (150, 400). A second read records a
    # different crop origin. Offsets may coincide for a real pair; one shared
    # offset for a whole panel is a translated copy, not a second read.
    for panel in ("a", "b", "c", "d"):
        offsets = []
        for row in marker_rows:
            if row["locator"]["panel"] != panel:
                continue
            read_1 = row["values"]["read_1"]["pixel_xy_crop"]
            read_2 = row["values"]["read_2"]
            crop = read_2["crop_origin_xy_page_px"]
            pixel = read_2["pixel_xy_crop"]
            assert crop != [150, 400]
            offsets.append(
                (
                    crop[0] + pixel[0] - 150 - read_1[0],
                    crop[1] + pixel[1] - 400 - read_1[1],
                )
            )
        assert len(set(offsets)) > 1

    prose_row = next(
        row for _, row in observations
        if row["observation_id"] == "shornikov_2008_prose_cordierite_minimum_delta_fg"
    )
    assert prose_row["values"]["value"] == "-26.9"
    assert prose_row["locator"]["page"] == 1
    assert prose_row["locator"]["pdf_page_index"] == 0
    assert prose_row["locator"]["published_page"] == 147
    assert "section" not in prose_row["locator"]
    for condition in prose_row["point_conditions"].values():
        assert condition["locator"]["page"] == 1
        assert condition["locator"]["pdf_page_index"] == 0
        assert condition["locator"]["published_page"] == 147
        assert "section" not in condition["locator"]
    assert prose_row["point_conditions"]["composition"]["state"]["tag"] == "unknown"

    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    migrated = [row for row in result.observations.values() if row.source_id == document["source_id"]]
    assert len(migrated) == 70
    assert sum(row.value.kind is ValueKind.POINT for row in migrated) == 66
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated) == 4
    assert sum(row.admission.status is AdmissionStatus.ADMITTED for row in migrated) == 66
    assert sum(row.admission.status is AdmissionStatus.REJECTED for row in migrated) == 4
    assert not result.registry_issues
    assert not result.validation.hard_issues

    for row in migrated:
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.locator is not None
        assert row.identity.temperature_K.is_value
        condition_value = temperature.state.value
        condition_point = getattr(condition_value, "point", condition_value)
        assert row.identity.temperature_K.value == condition_point

    for row in migrated:
        if (
            row.locator.figure == "1"
            and row.identity.quantity.value == "activity"
            and "_marker_" in row.observation_id
        ):
            composition = row.point_conditions["composition"]
            assert composition.state.is_value
            assert composition.locator is not None
            assert row.identity.composition.is_value
            assert row.identity.composition.value == composition.state.value

    b1 = next(
        row for row in migrated if row.observation_id.endswith("shornikov_2008_fig1_b_marker_1")
    )
    assert b1.admission.status is AdmissionStatus.ADMITTED
    assert dict(b1.identity.composition.value.components) == {
        "MgAl2O4": Decimal("0.980"),
        "SiO2": Decimal("0.020"),
    }
    assert dict(b1.point_conditions["composition"].state.value.components) == {
        "MgAl2O4": Decimal("0.980"),
        "SiO2": Decimal("0.020"),
    }

    for row in migrated:
        if row.locator.figure == "1" and "fig1_d_marker_" in row.observation_id:
            composition = row.point_conditions["composition"]
            assert composition.state.is_value
            assert composition.locator is not None
