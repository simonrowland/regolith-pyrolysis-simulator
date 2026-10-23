"""Source-grounded context pins for Abdul's 2023 clinker thesis."""

from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/abdul-2023-clinker-thesis.yaml"
CORPUS = regolith_corpus_root()
SOURCE_TEXT = CORPUS / "text/abdul-2023-clinker-thesis/pdftotext-layout.txt"

pytestmark = pytest.mark.skipif(
    not SOURCE_TEXT.is_file(), reason="source text is absent from REGOLITH_CORPUS_ROOT"
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_measured_values_are_linked_context_and_not_double_scored() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    context = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("context", [])
    }
    rankinite = context["abdul_thesis_table5_3_rankinite_heat_contents"]
    assert len(rankinite["values"]["rows"]) == 7
    assert rankinite["values"]["rows"][0]["H_T_minus_H_298.15_J_mol"] == 147433
    assert rankinite["values"]["rows"][-1]["T_K"] == 1471.65
    assert rankinite["values"]["linked_observation_id"] == "abdul_2023_rankinite_heat_content"

    belite = context["abdul_thesis_table5_4_belite_heat_contents"]
    assert len(belite["values"]["rows"]) == 6
    assert belite["values"]["rows"][-1]["H_T_minus_H_298.15_J_mol"] == 212360.1
    assert belite["values"]["printed_table_range_K"] == [972.86, 1471.05]
    assert belite["values"]["printed_prose_range_K"] == [972.86, 1471.65]

    transitions = context["abdul_thesis_table5_2_transition_context"]["values"]["rows"]
    assert len(transitions) == 19
    assert transitions[5]["transition_heat_kJ_mol_as_printed"] == "7.757–15.460"
    assert "temperature_C" not in transitions[6]
    assert "temperature_C" not in transitions[9]
    assert "temperature_C" not in transitions[16]

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert len(migrator.result.observations) == 0
    assert not migrator.result.validation.hard_issues


def test_context_figures_defer_digitisation() -> None:
    document = _document()
    figure_rows = [
        row
        for species in document["species"].values()
        for row in species.get("context", [])
        if row["type"] == "figure_context"
    ]
    assert {row["locator"]["figure"] for row in figure_rows} == {
        "4.2", "4.3", "5.2", "5.3", "5.4", "5.5", "5.6", "5.7", "5.8"
    }
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figure_rows)
    assert all("marker_count_per_series" in row["values"] for row in figure_rows)

