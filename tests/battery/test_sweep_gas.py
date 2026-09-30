from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.yaml_cache import load_cached_safe_yaml

from simulator.battery.enums import Quantity, RefusalReason
from simulator.battery.migrate import (
    Migrator,
    _pressure_total_from_extract,
    _sweep_gas_from_plain,
    experiment_from_plain,
    to_plain,
)
from simulator.battery.records import State, SweepGas, SweepGasComponent, ValueKind
from simulator.battery.validate import validate_experiment, validate_sweep_gas
from tests.battery import factories
from tests.battery.test_migrate import _migrate_real_extract, _write_min_tree
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
    doc = load_cached_safe_yaml(
        (EXTRACTS / "kems-027-plante-hastie-1983.yaml").read_text()
    )
    result = Migrator(root=_write_min_tree(tmp_path, doc)).run()
    experiment = next(e for e in result.experiments.values()
                      if e.experiment_id.endswith("::tms-n2-glass-series"))
    plain = to_plain(experiment)
    # The current extract preserves the paper's nominal and analytical Table 1
    # compositions. That intentional source enrichment changes serialized
    # bytes; this test guards the structured evidence and migration round-trip.
    printed = plain["sample"]["printed_composition"]["state"]["value"]
    assert set(printed) == {"nominal", "analytical"}
    assert printed["nominal"]["Na2O"] == "10.13"
    assert printed["analytical"]["Na2O"] == "8.59"
    gas = plain["pressure_environment"]["sweep_gas"]["state"]["value"]
    assert gas["species"] == "N2"
    payload = json.dumps(to_plain(experiment), sort_keys=True, separators=(",", ":")).encode()
    # SC-289 restores the comma-truncated temperature and duration notes in
    # this extract-derived payload. The candidate line also preserves the
    # printed nominal and analytical compositions above.
    assert len(payload) == 4429
    assert hashlib.sha256(payload).hexdigest() == (
        "42abec9a42d254ffc175d2953f72c5c7d044bbf37b27a72adbdec94ed832430d"
    )
    assert to_plain(experiment_from_plain(plain)) == plain


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
    doc = load_cached_safe_yaml((EXTRACTS / "ts1985.yaml").read_text())
    assert validate_extract_document(doc) == []
    result = Migrator(root=_write_min_tree(tmp_path, doc)).run()
    # EP split the printed composition/temperature runs into distinct
    # experiments; select an activity run and check its printed CO atmosphere.
    experiment = next(e for e in result.experiments.values()
                      if e.experiment_id.endswith("::na2o-sio2-xna2o-0p40-t1100"))
    located = experiment.pressure_environment.sweep_gas
    gas = located.state.value
    assert isinstance(gas, SweepGas)
    assert gas.species == "CO"
    assert gas.flow_sccm == State.unknown("not_published")
    assert gas.partial_pressure_Pa == State.of(Decimal("101325"))
    assert gas.components is None and gas.alternatives is None
    assert experiment.pressure_environment.total_pressure_Pa.state.value.point == Decimal("101325")
    assert located.locator.published_page == 816
    assert located.locator.pdf_page_index == 2
    assert located.locator.section == "2. Experimental principle"
    assert "printed P_CO = 1 atm" in located.locator.note
    activity = result.observations["ts1985::ts1985_na2o_table2_X0p40_T1100C"]
    assert activity.identity.total_pressure_Pa.is_value
    assert activity.identity.total_pressure_Pa.value == Decimal("101325")
    assert activity.identity.fO2_Pa.is_value
    assert activity.identity.fO2_Pa.value > 0
    assert activity.derivation is not None
    assert "experiment.pressure_environment.total_pressure_Pa" in activity.derivation.relation
    assert "graphite_c_co_buffer" in activity.derivation.relation

    anchor_experiment = next(e for e in result.experiments.values()
                             if e.experiment_id.endswith("::na2o-sio2-table1-xna2o-0p50-t1200"))
    anchor_located = anchor_experiment.pressure_environment.sweep_gas
    anchor_gas = anchor_located.state.value
    assert isinstance(anchor_gas, SweepGas)
    assert anchor_gas.species is None
    assert anchor_gas.flow_sccm == State.unknown("not_published")
    assert anchor_gas.partial_pressure_Pa == State.unknown("not_published")
    assert anchor_gas.alternatives is None
    assert anchor_gas.components is not None
    co, ar = anchor_gas.components
    assert tuple(c.species for c in anchor_gas.components) == ("CO", "Ar")
    assert co.partial_pressure_Pa == State.of(Decimal("10132.5"))
    assert co.mole_fraction == State.unknown("not_published")
    assert co.flow_sccm == State.unknown("not_published")
    assert ar.mole_fraction == State.unknown("not_published")
    assert ar.flow_sccm == State.unknown("not_published")
    assert ar.partial_pressure_Pa == State.unknown("not_published")
    assert anchor_experiment.pressure_environment.total_pressure_Pa.state == State.unknown("not_published")
    assert anchor_located.locator.published_page == 817
    assert anchor_located.locator.pdf_page_index == 3
    assert anchor_located.locator.table == "1"
    round_tripped = experiment_from_plain(to_plain(experiment))
    assert to_plain(round_tripped.pressure_environment.sweep_gas) == to_plain(located)
    assert not any("sweep_gas" in i.path for i in result.validation.hard_issues)


def _ts1985_single_activity_doc() -> dict:
    doc = load_cached_safe_yaml((EXTRACTS / "ts1985.yaml").read_text())
    experiment_id = "na2o-sio2-xna2o-0p40-t1100"
    experiment = next(
        item for item in doc["experiments"] if item["experiment_id"] == experiment_id
    )
    observation = next(
        item
        for item in doc["species"]["Na2O"]["observations"]
        if item["observation_id"] == "ts1985_na2o_table2_X0p40_T1100C"
    )
    doc["experiments"] = [copy.deepcopy(experiment)]
    doc["species"]["Na2O"]["observations"] = [copy.deepcopy(observation)]
    doc["species"]["Na2O"].pop("context", None)
    return doc


def test_experiment_unknown_pressure_is_not_inherited(tmp_path) -> None:
    doc = _ts1985_single_activity_doc()
    experiment = doc["experiments"][0]
    experiment["pressure_environment"]["total_pressure_Pa"] = UNKNOWN.copy()
    experiment["pressure_environment"]["sweep_gas"]["state"]["value"][
        "partial_pressure_Pa"
    ] = UNKNOWN.copy()
    doc["species"]["Na2O"]["observations"][0].pop("equipment", None)
    result = Migrator(root=_write_min_tree(tmp_path, doc)).run()
    observation = next(iter(result.observations.values()))
    assert observation.identity.total_pressure_Pa.is_unknown
    assert observation.identity.total_pressure_Pa.reason == (
        "no total_pressure_Pa mapped from source"
    )


def test_missing_cco_waypoint_keeps_fo2_refused(tmp_path) -> None:
    doc = _ts1985_single_activity_doc()
    doc["experiments"][0].pop("fO2_control", None)
    result = Migrator(root=_write_min_tree(tmp_path, doc)).run()
    observation = next(iter(result.observations.values()))
    assert observation.identity.total_pressure_Pa.is_value
    assert observation.identity.fO2_Pa.is_unknown
    assert observation.identity.fO2_Pa.reason == (
        "no fO2_Pa mapped from source"
    )


def test_migrated_activity_records_unknown_uncompared_axes(tmp_path) -> None:
    doc = _ts1985_single_activity_doc()
    experiment = doc["experiments"][0]
    experiment.pop("fO2_control", None)
    experiment["pressure_environment"]["total_pressure_Pa"] = UNKNOWN.copy()
    experiment["pressure_environment"]["sweep_gas"]["state"]["value"][
        "partial_pressure_Pa"
    ] = UNKNOWN.copy()
    doc["species"]["Na2O"]["observations"][0].pop("equipment", None)

    result = Migrator(root=_write_min_tree(tmp_path, doc)).run()
    identity = next(iter(result.observations.values())).identity

    assert identity.fO2_Pa.is_unknown
    assert identity.fO2_Pa.reason == "no fO2_Pa mapped from source"
    assert identity.total_pressure_Pa.is_unknown
    assert identity.total_pressure_Pa.reason == (
        "no total_pressure_Pa mapped from source"
    )


def test_hastie_model_pressure_does_not_inherit_to_kems_points(tmp_path) -> None:
    result = _migrate_real_extract(
        tmp_path, "kems-020-hastie-1981-nbsir.yaml"
    )
    points = [
        observation
        for observation in result.observations.values()
        if observation.identity.quantity == State.of(Quantity.P_PARTIAL)
    ]
    assert len(points) == 12
    assert all(
        observation.identity.total_pressure_Pa is not None
        and observation.identity.total_pressure_Pa.is_unknown
        for observation in points
    )
    experiment = next(iter(result.experiments.values()))
    assert experiment.pressure_environment.total_pressure_Pa.state.is_unknown


def test_model_row_total_pressure_is_not_a_partial_pressure_identity() -> None:
    model = load_cached_safe_yaml(
        (EXTRACTS / "kems-020-hastie-1981-nbsir.yaml").read_text()
    )
    row = next(
        observation
        for observation in model["species"]["Na"]["observations"]
        if observation["observation_id"]
        == "hastie_1981_table3_solgasmix_model_not_measurement"
    )
    total, provenance = _pressure_total_from_extract(
        row, row["values"], gas_formula="Na", source_text=""
    )
    assert total is None
    assert provenance is None


def test_ueda_about_chamber_pressure_does_not_inherit_to_knudsen_points(tmp_path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-095-ueda-1986.yaml")
    pressures = [
        experiment.pressure_environment.total_pressure_Pa
        for key, experiment in result.experiments.items()
        if "::experiment::ti-co-nco-" in key
    ]
    assert len(pressures) == 11
    assert all(pressure.state.is_unknown for pressure in pressures)
    assert all(
        "extract_value=about 3 x 10^-5 Pa" in (pressure.state.reason or "")
        for pressure in pressures
    )
    points = [
        observation
        for observation in result.observations.values()
        if "ueda_1986_table1_xrd::" in observation.observation_id
    ]
    assert len(points) == 9
    assert all(
        observation.identity.total_pressure_Pa is None
        or observation.identity.total_pressure_Pa.is_unknown
        for observation in points
    )


def test_homma_printed_chamber_pressure_is_an_interval(tmp_path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-001-homma-1966.yaml")
    experiments = [
        experiment
        for key, experiment in result.experiments.items()
        if "::experiment::" in key
    ]
    assert len(experiments) == 12
    for experiment in experiments:
        pressure = experiment.pressure_environment.total_pressure_Pa
        assert pressure.state.is_value
        assert pressure.state.value.kind is ValueKind.INTERVAL
        assert pressure.state.value.interval_low == Decimal(
            "0.1333223684210526315789473684"
        )
        assert pressure.state.value.interval_high == Decimal(
            "1.333223684210526315789473684"
        )
        assert pressure.locator.page == 516


def test_ohno_printed_chamber_pressure_is_an_interval(tmp_path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-002-ohno-1967.yaml")
    pressure = next(
        experiment.pressure_environment.total_pressure_Pa
        for key, experiment in result.experiments.items()
        if key.endswith("::kems-002-ohno-1967")
    )
    assert pressure.state.is_value
    assert pressure.state.value.kind is ValueKind.INTERVAL
    assert pressure.state.value.interval_low == Decimal(
        "0.1333223684210526315789473684"
    )
    assert pressure.state.value.interval_high == Decimal(
        "1.333223684210526315789473684"
    )
    assert pressure.locator.page == 1164


def test_richter_h2_pressure_note_does_not_demote_its_point(tmp_path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-037-richter-2002.yaml")
    pressures = [
        experiment.pressure_environment.total_pressure_Pa
        for experiment in result.experiments.values()
        if experiment.pressure_environment.total_pressure_Pa.inference is not None
        and "as_published=0.000187 bar"
        in experiment.pressure_environment.total_pressure_Pa.inference.inputs
    ]
    assert len(pressures) == 1
    assert pressures[0].state.is_value
    assert pressures[0].state.value.kind is ValueKind.POINT
    assert pressures[0].state.value.point == Decimal("18.7")


def test_heck_inferred_one_atmosphere_remains_a_point(tmp_path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-140-heck-2025.yaml")
    pressure = result.experiments[
        "10.1016/j.gca.2025.05.007::experiment::open-furnace-mvce-degassing-series"
    ].pressure_environment.total_pressure_Pa
    assert pressure.state.is_value
    assert pressure.state.value.kind is ValueKind.POINT
    assert pressure.state.value.point == Decimal("101325")


def test_sossi_printed_one_atmosphere_still_inherits(tmp_path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-012-sossi-2019.yaml")
    observation = result.observations[
        "kems-012-sossi-2019::sossi_2019_k_class_b1"
    ]
    assert observation.identity.total_pressure_Pa == State.of(Decimal("100000"))


@pytest.mark.parametrize(
    "observation_id",
    [
        "sossi_2019_na_alpha_e_authors_adopted_unity",
        "sossi_2019_na_alpha_e_authors_adopted_unity_quoted_20260906",
        "sossi_2019_na_pure_system_LH_table5",
        "sossi_2019_na_pure_system_LH_table5_quoted_20260906",
        "sossi_2019_k_open_furnace_alpha_e_context",
    ],
)
def test_model_carrier_keeps_printed_sossi_furnace_condition(
    tmp_path, observation_id: str
) -> None:
    result = _migrate_real_extract(tmp_path, "kems-012-sossi-2019.yaml")
    observation = result.observations[
        f"kems-012-sossi-2019::{observation_id}"
    ]
    assert observation.identity.total_pressure_Pa == State.of(Decimal("100000"))


@pytest.mark.parametrize(
    "observation_id",
    [
        "sossi_2019_na_logKstar_table3",
        "sossi_2019_na_logKstar_table3_quoted_20260906",
    ],
)
def test_sossi_log_k_star_does_not_use_furnace_pressure(
    tmp_path, observation_id: str
) -> None:
    result = _migrate_real_extract(tmp_path, "kems-012-sossi-2019.yaml")
    observation = result.observations[
        f"kems-012-sossi-2019::{observation_id}"
    ]
    assert observation.identity.total_pressure_Pa == State.not_applicable(
        "profile log10_K_star does not use total_pressure_Pa"
    )


def test_model_carrier_keeps_printed_fedkin_chamber_condition(tmp_path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-005-fedkin-2006.yaml")
    points = [
        observation
        for observation in result.observations.values()
        if "fedkin_2006_sio_hashimoto_table3_complete_b1::" in observation.observation_id
    ]
    assert len(points) == 4
    assert all(
        observation.identity.total_pressure_Pa == State.of(Decimal("0.0013"))
        for observation in points
    )


def test_norris_printed_atmosphere_ignores_page_range_locator(tmp_path) -> None:
    result = _migrate_real_extract(
        tmp_path, "norris-2017-earth-volatiles-nature.yaml"
    )
    observation = result.observations[
        "norris-2017-earth-volatiles-nature::norris_2017_volatile_loss_conditions"
    ]
    assert observation.identity.total_pressure_Pa == State.of(Decimal("101325"))


@pytest.mark.parametrize(
    "observation_id",
    [
        "ts1985_na2o_table2_log10_a_AT_B",
        "ts1985_na2o_prose_1200C_range",
    ],
)
def test_ts1985_printed_atmosphere_ignores_sibling_range_prose(
    tmp_path, observation_id: str
) -> None:
    result = _migrate_real_extract(tmp_path, "ts1985.yaml")
    matches = [
        observation
        for observation in result.observations.values()
        if f"::{observation_id}" in observation.observation_id
    ]
    assert len(matches) == (4 if observation_id.endswith("log10_a_AT_B") else 1)
    assert all(
        observation.identity.total_pressure_Pa == State.of(Decimal("101325"))
        for observation in matches
    )


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
