from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Quantity, StateTag, ValueKind
from simulator.battery.migrate import Migrator, REPO_ROOT
from simulator.battery.validate import validate_corpus


EXTRACT = REPO_ROOT / "data/literature/extracts/nazarov-2015-dhofar-silicides.yaml"
TABLE2_PREFIX = "nazarov_2015_table2_"
ATM_TO_PA = Decimal("101325")
SIO_RENDER_PINS = {
    1833: Decimal("6.95e-5"),
    1933: Decimal("2.28e-4"),
    2033: Decimal("8.87e-4"),
}


def _migrate(extract_path: Path = EXTRACT) -> Migrator:
    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(extract_path)
    migrator.finalize()
    return migrator


def _table2_observations(migrator: Migrator) -> list:
    return [
        observation
        for observation in migrator.result.observations.values()
        if TABLE2_PREFIX in observation.observation_id
    ]


def _assert_sio_render_pins(migrator: Migrator) -> None:
    sio_points = {
        int(observation.identity.temperature_K.value): observation
        for observation in _table2_observations(migrator)
        if "nazarov_2015_table2_sio::" in observation.observation_id
    }
    assert set(sio_points) == {1833, 1883, 1933, 1983, 2033}
    for temperature_K, expected_pressure_atm in SIO_RENDER_PINS.items():
        observation = sio_points[temperature_K]
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == expected_pressure_atm * ATM_TO_PA
        assert observation.locator.published_page == 172
        assert observation.locator.pdf_page_index == 4


def test_nazarov_real_migration_pins_render_and_row_conditions() -> None:
    migrator = _migrate()
    observations = _table2_observations(migrator)
    assert len(observations) == 33
    _assert_sio_render_pins(migrator)

    reduced = [
        observation
        for observation in observations
        if "table2_o_calculated" in observation.observation_id
        or "table2_o2_calculated" in observation.observation_id
    ]
    assert len(reduced) == 10
    assert all(
        observation.derivation is not None
        and observation.derivation.has_located_unprinted_raw_inputs
        for observation in reduced
    )
    assert all(
        observation.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
        for observation in reduced
    )

    for observation in observations:
        assert observation.identity.quantity.is_value
        assert observation.identity.quantity.value is Quantity.P_PARTIAL
        assert observation.identity.temperature_K.is_value
        assert observation.identity.composition.is_value
        assert observation.identity.total_pressure_Pa.is_unknown
        assert observation.identity.fO2_Pa.is_unknown

        conditions = observation.point_conditions or {}
        assert conditions["temperature_K"].state.tag is StateTag.VALUE
        assert conditions["temperature_K"].locator.published_page == 172
        assert conditions["composition"].state.tag is StateTag.VALUE
        assert conditions["composition"].locator.published_page == 172
        assert (
            conditions["composition"].state.value.basis
            == "formula_derived_from_synthetic_anorthite"
        )
        assert "total_pressure_Pa" not in conditions
        assert conditions["fO2_Pa"].state.tag is StateTag.UNKNOWN
        assert conditions["fO2_Pa"].locator.published_page == 172

    relation = next(
        observation
        for observation in migrator.result.observations.values()
        if observation.observation_id.endswith(
            "nazarov_2015_sio_pressure_temperature_relation"
        )
    )
    assert relation.locator.published_page == 171
    assert relation.locator.pdf_page_index == 3
    assert relation.locator.equation == "1"
    fit = relation.provenance["pressure_temperature_relation"]
    assert fit["pressure_unit_as_printed"] == "atm"
    assert fit["temperature_unit_as_printed"] == "K"
    assert fit["A_as_printed"] == "21 250 ± 1265"
    assert fit["A"] == 21250
    assert fit["sigma_A"] == 1265
    assert fit["B_as_printed"] == "7.414 ± 0.655"
    assert fit["B"] == 7.414
    assert fit["sigma_B"] == 0.655

    validation = validate_corpus(
        migrator.result.works,
        migrator.result.experiments,
        migrator.result.observations,
        residuals=None,
    )
    assert validation.hard_issues == ()


def test_nazarov_sio_eq1_range_and_uncertainty_are_declared() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    relation = next(
        observation
        for observation in document["species"]["SiO"]["observations"]
        if observation["observation_id"]
        == "nazarov_2015_sio_pressure_temperature_relation"
    )

    assert relation["locator"]["published_page"] == 171
    assert relation["locator"]["equation"] == "1"
    assert relation["T_range_K"] == [1833, 2033]
    assert relation["values"]["temperature_range_K"] == [1833, 2033]
    assert relation["values"]["range_basis"].startswith("Table 2")
    assert "confidence level not stated" in relation["uncertainty"]["note"]
    assert (
        relation["values"]["pressure_temperature_relation"]
        == relation["values"]["provenance"]["pressure_temperature_relation"]
    )


def test_nazarov_source_context_and_o2_note() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))

    for formula in ("MoO2", "MoO3"):
        assert not document["species"][formula].get("observations")
        context = document["species"][formula]["context"][0]["values"]
        assert context["flag"] == "cell_contamination"
        assert context["pressure_in_table2"] == "not_tabulated"
    assert document["species"]["Si"]["context"][0]["values"]["row_count"] == 16
    assert (
        document["species"]["O"]["observations"][0]["values"]["method_class"]
        == "measured_reduced"
    )
    o2_values = document["species"]["O2"]["observations"][0]["values"]
    assert o2_values["method_class"] == "measured_reduced"
    assert "calculated oxygen partial pressure" in o2_values["cell_contamination_note"]
    assert "O2 = 2O" in o2_values["cell_contamination_note"]
    assert "not molybdenum-cell vapor" in o2_values["cell_contamination_note"]


def test_yaml_mutation_reaches_migrator_and_breaks_render_pin(tmp_path: Path) -> None:
    source = EXTRACT.read_text(encoding="utf-8")
    original = "value: 2.28e-4\n          p_atm: 2.28e-4"
    mutated = "value: 2.29e-4\n          p_atm: 2.29e-4"
    assert source.count(original) == 1

    mutated_extract = tmp_path / EXTRACT.name
    mutated_extract.write_text(source.replace(original, mutated, 1), encoding="utf-8")

    with pytest.raises(AssertionError):
        _assert_sio_render_pins(_migrate(mutated_extract))
