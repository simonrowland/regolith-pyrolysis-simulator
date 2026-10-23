"""Regression pins for Van Ende et al. (2008) typed Table 4 values."""

from copy import deepcopy
import csv
from io import StringIO
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/van-ende-2008-mn-cr-slag-steel.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> list[dict]:
    return [
        observation
        for species in document["species"].values()
        for observation in species.get("observations", [])
    ]


def _table_rows(document: dict, table: str) -> list[list[str]]:
    contexts = document["species"]["SiO2"]["context"]
    record = next(row for row in contexts if row["locator"].get("table") == table)
    return list(csv.reader(StringIO(record["values"]["printed_rows_csv"])))


def _assert_table4_pins(document: dict) -> None:
    observations = {row["observation_id"]: row for row in _observations(document)}
    assert len(observations) == 189
    assert observations["van_ende_2008_1-1_slag_SiO2"]["values"]["component_amount"] == "26.0"
    assert observations["van_ende_2008_1-1_metal_Mn"]["values"]["component_amount"] == "7.8"
    oxygen = observations["van_ende_2008_1-2_metal_O"]
    assert oxygen["values"]["component_amount"] == "126.2"
    assert oxygen["values"]["basis"] == "element_ppm_by_mass"
    assert observations["van_ende_2008_1-1_LMn"]["values"]["distribution_ratio"] == "1.47"
    assert observations["van_ende_2008_1-1_LCr"]["values"]["distribution_ratio"] == "0.200"

    rows = _table_rows(document, "4")
    assert len(rows) == 20
    assert rows[1] == ["1-1", "", "", "—", "", "", "", "", "", "2.01", "", ""]
    assert rows[-1][0] == "6-2"
    assert rows[-1][9] == "1.84"
    assert sum(bool(row[9]) for row in rows[1:]) == 19
    typed_columns = (1, 2, 4, 5, 6, 7, 8, 10, 11)
    assert all(not row[column] for row in rows[1:] for column in typed_columns)
    assert sum(row[3] == "—" for row in rows[1:]) == 1


def test_van_ende_table4_migrates_typed_values_and_source_conditions() -> None:
    document = _document()
    contexts = document["species"]["SiO2"]["context"]
    equations = next(
        row
        for row in contexts
        if row["observation_id"] == "van_ende_2008_equations_2_9_standard_reaction_free_energies"
    )
    assert equations["locator"] == {"page": 2, "pdf_page_index": 1, "published_page": "1332"}
    assert equations["units"] == "(J/mol)"
    assert equations["values"]["reactions"] == [
        {
            "reaction_number": "1",
            "equation_number": "2",
            "reaction_as_printed": "Mn(l) + O_Fe = MnO(l)",
            "delta_G0_as_printed": "−229 589 + 63.74 T",
            "citations": "27–29",
        },
        {
            "reaction_number": "8",
            "equation_number": "9",
            "reaction_as_printed": "Cr(s) + 3/2 O_Fe = CrO1.5(s)",
            "delta_G0_as_printed": "−379 345 + 119.32 T",
            "citations": "34, 35",
        },
    ]
    assert "temperature" in equations["values"]["note"]
    assert len(_table_rows(document, "3")) == 20
    _assert_table4_pins(document)

    figures = [row for row in contexts if row["type"] == "figure"]
    assert len(figures) == 13
    for row in figures:
        assert row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1
        assert row["values"]["digitisation"] == "deferred to a figure pass"
        assert row["values"]["series"]
        assert "marker_count" in row["values"]["series"][0]

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    observations = [row for row in result.observations.values() if row.source_id == EXTRACT.stem]
    assert len(observations) == 189
    assert sum(row.identity.quantity.value.value == "equilibrium_phase_composition" for row in observations) == 151
    assert sum(row.identity.quantity.value.value == "distribution_ratio" for row in observations) == 38
    assert sum(
        row.identity.species.formula == "O"
        and row.identity.subtype.value == "element_ppm_by_mass"
        for row in observations
    ) == 18
    assert all(row.admission.status.value == "admitted" for row in observations)
    assert all(row.identity.temperature_K.tag.value == "value" for row in observations)
    assert all(row.point_conditions and "atmosphere" in row.point_conditions for row in observations)
    assert all(row.point_conditions["atmosphere"].state.value == "Ar" for row in observations)
    assert all(row.point_conditions["gas_flow_rate_cm3_min"].state.value == 200 for row in observations)
    assert all(row.identity.total_pressure_Pa.tag.value == "unknown" for row in observations)
    assert all(row.identity.fO2_Pa.tag.value == "unknown" for row in observations)
    assert all(row.locator and row.locator.page == 4 and row.locator.published_page == "1334" for row in observations)
    assert not [entry for entry in result.queue if entry.source.endswith(EXTRACT.name)]
    migrated_contexts = [
        context
        for rows in result.context_by_work.values()
        for context in rows
        if context["context_id"].startswith(f"{EXTRACT.stem}::context::")
    ]
    assert len(migrated_contexts) == len(contexts)
    assert not result.validation.hard_issues


def test_typed_table4_mutation_is_detected_by_independent_pdf_pins() -> None:
    mutated = deepcopy(_document())
    row = next(
        observation
        for observation in _observations(mutated)
        if observation["observation_id"] == "van_ende_2008_1-1_slag_SiO2"
    )
    row["values"]["component_amount"] = "26.1"
    with pytest.raises(AssertionError):
        _assert_table4_pins(mutated)
