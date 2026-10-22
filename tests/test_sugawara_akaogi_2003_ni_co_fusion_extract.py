"""Source-grounded migration and transcription guards for Sugawara & Akaogi 2003."""

from copy import deepcopy
from decimal import Decimal
import os
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/sugawara-akaogi-2003-ni-co-fusion.yaml"
CORPUS = Path(
    os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
)
SOURCE_TEXT = CORPUS / "text/sugawara-akaogi-2003-ni-co-fusion/pdftotext-layout.txt"

pytestmark = pytest.mark.skipif(
    not SOURCE_TEXT.is_file(), reason="source text is absent from REGOLITH_CORPUS_ROOT"
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observation(document: dict, observation_id: str) -> dict:
    return next(
        row
        for species in document["species"].values()
        for row in species.get("observations", [])
        if row["observation_id"] == observation_id
    )


def test_printed_table_1_values_migrate_with_species_temperature_and_locator() -> None:
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
    assert len(observations) == 75
    assert not migrator.result.validation.hard_issues

    first = observations[f"{EXTRACT.stem}::suga03_t1_co2sio4_01"]
    last = observations[f"{EXTRACT.stem}::suga03_t1_co2sio4_13"]
    assert first.identity.quantity.value is Quantity.H_MINUS_H298
    assert first.identity.species.formula == "Co2SiO4"
    assert first.value.point == Decimal("194.7")
    assert first.identity.temperature_K.value == Decimal("1373")
    assert first.locator.page == 3
    assert first.point_conditions["temperature_K"].state.value == Decimal("1373")
    assert last.value.point == Decimal("393.0")
    assert last.identity.temperature_K.value == Decimal("1773")

    for species_formula, species in document["species"].items():
        for row in species.get("observations", []):
            observation = observations[f"{EXTRACT.stem}::{row['observation_id']}"]
            assert observation.identity.species.formula == species_formula
            assert observation.value.kind.value == "point"
            assert observation.identity.temperature_K.is_value
            assert observation.locator is not None
            assert observation.locator.page == observation.locator.pdf_page_index + 1
            assert all(
                notice.kind.value != "source_value_outside_physical_range"
                for notice in observation.notices
            )


def test_fidelity_pin_rejects_a_mutated_printed_cell() -> None:
    mutated = deepcopy(_document())
    _observation(mutated, "suga03_t1_co2sio4_01")["values"]["enthalpy_increment"] = 194.8
    errors = validate_extract_document(
        mutated,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("fidelity" in error.casefold() for error in errors)


def test_table_4_author_calculations_remain_numeric_context() -> None:
    document = _document()
    context = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("context", [])
    }
    assert context["suga03_t4_co2sio4_1_heat_fusion"]["values"]["value"] == 103
    assert context["suga03_t4_co2sio4_1_entropy_fusion"]["values"]["value"] == 61
    assert context["suga03_t4_ni2sio4_1_heat_fusion"]["values"]["value"] == 221
    assert context["suga03_t4_ni2sio4_1_entropy_fusion"]["values"]["value"] == 115
