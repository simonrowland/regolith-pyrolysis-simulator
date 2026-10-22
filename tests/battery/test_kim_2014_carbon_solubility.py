from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    EvidenceClass,
    Phase,
    PhaseRole,
    Quantity,
    ValueKind,
)
from tests.battery.test_migrate import _migrate_real_extract
from tests.battery.test_phase_composition_rows import _condition_amount
from tests.helpers.corpus_paths import regolith_corpus_root


SOURCE_ID = "kim-2014-carbon-fe-v-mo-ni"
EXTRACT_NAME = f"{SOURCE_ID}.yaml"
FIXTURE = Path(__file__).parents[1] / "fixtures" / "literature" / f"{SOURCE_ID}.csv"
EXTRACT_PATH = Path(__file__).parents[2] / "data" / "literature" / "extracts" / EXTRACT_NAME


def _fixture_rows() -> list[dict[str, str]]:
    with FIXTURE.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_kim_2014_printed_table_rows_migrate_and_bind_to_samples(tmp_path: Path) -> None:
    fixture_rows = _fixture_rows()
    assert len(fixture_rows) == 66
    source = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    observations = source["species"]["SiO2"]["observations"]
    parameters = source["species"]["C"]["observations"]
    assert (len(observations), len(parameters)) == (230, 3)

    experiments = {item["sample"]["sample_id"]: item for item in source["experiments"]}
    source_rows: dict[str, list[dict]] = {}
    for observation in observations:
        sample_id = observation["values"]["sample_id"]
        source_rows.setdefault(sample_id, []).append(observation)

    # This fixture was transcribed independently from the 600 dpi page 3
    # render. The first V and C cells below pin the printed row spellings.
    assert next(row for row in fixture_rows if row["sample_id"] == "t1-r02")["V"] == "2.59"
    assert next(row for row in fixture_rows if row["sample_id"] == "t1-r14")["C"] == "9.19"
    assert next(row for row in fixture_rows if row["sample_id"] == "t2-r17")["C"] == "8.00"

    expected_ids: set[str] = set()
    for row in fixture_rows:
        sample_id = row["sample_id"]
        expected_components = ("V", "Mo", "Ni", "C") if row["table"] == "1" else ("V", "C")
        expected_values = {component: row[component] for component in expected_components}
        actual_rows = source_rows[sample_id]
        actual_values = {
            item["values"]["formula"]: item["values"]["component_amount"]
            for item in actual_rows
        }
        assert actual_values == expected_values
        assert {item["values"]["system_as_printed"] for item in actual_rows} == {row["system"]}
        assert len({item["experiment"] for item in actual_rows}) == 1
        sample = experiments[sample_id]["sample"]
        assert sample["component_values_as_printed"] == expected_values
        assert sample["system_as_printed"] == row["system"]
        assert sample["sample_id_note"].startswith("Local table-row key")
        assert sample["unreported_component"] == "Fe balance is not reported and was not calculated."
        for item in actual_rows:
            assert item["values"]["T_C"] == str(float(row["T_K"]) - 273.15)
            assert item["values"]["sample_id"] == sample_id
            assert item["values"]["basis"] == "wt_percent"
            assert item["values"]["phase_label"] == "metal"
            assert item["values"]["component_amount_as_published"] == item["values"]["component_amount"]
            expected_ids.add(item["observation_id"])

    result = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    assert result.queue == []
    migrated_by_id = {item.observation_id.split("::")[-1]: item for item in result.observations.values()}
    migrated_components = [
        item for item in result.observations.values()
        if item.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    ]
    migrated_parameters = [
        item for item in result.observations.values()
        if item.identity.quantity.value is Quantity.INTERACTION_PARAMETER
    ]
    assert (len(migrated_components), len(migrated_parameters)) == (230, 3)
    assert expected_ids <= set(migrated_by_id)
    for row in fixture_rows:
        sample_id = row["sample_id"]
        for item in source_rows[sample_id]:
            migrated = migrated_by_id[item["observation_id"]]
            formula = item["values"]["formula"]
            assert migrated.value.kind is ValueKind.POINT
            assert migrated.value.point == Decimal(row[formula])
            assert migrated.identity.species.formula == formula
            assert migrated.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
            assert migrated.identity.species.phase.value is Phase.L
            assert migrated.identity.phase_role.value is PhaseRole.METAL
            assert migrated.identity.subtype.value == "wt_percent"
            assert migrated.identity.temperature_K.value == Decimal(row["T_K"])
            assert _condition_amount(migrated, "temperature_K") == Decimal(row["T_K"])
            assert _condition_amount(migrated, "gas_mix") == "Ar"
            assert migrated.identity.fO2_Pa.is_unknown
            assert migrated.notices == ()

    expected_parameters = {
        "kim2014_table3_epsilon_C^V": (Decimal("-1.94"), "0.018"),
        "kim2014_table3_epsilon_C^Mo": (Decimal("-1.17"), "0.020"),
        "kim2014_table3_epsilon_C^Ni": (Decimal("0.91"), "0.025"),
    }
    for raw_id, (expected, sigma) in expected_parameters.items():
        migrated = migrated_by_id[raw_id]
        assert migrated.value.kind is ValueKind.POINT
        assert migrated.value.point == expected
        assert migrated.identity.species.formula == "C"
        assert migrated.identity.quantity.value is Quantity.INTERACTION_PARAMETER
        assert migrated.identity.temperature_K.value == Decimal("1873")
        assert migrated.evidence.class_.value is EvidenceClass.MODEL_DERIVED
        assert migrated.uncertainty.verbatim["sigma"] == sigma
        assert migrated.uncertainty.verbatim["unit"] == "dimensionless"
        assert migrated.notices == ()
    assert [item["values"]["digitisation"] for item in source["species"]["SiO2"]["context"] if item["type"] == "figure_only"] == ["deferred to a figure pass"] * 11


def test_kim_2014_locators_match_physical_pdf_pages_or_skip_without_corpus() -> None:
    text_path = regolith_corpus_root() / "text" / SOURCE_ID / "pdftotext-layout.txt"
    if not text_path.is_file():
        pytest.skip(f"corpus source text missing: {text_path}")
    pages = text_path.read_text(encoding="utf-8").split("\f")
    assert "Ar atmosphere" in pages[0]
    assert "Table 1" in pages[2] and "Table 2" in pages[2]
    assert "Table 3" in pages[5]
