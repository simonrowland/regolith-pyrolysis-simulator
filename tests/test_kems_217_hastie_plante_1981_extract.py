from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery import migrate as battery_migrate


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACTS = REPO_ROOT / "data" / "literature" / "extracts"
SOURCE = EXTRACTS / "kems-217-hastie-plante-1981-nbsir-81-2293.yaml"
PRIOR = EXTRACTS / "kems-020-hastie-1981-nbsir.yaml"


def test_hastie_plante_table1_reuses_existing_fits_and_preserves_unknown_conditions() -> None:
    extract = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    prior = yaml.safe_load(PRIOR.read_text(encoding="utf-8"))
    overlap = extract["source_metadata"]["table1_overlap"]
    assert overlap["printed_row_count"] == 17
    assert overlap["duplicate_rows"]["row_count"] == 17

    stored = next(
        row
        for row in prior["species"]["K"]["observations"]
        if row["observation_id"]
        == "hastie_1981_table2_k_logP_coefficients_quoted_20260906"
    )["values"]["rows"]
    assert len(stored) == 17

    # These values were checked against the 81-2293 page renders: row 1 on
    # PDF p. 19, row 12 on p. 20, and row 17 on p. 21. The fits already live
    # in the kems-020 Table 2 data, so this extract must not duplicate them.
    assert (stored[0]["A"], stored[0]["B"], stored[0]["T_range_K"]) == (
        4.721,
        15624,
        [1300, 1800],
    )
    assert stored[0]["additional_coefficients_as_printed"] == "C = 9.32"
    assert (
        stored[11]["A"],
        stored[11]["B"],
        stored[11]["additional_coefficients_as_printed"],
        stored[11]["T_range_K"],
    ) == (
        5.228,
        16794,
        "C = 7.887 exp-2; D = 1.741 exp-3; E = 1.825 exp-2",
        [1400, 1900],
    )
    assert (stored[16]["A"], stored[16]["B"], stored[16]["T_range_K"]) == (
        6.231,
        17863,
        [1470, 1820],
    )
    assert extract["species"] == {}

    migrator = battery_migrate.Migrator(REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(SOURCE)
    migrator.finalize()
    result = migrator.result
    assert result.validation.hard_issues == ()
    assert result.observations == {}
    assert len(result.benches) == 2
    assert len(result.experiments) == 1
    assert len(result.queue) == 1
    assert result.queue[0].why == "extract yielded no observations"

    kms = next(bench for bench in result.benches.values() if bench.id.endswith("::bench::kms"))
    tms = next(bench for bench in result.benches.values() if bench.id.endswith("::bench::tms"))
    assert kms.method.locator.page == 7
    assert tms.method.locator.page == 7

    def bench_fact(bench, name):
        return next(fact for fact in bench.other_facts if fact.name == name)

    for bench, pressure, residence_time in (
        (kms, Decimal("0.0001"), Decimal("0.04")),
        (tms, Decimal("1"), Decimal("20")),
    ):
        pressure_fact = bench_fact(bench, "upper_pressure_limit")
        residence_fact = bench_fact(bench, "upper_gas_residence_time")
        assert pressure_fact.value.state.value.point == pressure
        assert residence_fact.value.state.value.point == residence_time
        assert residence_fact.value.state.value.approximate
        assert pressure_fact.value.locator.page == 7
        assert residence_fact.value.locator.page == 7

    experiment = next(iter(result.experiments.values()))
    assert experiment.locator.page == 15
    assert experiment.locator.published_page == 11
    assert experiment.locator.figure == "7"
    assert experiment.sample.form.state.value == "NaCl + K1 slag"
    assert experiment.sample.form.locator.page == 15
    assert experiment.sample.form.locator.published_page == 11
    printed_composition = experiment.sample.printed_composition
    assert printed_composition.state.value == {"K2O": "19.4"}
    assert printed_composition.locator.page == 15
    assert printed_composition.locator.published_page == 11
    assert experiment.sample.initial_composition is None
    assert experiment.conditions["temperature_K"].state.tag.value == "unknown"
    assert experiment.pressure_environment.total_pressure_Pa.state.tag.value == "unknown"
    calibration = experiment.apparatus.calibration
    assert calibration["pressure_factor_method"].state.tag.value == "unknown"
    assert calibration["pressure_factor_method"].locator.page == 7
    assert calibration["activity_check"].locator.page == 16
    assert calibration["activity_check"].locator.published_page == 12
    assert "near-unit NaCl activity" in calibration["activity_check"].state.value
