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
    elemental = by_id["florin_2025_table_3_copper_residue_concentrations"]
    direct_points = direct["values"]["points"]
    reduced_points = reduced["values"]["points"]
    elemental_points = elemental["values"]["series"]
    assert len(direct_points) == len(reduced_points) == 24
    assert direct_points[0]["sample"] == "Cu12.1"
    assert direct_points[-1]["sample"] == "Cu15.6"
    assert baseline["values"]["points"][0]["isotope_delta_delta65CuERM633_permil"] == 0.16
    assert direct_points[0]["isotope_delta_delta65CuERM633_permil"] == 0.39
    assert [point["log_fO2"] for point in direct_points[:6]] == [
        -0.68, -6.91, -8.41, -10.41, -12.41, -14.41
    ]
    assert all("log_fO2" in point["point_conditions"] for point in direct_points)
    assert all("log_fO2" in point["point_conditions"] for point in reduced_points)
    assert reduced_points[0]["isotope_delta_Delta65CuSC_permil"] == 0.23
    assert all(
        round(source["isotope_delta_delta65CuERM633_permil"] - 0.16, 2)
        == result["isotope_delta_Delta65CuSC_permil"]
        for source, result in zip(direct_points, reduced_points, strict=True)
    )
    assert all(point["two_sigma_permil"] > 0 for point in direct_points + reduced_points)
    assert len(elemental_points) == 50
    assert elemental_points[0]["run"] == "1125a"
    assert elemental_points[0]["residue_ppm"] == 20653
    assert elemental_points[0]["point_conditions"]["temperature_C"]["locator"]["published_page"] == 135
    assert elemental_points[-1]["run"] == "1484eII"
    assert elemental_points[-1]["residue_ppm"] == 369
    transcript = by_id["florin_2025_table_3_elemental_runs_not_fully_transcribed"]["values"]["table3_as_printed"]
    assert "20,653" in transcript and "b.d.l." in transcript and "1484" in transcript

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    migrated = list(migrator.result.observations.values())
    assert len(migrated) == 99
    sample_observations = [row for row in migrated if row.identity.temperature_K.value is not None]
    assert len(sample_observations) == 98
    assert all(
        str(row.identity.temperature_K.value)
        in {"1398.15", "1402.15", "1458.15", "1487.15", "1523.15", "1573.15",
            "1601.15", "1658.15", "1673.15", "1698.15", "1723.15", "1757.15",
            "1773.15", "1779.15", "1807.15", "1815.15"}
        for row in sample_observations
    )
    assert all("delta_fmq" in row.point_conditions for row in sample_observations)
    assert all("log_fO2" in row.point_conditions for row in sample_observations)
    migrated_residues = [row for row in migrated if row.identity.quantity.value.value == "residue_component_composition"]
    assert len(migrated_residues) == 50
    assert float(migrated_residues[0].value.point) == 20653
    assert float(migrated_residues[-1].value.point) == 369
