"""Source pins and isolated migration checks for Shornikov (2019) CaO-FeO."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import AdmissionStatus, Phase, Quantity, UncertaintyKind, ValueKind
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
        (
            "1673",
            0,
            0,
            59,
            0.47,
            0.000451,
            [733, 576],
            [21, 30],
            "lmax-r2-a01",
            [1011, 2896],
        ),
        (
            "1673",
            0,
            9,
            79,
            0.76,
            0.000259,
            [1354, 281],
            [62, 70],
            "review-p1-a-10",
            [1590, 2560],
        ),
        (
            "1833",
            1,
            0,
            60,
            0.46,
            0.001424,
            [331, 581],
            [68, 84],
            "review-p1-b-1",
            [2464, 2849],
        ),
        (
            "1833",
            1,
            1,
            62,
            0.42,
            0.000499,
            [382, 617],
            [46, 64],
            "lmax-r2-b02",
            [2535, 2902],
        ),
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
        crop_id,
        crop_origin,
    ) in rendered_figure_reads:
        point = figure_series[series_index]["values"]["points"][point_index]
        assert point["T_K"] == temperature
        assert point["FeO_mol_pct_as_read"] == composition
        assert point["activity"] == activity
        assert point.get("read_uncertainty_dex") == uncertainty
        assert point["read_1"]["pixel_xy_crop"] == pixel_1
        assert point["read_2"]["pixel_xy_crop"] == pixel_2
        assert point["read_2"]["crop_id"] == crop_id
        assert point["read_2"]["crop_origin_xy_page_px"] == crop_origin

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

    all_points = [point for series in figure_series for point in series["values"]["points"]]
    assert len({point["read_2"]["crop_id"] for point in all_points}) == 36
    assert all(point["read_uncertainty_dex"] > 0 for point in all_points)
    # Read 1 was taken in the panel crop. A second read records a different crop
    # origin. Offsets may coincide for a real pair; one shared offset for a whole
    # panel is a translated copy, not a second read.
    panel_origins = ((300, 2350), (2199, 2349))
    for series, origin in zip(figure_series, panel_origins, strict=True):
        offsets = []
        for point in series["values"]["points"]:
            read_1 = point["read_1"]["pixel_xy_crop"]
            read_2 = point["read_2"]
            crop = read_2["crop_origin_xy_page_px"]
            pixel = read_2["pixel_xy_crop"]
            assert crop != [origin[0], origin[1]]
            offsets.append(
                (
                    crop[0] + pixel[0] - origin[0] - read_1[0],
                    crop[1] + pixel[1] - origin[1] - read_1[1],
                )
            )
        assert len(set(offsets)) > 1

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

    panel_b_rows = [
        row
        for row in migrated
        if "shornikov_2019_fig2_panel_b_ban_ya_or_fujita_markers" in row.observation_id
    ]
    assert len(panel_b_rows) == 18
    for row in panel_b_rows:
        assert row.identity.temperature_K.value == Decimal("1833")
        plotted_temperature = row.point_conditions["plotted_panel_temperature_K"]
        assert plotted_temperature.state.is_value
        assert plotted_temperature.state.value.point == Decimal("1873")

    transitions = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrated
        if row.observation_id.endswith("_allen_snow_1955")
    }
    decomposition = transitions["shornikov_2019_c2f2o5_decomposition_allen_snow_1955"]
    dissociation = transitions["shornikov_2019_c2feo3_dissociation_allen_snow_1955"]
    assert decomposition.identity.species.formula == "Ca2Fe2O5"
    assert dissociation.identity.species.formula == "CaFe2O3"
    assert decomposition.uncertainty.kind is UncertaintyKind.PRINTED
    assert dissociation.uncertainty.kind is UncertaintyKind.PRINTED
    assert decomposition.locator.page == dissociation.locator.page == 1
    assert decomposition.locator.published_page == dissociation.locator.published_page == 82

    shugurov = next(row for row in migrated if row.observation_id.endswith("shornikov_2019_cafeo2_delta_fh298_shugurov_2005"))
    assert shugurov.locator.page == 2
    assert shugurov.locator.pdf_page_index == 1
    assert shugurov.locator.published_page == 83

    fig2_curves = [
        row
        for row in migrated
        if row.locator.figure == "2"
        and row.value.kind is ValueKind.UNAVAILABLE
    ]
    assert len(fig2_curves) == 12
    for row in fig2_curves:
        expected_temperature = Decimal("1673") if "panel_a" in row.observation_id else Decimal("1873")
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == expected_temperature

    refusal_ids = {
        "shornikov_2019_cafeo2_sintering_temperature_range_srikanth_bazhin",
        "shornikov_2019_fig3_cao_feo_minimum_mixing_energy_range",
        *(f"shornikov_2019_fig3_cao_feo_panel_{panel}_curve_{curve}" for panel, curves in (("a", (1, 2, 3)), ("b", (4, 5, 6))) for curve in curves),
    }
    refused_source_rows = [
        row
        for block in document["species"].values()
        for row in block.get("context", [])
        if row["observation_id"] in refusal_ids
    ]
    assert len(refused_source_rows) == 8
    assert all(row["type"] == "thermodynamic_example" for row in refused_source_rows)
    assert all(row.get("locator") for row in refused_source_rows)
    assert all(row["values"].get("attribution") for row in refused_source_rows)
    assert all(row["values"].get("reason_no_number") for row in refused_source_rows)
    assert all(
        row["values"].get("sentence_as_published") or row["values"].get("caption_as_published")
        for row in refused_source_rows
    )
    assert all(
        not {"value", "quantity", "T_K", "admission_status"} & row["values"].keys()
        for row in refused_source_rows
    )
    assert not refusal_ids & {row.observation_id.rsplit("::", 1)[-1] for row in migrated}

    for formula, observation_id, value, _ in rendered_cells:
        row = next(
            row
            for row in migrated
            if row.observation_id == f"{document['source_id']}::{observation_id}"
        )
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == Decimal(value)
