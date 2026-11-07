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
        annotations={"legacy-landing": "baseline"},
        fix_status_lines=("Knudsen oxygen fix: applied", "Total-pressure fix: applied"),
    )

    assert files["html"].is_file()
    assert files["chart:A"].is_file()
    assert files["chart:B:vapour"].is_file()
    assert files["table:all_rows"].is_file()
    page = files["html"].read_text(encoding="utf-8")
    assert "<details" in page and "<table" in page
    assert "temperature-min" in page and "sio2-min" in page
    assert "row source" in page.lower()
    assert "legacy-landing" in page
    assert "0.375" in files["table:progress"].read_text(encoding="utf-8")
    assert "openimcc-pin" in files["methods"].read_text(encoding="utf-8")
    assert "Knudsen oxygen fix: applied" in files["methods"].read_text(encoding="utf-8")

    scorecard_rows = list(csv.DictReader(files["table:scorecard"].open(newline="")))
    plot_rows = hero_plot._scorecard_plot_values(scorecard_rows)
    assert plot_rows == [
        (row["rail"], row["comparison_engine"], Decimal(row["primary_rms_dex"]), Decimal(row["comparison_rms_dex"]))
        for row in scorecard_rows
        if row["primary_rms_dex"] and row["comparison_rms_dex"]
    ]

