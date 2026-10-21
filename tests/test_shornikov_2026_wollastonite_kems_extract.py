from __future__ import annotations

import copy
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Phase, Quantity, UncertaintyKind, ValueKind
from simulator.battery.migrate import Migrator, REPO_ROOT
from tests.literature_extract_test_helpers import (
    assert_no_unavailable_observation_values,
    iter_locators,
)


SID = "shornikov-2026-wollastonite-kems"
EXTRACT = REPO_ROOT / f"data/literature/extracts/{SID}.yaml"
CORPUS_ROOT = Path(
    os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
)
PDF = CORPUS_ROOT / "raw" / SID / f"{SID}.pdf"

if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

import validate_literature_extracts as validator  # noqa: E402


# Independent transcription of all Table 3 cells from the 600 dpi render of
# physical PDF page 11. None represents a printed dash; no blank is filled.
TABLE3_RENDER = (
    ("1751", "1.90e-10", None, "1.77e-6", "1.05e-9", "3.68e-9", "6.01e-12", "3.32e-11", None, "2.05e-11", "7.67e-10", "5.71e-10"),
    ("1783", "3.78e-10", None, "3.35e-6", "2.19e-9", "7.50e-9", "1.24e-11", "9.35e-11", "2.88e-12", "4.91e-11", "1.70e-9", "1.13e-9"),
    ("1800", "5.60e-10", None, "4.81e-6", "3.15e-9", "1.10e-8", "1.73e-11", "1.65e-10", "4.04e-12", "6.43e-11", "1.99e-9", "1.36e-9"),
    ("1815", "7.59e-10", None, "6.38e-6", "4.42e-9", "1.48e-8", "2.41e-11", "2.53e-10", "5.89e-12", "9.48e-11", "3.01e-9", "2.01e-9"),
    ("1833", "1.03e-9", None, "8.53e-6", "6.45e-9", "2.11e-8", "3.56e-11", "4.25e-10", "9.06e-12", "1.49e-10", "4.54e-9", "2.84e-9"),
    ("1859", "1.72e-9", None, "1.40e-5", "1.09e-8", "3.39e-8", "5.66e-11", "8.63e-10", "1.49e-11", "2.59e-10", "6.27e-9", "3.94e-9"),
    ("1885", "2.77e-9", None, "2.19e-5", "1.81e-8", "5.20e-8", "9.46e-11", "1.73e-9", "2.80e-11", "4.26e-10", "1.06e-8", "6.22e-9"),
    ("1910", "4.29e-9", "2.55e-12", "3.38e-5", "2.95e-8", "8.51e-8", "1.52e-10", "3.32e-9", "4.71e-11", "6.95e-10", "1.66e-8", "9.42e-9"),
    ("1933", "6.37e-9", "4.18e-12", "4.93e-5", "4.53e-8", "1.22e-7", "2.37e-10", "5.86e-9", "8.32e-11", "1.32e-9", "2.92e-8", "1.52e-8"),
    ("1970", "1.13e-8", "9.44e-12", "8.37e-5", "8.88e-8", "2.10e-7", "4.94e-10", "1.56e-8", "1.97e-10", "3.08e-9", "5.85e-8", "2.98e-8"),
    ("1988", "1.53e-8", "1.38e-11", "1.11e-4", "1.21e-7", "2.77e-7", "6.87e-10", "2.29e-8", "2.77e-10", "4.32e-9", "7.98e-8", "3.88e-8"),
    ("2025", "2.72e-8", "2.94e-11", "1.96e-4", "2.21e-7", "5.01e-7", "1.29e-9", "5.28e-8", "5.82e-10", "8.75e-9", "1.45e-7", "6.70e-8"),
    ("2066", "5.30e-8", "6.50e-11", "3.71e-4", "4.28e-7", "1.01e-6", "2.28e-9", "1.23e-7", "1.08e-9", "1.59e-8", "2.36e-7", "1.05e-7"),
    ("2087", "6.98e-8", "9.66e-11", "4.87e-4", "5.95e-7", "1.35e-6", "3.36e-9", "2.03e-7", "1.67e-9", "2.48e-8", "3.69e-7", "1.62e-7"),
)
TABLE3_SPECIES = ("Ca", "CaO", "SiO", "SiO2", "Si2O2", "O2", "CaSiO3", "Mo", "MoO", "MoO2", "MoO3")


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _table3_grid(document: dict) -> tuple[tuple[str | None, ...], ...]:
    rows: list[list[str | None]] = [
        [temperature, *([None] * len(TABLE3_SPECIES))]
        for temperature, *_ in TABLE3_RENDER
    ]
    for column, formula in enumerate(TABLE3_SPECIES, start=1):
        for observation in document["species"][formula]["observations"]:
            if not observation["observation_id"].startswith(
                f"shornikov_2026_{formula}_partial_pressure_table3_row_"
            ):
                continue
            row_index = int(observation["locator"]["row"]) - 1
            rows[row_index][0] = str(observation["values"]["T_K_as_printed"])
            rows[row_index][column] = str(observation["values"]["pressure_atm"])
    return tuple(tuple(row) for row in rows)


def _source_observations(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        observation["observation_id"]: (formula, observation)
        for formula, block in document["species"].items()
        for observation in block.get("observations", [])
    }


def test_table3_render_grid_and_both_cell_mutations_are_detected() -> None:
    document = _document()
    actual = _table3_grid(document)
    assert actual == TABLE3_RENDER
    assert len(actual) == 14
    assert sum(value is not None for row in actual for value in row[1:]) == 146

    changed_render = list(TABLE3_RENDER)
    first_row = list(changed_render[0])
    first_row[1] = "1.91e-10"
    changed_render[0] = tuple(first_row)
    with pytest.raises(AssertionError):
        assert actual == tuple(changed_render)

    changed_extract = copy.deepcopy(document)
    first_ca_row = next(
        observation
        for observation in changed_extract["species"]["Ca"]["observations"]
        if observation["observation_id"].endswith("_row_01")
    )
    first_ca_row["values"]["pressure_atm"] = "1.91e-10"
    with pytest.raises(AssertionError):
        assert _table3_grid(changed_extract) == TABLE3_RENDER


def test_isolated_migration_carries_each_species_and_pressure_condition() -> None:
    document = _document()
    source_rows = _source_observations(document)
    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    migrated = [row for row in result.observations.values() if row.source_id == SID]

    assert len(migrated) == 170
    assert_no_unavailable_observation_values(migrated)
    assert result.validation is not None and not result.validation.hard_issues
    assert not result.registry_issues

    for row in migrated:
        source_observation_id = row.observation_id.split("::", 2)[1]
        container, source = source_rows[source_observation_id]
        expected_formula = source["values"].get("formula", container)
        assert row.identity.species.formula == expected_formula

    pressure_rows = [
        row for row in migrated if "partial_pressure_table3" in row.observation_id
    ]
    assert len(pressure_rows) == 146
    for row in pressure_rows:
        assert row.identity.quantity.value is Quantity.P_PARTIAL
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value is Phase.G
        assert row.identity.temperature_K.is_value
        assert row.identity.composition.is_value
        assert row.point_conditions is not None
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["temperature_K"].locator.table == "3"
        assert row.point_conditions["oxygen_partial_pressure"].state.value.kind is ValueKind.POINT
        oxygen = row.point_conditions["fO2_Pa"]
        assert oxygen.state.is_value
        assert oxygen.state.value.kind is ValueKind.POINT
        assert oxygen.locator.table == "3"
        assert "column_header=pO2" in oxygen.locator.note
        assert row.point_conditions["composition"].state.is_value
        assert row.uncertainty.kind is UncertaintyKind.PRINTED
        assert row.uncertainty.verbatim["bound_value"] == "7%"

    activities = [row for row in migrated if row.identity.quantity.value is Quantity.ACTIVITY]
    assert len(activities) == 2
    activity_values = {row.identity.species.formula: row.value for row in activities}
    assert activity_values["CaO"].kind is ValueKind.POINT
    assert activity_values["CaO"].point == Decimal("0.015")
    assert activity_values["CaO"].approximate is True
    assert activity_values["SiO2"].kind is ValueKind.POINT
    assert activity_values["SiO2"].point == Decimal("0.20")
    assert activity_values["SiO2"].approximate is True
    expected_reference_phases = {"CaO": Phase.CR}
    for row in activities:
        formula = row.identity.species.formula
        assert row.identity.species.phase.is_unknown
        reference = row.identity.reference_state
        assert reference.is_value
        assert reference.value.convention.value == "raoultian_pure_endmember"
        assert reference.value.endmember.formula == formula
        if formula == "SiO2":
            assert reference.value.endmember.phase.is_unknown
            assert "cristobalite" in reference.value.endmember.phase.reason
        else:
            assert reference.value.endmember.phase.is_value
            assert reference.value.endmember.phase.value is expected_reference_phases[formula]
        assert row.identity.temperature_K.is_unknown
        assert row.point_conditions is not None
        activity_temperature = row.point_conditions["temperature_K"].state.value
        assert activity_temperature.kind is ValueKind.INTERVAL
        assert activity_temperature.interval_low == Decimal("1751")
        assert activity_temperature.interval_high == Decimal("2087")
        source_standard_state = next(
            source["standard_state"]
            for source_formula, block in document["species"].items()
            for source in block.get("observations", [])
            if source_formula == formula and source["values"].get("quantity") == "activity"
        )
        assert source_standard_state["inferred"] is True
        assert source_standard_state["basis"]
        if formula == "SiO2":
            assert source_standard_state["state"]["value"]["endmember"]["phase"]["tag"] == "unknown"
            assert "cristobalite" in source_standard_state["basis"].casefold()
            assert "1999 K" in source_standard_state["basis"]
            assert "crosses" in source_standard_state["basis"]

    source_activities = [
        observation
        for block in document["species"].values()
        for observation in block.get("observations", [])
        if observation["values"].get("quantity") == "activity"
    ]
    assert len(source_activities) == 2
    # The schema has no `activity` observation type; the `activity_coefficient` family type
    # carries the scored `values.quantity: activity` records.
    assert all(observation["type"] == "activity_coefficient" for observation in source_activities)
    assert all(observation["values"]["quantity"] == "activity" for observation in source_activities)


def test_every_deferred_figure_has_metadata_and_no_numeric_points() -> None:
    document = _document()
    figures = [
        record
        for block in document["species"].values()
        for record in block.get("context", [])
        if record.get("type") == "figure_record"
    ]
    assert len(figures) == 27
    for record in figures:
        values = record["values"]
        assert values["quantity"]
        assert values["species"]
        assert values["phase"]
        assert values["temperature_as_printed"]
        assert values["axes_as_printed"]
        assert values["caption_as_printed"]
        assert values["panel"]
        assert values["series"]
        assert values["attribution"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert "points" not in values
        assert all(isinstance(series["marker_count"], int) for series in values["series"])

    sio2_activity_figure = next(
        record for record in figures if record["observation_id"] == "shornikov_2026_fig3_SiO2_b"
    )
    figure_standard_state = sio2_activity_figure["values"]["standard_state"]
    assert figure_standard_state["state"]["value"]["endmember"]["phase"]["tag"] == "unknown"
    assert "cristobalite" in figure_standard_state["basis"].casefold()
    assert "1999 K" in figure_standard_state["basis"]
    assert "crosses" in figure_standard_state["basis"]

    for locator in iter_locators(document):
        if "pdf_page_index" in locator and "page" in locator:
            assert locator["page"] == locator["pdf_page_index"] + 1


@pytest.mark.skipif(not PDF.is_file(), reason="regolith-corpus source PDF is absent")
def test_locator_pages_match_the_source_pdf() -> None:
    document = _document()
    page_text: dict[int, str] = {}
    for locator in iter_locators(document):
        if "page" not in locator:
            continue
        page = int(locator["page"])
        assert int(locator["pdf_page_index"]) == page - 1
        if page not in page_text:
            result = subprocess.run(
                ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
                check=True,
                capture_output=True,
                text=True,
            )
            page_text[page] = result.stdout
        text = page_text[page]
        assert text.strip()
        if "table" in locator:
            assert f"Table {locator['table']}" in text
        if "figure" in locator:
            assert f"Fig. {locator['figure']}" in text


def test_per_file_validator_accepts_extract() -> None:
    document = _document()
    errors = validator.validate_extract_document(
        document,
        path=EXTRACT,
        expected_source_id=SID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert not errors
