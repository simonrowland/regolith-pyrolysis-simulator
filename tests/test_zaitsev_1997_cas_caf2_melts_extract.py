"""Source-grounded extraction and migration checks for Zaitsev et al. (1997)."""

from collections import Counter
from copy import deepcopy
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Quantity
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/zaitsev-1997-cas-caf2-melts.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observation(document: dict, observation_id: str) -> dict:
    return next(
        row
        for row in document["species"]["SiO2"]["observations"]
        if row["observation_id"] == observation_id
    )


def test_extract_validates_and_fidelity_mutation_is_detected() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []

    mutated = deepcopy(document)
    first_activity = _observation(
        mutated, "zaitsev_1997_table4_present_1798k"
    )["values"]["points"][0]
    first_activity["activity_as_printed"] = "4.62 × 10−5"
    assert check_all_fidelity_samples_match(mutated, label=EXTRACT.stem)


def test_table4_migrates_all_activities_with_located_temperature_and_unknown_composition() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    records = list(migrator.result.observations.values())
    assert len(records) == 42
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    assert all(
        record.identity.quantity.is_value
        and record.identity.quantity.value is Quantity.ACTIVITY
        for record in records
    )
    evidence = Counter(record.evidence.class_.value for record in records)
    assert evidence == {
        EvidenceClass.MEASURED_REDUCED: 21,
        EvidenceClass.QUOTED_ATTRIBUTED: 21,
    }

    for record in records:
        assert record.identity.temperature_K.is_value
        assert record.identity.temperature_K.value in {1798, 1873}
        assert record.point_conditions is not None
        temperature = record.point_conditions["temperature_K"]
        composition = record.point_conditions["composition"]
        assert temperature.state.is_value
        assert temperature.state.value == record.identity.temperature_K.value
        assert temperature.locator is not None
        assert temperature.locator.pdf_page_index == 6
        assert composition.state.is_unknown
        assert composition.locator is not None
        assert composition.locator.pdf_page_index == 6
        assert record.identity.composition is not None
        assert record.identity.composition.is_unknown


def test_model_tables_and_all_figures_remain_context_only() -> None:
    document = _document()
    context = document["species"]["SiO2"]["context"]
    figures = [item for item in context if item.get("type") == "figure_record"]
    assert len(figures) == 10
    for figure in figures:
        values = figure["values"]
        assert figure["panel"] == "whole_figure"
        assert figure["attribution"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert "points" not in values
        assert values.get("species")
        assert values.get("phase")
        assert values.get("temperature_as_printed")
        assert values.get("axes_as_printed")
        assert values.get("caption")
        assert values.get("series")
        assert all("marker_count" in series for series in values["series"])

    table5 = next(
        item
        for item in context
        if item["observation_id"] == "zaitsev_1997_table5_phase_transition_coordinates"
    )
    assert table5["values"]["t_1326"] is True
    assert len(table5["values"]["rows"]) == 11
    assert "CaO remains unknown" in table5["values"]["composition_note"]
    assert all(
        item["observation_id"] in {
            "zaitsev_1997_table1_associate_characteristics",
            "zaitsev_1997_table2_association_and_polymerization",
            "zaitsev_1997_table3_excess_gibbs_parameters",
            "zaitsev_1997_table5_phase_transition_coordinates",
        }
        for item in context
        if item.get("type") == "table_record"
    )
