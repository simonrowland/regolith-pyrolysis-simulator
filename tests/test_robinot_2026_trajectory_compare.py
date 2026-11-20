from __future__ import annotations

import csv
from copy import deepcopy
import hashlib
import math
from pathlib import Path

import pytest
import yaml

from simulator.diagnostic_helpers.reproduction_compare import (
    O2_RELEASE_RATE_DEPENDENCY,
    STANDARD_MOLAR_VOLUME_L_PER_MOL,
    TimeSeriesComparisonRefusal,
    compare_o2_release_time_series,
    compare_time_series,
    o2_release_molar_flow_to_ppm,
)


ROOT = Path(__file__).resolve().parents[1]
PRESET_PATH = ROOT / "data/presets/vacuum_pyrolysis/robinot_2026.yaml"
CORPUS_BRANCH = "w1119fig4-robinot-2026-f4"
CORPUS_COMMIT = "0269321364cb71577205e7de678588eb9f735aca"
CORPUS_ROOT = Path.home() / "Repos/regolith-corpus"
CORPUS_TABLES = Path("tables/kems-044-robinot-2026")
CORPUS_FILES = {
    "f4a_csv": "f4a.csv",
    "f4a_provenance": "f4a.provenance.yaml",
    "f4b_csv": "f4b.csv",
    "f4b_provenance": "f4b.provenance.yaml",
}


def _preset() -> dict:
    return yaml.safe_load(PRESET_PATH.read_text(encoding="utf-8"))


def _observable(preset: dict, observable_id: str) -> dict:
    return next(
        item
        for item in preset["trajectory_comparison"]["observables"]
        if item["observable_id"] == observable_id
    )


def _observation_points(observable: dict) -> list[dict]:
    fields = observable["point_fields"]
    return [
        {
            "time_min": point[fields.index("time_min")],
            "expected_value": point[fields.index("expected_value")],
            "expected_uncertainty": {
                "kind": "absolute",
                "value": point[fields.index("combined_uncertainty")],
            },
            "source_locator": {
                "source_pin": observable["source_pin"],
                "point_index": index,
            },
        }
        for index, point in enumerate(observable["points"])
    ]


def _runtime_samples(points: list[dict]) -> list[dict]:
    return [
        {"time_min": point["time_min"], "value": point["expected_value"]}
        for point in points
    ]


def _compare(
    points: list[dict],
    runtime_samples: list[dict],
    *,
    observable_id: str = "robinot_o2_ppm_time_series",
    units: str = "ppm",
):
    return compare_time_series(
        case_id="robinot_2026",
        source_id="kems-044-robinot-2026",
        observable_id=observable_id,
        observation_points=points,
        runtime_samples=runtime_samples,
        units=units,
        evidence_scope="analyzer_visible_o2_in_argon_carrier",
        source_locator={"figure": "4a"},
        recipe={"preset": "robinot_2026"},
        observation={"points": points},
        runtime={"samples": runtime_samples},
        species="O2",
    )


@pytest.mark.parametrize(
    ("observable_id", "units"),
    [
        ("robinot_o2_ppm_time_series", "ppm"),
        ("robinot_cumulative_o2_mass_time_series", "mg"),
    ],
)
def test_matching_runtime_has_zero_residuals_and_mutation_is_visible(
    observable_id: str,
    units: str,
) -> None:
    points = _observation_points(
        _observable(_preset(), observable_id)
    )
    samples = _runtime_samples(points)

    records = _compare(points, samples, observable_id=observable_id, units=units)

    assert len(records) == len(points) + 1
    assert records[0].units == units
    assert records[-1].units == f"{units} min"
    assert all(record.residual == pytest.approx(0.0) for record in records)
    mutated = deepcopy(samples)
    mutated[37]["value"] += 1.0
    mutated_records = _compare(
        points, mutated, observable_id=observable_id, units=units
    )
    assert mutated_records[37].residual == pytest.approx(1.0)


def test_shifted_runtime_has_known_point_and_integral_residuals() -> None:
    points = _observation_points(
        _observable(_preset(), "robinot_o2_ppm_time_series")
    )
    samples = _runtime_samples(points)
    shift = 3.25
    shifted = [
        {"time_min": row["time_min"], "value": row["value"] + shift}
        for row in samples
    ]

    records = _compare(points, shifted)

    assert all(record.residual == pytest.approx(shift) for record in records[:-1])
    duration = points[-1]["time_min"] - points[0]["time_min"]
    assert records[-1].residual == pytest.approx(shift * duration)
    mutated = [
        {"time_min": row["time_min"], "value": row["value"] + 1.0}
        for row in shifted
    ]
    assert _compare(points, mutated)[0].residual == pytest.approx(shift + 1.0)


def test_runtime_samples_are_interpolated_linearly_to_observation_times() -> None:
    points = [
        {
            "time_min": time_min,
            "expected_value": value,
            "expected_uncertainty": {"kind": "absolute", "value": 0.1},
        }
        for time_min, value in ((0.0, 0.0), (0.5, 5.0), (1.0, 10.0))
    ]
    runtime_samples = [
        {"time_min": 0.0, "value": 0.0},
        {"time_min": 1.0, "value": 10.0},
    ]

    records = _compare(points, runtime_samples)

    assert [record.actual_value for record in records[:-1]] == pytest.approx(
        [0.0, 5.0, 10.0]
    )
    assert [record.residual for record in records] == pytest.approx([0.0] * 4)
    mutated = [dict(runtime_samples[0]), {"time_min": 1.0, "value": 12.0}]
    assert _compare(points, mutated)[1].residual == pytest.approx(1.0)


def test_missing_runtime_sample_refuses_instead_of_becoming_zero() -> None:
    points = _observation_points(
        _observable(_preset(), "robinot_o2_ppm_time_series")
    )
    samples = _runtime_samples(points)
    missing_index = 25
    missing = deepcopy(samples)
    missing[missing_index]["value"] = None

    with pytest.raises(TimeSeriesComparisonRefusal) as refusal:
        _compare(points, missing)
    assert refusal.value.reason_code == "runtime_sample_missing"
    assert "12.5 min" in str(refusal.value)

    # Mutation: replacing the absent value with a fabricated zero is accepted
    # as data, demonstrating why the missing value must raise a typed refusal.
    zero_filled = deepcopy(samples)
    zero_filled[missing_index]["value"] = 0.0
    record = _compare(points, zero_filled)[missing_index]
    assert record.actual_value == 0.0
    assert record.residual == pytest.approx(-points[missing_index]["expected_value"])


def test_o2_release_rates_map_to_ppm_and_missing_rates_name_timestep_dependency() -> None:
    preset = _preset()
    argon_flow = preset["lab_schedule"]["gas_boundary"]["imposed_flow"]["value"]
    argon_mol_min = argon_flow / STANDARD_MOLAR_VOLUME_L_PER_MOL
    one_percent_o2_rate = argon_mol_min / 99.0

    assert o2_release_molar_flow_to_ppm(
        one_percent_o2_rate,
        argon_flow_nl_min=argon_flow,
    ) == pytest.approx(10_000.0)
    assert o2_release_molar_flow_to_ppm(
        one_percent_o2_rate * 1.5,
        argon_flow_nl_min=argon_flow,
    ) != pytest.approx(10_000.0)

    ppm_observations = [
        {
            "time_min": time_min,
            "expected_value": ppm,
            "expected_uncertainty": {"kind": "absolute", "value": 1.0},
        }
        for time_min, ppm in ((0.0, 10_000.0), (1.0, 20_000.0))
    ]
    rate_samples = [
        {"time_min": 0.0, "o2_release_mol_min": one_percent_o2_rate},
        {
            "time_min": 1.0,
            "o2_release_mol_min": argon_mol_min * 0.02 / 0.98,
        },
    ]
    converted_records = compare_o2_release_time_series(
        case_id="synthetic",
        source_id="source",
        observable_id="o2_ppm",
        observation_points=ppm_observations,
        runtime_rate_samples=rate_samples,
        argon_flow_nl_min=argon_flow,
        evidence_scope="synthetic carrier concentration",
        source_locator="synthetic",
        recipe={},
        observation={"points": ppm_observations},
        runtime={"rates": rate_samples},
    )
    assert [record.residual for record in converted_records] == pytest.approx(
        [0.0, 0.0, 0.0]
    )
    mutated_rates = deepcopy(rate_samples)
    mutated_rates[1]["o2_release_mol_min"] *= 1.5
    mutated_records = compare_o2_release_time_series(
        case_id="synthetic",
        source_id="source",
        observable_id="o2_ppm",
        observation_points=ppm_observations,
        runtime_rate_samples=mutated_rates,
        argon_flow_nl_min=argon_flow,
        evidence_scope="synthetic carrier concentration",
        source_locator="synthetic",
        recipe={},
        observation={"points": ppm_observations},
        runtime={"rates": mutated_rates},
    )
    assert mutated_records[1].residual != pytest.approx(0.0)

    points = _observation_points(
        _observable(preset, "robinot_o2_ppm_time_series")
    )
    with pytest.raises(TimeSeriesComparisonRefusal) as refusal:
        compare_o2_release_time_series(
            case_id="robinot_2026",
            source_id="kems-044-robinot-2026",
            observable_id="robinot_o2_ppm_time_series",
            observation_points=points,
            runtime_rate_samples=None,
            argon_flow_nl_min=argon_flow,
            evidence_scope="analyzer_visible_o2_in_argon_carrier",
            source_locator={"figure": "4a"},
            recipe=preset,
            observation={"points": points},
            runtime={},
        )
    assert refusal.value.reason_code == "o2_release_rate_series_missing"
    assert refusal.value.dependency == O2_RELEASE_RATE_DEPENDENCY
    assert "w1119dt" in str(refusal.value)


def _series_rows(path: Path) -> dict[str, list[dict[str, float]]]:
    result: dict[str, list[dict[str, float]]] = {}
    with path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            result.setdefault(row["series"], []).append(
                {
                    "time_min": float(row["x"]),
                    "value": float(row["y"]),
                    "uncertainty": float(row["y_unc"]),
                }
            )
    return result


def _source_value_at(rows: list[dict[str, float]], time_min: float) -> tuple[float, float]:
    left = None
    for index, right in enumerate(rows):
        if right["time_min"] == time_min:
            while (
                index + 1 < len(rows)
                and rows[index + 1]["time_min"] == time_min
            ):
                index += 1
            point = rows[index]
            return point["value"], point["uncertainty"]
        if right["time_min"] > time_min:
            if left is None:
                break
            width = right["time_min"] - left["time_min"]
            fraction = (time_min - left["time_min"]) / width
            return (
                left["value"] + fraction * (right["value"] - left["value"]),
                left["uncertainty"]
                + fraction * (right["uncertainty"] - left["uncertainty"]),
            )
        left = right
    raise AssertionError(f"source does not bracket {time_min} min")


def _grid_mismatches(
    preset_series: dict,
    source_rows: list[dict[str, float]],
) -> list[str]:
    fields = preset_series["point_fields"]
    indexes = {field: fields.index(field) for field in fields}
    mismatches = []
    for index, point in enumerate(preset_series["points"]):
        time_min = point[indexes["time_min"]]
        value, digitization = _source_value_at(source_rows, time_min)
        analyzer = abs(value) * 0.02
        combined = math.hypot(digitization, analyzer)
        for key, expected in (
            ("expected_value", value),
            ("digitization_uncertainty", digitization),
            ("analyzer_uncertainty", analyzer),
            ("combined_uncertainty", combined),
        ):
            if not math.isclose(
                point[indexes[key]], expected, rel_tol=0.0, abs_tol=0.000002
            ):
                mismatches.append(
                    f"{preset_series['observable_id']}[{index}].{key}: "
                    f"{point[indexes[key]]} != {expected}"
                )
    return mismatches


def test_corpus_pin_and_cell_mutation_guard() -> None:
    preset = _preset()
    trajectory = preset["trajectory_comparison"]
    pins = trajectory["source_pins"]
    observables = {
        item["observable_id"]: item for item in trajectory["observables"]
    }
    power = trajectory["recorded_input_traces"][0]
    assert set(observables) == {
        "robinot_o2_ppm_time_series",
        "robinot_cumulative_o2_mass_time_series",
    }
    assert power["role"] == "measured_driver"
    assert "observable_id" not in power
    assert trajectory["grid_interval_min"] == 0.5
    assert trajectory["grid_end_min"] == 59.5
    for series in (*observables.values(), power):
        assert len(series["points"]) == 120
        time_index = series["point_fields"].index("time_min")
        assert [point[time_index] for point in series["points"]] == pytest.approx(
            [index * 0.5 for index in range(120)]
        )

    paths = {
        pin: CORPUS_ROOT / CORPUS_TABLES / filename
        for pin, filename in CORPUS_FILES.items()
    }
    missing = [path for path in paths.values() if not path.is_file()]
    if missing:
        pytest.skip(
            f"corpus branch {CORPUS_BRANCH} commit {CORPUS_COMMIT} "
            f"lacks pinned file(s): {', '.join(str(path) for path in missing)}"
        )

    source_data = {}
    for pin, path in paths.items():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == pins[pin]["sha256"], f"corpus pin changed for {path}"
        assert pins[pin]["path"] == str(CORPUS_TABLES / CORPUS_FILES[pin])
        if pin.endswith("_csv"):
            source_data[pin] = _series_rows(path)

    ppm = observables["robinot_o2_ppm_time_series"]
    cumulative = observables["robinot_cumulative_o2_mass_time_series"]
    assert len(ppm["points"]) + len(cumulative["points"]) + len(power["points"]) == 360
    assert not _grid_mismatches(ppm, source_data["f4a_csv"]["O2 concentration"])
    assert not _grid_mismatches(
        cumulative, source_data["f4b_csv"]["Cumulative O2"]
    )

    power_fields = power["point_fields"]
    time_index = power_fields.index("time_min")
    value_index = power_fields.index("expected_value")
    digitization_index = power_fields.index("digitization_uncertainty")
    power_mismatches = []
    for index, point in enumerate(power["points"]):
        value, uncertainty = _source_value_at(
            source_data["f4a_csv"]["Solar power"], point[time_index]
        )
        if not math.isclose(point[value_index], value, abs_tol=0.000002):
            power_mismatches.append(f"power[{index}].value")
        if not math.isclose(
            point[digitization_index], uncertainty, abs_tol=0.000002
        ):
            power_mismatches.append(f"power[{index}].uncertainty")
    assert not power_mismatches

    # Mutation: changing one source cell makes the public resampled grid fail.
    mutated_source = deepcopy(source_data["f4a_csv"]["O2 concentration"])
    mutated_source[350]["value"] += 10.0
    assert _grid_mismatches(ppm, mutated_source)
