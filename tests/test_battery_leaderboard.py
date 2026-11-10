from __future__ import annotations

import csv
import json
import tarfile
from decimal import Decimal
from pathlib import Path

import pytest

import simulator.battery.leaderboard as leaderboard_module
from simulator.battery.enums import MetricOperation, NoticeKind
from simulator.battery.score import _median_rms_from_float64, compute_metric


def _residual(
    observation_id: str,
    *,
    species: str = "Fe",
    engine: str = "internal-analytical",
    rail: str = "vapour",
    observed: float = 1.0,
    predicted: float = 10.0,
    operation: str = "dex",
    unit: str = "dimensionless",
    temperature_K: float = 1000.0,
    fO2_Pa: float | None = None,
    source: str = "synthetic-source",
    status: str = "match",
    flagged: bool = False,
) -> dict[str, object]:
    candidate: dict[str, object] = {
        "experiment_id": observation_id,
        "observation_id": observation_id,
        "source_id": source,
        "engine": {"name": engine},
        "identity": {
            "species": {"formula": species},
            "temperature_K": {"tag": "value", "value": str(temperature_K)},
        },
    }
    if fO2_Pa is not None:
        candidate["identity"]["fO2_Pa"] = {
            "tag": "value",
            "value": str(fO2_Pa),
        }
    row: dict[str, object] = {
        "candidate_request": {"engine": engine},
        "candidate_observation": candidate,
        "reference": observation_id,
        "rail": rail,
        "status": status,
    }
    if status != "refused":
        operation_value = MetricOperation(operation)
        value = compute_metric(
            operation_value,
            Decimal(str(predicted)),
            Decimal(str(observed)),
        )
        assert value is not None
        candidate["value"] = {"kind": "point", "point": str(predicted)}
        row["numeric"] = {
            "operation": operation,
            "unit": unit,
            "value": str(value),
        }
    else:
        row["refusal"] = {"reason": "unsupported_species", "detail": {"formula": species}}
    if flagged:
        row["notices"] = [{"kind": NoticeKind.OUT_OF_CERTIFIED_BAND.value}]
    return row


def _write_archive(path: Path, rows: list[dict[str, object]]) -> None:
    residuals = path.with_suffix(".jsonl")
    residuals.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in [
                {"kind": "battery_store_stamp", "revision": "synthetic"},
                *rows,
            ]
        ),
        encoding="utf-8",
    )
    with tarfile.open(path, "w:gz") as archive:
        archive.add(residuals, arcname="data/battery/residuals.jsonl")


def _hero_report() -> dict[str, object]:
    return {
        "run_metadata": {
            "feedstock_id": "lunar_mare_low_ti",
            "backend": "internal-analytical",
            "activity_sources": {"vapour": ["openimcc"]},
        },
        "per_hour_summary": [
            {
                "hour": 1,
                "T_C": 100.0,
                "pO2_bar": 1e-5,
                "vapor_species_kg_hr": {"Fe": 0.055845},
            },
            {
                "hour": 2,
                "T_C": 200.0,
                "pO2_bar": 2e-5,
                "vapor_species_kg_hr": {"Fe": 0.055845},
            },
        ],
    }


def test_rows_rank_page_and_filter_by_engine_and_feedstock() -> None:
    rows = [
        _residual("fe-two", predicted=100.0),
        _residual("fe-minus-one", predicted=0.1),
        _residual("fe-openimcc", engine="openimcc", predicted=10.0 ** 0.5, flagged=True),
        _residual("fe-other-engine", engine="other", predicted=1000.0),
        _residual("fe-hot", temperature_K=1300.0, predicted=1e6),
        _residual("au", species="Au", predicted=1e4),
    ]

    worst = leaderboard_module.leaderboard(rows, level="row", order="worst", limit=20)
    assert worst["total"] == 5
    assert [row["observation_id"] for row in worst["rows"]] == [
        "fe-hot",
        "fe-other-engine",
        "fe-two",
        "fe-minus-one",
        "fe-openimcc",
    ]
    assert set(worst["rows"][0]) == {
        "rank",
        "observation_id",
        "source",
        "rail",
        "species",
        "engine",
        "T_K",
        "observed",
        "predicted",
        "residual",
        "unit",
        "flags",
    }
    assert worst["filters"]["temperature_K"] is None
    assert "Temperature: not filtered." in leaderboard_module._markdown(worst)
    assert worst["rows"][0]["observed"] == pytest.approx(1.0)
    assert worst["rows"][0]["predicted"] == pytest.approx(1e6)

    best = leaderboard_module.leaderboard(rows, level="row", order="best", limit=20)
    assert [row["observation_id"] for row in best["rows"]] == [
        "fe-openimcc",
        "fe-minus-one",
        "fe-two",
        "fe-other-engine",
        "fe-hot",
    ]
    assert best["rows"][0]["flags"] == [NoticeKind.OUT_OF_CERTIFIED_BAND.value]

    first = leaderboard_module.leaderboard(
        rows, level="row", order="worst", limit=2, offset=0
    )
    second = leaderboard_module.leaderboard(
        rows, level="row", order="worst", limit=2, offset=2
    )
    third = leaderboard_module.leaderboard(
        rows, level="row", order="worst", limit=2, offset=4
    )
    assert [*first["rows"], *second["rows"], *third["rows"]] == worst["rows"]
    assert second["total"] == 5
    assert second["offset"] == 2

    openimcc = leaderboard_module.leaderboard(rows, engine="openimcc", level="row")
    assert [row["observation_id"] for row in openimcc["rows"]] == ["fe-openimcc"]
    all_feedstocks = leaderboard_module.leaderboard(
        rows, feedstock="all", level="row", limit=20
    )
    assert all_feedstocks["total"] == 6
    assert "au" in [row["observation_id"] for row in all_feedstocks["rows"]]


def test_explicit_temperature_bounds_and_flag_spelling(tmp_path: Path) -> None:
    hot = _residual("fe-hot", temperature_K=1300.0)
    duplicate_flags = _residual("fe-flags")
    duplicate_flags["notices"] = [{"kind": "unverified-apparatus"}]
    duplicate_flags["candidate_observation"]["notices"] = [
        {"kind": "unverified_apparatus"},
        {"kind": "unverified-apparatus"},
    ]
    rows = [hot, duplicate_flags]

    bounded = leaderboard_module.leaderboard(
        rows, level="row", tmin_K=1200.0, tmax_K=1400.0
    )
    assert [row["observation_id"] for row in bounded["rows"]] == ["fe-hot"]
    assert bounded["filters"]["temperature_K"] == {
        "min": 1200.0,
        "max": 1400.0,
        "source": "explicit CLI bounds",
    }

    unbounded = leaderboard_module.leaderboard(rows, level="row", feedstock="all")
    flagged_row = next(
        row for row in unbounded["rows"] if row["observation_id"] == "fe-flags"
    )
    assert flagged_row["flags"] == ["unverified_apparatus"]

    archive = tmp_path / "bounds.tgz"
    _write_archive(archive, rows)
    output = tmp_path / "bounded.json"
    assert leaderboard_module.main(
        [
            "--archive",
            str(archive),
            "--feedstock",
            "all",
            "--tmin-K",
            "1200",
            "--tmax-K",
            "1400",
            "--format",
            "json",
            "--out",
            str(output),
        ]
    ) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["total"] == 1


def test_groups_keep_units_separate_include_flags_and_exclude_refusals() -> None:
    rows = [
        _residual("fe-a", observed=1.0, predicted=10.0 ** 0.3),
        _residual("fe-b", observed=1.0, predicted=10.0 ** -0.4),
        _residual(
            "fe-flagged",
            observed=1.0,
            predicted=10.0,
            flagged=True,
        ),
        _residual("fe-refused", status="refused"),
        _residual(
            "cao-enthalpy",
            species="CaO",
            rail="thermochemistry",
            operation="absolute",
            unit="kJ_per_declared_mol_basis",
            observed=10.0,
            predicted=20.0,
        ),
        _residual(
            "cao-ratio",
            species="CaO",
            rail="thermochemistry",
            operation="relative",
            unit="dimensionless",
            observed=10.0,
            predicted=12.0,
        ),
    ]
    result = leaderboard_module.leaderboard(rows, level="group", limit=20)

    iron = next(row for row in result["rows"] if row["species"] == "Fe")
    assert iron["n"] == 3
    assert iron["flagged_n"] == 1
    assert iron["rms"] == pytest.approx(
        float(_median_rms_from_float64([0.3, -0.4, 1.0])[1])
    )
    assert iron["rms_unflagged_only"] == pytest.approx(
        float(_median_rms_from_float64([0.3, -0.4])[1])
    )
    assert result["filters"]["refused_count"] == 1
    calcium = [row for row in result["rows"] if row["species"] == "CaO"]
    assert {row["unit"] for row in calcium} == {
        "dimensionless (relative)",
        "kJ_per_declared_mol_basis (absolute)",
    }
    assert len(calcium) == 2
    only_ratio = leaderboard_module.leaderboard(
        rows,
        rail="thermochemistry",
        species="CaO",
        level="row",
        unit="dimensionless (relative)",
    )
    assert [row["observation_id"] for row in only_ratio["rows"]] == ["cao-ratio"]
    dex_page = leaderboard_module.leaderboard(rows, level="row", unit="dex")
    assert dex_page["filters"]["refused_count"] == 1
    calcium_predictions = leaderboard_module.leaderboard(
        rows, rail="thermochemistry", species="CaO", level="row", limit=10
    )
    measured = {row["observation_id"]: row["observed"] for row in calcium_predictions["rows"]}
    assert measured == {"cao-ratio": pytest.approx(10.0), "cao-enthalpy": pytest.approx(10.0)}


def test_archive_run_report_and_versioned_json_markdown_and_csv(tmp_path: Path) -> None:
    rows = [
        _residual(
            "fe-in-envelope",
            engine="openimcc",
            observed=1.0,
            predicted=10.0,
            temperature_K=400.0,
            fO2_Pa=1.5,
        ),
        _residual(
            "fe-wrong-temperature",
            engine="openimcc",
            temperature_K=700.0,
            fO2_Pa=1.5,
        ),
        _residual(
            "fe-wrong-pressure",
            engine="openimcc",
            temperature_K=400.0,
            fO2_Pa=100.0,
        ),
        _residual(
            "wrong-engine",
            engine="other",
            temperature_K=400.0,
            fO2_Pa=1.5,
        ),
        _residual(
            "wrong-species",
            species="Au",
            engine="openimcc",
            temperature_K=400.0,
            fO2_Pa=1.5,
        ),
        _residual(
            "fe-refused",
            engine="openimcc",
            status="refused",
            temperature_K=400.0,
            fO2_Pa=1.5,
        ),
    ]
    archive = tmp_path / "synthetic-battery.tgz"
    _write_archive(archive, rows)
    run_path = tmp_path / "hero-run.json"
    run_path.write_text(json.dumps(_hero_report()), encoding="utf-8")
    output = tmp_path / "leaderboard.json"

    status = leaderboard_module.main(
        [
            "--archive",
            str(archive),
            "--run",
            str(run_path),
            "--engine",
            "openimcc",
            "--level",
            "group",
            "--format",
            "json",
            "--out",
            str(output),
        ]
    )
    assert status == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert set(payload) == {"schema", "filters", "total", "limit", "offset", "rows"}
    assert payload["schema"] == "battery-leaderboard.v1"
    assert set(payload["filters"]) == {
        "feedstock",
        "engine",
        "level",
        "order",
        "rail",
        "species",
        "unit",
        "temperature_K",
        "run_envelope",
        "refused_count",
        "rank_policy",
    }
    assert set(payload["filters"]["run_envelope"]) == {
        "feedstock_composition_class",
        "feedstock_class_source",
        "backend",
        "engines_by_role",
        "temperature_K",
        "pO2_bar",
        "species_by_rail",
        "evaporated_mol_by_species",
        "evaporated_mol_method",
    }
    assert payload["filters"]["run_envelope"]["pO2_bar"] == {
        "min": 1e-5,
        "max": 2e-5,
    }
    assert payload["filters"]["run_envelope"]["engines_by_role"]["vapour"] == [
        "openimcc"
    ]
    assert payload["total"] == 1
    assert payload["filters"]["refused_count"] == 1
    assert set(payload["rows"][0]) == {
        "rank",
        "rail",
        "species",
        "engine",
        "unit",
        "n",
        "flagged_n",
        "rms",
        "rms_unflagged_only",
        "median",
        "bias",
        "hero_evaporated_mol",
        "worst_observation_ids",
    }
    assert payload["rows"][0]["hero_evaporated_mol"] == pytest.approx(2.0)

    markdown_path = tmp_path / "leaderboard.md"
    assert leaderboard_module.main(
        [
            "--archive",
            str(archive),
            "--run",
            str(run_path),
            "--engine",
            "openimcc",
            "--level",
            "group",
            "--format",
            "md",
            "--out",
            str(markdown_path),
        ]
    ) == 0
    assert "Total ranked rows: 1" in markdown_path.read_text(encoding="utf-8")

    csv_path = tmp_path / "leaderboard.csv"
    assert leaderboard_module.main(
        [
            "--archive",
            str(archive),
            "--run",
            str(run_path),
            "--engine",
            "openimcc",
            "--level",
            "row",
            "--format",
            "csv",
            "--out",
            str(csv_path),
        ]
    ) == 0
    csv_lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert csv_lines[0] == "# total=1; limit=10; offset=0; refused_count=1"
    assert next(csv.reader(csv_lines[1:])) == [
        "rank",
        "observation_id",
        "source",
        "rail",
        "species",
        "engine",
        "T_K",
        "observed",
        "predicted",
        "residual",
        "unit",
        "flags",
    ]

