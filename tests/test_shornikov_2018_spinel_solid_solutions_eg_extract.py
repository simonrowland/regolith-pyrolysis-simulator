from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]

EXTRACT = (
    ROOT / "data/literature/extracts/shornikov-2018-spinel-solid-solutions-eg.yaml"
)


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _contexts(document):
    return document["species"]["MgAl2O4"]["context"]


def _context(document, observation_id):
    return next(
        context
        for context in _contexts(document)
        if context["observation_id"] == observation_id
    )


def test_unique_thermodynamic_values_are_pinned_to_printed_page_121():
    document = _document()
    expected = {
        "shornikov_2018_eg_spinel_formation_from_oxides_context": {
            "enthalpy_as_printed": "–12.02±1.14 kJ/mole",
            "entropy_as_printed": "5.03±0.56 J/(mole·K)",
        },
        "shornikov_2018_eg_spinel_melting_enthalpy_context": {
            "enthalpy_as_printed": "55.81±4.62 kJ/mole",
        },
    }

    for observation_id, values in expected.items():
        context = _context(document, observation_id)
        assert context["locator"]["published_page"] == 121
        assert context["locator"]["pdf_page_index"] == 1
        for field, printed_value in values.items():
            assert context["values"][field] == printed_value

    formation = _context(
        document, "shornikov_2018_eg_spinel_formation_from_oxides_context"
    )["values"]
    assert formation["reaction_as_printed"] == "spinel formation (from simple oxides)"
    assert formation["admission_status"] == "context_only"
    assert formation["quantity"] == "spinel_formation_from_simple_oxides_context"
    melting = _context(
        document, "shornikov_2018_eg_spinel_melting_enthalpy_context"
    )["values"]
    assert "property_kind" not in melting


def test_shared_boundary_and_table_findings_are_cross_referenced_not_repeated():
    document = _document()
    shared = _context(
        document, "shornikov_2018_eg_shared_findings_rjpc_cross_reference"
    )["values"]
    existing = yaml.safe_load(
        (
            ROOT
            / "data/literature/extracts/shornikov-2018-spinel-solid-solutions.yaml"
        ).read_text(encoding="utf-8")
    )
    existing_ids = {
        row["observation_id"]
        for species in existing["species"].values()
        for collection in (species.get("observations", []), species.get("context", []))
        for row in collection
    }

    assert shared["related_source_id"] == "shornikov-2018-spinel-solid-solutions"
    assert set(shared["referenced_observation_ids"]) <= existing_ids
    assert "shornikov_2018_phase_boundary_points" in shared[
        "referenced_observation_ids"
    ]
    assert "shornikov_2018_discussion_qualifications" in shared[
        "referenced_observation_ids"
    ]
    assert shared["discrepancy"] == (
        "none found in the shared boundary values or findings checked"
    )
    assert document["species"]["MgAl2O4"].get("observations", []) == []


def test_all_figures_are_metadata_only_and_keep_axes_and_marker_counts():
    document = _document()
    figures = [
        context
        for context in _contexts(document)
        if context["observation_id"].startswith("shornikov_2018_eg_fig")
    ]
    assert len(figures) == 4

    for figure in figures:
        values = figure["values"]
        assert values["digitised"] is False
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["identity"]["species_as_printed"]
        assert values["identity"]["phase_and_temperature_as_printed"]
        assert values["axes_as_printed"]
        assert "points" not in values
        for panel in values["panels"]:
            for series in panel["species"]:
                assert isinstance(series["marker_count"], int)

    pressure = _context(document, "shornikov_2018_eg_fig2_partial_pressures")
    assert sum(
        series["marker_count"]
        for series in pressure["values"]["panels"][0]["species"]
    ) == 96


def test_context_only_overlap_passes_validation_and_has_no_scored_rows():
    document = _document()
    assert validate_extract_file(EXTRACT) == []

    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = [
        row
        for row in migrator.result.observations.values()
        if row.source_id == document["source_id"]
    ]
    migrated_context = [
        row
        for rows in migrator.result.context_by_work.values()
        for row in rows
        if row["source_id"] == document["source_id"]
    ]

    assert migrated == []
    assert len(migrated_context) == len(_contexts(document)) == 9
    assert len(migrator.result.works) == 1
    assert not migrator.result.experiments
    assert not migrator.result.registry_issues
    assert not migrator.result.validation.hard_issues
    assert all(row.identity.temperature_K.is_value for row in migrated)
