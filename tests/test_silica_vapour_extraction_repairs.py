from copy import deepcopy
import csv
from decimal import Decimal

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Quantity, StateTag
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


def _assert_yakovlev_1984_figure_and_range_pins(extract, audit_rows):
    printed_maxima = {
        "yakovlev_1984_murchison_Fe_max_1450_1725C": ("Fe", 1e-2),
        "yakovlev_1984_murchison_SiO_Mg_max_1750_1825C": ("SiO", 2e-2),
        "yakovlev_1984_murchison_Mg_max_1750_1825C": ("Mg", 8.3e-3),
        "yakovlev_1984_murchison_Ca_max_1950_2025C": ("Ca", 4.4e-4),
    }
    for observation_id, (species, expected_pressure) in printed_maxima.items():
        row = next(
            item for item in extract["species"][species]["observations"]
            if item["observation_id"] == observation_id
        )
        assert float(row["values"]["p_torr"]) == expected_pressure
        assert row["values"]["admission_status"] == "rejected"

    literal_figure_pins = {
        "F1-Ca-07": (2110, 2.4e-04, (1780, 880), (1780, 880)),
        "F1-Mg-02": (1475, 2.4e-05, (868, 1177), (868, 1178)),
        "F2-Ca-08": (2175, 2.9e-03, (1217, 410), (1217, 409)),
        "F2-SiO-09": (1875, 6.3e-03, (678, 292), (680, 291)),
    }

    expected_counts = {
        ("1", "SiO"): 8,
        ("1", "Mg"): 12,
        ("1", "Ca"): 5,
        ("1", "Al"): 3,
        ("2", "SiO"): 18,
        ("2", "Mg"): 8,
        ("2", "Ca"): 10,
        ("2", "Al"): 5,
    }
    assert len(audit_rows) == sum(expected_counts.values()) == 69
    for key, expected_count in expected_counts.items():
        figure, species = key
        sample = "Murchison" if figure == "1" else "CAI"
        observation_id = f"yakovlev_1984_fig{figure}_{sample}_{species}_digitized"
        observation = next(
            item for item in extract["species"][species]["observations"]
            if item["observation_id"] == observation_id
        )
        points = {point["point_id"]: point for point in observation["values"]["points"]}
        rows = [row for row in audit_rows if (row["figure"], row["species"]) == key]
        assert len(rows) == expected_count
        assert len(points) == expected_count
        for row in rows:
            point = points[row["point_id"]]
            assert point["T_C"] == int(row["stored_T_C"])
            assert point["P"] == float(row["stored_p_torr"])
            if row["point_id"] in literal_figure_pins:
                temperature, pressure, read1, read2 = literal_figure_pins[row["point_id"]]
                assert point["T_C"] == temperature
                assert point["P"] == pressure
                assert int(row["stored_T_C"]) == temperature
                assert float(row["stored_p_torr"]) == pressure
                assert (int(row["read1_x_px"]), int(row["read1_y_px"])) == read1
                assert (int(row["read2_x_px"]), int(row["read2_y_px"])) == read2
            assert point["marker_symbol"] == {
                "open_circle": "open circle",
                "open_square": "open square",
                "filled_square": "filled square",
                "open_triangle": "open triangle",
            }[row["marker_symbol"]]
            assert all(row[f"read{index}_{axis}_px"] for index in (1, 2) for axis in ("x", "y"))
            assert float(row["read_difference_decades"]) <= 0.02
            expected_uncertainty = abs(
                float(row["read1_log10_p_torr"])
                - float(row["read2_log10_p_torr"])
            ) / 2
            assert float(row["read_uncertainty_dex"]) == round(expected_uncertainty, 5)
            composition = point["point_conditions"]["composition"]
            assert composition["state"] == {
                "tag": "unknown",
                "reason": "numerical_bulk_composition_not_published",
            }
            assert composition["locator"]["figure"] == figure
            assert composition["locator"]["published_page"] == 946
            assert composition["locator"]["pdf_page_index"] == 2
            note = composition["locator"]["note"]
            assert "log10(p_torr)=" in note
            assert f"({row['read1_x_px']},{row['read1_y_px']})" in note
            assert f"({row['read2_x_px']},{row['read2_y_px']})" in note
            assert "read uncertainty" in note and "dex" in note


def test_yakovlev_1984_printed_maxima_and_digitized_figures_migrate_with_conditions():
    extract_path = EXTRACTS / "kems-028-yakovlev-1984.yaml"
    extract = yaml.safe_load(extract_path.read_text())
    with (EXTRACTS / "kems-028-yakovlev-1984-figure-audit.csv").open(newline="") as file:
        audit_rows = list(csv.DictReader(file))
    _assert_yakovlev_1984_figure_and_range_pins(extract, audit_rows)

    mutated = deepcopy(extract)
    mutated_audit_rows = deepcopy(audit_rows)
    mutated_point = next(
        point
        for observation in mutated["species"]["SiO"]["observations"]
        for point in observation["values"].get("points", [])
        if point["point_id"] == "F2-SiO-09"
    )
    mutated_row = next(row for row in mutated_audit_rows if row["point_id"] == "F2-SiO-09")
    mutated_point["P"] *= 2
    mutated_row["stored_p_torr"] = str(float(mutated_row["stored_p_torr"]) * 2)
    with pytest.raises(AssertionError):
        _assert_yakovlev_1984_figure_and_range_pins(mutated, mutated_audit_rows)
    mutated = deepcopy(extract)
    fe_max = next(
        item for item in mutated["species"]["Fe"]["observations"]
        if item["observation_id"] == "yakovlev_1984_murchison_Fe_max_1450_1725C"
    )
    fe_max["values"]["p_torr"] = float(fe_max["values"]["p_torr"]) * 2
    with pytest.raises(AssertionError):
        _assert_yakovlev_1984_figure_and_range_pins(mutated, audit_rows)

    result = _migrate("kems-028-yakovlev-1984.yaml")
    parent_ids = {
        f"yakovlev_1984_fig{row['figure']}_{'Murchison' if row['figure'] == '1' else 'CAI'}_{row['species']}_digitized"
        for row in audit_rows
    }
    observations = [
        row for row in result.observations.values()
        if any(parent_id in row.observation_id for parent_id in parent_ids)
    ]
    assert len(observations) == 69
    _validate_rows(result, observations)
    for audit_row in audit_rows:
        figure = audit_row["figure"]
        parent_id = (
            f"yakovlev_1984_fig{figure}_"
            f"{'Murchison' if figure == '1' else 'CAI'}_"
            f"{audit_row['species']}_digitized"
        )
        temperature = Decimal(audit_row["stored_T_C"]) + Decimal("273.15")
        matches = [
            row for row in observations
            if parent_id in row.observation_id
            and row.identity.species.formula == audit_row["species"]
            and row.identity.temperature_K.is_value
            and row.identity.temperature_K.value == temperature
        ]
        assert len(matches) == 1, (parent_id, audit_row["point_id"])
        observation = matches[0]
        assert observation.identity.quantity.is_value
        assert observation.identity.quantity.value is Quantity.P_PARTIAL
        assert observation.value.point == convert_pressure_to_pa(
            Decimal(audit_row["stored_p_torr"]), "torr"
        )[0]
        assert observation.evidence.class_.is_value
        assert observation.evidence.class_.value is EvidenceClass.FIGURE_ONLY
        assert observation.point_conditions["temperature_K"].state.value == temperature
        assert observation.point_conditions["temperature_K"].locator.figure == figure
        composition = observation.point_conditions["composition"]
        assert composition.state.is_unknown
        assert composition.locator.figure == figure


def test_yakovlev_shornikov_2011_about_sio_and_o2_values_keep_provenance():
    extract = yaml.safe_load(
        (EXTRACTS / "kems-029-yakovlev-shornikov-2011.yaml").read_text()
    )
    sio = extract["species"]["SiO"]["observations"][0]
    o2 = extract["species"]["O2"]["observations"][0]
    assert sio["values"]["p_bar"] == 1e-06
    assert sio["values"]["approximate"] is True
    assert sio["values"]["T_C_range"] == [2000.0, 2400.0]
    assert o2["values"]["points"][0]["p_bar"] == 1.9e-09
    assert o2["values"]["points"][10]["p_bar"] == 2.5e-07
    assert check_all_fidelity_samples_match(extract) == []
    mutated = deepcopy(extract)
    mutated["species"]["SiO"]["observations"][0]["values"]["p_bar"] *= 2
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
