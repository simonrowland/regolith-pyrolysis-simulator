"""Pins the Table 3 copper isotope series and its sample-wise reduction."""

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/florin-2025-cu-silicate-evaporation.yaml"


def test_florin_2025_isotope_series_and_isolated_migration() -> None:
    payload = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = payload["species"]["Cu"]["observations"]
    by_id = {row["observation_id"]: row for row in observations}
    direct = by_id["florin_2025_table_3_cu_isotope_delta_erm633"]
    reduced = by_id["florin_2025_table_3_cu_isotope_delta_sc"]
    baseline = by_id["florin_2025_table_3_starting_isotope_composition"]
    direct_points = direct["values"]["points"]
    reduced_points = reduced["values"]["points"]
    assert len(direct_points) == len(reduced_points) == 24
    assert direct_points[0]["sample"] == "Cu12.1"
    assert direct_points[-1]["sample"] == "Cu15.6"
    assert baseline["values"]["points"][0]["isotope_delta_delta65CuERM633_permil"] == 0.16
    assert direct_points[0]["isotope_delta_delta65CuERM633_permil"] == 0.39
    assert reduced_points[0]["isotope_delta_Delta65CuSC_permil"] == 0.23
    assert all(
        round(source["isotope_delta_delta65CuERM633_permil"] - 0.16, 2)
        == result["isotope_delta_Delta65CuSC_permil"]
        for source, result in zip(direct_points, reduced_points, strict=True)
    )
    assert all(point["two_sigma_permil"] > 0 for point in direct_points + reduced_points)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    migrated = list(migrator.result.observations.values())
    assert len(migrated) == 49
    sample_observations = [row for row in migrated if row.identity.temperature_K.value is not None]
    assert len(sample_observations) == 48
    assert all(
        str(row.identity.temperature_K.value)
        in {"1473.15", "1573.15", "1673.15", "1773.15"}
        for row in sample_observations
    )
    assert all("delta_fmq" in row.point_conditions for row in sample_observations)
    assert all(row.point_conditions["delta_fmq"].locator.page == 6 for row in sample_observations)
