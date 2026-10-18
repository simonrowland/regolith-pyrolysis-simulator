"""Rendered source pins and isolated migration for the RJPC perovskite paper."""

from __future__ import annotations

import csv
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2019-perovskite-evaporation-rjpc.yaml"
TMS_EXTRACT = ROOT / "data/literature/extracts/shornikov-2019-perovskite-evaporation.yaml"
COMPARE_AUDIT = ROOT / (
    "data/literature/extracts/audits/"
    "shornikov-2019-perovskite-evaporation-rjpc.mineru-compare.csv"
)

if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import validate_literature_extracts as vle  # noqa: E402


TABLE3_TEMPERATURES = [
    1791, 1845, 1875, 1920, 1941, 1956, 1976,
    1992, 2027, 2037, 2051, 2100, 2182,
]
TABLE3_IDS = {
    "shornikov_2019_rjpc_table3_eq22_23_atomic_oxygen_pressure",
    "shornikov_2019_rjpc_table3_eq24_atomic_oxygen_pressure",
    "shornikov_2019_rjpc_table3_eq25_atomic_oxygen_pressure",
    "shornikov_2019_rjpc_table3_eq26_atomic_oxygen_pressure",
    "shornikov_2019_rjpc_table3_eq27_atomic_oxygen_pressure",
    "shornikov_2019_rjpc_table3_eq28_atomic_oxygen_pressure",
}
HK_PRINTED = [
    "1.26 × 10−10", "3.52 × 10−10", "6.10 × 10−10",
    "1.36 × 10−9", "1.97 × 10−9", "2.52 × 10−9",
    "3.52 × 10−9", "4.57 × 10−9", "7.76 × 10−9",
    "9.31 × 10−9", "1.13 × 10−8", "2.39 × 10−8",
    "7.73 × 10−8",
]
HK_NUMERIC = [
    "1.26e-10", "3.52e-10", "6.10e-10",
    "1.36e-9", "1.97e-9", "2.52e-9",
    "3.52e-9", "4.57e-9", "7.76e-9",
    "9.31e-9", "1.13e-8", "2.39e-8",
    "7.73e-8",
]


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _observations(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        observation["observation_id"]: (species, observation)
        for species, block in document["species"].items()
        for observation in block.get("observations", [])
    }


def test_table3_series_are_render_pinned_and_migrate_without_hard_issues():
    document = _load_yaml(EXTRACT)
    observations = _observations(document)
    assert set(observations) == TABLE3_IDS
    assert all(species == "O" for species, _ in observations.values())

    hk_id = "shornikov_2019_rjpc_table3_eq22_23_atomic_oxygen_pressure"
    hk = observations[hk_id][1]
    assert hk["type"] == "partial_pressure"
    assert hk["phase"] == "gas"
    assert hk["units"] == "atm"
    assert hk["values"]["method_class"] == "measured_tabulated"
    assert hk["values"]["evidence_class"] == "measured_tabulated"
    assert [point["T_K"] for point in hk["values"]["points"]] == TABLE3_TEMPERATURES
    assert [point["pressure_atm_as_printed"] for point in hk["values"]["points"]] == HK_PRINTED
    assert [
        Decimal(str(point["pressure_atm"]))
        for point in hk["values"]["points"]
    ] == [Decimal(value) for value in HK_NUMERIC]
    assert all(
        point["locator"]["table"] == "3"
        and point["locator"]["published_page"] == 1029
        for point in hk["values"]["points"]
    )

    quoted_counts = {
        "shornikov_2019_rjpc_table3_eq24_atomic_oxygen_pressure": 10,
        "shornikov_2019_rjpc_table3_eq25_atomic_oxygen_pressure": 11,
        "shornikov_2019_rjpc_table3_eq26_atomic_oxygen_pressure": 13,
        "shornikov_2019_rjpc_table3_eq27_atomic_oxygen_pressure": 8,
        "shornikov_2019_rjpc_table3_eq28_atomic_oxygen_pressure": 13,
    }
    assert sum(quoted_counts.values()) == 55
    for observation_id, count in quoted_counts.items():
        observation = observations[observation_id][1]
        assert observation["type"] == "partial_pressure"
        assert observation["values"]["method_class"] == "quoted_attributed"
        assert observation["values"]["evidence_class"] == "quoted_attributed"
        assert len(observation["values"]["points"]) == count
        assert observation["values"]["attribution"].startswith(
            "calculated in the present study from the thermochemical data of [9]"
        )
        assert all(point["pressure_atm_as_printed"].count("−") == 1
                   for point in observation["values"]["points"])

    assert not any(
        row.get("observation_id") == "shornikov_2019_rjpc_table3_atomic_oxygen_calculations"
        for row in document["species"]["O"]["context"]
    )
    assert vle.check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []

    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    migrated = [
        observation
        for observation in result.observations.values()
        if observation.source_id == document["source_id"]
    ]
    migrated_series = Counter(
        row.observation_id.split("::", 2)[1] for row in migrated
    )
    assert migrated_series == Counter({
        "shornikov_2019_rjpc_table3_eq22_23_atomic_oxygen_pressure": 13,
        "shornikov_2019_rjpc_table3_eq24_atomic_oxygen_pressure": 10,
        "shornikov_2019_rjpc_table3_eq25_atomic_oxygen_pressure": 11,
        "shornikov_2019_rjpc_table3_eq26_atomic_oxygen_pressure": 13,
        "shornikov_2019_rjpc_table3_eq27_atomic_oxygen_pressure": 8,
        "shornikov_2019_rjpc_table3_eq28_atomic_oxygen_pressure": 13,
    })
    assert len(migrated) == 68
    assert all(row.identity.temperature_K.is_value for row in migrated)
    assert {
        int(row.identity.temperature_K.value) for row in migrated
    } == set(TABLE3_TEMPERATURES)
    assert not result.registry_issues
    assert not result.validation.hard_issues


def test_table2_cross_references_match_the_existing_tms_points():
    document = _load_yaml(EXTRACT)
    tms = _load_yaml(TMS_EXTRACT)
    cross_references = {
        row["values"]["existing_observation_id"]: species
        for species, block in document["species"].items()
        for row in block["context"]
        if row["type"] == "cross_reference"
        and row["values"].get("quantity") == "existing_partial_pressure_series"
    }
    tms_observations = _observations(tms)
    audit_rows = list(csv.DictReader(COMPARE_AUDIT.open(encoding="utf-8", newline="")))
    shared_rows = [
        row for row in audit_rows
        if row["comparison_type"] == "table2_cross_reference"
    ]
    assert len(shared_rows) == 76
    actual_counts = Counter(row["existing_observation_id"] for row in shared_rows)
    expected_counts = {
        observation_id: len(tms_observations[observation_id][1]["values"]["points"])
        for observation_id in cross_references
    }
    assert actual_counts == Counter(expected_counts)
    assert len(cross_references) == 7

    for row in shared_rows:
        observation_id = row["existing_observation_id"]
        assert observation_id in tms_observations
        species, observation = tms_observations[observation_id]
        assert row["species"] == species == cross_references[observation_id]
        temperature = int(row["T_K"])
        point = next(
            point for point in observation["values"]["points"]
            if int(point["T_K"]) == temperature
        )
        assert Decimal(row["value"]) == Decimal(str(point["pressure_atm"]))


def test_table3_compare_audit_cites_the_current_observations():
    rows = list(csv.DictReader(COMPARE_AUDIT.open(encoding="utf-8", newline="")))
    table3_rows = [
        row for row in rows
        if row["comparison_type"] == "table3_observation"
    ]
    assert len(table3_rows) == 68
    assert all(row["status"] == "matched" for row in table3_rows)
    assert all(
        row["observation_id"] in TABLE3_IDS
        and f"observations[{row['observation_id']}]" in row["path"]
        for row in table3_rows
    )


def test_apparatus_cross_reference_uses_tms_bench_and_experiment_ids():
    document = _load_yaml(EXTRACT)
    narrative = next(
        row
        for block in document["species"].values()
        for row in block["context"]
        if row["type"] == "cross_reference"
        and row["values"].get("quantity") == "existing_narrative_context"
    )
    references = narrative["values"]["references"]
    bench = next(row for row in references if "existing_bench_id" in row)
    preparation = next(
        row for row in references if "existing_experiment_id" in row
    )
    assert bench["existing_bench_id"] == "shornikov_2019_mi1201_mo"
    assert "existing_observation_id" not in bench
    assert preparation["existing_experiment_id"] == "shornikov_2019_mo_cell_kems"
    assert preparation["matching_items"] == ["sample preparation schedule"]


def test_table4_fits_remain_typed_refusals():
    document = _load_yaml(EXTRACT)
    refusals = [
        row
        for block in document["species"].values()
        for row in block["context"]
        if row["type"] == "fit_refusal"
    ]
    assert len(refusals) == 8
    assert all(
        row["values"]["reason"] == "schema_gap_function_valued_fit"
        for row in refusals
    )
    assert all(
        not block["observations"]
        for species, block in document["species"].items()
        if species != "O"
    )
