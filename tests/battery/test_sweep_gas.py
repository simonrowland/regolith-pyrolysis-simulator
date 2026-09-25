from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import RefusalReason
from simulator.battery.migrate import (
    Migrator,
    _sweep_gas_from_plain,
    experiment_from_plain,
    to_plain,
)
from simulator.battery.records import State, SweepGas, SweepGasComponent
from simulator.battery.validate import validate_experiment, validate_sweep_gas
from tests.battery import factories
from tests.battery.test_migrate import _write_min_tree
from tests.battery.test_migrate_benches import _registry_extract
from tools.validate_literature_extracts import validate_extract_document

EXTRACTS = Path(__file__).resolve().parents[2] / "data/literature/extracts"
UNKNOWN = {"tag": "unknown", "reason": "not_published"}


def _mixture() -> dict:
    return {
        "flow_sccm": UNKNOWN.copy(),
        "partial_pressure_Pa": UNKNOWN.copy(),
        "components": [
            {"species": species, "mole_fraction": UNKNOWN.copy(),
             "flow_sccm": UNKNOWN.copy(), "partial_pressure_Pa": UNKNOWN.copy()}
            for species in ("CO", "Ar")
        ],
    }


def _extract_with_gas(gas: object) -> dict:
    doc = _registry_extract()
    doc["experiments"][0]["pressure_environment"]["sweep_gas"]["state"] = {
        "tag": "value", "value": gas,
    }
    return doc


def test_existing_single_species_migrates_byte_identically(tmp_path) -> None:
    doc = yaml.safe_load((EXTRACTS / "kems-027-plante-hastie-1983.yaml").read_text())
    result = Migrator(root=_write_min_tree(tmp_path, doc)).run()
    experiment = next(e for e in result.experiments.values()
                      if e.experiment_id.endswith("::tms-n2-glass-series"))
    payload = json.dumps(to_plain(experiment), sort_keys=True, separators=(",", ":")).encode()
    # 4702a4d92 preserves each printed original beside a converted scalar.
    # SC-289 restores the comma-truncated temperature note ("Higher
    # temperatures than KMS are stated, but no numerical temperature or
    # interval is printed for this series") and duration note ("Higher
    # temperatures than KMS stated, but no single duration printed"), adding
    # 101 bytes to this extract-derived payload.
    assert len(payload) == 2738
    assert hashlib.sha256(payload).hexdigest() == (
        "6060c29202f5d63c3ecd15a27b0b615e247ea819180dbbb10231e5246854a7c5"
    )
    assert experiment_from_plain(to_plain(experiment)) == experiment


@pytest.mark.parametrize("bad", ["CO-Ar", 42, ["CO", "Ar"], {"components": "CO-Ar"}])
def test_malformed_located_payload_is_typed_refusal(bad) -> None:
    experiment = factories.kems_experiment()
    experiment = replace(experiment, pressure_environment=replace(
        experiment.pressure_environment, sweep_gas=factories.located(bad)))
    issues = validate_experiment(experiment, {"work-1": factories.work()}, {})
    assert any(i.reason is RefusalReason.CONDITIONAL_FIELD
               and i.path.endswith(".sweep_gas") for i in issues)


@pytest.mark.parametrize("field", ["flow_sccm", "partial_pressure_Pa"])
def test_malformed_nested_state_is_typed_refusal(field) -> None:
    experiment = factories.kems_experiment()
    gas = replace(experiment.pressure_environment.sweep_gas.state.value,
                  **{field: "not a State"})
    experiment = replace(experiment, pressure_environment=replace(
        experiment.pressure_environment, sweep_gas=factories.located(gas)))
    issues = validate_experiment(experiment, {"work-1": factories.work()}, {})
    assert any(i.reason is RefusalReason.CONDITIONAL_FIELD and i.path.endswith(field)
               for i in issues)


def test_unknown_mixture_fractions_survive_migration(tmp_path) -> None:
    result = Migrator(root=_write_min_tree(tmp_path, _extract_with_gas(_mixture()))).run()
    assert result.validation.ok, result.validation.hard_issues
    experiment = next(iter(result.experiments.values()))
    gas = experiment.pressure_environment.sweep_gas.state.value
    assert isinstance(gas, SweepGas) and gas.species is None
    assert tuple(c.species for c in gas.components) == ("CO", "Ar")
    for component in gas.components:
        assert component.mole_fraction == State.unknown("not_published")
        assert component.flow_sccm == State.unknown("not_published")
        assert component.partial_pressure_Pa == State.unknown("not_published")
    assert to_plain(gas) == _mixture()
    assert experiment_from_plain(to_plain(experiment)) == experiment


def test_component_quantities_round_trip() -> None:
    plain = _mixture()
    for component, fraction, flow, pressure in zip(
        plain["components"], ("0.25", "0.75"), ("10", "30"), ("100", "300"), strict=True,
    ):
        for field, value in (("mole_fraction", fraction), ("flow_sccm", flow),
                             ("partial_pressure_Pa", pressure)):
            component[field] = {"tag": "value", "value": value}
    gas = _sweep_gas_from_plain(plain)
    assert validate_sweep_gas(gas, "gas") == []
    assert to_plain(gas) == plain
    assert gas.components[0].mole_fraction.value == Decimal("0.25")


def test_ts1985_keeps_printed_alternatives_and_absences(tmp_path) -> None:
    doc = yaml.safe_load((EXTRACTS / "ts1985.yaml").read_text())
    assert validate_extract_document(doc) == []
    result = Migrator(root=_write_min_tree(tmp_path, doc)).run()
    experiment = next(e for e in result.experiments.values()
                      if e.experiment_id.endswith("::na2o-sio2-equilibration-series"))
    located = experiment.pressure_environment.sweep_gas
    gas = located.state.value
    assert isinstance(gas, SweepGas)
    assert gas.species is None and gas.components is None
    single, mixture = gas.alternatives
    assert single.species == "CO" and single.components is None
    assert tuple(c.species for c in mixture.components) == ("CO", "Ar")
    assert all(c.mole_fraction == State.unknown("not_published") for c in mixture.components)
    assert located.locator.published_page == 817
    assert located.locator.pdf_page_index == 3
    assert located.locator.section == "3.2 Experimental method"
    assert "Printed as CO or CO-Ar mixture, dried and deoxidized" in located.locator.note
    assert experiment_from_plain(to_plain(experiment)) == experiment
    assert not any("sweep_gas" in i.path for i in result.validation.hard_issues)


@pytest.mark.parametrize("defect", [
    "scalar", "missing_flow", "extra_field", "component_extra_field", "component_missing_fraction",
    "bad_fraction", "bad_components", "bad_alternatives", "both_forms", "empty_species",
])
def test_file_and_store_refuse_same_shapes(tmp_path, defect) -> None:
    gas = _mixture()
    if defect == "scalar":
        gas = "CO-Ar"
    elif defect == "missing_flow":
        del gas["flow_sccm"]
    elif defect == "extra_field":
        gas["fraction"] = "0.5"
    elif defect == "component_extra_field":
        gas["components"][0]["fraction"] = "0.5"
    elif defect == "component_missing_fraction":
        del gas["components"][0]["mole_fraction"]
    elif defect == "bad_fraction":
        gas["components"][0]["mole_fraction"] = {"tag": "value", "value": "not numeric"}
    elif defect == "bad_components":
        gas["components"] = ["CO", "Ar"]
    elif defect == "bad_alternatives":
        del gas["components"]
        gas["alternatives"] = ["CO", "Ar"]
    elif defect == "both_forms":
        gas["species"] = "CO"
    elif defect == "empty_species":
        del gas["components"]
        gas["species"] = ""
    doc = _extract_with_gas(gas)
    assert any("sweep_gas" in error for error in validate_extract_document(doc))
    result = Migrator(root=_write_min_tree(tmp_path, doc)).run()
    assert any("sweep_gas" in issue.path and issue.reason is RefusalReason.CONDITIONAL_FIELD
               for issue in result.validation.hard_issues)


def test_file_accepts_exact_serialized_field_lists() -> None:
    plain = _mixture()
    gas = _sweep_gas_from_plain(plain)
    assert isinstance(gas.components[0], SweepGasComponent)
    for payload in (plain, to_plain(gas)):
        assert validate_extract_document(_extract_with_gas(copy.deepcopy(payload))) == []


def _single_species() -> dict:
    return {
        "species": "N2",
        "flow_sccm": UNKNOWN.copy(),
        "partial_pressure_Pa": UNKNOWN.copy(),
    }


def test_empty_optional_collections_are_absent() -> None:
    base = _single_species()
    reference = _sweep_gas_from_plain(base)
    assert isinstance(reference, SweepGas)
    for key in ("components", "alternatives"):
        gas = _sweep_gas_from_plain({**base, key: []})
        assert isinstance(gas, SweepGas)
        assert gas == reference
        assert validate_sweep_gas(gas, "gas") == []
        assert to_plain(gas) == base
    both = _sweep_gas_from_plain({**base, "components": [], "alternatives": []})
    assert both == reference
    assert to_plain(both) == base


def test_single_species_with_empty_collections_migrates_byte_identically(tmp_path) -> None:
    payloads = {
        "plain": _single_species(),
        "components": {**_single_species(), "components": []},
        "alternatives": {**_single_species(), "alternatives": []},
        "both": {**_single_species(), "components": [], "alternatives": []},
    }
    serialized = []
    for label, payload in payloads.items():
        result = Migrator(root=_write_min_tree(tmp_path / label, _extract_with_gas(payload))).run()
        assert result.validation.ok, result.validation.hard_issues
        experiment = next(iter(result.experiments.values()))
        serialized.append(
            json.dumps(to_plain(experiment), sort_keys=True, separators=(",", ":")).encode()
        )
        assert experiment_from_plain(to_plain(experiment)) == experiment
    assert len(set(serialized)) == 1
