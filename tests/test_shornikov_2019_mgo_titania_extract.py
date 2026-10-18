"""Source pins and isolated migration checks for Shornikov (2019)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import AdmissionStatus, ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2019-mgo-titania-melts.yaml"
SOURCE_ID = "shornikov-2019-mgo-titania-melts"


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
        if row["locator"].get("table") in {"1", "2"}
    ]
    figure_rows = [row for _, row in observations if row["locator"].get("figure")]
    assert len(table_rows) == 42
    assert len(figure_rows) == 16
    assert len(observations) == 63

    # Literal reads from the rendered table pages, independent of the audit CSV.
    rendered_table_cells = (
        (
            "Mg2TiO4",
            "shornikov_2019_mgo_titania_melts_p002_t01_r03_c03",
            "-5.69",
            "±0.33",
            "1",
            2,
            86,
        ),
        (
            "MgO",
            "shornikov_2019_mgo_titania_melts_p003_t02_r03_c02",
            "-355.987",
            None,
            "2",
            3,
            87,
        ),
        (
            "O2",
            "shornikov_2019_mgo_titania_melts_p003_t02_r12_c06",
            "0.000",
            None,
            "2",
            3,
            87,
        ),
    )
    for formula, observation_id, value, uncertainty, table, page, published_page in rendered_table_cells:
        row = next(
            row
            for candidate, row in observations
            if candidate == formula and row["observation_id"] == observation_id
        )
        assert row["values"]["value"] == value
        assert row["locator"]["table"] == table
        assert row["locator"]["page"] == page
        assert row["locator"]["published_page"] == published_page
        if uncertainty is not None:
            assert row["values"]["uncertainty_as_published"] == uncertainty

    interval_row = next(
        row
        for _, row in observations
        if row["observation_id"]
        == "shornikov_2019_mgo_titania_melts_p002_t01_r07_c03"
    )
    interval = interval_row["point_conditions"]["temperature_K"]
    assert interval["state"]["value"] == {
        "kind": "interval",
        "interval_low": "1700",
        "interval_high": "2030",
    }
    assert interval["locator"]["page"] == 2

    # These panels contain only calculated curves, so the figure rule requires
    # unavailable curve records and forbids sampling them as data markers.
    model_curve_pins = (
        ("shornikov_2019_mgo_titania_melts_fig2_panel_a_curve_1", "1973", "2"),
        ("shornikov_2019_mgo_titania_melts_fig3_curve_1", "1973", "3"),
        ("shornikov_2019_mgo_titania_melts_fig4_curve_1", "2300", "4"),
    )
    for observation_id, temperature, figure in model_curve_pins:
        row = next(row for _, row in observations if row["observation_id"] == observation_id)
        assert row["locator"]["figure"] == figure
        assert row["values"]["T_K"] == temperature
        assert row["values"]["method_class"] == "figure_only"
        assert row["values"]["admission_status"] == "typed_refusal"
        assert row["point_conditions"]["temperature_K"]["locator"] is not None
        assert row["point_conditions"]["composition"]["state"]["tag"] == "unknown"

    fig4_axis_reasons = {
        row["observation_id"]: row["point_conditions"]["composition"]["state"]["reason"]
        for _, row in observations
        if row["observation_id"].endswith(("fig4_curve_1", "fig4_curve_2"))
    }
    assert len(fig4_axis_reasons) == 2
    assert all("SiO2 composition axis" in reason for reason in fig4_axis_reasons.values())

    fusion_quote = next(
        row
        for _, row in observations
        if row["observation_id"].endswith("prose_mgti2o5_heat_of_fusion_bale_2016")
    )
    assert fusion_quote["values"]["delta_H_fus_kJ_mol-1"] == "54.343"
    assert fusion_quote["values"]["T_fus_K"] == "1930"

    approximate_estimate = next(
        row
        for _, row in observations
        if row["observation_id"].endswith("prose_mgtio3_delta_fh298_estimate")
    )
    assert approximate_estimate["values"]["value"] == "-856"
    assert approximate_estimate["values"]["approximate"] is True
    assert approximate_estimate["phase"] == "g"
    assert approximate_estimate["per"] == "mol_species"
    assert approximate_estimate["values"]["method_class"] == "quoted_attributed"
    assert approximate_estimate["values"]["attribution"] == "Lopatin and Semenov (2001)"

    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    migrated = [row for row in result.observations.values() if row.source_id == SOURCE_ID]
    assert len(migrated) == 63
    assert sum(row.value.kind is ValueKind.POINT for row in migrated) == 37
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated) == 25
    assert sum(row.value.kind is ValueKind.CATEGORICAL for row in migrated) == 1
    assert sum(row.admission.status is AdmissionStatus.ADMITTED for row in migrated) == 22
    assert sum(row.admission.status is AdmissionStatus.REJECTED for row in migrated) == 41
    assert not result.registry_issues
    assert not result.validation.hard_issues

    for row in migrated:
        temperature = row.point_conditions["temperature_K"]
        assert temperature.locator is not None
        raw_temperature = temperature.state.value
        temperature_kind = getattr(raw_temperature, "kind", ValueKind.POINT)
        if temperature.state.is_value and temperature_kind is ValueKind.POINT:
            assert row.identity.temperature_K is not None
            assert row.identity.temperature_K.is_value
            point = getattr(raw_temperature, "point", raw_temperature)
            assert row.identity.temperature_K.value == point
        else:
            assert row.identity.temperature_K is None or row.identity.temperature_K.is_unknown

    for row in migrated:
        if row.locator.figure is not None:
            assert row.value.kind is ValueKind.UNAVAILABLE
            assert row.admission.status is AdmissionStatus.REJECTED
            composition = row.point_conditions["composition"]
            assert composition.state.is_unknown
            assert composition.locator is not None

    fusion = next(row for row in migrated if row.observation_id.endswith("prose_mgti2o5_heat_of_fusion_bale_2016"))
    assert fusion.value.kind is ValueKind.POINT
    assert fusion.value.point == Decimal("54.343")
    assert fusion.identity.temperature_K.value == Decimal("1930")

    estimate = next(row for row in migrated if row.observation_id.endswith("prose_mgtio3_delta_fh298_estimate"))
    assert estimate.value.kind is ValueKind.POINT
    assert estimate.value.point == Decimal("-856")
    assert estimate.value.approximate
    assert estimate.identity.temperature_K.value == Decimal("298")
