from __future__ import annotations

import csv
import io
import json
import tarfile
from decimal import Decimal
from pathlib import Path

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

