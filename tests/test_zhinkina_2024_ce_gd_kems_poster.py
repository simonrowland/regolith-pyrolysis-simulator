"""Source-grounded checks for the context-only Zhinkina et al. poster extract."""

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/zhinkina-2024-ce-gd-kems-poster.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_poster_assignments_and_reported_mixing_energy_are_preserved() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    species = document["species"]["CeO2"]
    assert species["observations"] == []
    context = {row["observation_id"]: row for row in species["context"]}

    assignments = context["zhinkina_2024_vapour_ion_assignments"]["values"]
    assert assignments["assignments"] == [
        {"ion": "CeO+", "m_z": 156, "molecule_assignments": ["CeO", "CeO2"]},
        {"ion": "CeO2+", "m_z": 172, "molecule_assignments": ["CeO2"]},
        {"ion": "GdO+", "m_z": 176, "molecule_assignments": ["GdO"]},
    ]
    minimum = context["zhinkina_2024_gibbs_mixing_energy_minimum"]["values"]
    assert minimum["value_as_printed"] == "−16.5 ± 1.4"
    assert minimum["composition_as_printed"] == "x(CeO2) = 0.53"
    assert minimum["temperature_K"] == 2150


def test_poster_plot_records_are_deferred_and_migrate_as_context_only() -> None:
    document = _document()
    context = document["species"]["CeO2"]["context"]
    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(figures) == 7
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("points" not in row["values"] for row in figures)
    assert all(row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1 for row in context)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert migrator.result.observations == {}
    assert sum(len(rows) for rows in migrator.result.context_by_work.values()) == len(context)
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
