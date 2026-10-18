"""Render pins and isolated migration checks for Shornikov (2008)."""

from __future__ import annotations

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
        row for _, row in observations if row["locator"].get("table") == "3"
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

    # Literal page-render reads pin all three data shapes: activity log axes,
    # a different activity series, and the linearly plotted -delta_fG ordinate.
    rendered_figure_reads = (
        (
            "shornikov_2008_fig1_a_marker_3",
            "activity",
            0.19,
            52,
            0.00083,
            [1053, 352],
            [1002, 601],
        ),
        (
            "shornikov_2008_fig1_b_marker_2",
            "activity",
            "0.20",
            19,
            0.00042,
            [1756, 341],
            [1705, 591],
        ),
        (
            "shornikov_2008_fig1_d_marker_3",
            "value",
            "-27",
            52,
            0.0,
            [2119, 1277],
            [2069, 1527],
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
    ) in rendered_figure_reads:
        row = next(row for _, row in observations if row["observation_id"] == observation_id)
        values = row["values"]
        assert values["T_K"] == "2050"
        assert values[value_key] == value
        assert values["x_SiO2_mol_pct_as_read"] == x_siO2_mol_pct
        assert values.get("read_uncertainty_dex", values.get("read_uncertainty_kJ_mol")) == uncertainty
        assert values["read_1"]["pixel_xy_crop"] == pixel_1
        assert values["read_2"]["pixel_xy_crop"] == pixel_2

    prose_row = next(
        row for _, row in observations
        if row["observation_id"] == "shornikov_2008_prose_cordierite_minimum_delta_fg"
    )
    assert prose_row["values"]["value"] == "-26.9"
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

    for row in migrated:
        if row.locator.figure == "1" and "fig1_d_marker_" in row.observation_id:
            composition = row.point_conditions["composition"]
            assert composition.state.is_value
            assert composition.locator is not None
