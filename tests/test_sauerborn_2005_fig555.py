from __future__ import annotations

import shutil
from inspect import signature

import pytest

from simulator.corpus_paths import regolith_corpus_root
from simulator.diagnostic_helpers.reproduction_compare import (
    TimeSeriesComparisonRefusal,
)
from simulator.diagnostic_helpers.sauerborn_2005 import (
    SAUERBORN_FIG555_FILES,
    SauerbornCorpusError,
    compare_sauerborn_o2_partial_pressure_time_series,
    load_sauerborn_fig555_path_series,
)


def _require_corpus_files() -> None:
    corpus_root = regolith_corpus_root()
    missing = [
        str(corpus_root / relative_path)
        for relative_path, _ in SAUERBORN_FIG555_FILES.values()
        if not (corpus_root / relative_path).is_file()
    ]
    if missing:
        pytest.skip(
            "Sauerborn Fig. 5.55 corpus files are missing: "
            f"{', '.join(missing)}"
        )


def test_reader_loads_cao_path_without_subtracting_blank() -> None:
    _require_corpus_files()
    series = load_sauerborn_fig555_path_series()

    assert set(series) == {"CaO", "SiO2", "ZnO", "ohne_Probe"}
    cao_path = series["CaO"]
    assert all(row["point_type"].startswith("path_") for row in cao_path)

    flat_top = [row for row in cao_path if 65.0 <= row["time_s"] <= 65.6]
    assert flat_top
    assert min(row["time_s"] for row in flat_top) == pytest.approx(65.02, abs=0.01)
    assert max(row["time_s"] for row in flat_top) == pytest.approx(65.59, abs=0.01)
    assert all(
        row["value_mbar"] == pytest.approx(8.23e-6, rel=0.001)
        for row in flat_top
    )
    blank_peak = max(row["value_mbar"] for row in series["ohne_Probe"])
    assert blank_peak < flat_top[0]["value_mbar"]
    after_top = next(row["value_mbar"] for row in cao_path if row["time_s"] > 68.0)
    assert after_top < flat_top[0]["value_mbar"]


@pytest.mark.parametrize("trace", tuple(SAUERBORN_FIG555_FILES))
def test_reader_rejects_a_changed_corpus_sha256(tmp_path, trace: str) -> None:
    _require_corpus_files()
    source_root = regolith_corpus_root()
    for relative_path, _ in SAUERBORN_FIG555_FILES.values():
        source = source_root / relative_path
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    relative_path, _ = SAUERBORN_FIG555_FILES[trace]
    mutated_path = tmp_path / relative_path
    mutated_path.write_bytes(mutated_path.read_bytes() + b"\n")

    with pytest.raises(SauerbornCorpusError, match=f"SHA-256 mismatch for {trace}"):
        load_sauerborn_fig555_path_series(tmp_path)


def test_sauerborn_comparison_refuses_missing_runtime_pressure_series() -> None:
    assert "argon_flow_nl_min" not in signature(
        compare_sauerborn_o2_partial_pressure_time_series
    ).parameters

    with pytest.raises(TimeSeriesComparisonRefusal) as refusal:
        compare_sauerborn_o2_partial_pressure_time_series(
            case_id="sauerborn_2005_fig555",
            source_id="kems-035-sauerborn-2005",
            observable_id="sauerborn_o2_partial_pressure_time_series",
            observation_points=(),
            runtime_pressure_samples=None,
            pre_high_temperature_base_mbar=1e-7,
            evidence_scope="QMS-cross O2 partial pressure",
            source_locator={"figure": "5.55"},
            recipe={},
            observation={},
            runtime={},
        )

    assert refusal.value.reason_code == "qms_po2_series_missing"


def test_sauerborn_comparison_scales_from_base_before_pointwise_compare() -> None:
    records = compare_sauerborn_o2_partial_pressure_time_series(
        case_id="sauerborn_2005_fig555",
        source_id="kems-035-sauerborn-2005",
        observable_id="sauerborn_o2_partial_pressure_time_series",
        observation_points=(
            {
                "time_s": 0.0,
                "expected_value_mbar": 1e-7,
                "expected_uncertainty": {"kind": "absolute", "value": 0.0},
                "source_locator": {"point_index": 0},
            },
            {
                "time_s": 60.0,
                # The pinned ZnO curve has a 1.1218782056e-4 mbar plateau.
                "expected_value_mbar": 1.1218782056e-4,
                "expected_uncertainty": {"kind": "absolute", "value": 0.0},
                "source_locator": {"point_index": 1},
            },
        ),
        runtime_pressure_samples=(
            {"time_s": 0.0, "value_mbar": 2e-7},
            {"time_s": 60.0, "value_mbar": 2e-4},
        ),
        pre_high_temperature_base_mbar=2e-7,
        evidence_scope="QMS-cross O2 partial pressure",
        source_locator={"figure": "5.55"},
        recipe={},
        observation={},
        runtime={},
    )

    assert len(records) == 2
    assert [record.units for record in records] == ["mbar", "mbar"]
    assert [record.actual_value for record in records] == pytest.approx(
        [1e-7, 1e-4]
    )
    assert records[0].coordinate == {"time_s": 0.0}
    assert records[1].coordinate == {"time_s": 60.0}
    # A gain fitted to the ZnO plateau changes the baseline or second value.
    assert [record.status for record in records] == ["match", "mismatch"]
