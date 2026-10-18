"""Source pins and isolated migration checks for Shornikov (2019) CaO-FeO."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import AdmissionStatus, Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2019-ca-feo-vapor.yaml"


def test_table_and_figure_rows_are_render_pinned_and_migrate_with_conditions():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table_rows = [
        (formula, row)
        for formula, block in document["species"].items()
        for row in block.get("observations", [])
        if row["locator"].get("table") == "1"
    ]
    assert len(table_rows) == 16

    figure_series = [
        row
        for block in document["species"].values()
        for row in block.get("observations", [])
        if row["type"] == "activity_coefficient" and row["values"].get("points")
    ]
    assert len(figure_series) == 2
    assert [len(row["values"]["points"]) for row in figure_series] == [18, 18]

    rendered_figure_reads = (
        ("1673", 0, 0, 59, 0.47, 0.0, [733, 576], [734, 576]),
        ("1673", 0, 9, 79, 0.75, 0.0006, [1354, 281], [1353, 283]),
        ("1873", 1, 0, 60, 0.46, 0.0005, [331, 581], [331, 582]),
    )
    for (
        temperature,
        series_index,
        point_index,
        composition,
        activity,
        uncertainty,
        pixel_1,
        pixel_2,
    ) in rendered_figure_reads:
        point = figure_series[series_index]["values"]["points"][point_index]
        assert point["T_K"] == temperature
        assert point["FeO_mol_pct_as_read"] == composition
        assert point["activity"] == activity
        assert point["read_uncertainty_dex"] == uncertainty
        assert point["read_1"]["pixel_xy_crop"] == pixel_1
        assert point["read_2"]["pixel_xy_crop"] == pixel_2

    rendered_cells = (
        (
            "CaO",
            "shornikov_2019_ca_feo_vapor_p003_t01_r03_c02",
            "-435.296",
            Phase.CR,
        ),
        (
            "CaO",
            "shornikov_2019_ca_feo_vapor_p003_t01_r03_c04",
            "-422.317",
            Phase.L,
        ),
        (
            "Ca",
            "shornikov_2019_ca_feo_vapor_p003_t01_r03_c06",
            "-9.985",
            Phase.G,
        ),
    )
    for formula, observation_id, value, phase in rendered_cells:
        row = next(
            row
            for candidate_formula, row in table_rows
            if candidate_formula == formula
            and row["observation_id"] == observation_id
        )
        assert row["values"]["value"] == value
        assert row["locator"]["pdf_page_index"] == 2
        assert row["locator"]["published_page"] == 84
        assert row["phase"] == phase.value

    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    migrated = [
        row
        for row in result.observations.values()
        if row.source_id == document["source_id"]
    ]
    assert len(migrated) == 68
    assert sum(row.value.kind is ValueKind.POINT for row in migrated) == 56
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated) == 12
    assert sum(
        row.admission.status is AdmissionStatus.ADMITTED
        and row.value.kind is ValueKind.POINT
        for row in migrated
    ) == 20
    assert not result.registry_issues
    assert not result.validation.hard_issues

    for row in migrated:
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value is not None
        assert temperature.locator is not None
        if (
            row.value.kind is ValueKind.POINT
            and row.identity.quantity.value is not Quantity.TRANSITION_TEMPERATURE
        ):
            assert row.identity.temperature_K.is_value
            assert row.identity.temperature_K.value == temperature.state.value

    for row in migrated:
        if row.locator.figure == "2" and row.locator.table is None:
            assert row.identity.composition.is_unknown
            assert row.point_conditions["composition"].locator is not None
            if row.value.kind is ValueKind.POINT:
                assert row.admission.status is AdmissionStatus.REJECTED

    for formula, observation_id, value, _ in rendered_cells:
        row = next(
            row
            for row in migrated
            if row.observation_id == f"{document['source_id']}::{observation_id}"
        )
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == Decimal(value)
