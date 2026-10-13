"""Source-specific migration and fidelity guards for Sugawara & Akaogi (2004)."""

from copy import deepcopy
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Quantity
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/sugawara-akaogi-2004-liquid-calorimetry.yaml"
)


def test_table1_rows_migrate_with_temperature_and_direct_evidence() -> None:
    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    table1 = [
        observation
        for observation in migrator.result.observations.values()
        if observation.locator is not None
        and observation.locator.table == "1"
        and observation.locator.published_page == 1588
    ]
    assert len(table1) == 22
    assert all(
        observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.H_MINUS_H298
        for observation in table1
    )
    assert all(
        observation.evidence.class_.is_value
        and observation.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
        for observation in table1
    )
    assert all(
        observation.point_conditions is not None
        and observation.point_conditions["temperature_K"].state.is_value
        and observation.point_conditions["temperature_K"].locator is not None
        for observation in table1
    )


def test_table1_fidelity_pin_fails_after_enthalpy_mutation() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    first = mutated["species"]["Na2SiO3"]["observations"][0]["values"][
        "series"
    ][0]
    first["enthalpy_increment_kJ_mol"] = 210.7

    errors = validate_extract_document(
        mutated,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("fidelity" in error.casefold() for error in errors)
