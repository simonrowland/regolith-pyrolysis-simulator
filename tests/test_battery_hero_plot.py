from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.battery_hero_plot import (
    draw_panel,
    element_view_series,
    read_scores,
    render_plots,
    species_tier,
    stamp_text,
    style_for_tier,
)
import matplotlib.pyplot as plt
from simulator.diagnostic_helpers.species_rail import (
    TIER_MAJOR,
    TIER_MINOR,
    TIER_TRACE,
    derive_species_rail,
)


def _record(
    *,
    engine: str,
    experiment: str,
    formula: str,
    temperature_K: int,
    residual: float | None,
    rail: str = "vapour",
    authority: str = "certified",
    status: str = "match",
    version: str = "1.2.3",
    source_relation: str = "independent",
) -> dict[str, object]:
    row: dict[str, object] = {
        "candidate_request": {"engine": engine, "experiment_id": experiment},
        "candidate_observation": {
            "authority": authority,
            "engine": {"name": engine, "version": version},
            "experiment_id": experiment,
            "identity": {
                "species": {"formula": formula},
                "temperature_K": {"tag": "value", "value": str(temperature_K)},
            },
            "notices": [],
        },
        "rail": rail,
        "refusal": None,
        "source_relation": source_relation,
        "status": status,
    }
    if residual is not None:
        row["numeric"] = {"operation": "dex", "unit": "dimensionless", "value": residual}
    if status == "refused":
        row["refusal"] = {"reason": "unsupported_species"}
    return row


def _score_file(path: Path) -> None:
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "test-store"},
        _record(engine="openimcc", experiment="e1", formula="SiO2", temperature_K=1001, residual=0.1),
        _record(engine="openimcc", experiment="e1", formula="SiO2", temperature_K=1001, residual=0.3),
        _record(engine="openimcc", experiment="e2", formula="SiO2", temperature_K=1040, residual=0.4),
        _record(engine="openimcc", experiment="e3", formula="SiO2", temperature_K=1110, residual=0.5),
        _record(engine="openimcc", experiment="e4", formula="SiO2", temperature_K=1120, residual=0.7),
        _record(engine="openimcc", experiment="e5", formula="SiO2", temperature_K=1140, residual=0.9),
        _record(engine="openimcc", experiment="e6", formula="SiO2", temperature_K=1200, residual=0.8),
        _record(engine="openimcc", experiment="e7", formula="SiO2", temperature_K=1220, residual=1.0),
        _record(engine="openimcc", experiment="e8", formula="SiO2", temperature_K=1240, residual=1.2),
        _record(engine="openimcc", experiment="ea3", formula="SiO2", temperature_K=1110, residual=0.2, rail="melt_activity"),
        _record(engine="openimcc", experiment="ea4", formula="SiO2", temperature_K=1120, residual=0.4, rail="melt_activity"),
        _record(engine="openimcc", experiment="ea5", formula="SiO2", temperature_K=1140, residual=0.6, rail="melt_activity"),
        _record(engine="openimcc", experiment="ea6", formula="SiO2", temperature_K=1200, residual=0.6, rail="melt_activity"),
        _record(engine="openimcc", experiment="ea7", formula="SiO2", temperature_K=1220, residual=0.8, rail="melt_activity"),
        _record(engine="openimcc", experiment="ea8", formula="SiO2", temperature_K=1240, residual=1.0, rail="melt_activity"),
        _record(
            engine="openimcc",
            experiment="ea6",
            formula="SiO2",
            temperature_K=1200,
            residual=0.2,
            rail="melt_activity",
            authority="extrapolated",
        ),
        _record(engine="openimcc", experiment="ea9", formula="SiO2", temperature_K=1260, residual=1.2, rail="melt_activity"),
        _record(
            engine="magemin",
            experiment="e9",
            formula="P2O5",
            temperature_K=1200,
            residual=0.4,
            rail="melt_activity",
            source_relation="quoted_overlap",
        ),
        _record(
            engine="magemin",
            experiment="e10",
            formula="Ag",
            temperature_K=1220,
            residual=0.6,
        ),
        _record(
            engine="openimcc",
            experiment="e-refused",
            formula="SiO2",
            temperature_K=1000,
            residual=None,
            status="refused",
        ),
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_binning_averages_same_experiment_duplicates_and_excludes_refusals(tmp_path: Path) -> None:
    residuals = tmp_path / "residuals.jsonl"
    _score_file(residuals)

    data = read_scores(residuals)

    assert data.engines == ("openimcc", "magemin")
    series = data.series[("openimcc", "SiO2", "vapour")]
    points = {point.bin_index: point for point in series}
    assert points[10].residual_dex == pytest.approx(0.3)
    assert points[10].n == 2  # two experiments; the same-experiment copy counts once
    assert points[11].residual_dex == pytest.approx(0.7)
    assert points[11].n == 3
    assert points[12].residual_dex == pytest.approx(1.0)
    assert points[12].n == 3
    assert data.summaries["openimcc"].scored_rows == 17
    assert data.summaries["openimcc"].unique_points == 15
    assert data.summaries["openimcc"].refusals == {"unsupported_species": 1}
    assert data.summaries["magemin"].source_relations == {"independent": 1, "quoted_overlap": 1}
    activity_bin = {
        point.bin_index: point
        for point in data.series[("openimcc", "SiO2", "melt_activity")]
    }[12]
    assert activity_bin.residual_dex == pytest.approx(0.85)
    assert activity_bin.n == 4
    assert activity_bin.flagged
    element_series = element_view_series(data, derive_species_rail())
    element_points = {point.bin_index: point for point in element_series[("openimcc", "Si")]}
    assert element_points[10].residual_dex == pytest.approx(0.3)
    assert element_points[10].n == 2
    assert element_points[11].residual_dex == pytest.approx(0.55)
    assert element_points[11].n == 6
    assert element_points[12].residual_dex == pytest.approx(6.4 / 7)
    assert element_points[12].n == 7
    assert element_points[12].flagged


def test_low_count_bins_are_markers_and_flagged_rows_are_hollow(tmp_path: Path) -> None:
    residuals = tmp_path / "residuals.jsonl"
    _score_file(residuals)
    data = read_scores(residuals)
    species_rail = derive_species_rail()
    figure, axis = plt.subplots()
    try:
        right_axis = draw_panel(axis, data, "openimcc", species_rail)
        series_gid = "series:openimcc:SiO2:vapour"
        series_lines = [line for line in axis.lines if line.get_gid() == series_gid]
        assert len(series_lines) == 1
        assert len(series_lines[0].get_xdata()) == 2
        assert list(series_lines[0].get_xdata()) == pytest.approx([1123.3333333333, 1220.0])
        activity_gid = "series:openimcc:SiO2:melt_activity"
        activity_lines = [line for line in axis.lines if line.get_gid() == activity_gid]
        assert len(activity_lines) == 1
        assert activity_lines[0].get_linestyle() == "--"
        assert {label.get_text() for label in right_axis.get_yticklabels()} == {"O", "Si"}
        assert any(
            artist.get_gid() == f"low-count-marker:{series_gid}"
            for artist in axis.collections
        )
        flagged_gid = "binned-marker:series:openimcc:SiO2:melt_activity"
        flagged = next(
            artist
            for artist in axis.collections
            if artist.get_gid() == flagged_gid
            and float(artist.get_offsets()[0][0]) > 1200
        )
        assert len(flagged.get_facecolors()) == 0
    finally:
        plt.close(figure)

    element_figure, element_axis = plt.subplots()
    try:
        draw_panel(element_axis, data, "openimcc", species_rail, view="element")
        si_gid = "series:openimcc:Si:pooled:element"
        si_lines = [line for line in element_axis.lines if line.get_gid() == si_gid]
        assert len(si_lines) == 1
        assert si_lines[0].get_linestyle() == "--"
        flagged_element_markers = [
            artist
            for artist in element_axis.collections
            if artist.get_gid() == f"binned-marker:{si_gid}"
            and float(artist.get_offsets()[0][0]) > 1200
        ]
        assert len(flagged_element_markers) == 1
        assert len(flagged_element_markers[0].get_facecolors()) == 0
    finally:
        plt.close(element_figure)


def test_existing_element_tiers_drive_plot_styling() -> None:
    species_rail = derive_species_rail()

    assert species_tier("SiO2", species_rail) == TIER_MAJOR
    assert species_tier("P2O5", species_rail) == TIER_MINOR
    assert species_tier("Ag", species_rail) == TIER_TRACE
    assert style_for_tier(TIER_MAJOR)["color"] == "#111111"
    assert style_for_tier(TIER_MAJOR)["linewidth"] == 2.0
    assert style_for_tier(TIER_MINOR)["color"] == "#777777"
    assert style_for_tier(TIER_MINOR)["linewidth"] == 1.2
    assert style_for_tier(TIER_TRACE)["color"] == "#b9b9b9"
    assert style_for_tier(TIER_TRACE)["linewidth"] == 0.5


def test_stamp_contents_and_png_output(tmp_path: Path) -> None:
    residuals = tmp_path / "residuals.jsonl"
    _score_file(residuals)
    data = read_scores(residuals)
    stamp = stamp_text(
        data,
        simulator_commit="sim-test",
        openimcc_commit="openimcc-test",
        render_date="2026-10-15",
    )
    assert "OpenIMCC 1.2.3 · openimcc-test" in stamp
    assert "simulator sim-test" in stamp
    assert "store test-store" in stamp
    assert "rendered 2026-10-15 UTC" in stamp
    assert "patch" not in stamp
    patched = replace(
        data,
        stamp={
            **data.stamp,
            "simulator_modified": True,
            "simulator_patch_sha256": "0123456789abcdef",
            "openimcc_patch_sha256": "abcdef0123456789",
        },
    )
    patched_stamp = stamp_text(
        patched,
        simulator_commit="sim-test",
        openimcc_commit="openimcc-test",
        render_date="2026-10-15",
    )
    assert "patch 0123456789ab" in patched_stamp
    assert "openimcc patch abcdef012345" in patched_stamp
    clean = replace(
        data,
        stamp={
            **data.stamp,
            "simulator_tree_commit": "sim-test",
            "simulator_patch_sha256": "0123456789abcdef",
            "openimcc_installed_commit": "openimcc-test",
            "openimcc_patch_sha256": "abcdef0123456789",
        },
    )
    clean_stamp = stamp_text(
        clean,
        simulator_commit="sim-test",
        openimcc_commit="openimcc-test",
        render_date="2026-10-15",
    )
    assert "patch" not in clean_stamp

    files = render_plots(
        data,
        tmp_path / "images",
        simulator_commit="sim-test",
        openimcc_commit="openimcc-test",
        render_date="2026-10-15",
        species_rail=derive_species_rail(),
    )
    assert files["species:tile_png"].is_file()
    assert files["species:tile_svg"].is_file()
    assert files["species:engine:openimcc"].is_file()
    assert files["element:tile_png"].is_file()
    assert files["element:tile_svg"].is_file()
    assert files["element:engine:openimcc"].is_file()
    assert files["species:tile_png"].read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert "test-store-2026-10-15-species" in files["species:tile_png"].name
    assert "test-store-2026-10-15-element" in files["element:tile_png"].name
