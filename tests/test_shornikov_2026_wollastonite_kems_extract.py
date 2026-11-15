from __future__ import annotations

import copy
import subprocess
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests.helpers.corpus_paths import regolith_corpus_root
from simulator.battery.enums import (
    EvidenceClass,
    Phase,
    Quantity,
    RawInputStatus,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.migrate import Migrator, REPO_ROOT
from simulator.battery.score import (
    _initial_charge_composition_notice,
    rail_for_quantity,
)
from simulator.battery.waypoints import consumer_readiness
from tests.literature_extract_test_helpers import iter_locators


SID = "shornikov-2026-wollastonite-kems"
EXTRACT = REPO_ROOT / f"data/literature/extracts/{SID}.yaml"
CORPUS_ROOT = regolith_corpus_root()
PDF = CORPUS_ROOT / "raw" / SID / f"{SID}.pdf"

if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

import validate_literature_extracts as validator  # noqa: E402


# Independent transcription of all Table 3 cells from the 600 dpi render of
# physical PDF page 11. None represents a printed dash; no blank is filled.
TABLE3_RENDER = (
    ("1751", "1.90e-10", None, "1.77e-6", "1.05e-9", "3.68e-9", "6.01e-12", "3.32e-11", None, "2.05e-11", "7.67e-10", "5.71e-10"),
    ("1783", "3.78e-10", None, "3.35e-6", "2.19e-9", "7.50e-9", "1.24e-11", "9.35e-11", "2.88e-12", "4.91e-11", "1.70e-9", "1.13e-9"),
    ("1800", "5.60e-10", None, "4.81e-6", "3.15e-9", "1.10e-8", "1.73e-11", "1.65e-10", "4.04e-12", "6.43e-11", "1.99e-9", "1.36e-9"),
    ("1815", "7.59e-10", None, "6.38e-6", "4.42e-9", "1.48e-8", "2.41e-11", "2.53e-10", "5.89e-12", "9.48e-11", "3.01e-9", "2.01e-9"),
    ("1833", "1.03e-9", None, "8.53e-6", "6.45e-9", "2.11e-8", "3.56e-11", "4.25e-10", "9.06e-12", "1.49e-10", "4.54e-9", "2.84e-9"),
    ("1859", "1.72e-9", None, "1.40e-5", "1.09e-8", "3.39e-8", "5.66e-11", "8.63e-10", "1.49e-11", "2.59e-10", "6.27e-9", "3.94e-9"),
    ("1885", "2.77e-9", None, "2.19e-5", "1.81e-8", "5.20e-8", "9.46e-11", "1.73e-9", "2.80e-11", "4.26e-10", "1.06e-8", "6.22e-9"),
    ("1910", "4.29e-9", "2.55e-12", "3.38e-5", "2.95e-8", "8.51e-8", "1.52e-10", "3.32e-9", "4.71e-11", "6.95e-10", "1.66e-8", "9.42e-9"),
    ("1933", "6.37e-9", "4.18e-12", "4.93e-5", "4.53e-8", "1.22e-7", "2.37e-10", "5.86e-9", "8.32e-11", "1.32e-9", "2.92e-8", "1.52e-8"),
    ("1970", "1.13e-8", "9.44e-12", "8.37e-5", "8.88e-8", "2.10e-7", "4.94e-10", "1.56e-8", "1.97e-10", "3.08e-9", "5.85e-8", "2.98e-8"),
    ("1988", "1.53e-8", "1.38e-11", "1.11e-4", "1.21e-7", "2.77e-7", "6.87e-10", "2.29e-8", "2.77e-10", "4.32e-9", "7.98e-8", "3.88e-8"),
    ("2025", "2.72e-8", "2.94e-11", "1.96e-4", "2.21e-7", "5.01e-7", "1.29e-9", "5.28e-8", "5.82e-10", "8.75e-9", "1.45e-7", "6.70e-8"),
    ("2066", "5.30e-8", "6.50e-11", "3.71e-4", "4.28e-7", "1.01e-6", "2.28e-9", "1.23e-7", "1.08e-9", "1.59e-8", "2.36e-7", "1.05e-7"),
    ("2087", "6.98e-8", "9.66e-11", "4.87e-4", "5.95e-7", "1.35e-6", "3.36e-9", "2.03e-7", "1.67e-9", "2.48e-8", "3.69e-7", "1.62e-7"),
)
TABLE3_SPECIES = ("Ca", "CaO", "SiO", "SiO2", "Si2O2", "O2", "CaSiO3", "Mo", "MoO", "MoO2", "MoO3")

# Independent transcription of every Table 5 row from the 600 dpi render of
# physical PDF page 13. The rendered table contains fit intervals, not point T.
TABLE5_RENDER = (
    ("1", "O", "1751", "1817", "-27820 ± 392", "6.164 ± 0.219"),
    ("2", "O", "1817", "2087", "-28311 ± 146", "6.444 ± 0.075"),
    ("3", "O2", "1751", "1817", "-29787 ± 440", "5.792 ± 0.246"),
    ("4", "O2", "1817", "2087", "-30049 ± 278", "5.929 ± 0.143"),
    ("5", "Ca", "1751", "1817", "-29978 ± 426", "7.397 ± 0.238"),
    ("6", "Ca", "1817", "2087", "-27468 ± 126", "6.007 ± 0.065"),
    ("7", "CaO", "1910", "2087", "-35633 ± 92", "7.061 ± 0.046"),
    ("8", "SiO", "1751", "1817", "-27767 ± 347", "10.104 ± 0.194"),
    ("9", "SiO", "1817", "2087", "-26284 ± 161", "9.280 ± 0.082"),
    ("10", "SiO2", "1751", "1817", "-30893 ± 209", "8.664 ± 0.117"),
    ("11", "SiO2", "1817", "2087", "-29601 ± 95", "7.964 ± 0.049"),
    ("12", "Si2O2", "1751", "1817", "-30142 ± 323", "8.781 ± 0.181"),
    ("13", "Si2O2", "1817", "2087", "-27068 ± 270", "7.084 ± 0.139"),
    ("14", "CaSiO3", "1751", "1817", "-44065 ± 573", "14.687 ± 0.321"),
    ("15", "CaSiO3", "1817", "2087", "-40293 ± 215", "12.617 ± 0.110"),
)

# Table 7 literature rows, physical PDF page 16, pinned against the rendered
# cells. The paper supplies no confidence interpretation for these widths.
TABLE7_RENDER = (
    ("1", "1785", "20", "28.03", "3.14"),
    ("2", "1813", "5", "24.18", "2.50"),
    ("3", "1813", "5", "28.45", "2.43"),
    ("4", "1815", "15", "30.75", "3.30"),
    ("5", "1817", "15", "25.03", "2.43"),
    ("6", "1817", "5", "33.93", "0.54"),
    ("7", "1817", "5", "31.38", "2.09"),
    ("8", "1817", "5", "28.66", "1.50"),
    ("9", "1821", "3", "28.50", "2.50"),
    ("10", "1821", "3", "28.65", "0.15"),
    ("11", "1809", "1", "26.75", "0.01"),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _table3_grid(document: dict) -> tuple[tuple[str | None, ...], ...]:
    rows: list[list[str | None]] = [
        [temperature, *([None] * len(TABLE3_SPECIES))]
        for temperature, *_ in TABLE3_RENDER
    ]
    for column, formula in enumerate(TABLE3_SPECIES, start=1):
        for observation in document["species"][formula]["observations"]:
            if not observation["observation_id"].startswith(
                f"shornikov_2026_{formula}_partial_pressure_table3_row_"
            ):
                continue
            row_index = int(observation["locator"]["row"]) - 1
            rows[row_index][0] = str(observation["values"]["T_K_as_printed"])
            rows[row_index][column] = str(observation["values"]["pressure_atm"])
    return tuple(tuple(row) for row in rows)


def _table5_grid(document: dict) -> tuple[tuple[str, ...], ...]:
    rows = []
    for formula, block in document["species"].items():
        for observation in block.get("observations", []):
            if not observation["observation_id"].startswith("shornikov_2026_table5_"):
                continue
            relation = observation["values"]["pressure_temperature_relation"]
            rows.append(
                (
                    str(observation["locator"]["row"]),
                    formula,
                    *(str(value) for value in observation["T_range_K"]),
                    str(relation["A_as_printed"]),
                    str(relation["B_as_printed"]),
                )
            )
    return tuple(sorted(rows, key=lambda row: int(row[0])))


def _table7_grid(document: dict) -> tuple[tuple[str, ...], ...]:
    observations = [
        observation
        for block in document["species"].values()
        for observation in block.get("observations", [])
        if observation.get("locator", {}).get("table") == "7"
    ]
    rows = []
    for row in sorted(
        {str(observation["locator"]["row"]) for observation in observations},
        key=int,
    ):
        melting = next(
            observation
            for observation in observations
            if observation["locator"]["row"] == row
            and observation["values"]["quantity"] == "transition_temperature"
        )
        fusion = next(
            observation
            for observation in observations
            if observation["locator"]["row"] == row
            and observation["values"]["quantity"] == "heat_of_fusion"
        )
        rows.append(
            (
                row,
                str(melting["values"]["T_K"]),
                str(melting["uncertainty"]["value"]),
                str(melting["uncertainty"]["value_as_published"]),
                str(fusion["values"]["delta_H_fus_kJ_mol-1"]),
                str(fusion["uncertainty"]["value"]),
                str(fusion["uncertainty"]["value_as_published"]),
            )
        )
    return tuple(rows)


def _source_observations(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        observation["observation_id"]: (formula, observation)
        for formula, block in document["species"].items()
        for observation in block.get("observations", [])
    }


def test_table3_render_grid_and_both_cell_mutations_are_detected() -> None:
    document = _document()
    actual = _table3_grid(document)
    assert actual == TABLE3_RENDER
    assert len(actual) == 14
    assert sum(value is not None for row in actual for value in row[1:]) == 146

    changed_render = list(TABLE3_RENDER)
    first_row = list(changed_render[0])
    first_row[1] = "1.91e-10"
    changed_render[0] = tuple(first_row)
    with pytest.raises(AssertionError):
        assert actual == tuple(changed_render)

    changed_extract = copy.deepcopy(document)
    first_ca_row = next(
        observation
        for observation in changed_extract["species"]["Ca"]["observations"]
        if observation["observation_id"].endswith("_row_01")
    )
    first_ca_row["values"]["pressure_atm"] = "1.91e-10"
    with pytest.raises(AssertionError):
        assert _table3_grid(changed_extract) == TABLE3_RENDER


def test_table5_render_rows_and_both_cell_mutations_are_detected() -> None:
    document = _document()
    actual = _table5_grid(document)
    assert actual == TABLE5_RENDER
    assert len(actual) == 15
    for row, *_ in actual:
        observation = next(
            source
            for block in document["species"].values()
            for source in block.get("observations", [])
            if source.get("locator", {}).get("row") == row
            and source.get("locator", {}).get("table") == "5"
        )
        assert observation["locator"]["page"] == 13
        assert observation["locator"]["pdf_page_index"] == 12
        temperature = observation["point_conditions"]["temperature_K"]
        assert temperature["locator"]["column_header"] == "T, K"
        assert temperature["state"]["tag"] == "value"
        assert temperature["state"]["value"]["kind"] == "interval"
        assert Decimal(str(temperature["state"]["value"]["interval_low"])) == Decimal(
            str(observation["T_range_K"][0])
        )
        assert Decimal(str(temperature["state"]["value"]["interval_high"])) == Decimal(
            str(observation["T_range_K"][1])
        )

    changed_render = list(TABLE5_RENDER)
    changed_render[0] = (*changed_render[0][:4], "-27821 ± 392", *changed_render[0][5:])
    with pytest.raises(AssertionError):
        assert actual == tuple(changed_render)

    changed_extract = copy.deepcopy(document)
    first_fit = next(
        observation
        for observation in changed_extract["species"]["O"]["observations"]
        if observation["observation_id"] == "shornikov_2026_table5_o_low"
    )
    first_fit["values"]["pressure_temperature_relation"]["A_as_printed"] = "-27821 ± 392"
    with pytest.raises(AssertionError):
        assert _table5_grid(changed_extract) == TABLE5_RENDER


def test_table7_render_uncertainties_and_mutation_are_pinned() -> None:
    document = _document()
    actual = _table7_grid(document)
    expected = tuple(
        (
            row,
            temperature,
            sigma_temperature,
            f"{temperature} ± {sigma_temperature}",
            enthalpy,
            sigma_enthalpy,
            f"{enthalpy} ± {sigma_enthalpy}",
        )
        for row, temperature, sigma_temperature, enthalpy, sigma_enthalpy in TABLE7_RENDER
    )
    assert actual == expected
    assert len(actual) == 11
    for block in document["species"].values():
        for observation in block.get("observations", []):
            if observation.get("locator", {}).get("table") != "7":
                continue
            assert observation["locator"]["page"] == 16
            assert observation["locator"]["pdf_page_index"] == 15
            uncertainty = observation["uncertainty"]
            assert uncertainty["confidence_level"] == "not_stated"
            assert uncertainty["units"] == observation["units"]

    changed = copy.deepcopy(document)
    first = next(
        observation
        for block in changed["species"].values()
        for observation in block.get("observations", [])
        if observation.get("observation_id")
        == "shornikov_2026_table7_row_01_melting_temperature"
    )
    first["uncertainty"]["value"] = "21"
    with pytest.raises(AssertionError):
        assert _table7_grid(changed) == expected


def _local_id(observation_id: str) -> str:
    return observation_id.split("::", 2)[1]


def _family(local_id: str) -> str:
    if "partial_pressure_table3" in local_id:
        return "table3"
    if "table4_p_o_" in local_id:
        return "table4"
    if local_id.startswith("shornikov_2026_table5_"):
        return "table5"
    if local_id.startswith("shornikov_2026_table6_"):
        return "table6"
    if local_id.startswith("shornikov_2026_table7_"):
        return "table7"
    return "other"


def test_isolated_migration_carries_each_species_and_pressure_condition() -> None:
    document = _document()
    source_rows = _source_observations(document)
    stored_ids = set(source_rows)
    for record in (
        block.get("context", []) for block in document["species"].values()
    ):
        stored_ids.update(item["observation_id"] for item in record)
    for blocked in (
        "shornikov_2026_CaO_activity_prose",
        "shornikov_2026_SiO2_activity_prose",
        "shornikov_2026_table4_atomic_oxygen_calculations",
        "shornikov_2026_table5_pressure_fit_coefficients",
        "shornikov_2026_table6_reaction_thermodynamics_schema_gap",
    ):
        assert blocked not in stored_ids
    overlap = next(
        record
        for block in document["species"].values()
        for record in block.get("context", [])
        if record["observation_id"] == "shornikov_2026_overlap_with_2024_chapter"
    )
    shared = overlap["values"]["shared_record_ids_in_the_2024_extract"]
    assert "shornikov_2024_cao_activity_prose" in shared
    assert "shornikov_2024_sio2_activity_prose" in shared
    assert "0.015" not in overlap["values"]["kept_here"]
    assert "0.20" not in overlap["values"]["kept_here"]

    sio_fit = source_rows["shornikov_2026_table5_sio_high"][1]
    sio_relation = sio_fit["values"]["pressure_temperature_relation"]
    assert sio_relation["B_as_printed"] == "9.280 ± 0.082"
    assert Decimal(str(sio_relation["B"])) == Decimal("9.280")
    assert Decimal(str(sio_relation["sigma_B"])) == Decimal("0.082")
    casio3_fit = source_rows["shornikov_2026_table5_casio3_high"][1]
    casio3_relation = casio3_fit["values"]["pressure_temperature_relation"]
    assert casio3_relation["B_as_printed"] == "12.617 ± 0.110"
    assert Decimal(str(casio3_relation["B"])) == Decimal("12.617")
    assert Decimal(str(casio3_relation["sigma_B"])) == Decimal("0.110")

    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    migrated = [row for row in result.observations.values() if row.source_id == SID]
    migrated_ids = {row.observation_id for row in migrated}

    # 146 Table 3 cells + 90 Table 4 points + 15 Table 5 fits
    # + 4 Table 6 refusals + 22 Table 7 literature rows.
    assert len(migrated) == 277
    assert not result.registry_issues
    raw_input_source_ids = {
        local_id
        for local_id, (_, source) in source_rows.items()
        if (
            (source.get("values") or {}).get("derivation") or {}
        ).get("raw_inputs", {}).get("state", {}).get("value")
        == "not_printed"
    }
    assert len(raw_input_source_ids) == 10
    for local_id in raw_input_source_ids:
        rows = [
            row
            for row in migrated
            if row.observation_id == f"{SID}::{local_id}"
            or row.observation_id.startswith(f"{SID}::{local_id}::")
        ]
        assert rows
        for row in rows:
            assert row.derivation is not None
            raw_inputs = row.derivation.raw_inputs
            assert raw_inputs is not None
            assert raw_inputs.state.is_value
            assert raw_inputs.state.value is RawInputStatus.NOT_PRINTED
            assert raw_inputs.locator is not None
            assert raw_inputs.locator.page is not None
            method = row.derivation.method
            assert method is not None
            assert method.locator is not None
            assert method.locator.equation is not None
            assert row.derivation.references
    families = {_family(_local_id(row.observation_id)): 0 for row in migrated}
    for row in migrated:
        families[_family(_local_id(row.observation_id))] += 1
    assert families == {
        "table3": 146,
        "table4": 90,
        "table5": 15,
        "table6": 4,
        "table7": 22,
    }

    for row in migrated:
        local_id = _local_id(row.observation_id)
        container, source = source_rows[local_id]
        expected_formula = source["values"].get("formula", container)
        assert row.identity.species.formula == expected_formula

    pressure_rows = [
        row for row in migrated if "partial_pressure_table3" in row.observation_id
    ]
    assert len(pressure_rows) == 146
    experiment = next(
        item
        for item in result.experiments.values()
        if item.experiment_id.endswith(
            "::experiment::shornikov-2026-wollastonite-kems-runset"
        )
    )
    bench = result.benches[experiment.bench_id]
    temperature_accuracy = bench.temperature_measurement["accuracy"]
    assert temperature_accuracy.state.is_unknown
    assert temperature_accuracy.state.reason == "not_published"
    assert temperature_accuracy.locator.page == 9
    assert "no unit for 10" in temperature_accuracy.locator.note
    for row in pressure_rows:
        assert row.identity.quantity.value is Quantity.P_PARTIAL
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value is Phase.G
        assert row.identity.temperature_K.is_value
        assert row.identity.composition.is_value
        assert row.identity.composition.value.proxy_flag == "initial_charge_only"
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.point_conditions is not None
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["temperature_K"].locator.table == "3"
        assert row.point_conditions["oxygen_partial_pressure"].state.value.kind is ValueKind.POINT
        oxygen = row.point_conditions["fO2_Pa"]
        assert oxygen.state.is_value
        assert oxygen.state.value.kind is ValueKind.POINT
        assert oxygen.locator.table == "3"
        assert "column_header=pO2" in oxygen.locator.note
        assert row.point_conditions["composition"].state.is_value
        assert row.uncertainty.kind is UncertaintyKind.PRINTED
        assert row.uncertainty.verbatim["bound_value"] == "7%"
        assert row.uncertainty.verbatim["confidence_level"] == "not_stated"
        assert _initial_charge_composition_notice(row) is not None

    render_row = TABLE3_RENDER[0]
    pinned_pressure_values = (
        ("Ca", "shornikov_2026_Ca_partial_pressure_table3_row_01", render_row[1]),
        ("SiO", "shornikov_2026_SiO_partial_pressure_table3_row_01", render_row[3]),
        ("SiO2", "shornikov_2026_SiO2_partial_pressure_table3_row_01", render_row[4]),
    )
    pressure_by_id = {row.observation_id.split("::", 1)[1]: row for row in pressure_rows}
    for formula, local_id, pressure_atm in pinned_pressure_values:
        assert formula in TABLE3_SPECIES
        row = pressure_by_id[local_id]
        assert row.value.point == Decimal(pressure_atm) * Decimal("101325")
        assert row.identity.temperature_K.value == Decimal("1751")
        assert row.identity.fO2_Pa.is_value
        assert row.identity.fO2_Pa.value == Decimal("6.01e-12") * Decimal("101325")
        assert row.identity.total_pressure_Pa.is_unknown

    table4 = [row for row in migrated if _family(_local_id(row.observation_id)) == "table4"]
    hertz_knudsen = [row for row in table4 if "table4_p_o_hk" in row.observation_id]
    equilibrium = [row for row in table4 if "table4_p_o_eq" in row.observation_id]
    assert len(hertz_knudsen) == 14
    assert len(equilibrium) == 76
    for row in hertz_knudsen:
        assert row.evidence.class_.value is EvidenceClass.MEASURED_TABULATED
        assert row.identity.temperature_K.is_value
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["temperature_K"].locator is not None
        assert row.identity.composition.is_value
        assert set(row.identity.composition.value.as_map()) == {"CaO", "SiO2"}
        assert row.uncertainty.verbatim["confidence_level"] == "not_stated"
    for row in equilibrium:
        assert row.evidence.class_.value is EvidenceClass.MODEL_DERIVED
        assert row.identity.temperature_K.is_value
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["temperature_K"].locator is not None

    fits = [row for row in migrated if _family(_local_id(row.observation_id)) == "table5"]
    assert len(fits) == 15
    expected_table5_ids = {
        f"shornikov_2026_table5_{species}_{side}"
        for species, sides in (
            ("o", ("low", "high")),
            ("o2", ("low", "high")),
            ("ca", ("low", "high")),
            ("sio", ("low", "high")),
            ("sio2", ("low", "high")),
            ("si2o2", ("low", "high")),
            ("casio3", ("low", "high")),
        )
        for side in sides
    } | {"shornikov_2026_table5_cao"}
    assert {_local_id(row.observation_id) for row in fits} == expected_table5_ids
    for row in fits:
        local_id = _local_id(row.observation_id)
        _, source = source_rows[local_id]
        assert row.derived_from
        assert all(parent in migrated_ids for parent in row.derived_from)
        assert row.derivation is not None
        assert row.value.kind is ValueKind.UNAVAILABLE
        assert row.value.unavailable_reason is not None
        assert "no mapped numeric p_partial selected" in row.value.unavailable_reason
        assert "is a temperature domain, not the observable" in row.value.unavailable_reason
        assert row.identity.temperature_K.is_unknown
        assert row.identity.temperature_K.reason is not None
        assert "T_range_K" in row.identity.temperature_K.reason
        assert "no midpoint invented" in row.identity.temperature_K.reason
        assert row.point_conditions is not None
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value.kind is ValueKind.INTERVAL
        assert temperature.locator is not None
        assert temperature.locator.table == "5"
        assert temperature.locator.note is not None
        assert f"row={source['locator']['row']}" in temperature.locator.note
        source_temperature = source["point_conditions"]["temperature_K"]["state"]["value"]
        assert temperature.state.value.interval_low == Decimal(source_temperature["interval_low"])
        assert temperature.state.value.interval_high == Decimal(source_temperature["interval_high"])
        assert row.identity.composition.is_value
        assert row.identity.composition.value.proxy_flag == "initial_charge_only"
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa.is_unknown
        assert row.uncertainty.kind is UncertaintyKind.PRINTED
        assert row.uncertainty.verbatim["confidence_level"] == "not_stated"
        assert _initial_charge_composition_notice(row) is not None
        if local_id in {"shornikov_2026_table5_o_low", "shornikov_2026_table5_o_high"}:
            assert all("table4_p_o_hk" in parent for parent in row.derived_from)
        else:
            assert all("partial_pressure_table3" in parent for parent in row.derived_from)

    experiment_readiness = Counter()
    engine_readiness = Counter()
    engine_gaps = Counter()
    for family, family_rows in (
        ("table3", pressure_rows),
        ("table4", table4),
        ("table5", fits),
    ):
        for row in family_rows:
            readiness = consumer_readiness(experiment, bench, row)
            kems = next(item for item in readiness if item.consumer == "kems")
            engine = next(
                item
                for item in readiness
                if item.consumer == "engine_point" and item.engine == "magemin"
            )
            experiment_readiness[(family, kems.status.value)] += 1
            engine_readiness[(family, engine.status.value)] += 1
            for gap in engine.gaps:
                for missing in gap.missing:
                    engine_gaps[(family, missing)] += 1
    assert experiment_readiness == Counter(
        {("table3", "gap"): 146, ("table4", "gap"): 90, ("table5", "gap"): 15}
    )
    assert engine_readiness == Counter(
        {("table3", "gap"): 146, ("table4", "gap"): 90, ("table5", "gap"): 15}
    )
    assert engine_gaps == Counter(
        {
            ("table3", "experiment.pressure_environment.total_pressure_Pa"): 146,
            ("table3", "bench.other_facts.gas_load_Pa_m3_s"): 146,
            ("table3", "bench.pumping_speed_m3_s"): 146,
            ("table4", "experiment.pressure_environment.total_pressure_Pa"): 90,
            ("table4", "bench.other_facts.gas_load_Pa_m3_s"): 90,
            ("table4", "bench.pumping_speed_m3_s"): 90,
            ("table4", "fO2_log"): 90,
            ("table4", "experiment.fO2_control"): 90,
            ("table4", "total_pressure_Pa"): 90,
            ("table5", "temperature_K"): 15,
            ("table5", "experiment.pressure_environment.total_pressure_Pa"): 15,
            ("table5", "bench.other_facts.gas_load_Pa_m3_s"): 15,
            ("table5", "bench.pumping_speed_m3_s"): 15,
            ("table5", "fO2_log"): 15,
            ("table5", "experiment.fO2_control"): 15,
            ("table5", "total_pressure_Pa"): 15,
        }
    )
    rails = Counter(
        rail_for_quantity(
            row.identity.quantity.value,
            species_formula=row.identity.species.formula,
        ).value
        for row in (*pressure_rows, *table4, *fits)
    )
    assert rails == Counter({"vapour": 219, "SiO_evolution": 32})

    reactions = [row for row in migrated if _family(_local_id(row.observation_id)) == "table6"]
    assert {
        _local_id(row.observation_id) for row in reactions
    } == {
        "shornikov_2026_table6_sublimation_enthalpy",
        "shornikov_2026_table6_sublimation_entropy",
        "shornikov_2026_table6_dissociation_enthalpy",
        "shornikov_2026_table6_dissociation_entropy",
    }
    for row in reactions:
        assert row.value.kind is ValueKind.UNAVAILABLE
        reason = row.value.unavailable_reason or ""
        assert "refusing to pick a number" in reason
        assert "unsupported quantity" in reason
        local_id = _local_id(row.observation_id)
        source_quantity = source_rows[local_id][1]["values"]["quantity"]
        if "enthalpy" in local_id:
            assert source_quantity == "reaction_enthalpy_of_named_reaction"
        else:
            assert source_quantity == "reaction_entropy_of_named_reaction"
        assert row.identity.quantity.is_unknown
        assert row.derivation is not None
        assert row.derivation.method is not None
        assert row.derivation.method.locator is not None
        assert row.derivation.method.locator.page == 12
        assert row.derivation.method.locator.pdf_page_index == 11
        assert row.derivation.method.locator.published_page == 619
        assert row.derivation.method.locator.equation == "(21)"
        assert row.identity.temperature_K.is_unknown
        assert row.identity.temperature_K.reason is not None
        assert "T_range_K" in row.identity.temperature_K.reason
        assert "no midpoint invented" in row.identity.temperature_K.reason
        assert row.point_conditions is not None
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value.kind is ValueKind.INTERVAL
        assert temperature.locator is not None
        assert temperature.locator.table == "6"
        assert temperature.locator.page == 14
        expected_row = "2" if "sublimation" in local_id else "5"
        assert source_rows[local_id][1]["locator"]["row"] == expected_row
        assert temperature.locator.note is not None
        assert f"row={expected_row}" in temperature.locator.note
        assert row.identity.composition.is_unknown
        assert row.identity.total_pressure_Pa is None
        assert row.identity.fO2_Pa is None
        source_conditions = source_rows[local_id][1]["point_conditions"]
        for condition_name in ("total_pressure_Pa", "fO2_Pa"):
            source_condition = source_conditions[condition_name]
            assert source_condition["state"]["tag"] == "unknown"
            assert source_condition["state"]["reason"].startswith("not_published:")
        assert "total_pressure_Pa" not in (row.point_conditions or {})
        assert row.point_conditions["fO2_Pa"].state.is_unknown
        assert row.uncertainty.verbatim["confidence_level"] == "not_stated"
        assert row.uncertainty.verbatim["value"] in {"13", "5", "12", "6"}

    quantity_unknown_ids = {
        row.observation_id for row in migrated if row.identity.quantity.is_unknown
    }
    assert quantity_unknown_ids == {
        f"{SID}::shornikov_2026_table6_{suffix}"
        for suffix in (
            "sublimation_enthalpy",
            "sublimation_entropy",
            "dissociation_enthalpy",
            "dissociation_entropy",
        )
    }
    assert not any(row.identity.species.phase.is_unknown for row in migrated)

    literature = [row for row in migrated if _family(_local_id(row.observation_id)) == "table7"]
    assert len(literature) == 22
    for row in literature:
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.locator is not None
        assert row.uncertainty.verbatim["confidence_level"] == "not_stated"
        assert row.uncertainty.verbatim["value"] is not None
        assert row.uncertainty.verbatim["value_as_published"]
        _, source = source_rows[_local_id(row.observation_id)]
        assert row.uncertainty.verbatim["units"] == source["units"]

    uncertainty_counts = Counter()
    for row in migrated:
        verbatim = row.uncertainty.verbatim or {}
        confidence = verbatim.get("confidence_level")
        if confidence is None:
            assert "no printed uncertainty" in verbatim.get("note", "")
            confidence = "not_printed"
        uncertainty_counts[(row.uncertainty.kind.value, confidence)] += 1
    assert uncertainty_counts == Counter(
        {("printed", "not_stated"): 201, ("printed", "not_printed"): 76}
    )

    assert not any(
        row.identity.quantity.is_value and row.identity.quantity.value is Quantity.ACTIVITY
        for row in migrated
    )
    assert not any(
        observation["values"].get("quantity") == "activity"
        for block in document["species"].values()
        for observation in block.get("observations", [])
    )

    assert result.validation is not None
    assert result.validation.hard_issues == ()


def test_every_deferred_figure_has_metadata_and_no_numeric_points() -> None:
    document = _document()
    figures = [
        record
        for block in document["species"].values()
        for record in block.get("context", [])
        if record.get("type") == "figure_record"
    ]
    assert len(figures) == 27
    for record in figures:
        values = record["values"]
        assert values["quantity"]
        assert values["species"]
        assert values["phase"]
        assert values["temperature_as_printed"]
        assert values["axes_as_printed"]
        assert values["caption_as_printed"]
        assert values["panel"]
        assert values["series"]
        assert values["attribution"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert "points" not in values
        assert all(isinstance(series["marker_count"], int) for series in values["series"])

    sio2_activity_figure = next(
        record for record in figures if record["observation_id"] == "shornikov_2026_fig3_SiO2_b"
    )
    figure_standard_state = sio2_activity_figure["values"]["standard_state"]
    assert figure_standard_state["state"]["value"]["endmember"]["phase"]["tag"] == "unknown"
    assert "cristobalite" in figure_standard_state["basis"].casefold()
    assert "1999 K" in figure_standard_state["basis"]
    assert "crosses" in figure_standard_state["basis"]

    cao_activity_figure = next(
        record for record in figures if record["observation_id"] == "shornikov_2026_fig3_CaO_a"
    )
    cao_standard_state = cao_activity_figure["values"]["standard_state"]
    cao_phase = cao_standard_state["state"]["value"]["endmember"]["phase"]
    assert cao_phase["tag"] == "unknown"
    assert cao_standard_state["inferred"] is False
    assert "crystalline" not in cao_phase.get("reason", "").casefold()
    assert "temperature" in cao_standard_state["basis"].casefold()

    fig10 = next(
        record
        for record in figures
        if record["observation_id"] == "shornikov_2026_fig10_oxygen_content_and_ratio"
    )
    assert fig10["values"]["axes_as_printed"]["y_a"] == "O + O2, mol %"
    assert fig10["values"]["axes_as_printed"]["y_b"] == "O2 + O, mol %"
    assert "O2/O ratio" in fig10["values"]["caption_as_printed"]

    for locator in iter_locators(document):
        if "pdf_page_index" in locator and "page" in locator:
            assert locator["page"] == locator["pdf_page_index"] + 1


@pytest.mark.skipif(not PDF.is_file(), reason="regolith-corpus source PDF is absent")
def test_locator_pages_match_the_source_pdf() -> None:
    document = _document()
    page_text: dict[int, str] = {}
    for locator in iter_locators(document):
        if "page" not in locator:
            continue
        page = int(locator["page"])
        assert int(locator["pdf_page_index"]) == page - 1
        if page not in page_text:
            result = subprocess.run(
                ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
                check=True,
                capture_output=True,
                text=True,
            )
            page_text[page] = result.stdout
        text = page_text[page]
        assert text.strip()
        if "table" in locator:
            assert f"Table {locator['table']}" in text
        if "figure" in locator:
            assert f"Fig. {locator['figure']}" in text


def test_per_file_validator_accepts_extract() -> None:
    document = _document()
    errors = validator.validate_extract_document(
        document,
        path=EXTRACT,
        expected_source_id=SID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert not errors
