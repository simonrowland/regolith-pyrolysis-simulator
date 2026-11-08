from __future__ import annotations

import csv
import io
import json
import math
import tarfile
import tracemalloc
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

import scripts.battery_hero_plot as hero_plot


ENGINE_ORDER = (
    "openimcc",
    "vaporock",
    "thermoengine",
    "internal-analytical",
    "magemin",
    "alphamelts",
)


def _residual(engine: str, rail: str, experiment: str, temperature: int, dex: float) -> dict[str, object]:
    return {
        "candidate_request": {"engine": engine, "experiment_id": experiment},
        "candidate_observation": {
            "engine": {"name": engine, "version": "1.2.3"},
            "experiment_id": experiment,
            "source_id": "fixture-source",
            "identity": {
                "species": {"formula": "SiO" if rail == "vapour" else "SiO2"},
                "temperature_K": {"tag": "value", "value": str(temperature)},
                "composition": {
                    "tag": "value",
                    "value": {
                        "amount_basis": "mole_fraction",
                        "components": [["SiO2", "0.4"], ["FeO", "0.6"]],
                    },
                },
            },
            "notices": [],
        },
        "numeric": {"operation": "dex", "unit": "dimensionless", "value": dex},
        "rail": rail,
        "reference": experiment,
        "source_relation": "independent",
        "status": "match",
    }


def _fixture_archive(path: Path) -> None:
    rows = [{"kind": "battery_store_stamp", "revision": "redesign-fixture"}]
    for engine, offset in (("openimcc", 0.0), ("vaporock", 0.2)):
        for rail in ("vapour", "melt_activity"):
            for index, temperature in enumerate((1600, 1800)):
                rows.append(
                    _residual(
                        engine,
                        rail,
                        f"fixture-{rail}-{index}",
                        temperature,
                        0.1 + offset + index * 0.1,
                    )
                )
    for index in range(300):
        residual = 4.2 if index == 0 else -4.4 if index == 1 else ((index % 9) - 4) / 10
        rows.append(
            _residual(
                "openimcc",
                "vapour",
                f"density-fixture-{index}",
                1500 + index * 2,
                residual,
            )
        )
    residuals = "".join(json.dumps(row) + "\n" for row in rows).encode()
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("data/battery/residuals.jsonl")
        member.size = len(residuals)
        archive.addfile(member, io.BytesIO(residuals))


def _many_row_archive(path: Path, count: int) -> None:
    rows = [{"kind": "battery_store_stamp", "revision": "memory-fixture"}]
    rows.extend(
        _residual(
            "openimcc",
            "vapour",
            f"memory-{index}",
            1500 + index % 1200,
            ((index % 5) - 2) / 10,
        )
        for index in range(count)
    )
    residuals = "".join(json.dumps(row) + "\n" for row in rows).encode()
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("data/battery/residuals.jsonl")
        member.size = len(residuals)
        archive.addfile(member, io.BytesIO(residuals))


def _capture_collection_figures(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    figures: list[object] = []
    monkeypatch.setattr(
        hero_plot,
        "_save_collection_png",
        lambda figure, _path: figures.append(figure),
    )
    return figures


def _collection_csv_round_trip(
    tmp_path: Path, tables: dict[str, list[dict[str, str]]]
) -> dict[str, list[dict[str, str]]]:
    files = hero_plot._write_collection_csvs(tmp_path, tables)
    result: dict[str, list[dict[str, str]]] = {}
    for name in tables:
        with files[f"table:{name}"].open(newline="") as source:
            result[name] = list(csv.DictReader(source))
    return result


def _artist_plot_csv_inputs() -> dict[str, list[dict[str, str]]]:
    return {
        "temperature_density": [
            {
                "rail": "vapour",
                "engine": "openimcc",
                "temperature_center_K": "1600",
                "residual_center_dex": "0.25",
                "hex_x_radius_K": "10",
                "hex_y_radius_dex": "0.5",
                "flagged": "0",
                "clipped": "0",
                "row_count": "5",
            },
            {
                "rail": "vapour",
                "engine": "openimcc",
                "temperature_center_K": "1700",
                "residual_center_dex": "-0.25",
                "hex_x_radius_K": "10",
                "hex_y_radius_dex": "0.5",
                "flagged": "0",
                "clipped": "0",
                "row_count": "7",
            },
        ],
        "coverage": [
            {
                "rail": "vapour",
                "engine": "openimcc",
                "scored_n": "7",
                "input_missing_n": "2",
                "outside_domain_n": "1",
                "not_output_n": "0",
                "other_refused_n": "3",
                "scored_percent": "54",
            }
        ],
        "progress": [
            {
                "rail": "vapour",
                "engine": "openimcc",
                "date": "2026-11-06",
                "rms_dex": "0.5",
                "n": "10",
                "held_out_n": "5",
                "held_out_rms_dex": "5.0",
            }
        ],
    }


def test_engine_palette_is_fixed_and_never_uses_goal_colour() -> None:
    colours = hero_plot._hero_engine_colors(tuple(reversed(ENGINE_ORDER)))

    assert tuple(colours) == ENGINE_ORDER
    assert colours == {
        "openimcc": "#2a78d6",
        "vaporock": "#eb6834",
        "thermoengine": "#1baf7a",
        "internal-analytical": "#eda100",
        "magemin": "#e87ba4",
        "alphamelts": "#6250d6",
    }
    assert "#0ca30c" not in colours.values()


def test_fingerprint_traffic_light_boundaries() -> None:
    assert hero_plot._fingerprint_band(Decimal("0.5")) == ("good", "#1a9641")
    assert hero_plot._fingerprint_band(Decimal("0.5001")) == ("okay", "#f6c90e")
    assert hero_plot._fingerprint_band(Decimal("1.0")) == ("okay", "#f6c90e")
    assert hero_plot._fingerprint_band(Decimal("1.0001")) == ("needs work", "#d7191c")


def test_collection_smoke_render_csv_values_html_and_legacy_history(tmp_path: Path) -> None:
    archive = tmp_path / "fixture-battery.tgz"
    _fixture_archive(archive)
    data = hero_plot.read_scores(archive)
    annotation_file = tmp_path / "caller-notes.json"
    annotation_file.write_text(
        json.dumps(
            {
                "annotations": {"legacy-landing": "baseline"},
                "fix_status": ["Knudsen oxygen fix: applied", "Total-pressure fix: applied"],
            }
        ),
        encoding="utf-8",
    )
    annotations, fix_status_lines = hero_plot._read_annotations_file(annotation_file)
    history = tmp_path / "legacy-history.jsonl"
    history.write_text(
        json.dumps(
            {
                "kind": "battery_hero_history",
                "schema_version": 1,
                "store_sha": "legacy-landing",
                "commit_date": "2026-11-06T12:00:00Z",
                "metrics": [
                    {
                        "rail": "vapour",
                        "engine": "openimcc",
                        "n": 5,
                        "rms_dex": "0.375",
                        "median_abs_dex": "0.3",
                    }
                ],
                "comparisons": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    files = hero_plot.render_hero_collection(
        data,
        archive,
        tmp_path / "collection",
        simulator_commit="simulator-pin",
        openimcc_commit="openimcc-pin",
        render_date="2026-11-07",
        history_path=history,
        annotations=annotations,
        fix_status_lines=fix_status_lines,
    )

    assert files["html"].is_file()
    assert files["chart:A"].is_file()
    assert files["chart:B:vapour"].is_file()
    assert files["table:all_rows"].is_file()
    page = files["html"].read_text(encoding="utf-8")
    assert "<details" in page and "<table" in page
    assert "temperature-min" in page and "sio2-min" in page
    assert "row source" in page.lower()
    assert "function showTip" in page and "row.source" in page
    assert page.index('id="chart-a"') < page.index('id="application-window"')
    assert 'id="chart-g"' in page and ".collection-grid" in page
    assert ":root[data-theme=dark]" in page
    assert "data:image/png;base64," in page
    assert "<script src=" not in page.lower() and "fetch(" not in page
    assert "legacy-landing" in page
    assert "0.375" in files["table:progress"].read_text(encoding="utf-8")
    assert "openimcc-pin" in files["methods"].read_text(encoding="utf-8")
    assert "Knudsen oxygen fix: applied" in files["methods"].read_text(encoding="utf-8")

    with files["table:temperature_density"].open(newline="") as density_file:
        density_rows = list(csv.DictReader(density_file))
    openimcc_vapour_density = [
        row for row in density_rows if row["rail"] == "vapour" and row["engine"] == "openimcc"
    ]
    assert sum(int(row["row_count"]) for row in openimcc_vapour_density) == 302
    assert any(row["clipped"] == "1" and row["y_bin"] == "-1" for row in openimcc_vapour_density)
    assert any(row["clipped"] == "1" and row["y_bin"] == "36" for row in openimcc_vapour_density)
    with files["table:temperature_points"].open(newline="") as point_file:
        point_rows = list(csv.DictReader(point_file))
    assert not any(row["rail"] == "vapour" and row["engine"] == "openimcc" for row in point_rows)

    with files["table:scorecard"].open(newline="") as scorecard_file:
        scorecard_rows = list(csv.DictReader(scorecard_file))
    expected_plot_values = {
        (row["rail"], row["primary_engine"], row["comparison_engine"]): tuple(
            Decimal(row[field]) if row[field] else None
            for field in (
                "high_primary_rms_dex",
                "high_comparison_rms_dex",
                "low_primary_rms_dex",
                "low_comparison_rms_dex",
            )
        )
        for row in scorecard_rows
    }
    assert hero_plot._scorecard_plot_values(scorecard_rows) == expected_plot_values
    assert [
        handle.get_label() for handle in hero_plot._fingerprint_legend_handles()
    ] == [
        "within 0.5 dex — good",
        "0.5–1 dex — okay",
        "over 1 dex — needs work",
    ]


def test_progress_uses_shared_row_metrics_when_history_has_comparisons(tmp_path: Path) -> None:
    archive = tmp_path / "fixture-battery.tgz"
    _fixture_archive(archive)
    data = hero_plot.read_scores(archive)
    history_row = {
        "kind": "battery_hero_history",
        "schema_version": 2,
        "store_sha": "shared-landing",
        "store_revision": "redesign-fixture",
        "commit_date": "2026-11-06T12:00:00Z",
        "metrics": [
            {"rail": "vapour", "engine": "openimcc", "n": 20, "rms_dex": "9.0"},
            {"rail": "vapour", "engine": "vaporock", "n": 20, "rms_dex": "8.0"},
        ],
        "comparisons": [
            {
                "rail": "vapour",
                "primary_engine": "openimcc",
                "comparisons": [
                    {
                        "engine": "vaporock",
                        "views": {
                            "all": {
                                "n_shared": 7,
                                "exposed_n": 2,
                                "primary_rms_dex_on_shared": "0.25",
                                "outside_rms_dex": "0.75",
                                "held_out": {
                                    "n": 5,
                                    "primary_rms_dex_on_shared": "0.3",
                                    "outside_rms_dex": "0.8",
                                },
                            }
                        },
                    }
                ],
            }
        ],
    }

    rows = hero_plot._history_collection_rows(
        data,
        [history_row],
        simulator_commit="shared-landing",
        render_date="2026-11-07",
        annotations={},
    )
    by_engine = {row["engine"]: row for row in rows}

    assert by_engine["openimcc"]["rms_dex"] == "0.25"
    assert by_engine["openimcc"]["n"] == 7
    assert by_engine["openimcc"]["held_out_rms_dex"] == "0.3"
    assert by_engine["vaporock"]["rms_dex"] == "0.75"
    assert by_engine["vaporock"]["n"] == 7
    assert by_engine["vaporock"]["held_out_rms_dex"] == "0.8"


def test_html_uses_a_stratified_sample_and_exact_window_aggregates(tmp_path: Path) -> None:
    archive = tmp_path / "many-rows.tgz"
    _many_row_archive(archive, 1200)
    data = hero_plot.read_scores(archive)
    files = hero_plot.render_hero_collection(
        data,
        archive,
        tmp_path / "collection",
        simulator_commit="simulator-pin",
        openimcc_commit="openimcc-pin",
        render_date="2026-11-07",
    )

    page = files["html"].read_text(encoding="utf-8")
    row_payload = page.split('id="row-data">', 1)[1].split("</script>", 1)[0]
    sampled_rows = json.loads(row_payload)
    counts: dict[tuple[str, str], int] = {}
    for row in sampled_rows:
        key = row["rail"], row["engine"]
        counts[key] = counts.get(key, 0) + 1
    assert counts and max(counts.values()) <= 100
    assert "sample" in page.lower()
    assert 'data-deferred-table="samples"' in page
    assert 'data-deferred-table="fingerprints"' in page
    assert "document.createElement('table')" in page
    assert len({row["temperature_bin"] for row in sampled_rows}) > 1

    with files["table:fingerprints"].open(newline="") as source:
        cells = list(csv.DictReader(source))
    cell = next(
        row
        for row in cells
        if row["rail"] == "vapour"
        and row["engine"] == "openimcc"
        and row["temperature_lower_K"] == "1500"
        and row["sio2_lower"] == "0.4"
    )
    assert int(cell["n"]) == 100
    assert math.isclose(float(cell["sum_residual_dex"]), 0.0)
    assert math.isclose(float(cell["sum_squared_residual_dex"]), 2.0)
    assert "snap" in files["methods"].read_text(encoding="utf-8").lower()
    assert "exact" in files["methods"].read_text(encoding="utf-8").lower()
    assert files["html"].stat().st_size <= 8 * 1024 * 1024


def test_read_scores_peak_memory_is_bounded_for_many_scored_rows(tmp_path: Path) -> None:
    archive = tmp_path / "memory.tgz"
    _many_row_archive(archive, 20_000)

    tracemalloc.start()
    hero_plot.read_scores(archive, _collection_mode=True)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert peak < 40 * 1024 * 1024


def test_component_rows_pair_openimcc_with_each_partners_shared_rows(tmp_path: Path) -> None:
    archive = tmp_path / "fixture.tgz"
    _fixture_archive(archive)
    data = hero_plot.read_scores(archive)
    comparisons = (
        {
            "rail": "vapour",
            "species": "SiO",
            "primary_engine": "openimcc",
            "comparisons": [
                {
                    "engine": "vaporock",
                    "views": {
                        "all": {
                            "n_shared": 10,
                            "primary_rms_dex_on_shared": "0.1",
                            "outside_rms_dex": "0.2",
                        }
                    },
                },
                {
                    "engine": "thermoengine",
                    "views": {
                        "all": {
                            "n_shared": 5,
                            "primary_rms_dex_on_shared": "2.0",
                            "outside_rms_dex": "1.0",
                        }
                    },
                },
            ],
        },
    )
    rows = hero_plot._component_table_rows(
        replace(data, hero_component_comparisons=comparisons)
    )
    pairs = {
        (row["engine"], row.get("shared_with")): (
            int(row["n_shared"]),
            str(row["rms_dex"]),
        )
        for row in rows
        if row["rail"] == "vapour" and row["component"] == "SiO"
    }

    assert pairs == {
        ("openimcc", "vaporock"): (10, "0.1"),
        ("vaporock", "vaporock"): (10, "0.2"),
        ("openimcc", "thermoengine"): (5, "2.0"),
        ("thermoengine", "thermoengine"): (5, "1.0"),
    }


def test_artists_match_csv_values_for_scorecard_and_component_pairs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from matplotlib.collections import PathCollection
    from matplotlib.colors import to_hex

    figures = _capture_collection_figures(monkeypatch)
    scorecard = [
        {
            "rail": "vapour",
            "primary_engine": "openimcc",
            "comparison_engine": "vaporock",
            "n_shared": "5",
            "high_n_shared": "5",
            "high_primary_rms_dex": "0.2",
            "high_comparison_rms_dex": "0.8",
            "low_n_shared": "",
            "low_primary_rms_dex": "",
            "low_comparison_rms_dex": "",
        }
    ]
    csv_tables = _collection_csv_round_trip(
        tmp_path,
        {
            "scorecard": scorecard,
            "components": [
                {
                    "rail": "vapour",
                    "component": "SiO",
                    "engine": "openimcc",
                    "shared_with": "vaporock",
                    "n_shared": "10",
                    "rms_dex": "0.1",
                },
                {
                    "rail": "vapour",
                    "component": "SiO",
                    "engine": "vaporock",
                    "shared_with": "vaporock",
                    "n_shared": "10",
                    "rms_dex": "0.2",
                },
                {
                    "rail": "vapour",
                    "component": "SiO",
                    "engine": "openimcc",
                    "shared_with": "thermoengine",
                    "n_shared": "5",
                    "rms_dex": "2.0",
                },
                {
                    "rail": "vapour",
                    "component": "SiO",
                    "engine": "thermoengine",
                    "shared_with": "thermoengine",
                    "n_shared": "5",
                    "rms_dex": "1.0",
                },
            ],
            **_artist_plot_csv_inputs(),
        },
    )
    scorecard_csv = csv_tables["scorecard"]
    hero_plot._draw_scorecard_png(tmp_path / "a.png", scorecard_csv)
    axis = figures[-1].axes[0]
    plotted_by_engine: dict[str, float] = {}
    for collection in axis.collections:
        if not isinstance(collection, PathCollection) or not len(collection.get_offsets()):
            continue
        colour = to_hex(collection.get_facecolors()[0])
        engine = next(
            name
            for name, value in hero_plot._hero_engine_colors(ENGINE_ORDER).items()
            if value == colour
        )
        plotted_by_engine[engine] = float(collection.get_offsets()[0][0])
    assert plotted_by_engine == {
        "openimcc": float(scorecard_csv[0]["high_primary_rms_dex"]),
        "vaporock": float(scorecard_csv[0]["high_comparison_rms_dex"]),
    }
    swapped = [
        dict(scorecard_csv[0])
    ]
    swapped[0]["high_primary_rms_dex"], swapped[0]["high_comparison_rms_dex"] = (
        swapped[0]["high_comparison_rms_dex"],
        swapped[0]["high_primary_rms_dex"],
    )
    swapped_expected = {
        "openimcc": float(swapped[0]["high_primary_rms_dex"]),
        "vaporock": float(swapped[0]["high_comparison_rms_dex"]),
    }
    assert plotted_by_engine != swapped_expected

    component_rows = csv_tables["components"]
    hero_plot._draw_component_png(tmp_path / "c.png", "vapour", component_rows)
    pair_line_x = {
        tuple(float(value) for value in line.get_xdata())
        for line in figures[-1].axes[0].lines
        if len(line.get_xdata()) == 2 and line.get_xdata()[0] != line.get_xdata()[1]
    }
    expected_pairs = {
        tuple(
            float(row["rms_dex"])
            for row in sorted(
                (item for item in component_rows if item["shared_with"] == partner),
                key=lambda item: item["engine"],
            )
        )
        for partner in {row["shared_with"] for row in component_rows}
    }
    assert pair_line_x == expected_pairs


def test_hexbin_artists_use_tessellated_cells_and_csv_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from matplotlib.collections import PolyCollection

    figures = _capture_collection_figures(monkeypatch)
    density = _collection_csv_round_trip(
        tmp_path,
        {"temperature_density": _artist_plot_csv_inputs()["temperature_density"]},
    )["temperature_density"]
    hero_plot._draw_temperature_png(
        tmp_path / "b.png",
        "vapour",
        [
            {
                "rail": "vapour",
                "engine": "openimcc",
                "n": "400",
                "plotted_n": "400",
                "rms_dex": "0.5",
                "temperature_min_K": "1500",
                "temperature_max_K": "1800",
                "flagged_n": "0",
                "clipped_low_n": "0",
                "clipped_high_n": "0",
            }
        ],
        [],
        density,
        [],
    )
    hex_cells = [
        collection
        for collection in figures[-1].axes[0].collections
        if isinstance(collection, PolyCollection)
        and collection.get_array() is not None
    ]
    assert hex_cells
    artist = hex_cells[0]
    assert sorted(int(value) for value in artist.get_array()) == sorted(
        int(row["row_count"]) for row in density
    )
    vertices = [
        path.vertices[:-1]
        if len(path.vertices) == 7
        and (path.vertices[0] == path.vertices[-1]).all()
        else path.vertices
        for path in artist.get_paths()
    ]
    assert all(len(points) == 6 for points in vertices)
    centers = {
        tuple(round(float(value), 6) for value in points.mean(axis=0))
        for points in vertices
    }
    assert centers == {
        (float(row["temperature_center_K"]), float(row["residual_center_dex"]))
        for row in density
    }


def test_coverage_and_progress_artists_match_csv_and_include_held_out_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from matplotlib.collections import PathCollection

    figures = _capture_collection_figures(monkeypatch)
    csv_tables = _collection_csv_round_trip(
        tmp_path,
        {
            "coverage": _artist_plot_csv_inputs()["coverage"],
            "progress": _artist_plot_csv_inputs()["progress"],
        },
    )
    coverage = csv_tables["coverage"]
    hero_plot._draw_coverage_png(tmp_path / "e.png", coverage)
    widths = [patch.get_width() for patch in figures[-1].axes[0].patches[:5]]
    assert widths == [
        int(coverage[0][field])
        for field in (
            "scored_n",
            "input_missing_n",
            "outside_domain_n",
            "not_output_n",
            "other_refused_n",
        )
    ]

    progress = csv_tables["progress"]
    hero_plot._draw_progress_png(tmp_path / "f.png", progress)
    axis = figures[-1].axes[0]
    assert list(axis.lines[-1].get_ydata()) == [float(progress[0]["rms_dex"])]
    held_out = [
        collection
        for collection in axis.collections
        if isinstance(collection, PathCollection)
        and len(collection.get_offsets())
    ]
    assert any(
        float(collection.get_offsets()[0][1]) == float(progress[0]["held_out_rms_dex"])
        for collection in held_out
    )
    assert axis.get_ylim()[1] > float(progress[0]["held_out_rms_dex"])


def test_fingerprint_cell_gap_is_two_export_pixels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    figures = _capture_collection_figures(monkeypatch)
    rows = [
        {
            "rail": "vapour",
            "engine": "openimcc",
            "temperature_lower_K": "1500",
            "temperature_upper_K": "1600",
            "sio2_lower": "0.0",
            "sio2_upper": "0.05",
            "n": "3",
            "median_abs_dex": "0.2",
            "colour": "#1a9641",
        },
        {
            "rail": "vapour",
            "engine": "openimcc",
            "temperature_lower_K": "1500",
            "temperature_upper_K": "1600",
            "sio2_lower": "0.05",
            "sio2_upper": "0.1",
            "n": "3",
            "median_abs_dex": "0.2",
            "colour": "#1a9641",
        },
    ]
    hero_plot._draw_fingerprint_png(tmp_path / "d.png", "vapour", rows, ["openimcc"])
    figure = figures[-1]
    axis = figure.axes[0]
    left, right = sorted(axis.patches, key=lambda patch: patch.get_x())
    y = 1550
    left_px = axis.transData.transform((left.get_x() + left.get_width(), y))[0]
    right_px = axis.transData.transform((right.get_x(), y))[0]
    gap_at_export = (right_px - left_px) * 150 / figure.dpi
    assert gap_at_export == pytest.approx(2.0, abs=0.05)


def test_collection_cli_flags_are_applied_or_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "fixture.tgz"
    _fixture_archive(archive)
    data = hero_plot.read_scores(archive, rails=("vapour",))

    files = hero_plot.render_hero_collection(
        data,
        archive,
        tmp_path / "selected",
        simulator_commit="simulator-pin",
        openimcc_commit="openimcc-pin",
        render_date="2026-11-07",
    )
    assert "chart:B:vapour" in files
    assert "chart:B:melt_activity" not in files

    with pytest.raises(ValueError, match="group_by"):
        hero_plot.render_plots(
            data,
            tmp_path / "grouped",
            simulator_commit="simulator-pin",
            openimcc_commit="openimcc-pin",
            source_path=archive,
            group_by=("species",),
        )
    with pytest.raises(ValueError, match="omit_empty_engines"):
        hero_plot.render_plots(
            data,
            tmp_path / "empty-engines",
            simulator_commit="simulator-pin",
            openimcc_commit="openimcc-pin",
            source_path=archive,
            omit_empty_engines=True,
        )


def test_full_silica_window_keeps_exact_totals_for_unknown_compositions(
    tmp_path: Path,
) -> None:
    rows = [{"kind": "battery_store_stamp", "revision": "unknown-sio2-fixture"}]
    for index, residual in enumerate((0.3, -0.4)):
        row = _residual("openimcc", "vapour", f"unknown-{index}", 1500, residual)
        row["candidate_observation"]["identity"]["composition"] = {"tag": "unknown"}
        rows.append(row)
    residuals = "".join(json.dumps(row) + "\n" for row in rows).encode()
    archive_path = tmp_path / "unknown-sio2.tgz"
    with tarfile.open(archive_path, "w:gz") as archive:
        member = tarfile.TarInfo("data/battery/residuals.jsonl")
        member.size = len(residuals)
        archive.addfile(member, io.BytesIO(residuals))

    data = hero_plot.read_scores(archive_path)
    files = hero_plot.render_hero_collection(
        data,
        archive_path,
        tmp_path / "unknown-collection",
        simulator_commit="simulator-pin",
        openimcc_commit="openimcc-pin",
        render_date="2026-11-07",
    )
    page = files["html"].read_text(encoding="utf-8")
    payload = json.loads(page.split('id="cell-data">', 1)[1].split("</script>", 1)[0])
    unknown = next(cell for cell in payload["cells"] if cell[9])
    assert payload["rails"][unknown[0]] == "vapour"
    assert payload["engines"][unknown[1]] == "openimcc"
    assert unknown[2] * 100 == 1500
    assert unknown[4] == 2
    assert math.isclose(float(unknown[5]), -0.1)
    assert math.isclose(float(unknown[6]), 0.25)

