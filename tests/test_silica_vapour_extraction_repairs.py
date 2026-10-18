from copy import deepcopy
from decimal import Decimal

import yaml

from simulator.battery.enums import EvidenceClass, StateTag
from simulator.battery.migrate import (
    Migrator,
    REPO_ROOT,
    _mole_fraction_composition_from_values,
    convert_pressure_to_pa,
)
from simulator.battery.validate import validate_corpus
from tools.validate_literature_extracts import check_all_fidelity_samples_match


EXTRACTS = REPO_ROOT / "data/literature/extracts"


def _migrate(name: str):
    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACTS / name)
    migrator.finalize()
    return migrator.result


def _validate_rows(result, observations):
    report = validate_corpus(
        result.works,
        result.experiments,
        observations,
        residuals=None,
    )
    assert report.ok
    assert report.hard_issues == ()


def test_steurer_table_1_sio2_and_table_2_o2_values_migrate_as_models():
    extract = yaml.safe_load(
        (EXTRACTS / "steurer-1985-vapor-phase-pyrolysis.yaml").read_text()
    )
    table1 = extract["species"]["SiO2"]["observations"][0]
    sio_table1 = extract["species"]["SiO"]["observations"][0]
    table2 = extract["species"]["O2"]["observations"][0]
    assert [point["P"] for point in table1["values"]["points"]] == [
        1.92e-04,
        1.77e-01,
        1.53e01,
    ]
    assert [point["P"] for point in table2["values"]["points"]] == [
        3.2e-06,
        1.5e-03,
        8.1e-02,
    ]
    assert [point["P"] for point in sio_table1["values"]["points"]] == [
        7.13e-03,
        3.26,
        1.80e02,
    ]
    assert check_all_fidelity_samples_match(extract) == []
    mutated = deepcopy(extract)
    mutated["species"]["SiO2"]["observations"][0]["values"]["points"][2]["P"] += 1
    assert check_all_fidelity_samples_match(mutated)

    result = _migrate("steurer-1985-vapor-phase-pyrolysis.yaml")
    observations = [
        row
        for row in result.observations.values()
        if "steurer_1985_table1_single_oxide_psat::T=" in row.observation_id
        or "steurer_1985_table1_sio_psat::T=" in row.observation_id
        or "steurer_1985_table2_mixture_psat::T=" in row.observation_id
    ]
    assert len(observations) == 9
    _validate_rows(result, observations)
    table1_rows = [
        row
        for row in observations
        if "table1_single_oxide" in row.observation_id
        or "table1_sio_psat" in row.observation_id
    ]
    assert len(table1_rows) == 6
    for row in table1_rows:
        composition = row.identity.composition
        assert composition is not None and composition.is_value
        assert composition.value.components == (("SiO2", Decimal("1")),)
        assert composition.value.basis == "pure_oxide_system"
        assert row.locator is not None
        assert row.locator.table == "1"
        assert row.locator.published_page == 126
    assert _mole_fraction_composition_from_values(
        {"composition_mol": {"SiO2": 1.0}}
    )[0] is None
    sio_rows = [row for row in table1_rows if row.identity.species.formula == "SiO"]
    assert len(sio_rows) == 3
    assert [row.value.point for row in sio_rows] == [
        convert_pressure_to_pa(Decimal(str(value)), "torr")[0]
        for value in (7.13e-03, 3.26, 1.80e02)
    ]
    assert all(row.evidence.class_.value is EvidenceClass.MODEL_DERIVED for row in sio_rows)
    assert [row.identity.temperature_K.value for row in sio_rows] == [
        Decimal("2000.0"),
        Decimal("2500.0"),
        Decimal("3000.0"),
    ]
    for row in sio_rows:
        condition = row.point_conditions["temperature_K"]
        assert condition.state.value == row.identity.temperature_K.value
        assert condition.locator is not None
        assert condition.locator.table == "1"
    table1_row = next(
        row for row in observations if "table1_single_oxide" in row.observation_id and "T=3000" in row.observation_id
    )
    table2_row = next(
        row for row in observations if "table2_mixture" in row.observation_id and "T=3000" in row.observation_id
    )
    assert table1_row.value.point == convert_pressure_to_pa(Decimal("15.3"), "torr")[0]
    assert table2_row.value.point == convert_pressure_to_pa(Decimal("0.081"), "atm")[0]
    assert table1_row.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    assert table2_row.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    for row in observations:
        condition = row.point_conditions["temperature_K"]
        assert row.identity.temperature_K.is_value
        assert condition.state.tag is StateTag.VALUE
        assert condition.locator is not None
        assert condition.locator.table in {"1", "2"}


def test_yakovlev_1984_o2_table_is_model_derived_with_located_points():
    extract = yaml.safe_load(
        (EXTRACTS / "kems-028-yakovlev-1984.yaml").read_text()
    )
    points = extract["species"]["O2"]["observations"][0]["values"]["points"]
    assert (points[0]["T_C"], points[0]["P"]) == (2020, 1.4e-06)
    assert (points[10]["T_C"], points[10]["P"]) == (2348, 1.9e-04)
    assert all("p_Pa" not in point for point in points)
    assert check_all_fidelity_samples_match(extract) == []
    mutated = deepcopy(extract)
    mutated["species"]["O2"]["observations"][0]["values"]["points"][10]["P"] += 1
    assert check_all_fidelity_samples_match(mutated)

    result = _migrate("kems-028-yakovlev-1984.yaml")
    observations = [
        row
        for row in result.observations.values()
        if "yakovlev_1984_Po2_table_calculated::T=" in row.observation_id
    ]
    assert len(observations) == 11
    _validate_rows(result, observations)
    first = next(row for row in observations if "T=2293.15" in row.observation_id)
    last = next(row for row in observations if "T=2621.15" in row.observation_id)
    assert first.value.point == convert_pressure_to_pa(
        Decimal(str(points[0]["P"])), "torr"
    )[0]
    assert last.value.point == convert_pressure_to_pa(
        Decimal(str(points[10]["P"])), "torr"
    )[0]
    assert all(
        row.evidence.class_.value is EvidenceClass.MODEL_DERIVED
        for row in observations
    )
    for row in observations:
        condition = row.point_conditions["temperature_K"]
        assert row.identity.temperature_K.is_value
        assert condition.state.value == row.identity.temperature_K.value
        assert condition.locator is not None
        assert condition.locator.table == "Po2"
        assert row.point_conditions["composition"].state.is_unknown


def test_yakovlev_shornikov_2011_about_sio_and_o2_values_keep_provenance():
    extract = yaml.safe_load(
        (EXTRACTS / "kems-029-yakovlev-shornikov-2011.yaml").read_text()
    )
    sio = next(
        row
        for row in extract["species"]["SiO"]["observations"]
        if row["observation_id"] == "yakovlev_shornikov_2011_CAI_SiO_highT_about_1e-6_bar"
    )
    o2 = extract["species"]["O2"]["observations"][0]
    assert sio["values"]["p_bar"] == 1e-06
    assert sio["values"]["approximate"] is True
    assert sio["values"]["T_C_range"] == [2000.0, 2400.0]
    assert o2["values"]["points"][0]["p_bar"] == 1.9e-09
    assert o2["values"]["points"][10]["p_bar"] == 2.5e-07
    assert check_all_fidelity_samples_match(extract) == []
    mutated = deepcopy(extract)
    mutated_sio = next(
        row
        for row in mutated["species"]["SiO"]["observations"]
        if row["observation_id"] == "yakovlev_shornikov_2011_CAI_SiO_highT_about_1e-6_bar"
    )
    mutated_sio["values"]["p_bar"] *= 2
    assert check_all_fidelity_samples_match(mutated)

    result = _migrate("kems-029-yakovlev-shornikov-2011.yaml")
    observations = [
        row
        for row in result.observations.values()
        if "yakovlev_shornikov_2011_CAI_SiO_highT" in row.observation_id
        or "yakovlev_shornikov_2011_Po2_table_bar_calculated::T=" in row.observation_id
    ]
    o2_rows = [row for row in observations if row.identity.species.formula == "O2"]
    sio_row = next(row for row in observations if row.identity.species.formula == "SiO")
    assert len(o2_rows) == 11
    assert sio_row.value.point == convert_pressure_to_pa(Decimal("1e-06"), "bar")[0]
    assert sio_row.value.approximate
    assert sio_row.identity.temperature_K.is_unknown
    _validate_rows(result, observations)
    assert all(row.evidence.class_.value is EvidenceClass.MODEL_DERIVED for row in o2_rows)
    for row in o2_rows:
        condition = row.point_conditions["temperature_K"]
        assert row.identity.temperature_K.is_value
        assert condition.state.value == row.identity.temperature_K.value
        assert condition.locator is not None
        assert condition.locator.table == "O2"
        assert row.point_conditions["composition"].state.is_unknown
    first = next(row for row in o2_rows if "T=2293.15" in row.observation_id)
    last = next(row for row in o2_rows if "T=2621.15" in row.observation_id)
    assert first.value.point == convert_pressure_to_pa(Decimal("1.9e-09"), "bar")[0]
    assert last.value.point == convert_pressure_to_pa(Decimal("2.5e-07"), "bar")[0]
