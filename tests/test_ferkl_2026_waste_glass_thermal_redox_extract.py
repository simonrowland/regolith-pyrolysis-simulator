"""Render-grid and migration checks for Ferkl et al. (2026)."""

from __future__ import annotations

import csv
import hashlib
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
SID = "ferkl-2026-waste-glass-thermal-redox"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
TABLES = ROOT / f"data/literature/tables/{SID}"
FIXTURES = ROOT / "tests/fixtures/literature_extracts"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _context_record(document: dict, observation_id: str) -> dict:
    for body in document["species"].values():
        for record in body.get("context", ()):
            if record.get("observation_id") == observation_id:
                return record
    raise AssertionError(f"missing context record {observation_id}")


def _assert_table1_matches(document: dict, grid: list[dict[str, str]]) -> None:
    expected = _context_record(document, "ferkl_2026_table1_literature_redox_model_parameters")["values"]["rows"]
    assert grid == expected


def _assert_table2_matches(document: dict, grid: list[dict[str, str]]) -> None:
    rows = _context_record(document, "ferkl_2026_table2_target_glass_compositions")["values"]["rows"]
    expected = [
        {
            "component": row["component_as_printed"],
            "LAWC100": row["values_mass_percent"]["LAWC100"],
            "LAWA44": row["values_mass_percent"]["LAWA44"],
            "APPS-09": row["values_mass_percent"]["APPS-09"],
            "Al-19_MF": row["values_mass_percent"]["Al-19_MF"],
        }
        for row in rows
    ]
    assert grid == expected


def _assert_table3_matches(document: dict, grid: list[dict[str, str]]) -> None:
    typed = {}
    for body in document["species"].values():
        records = [*body.get("observations", ()), *body.get("context", ())]
        for observation in records:
            if observation.get("values", {}).get("quantity") != "valence_ratio":
                continue
            column = observation["values"]["printed_ratio_as"]
            method = "rapidox" if "rapidox" in observation["observation_id"] else "xanes"
            for point in observation["values"]["points"]:
                sample = point["sample_as_published"]
                typed[(sample, column, method)] = point

    columns = {
        "rapidox_Fe2_Fetot": ("Fe2+/Fetot", "rapidox"),
        "xanes_Fe2_Fetot": ("Fe2+/Fetot", "xanes"),
        "rapidox_V4_Vtot": ("V4+/Vtot", "rapidox"),
        "rapidox_Cr3_Crtot": ("Cr3+/Crtot", "rapidox"),
        "xanes_Cr3_Crtot": ("Cr3+/Crtot", "xanes"),
    }
    assert len(typed) == 28
    rapidox_context = [
        (formula, record)
        for formula, body in document["species"].items()
        for record in body.get("context", ())
        if "rapidox" in record.get("observation_id", "")
    ]
    assert len(rapidox_context) == 3
    assert sum(len(record["values"]["points"]) for _, record in rapidox_context) == 16
    for _, record in rapidox_context:
        values = record["values"]
        assert record["type"] == "model_derived_ratio_series"
        assert values["method_class"] == "model_derived"
        assert "admission_status" not in values
        assert record["derived_from"] == [
            f"tables:{SID}:t1",
            f"tables:{SID}:t3",
        ]
        assert (
            "Equation (1), ln(w_red/w_ox) = α − (σ/4) ln(pO2) − ΔH/(RT)"
            in record["derivation"]["relation"]
        )
        assert "Rapidox pO2" in record["derivation"]["relation"]
    estimated_po2 = _context_record(
        document, "ferkl_2026_table3_xanes_derived_pO2_comparison_values"
    )["values"]["rows"]
    assert estimated_po2 == [
        {"sample_as_printed": row["sample"], "pO2_as_printed": row["xanes_pO2_Pa"]}
        for row in grid
    ]
    target_composition_sample = {
        "LAWC100_R": "LAWC100",
        "LAWC100": "LAWC100",
        "LAWA44": "LAWA44",
        "APPS-09": "APPS-09",
        "APPS-09_MF": "APPS-09",
        "Al-19_MF": "Al-19_MF",
    }
    for row in grid:
        sample = row["sample"]
        for column, (printed_ratio, method) in columns.items():
            expected = row[column]
            key = (sample, printed_ratio, method)
            if expected == "—":
                assert key not in typed
                continue
            point = typed[key]
            assert point["value_as_published"] == expected
            assert point["row_as_printed"] == sample
            assert point["sample_as_published"] == sample
            condition = point["point_conditions"]
            assert condition["sample_id"]["state"]["value"] == sample
            assert condition["sample_id"]["locator"]["row"] == sample
            assert (
                f"Rapidox result printed as {row['rapidox_pO2_Pa']} Pa;"
                in condition["oxygen_partial_pressure"]["note"]
            )
            composition_sample = target_composition_sample[sample]
            assert point["composition_as_published"]["sample_column"] == composition_sample
            assert condition["composition"]["locator"]["row"] == composition_sample
            assert condition["oxygen_partial_pressure"]["units"] == "Pa"
            assert condition["oxygen_partial_pressure"]["state"]["tag"] == "value"
            assert condition["oxygen_partial_pressure"]["locator"]["row"] == sample
            if method == "rapidox":
                assert point["T_C"] == "1150"
                assert condition["temperature_K"]["state"]["value"] == "1423.15"
            else:
                assert condition["temperature_K"]["state"]["tag"] == "unknown"
                assert point["quench_statement"]["preservation"] == "not_stated"


def test_render_transcriptions_pin_context_and_typed_rows_and_mutations_fail() -> None:
    document = _document()
    table1 = _csv_rows(FIXTURES / f"{SID}-table1-render-grid.csv")
    table2 = _csv_rows(FIXTURES / f"{SID}-table2-render-grid.csv")
    table3 = _csv_rows(FIXTURES / f"{SID}-table3-render-grid.csv")

    assert (len(table1), len(table2), len(table3)) == (14, 23, 6)
    assert _csv_rows(TABLES / "t1.csv") == table1
    assert _csv_rows(TABLES / "t2.csv") == table2
    assert _csv_rows(TABLES / "t3.csv") == table3
    for asset in document["source_files"]["files"]:
        declared_path = ROOT / asset["path"]
        assert declared_path.is_file()
        assert hashlib.sha256(declared_path.read_bytes()).hexdigest() == asset["sha256"]["value"]
    _assert_table1_matches(document, table1)
    _assert_table2_matches(document, table2)
    _assert_table3_matches(document, table3)
    assert check_all_fidelity_samples_match(document, label=SID) == []

    changed_grid = deepcopy(table3)
    changed_grid[0]["rapidox_Fe2_Fetot"] = "0.8"
    with pytest.raises(AssertionError):
        _assert_table3_matches(document, changed_grid)

    changed_document = deepcopy(document)
    rapidox_fe = next(
        observation
        for observation in changed_document["species"]["Fe"]["context"]
        if observation["observation_id"] == "ferkl_2026_table3_rapidox_fe2_fraction"
    )
    rapidox_fe["values"]["points"][0]["value_as_published"] = "0.8"
    with pytest.raises(AssertionError):
        _assert_table3_matches(changed_document, table3)


def test_isolated_migration_preserves_species_samples_conditions_and_quench() -> None:
    document = _document()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 12

    source_by_id = {
        observation["observation_id"]: (formula, observation)
        for formula, body in document["species"].items()
        for observation in body.get("observations", ())
    }
    assert len(source_by_id) == 2
    migrated_by_id = {
        source_id: [
            observation
            for observation in result.observations.values()
            if f"::{source_id}" in observation.observation_id
        ]
        for source_id in source_by_id
    }

    for source_id, (formula, source) in source_by_id.items():
        assert len(migrated_by_id[source_id]) == len(source["values"]["points"])
        expected_samples = {
            point["sample_as_published"] for point in source["values"]["points"]
        }
        for migrated in migrated_by_id[source_id]:
            assert migrated.identity.species.formula == formula
            assert migrated.admission.status.value == "admitted"
            assert migrated.identity.quantity.value.value == "valence_ratio"
            assert migrated.evidence.class_.value == "measured_reduced"
            assert migrated.identity.species.phase.value.value == "glass"
            conditions = migrated.point_conditions or {}
            assert conditions["sample_id"].state.is_value
            assert conditions["sample_id"].state.value in expected_samples
            assert conditions["sample_id"].locator is not None
            assert conditions["temperature_K"].locator is not None
            assert conditions["composition"].state.is_unknown
            assert migrated.identity.fO2_Pa is not None
            assert migrated.identity.fO2_Pa.is_value
            assert conditions["oxygen_partial_pressure"].state.is_value
            assert conditions["oxygen_partial_pressure"].locator is not None
            assert migrated.identity.fO2_Pa.value == conditions["oxygen_partial_pressure"].state.value
            assert migrated.identity.temperature_K.is_unknown
            assert conditions["temperature_K"].state.is_unknown

    xanes_points = [
        point
        for _, source in source_by_id.values()
        if "xanes" in source["observation_id"]
        for point in source["values"]["points"]
    ]
    assert len(xanes_points) == 12
    assert all(point["quench_statement"]["quench_direction"] == "not_stated" for point in xanes_points)
    assert all(
        source["phase"] == "glass" and source["regime"] == "quenched_glass_analysis"
        for source_id, (_, source) in source_by_id.items()
        if "xanes" in source_id
    )
    assert all(
        observation.uncertainty.value is None or observation.uncertainty.value > 0
        for observations in migrated_by_id.values()
        for observation in observations
    )
