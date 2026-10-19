from __future__ import annotations

import gzip
import io
import json
import re
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

import scripts.battery_hero_plot as hero_plot
from simulator.battery.enums import NoticeKind
from scripts.battery_hero_plot import (
    HERO_MINOR_FACETS,
    HERO_RAILS,
    _draw_hero_current,
    _draw_hero_refusals,
    _draw_hero_sweet_spot,
    _history_record,
    _hero_engine_colors,
    _hero_facets,
    _hero_legend_handles,
    _hero_refusal_entries,
    _refusal_coverage_from_summaries,
    _parser,
    append_history,
    draw_panel,
    element_tier,
    element_view_series,
    history_record_from_archive,
    load_element_abundance,
    read_scores,
    render_hero_charts,
    render_plots,
    species_tier,
    stamp_text,
    style_for_tier,
    TIER_MAJOR,
    TIER_MINOR,
    TIER_TRACE,
)
from matplotlib.colors import ListedColormap, Normalize, to_rgba
from matplotlib.figure import Figure
from matplotlib.text import Text
from matplotlib.ticker import MaxNLocator
import matplotlib.pyplot as plt


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
    flagged_activity = _record(
        engine="openimcc",
        experiment="ea6",
        formula="SiO2",
        temperature_K=1200,
        residual=0.2,
        rail="melt_activity",
        authority="extrapolated",
    )
    flagged_activity["notices"] = [
        {
            "kind": NoticeKind.OUT_OF_CERTIFIED_BAND.value,
            "reason": "extrapolated beyond certified band",
        }
    ]
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
        flagged_activity,
        _record(engine="openimcc", experiment="ea9", formula="SiO2", temperature_K=1260, residual=1.2, rail="melt_activity"),
        _record(
            engine="openimcc",
            experiment="e9",
            formula="P2O5",
            temperature_K=1200,
            residual=0.4,
            rail="melt_activity",
            source_relation="quoted_overlap",
        ),
        _record(
            engine="openimcc",
            experiment="e10",
            formula="Ag",
            temperature_K=1220,
            residual=0.6,
        ),
        _record(
            engine="magemin",
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
    assert data.summaries["openimcc"].scored_rows == 19
    assert data.summaries["openimcc"].unique_points == 17
    assert data.summaries["openimcc"].refusals == {}
    assert data.summaries["magemin"].scored_rows == 0
    assert data.summaries["magemin"].refusals == {}
    assert data.summaries["magemin"].not_observable_by_rail["vapour"] == 1
    assert data.summaries["openimcc"].source_relations["quoted_overlap"] == 1
    activity_bin = {
        point.bin_index: point
        for point in data.series[("openimcc", "SiO2", "melt_activity")]
    }[12]
    assert activity_bin.residual_dex == pytest.approx(0.85)
    assert activity_bin.n == 4
    assert activity_bin.flagged
    element_series = element_view_series(data)
    element_points = {point.bin_index: point for point in element_series[("openimcc", "Si")]}
    assert element_points[10].residual_dex == pytest.approx(0.3)
    assert element_points[10].n == 2
    assert element_points[11].residual_dex == pytest.approx(0.55)
    assert element_points[11].n == 6
    assert element_points[12].residual_dex == pytest.approx(6.4 / 7)
    assert element_points[12].n == 7
    assert element_points[12].flagged


def test_hero_aggregation_float_hex_pin_from_r4_pre_move_tip(tmp_path: Path) -> None:
    """Pin current aggregation outputs before moving them into score.py."""

    residuals = tmp_path / "residuals.jsonl"
    _score_file(residuals)
    data = read_scores(residuals)

    # Captured with the existing plotter at 94a303798889ad5ceab7a4160f67040a29f22f2b.
    species_points = {
        point.bin_index: point
        for point in data.series[("openimcc", "SiO2", "vapour")]
    }
    hero_points = {
        point.bin_index: point
        for point in data.hero_series[("vapour", "SiO2", "openimcc", "unknown")]
    }
    activity_points = {
        point.bin_index: point
        for point in data.series[("openimcc", "SiO2", "melt_activity")]
    }
    element_points = {
        point.bin_index: point
        for point in element_view_series(data)[("openimcc", "Si")]
    }

    assert data.summaries["openimcc"].rms_dex.hex() == "0x1.77a0584f68ef6p-1"
    assert species_points[10].residual_dex.hex() == "0x1.3333333333334p-2"
    assert species_points[10].temperature_K.hex() == "0x1.fe40000000000p+9"
    assert hero_points[10].residual_dex.hex() == "0x1.3333333333334p-2"
    assert activity_points[12].residual_dex.hex() == "0x1.b333333333334p-1"
    assert element_points[12].residual_dex.hex() == "0x1.d41d41d41d41ep-1"
    assert element_points[12].temperature_K.hex() == "0x1.326db6db6db6ep+10"
    assert element_points[12].n == 7


def test_hero_element_pooling_float_hex_pin_preserves_leading_dot_formula(
    tmp_path: Path,
) -> None:
    residuals = tmp_path / "leading-dot.jsonl"
    _score_file(residuals)
    row = _record(
        engine="openimcc",
        experiment="fe-dot",
        formula="Fe.947O",
        temperature_K=1050,
        residual=0.3,
    )
    with residuals.open("a", encoding="utf-8") as target:
        target.write(json.dumps(row) + "\n")

    data = read_scores(residuals)
    element_points = element_view_series(data)
    iron = next(
        point for point in element_points[("openimcc", "Fe")] if point.bin_index == 10
    )
    oxygen = next(
        point for point in element_points[("openimcc", "O")] if point.bin_index == 10
    )

    # Captured with the pre-move plotter at 94a303798889ad5ceab7a4160f67040a29f22f2b.
    assert iron.residual_dex.hex() == "0x1.3333333333333p-2"
    assert iron.temperature_K.hex() == "0x1.0680000000000p+10"
    assert iron.n == 1
    assert oxygen.residual_dex.hex() == "0x1.3333333333334p-2"
    assert oxygen.temperature_K.hex() == "0x1.0195555555555p+10"
    assert oxygen.n == 3


def test_refusal_status_legacy_spellings_are_counted(tmp_path: Path) -> None:
    residuals = tmp_path / "residuals.jsonl"
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "test-store"}
    ]
    for index, status in enumerate(("refused", "typed-refusal", "typed_refusal")):
        row = _record(
            engine="openimcc",
            experiment=f"refused-{index}",
            formula="SiO2",
            temperature_K=1000,
            residual=None,
        )
        row["status"] = status
        row["refusal"] = {"reason": f"reason-{index}"}
        rows.append(row)
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    data = read_scores(residuals)

    assert data.summaries["openimcc"].refusals == {
        "reason-0": 1,
        "reason-1": 1,
        "reason-2": 1,
    }


def test_low_count_bins_are_markers_and_flagged_rows_are_hollow(tmp_path: Path) -> None:
    residuals = tmp_path / "residuals.jsonl"
    _score_file(residuals)
    data = read_scores(residuals)
    abundance = load_element_abundance("lunar_mare_low_ti")
    figure, axis = plt.subplots()
    try:
        right_axis = draw_panel(axis, data, "openimcc", abundance)
        series_gid = "series:openimcc:SiO2:vapour"
        series_lines = [line for line in axis.lines if line.get_gid() == series_gid]
        assert len(series_lines) == 1
        assert len(series_lines[0].get_xdata()) == 2
        assert list(series_lines[0].get_xdata()) == pytest.approx([1123.3333333333, 1220.0])
        activity_gid = "series:openimcc:SiO2:melt_activity"
        activity_lines = [line for line in axis.lines if line.get_gid() == activity_gid]
        assert len(activity_lines) == 1
        assert activity_lines[0].get_linestyle() == "--"
        assert not right_axis.get_yticklabels()
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
        draw_panel(element_axis, data, "openimcc", abundance, view="element")
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


def test_deck_scatter_uses_only_scorer_flag_for_hollow_markers() -> None:
    figure, axis = plt.subplots()
    try:
        hero_plot._draw_hero_deck_scatter(
            axis,
            [
                {
                    "chart": "polymerisation",
                    "engine": "openimcc",
                    "x_value": "1.0",
                    "y_value": "0.25",
                    "flagged": True,
                }
            ],
            engine_colors={"openimcc": "#0072b2"},
        )
        figure.canvas.draw()

        marker = axis.collections[0]
        assert list(marker.get_facecolors()[0]) == pytest.approx(
            list(to_rgba("#ffffff"))
        )
        assert list(marker.get_edgecolors()[0]) == pytest.approx(
            list(to_rgba("#0072b2"))
        )
    finally:
        plt.close(figure)


def test_solved_oxygen_marker_shape_preserves_flag_fill_semantics() -> None:
    engine_color = "#0072b2"
    points = [
        {
            "chart": "redox_sensitivity",
            "engine": "openimcc",
            "x_value": "0.1",
            "y_value": "0.25",
            "oxygen_mode": "solved",
            "marker": "^",
            "flagged": False,
        },
        {
            "chart": "redox_sensitivity",
            "engine": "openimcc",
            "x_value": "0.2",
            "y_value": "0.5",
            "oxygen_mode": "solved",
            "marker": "^",
            "flagged": True,
        },
    ]
    figure, axis = plt.subplots()
    try:
        hero_plot._draw_hero_deck_scatter(
            axis, points, engine_colors={"openimcc": engine_color}
        )
        by_gid = {artist.get_gid(): artist for artist in axis.collections}
        ordinary = by_gid["hero-deck-marker:openimcc:ordinary:^"]
        flagged = by_gid["hero-deck-marker:openimcc:flagged:^"]
        assert list(ordinary.get_facecolors()[0]) == pytest.approx(
            list(to_rgba(engine_color))
        )
        assert list(flagged.get_facecolors()[0]) == pytest.approx(
            list(to_rgba("#ffffff"))
        )

        handles = hero_plot._hero_deck_legend_handles(
            points,
            {"openimcc": engine_color},
            source_labels=(("^", "solved cell oxygen balance"),),
        )
        source_handle = handles[-1]
        assert source_handle.get_marker() == "^"
        assert list(to_rgba(source_handle.get_markerfacecolor())) == pytest.approx(
            list(to_rgba("#555555"))
        )
    finally:
        plt.close(figure)


def test_deck_renderer_emits_all_panels_and_missing_axis_labels(
    tmp_path: Path,
) -> None:
    residuals = tmp_path / "residuals.jsonl"
    _score_file(residuals)
    data = read_scores(residuals)
    history = tmp_path / "history.jsonl"
    history.write_text(
        json.dumps(
            {
                "kind": "battery_hero_history",
                "schema_version": 1,
                "store_sha": "old-store",
                "commit_date": "2026-10-18T10:27:53-04:00",
                "metrics": [
                    {
                        "rail": "vapour",
                        "engine": "openimcc",
                        "n": 1,
                        "rms_dex": "0.2",
                        "median_abs_dex": "0.1",
                    }
                ],
                "benchmarks": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    files = hero_plot.render_hero_deck_charts(
        data, tmp_path / "deck", history_path=history
    )

    for chart in (
        "polymerisation",
        "redox-sensitivity",
        "sio-evolution",
        "residue-composition",
        "fo2-cross-comparison",
    ):
        assert Path(files[f"deck:{chart}:png"]).is_file()
        assert Path(files[f"deck:{chart}:svg"]).is_file()
    cross_svg = Path(files["deck:fo2-cross-comparison:svg"]).read_text(
        encoding="utf-8"
    )
    polymerisation_svg = Path(files["deck:polymerisation:svg"]).read_text(
        encoding="utf-8"
    )
    residue_svg = Path(files["deck:residue-composition:svg"]).read_text(
        encoding="utf-8"
    )
    sio_svg = Path(files["deck:sio-evolution:svg"]).read_text(encoding="utf-8")
    assert "engine-computed fO2 not yet available" in cross_svg
    assert "fraction evaporated: no typed mass-loss value yet (t-1312)" in residue_svg
    assert "no history rows" in sio_svg
    assert "1970-01-01" not in sio_svg
    assert "Green landing date" not in polymerisation_svg


def test_sio_deck_distinguishes_oxygen_sources_and_pO2_bins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    residuals = tmp_path / "residuals.jsonl"
    _score_file(residuals)
    data = read_scores(residuals)
    base = {
        "chart": "sio_evolution",
        "engine": "openimcc",
        "facet": "10^-10–10^-9 bar",
        "x_value": "1200",
        "y_value": "0.2",
        "residual_dex": "0.2",
        "flagged": False,
    }
    points = (
        {
            **base,
            "group_index": -10,
            "group_label": "10^-10–10^-9 bar",
            "oxygen_mode": "solved",
        },
        {
            **base,
            "group_index": -10,
            "group_label": "10^-10–10^-9 bar",
            "oxygen_mode": "printed",
        },
        {
            **base,
            "group_index": -9,
            "group_label": "10^-9–10^-8 bar",
            "oxygen_mode": "solved",
        },
    )
    data = replace(data, hero_deck_points=points)
    drawn_points: list[dict[str, object]] = []
    original_draw = hero_plot._draw_hero_deck_scatter

    def capture_draw(axis, plotted_points, *, engine_colors):
        if plotted_points and plotted_points[0].get("chart") == "sio_evolution":
            drawn_points.extend(plotted_points)
        original_draw(axis, plotted_points, engine_colors=engine_colors)

    monkeypatch.setattr(hero_plot, "_draw_hero_deck_scatter", capture_draw)
    files = hero_plot.render_hero_deck_charts(data, tmp_path / "deck")
    svg = Path(files["deck:sio-evolution:svg"]).read_text(encoding="utf-8")
    by_mode = {
        str(point["oxygen_mode"]): point
        for point in drawn_points
        if point["group_index"] == -10
    }
    solved_later_bin = next(
        point for point in drawn_points if point["group_index"] == -9
    )

    assert by_mode["solved"]["marker"] == "^"
    assert by_mode["printed"]["marker"] == "o"
    assert by_mode["solved"]["marker_size"] != solved_later_bin["marker_size"]
    assert "marker shapes distinguish source" in svg
    assert "10^-10–10^-9 bar" in svg
    assert "10^-9–10^-8 bar" in svg


def test_deck_refusal_strip_includes_missing_scorer_axis(tmp_path: Path) -> None:
    residuals = tmp_path / "residuals.jsonl"
    row = _record(
        engine="openimcc",
        experiment="missing-oxygen-condition",
        formula="Fe",
        temperature_K=1200,
        residual=0.25,
    )
    row["reference"] = "missing-deck-source::missing-oxygen-condition"
    residuals.write_text(
        "".join(
            json.dumps(item) + "\n"
            for item in (
                {"kind": "battery_store_stamp", "revision": "test-store"},
                row,
            )
        ),
        encoding="utf-8",
    )

    data = read_scores(residuals)
    assert data.hero_deck_axis_refusals[
        ("redox_sensitivity", "openimcc", "no fO2 for this row")
    ] == 1
    files = hero_plot.render_hero_deck_charts(data, tmp_path / "deck")
    redox_svg = Path(files["deck:redox-sensitivity:svg"]).read_text(
        encoding="utf-8"
    )
    assert "no fO2 for this row" in redox_svg


def test_deck_refusal_strip_height_scales_with_reason_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    residuals = tmp_path / "residuals.jsonl"
    _score_file(residuals)
    data = read_scores(residuals)
    reasons = 14
    data.hero_deck_axis_refusals.update(
        {
            ("polymerisation", "openimcc", f"deck-reason-{index}"): 1
            for index in range(reasons)
        }
    )

    original_draw = hero_plot._draw_hero_refusals
    measured_heights: list[tuple[int, float]] = []

    def capture_height(axis, entries, *, engine_colors):
        original_draw(axis, entries, engine_colors=engine_colors)
        count = len({reason for _engine, reason, _count in entries})
        if count >= reasons:
            height_inches = axis.get_position().height * axis.figure.get_figheight()
            measured_heights.append((count, height_inches))

    monkeypatch.setattr(hero_plot, "_draw_hero_refusals", capture_height)
    hero_plot.render_hero_deck_charts(data, tmp_path / "deck")

    assert measured_heights
    count, height_inches = measured_heights[0]
    assert count >= reasons
    assert height_inches >= count * 0.12


@pytest.mark.parametrize("view", ("species", "element"))
def test_clipped_flagged_points_keep_a_hollow_marker(tmp_path: Path, view: str) -> None:
    residuals = tmp_path / "residuals.jsonl"
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "test-store"}
    ]
    for index, (authority, residual) in enumerate(
        (
            ("certified", 4.5),
            ("extrapolated", 4.5),
            ("certified", -4.5),
            ("extrapolated", -4.5),
        )
    ):
        row = _record(
            engine="openimcc",
            experiment=f"clipped-{index}",
            formula="SiO2",
            temperature_K=1000 + index * 100,
            residual=residual,
            authority=authority,
        )
        if authority == "extrapolated":
            row["notices"] = [
                {
                    "kind": NoticeKind.OUT_OF_CERTIFIED_BAND.value,
                    "reason": "extrapolated beyond certified band",
                }
            ]
        rows.append(row)
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    data = read_scores(residuals)
    figure, axis = plt.subplots()
    try:
        draw_panel(axis, data, "openimcc", load_element_abundance(), view=view)
        clipped_arrows = [
            artist
            for artist in axis.texts
            if (artist.get_gid() or "").startswith("clipped-arrow:")
        ]
        flagged_markers = [
            artist
            for artist in axis.collections
            if (artist.get_gid() or "").startswith("clipped-flag-marker:")
        ]
        assert len(clipped_arrows) == (4 if view == "species" else 8)
        assert len(flagged_markers) == (2 if view == "species" else 4)
        assert all(len(marker.get_facecolors()) == 0 for marker in flagged_markers)
    finally:
        plt.close(figure)


def test_hero_hollow_encoding_uses_only_scorer_flags(tmp_path: Path) -> None:
    residuals = tmp_path / "flag-sources.jsonl"
    ordinary = _record(
        engine="openimcc",
        experiment="authority-only",
        formula="SiO",
        temperature_K=1200,
        residual=0.2,
        authority="extrapolated",
    )
    bridge = _record(
        engine="openimcc",
        experiment="bridge-authority",
        formula="SiO2",
        temperature_K=1300,
        residual=0.4,
        authority="bridge",
    )
    flagged = _record(
        engine="openimcc",
        experiment="scorer-flag",
        formula="CaO",
        temperature_K=1200,
        residual=0.3,
    )
    flagged["notices"] = [
        {
            "kind": NoticeKind.UNVERIFIED_APPARATUS.value,
            "reason": "apparatus_unverified:test",
        }
    ]
    rows = [
        {"kind": "battery_store_stamp", "revision": "flag-sources"},
        ordinary,
        bridge,
        flagged,
    ]
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    data = read_scores(residuals, rails=HERO_RAILS)

    ordinary_point = data.hero_series[("vapour", "SiO", "openimcc", "unknown")][0]
    flagged_point = data.hero_series[("vapour", "CaO", "openimcc", "unknown")][0]
    bridge_point = data.series[("openimcc", "SiO2", "vapour")][0]
    assert not ordinary_point.flagged
    assert flagged_point.flagged
    assert not bridge_point.flagged

    tile_flagged_label = next(
        handle.get_label()
        for handle in hero_plot._legend_handles()
        if handle.get_markerfacecolor() == "none"
    )
    hero_flagged_label = next(
        handle.get_label()
        for handle in _hero_legend_handles(
            data, "vapour", _hero_engine_colors(data.engines)
        )
        if handle.get_markerfacecolor() == "#ffffff"
    )
    assert tile_flagged_label == hero_flagged_label == "flagged / extrapolated"

    figure, axis = plt.subplots()
    try:
        _draw_hero_current(
            axis,
            data,
            "vapour",
            "SiO",
            engine_colors={"openimcc": "#123456"},
        )
        ordinary_artist = axis.collections[0]
        assert ordinary_artist.get_gid().endswith(":ordinary")
        assert ordinary_artist.get_facecolors()[0] == pytest.approx(to_rgba("#123456"))
        _draw_hero_current(
            axis,
            data,
            "vapour",
            "CaO",
            engine_colors={"openimcc": "#123456"},
        )
        flagged_artist = axis.collections[1]
        assert flagged_artist.get_gid().endswith(":flagged")
        assert flagged_artist.get_facecolors()[0] == pytest.approx(to_rgba("#ffffff"))
        assert flagged_artist.get_edgecolors()[0] == pytest.approx(to_rgba("#123456"))
    finally:
        plt.close(figure)

    tile_figure, tile_axis = plt.subplots()
    try:
        draw_panel(tile_axis, data, "openimcc", load_element_abundance())
        bridge_marker = next(
            artist
            for artist in tile_axis.collections
            if (artist.get_gid() or "").startswith(
                "low-count-marker:series:openimcc:SiO2:vapour"
            )
        )
        assert bridge_marker.get_facecolors()[0] == pytest.approx(
            to_rgba(style_for_tier(TIER_MAJOR)["color"])
        )
    finally:
        plt.close(tile_figure)


def test_species_view_labels_major_curves_and_element_view_labels_elements(
    tmp_path: Path,
) -> None:
    residuals = tmp_path / "residuals.jsonl"
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "test-store"}
    ]
    for index, (formula, rail, residual) in enumerate(
        (
            ("Al", "vapour", -1.5),
            ("Al2O", "vapour", -1.0),
            ("SiO", "vapour", 0.5),
            ("MgO", "melt_activity", 1.5),
        )
    ):
        rows.append(
            _record(
                engine="openimcc",
                experiment=f"major-{index}",
                formula=formula,
                temperature_K=1000 + index * 100,
                residual=residual,
                rail=rail,
            )
        )
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    data = read_scores(residuals)
    abundance = load_element_abundance()

    species_figure, species_axis = plt.subplots()
    try:
        species_right_axis = draw_panel(
            species_axis, data, "openimcc", abundance, view="species"
        )
        labels = [
            artist
            for artist in species_axis.texts
            if (artist.get_gid() or "").startswith("major-label:")
        ]
        assert {artist.get_text() for artist in labels} == {
            "Al",
            "Al2O",
            "SiO",
            "MgO activity",
        }
        assert all(artist.arrow_patch is not None for artist in labels)
        positions = sorted(float(artist.get_position()[1]) for artist in labels)
        assert all(right - left >= 0.35 for left, right in zip(positions, positions[1:]))
        assert not species_right_axis.get_yticklabels()
    finally:
        plt.close(species_figure)

    element_figure, element_axis = plt.subplots()
    try:
        element_right_axis = draw_panel(
            element_axis, data, "openimcc", abundance, view="element"
        )
        assert {label.get_text() for label in element_right_axis.get_yticklabels()} == {
            "Al",
            "Mg",
            "O",
            "Si",
        }
        assert not any(
            (artist.get_gid() or "").startswith("major-label:")
            for artist in element_axis.texts
        )
    finally:
        plt.close(element_figure)


def test_d139_rows_are_excluded_from_panels_and_reported_in_footer(
    tmp_path: Path,
) -> None:
    residuals = tmp_path / "residuals.jsonl"
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "test-store"}
    ]
    rows.append(
        _record(
            engine="openimcc",
            experiment="ordinary-1",
            formula="SiO2",
            temperature_K=1000,
            residual=1.0,
        )
    )
    uncalibrated = _record(
        engine="openimcc",
        experiment="d139-openimcc",
        formula="SiO2",
        temperature_K=1100,
        residual=4.5,
        status="no_band",
    )
    uncalibrated["candidate_observation"]["identity"]["quantity"] = "p_partial"
    uncalibrated["candidate_observation"]["notices"] = [
        {
            "kind": NoticeKind.UNVERIFIED_APPARATUS.value,
            "reason": "calibration_not_grounded: No calibration entry has been recorded yet.",
        }
    ]
    uncalibrated["numeric"]["decision_band"] = None
    rows.append(uncalibrated)

    unmarked_no_band = _record(
        engine="openimcc",
        experiment="ordinary-no-band",
        formula="SiO2",
        temperature_K=1200,
        residual=3.0,
        status="no_band",
    )
    unmarked_no_band["numeric"]["decision_band"] = None
    rows.append(unmarked_no_band)

    uncalibrated_other_rail = _record(
        engine="openimcc",
        experiment="d139-openimcc-melt-activity",
        formula="SiO2",
        temperature_K=1150,
        residual=2.5,
        rail="melt_activity",
        status="no_band",
    )
    uncalibrated_other_rail["candidate_observation"]["identity"]["quantity"] = "p_partial"
    uncalibrated_other_rail["candidate_observation"]["notices"] = [
        {
            "kind": NoticeKind.UNVERIFIED_APPARATUS.value,
            "reason": "calibration_not_grounded: No calibration entry has been recorded yet.",
        }
    ]
    uncalibrated_other_rail["numeric"]["decision_band"] = None
    rows.append(uncalibrated_other_rail)

    other_engine = _record(
        engine="thermoengine",
        experiment="d139-thermoengine",
        formula="Al2O",
        temperature_K=1100,
        residual=-4.5,
        status="no_band",
    )
    other_engine["candidate_observation"]["identity"]["quantity"] = "p_partial"
    other_engine["candidate_observation"]["notices"] = [
        {
            "kind": NoticeKind.UNVERIFIED_APPARATUS.value,
            "reason": "calibration_not_grounded: No calibration entry has been recorded yet.",
        }
    ]
    other_engine["numeric"]["decision_band"] = None
    rows.append(other_engine)

    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    data = read_scores(residuals)

    openimcc = data.summaries["openimcc"]
    assert openimcc.excluded_d139_rows == 2
    assert openimcc.scored_rows == 2
    assert openimcc.unique_points == 2
    assert openimcc.rms_dex == pytest.approx(5**0.5)
    assert [
        point.bin_index for point in data.series["openimcc", "SiO2", "vapour"]
    ] == [10, 12]
    assert data.summaries["thermoengine"].excluded_d139_rows == 1
    assert data.summaries["thermoengine"].scored_rows == 0

    files = render_plots(
        data,
        tmp_path / "images",
        simulator_commit="sim-test",
        openimcc_commit="openimcc-test",
        render_date="2026-10-15",
        group_by=("species",),
        omit_empty_engines=True,
    )
    footer = files["species:tile_svg"].read_text(encoding="utf-8")
    assert (
        "excluded under d-139: openimcc 2 uncalibrated KEMS p_partial, "
        "thermoengine 1 uncalibrated KEMS p_partial"
    ) in footer
    hero_files = render_hero_charts(data, tmp_path / "hero-images")
    hero_caption = hero_files["hero:vapour:svg"].read_text(encoding="utf-8").casefold()
    assert (
        "vapour scatter excludes uncalibrated kems p_partial rows under d-139: "
        "openimcc 1, thermoengine 1; scorer all-numeric and same-row rms include them."
    ) in hero_caption


def test_lunar_feedstock_abundance_drives_plot_styling() -> None:
    abundance = load_element_abundance("lunar_mare_low_ti")

    assert abundance.oxide_wt_pct["Si"] == pytest.approx(44.5)
    assert abundance.oxide_wt_pct["Ti"] == pytest.approx(1.5)
    for element in ("Si", "Fe", "Mg", "Ca", "Al", "Ti"):
        assert element_tier(element, abundance) == TIER_MAJOR
    assert element_tier("O", abundance) == TIER_MAJOR
    for element in ("Na", "K", "Cr", "Mn", "P"):
        assert element_tier(element, abundance) == TIER_MINOR
    assert species_tier("Na2O", abundance) == TIER_MINOR
    assert species_tier("P2O5", abundance) == TIER_MINOR
    assert species_tier("Ag", abundance) == TIER_TRACE
    assert style_for_tier(TIER_MAJOR)["color"] == "#111111"
    assert style_for_tier(TIER_MAJOR)["linewidth"] == 2.0
    assert style_for_tier(TIER_MINOR)["color"] == "#777777"
    assert style_for_tier(TIER_MINOR)["linewidth"] == 1.2
    assert style_for_tier(TIER_TRACE)["color"] == "#b9b9b9"
    assert style_for_tier(TIER_TRACE)["linewidth"] == 0.5


def test_hero_series_keep_composition_and_count_refusals_by_reason(
    tmp_path: Path,
) -> None:
    residuals = tmp_path / "residuals.jsonl"
    first = _record(
        engine="internal-analytical",
        experiment="same-a",
        formula="SiO",
        temperature_K=1200,
        residual=0.2,
        rail="vapour",
    )
    second = _record(
        engine="internal-analytical",
        experiment="same-b",
        formula="SiO",
        temperature_K=1200,
        residual=0.4,
        rail="vapour",
    )
    first["candidate_observation"]["identity"]["composition"] = {
        "tag": "value",
        "value": {"amount_basis": "mole_fraction", "components": [["CaO", "0.4"]]},
    }
    second["candidate_observation"]["identity"]["composition"] = {
        "tag": "value",
        "value": {"amount_basis": "mole_fraction", "components": [["CaO", "0.7"]]},
    }
    refused = _record(
        engine="vaporock",
        experiment="refused-P",
        formula="P2O5",
        temperature_K=1200,
        residual=None,
        rail="vapour",
        status="refused",
    )
    refused["refusal"] = {"reason": "identity_incomplete"}
    residuals.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                {"kind": "battery_store_stamp", "revision": "hero-test"},
                first,
                second,
                refused,
            )
        ),
        encoding="utf-8",
    )

    data = read_scores(residuals, rails=HERO_RAILS)

    composition_keys = {
        composition
        for rail, facet, engine, composition in data.hero_series
        if (rail, facet, engine) == ("vapour", "SiO", "internal-analytical")
    }
    assert len(composition_keys) == 2
    assert all(key != "unknown" for key in composition_keys)
    assert {
        point.composition_sio2_mole_fraction
        for (rail, facet, engine, _composition), points in data.hero_series.items()
        if (rail, facet, engine) == ("vapour", "SiO", "internal-analytical")
        for point in points
    } == {0.0}
    element_points = element_view_series(data)[
        ("internal-analytical", "Si")
    ]
    assert {point.composition_key for point in element_points} == composition_keys
    assert data.hero_refusal_totals[("vapour", "vaporock", "identity_incomplete")] == 1
    assert data.hero_diagnostics[("vapour", "vaporock")]["refused_count"] == 1
    assert data.hero_diagnostics[("vapour", "vaporock")]["refused_by_reason"] == {
        "identity_incomplete": 1
    }
    assert _hero_refusal_entries(data, "vapour", "minor:P") == (
        ("vaporock", "identity_incomplete", 1),
    )
    assert set(_hero_facets(data, "vapour")) >= {
        "SiO",
        "P2O5",
        *(facet for facet, _label in HERO_MINOR_FACETS),
    }
    legend_handles = _hero_legend_handles(
        data, "vapour", _hero_engine_colors(data.engines)
    )
    assert len(legend_handles) <= len(data.engines) + 2
    assert [handle.get_label() for handle in legend_handles].count(
        "internal-analytical"
    ) == 1

    files = render_hero_charts(data, tmp_path / "collapsed-facets")
    svg = files["hero:vapour:svg"].read_text(encoding="utf-8")
    assert svg.count("No numeric predictions yet:") == 1
    assert svg.count("Minor species / foulants (no numeric predictions yet):") == 1
    assert "P2O5 (refused 1: identity_incomplete 1" in svg
    assert "P (refused 1: identity_incomplete 1" in svg
    assert "minor:P" not in svg


def test_non_observable_rows_are_separate_from_refusals_and_history(
    tmp_path: Path,
) -> None:
    residuals = tmp_path / "residuals.jsonl"
    not_observable = _record(
        engine="magemin",
        experiment="magemin-vapour",
        formula="P2O5",
        temperature_K=1400,
        residual=None,
        rail="vapour",
        status="refused",
    )
    not_observable["refusal"] = {"reason": "unsupported"}
    refused = _record(
        engine="vaporock",
        experiment="vaporock-refusal",
        formula="SiO2",
        temperature_K=1400,
        residual=None,
        rail="vapour",
        status="refused",
    )
    refused["refusal"] = {"reason": "outside_supported_species"}
    residuals.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                {"kind": "battery_store_stamp", "revision": "non-observable-test"},
                not_observable,
                refused,
            )
        ),
        encoding="utf-8",
    )

    data = read_scores(residuals, rails=HERO_RAILS)

    assert data.hero_refusal_totals == {
        ("vapour", "vaporock", "outside_supported_species"): 1
    }
    assert data.hero_diagnostics[("vapour", "magemin")]["refused_count"] == 0
    assert data.hero_diagnostics[("vapour", "magemin")]["not_observable_count"] == 1
    assert data.hero_diagnostics[("vapour", "magemin")]["refused_by_reason"] == {}
    assert data.summaries["magemin"].refusals == {}
    assert data.summaries["magemin"].not_observable_by_rail["vapour"] == 1

    record = _history_record(
        {"store": {"revision": "non-observable-test"}, "records": []},
        landing_sha="non-observable-test",
        commit_date="2026-10-19T00:00:00Z",
        benchmarks=data.hero_benchmarks,
        diagnostics=data.hero_diagnostics,
        refusal_coverage=_refusal_coverage_from_summaries(data.summaries),
    )
    metric = next(
        item
        for item in record["metrics"]
        if item["rail"] == "vapour" and item["engine"] == "magemin"
    )
    assert metric["not_observable_count"] == 1
    assert metric["refused_count"] == 0
    assert metric["refused_by_reason"] == {}
    coverage = next(
        item
        for item in record["refusal_coverage"]
        if item["rail"] == "vapour" and item["engine"] == "magemin"
    )
    assert coverage["not_observable_count"] == 1
    assert coverage["refused_count"] == 0
    assert any(
        item["rail"] == "thermochemistry" and item["engine"] == "magemin"
        for item in record["refusal_coverage"]
    )

    files = render_hero_charts(data, tmp_path / "rendered")
    svg = files["hero:vapour:svg"].read_text(encoding="utf-8").casefold()
    assert "not an observable of this engine" in svg
    assert "magemin 1" in svg


def test_sweet_spot_panels_show_shared_cell_counts_and_hatch_sparse_cells(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "sweet-spot-test"}
    ]
    for engine, experiment, residual, temperature in (
        ("internal-analytical", "ia-1", 0.2, 1200),
        ("internal-analytical", "ia-2", -0.8, 1200),
        ("internal-analytical", "ia-3", 1.0, 1200),
        ("openimcc", "open-1", 0.4, 1200),
        ("openimcc", "open-2", 0.6, 1400),
    ):
        row = _record(
            engine=engine,
            experiment=experiment,
            formula="SiO",
            temperature_K=temperature,
            residual=residual,
            rail="vapour",
        )
        row["candidate_observation"]["identity"]["composition"] = {
            "tag": "value",
            "value": {
                "amount_basis": "mole_fraction",
                "components": [["SiO2", "0.25"], ["FeO", "0.75"]],
            },
        }
        rows.append(row)

    residuals = tmp_path / "sweet-spot.jsonl"
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    data = read_scores(residuals, rails=HERO_RAILS)
    internal_cells = data.hero_sweet_spot_cells[("vapour", "internal-analytical")]
    openimcc_cells = data.hero_sweet_spot_cells[("vapour", "openimcc")]
    internal_cell = next(cell for cell in internal_cells if cell["n"] == 3)
    openimcc_cell = next(cell for cell in openimcc_cells if cell["n"] == 1)
    assert internal_cell["median_abs_dex"] == "0.8"
    assert internal_cell["sio2_lower"] == openimcc_cell["sio2_lower"] == "0.25"
    assert (
        internal_cell["temperature_lower_K"]
        == openimcc_cell["temperature_lower_K"]
    )
    all_cells = [*internal_cells, *openimcc_cells]
    x_limits = (
        min(float(cell["sio2_lower"]) for cell in all_cells),
        max(float(cell["sio2_upper"]) for cell in all_cells),
    )
    temperature_limits = (
        min(float(cell["temperature_lower_K"]) for cell in all_cells),
        max(float(cell["temperature_upper_K"]) for cell in all_cells),
    )

    fig, axis = plt.subplots()
    _draw_hero_sweet_spot(
        axis,
        "openimcc",
        openimcc_cells,
        colormap=plt.get_cmap("viridis"),
        normalization=Normalize(vmin=0, vmax=1),
        x_limits=x_limits,
        temperature_limits=temperature_limits,
    )
    assert axis.get_xlim() == x_limits
    assert axis.get_ylim() == temperature_limits
    assert any(patch.get_hatch() == "///" for patch in axis.patches)
    assert "1" in {text.get_text() for text in axis.texts}
    assert "0" not in {text.get_text() for text in axis.texts}
    assert len(axis.patches) == sum(int(cell["n"]) > 0 for cell in openimcc_cells)
    plt.close(fig)

    import scripts.battery_hero_plot as plotter

    rendered_limits: list[tuple[tuple[float, float], tuple[float, float]]] = []
    original_draw = plotter._draw_hero_sweet_spot

    def capture_limits(axis, engine, cells, **kwargs):
        drawn = original_draw(axis, engine, cells, **kwargs)
        if drawn:
            rendered_limits.append((axis.get_xlim(), axis.get_ylim()))
        return drawn

    monkeypatch.setattr(plotter, "_draw_hero_sweet_spot", capture_limits)
    files = render_hero_charts(data, tmp_path / "sweet-spot-render")
    assert len(rendered_limits) == 2
    assert rendered_limits[0] == rendered_limits[1]
    svg = files["hero:vapour:svg"].read_text(encoding="utf-8").casefold()
    assert "cell label=n" in svg
    assert "sweet-spot fill=median" in svg
    assert (
        "scatter marker face=engine colour; flagged/extrapolated=white face with "
        "engine-coloured edge"
    ) in svg
    assert "marker edge=sio₂ mole fraction" not in svg
    assert "1≤n&lt;3 is grey and hatched; n=0 is blank" in svg
    assert files["hero:vapour:png"].read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    melt_svg = files["hero:melt_activity:svg"].read_text(encoding="utf-8").casefold()
    assert "140/147 rows" in melt_svg
    assert "main's fix is pending" in melt_svg


def test_sweet_spot_plot_uses_scorer_color_eligibility() -> None:
    cell = {
        "sio2_lower": "0.2",
        "sio2_upper": "0.25",
        "temperature_lower_K": "1900",
        "temperature_upper_K": "2000",
        "n": 1,
        "color_eligible": True,
        "median_abs_dex": "0.25",
    }
    colormap = plt.get_cmap("viridis")
    fig, axis = plt.subplots()
    try:
        assert _draw_hero_sweet_spot(
            axis,
            "openimcc",
            [cell],
            colormap=colormap,
            normalization=Normalize(vmin=0, vmax=1),
            x_limits=(0.2, 0.25),
            temperature_limits=(1900, 2000),
        )
        assert axis.patches[0].get_facecolor() == pytest.approx(
            colormap(0.25)
        )
        assert axis.patches[0].get_hatch() is None
    finally:
        plt.close(fig)


def test_sweet_spot_count_text_contrasts_with_viridis_fill() -> None:
    viridis = plt.get_cmap("viridis")
    fills = [viridis(index / 10) for index in range(11)] + [
        (0.395, 0.797, 0.368, 1.0)
    ]
    colormap = ListedColormap(fills)
    cells = [
        {
            "sio2_lower": str(index * 0.05),
            "sio2_upper": str((index + 1) * 0.05),
            "temperature_lower_K": "1000",
            "temperature_upper_K": "1100",
            "n": 3,
            "color_eligible": True,
            "median_abs_dex": str(index),
        }
        for index in range(len(fills))
    ]
    figure, axis = plt.subplots()
    try:
        assert _draw_hero_sweet_spot(
            axis,
            "openimcc",
            cells,
            colormap=colormap,
            normalization=Normalize(vmin=0, vmax=len(fills) - 1),
            x_limits=(0, len(fills) * 0.05),
            temperature_limits=(1000, 1100),
        )
        assert [patch.get_facecolor() for patch in axis.patches] == pytest.approx(
            fills
        )
        # WCAG contrast winners for viridis at 0.0 through 1.0, followed by
        # the review's actual mid-tone green cell. At 0.5 neither candidate
        # reaches 4.5:1, so the higher-contrast dark text is expected.
        assert [text.get_color() for text in axis.texts] == [
            "#f7f7f7",
            "#f7f7f7",
            "#f7f7f7",
            "#f7f7f7",
            "#f7f7f7",
            "#222222",
            "#222222",
            "#222222",
            "#222222",
            "#222222",
            "#222222",
            "#222222",
        ]
        assert hero_plot._wcag_contrast_ratio("#000000", "#ffffff") == pytest.approx(
            21.0
        )
        assert hero_plot._wcag_contrast_ratio(
            fills[-1], "#222222"
        ) == pytest.approx(7.785, abs=0.001)
        assert hero_plot._wcag_contrast_ratio(
            fills[-1], "#f7f7f7"
        ) == pytest.approx(1.908, abs=0.001)
        for fill, label in zip(fills, axis.texts, strict=True):
            chosen = hero_plot._wcag_contrast_ratio(fill, label.get_color())
            best = max(
                hero_plot._wcag_contrast_ratio(fill, "#222222"),
                hero_plot._wcag_contrast_ratio(fill, "#f7f7f7"),
            )
            if best >= 4.5:
                assert chosen >= 4.5
            else:
                assert chosen == pytest.approx(best)
    finally:
        plt.close(figure)


def test_refusal_strip_uses_integer_unique_ticks() -> None:
    fig, axis = plt.subplots()
    _draw_hero_refusals(
        axis,
        [
            ("openimcc", "unsupported", 37),
            ("magemin", "unsupported", 22),
            ("openimcc", "out_of_basis", 11),
        ],
        engine_colors={"openimcc": "#2244aa", "magemin": "#888888"},
    )

    assert isinstance(axis.xaxis.get_major_locator(), MaxNLocator)
    assert all(float(tick).is_integer() for tick in axis.get_xticks())
    labels = [label.get_text() for label in axis.get_xticklabels()]
    assert len(labels) == len(set(labels))
    assert axis.get_xlabel() == ""
    plt.close(fig)


def test_refusal_ticks_do_not_overlap_the_next_facet_title(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "strip-spacing-test"}
    ]
    for facet_index, formula in enumerate(
        ("Al2O3", "CaO", "FeO", "K2O", "MgO", "SiO2")
    ):
        rows.append(
            _record(
                engine="openimcc",
                experiment=f"numeric-{facet_index}",
                formula=formula,
                temperature_K=1935 + facet_index,
                residual=0.25,
            )
        )
        for refusal_index in range(2):
            refused = _record(
                engine="openimcc",
                experiment=f"refused-{facet_index}-{refusal_index}",
                formula=formula,
                temperature_K=1935 + facet_index,
                residual=None,
                status="refused",
            )
            refused["refusal"] = {"reason": "identity_unknown"}
            rows.append(refused)

    residuals = tmp_path / "strip-spacing.jsonl"
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    data = read_scores(residuals, rails=HERO_RAILS)
    captured: list[Figure] = []
    import scripts.battery_hero_plot as plotter

    close = plt.close
    monkeypatch.setattr(plotter.plt, "close", lambda figure: captured.append(figure))
    try:
        render_hero_charts(data, tmp_path / "strip-spacing-render")
    finally:
        monkeypatch.setattr(plotter.plt, "close", close)

    try:
        vapor = captured[0]
        vapor.canvas.draw()
        renderer = vapor.canvas.get_renderer()
        titles = [
            (axis, axis._left_title, axis._left_title.get_window_extent(renderer))
            for axis in vapor.axes
            if axis.get_title(loc="left")
        ]
        assert len(titles) >= 6
        tick_labels = [
            (axis, label, label.get_window_extent(renderer))
            for axis in vapor.axes
            for label in (*axis.get_xticklabels(), *axis.get_yticklabels())
            if label.get_visible() and label.get_text()
        ]
        collisions = [
            (label.get_text(), title.get_text())
            for label_axis, label, label_box in tick_labels
            for title_axis, title, title_box in titles
            if label_axis is not title_axis and label_box.overlaps(title_box)
        ]
        assert not collisions
    finally:
        for figure in captured:
            close(figure)


def test_temperature_tick_labels_are_unique_at_three_significant_figures(
    tmp_path: Path,
) -> None:
    residuals = tmp_path / "temperature-ticks.jsonl"
    rows = [
        {"kind": "battery_store_stamp", "revision": "temperature-ticks-test"},
        _record(
            engine="openimcc",
            experiment="t1935",
            formula="SiO2",
            temperature_K=1935,
            residual=0.25,
        ),
        _record(
            engine="openimcc",
            experiment="t1940",
            formula="SiO2",
            temperature_K=1940,
            residual=0.5,
        ),
    ]
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    data = read_scores(residuals, rails=HERO_RAILS)
    figure, axis = plt.subplots()
    try:
        _draw_hero_current(
            axis,
            data,
            "vapour",
            "SiO2",
            engine_colors={"openimcc": "#0072b2"},
        )
        axis.set_xlim(1935, 1940)
        figure.canvas.draw()
        labels = [
            label.get_text()
            for label in axis.get_xticklabels()
            if label.get_visible() and label.get_text()
        ]
        assert labels
        assert len(labels) == len(set(labels))
    finally:
        plt.close(figure)


def test_hero_flagged_points_are_hollow_and_benchmark_uses_shared_rows(
    tmp_path: Path,
) -> None:
    residuals = tmp_path / "residuals.jsonl"
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "hero-test"}
    ]
    for engine, experiment, value, authority in (
        ("internal-analytical", "shared", 3.0, "extrapolated"),
        ("internal-analytical", "internal-only", 100.0, "certified"),
        ("openimcc", "shared", 1.0, "certified"),
        ("openimcc", "comparison-only", 100.0, "certified"),
        ("thermoengine", "shared", 2.0, "certified"),
    ):
        row = _record(
            engine=engine,
            experiment=experiment,
            formula="FeO",
            temperature_K=1550 if experiment == "internal-only" else 1300,
            residual=value,
            rail="redox",
            authority=authority,
        )
        row["reference"] = experiment
        row["candidate_observation"]["identity"]["quantity"] = {
            "tag": "value",
            "value": "fe3_fe2_ratio",
        }
        if engine == "internal-analytical" and authority == "extrapolated":
            row["notices"] = [
                {
                    "kind": NoticeKind.OUT_OF_CERTIFIED_BAND.value,
                    "reason": "extrapolated beyond certified band",
                }
            ]
        rows.append(row)
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    data = read_scores(residuals, rails=HERO_RAILS)

    benchmark = data.hero_benchmarks[("redox", "internal-analytical")]
    assert benchmark["benchmark_engine"] == "thermoengine"
    assert benchmark["n"] == 1
    assert benchmark["primary_rms_dex"] == "3.0"
    assert benchmark["benchmark_rms_dex"] == "2.0"
    assert data.hero_benchmarks[("redox", "openimcc")]["benchmark_engine"] == "thermoengine"
    assert data.hero_diagnostics[("redox", "internal-analytical")][
        "open_predict_and_flag_notice_counts_by_kind"
    ] == {NoticeKind.OUT_OF_CERTIFIED_BAND.value: 1}
    assert "Fe3+/Fe2+ ratio" in _hero_facets(data, "redox")

    figure, axis = plt.subplots()
    try:
        _draw_hero_current(
            axis,
            data,
            "redox",
            "Fe3+/Fe2+ ratio",
            engine_colors={"internal-analytical": "#111111"},
        )
        flagged = [
            artist
            for artist in axis.collections
            if (artist.get_gid() or "").endswith(":flagged")
        ]
        assert len(flagged) == 1
        assert flagged[0].get_facecolors()[0] == pytest.approx(to_rgba("#ffffff"))
        assert flagged[0].get_edgecolors()[0] == pytest.approx(to_rgba("#111111"))
        ordinary = next(
            artist
            for artist in axis.collections
            if (artist.get_gid() or "").startswith(
                "hero-marker:redox:Fe3+/Fe2+ ratio:internal-analytical:"
            )
            and (artist.get_gid() or "").endswith(":ordinary")
        )
        assert ordinary.get_facecolors()[0] == pytest.approx(to_rgba("#111111"))
        assert not axis.lines
    finally:
        plt.close(figure)


def test_history_appends_without_rewriting_existing_bytes(tmp_path: Path) -> None:
    history = tmp_path / "hero-history.jsonl"
    history.write_text('{"store_sha":"old"}\n', encoding="utf-8")
    previous = history.read_bytes()
    record = {
        "kind": "battery_hero_history",
        "store_sha": "new",
        "commit_date": "2026-10-18T10:27:53-04:00",
        "metrics": [],
        "benchmarks": [],
    }

    assert append_history(history, record)
    assert history.read_bytes().startswith(previous)
    assert not append_history(history, record)
    assert len(history.read_text(encoding="utf-8").splitlines()) == 2


def test_current_hero_summary_uses_input_metrics_when_history_is_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    residuals = tmp_path / "new-residuals.jsonl"
    row = _record(
        engine="openimcc",
        experiment="new-current-row",
        formula="SiO2",
        temperature_K=1200,
        residual=0.75,
    )
    residuals.write_text(
        "".join(
            json.dumps(item) + "\n"
            for item in (
                {"kind": "battery_store_stamp", "revision": "new-store"},
                row,
            )
        ),
        encoding="utf-8",
    )
    history = tmp_path / "old-history.jsonl"
    history.write_text(
        json.dumps(
            {
                "kind": "battery_hero_history",
                "store_revision": "old-store",
                "commit_date": "2026-10-18T10:27:53-04:00",
                "metrics": [
                    {
                        "rail": "vapour",
                        "engine": "openimcc",
                        "numeric_count": 1,
                        "n": 1,
                        "rms_dex": "0.01",
                        "median_abs_dex": "0.01",
                    }
                ],
                "benchmarks": [],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    figures: list[Figure] = []
    original_savefig = Figure.savefig

    def capture_figure(self: Figure, *args: object, **kwargs: object) -> object:
        result = original_savefig(self, *args, **kwargs)
        if kwargs.get("format") == "svg":
            figures.append(self)
        return result

    monkeypatch.setattr(Figure, "savefig", capture_figure)
    data = read_scores(residuals, rails=HERO_RAILS)
    render_hero_charts(data, tmp_path / "new-render", history_path=history)

    labels = [
        artist.get_text()
        for figure in figures
        for artist in figure.findobj(match=Text)
        if artist.get_text()
    ]
    assert any("all-numeric RMS 0.75 dex (n=1)" in label for label in labels)
    assert all("all-numeric RMS 0.01 dex" not in label for label in labels)


def test_history_schema_keeps_scorer_metrics_and_per_rail_counts() -> None:
    record = _history_record(
        {
            "store": {"revision": "redox-test"},
            "records": [
                {
                    "tier": "all_numeric",
                    "rail": "redox",
                    "engine": "internal-analytical",
                    "n": 7,
                    "rms_dex": "0.4",
                    "median_abs_dex": "0.3",
                }
            ],
        },
        landing_sha="green-test",
        commit_date="2026-10-18T10:27:53-04:00",
        benchmarks={
            ("redox", "internal-analytical"): {
                "rail": "redox",
                "primary_engine": "internal-analytical",
                "benchmark_engine": None,
                "n": 0,
                "primary_rms_dex": None,
                "benchmark_rms_dex": None,
            },
            ("redox", "openimcc"): {
                "rail": "redox",
                "primary_engine": "openimcc",
                "benchmark_engine": None,
                "n": 0,
                "primary_rms_dex": None,
                "benchmark_rms_dex": None,
            },
        },
        diagnostics={
            ("redox", "internal-analytical"): {
                "refused_count": 2,
                "not_observable_count": 4,
                "refused_by_reason": {"unsupported": 2},
                "open_predict_and_flag_notice_counts_by_kind": {
                    "out_of_certified_band": 1
                },
            }
        },
        refusal_coverage=[
            {
                "rail": "redox",
                "engine": "internal-analytical",
                "refused_count": 2,
                "not_observable_count": 4,
            }
        ],
    )

    assert record["kind"] == "battery_hero_history"
    assert record["schema_version"] == 1
    assert [entry["primary_engine"] for entry in record["benchmarks"]] == [
        "internal-analytical",
        "openimcc",
    ]
    assert all(entry["benchmark_engine"] is None for entry in record["benchmarks"])
    metric = record["metrics"][0]
    assert metric["numeric_count"] == 7
    assert metric["rms_dex"] == "0.4"
    assert metric["median_abs_dex"] == "0.3"
    assert metric["refused_count"] == 2
    assert metric["not_observable_count"] == 4
    assert metric["refused_by_reason"] == {"unsupported": 2}
    assert metric["open_predict_and_flag_notice_counts_by_kind"] == {
        "out_of_certified_band": 1
    }
    assert record["refusal_coverage"] == [
        {
            "rail": "redox",
            "engine": "internal-analytical",
            "refused_count": 2,
            "not_observable_count": 4,
        }
    ]


def test_cli_requires_explicit_history_path() -> None:
    with pytest.raises(SystemExit) as error:
        _parser().parse_args(
            [
                "--input",
                "residuals.jsonl",
                "--output-dir",
                "rendered",
                "--simulator-commit",
                "simulator-pin",
                "--openimcc-commit",
                "openimcc-pin",
            ]
        )
    assert error.value.code == 2


def test_missing_comparison_engine_is_reported_as_unavailable(tmp_path: Path) -> None:
    residuals = tmp_path / "residuals.jsonl"
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "unavailable-test"}
    ]
    for engine, value in (("openimcc", 0.2), ("internal-analytical", 0.3)):
        row = _record(
            engine=engine,
            experiment="shared-redox",
            formula="FeO",
            temperature_K=1400,
            residual=value,
            rail="redox",
        )
        row["reference"] = "shared-redox"
        row["candidate_observation"]["identity"]["quantity"] = {
            "tag": "value",
            "value": "fe3_fe2_ratio",
        }
        rows.append(row)
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    data = read_scores(residuals, rails=HERO_RAILS)
    for engine in ("openimcc", "internal-analytical"):
        assert data.hero_benchmarks[("redox", engine)]["benchmark_engine"] is None

    files = render_hero_charts(data, tmp_path / "rendered")
    svg = files["hero:redox:svg"].read_text(encoding="utf-8").casefold()
    assert "benchmark unavailable" in svg
    vapour_svg = files["hero:vapour:svg"].read_text(encoding="utf-8").casefold()
    assert "all-numeric rms unavailable (n=0)" in vapour_svg
    assert "all-numeric rms unavailable dex (n=0)" not in vapour_svg


def test_read_scores_streams_residuals_from_archive_member(tmp_path: Path) -> None:
    magemin_vapour = _record(
        engine="magemin",
        experiment="archive-magemin-vapour",
        formula="SiO",
        temperature_K=1200,
        residual=None,
        rail="vapour",
        status="refused",
    )
    magemin_vapour["refusal"] = {"reason": "unsupported"}
    attempted_vapour_refusal = _record(
        engine="vaporock",
        experiment="archive-vaporock-refusal",
        formula="SiO",
        temperature_K=1200,
        residual=None,
        rail="vapour",
        status="refused",
    )
    attempted_vapour_refusal["refusal"] = {
        "reason": "outside_supported_species"
    }
    compilation_refusal = _record(
        engine="alphamelts",
        experiment="archive-thermo-refusal",
        formula="SiO",
        temperature_K=1200,
        residual=None,
        rail="thermochemistry",
        status="refused",
    )
    compilation_refusal["candidate_request"]["quantity"] = "H_minus_H298"
    compilation_refusal["refusal"] = {
        "reason": "unsupported",
        "detail": {"reason": "quantity_not_predicted"},
    }
    rows = [
        {"kind": "battery_store_stamp", "revision": "archive-test"},
        _record(
            engine="internal-analytical",
            experiment="archive-row",
            formula="SiO",
            temperature_K=1200,
            residual=0.25,
            rail="vapour",
        ),
        magemin_vapour,
        attempted_vapour_refusal,
        compilation_refusal,
    ]
    payload = "".join(json.dumps(row) + "\n" for row in rows).encode("utf-8")
    summary_payload = json.dumps(
        {
            "store": {"revision": "archive-test"},
            "records": [
                {
                    "tier": "all_numeric",
                    "rail": "vapour",
                    "engine": "internal-analytical",
                    "n": 1,
                    "rms_dex": "0.25",
                    "median_abs_dex": "0.25",
                }
            ],
        }
    ).encode("utf-8")
    archive_path = tmp_path / "battery.tgz"
    with tarfile.open(archive_path, "w:gz") as archive:
        member = tarfile.TarInfo("data/battery/residuals.jsonl")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
        summary_member = tarfile.TarInfo("data/battery/score-summary.json")
        summary_member.size = len(summary_payload)
        archive.addfile(summary_member, io.BytesIO(summary_payload))

    data = read_scores(archive_path, rails=HERO_RAILS)

    assert data.hero_series[("vapour", "SiO", "internal-analytical", "unknown")][0].residual_dex == 0.25

    compact_path = tmp_path / "compact-residuals.jsonl.gz"
    with gzip.open(compact_path, "wb") as compact:
        compact.write(payload)
    compact_data = read_scores(compact_path, rails=HERO_RAILS)
    assert compact_data.hero_series == data.hero_series

    history = history_record_from_archive(archive_path, landing_sha="HEAD")
    coverage = {
        (item["rail"], item["engine"]): item
        for item in history["refusal_coverage"]
    }
    assert coverage[("vapour", "magemin")] == {
        "rail": "vapour",
        "engine": "magemin",
        "refused_count": 0,
        "not_observable_count": 1,
    }
    assert coverage[("vapour", "vaporock")] == {
        "rail": "vapour",
        "engine": "vaporock",
        "refused_count": 1,
        "not_observable_count": 0,
    }
    assert coverage[("thermochemistry", "alphamelts")] == {
        "rail": "thermochemistry",
        "engine": "alphamelts",
        "refused_count": 0,
        "not_observable_count": 1,
    }


def test_hero_svg_and_png_are_byte_identical_for_the_same_input(tmp_path: Path) -> None:
    residuals = tmp_path / "residuals.jsonl"
    row = _record(
        engine="internal-analytical",
        experiment="hero-1",
        formula="SiO",
        temperature_K=1200,
        residual=0.2,
        rail="vapour",
    )
    row["candidate_observation"]["identity"]["composition"] = {
        "tag": "value",
        "value": {
            "amount_basis": "mole_fraction",
            "components": [["SiO2", "0.25"], ["FeO", "0.75"]],
        },
    }
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "hero-test"},
        row,
    ]
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    history = tmp_path / "hero-history.jsonl"
    history.write_text(
        json.dumps(
            {
                "kind": "battery_hero_history",
                "store_sha": "hero-test",
                "store_revision": "hero-test",
                "commit_date": "2026-10-18T10:27:53-04:00",
                "metrics": [
                    {
                        "rail": "vapour",
                        "engine": "internal-analytical",
                        "rms_dex": "0.2",
                        "median_abs_dex": "0.2",
                    }
                ],
                "benchmarks": [],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    data = read_scores(residuals, rails=HERO_RAILS)

    first = render_hero_charts(data, tmp_path / "first", history_path=history)
    second = render_hero_charts(data, tmp_path / "second", history_path=history)

    for rail in HERO_RAILS:
        assert first[f"hero:{rail}:svg"].read_bytes() == second[f"hero:{rail}:svg"].read_bytes()
        assert first[f"hero:{rail}:png"].read_bytes() == second[f"hero:{rail}:png"].read_bytes()


def test_hero_legend_is_bounded_and_numeric_labels_use_three_significant_figures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    residuals = tmp_path / "formatted-residuals.jsonl"
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "formatted-store"}
    ]
    for engine, value in (
        ("internal-analytical", 0.3333333333333333),
        ("openimcc", 0.5555555555555555),
        ("thermoengine", 0.2222222222222222),
    ):
        row = _record(
            engine=engine,
            experiment="shared",
            formula="FeO",
            temperature_K=1300,
            residual=value,
            rail="redox",
        )
        row["reference"] = "shared"
        row["candidate_observation"]["identity"]["quantity"] = {
            "tag": "value",
            "value": "fe3_fe2_ratio",
        }
        row["candidate_observation"]["identity"]["composition"] = {
            "tag": "value",
            "value": {
                "amount_basis": "mole_fraction",
                "components": [["SiO2", "0.37"], ["FeO", "0.63"]],
            },
        }
        rows.append(row)
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    data = read_scores(residuals, rails=HERO_RAILS)
    history = tmp_path / "formatted-history.jsonl"
    history.write_text(
        json.dumps(
            {
                "kind": "battery_hero_history",
                "store_revision": "formatted-store",
                "commit_date": "2026-10-18T10:27:53-04:00",
                "metrics": [
                    {
                        "rail": "redox",
                        "engine": "internal-analytical",
                        "n": 147,
                        "numeric_count": 147,
                        "rms_dex": "0.30636297707166455257",
                        "median_abs_dex": "0.30636297707166455257",
                    }
                ],
                "benchmarks": [],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    legend_labels: list[list[str]] = []
    figures: list[Figure] = []
    original_legend = Figure.legend
    original_savefig = Figure.savefig

    def record_legend(self: Figure, *args: object, **kwargs: object) -> object:
        handles = kwargs.get("handles", args[0] if args else ())
        legend_labels.append([handle.get_label() for handle in handles])
        return original_legend(self, *args, **kwargs)

    def record_figure(self: Figure, *args: object, **kwargs: object) -> object:
        result = original_savefig(self, *args, **kwargs)
        if args and Path(str(args[0])).suffix == ".svg":
            figures.append(self)
        return result

    monkeypatch.setattr(Figure, "legend", record_legend)
    monkeypatch.setattr(Figure, "savefig", record_figure)
    render_hero_charts(data, tmp_path / "formatted-render", history_path=history)

    assert legend_labels
    assert all(len(labels) <= len(data.engines) + 2 for labels in legend_labels)
    labels = [
        artist.get_text()
        for figure in figures
        for artist in figure.findobj(match=Text)
        if artist.get_text()
    ]
    assert any("0.33 dex" in label and "n=1" in label for label in labels)
    assert all("0.30636297707166455257" not in label for label in labels)
    for label in labels:
        without_dates = re.sub(r"\b\d{4}-\d{2}(?:-\d{2})?\b", "", label)
        for number in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?(?:[kM])?", without_dates):
            value = number[:-1] if number.endswith(("k", "M")) else number
            digits = re.sub(r"\D", "", value)
            significant = len(digits.lstrip("0")) if "." in value else len(
                digits.lstrip("0").rstrip("0")
            )
            assert significant <= 3, f"too many significant figures in {label!r}"


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
    dirty_equal_commits = replace(
        patched,
        stamp={
            **patched.stamp,
            "simulator_tree_commit": "sim-test",
            "openimcc_installed_commit": "openimcc-test",
            "openimcc_reported_commit": "openimcc-test",
            "simulator_modified": True,
            "openimcc_modified": True,
        },
    )
    dirty_equal_stamp = stamp_text(
        dirty_equal_commits,
        simulator_commit="sim-test",
        openimcc_commit="openimcc-test",
        render_date="2026-10-15",
    )
    assert "patch 0123456789ab" in dirty_equal_stamp
    assert "openimcc patch abcdef012345" in dirty_equal_stamp
    clean = replace(
        data,
        stamp={
            **data.stamp,
            "simulator_tree_commit": "sim-test",
            "simulator_modified": False,
            "simulator_patch_sha256": "0123456789abcdef",
            "openimcc_installed_commit": "openimcc-test",
            "openimcc_modified": False,
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
    )
    assert files["species:tile_png"].is_file()
    assert files["species:tile_svg"].is_file()
    assert files["species:engine:openimcc"].is_file()
    assert files["species:engine:magemin"].is_file()
    assert files["element:tile_png"].is_file()
    assert files["element:tile_svg"].is_file()
    assert files["element:engine:openimcc"].is_file()
    assert files["species:tile_png"].read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert "test-store-2026-10-15-species" in files["species:tile_png"].name
    assert "test-store-2026-10-15-element" in files["element:tile_png"].name
    assert "no scored rows: magemin (not an observable of this engine ×1)" in files["species:tile_svg"].read_text(encoding="utf-8")
