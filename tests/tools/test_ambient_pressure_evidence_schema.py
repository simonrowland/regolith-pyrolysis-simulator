from __future__ import annotations

import pytest

from tools.validate_literature_extracts import (
    SCHEMA_VERSION,
    validate_extract_document,
)


def _document(evidence_class: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": "ambient-pressure-fixture",
        "source": {"citation": "Synthetic schema fixture."},
        "extraction": {
            "method": "unit test",
            "date": "2026-11-03",
            "worker": "pytest",
        },
        "review_status": "draft",
        "species": {},
        "experiments": [
            {
                "experiment_id": "open",
                "ambient_pressure_evidence": {
                    "evidence_class": evidence_class,
                    "quote": "The furnace was open to the atmosphere.",
                    "locator": {"page": 3, "section": "Experimental method"},
                },
            }
        ],
    }


@pytest.mark.parametrize(
    "evidence_class",
    ["gas_flow", "gas_mixing", "vented", "atmospheric_pressure_statement"],
)
def test_open_furnace_evidence_classes_validate(evidence_class: str) -> None:
    errors = validate_extract_document(_document(evidence_class))

    assert not any("ambient_pressure_evidence" in error for error in errors)


@pytest.mark.parametrize("evidence_class", ["sealed", "vacuum", "knudsen"])
def test_non_open_furnace_evidence_classes_are_rejected(evidence_class: str) -> None:
    errors = validate_extract_document(_document(evidence_class))

    assert any(
        "ambient_pressure_evidence.evidence_class" in error
        for error in errors
    )


def test_ambient_pressure_evidence_requires_quote_and_locator() -> None:
    document = _document("gas_flow")
    document["experiments"][0]["ambient_pressure_evidence"].update(
        {"quote": "  ", "locator": {}}
    )

    errors = validate_extract_document(document)

    assert any("ambient_pressure_evidence.quote" in error for error in errors)
    assert any("ambient_pressure_evidence.locator" in error for error in errors)
