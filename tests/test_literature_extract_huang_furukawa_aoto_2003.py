from __future__ import annotations

import copy
import re
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import AmountBasis
from simulator.battery.records import ValueKind
from tests.battery.test_migrate import _migrate_real_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/huang-furukawa-aoto-2003-sodium-iron-oxides-kems.yaml"
SOURCE_ID = "huang-furukawa-aoto-2003-sodium-iron-oxides-kems"

TABLE_1 = (
    ("590.6", "4.10E-03", "83575", "4.05E-08"),
    ("606.4", "8.09E-03", "82397", "7.99E-08"),
    ("622.3", "1.69E-02", "80736", "1.67E-07"),
    ("638.2", "3.58E-02", "78816", "3.54E-07"),
    ("654.0", "6.25E-02", "77752", "6.17E-07"),
    ("664.6", "8.91E-02", "77051", "8.79E-07"),
    ("669.9", "1.09E-01", "76554", "1.07E-06"),
    ("675.2", "1.27E-01", "76281", "1.25E-06"),
    ("685.8", "2.40E-01", "73861", "2.37E-06"),
    ("696.4", "3.53E-01", "72751", "3.49E-06"),
    ("701.6", "4.31E-01", "72150", "4.25E-06"),
    ("701.6", "5.31E-01", "70931", "5.24E-06"),
    ("706.9", "6.00E-01", "70746", "5.92E-06"),
    ("712.2", "6.57E-01", "70734", "6.49E-06"),
    ("717.5", "7.27E-01", "70661", "7.17E-06"),
    ("590.6", "3.68E-03", "84112", "3.63E-08"),
    ("601.1", "6.13E-03", "83064", "6.05E-08"),
    ("611.7", "1.01E-02", "81999", "9.95E-08"),
    ("622.3", "1.49E-02", "81397", "1.47E-07"),
    ("632.9", "2.19E-02", "80742", "2.17E-07"),
    ("643.5", "3.35E-02", "79834", "3.30E-07"),
    ("654.0", "5.19E-02", "78763", "5.12E-07"),
    ("664.6", "8.50E-02", "77306", "8.39E-07"),
    ("675.2", "1.20E-01", "76601", "1.18E-06"),
    ("685.8", "1.80E-01", "75495", "1.78E-06"),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _source_observation(document: dict, species: str, observation_id: str) -> dict:
    return next(
        row
        for row in document["species"][species]["observations"]
        if row["observation_id"] == observation_id
    )


def _table_row(observation) -> int:
    match = re.search(r":row=(\d+):h=", observation.observation_id)
    assert match is not None, observation.observation_id
    return int(match.group(1))


def _migrated_observation(result, observation_id: str):
    return next(
        row
        for row in result.observations.values()
        if row.observation_id.endswith(f"::{observation_id}")
    )


def test_table_1_transcription_render_pins_and_interior_mutation() -> None:
    document = _document()
    assert len(TABLE_1) == 25
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []

    pressure = _source_observation(
        document, "Na", "huang2003_table1_na_partial_pressure"
    )
    gibbs = _source_observation(
        document, "Na3FeO3", "huang2003_table1_reaction_gibbs_typed_refusal"
    )
    kp = _source_observation(
        document, "Na3FeO3", "huang2003_table1_kp_typed_refusal"
    )
    assert kp["units"] == "not_printed"
    assert len(pressure["values"]["points"]) == 25
    assert len(gibbs["values"]["points"]) == 25
    assert len(kp["values"]["points"]) == 25
    assert pressure["locator"]["page"] == 5
    assert pressure["locator"]["table"] == "1"

    for row_number, (temperature, pressure_pa, reaction_gibbs, equilibrium) in enumerate(
        TABLE_1, start=1
    ):
        p_point = pressure["values"]["points"][row_number - 1]
        g_point = gibbs["values"]["points"][row_number - 1]
        k_point = kp["values"]["points"][row_number - 1]
        assert p_point["row_number_as_printed"] == str(row_number)
        assert p_point["T_K"] == temperature
        assert p_point["partial_pressure_pa"] == pressure_pa
        assert g_point["T_K"] == temperature
        assert g_point["value"] == reaction_gibbs
        assert k_point["T_K"] == temperature
        assert k_point["value"] == equilibrium
        for point, column in ((p_point, 3), (g_point, 4), (k_point, 5)):
            assert point["locator"]["page"] == 5
            assert point["locator"]["table"] == "1"
            assert point["locator"]["row"] == row_number
            assert point["locator"]["column"] == column

    # These rendered cells pin both ends and an interior Table 1 row.
    for sample_index in (0, 1, 2, 3):
        assert document["fidelity_samples"][sample_index]["locator"]["page"] == 5
        assert document["fidelity_samples"][sample_index]["locator"]["table"] == "1"
    assert document["fidelity_samples"][1]["locator"]["row"] == 13

    mutated = copy.deepcopy(document)
    mutated_pressure = _source_observation(
        mutated, "Na", "huang2003_table1_na_partial_pressure"
    )
    mutated_pressure["values"]["points"][12]["partial_pressure_pa"] = "6.01E-01"
    assert check_all_fidelity_samples_match(mutated, label="mutated Table 1 row 13")


def test_isolated_migration_binds_all_numeric_rows_and_conditions(tmp_path: Path) -> None:
    document = _document()
    for species, observation_id in (
        ("Na", "huang2003_table1_na_partial_pressure"),
        ("Na3FeO3", "huang2003_table1_reaction_gibbs_typed_refusal"),
        ("Na3FeO3", "huang2003_table1_kp_typed_refusal"),
    ):
        source_row = _source_observation(document, species, observation_id)
        assert source_row["point_conditions"]["total_pressure_Pa"]["state"]["tag"] == "unknown"
        assert source_row["point_conditions"]["fO2_Pa"]["state"]["tag"] == "unknown"

    result = _migrate_real_extract(
        tmp_path,
        EXTRACT.name,
        write=True,
        extract_doc=document,
    )
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 77
    migrated = list(result.observations.values())
    assert sum(row.value.kind is ValueKind.POINT for row in migrated) == 27
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated) == 50
    assert not any(row.identity.species.phase.is_unknown for row in migrated)

    pressure_rows = sorted(
        (row for row in migrated if "::huang2003_table1_na_partial_pressure::" in row.observation_id),
        key=_table_row,
    )
    gibbs_rows = sorted(
        (row for row in migrated if "::huang2003_table1_reaction_gibbs_typed_refusal::" in row.observation_id),
        key=_table_row,
    )
    kp_rows = sorted(
        (row for row in migrated if "::huang2003_table1_kp_typed_refusal::" in row.observation_id),
        key=_table_row,
    )
    assert len(pressure_rows) == len(gibbs_rows) == len(kp_rows) == 25

    for row_number, (temperature, pressure_pa, reaction_gibbs, equilibrium) in enumerate(
        TABLE_1, start=1
    ):
        p_row, g_row, k_row = pressure_rows[row_number - 1], gibbs_rows[row_number - 1], kp_rows[row_number - 1]
        assert _table_row(p_row) == _table_row(g_row) == _table_row(k_row) == row_number
        assert p_row.value.kind is ValueKind.POINT
        assert p_row.value.point == Decimal(pressure_pa)
        assert p_row.identity.quantity.value.value == "p_partial"
        assert p_row.identity.species.formula == "Na"
        assert p_row.identity.species.phase.value.value == "g"
        assert (p_row.locator.page, p_row.locator.table) == (5, "1")
        assert p_row.identity.temperature_K.value == Decimal(temperature)
        assert p_row.point_conditions["temperature_K"].state.value.point == Decimal(temperature)
        assert p_row.point_conditions["temperature_K"].locator.page == 5
        assert p_row.identity.composition.is_value
        composition = p_row.identity.composition.value
        assert composition.amount_basis is AmountBasis.MOL_INVENTORY
        assert dict(composition.components) == {
            "Na2O": Decimal("2"),
            "FeO": Decimal("1"),
        }
        assert p_row.point_conditions["composition"].state.value == composition
        assert p_row.identity.total_pressure_Pa.is_unknown
        assert p_row.identity.fO2_Pa.is_unknown
        assert "total_pressure_Pa" not in p_row.point_conditions
        assert g_row.value.kind is ValueKind.UNAVAILABLE
        assert k_row.value.kind is ValueKind.UNAVAILABLE
        assert g_row.identity.quantity.is_unknown
        assert k_row.identity.quantity.is_unknown
        assert g_row.identity.temperature_K.value == Decimal(temperature)
        assert k_row.identity.temperature_K.value == Decimal(temperature)
        assert g_row.point_conditions["temperature_K"].state.value.point == Decimal(temperature)
        assert k_row.point_conditions["temperature_K"].state.value.point == Decimal(temperature)
        assert g_row.identity.composition.is_value
        assert k_row.identity.composition.is_value
        assert g_row.point_conditions["composition"].state.value == g_row.identity.composition.value
        assert k_row.point_conditions["composition"].state.value == k_row.identity.composition.value
        assert g_row.identity.total_pressure_Pa is None
        assert k_row.identity.total_pressure_Pa is None
        assert g_row.identity.fO2_Pa is None
        assert k_row.identity.fO2_Pa is None
        assert "total_pressure_Pa" not in g_row.point_conditions
        assert "total_pressure_Pa" not in k_row.point_conditions
        assert g_row.identity.species.phase.value.value == "cr"
        assert k_row.identity.species.phase.value.value == "cr"
        assert g_row.value.kind is ValueKind.UNAVAILABLE and reaction_gibbs.isdigit()
        assert k_row.value.kind is ValueKind.UNAVAILABLE and Decimal(equilibrium) > 0

    expected_unknown = {
        row.observation_id
        for row in migrated
        if "::huang2003_table1_reaction_gibbs_typed_refusal::" in row.observation_id
        or "::huang2003_table1_kp_typed_refusal::" in row.observation_id
    }
    assert len(expected_unknown) == 50
    assert {row.observation_id for row in migrated if row.identity.quantity.is_unknown} == expected_unknown

    for observation_id, expected_value, page in (
        ("huang2003_na_gas_delta_fh_298_15_this_work", "107.6", 3),
        ("huang2003_na_gas_delta_fh_298_15_nist_janaf", "107.3", 3),
    ):
        row = _migrated_observation(result, observation_id)
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == Decimal(expected_value)
        assert row.identity.temperature_K.value == Decimal("298.15")
        assert row.point_conditions["temperature_K"].state.value == Decimal("298.15")
        assert row.point_conditions["temperature_K"].locator.page == page
        assert row.locator.page == page
        assert row.locator.section == "2.2 Sample preparation and pressure calibration"
        assert not row.identity.total_pressure_Pa.is_value

    measured_enthalpy = _migrated_observation(
        result, "huang2003_na_gas_delta_fh_298_15_this_work"
    )
    assert measured_enthalpy.uncertainty.kind.value == "printed"
    assert measured_enthalpy.uncertainty.verbatim["value"] == "0.8"
    assert measured_enthalpy.uncertainty.verbatim["confidence_level"] == "not_stated"
