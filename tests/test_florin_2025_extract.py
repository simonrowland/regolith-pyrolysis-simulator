"""Pins the Table 3 copper isotope series and its sample-wise reduction."""

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import UncertaintyKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/florin-2025-cu-silicate-evaporation.yaml"
TABLE_3_TWO_SIGMA_PERMIL = {
    # Transcribed independently from the ±2 s.d. column on published p. 136 (PDF p. 6).
    "Cu12.1": "0.08",
    "Cu12.2": "0.09",
    "Cu12.3": "0.04",
    "Cu12.4": "0.13",
    "Cu12.5": "0.06",
    "Cu12.6": "0.08",
    "Cu13.1": "0.06",
    "Cu13.2": "0.06",
    "Cu13.3": "0.04",
    "Cu13.4": "0.12",
    "Cu13.5": "0.05",
    "Cu13.6": "0.05",
    "Cu14.1": "0.04",
    "Cu14.2": "0.07",
    "Cu14.3": "0.07",
    "Cu14.4": "0.09",
    "Cu14.5": "0.02",
    "Cu14.6": "0.04",
    "Cu15.1": "0.02",
    "Cu15.2": "0.15",
    "Cu15.3": "0.12",
    "Cu15.4": "0.09",
    "Cu15.5": "0.08",
    "Cu15.6": "0.20",
}


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
    assert all(
        Decimal(str(point["sigma"]))
        == Decimal(TABLE_3_TWO_SIGMA_PERMIL[point["sample"]])
        and Decimal(str(point["two_sigma_permil"]))
        == Decimal(TABLE_3_TWO_SIGMA_PERMIL[point["sample"]])
        for point in direct_points + reduced_points
    )
    assert direct["uncertainty"]["confidence_level"] == "two_sigma"
    assert reduced["uncertainty"]["confidence_level"] == "two_sigma"
    assert direct_points[0]["log_fO2"] == -0.68
    elemental = by_id["florin_2025_table_3_elemental_residue_rows"]["values"]["series"]
    assert len(elemental) == 50
    assert elemental[0]["residue_ppm"] == "20653"
    assert elemental[0]["f_Cu_as_printed"] == "0.95"
    assert elemental[0]["S_ug_g_as_printed"] == "—"
    assert elemental[37]["S_ug_g_as_printed"] == "b.d.l."

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    migrated = list(migrator.result.observations.values())
    assert len(migrated) == 99
    sample_observations = [
        row for row in migrated
        if row.observation_id.split("::")[1] in {
            "florin_2025_table_3_cu_isotope_delta_erm633",
            "florin_2025_table_3_cu_isotope_delta_sc",
        }
    ]
    assert len(sample_observations) == 48
    assert {row.identity.species.formula for row in sample_observations} == {"Cu"}
    assert all(
        str(row.identity.temperature_K.value)
        in {"1473.15", "1573.15", "1673.15", "1773.15"}
        for row in sample_observations
    )
    assert all("delta_fmq" in row.point_conditions for row in sample_observations)
    assert all(row.point_conditions["delta_fmq"].locator.page == 6 for row in sample_observations)
    assert all("log_fO2" in row.point_conditions for row in sample_observations)
    assert all(row.point_conditions["log_fO2"].locator.page == 6 for row in sample_observations)
    assert all(
        row.uncertainty.kind is UncertaintyKind.PRINTED
        for row in sample_observations
    )
    assert all(
        row.uncertainty.verbatim["confidence_level"] == "two_sigma"
        for row in sample_observations
    )
    for sample, expected_width in TABLE_3_TWO_SIGMA_PERMIL.items():
        for parent_id in (
            "florin_2025_table_3_cu_isotope_delta_erm633",
            "florin_2025_table_3_cu_isotope_delta_sc",
        ):
            migrated_point = next(
                row for row in sample_observations
                if f"{parent_id}::" in row.observation_id
                and f"row={sample.lower()}:" in row.observation_id
            )
            assert (
                Decimal(str(migrated_point.uncertainty.verbatim["sigma"]))
                == Decimal(expected_width)
            )
    assert "value_as_published" not in direct_points[0]
    residue_points = [row for row in migrated if row.observation_id.split("::")[1] == "florin_2025_table_3_elemental_residue_rows"]
    assert len(residue_points) == 50
    assert all(row.identity.temperature_K.is_value for row in residue_points)
    assert residue_points[0].value.point == 20653
