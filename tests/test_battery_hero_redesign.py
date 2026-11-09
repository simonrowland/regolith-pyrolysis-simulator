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


def _scorecard_pair_archive(
    path: Path, count: int = 25
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "scorecard-pair"}
    ]
    for engine, residual in (("openimcc", 0.1), ("internal-analytical", 0.3)):
        rows.extend(
            _residual(engine, "vapour", f"pair-{index}", 1500, residual)
            for index in range(count)
        )
    refused = _residual("magemin", "vapour", "not-scored", 1500, 0.1)
    refused["status"] = "refused"
    refused["numeric"] = None
    refused["refusal"] = {"reason": "unsupported_species"}
    refused["execution"] = {"call_evidence": True}
    rows.append(refused)
    _write_archive(path, rows)
    return rows


def _scorecard_layout_archive(path: Path) -> None:
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "scorecard-layout"}
    ]
    for rail, _label in hero_plot.COLLECTION_OBSERVABLES:
        for engine_index, engine in enumerate(ENGINE_ORDER):
            rows.extend(
                _residual(
                    engine,
                    rail,
                    f"layout-{rail}-{index}",
                    1500,
                    0.1 + engine_index / 100,
                )
                for index in range(25)
            )
    _write_archive(path, rows)


def _scorecard_empty_rail_archive(path: Path) -> None:
    refused = _residual("magemin", "vapour", "refused-only", 1500, 0.1)
    refused["status"] = "refused"
    refused["numeric"] = None
    refused["refusal"] = {"reason": "unsupported_species"}
    refused["execution"] = {"call_evidence": True}
    _write_archive(
        path,
        [{"kind": "battery_store_stamp", "revision": "scorecard-empty"}, refused],
    )


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


def _render_vapour_scorecard(
    archive: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[object, object, dict[str, Path]]:
    data = hero_plot.read_scores(archive, rails=("vapour",), _collection_mode=True)
    figures: list[object] = []
    save_collection_png = hero_plot._save_collection_png

    def capture_and_save(figure: object, path: Path) -> None:
        figures.append(figure)
        save_collection_png(figure, path)

    monkeypatch.setattr(hero_plot, "_save_collection_png", capture_and_save)
    files = hero_plot.render_hero_collection(
        data,
        archive,
        tmp_path / "collection",
        simulator_commit="simulator-pin",
        openimcc_commit="openimcc-pin",
        render_date="2026-11-09",
    )
    return data, figures[0], files


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
    assert scorecard_rows
    assert all(row["n_shared"] for row in scorecard_rows)
    assert all(row["primary_rms_dex"] for row in scorecard_rows)
    assert all(row["comparison_rms_dex"] for row in scorecard_rows)
    assert [
        handle.get_label() for handle in hero_plot._fingerprint_legend_handles()
    ] == [
        "within 0.5 dex — good",
        "0.5–1 dex — okay",
        "over 1 dex — needs work",
    ]


def test_d139_rows_are_kept_flagged_in_temperature_panels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ordinary = _residual("openimcc", "vapour", "ordinary", 1650, 0.25)
    uncalibrated = _residual("openimcc", "vapour", "d139-fixture", 1700, 4.5)
    uncalibrated["status"] = "no_band"
    uncalibrated["candidate_observation"]["identity"]["quantity"] = "p_partial"
    uncalibrated["candidate_observation"]["notices"] = [
        {
            "kind": "unverified_apparatus",
            "reason": "calibration_not_grounded: fixture without calibration",
        }
    ]
    uncalibrated["numeric"]["decision_band"] = None
    archive = tmp_path / "d139-panels.tgz"
    _write_archive(
        archive,
        [
            {"kind": "battery_store_stamp", "revision": "d139-fixture"},
            ordinary,
            uncalibrated,
        ],
    )
    data = hero_plot.read_scores(archive, _collection_mode=True)
    figures: list[object] = []
    save_collection_png = hero_plot._save_collection_png

    def capture_and_save(figure: object, path: Path) -> None:
        figures.append(figure)
        save_collection_png(figure, path)

    monkeypatch.setattr(hero_plot, "_save_collection_png", capture_and_save)
    files = hero_plot.render_hero_collection(
        data,
        archive,
        tmp_path / "collection",
        simulator_commit="simulator-pin",
        openimcc_commit="openimcc-pin",
        render_date="2026-11-07",
    )

    with files["table:temperature_points"].open(newline="") as point_file:
        points = list(csv.DictReader(point_file))
    d139_point = next(
        row for row in points if row["reference"] == "d139-fixture"
    )
    assert d139_point["flagged"] == "1"
    with files["table:panels"].open(newline="") as panel_file:
        panels = list(csv.DictReader(panel_file))
    panel = next(
        row
        for row in panels
        if row["rail"] == "vapour" and row["engine"] == "openimcc"
    )
    chart_a_row = next(
        row
        for row in hero_plot._hero_scorecard_rows(data, "vapour")
        if row[0] == "openimcc"
    )
    assert panel["n"] == "2"
    assert f"(n={panel['n']})" in chart_a_row[1]
    assert panel["flagged_n"] == "1"

    with files["table:temperature_bins"].open(newline="") as bin_file:
        bins = list(csv.DictReader(bin_file))
    d139_bin = next(
        row
        for row in bins
        if row["rail"] == "vapour"
        and row["engine"] == "openimcc"
        and row["temperature_bin"] == "17"
    )
    assert d139_bin["n"] == "1"
    with files["table:temperature_density"].open(newline="") as density_file:
        density = list(csv.DictReader(density_file))
    flagged_density = [
        row
        for row in density
        if row["rail"] == "vapour"
        and row["engine"] == "openimcc"
        and row["flagged"] == "1"
    ]
    assert sum(int(row["row_count"]) for row in flagged_density) == 1

    page = files["html"].read_text(encoding="utf-8")
    row_payload = page.split('id="row-data">', 1)[1].split("</script>", 1)[0]
    sampled_rows = json.loads(row_payload)
    assert next(
        row for row in sampled_rows if row["reference"] == "d139-fixture"
    )["flagged"] == "1"
    assert "const flagged=Number(row.flagged)===1" in page
    assert "ctx.fillStyle=flagged?lighten(PALETTE[row.engine],.38)" in page

    vapour_figure = next(
        figure
        for figure in figures
        if figure._suptitle.get_text()
        == "How does vapour pressure error change with temperature?"
    )
    axis = vapour_figure.axes[0]
    assert "1 flagged (uncalibrated KEMS)" in axis.get_title(loc="left")
    from matplotlib.collections import PathCollection
    from matplotlib.colors import to_rgba

    outlier = next(
        collection
        for collection in axis.collections
        if isinstance(collection, PathCollection)
        and len(collection.get_offsets())
        and any(float(point[1]) == 3.0 for point in collection.get_offsets())
    )
    expected_tint = to_rgba(
        hero_plot._lighten_colour(
            hero_plot._hero_engine_colors(data.engines)["openimcc"], 0.35
        )
    )
    assert outlier.get_facecolors()[0] == pytest.approx(expected_tint)

    methods = files["methods"].read_text(encoding="utf-8").casefold()
    assert (
        "plotted scatter, chart a, and the headline rms share one population; "
        "uncalibrated kems p_partial rows (d-139) are drawn tinted and counted, "
        "not excluded."
    ) in methods


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
    figures = _capture_collection_figures(monkeypatch)
    archive = tmp_path / "scorecard-artists.tgz"
    _scorecard_pair_archive(archive)
    data = hero_plot.read_scores(
        archive, rails=("vapour",), _collection_mode=True
    )
    scorecard = hero_plot._scorecard_table_rows(data)
    csv_tables = _collection_csv_round_trip(
        tmp_path,
        {
            "scorecard": scorecard,
            "coverage": _csv_rows(hero_plot._coverage_table_rows(data)),
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
    hero_plot._draw_scorecard_png(
        tmp_path / "a.png",
        data,
        scorecard_csv,
        csv_tables["coverage"],
        observables=(("vapour", "Vapour pressure"),),
    )
    axis = figures[-1].axes[0]
    pair_row = scorecard_csv[0]
    pair_line = next(
        line
        for line in axis.lines
        if line.get_gid() == "shared-row-pair:vapour:internal-analytical"
    )
    assert tuple(float(value) for value in pair_line.get_xdata()) == (
        float(pair_row["primary_rms_dex"]),
        float(pair_row["comparison_rms_dex"]),
    )
    all_rows = {
        collection.get_gid(): float(collection.get_offsets()[0][0])
        for collection in axis.collections
        if collection.get_gid()
    }
    for engine in ("openimcc", "internal-analytical"):
        metric = data.hero_all_numeric_metrics[("vapour", engine)]["views"]["all"]
        assert all_rows[f"all-rows-rms:vapour:{engine}"] == float(
            Decimal(str(metric["rms_dex"]))
        )
    assert tuple(float(value) for value in pair_line.get_xdata()) != (
        float(pair_row["comparison_rms_dex"]),
        float(pair_row["primary_rms_dex"]),
    )

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


def test_r2_metric_pins_and_decimal_temperature_quantile_values(
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
    # Sorted residuals 0.1, 0.2, 0.5, 1.1 at ranks 0.75, 1.5, 2.25
    # give Decimal quartiles 0.175, 0.35, 0.650. Decimal interpolation puts
    # Q1 one ULP below the pre-r5 float result; this changes chart display only.
    assert tuple(
        float(temperature_bin[field]).hex()
        for field in ("q1_dex", "median_dex", "q3_dex")
    ) == (
        "0x1.6666666666666p-3",
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


def test_renderer_quartiles_delegate_to_scorer_decimal_percentile_helper(
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


def test_interactive_empty_error_and_fingerprint_plots_skip_axes() -> None:
    script = hero_plot._COLLECTION_INTERACTIVE_SCRIPT

    def function_source(name: str, next_name: str) -> str:
        start = script.index(f"function {name}(")
        end = script.index(f"\nfunction {next_name}(", start)
        return script[start:end]

    unavailable_start = script.find("function setPlotUnavailable(")
    unavailable_helpers = (
        ""
        if unavailable_start < 0
        else script[
            unavailable_start : script.index("\nfunction plotError(", unavailable_start)
        ]
    )
    plot_error = function_source("plotError", "plotMap")
    plot_map = function_source("plotMap", "showTip")
    node_script = "\n".join(
        (
            "const stats={strokes:0,frames:0,labels:[],sizeCalls:0};",
            "const ctx={clearRect(){},fillRect(){},stroke(){stats.strokes++;},strokeRect(){stats.frames++;},fillText(value){stats.labels.push(String(value));},beginPath(){},moveTo(){},lineTo(){},setLineDash(){},save(){},translate(){},rotate(){},restore(){},arc(){},fill(){}};",
            "const errorCanvas={style:{display:''},_plot:{old:true}},mapCanvas={style:{display:''},_plot:{old:true}};",
            "const errorEmpty={hidden:true,textContent:''},mapEmpty={hidden:true,textContent:''};",
            "const document={documentElement:{},getElementById(id){return {'error-empty':errorEmpty,'map-empty':mapEmpty}[id];}};",
            "const railSelect={value:'vapour'},engineSelect={value:'openimcc'};",
            "const cellData={rails:['vapour'],engines:['openimcc']},PALETTE={openimcc:'#000'},BANDS={good:'#000'};",
            "const TEMPERATURE_FLOOR_K=1473.15,TEMPERATURE_BIN_K=50,SIO2_BIN_WIDTH=.05;",
            "let rows=[],cells=[],windowHasRows=true;",
            "function size(){stats.sizeCalls++;return {ctx,w:800,h:400};}",
            "function windowValues(){return [15,16,3,6];}",
            "function inWindow(row){return windowHasRows&&row.inWindow;}",
            "function enabled(){return true;}",
            "function getComputedStyle(){return {getPropertyValue(){return '#000';}};}",
            unavailable_helpers,
            plot_error,
            plot_map,
            "function run(inputRows,inputCells,hasWindowRows){rows=inputRows;cells=inputCells;windowHasRows=hasWindowRows;Object.assign(stats,{strokes:0,frames:0,labels:[],sizeCalls:0});errorCanvas.style.display='';mapCanvas.style.display='';errorCanvas._plot={old:true};mapCanvas._plot={old:true};errorEmpty.hidden=true;mapEmpty.hidden=true;errorEmpty.textContent='';mapEmpty.textContent='';plotError();plotMap();return {strokes:stats.strokes,frames:stats.frames,labels:stats.labels,sizeCalls:stats.sizeCalls,error:{hidden:errorEmpty.hidden,text:errorEmpty.textContent,canvas:errorCanvas.style.display,plot:errorCanvas._plot},map:{hidden:mapEmpty.hidden,text:mapEmpty.textContent,canvas:mapCanvas.style.display,plot:mapCanvas._plot}};}",
            "const filteredRows=[{rail:'vapour',engine:'openimcc',inWindow:false,temperature_K:5000,residual_dex:'0.2'}],filteredCells=[[0,0,50,3,3,'0','0','0.2',1,false]];",
            "console.log(JSON.stringify([run([],[],true),run(filteredRows,filteredCells,false)]));",
        )
    )
    result = subprocess.run(
        ["node", "-e", node_script],
        check=True,
        capture_output=True,
        text=True,
    )
    cases = json.loads(result.stdout)
    assert len(cases) == 2
    for case in cases:
        assert case["strokes"] == 0
        assert case["frames"] == 0
        assert case["labels"] == []
        assert case["error"]["hidden"] is False
        assert "No scored rows" in case["error"]["text"]
        assert case["error"]["canvas"] == "none"
        assert case["error"]["plot"] is None
        assert case["map"]["hidden"] is False
        assert "No fingerprint cells" in case["map"]["text"]
        assert case["map"]["canvas"] == "none"
        assert case["map"]["plot"] is None


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
    archive = tmp_path / "scorecard-header.tgz"
    _scorecard_pair_archive(archive)
    data = hero_plot.read_scores(
        archive, rails=("vapour",), _collection_mode=True
    )
    hero_plot._draw_scorecard_png(
        tmp_path / "a.png",
        data,
        hero_plot._scorecard_table_rows(data),
        _csv_rows(hero_plot._coverage_table_rows(data)),
        observables=(("vapour", "Vapour pressure"),),
    )
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


def test_scorecard_pairs_lead_the_separate_all_rows_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = tmp_path / "scorecard-order.tgz"
    _scorecard_pair_archive(archive)
    data = hero_plot.read_scores(
        archive, rails=("vapour",), _collection_mode=True
    )
    figures = _capture_collection_figures(monkeypatch)

    hero_plot._draw_scorecard_png(
        tmp_path / "a.png",
        data,
        hero_plot._scorecard_table_rows(data),
        _csv_rows(hero_plot._coverage_table_rows(data)),
        observables=(("vapour", "Vapour pressure"),),
    )

    axis = figures[-1].axes[0]
    pair_line = next(
        line
        for line in axis.lines
        if line.get_gid() == "shared-row-pair:vapour:internal-analytical"
    )
    summary_markers = [
        collection
        for collection in axis.collections
        if str(collection.get_gid()).startswith("all-rows-rms:vapour:")
    ]
    assert len(summary_markers) == 2
    assert all(
        float(marker.get_offsets()[0][1]) > float(pair_line.get_ydata()[0])
        for marker in summary_markers
    )
    assert any(text.get_text() == "All rows" for text in axis.texts)


def test_scorecard_multi_rail_labels_and_markers_do_not_overlap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from matplotlib.collections import PathCollection
    from matplotlib.transforms import Affine2D, Bbox

    archive = tmp_path / "scorecard-layout.tgz"
    _scorecard_layout_archive(archive)
    data = hero_plot.read_scores(
        archive,
        rails=tuple(rail for rail, _label in hero_plot.COLLECTION_OBSERVABLES),
        _collection_mode=True,
    )
    figures = _capture_collection_figures(monkeypatch)
    hero_plot._draw_scorecard_png(
        tmp_path / "scorecard-layout.png",
        data,
        hero_plot._scorecard_table_rows(data),
        _csv_rows(hero_plot._coverage_table_rows(data)),
    )

    figure = figures[-1]
    _assert_chart_header_text_does_not_overlap(figure)
    renderer = figure.canvas.get_renderer()
    assert len(figure.axes) == len(hero_plot.COLLECTION_OBSERVABLES)
    pair_labels_by_axis = []
    count_labels_by_axis = []
    for axis in figure.axes:
        pair_lines = [
            line
            for line in axis.lines
            if str(line.get_gid()).startswith("shared-row-pair:")
        ]
        all_row_markers = [
            collection
            for collection in axis.collections
            if str(collection.get_gid()).startswith("all-rows-rms:")
        ]
        pair_labels = [text for text in axis.texts if " vs " in text.get_text()]
        count_labels = [
            text
            for text in axis.texts
            if text.get_text().startswith(("shared rows, n=", "all rows, n="))
        ]
        assert len(pair_lines) == len(ENGINE_ORDER) - 1
        assert len(pair_labels) == len(pair_lines)
        assert len(all_row_markers) == len(ENGINE_ORDER)
        assert len(count_labels) == len(pair_lines) + len(all_row_markers)
        assert all(
            min(float(marker.get_offsets()[0][1]) for marker in all_row_markers)
            > max(float(line.get_ydata()[0]) for line in pair_lines)
            for marker in all_row_markers
        )

        marker_boxes = []
        for collection in axis.collections:
            if not isinstance(collection, PathCollection) or not len(
                collection.get_offsets()
            ):
                continue
            marker_box = collection.get_window_extent(renderer)
            if all(math.isfinite(value) for value in marker_box.extents):
                marker_boxes.append(marker_box)
                continue
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
        summary_engine_labels = [
            text
            for text in axis.texts
            if text.get_text() in ENGINE_ORDER
        ]
        protected_text = pair_labels + count_labels + summary_engine_labels
        for text in protected_text:
            text_box = text.get_window_extent(renderer)
            assert all(
                not text_box.overlaps(marker_box) for marker_box in marker_boxes
            ), f"{text.get_text()} overlaps a scorecard marker"
        pair_labels_by_axis.append(pair_labels)
        count_labels_by_axis.append(count_labels)

    for index in range(len(figure.axes) - 1):
        for count in count_labels_by_axis[index]:
            for label in pair_labels_by_axis[index + 1]:
                assert not count.get_window_extent(renderer).overlaps(
                    label.get_window_extent(renderer)
                ), "neighboring rail count overlaps a pair label"


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
    assert any("no scored rows" in text.get_text() for text in axis.texts)
    assert not axis.axison
    assert not any(
        len(line.get_ydata())
        and all(float(value) == 0 for value in line.get_ydata())
        for line in axis.lines
    )


def test_empty_collection_panels_explain_missing_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    figures = _capture_collection_figures(monkeypatch)
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
    assert any("no scored rows" in text for text in temperature_text)
    assert "unavailable dex" not in figures[-1].axes[0].get_title()

    hero_plot._draw_fingerprint_png(tmp_path / "d.png", "vapour", [], ["magemin"])
    fingerprint_text = [text.get_text() for text in figures[-1].axes[0].texts]
    assert "no scored rows" in fingerprint_text
    assert not figures[-1].axes[0].axison


def test_empty_collection_chart_facets_explain_missing_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    figures = _capture_collection_figures(monkeypatch)
    observables = (("vapour", "Vapour pressure"),)
    hero_plot._draw_temperature_png(
        tmp_path / "empty-b.png", "vapour", [], [], [], []
    )
    hero_plot._draw_component_png(
        tmp_path / "empty-c.png",
        "vapour",
        [
            {
                "rail": "vapour",
                "component": "SiO",
                "shared_with": "vaporock",
                "engine": engine,
                "n_shared": "5",
                "rms_dex": "",
            }
            for engine in ("openimcc", "vaporock")
        ],
    )
    hero_plot._draw_fingerprint_png(
        tmp_path / "empty-d.png",
        "vapour",
        [
            {
                "rail": "vapour",
                "engine": "magemin",
                "temperature_lower_K": "1500",
                "temperature_upper_K": "1600",
                "sio2_lower": "0.2",
                "sio2_upper": "0.25",
                "n": "1",
                "median_abs_dex": "0.2",
            }
        ],
        ["magemin"],
    )
    hero_plot._draw_coverage_png(tmp_path / "empty-e.png", [], observables)
    hero_plot._draw_progress_png(tmp_path / "empty-f.png", [], observables)
    hero_plot._draw_exposure_png(
        tmp_path / "empty-g.png",
        [
            {
                "rail": "vapour",
                "engine": "openimcc",
                "exposed_n": "0",
                "held_out_n": "0",
            }
        ],
        observables,
    )

    expected = (
        "no coverage data",
        "no shared-row component comparisons",
        "no fingerprint cells with n ≥ 3",
        "no coverage data",
        "no landing history",
        "no exposure data",
    )
    for figure, label in zip(figures, expected, strict=True):
        rendered = "\n".join(
            [text.get_text() for text in figure.texts]
            + [
                text.get_text()
                for axis in figure.axes
                for text in axis.texts
            ]
        ).casefold()
        assert label in rendered
        assert figure.axes
        assert all(not axis.axison for axis in figure.axes)


def test_temperature_panel_with_scores_but_no_temperature_shows_coverage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    row = _residual("openimcc", "vapour", "missing-temperature", 1500, 0.25)
    row["candidate_observation"]["identity"].pop("temperature_K")
    archive = tmp_path / "missing-temperature.tgz"
    _write_archive(
        archive,
        [{"kind": "battery_store_stamp", "revision": "missing-temperature"}, row],
    )
    data = hero_plot.read_scores(
        archive, rails=("vapour",), _collection_mode=True
    )
    database = sqlite3.connect(":memory:")
    hero_plot._collection_row_database(
        archive, database, bin_width_K=data.bin_width_K
    )
    tables = hero_plot._collection_raw_tables(
        database, data, bin_width_K=data.bin_width_K
    )
    database.close()
    panel = next(
        row
        for row in tables["panels"]
        if row["rail"] == "vapour" and row["engine"] == "openimcc"
    )
    assert panel["n"] == 1
    assert panel["plotted_n"] == 0
    assert data.hero_all_numeric_metrics[("vapour", "openimcc")]["views"][
        "all"
    ]["n"] == 1

    figures = _capture_collection_figures(monkeypatch)
    hero_plot._draw_temperature_png(
        tmp_path / "missing-temperature.png",
        "vapour",
        _csv_rows([panel]),
        [],
        [],
        [],
    )
    axis = figures[-1].axes[0]
    assert not axis.axison
    assert "1 scored, 0 with temperature" in " ".join(
        text.get_text() for text in axis.texts
    )
    assert not axis.lines
    assert not axis.collections


def test_scorecard_shows_internal_analytical_pair_and_separate_all_rows_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "scorecard-pair.tgz"
    _scorecard_pair_archive(archive)

    _data, figure, files = _render_vapour_scorecard(archive, tmp_path, monkeypatch)

    labels = [text.get_text() for text in figure.texts]
    labels.extend(text.get_text() for axis in figure.axes for text in axis.texts)
    labels.extend(
        text.get_text()
        for legend in figure.legends
        for text in legend.get_texts()
    )
    if figure._suptitle is not None:
        labels.append(figure._suptitle.get_text())
    rendered = "\n".join(labels).casefold()
    assert "internal-analytical" in rendered
    assert "shared rows, n=25" in rendered
    assert "each engine on its own rows; not a like-for-like comparison" in rendered
    assert "not scored: magemin (outside the engine's domain)" in rendered
    assert "no shared rows" not in rendered
    assert "interim" in files["methods"].read_text(encoding="utf-8").casefold()
    assert "<title>Interim battery hero charts</title>" in files["html"].read_text(
        encoding="utf-8"
    )


def test_scorecard_drops_empty_rail_axis_and_lists_refusal_mix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "scorecard-empty.tgz"
    _scorecard_empty_rail_archive(archive)

    _data, figure, _files = _render_vapour_scorecard(archive, tmp_path, monkeypatch)

    assert figure.axes == []
    assert figure.legends == []
    footer = "\n".join(text.get_text() for text in figure.texts).casefold()
    assert "vapour: no engine produced a scored prediction" in footer
    assert "refusal mix:" in footer


def test_scorecard_all_rows_rms_matches_scorer_headline_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "scorecard-metrics.tgz"
    fixture_rows = _scorecard_pair_archive(archive)

    data, figure, _files = _render_vapour_scorecard(archive, tmp_path, monkeypatch)

    markers = {
        collection.get_gid(): collection
        for axis in figure.axes
        for collection in axis.collections
        if collection.get_gid()
    }
    for engine in ("openimcc", "internal-analytical"):
        fixture_values = [
            float(row["numeric"]["value"])
            for row in fixture_rows
            if row.get("rail") == "vapour"
            and row.get("status") == "match"
            and row.get("candidate_request", {}).get("engine") == engine
            and row.get("numeric", {}).get("operation") == "dex"
        ]
        fixture_n = len(fixture_values)
        fixture_rms = math.sqrt(
            math.fsum(value * value for value in fixture_values) / fixture_n
        )
        all_rows = data.hero_all_numeric_metrics[("vapour", engine)]["views"]["all"]
        marker = markers[f"all-rows-rms:vapour:{engine}"]
        plotted_rms = float(marker.get_offsets()[0][0])
        assert int(all_rows["n"]) == fixture_n
        assert float(Decimal(str(all_rows["rms_dex"]))) == fixture_rms
        assert plotted_rms == fixture_rms
        assert f"n={fixture_n}" in "\n".join(
            text.get_text() for axis in figure.axes for text in axis.texts
        )


def test_scorecard_low_count_pair_is_text_with_both_shared_row_rms_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "scorecard-small-pair.tgz"
    _scorecard_pair_archive(archive, count=10)

    _data, figure, _files = _render_vapour_scorecard(archive, tmp_path, monkeypatch)

    labels = "\n".join(
        text.get_text() for axis in figure.axes for text in axis.texts
    ).casefold()
    assert "internal-analytical" in labels
    assert "shared rows, n=10" in labels
    assert "both rms" in labels or ("0.1" in labels and "0.3" in labels)


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
