"""Source-grounded migration pins for Konar (2018), Tables 5.2 and A-D."""

from copy import deepcopy
import os
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/konar-2018-alkali-cmas-thesis.yaml"
CORPUS = Path(
    os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
)
SOURCE_TEXT = CORPUS / "text/konar-2018-alkali-cmas-thesis/pdftotext-layout.txt"

pytestmark = pytest.mark.skipif(
    not SOURCE_TEXT.is_file(), reason="source text is absent from REGOLITH_CORPUS_ROOT"
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_dsc_and_quench_rows_migrate_with_printed_values_and_coordinates() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    observations = migrator.result.observations
    assert len(observations) == 51
    assert not migrator.result.validation.hard_issues

    s0 = observations[f"{EXTRACT.stem}::konar_t5_2_s0_3_melting_point"]
    assert str(s0.value.point) == "1352"
    assert s0.identity.species.formula == "Na2SiO3"
    assert s0.identity.total_pressure_Pa.is_value
    assert str(s0.identity.total_pressure_Pa.value) == "101325"
    assert s0.locator.page == 184

    b2 = observations[f"{EXTRACT.stem}::konar_b2_lsm5_melting"]
    assert str(b2.value.point) == "1738.15"
    assert b2.identity.species.formula == "Li2MgSiO4"
    assert b2.locator.page == 402

    for formula, species in document["species"].items():
        for row in species.get("observations", []):
            migrated = observations[f"{EXTRACT.stem}::{row['observation_id']}"]
            assert migrated.value.kind.value == "point"
            assert migrated.identity.species.formula == row["values"].get("formula", formula)
            if row["values"].get("quantity") == "equilibrium_phase_composition":
                assert migrated.identity.temperature_K.is_value
            else:
                assert migrated.value.point is not None
            if row["values"].get("quantity") == "equilibrium_phase_composition":
                assert migrated.identity.composition.is_value
            assert migrated.locator.page == migrated.locator.pdf_page_index + 1
            if row["values"].get("basis") == "mole_fraction":
                assert 0 < migrated.value.point <= 1


def test_table_rows_match_published_cells_and_distinguish_blank_cells() -> None:
    document = _document()
    rows = [
        row
        for species in document["species"].values()
        for row in species.get("observations", [])
    ]
    assert len([row for row in rows if row["locator"].get("table") == "5.2"]) == 13
    assert len([row for row in rows if row["locator"].get("table") == "A.1"]) == 12
    assert len([row for row in rows if row["locator"].get("table") == "B.2"]) == 14
    assert len([row for row in rows if row["locator"].get("table") == "D.3"]) == 9

    glass = [row for row in rows if row["locator"].get("table") == "C.3"]
    assert [row["values"]["component_amount"] for row in glass] == [0.168, 0.344, 0.488]

    figure_rows = [
        row
        for species in document["species"].values()
        for row in species.get("context", [])
        if row["type"] == "figure_context"
    ]
    assert {row["locator"]["figure"] for row in figure_rows} == {
        "5.3", "5.4", "A.1", "A.3", "B.4", "C.2", "C.4", "D.3"
    }
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figure_rows)


def test_fidelity_pin_rejects_a_mutated_printed_cell() -> None:
    mutated = deepcopy(_document())
    row = next(
        row
        for row in mutated["species"]["SiO2"]["observations"]
        if row["observation_id"] == "konar_t5_2_s0_3_melting_point"
    )
    row["values"]["temperature_K"] = 1353
    errors = validate_extract_document(
        mutated,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("fidelity" in error.casefold() for error in errors)

