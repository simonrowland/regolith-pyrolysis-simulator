"""Source-grounded extraction and migration checks for Morishita et al. (2004)."""

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Quantity
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/morishita-2004-sio2-partial-enthalpy.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _table_observation(document: dict, observation_id: str) -> dict:
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
    first_na_point = _table_observation(
        mutated, "morishita_2004_table_iii_na2o_1465k"
    )["values"]["points"][0]
    first_na_point["partial_molar_enthalpy_as_printed"]["value"] = -17.05
    assert check_all_fidelity_samples_match(mutated, label=EXTRACT.stem)


def test_tables_migrate_with_located_temperature_and_composition() -> None:
    document = _document()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    records = list(migrator.result.observations.values())
    assert len(records) == 60
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    assert all(
        record.identity.quantity.is_value
        and record.identity.quantity.value is Quantity.PARTIAL_MOLAR_ENTHALPY
        for record in records
    )
    assert all(
        record.evidence.class_.is_value
        and record.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
        for record in records
    )

    for record in records:
        assert record.identity.temperature_K.is_value
        assert record.point_conditions is not None
        temperature = record.point_conditions["temperature_K"]
        composition = record.point_conditions["composition"]
        assert temperature.state.is_value
        assert temperature.state.value == record.identity.temperature_K.value
        assert temperature.locator is not None
        assert temperature.locator.pdf_page_index is not None
        assert composition.locator is not None
        assert composition.locator.pdf_page_index is not None
        assert record.identity.composition is not None
        assert record.identity.composition.tag is composition.state.tag
        assert record.uncertainty.verbatim["sigma"] > 0

    counts = Counter(
        next(
            table_id
            for table_id in (
                "morishita_2004_table_iii_na2o_1465k",
                "morishita_2004_table_iii_k2o_1465k",
                "morishita_2004_table_iii_cs2o_1465k",
                "morishita_2004_table_iv_li2o_1663k",
                "morishita_2004_table_iv_na2o_1663k",
                "morishita_2004_table_iv_k2o_1663k",
                "morishita_2004_table_vi_cas_1663k",
            )
            if f"::{table_id}::" in record.observation_id
        )
        for record in records
    )
    assert counts == {
        "morishita_2004_table_iii_na2o_1465k": 9,
        "morishita_2004_table_iii_k2o_1465k": 13,
        "morishita_2004_table_iii_cs2o_1465k": 7,
        "morishita_2004_table_iv_li2o_1663k": 11,
        "morishita_2004_table_iv_na2o_1663k": 6,
        "morishita_2004_table_iv_k2o_1663k": 7,
        "morishita_2004_table_vi_cas_1663k": 7,
    }

    by_source_row = {
        table_id: next(
            record
            for record in records
            if f"::{table_id}::" in record.observation_id
        )
        for table_id in (
            "morishita_2004_table_iii_na2o_1465k",
            "morishita_2004_table_iv_li2o_1663k",
            "morishita_2004_table_vi_cas_1663k",
        )
    }
    assert by_source_row[
        "morishita_2004_table_iii_na2o_1465k"
    ].value.point == Decimal("-17.06")
    assert by_source_row[
        "morishita_2004_table_iv_li2o_1663k"
    ].value.point == Decimal("-4.61")
    assert by_source_row[
        "morishita_2004_table_vi_cas_1663k"
    ].value.point == Decimal("-25.38")
    assert by_source_row[
        "morishita_2004_table_vi_cas_1663k"
    ].identity.composition.is_unknown

    figures = [
        item
        for item in document["species"]["SiO2"]["context"]
        if item.get("type") == "figure_record"
    ]
    assert len(figures) == 4
    for figure in figures:
        assert figure["panel"] == "whole_figure"
        assert figure["attribution"]
        assert figure["values"]["digitisation"] == "deferred to a figure pass"
        assert "points" not in figure["values"]
        assert all("marker_count" in series for series in figure["values"]["series"])


def test_printed_table_iii_k2o_composition_anomaly_is_retained() -> None:
    document = _document()
    k2o = _table_observation(
        document, "morishita_2004_table_iii_k2o_1465k"
    )["values"]["points"][-1]
    assert k2o["average_sio2_mol_percent_as_printed"] == 78.1
    assert k2o["partial_molar_enthalpy_as_printed"]["as_printed"] == "−6.80"
    assert k2o["composition_mol"]["SiO2"] == "0.781"

    drop_solution = next(
        item
        for item in document["species"]["SiO2"]["context"]
        if item["observation_id"] == "morishita_2004_table_iii_drop_solution_heat"
    )
    assert drop_solution["values"]["rows"][21]["final_sio2_mol_percent"] == 21.9
