from __future__ import annotations

import csv
import io
import json
import math
import sqlite3
import subprocess
import tarfile
import tracemalloc
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from simulator.battery import score as battery_score

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


def _many_row_archive(path: Path, count: int, *, refused_count: int = 0) -> None:
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
    for index in range(refused_count):
        sio2 = 0.3 + index / 10000
        row = _residual(
            "openimcc", "vapour", f"refused-memory-{index}", 1500, 0.1
        )
        row["status"] = "refused"
        row["numeric"] = None
        row["refusal"] = {"reason": "identity_incomplete"}
        row["execution"] = {"call_evidence": True}
        row["candidate_observation"]["identity"]["composition"]["value"][
            "components"
        ] = [["SiO2", f"{sio2:.4f}"], ["FeO", f"{1 - sio2:.4f}"]]
        rows.append(row)
    residuals = "".join(json.dumps(row) + "\n" for row in rows).encode()
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("data/battery/residuals.jsonl")
        member.size = len(residuals)
        archive.addfile(member, io.BytesIO(residuals))


def _write_archive(path: Path, rows: list[dict[str, object]]) -> None:
    residuals = "".join(json.dumps(row) + "\n" for row in rows).encode()
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("data/battery/residuals.jsonl")
        member.size = len(residuals)
        archive.addfile(member, io.BytesIO(residuals))


def _csv_rows(rows: list[dict[str, object]]) -> list[dict[str, str]]:
    return [{key: str(value) for key, value in row.items()} for row in rows]


def _assert_chart_header_text_does_not_overlap(figure: object) -> None:
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()
    labels = []
    if figure._suptitle is not None:
        labels.append(("suptitle", figure._suptitle.get_window_extent(renderer)))
    labels.extend(
        (f"subtitle-{index}", text.get_window_extent(renderer))
        for index, text in enumerate(figure.texts)
        if text is not figure._suptitle
    )
    labels.extend(
        (f"legend-{index}", legend.get_window_extent(renderer))
        for index, legend in enumerate(figure.legends)
    )
    labels.extend(
        (f"panel-title-{index}", axis._left_title.get_window_extent(renderer))
        for index, axis in enumerate(figure.axes)
        if axis.get_title(loc="left")
    )
    for index, (left_name, left_box) in enumerate(labels):
        for right_name, right_box in labels[index + 1 :]:
            assert not left_box.overlaps(right_box), f"{left_name} overlaps {right_name}"


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
    assert [
        battery_score._battery_hero_fingerprint_band(Decimal(value))
        for value in ("0.5", "0.5001", "1.0", "1.0001")
    ] == ["good", "okay", "okay", "needs work"]
    assert hero_plot.FINGERPRINT_COLOURS == {
        "good": "#1a9641",
        "okay": "#f6c90e",
        "needs work": "#d7191c",
    }


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
    assert f"TEMPERATURE_FLOOR_K={hero_plot.HERO_TEMPERATURE_FLOOR_K}" in page
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
    methods = files["methods"].read_text(encoding="utf-8")
    assert "snap" in methods.lower()
    assert "exact" in files["methods"].read_text(encoding="utf-8").lower()
    assert "Number.EPSILON" in methods
    assert "nearest integer index" in methods
    assert files["html"].stat().st_size <= 8 * 1024 * 1024


def test_read_scores_peak_memory_is_bounded_for_many_scored_rows(tmp_path: Path) -> None:
    archive = tmp_path / "memory.tgz"
    _many_row_archive(archive, 20_000, refused_count=1_000)

    tracemalloc.start()
    data = hero_plot.read_scores(archive, _collection_mode=True)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert peak < 40 * 1024 * 1024
    assert data.hero_refusal_totals[("vapour", "openimcc", "identity_incomplete")] == 1000
    assert not data.hero_refusals


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


def test_collection_mode_does_not_retain_composition_keyed_series(
    tmp_path: Path,
) -> None:
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "many-compositions"}
    ]
    for index in range(1000):
        sio2 = 0.3 + index / 10000
        row = _residual("openimcc", "vapour", f"composition-{index}", 1500, 0.1)
        row["candidate_observation"]["identity"]["composition"]["value"][
            "components"
        ] = [["SiO2", f"{sio2:.4f}"], ["FeO", f"{1 - sio2:.4f}"]]
        rows.append(row)
    archive = tmp_path / "many-compositions.tgz"
    _write_archive(archive, rows)

    data = hero_plot.read_scores(archive, _collection_mode=True)

    assert data.summaries["openimcc"].unique_points == 1000
    assert not data.series
    assert not data.hero_series
    assert not data.element_series


def test_r2_metric_and_temperature_quantile_outputs_are_bit_identical(
    tmp_path: Path,
) -> None:
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "numeric-pin"}
    ]
    for index, residual in enumerate((0.1, 0.2, 0.5, 1.1)):
        rows.append(_residual("openimcc", "vapour", f"pin-{index}", 1500, residual))
    archive = tmp_path / "numeric-pin.tgz"
    _write_archive(archive, rows)
    data = hero_plot.read_scores(archive, _collection_mode=True)

    metric = data.hero_all_numeric_metrics[("vapour", "openimcc")]["views"]["all"]
    assert metric["rms_dex"] == "0.6144102863722253795906081777"
    assert metric["sd_dex"] == "0.3897114317029973910436754268"
    assert metric["median_abs_dex"] == "0.35"

    database = sqlite3.connect(":memory:")
    hero_plot._collection_row_database(archive, database, bin_width_K=data.bin_width_K)
    tables = hero_plot._collection_raw_tables(
        database, data, bin_width_K=data.bin_width_K
    )
    database.close()
    temperature_bin = next(
        row for row in tables["temperature_bins"] if row["engine"] == "openimcc"
    )
    assert tuple(
        float(temperature_bin[field]).hex()
        for field in ("q1_dex", "median_dex", "q3_dex")
    ) == (
        "0x1.6666666666667p-3",
        "0x1.6666666666666p-2",
        "0x1.4cccccccccccdp-1",
    )


def test_scorer_long_decimal_median_and_iqr_match_pre_r3_outputs() -> None:
    # Expected strings were emitted by the production _median/_iqr functions
    # from tree 37ce21876, before the r3 percentile refactor.
    cases = (
        (
            (
                "0.123456789012345678901234567890",
                "0.223456789012345678901234567890",
                "0.823456789012345678901234567890",
                "0.923456789012345678901234567890",
            ),
            "0.523456789012345678901234568",
            "0.6500000000000000000000000000",
        ),
        (
            (
                "0.1",
                "0.1234567890123456789012345678",
                "0.9234567890123456789012345678",
            ),
            "0.1234567890123456789012345678",
            "0.4117283945061728394506172839",
        ),
        (
            ("0.12345678901234567890123456789",),
            "0.12345678901234567890123456789",
            None,
        ),
        (
            (
                "1.000000000000000000000000000001",
                "1.000000000000000000000000000003",
                "1.000000000000000000000000000009",
                "1.000000000000000000000000000021",
                "1.000000000000000000000000000055",
            ),
            "1.000000000000000000000000000009",
            "0E-27",
        ),
    )
    for inputs, expected_median, expected_iqr in cases:
        values = [Decimal(value) for value in inputs]
        assert str(battery_score._median(values)) == expected_median
        iqr = battery_score._iqr(values)
        assert (None if iqr is None else str(iqr)) == expected_iqr


def test_renderer_quartiles_use_scorer_decimal_percentile_helper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = tmp_path / "quartile-renderer.tgz"
    _fixture_archive(archive)
    data = hero_plot.read_scores(archive)
    database = sqlite3.connect(":memory:")
    database.create_collation(
        "hero_decimal",
        lambda left, right: (Decimal(left) > Decimal(right))
        - (Decimal(left) < Decimal(right)),
    )
    database.execute(
        """CREATE TABLE collection_unique_rows (
               rail TEXT NOT NULL,
               engine TEXT NOT NULL,
               temperature_bin INTEGER NOT NULL,
               residual_dex TEXT COLLATE hero_decimal NOT NULL,
               temperature_K REAL NOT NULL,
               flagged INTEGER NOT NULL,
               source TEXT NOT NULL,
               reference TEXT NOT NULL,
               sio2 REAL NOT NULL,
               composition TEXT NOT NULL,
               species TEXT NOT NULL,
               sio2_bin INTEGER NOT NULL,
               temperature_key TEXT NOT NULL
           )"""
    )
    values = tuple(
        Decimal(value)
        for value in (
            "0.100000000000000000000000000001",
            "0.200000000000000000000000000003",
            "0.300000000000000000000000000007",
            "0.900000000000000000000000000009",
        )
    )
    database.executemany(
        """INSERT INTO collection_unique_rows VALUES (
               'vapour', 'openimcc', 15, ?, ?, 0, ?, ?, 0.4, '{}', 'SiO', 8, ?
           )""",
        [
            (str(value), 1500 + index, f"source-{index}", f"reference-{index}", str(1500 + index))
            for index, value in enumerate(values)
        ],
    )
    scorer_percentiles = battery_score._battery_hero_ordered_percentiles_from_samples
    helper_calls: list[tuple[int, dict[int, Decimal], tuple[float, ...]]] = []

    def record_percentiles(
        count: int,
        samples: dict[int, Decimal],
        percentiles: tuple[float, ...],
    ) -> tuple[Decimal, ...]:
        helper_calls.append((count, dict(samples), tuple(percentiles)))
        return scorer_percentiles(count, samples, percentiles)

    monkeypatch.setattr(
        hero_plot, "_battery_hero_ordered_percentiles_from_samples", record_percentiles
    )
    tables = hero_plot._collection_raw_tables(
        database, data, bin_width_K=data.bin_width_K
    )
    database.close()

    expected = scorer_percentiles(4, dict(enumerate(values)), (0.25, 0.5, 0.75))
    assert helper_calls == [(4, dict(enumerate(values)), (0.25, 0.5, 0.75))]
    row = next(
        row for row in tables["temperature_bins"] if row["engine"] == "openimcc"
    )
    assert tuple(row[field] for field in ("q1_dex", "median_dex", "q3_dex")) == tuple(
        float(value) for value in expected
    )


def test_clipped_hexbin_rows_keep_temperature_and_count_all_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "clipped-temperature"}
    ]
    rows.extend(
        _residual("openimcc", "vapour", f"clipped-{index}", 1500 + index % 100, 4.0)
        for index in range(301)
    )
    archive = tmp_path / "clipped-temperature.tgz"
    _write_archive(archive, rows)
    data = hero_plot.read_scores(archive, _collection_mode=True)
    database = sqlite3.connect(":memory:")
    hero_plot._collection_row_database(archive, database, bin_width_K=data.bin_width_K)
    tables = hero_plot._collection_raw_tables(
        database, data, bin_width_K=data.bin_width_K
    )
    database.close()
    panel = next(
        row
        for row in tables["panels"]
        if row["rail"] == "vapour" and row["engine"] == "openimcc"
    )
    clipped = [
        row
        for row in tables["density"]
        if row["rail"] == "vapour"
        and row["engine"] == "openimcc"
        and row["clipped"] == 1
        and row["y_bin"] == 36
    ]

    assert sum(int(row["row_count"]) for row in clipped) == 301
    assert all(
        float(panel["temperature_min_K"])
        <= float(row["temperature_center_K"])
        <= float(panel["temperature_max_K"])
        for row in clipped
    )

    figures = _capture_collection_figures(monkeypatch)
    hero_plot._draw_temperature_png(
        tmp_path / "clipped.png",
        "vapour",
        _csv_rows([row for row in tables["panels"] if row["rail"] == "vapour"]),
        _csv_rows([row for row in tables["temperature_bins"] if row["rail"] == "vapour"]),
        _csv_rows([row for row in tables["density"] if row["rail"] == "vapour"]),
        _csv_rows([row for row in tables["points"] if row["rail"] == "vapour"]),
    )
    axis = figures[-1].axes[0]
    axis.figure.canvas.draw()
    high_triangles = [
        collection
        for collection in axis.collections
        if len(collection.get_offsets())
        and all(float(point[1]) == 3.0 for point in collection.get_offsets())
    ]
    assert high_triangles
    x_low, x_high = axis.get_xlim()
    assert all(
        x_low <= float(point[0]) <= x_high
        for collection in high_triangles
        for point in collection.get_offsets()
    )


def test_window_filters_use_snapped_half_open_bin_indices() -> None:
    script = hero_plot._COLLECTION_INTERACTIVE_SCRIPT
    assert "function resolveBinIndex" in script
    assert "function windowIndices" in script
    assert "row.fingerprint_temperature_bin<tlo||row.fingerprint_temperature_bin>=thi" in script
    assert "cell[2]<tlo||cell[2]>=thi" in script
    assert "cell[3]<slo||cell[3]>=shi" in script

    start = script.index("function resolveBinIndex")
    end = script.index("\nfunction updateWindowRms()", start)
    browser_functions = script[start:end]
    node_script = "\n".join(
        (
            "const TEMPERATURE_BIN_K=100,SIO2_BIN_WIDTH=0.05;",
            "const tMin={value:'1500'},tMax={value:'1600'},sMin={value:'0.15'},sMax={value:'0.30'};",
            browser_functions,
            "console.log(JSON.stringify({edges:[resolveBinIndex(0.15,0.05,'lower'),resolveBinIndex(0.30,0.05,'lower'),resolveBinIndex(0.60,0.05,'upper')],window:windowIndices(),inside:inWindow({temperature_bin:31,fingerprint_temperature_bin:15,sio2_bin:3}),upperSiO2:inWindow({temperature_bin:31,fingerprint_temperature_bin:15,sio2_bin:6}),upperTemperature:inWindow({temperature_bin:32,fingerprint_temperature_bin:16,sio2_bin:3})}));",
        )
    )
    result = subprocess.run(
        ["node", "-e", node_script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == {
        "edges": [3, 6, 12],
        "window": [15, 16, 3, 6],
        "inside": True,
        "upperSiO2": False,
        "upperTemperature": False,
    }


def test_collection_sample_uses_scorer_temperature_bin_at_custom_chart_width(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "custom-bin-width.tgz"
    _write_archive(
        archive,
        [
            {"kind": "battery_store_stamp", "revision": "custom-bin-width"},
            _residual("openimcc", "vapour", "1550-K", 1550, 0.1),
        ],
    )
    data = hero_plot.read_scores(
        archive, bin_width_K=50, _collection_mode=True
    )
    database = sqlite3.connect(":memory:")
    hero_plot._collection_row_database(archive, database, bin_width_K=50)
    tables = hero_plot._collection_raw_tables(
        database, data, bin_width_K=50
    )
    database.close()

    sample = next(row for row in tables["sample"] if row["engine"] == "openimcc")
    assert sample["temperature_bin"] == 31
    assert sample["fingerprint_temperature_bin"] == 15


def test_html_temperature_window_filters_sample_after_csv_round_trip(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "html-window-sample.tgz"
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
    sample_rows = json.loads(
        page.split('id="row-data">', 1)[1].split("</script>", 1)[0]
    )
    outside_row = next(
        row for row in sample_rows if float(row["temperature_K"]) >= 1600
    )
    assert "fingerprint_temperature_bin" in outside_row

    start = page.index("function resolveBinIndex")
    end = page.index("\nfunction updateWindowRms()", start)
    node_script = "\n".join(
        (
            "const TEMPERATURE_BIN_K=100,SIO2_BIN_WIDTH=0.05;",
            "const tMin={value:'1500'},tMax={value:'1600'},sMin={value:'0'},sMax={value:'1'};",
            page[start:end],
            f"const row=JSON.parse({json.dumps(json.dumps(outside_row, separators=(',', ':')))});",
            "console.log(JSON.stringify(inWindow(row)));",
        )
    )
    result = subprocess.run(
        ["node", "-e", node_script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) is False


def test_interactive_fingerprint_palette_uses_one_python_definition(
    tmp_path: Path,
) -> None:
    chart = tmp_path / "chart.png"
    chart.write_bytes(b"png")
    charts = {
        "chart:A": chart,
        **{f"chart:B:{rail}": chart for rail, _label in hero_plot.COLLECTION_OBSERVABLES},
        "chart:C:vapour": chart,
        "chart:C:melt_activity": chart,
        **{f"chart:D:{rail}": chart for rail, _label in hero_plot.COLLECTION_OBSERVABLES},
        "chart:E": chart,
        "chart:F": chart,
        "chart:G": chart,
    }
    tables = {name: [] for name in hero_plot._COLLECTION_TABLE_FIELDS}
    page = tmp_path / "collection.html"

    hero_plot._write_collection_html(
        page,
        chart_files=charts,
        table_rows=tables,
        engine_names=ENGINE_ORDER,
    )

    html = page.read_text(encoding="utf-8")
    palette = json.dumps(dict(hero_plot.FINGERPRINT_BANDS), separators=(",", ":"))
    assert f"const BANDS={palette};" in html
    assert "const BANDS={good:'#1a9641',okay:'#f6c90e','needs work':'#d7191c'}" not in html


def test_html_tables_format_decimals_but_csv_keeps_full_precision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    precise = "1.214777037924557481161297120"
    csv_files = hero_plot._write_collection_csvs(
        tmp_path,
        {
            "all_rows": [
                {
                    "rail": "vapour",
                    "engine": "openimcc",
                    "view": "all",
                    "n": 1,
                    "rms_dex": precise,
                    "sd_dex": "0",
                    "median_abs_dex": precise,
                }
            ]
        },
    )
    rows = hero_plot._read_collection_csv(csv_files["table:all_rows"])
    html = hero_plot._html_table(rows)

    assert rows[0]["rms_dex"] == precise
    assert "1.215" in html
    assert precise not in html
    assert "function displayCell" in hero_plot._COLLECTION_INTERACTIVE_SCRIPT
    assert "cell.textContent=displayCell(column,record[column])" in hero_plot._COLLECTION_INTERACTIVE_SCRIPT

    figures = _capture_collection_figures(monkeypatch)
    hero_plot._draw_temperature_png(
        tmp_path / "temperature.png",
        "vapour",
        [
            {
                "engine": "openimcc",
                "n": "1",
                "plotted_n": "1",
                "rms_dex": precise,
                "flagged_n": "0",
                "clipped_low_n": "0",
                "clipped_high_n": "0",
                "temperature_min_K": "1500",
                "temperature_max_K": "1500",
            }
        ],
        [],
        [],
        [],
    )
    title = figures[-1].axes[0].get_title(loc="left")
    assert "1.21 dex" in title
    assert precise not in title


def test_hero_chart_header_legends_and_panel_titles_do_not_overlap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    hero_plot._draw_scorecard_png(tmp_path / "a.png", scorecard)
    _assert_chart_header_text_does_not_overlap(figures[-1])

    fingerprint = [
        {
            "rail": "vapour",
            "engine": engine,
            "temperature_lower_K": "1500",
            "temperature_upper_K": "1600",
            "sio2_lower": "0.0",
            "sio2_upper": "0.05",
            "n": "3",
            "median_abs_dex": "0.2",
            "colour": "#1a9641",
        }
        for engine in ENGINE_ORDER
    ]
    hero_plot._draw_fingerprint_png(
        tmp_path / "d.png", "vapour", fingerprint, ENGINE_ORDER
    )
    _assert_chart_header_text_does_not_overlap(figures[-1])

    hero_plot._draw_progress_png(tmp_path / "f.png", _artist_plot_csv_inputs()["progress"])
    _assert_chart_header_text_does_not_overlap(figures[-1])


def test_scorecard_row_band_groups_temperature_pairs_and_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from matplotlib.collections import PathCollection
    from matplotlib.transforms import Affine2D, Bbox

    figures = _capture_collection_figures(monkeypatch)
    rows = []
    partners = (
        "vaporock",
        "thermoengine",
        "alphamelts",
        "magemin",
        "internal-analytical",
    )
    for rail, _label in hero_plot.COLLECTION_OBSERVABLES:
        for index, partner in enumerate(partners):
            rows.append(
                {
                    "rail": rail,
                    "primary_engine": "openimcc",
                    "comparison_engine": partner,
                    "n_shared": "10",
                    "high_n_shared": "6",
                    "high_primary_rms_dex": str(0.2 + index / 100),
                    "high_comparison_rms_dex": str(0.3 + index / 100),
                    "low_n_shared": "4",
                    "low_primary_rms_dex": str(0.4 + index / 100),
                    "low_comparison_rms_dex": str(0.5 + index / 100),
                }
            )

    hero_plot._draw_scorecard_png(tmp_path / "a.png", rows)

    figure = figures[-1]
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()
    axis = figure.axes[0]
    labels = [text for text in axis.texts if " vs " in text.get_text()]
    counts = [text for text in axis.texts if text.get_text().startswith("n=10")]
    pair_lines = [
        line
        for line in axis.lines
        if len(line.get_ydata()) == 2
        and line.get_ydata()[0] == line.get_ydata()[1]
        and line.get_xdata()[0] != line.get_xdata()[1]
        and line.get_ydata()[0] != 0
    ]
    assert len(labels) == 5
    assert len(counts) == 5
    assert len(pair_lines) == 10
    assert any("internal-analytical" in text.get_text() for text in labels)
    for row_index in range(5):
        pair_positions = sorted(
            float(line.get_ydata()[0])
            for line in pair_lines
            if round(float(line.get_ydata()[0])) == row_index
        )
        label = next(
            text for text in labels
            if text.get_text().endswith(partners[row_index])
        )
        count = counts[row_index]
        assert len(pair_positions) == 2
        assert label.get_position()[1] < pair_positions[0]
        assert count.get_position()[1] > pair_positions[1]

    row_texts_by_axis = []
    count_texts_by_axis = []
    for panel in figure.axes:
        row_texts = [text for text in panel.texts if " vs " in text.get_text()]
        count_texts = [
            text for text in panel.texts if text.get_text().startswith("n=")
        ]
        marker_boxes = []
        for collection in panel.collections:
            if (
                not isinstance(collection, PathCollection)
                or not len(collection.get_offsets())
            ):
                continue
            marker_box = collection.get_window_extent(renderer)
            if all(math.isfinite(value) for value in marker_box.extents):
                marker_boxes.append(marker_box)
                continue
            # Matplotlib returns an invalid extent for PathCollection. Use its
            # renderer-resolved size transform and data-to-display offsets.
            path_box = collection.get_paths()[0].get_extents(
                Affine2D(collection.get_transforms()[0])
            )
            stroke = renderer.points_to_pixels(collection.get_linewidths()[0]) / 2
            for center_x, center_y in collection.get_offset_transform().transform(
                collection.get_offsets()
            ):
                marker_boxes.append(
                    Bbox.from_extents(
                        center_x + path_box.x0 - stroke,
                        center_y + path_box.y0 - stroke,
                        center_x + path_box.x1 + stroke,
                        center_y + path_box.y1 + stroke,
                    )
                )
        for text in row_texts + count_texts:
            text_box = text.get_window_extent(renderer)
            assert all(
                not text_box.overlaps(marker_box)
                for marker_box in marker_boxes
            ), f"{text.get_text()} overlaps a comparison marker"
        row_texts_by_axis.append(row_texts)
        count_texts_by_axis.append(count_texts)
    for index in range(len(figure.axes) - 1):
        left_panel_counts = count_texts_by_axis[index]
        right_panel_labels = row_texts_by_axis[index + 1]
        for count in left_panel_counts:
            for label in right_panel_labels:
                assert not count.get_window_extent(renderer).overlaps(
                    label.get_window_extent(renderer)
                ), "neighboring panel count overlaps a long comparison label"


def test_empty_temperature_panel_does_not_draw_zero_line_through_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    figures = _capture_collection_figures(monkeypatch)
    hero_plot._draw_temperature_png(
        tmp_path / "empty-b.png",
        "vapour",
        [
            {
                "rail": "vapour",
                "engine": "magemin",
                "n": "0",
                "plotted_n": "0",
                "rms_dex": "",
                "flagged_n": "0",
                "clipped_low_n": "0",
                "clipped_high_n": "0",
                "temperature_min_K": "",
                "temperature_max_K": "",
            }
        ],
        [],
        [],
        [],
    )
    axis = figures[-1].axes[0]
    assert any(text.get_text() == "no scored rows" for text in axis.texts)
    assert not any(
        len(line.get_ydata())
        and all(float(value) == 0 for value in line.get_ydata())
        for line in axis.lines
    )


def test_empty_collection_panels_explain_missing_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    figures = _capture_collection_figures(monkeypatch)
    hero_plot._draw_scorecard_png(tmp_path / "a.png", [])
    scorecard_text = [text.get_text() for axis in figures[-1].axes for text in axis.texts]
    assert scorecard_text.count("no shared rows") >= 2

    hero_plot._draw_temperature_png(
        tmp_path / "b.png",
        "vapour",
        [
            {
                "engine": "magemin",
                "n": "0",
                "plotted_n": "0",
                "rms_dex": "",
                "flagged_n": "0",
                "clipped_low_n": "0",
                "clipped_high_n": "0",
                "temperature_min_K": "",
                "temperature_max_K": "",
            }
        ],
        [],
        [],
        [],
    )
    temperature_text = [text.get_text() for text in figures[-1].axes[0].texts]
    assert "no scored rows" in temperature_text
    assert "unavailable dex" not in figures[-1].axes[0].get_title()

    hero_plot._draw_fingerprint_png(tmp_path / "d.png", "vapour", [], ["magemin"])
    fingerprint_text = [text.get_text() for text in figures[-1].axes[0].texts]
    assert "no scored rows" in fingerprint_text


def test_temperature_hexagons_are_regular_in_display_space(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from matplotlib.collections import PolyCollection

    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "hex-aspect"}
    ]
    rows.extend(
        _residual(
            "vaporock",
            "vapour",
            f"hex-{index}",
            1100 + round(index * 1900 / 300),
            -2.9 + (index % 30) * 0.2,
        )
        for index in range(301)
    )
    archive = tmp_path / "hex-aspect.tgz"
    _write_archive(archive, rows)
    data = hero_plot.read_scores(archive, _collection_mode=True)
    database = sqlite3.connect(":memory:")
    hero_plot._collection_row_database(archive, database, bin_width_K=data.bin_width_K)
    tables = hero_plot._collection_raw_tables(
        database, data, bin_width_K=data.bin_width_K
    )
    database.close()
    figures = _capture_collection_figures(monkeypatch)
    hero_plot._draw_temperature_png(
        tmp_path / "hex-aspect.png",
        "vapour",
        _csv_rows([row for row in tables["panels"] if row["rail"] == "vapour"]),
        _csv_rows([row for row in tables["temperature_bins"] if row["rail"] == "vapour"]),
        _csv_rows([row for row in tables["density"] if row["rail"] == "vapour"]),
        _csv_rows([row for row in tables["points"] if row["rail"] == "vapour"]),
    )
    figure = figures[-1]
    figure.canvas.draw()
    axis = next(
        axis
        for axis in figure.axes
        if axis.get_title(loc="left").startswith("vaporock")
    )
    collections = [
        collection
        for collection in axis.collections
        if isinstance(collection, PolyCollection) and collection.get_array() is not None
    ]
    assert collections
    lengths = []
    for polygon in collections[0].get_paths():
        screen = axis.transData.transform(polygon.vertices)
        lengths.extend(
            math.dist(screen[index], screen[index + 1])
            for index in range(6)
        )
    assert lengths
    assert max(lengths) / min(lengths) < 1.02


def test_fingerprint_and_html_engine_order_use_one_definition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    figures = _capture_collection_figures(monkeypatch)
    hero_plot._draw_fingerprint_png(tmp_path / "d.png", "vapour", [], tuple(reversed(ENGINE_ORDER)))
    assert [axis.get_title(loc="left") for axis in figures[-1].axes] == list(ENGINE_ORDER)

    archive = tmp_path / "engine-order.tgz"
    _fixture_archive(archive)
    data = hero_plot.read_scores(archive, _collection_mode=True)
    comparison_records = [
        {
            "engine": engine,
            "n_shared": count,
            "views": {"all": {"n_shared": count}},
        }
        for engine, count in (
            ("alphamelts", 50),
            ("magemin", 40),
            ("internal-analytical", 30),
            ("thermoengine", 20),
            ("vaporock", 10),
        )
    ]
    data = replace(
        data,
        hero_comparisons={
            ("vapour", "openimcc"): {"comparisons": comparison_records}
        },
    )
    scorecard_rows = hero_plot._scorecard_table_rows(data)
    assert [
        row["comparison_engine"]
        for row in scorecard_rows
        if row["rail"] == "vapour"
    ] == [
        engine for engine in ENGINE_ORDER if engine != "openimcc"
    ]

    chart = tmp_path / "chart.png"
    chart.write_bytes(b"png")
    charts = {
        "chart:A": chart,
        **{f"chart:B:{rail}": chart for rail, _label in hero_plot.COLLECTION_OBSERVABLES},
        "chart:C:vapour": chart,
        "chart:C:melt_activity": chart,
        **{f"chart:D:{rail}": chart for rail, _label in hero_plot.COLLECTION_OBSERVABLES},
        "chart:E": chart,
        "chart:F": chart,
        "chart:G": chart,
    }
    tables = {name: [] for name in hero_plot._COLLECTION_TABLE_FIELDS}
    page = tmp_path / "ordered.html"
    hero_plot._write_collection_html(
        page,
        chart_files=charts,
        table_rows=tables,
        engine_names=tuple(reversed(ENGINE_ORDER)),
    )
    html = page.read_text(encoding="utf-8")
    assert json.dumps(hero_plot._hero_engine_colors(ENGINE_ORDER)) in html
    payload = json.loads(html.split('id="cell-data">', 1)[1].split("</script>", 1)[0])
    assert payload["engines"] == list(ENGINE_ORDER)


def test_progress_uses_shared_dates_and_marks_current_candidate_not_green(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from matplotlib.colors import to_hex

    archive = tmp_path / "candidate.tgz"
    _fixture_archive(archive)
    data = hero_plot.read_scores(archive, _collection_mode=True)
    rows = hero_plot._history_collection_rows(
        data,
        [],
        simulator_commit="candidate-sha",
        render_date="2026-11-08",
        annotations={},
    )
    assert rows
    assert all(str(row["basis"]).startswith("candidate (not green)") for row in rows)

    figures = _capture_collection_figures(monkeypatch)
    hero_plot._draw_progress_png(tmp_path / "f.png", rows)
    figure = figures[-1]
    axes = figure.axes
    assert len(axes) > 1
    assert all(axes[0].get_shared_x_axes().joined(axes[0], axis) for axis in axes[1:])
    assert "candidate (not green)" in [
        text.get_text() for text in figure.legends[0].get_texts()
    ]
    candidate_artists = [
        collection
        for axis in axes
        for collection in axis.collections
        if collection.get_label().startswith("candidate:")
    ]
    assert {artist.get_label().split(":", 1)[1] for artist in candidate_artists} == {
        "openimcc",
        "vaporock",
    }
    for artist in candidate_artists:
        engine = artist.get_label().split(":", 1)[1]
        assert to_hex(artist.get_facecolors()[0]) == hero_plot._HERO_ENGINE_COLORS[engine]
        assert to_hex(artist.get_edgecolors()[0]) == "#263238"

    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()
    legend_bottom = figure.legends[0].get_window_extent(renderer).y0
    first_panel_top = axes[0].get_window_extent(renderer).y1
    assert legend_bottom - first_panel_top < figure.dpi * 0.35


def test_temperature_chart_omits_unexplained_association_floor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    figures = _capture_collection_figures(monkeypatch)
    hero_plot._draw_temperature_png(
        tmp_path / "b.png",
        "vapour",
        [
            {
                "engine": "openimcc",
                "n": "1",
                "plotted_n": "1",
                "rms_dex": "0.25",
                "flagged_n": "0",
                "clipped_low_n": "0",
                "clipped_high_n": "0",
                "temperature_min_K": "1500",
                "temperature_max_K": "1500",
            }
        ],
        [],
        [],
        [],
    )
    text = " ".join(
        artist.get_text()
        for artist in figures[-1].findobj()
        if hasattr(artist, "get_text")
    )
    assert "association data floor" not in text

