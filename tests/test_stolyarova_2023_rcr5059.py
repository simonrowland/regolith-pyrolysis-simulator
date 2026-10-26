"""Source pins and migration checks for Stolyarova et al. (2023), Table 2."""

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Phase
from simulator.battery.migrate import Migrator
from tools import validate_literature_extracts as vle


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/stolyarova-2023-rcr5059-cs-sr-ba.yaml"
SOURCE_ID = "stolyarova-2023-rcr5059-cs-sr-ba"


def _table2_rows():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    return document, document["species"]["CsBO2"]["observations"]


def _migrated_result():
    migrator = Migrator(root=ROOT, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator.result


def _migrated_by_id():
    result = _migrated_result()
    return {
        observation.observation_id.split("::", 1)[1]: observation
        for observation in result.observations.values()
        if observation.observation_id.startswith(f"{SOURCE_ID}::")
    }


def test_table2_render_pins_and_row_count_reach_migrated_observations():
    _, rows = _table2_rows()
    table2 = [row for row in rows if row["locator"].get("table") == "2"]
    assert len(table2) == 11
    expected = {1: ("8 × 10^-3", Decimal("0.008")), 4: ("0.29", Decimal("0.29")), 10: ("0.67", Decimal("0.67"))}
    migrated = _migrated_by_id()

    for row in table2:
        observation = migrated[row["observation_id"]]
        assert observation.identity.temperature_K.value == Decimal("1020")
        assert observation.point_conditions["temperature_K"].state.value == Decimal("1020")

    for printed_row, (printed, migrated_value) in expected.items():
        row = next(row for row in table2 if row["locator"]["row"] == printed_row)
        assert row["locator"]["page"] == 8
        assert row["values"]["activity_as_published"] == printed
        observation = migrated[row["observation_id"]]
        assert observation.value.point == migrated_value
        assert observation.identity.species.phase.value is Phase.L
        assert observation.identity.temperature_K.value == Decimal("1020")
        assert observation.point_conditions["temperature_K"].state.value == Decimal("1020")


def test_mol_percent_and_unprinted_conditions_stay_explicitly_unknown():
    _, rows = _table2_rows()
    table2 = [row for row in rows if row["locator"].get("table") == "2"]
    migrated = _migrated_by_id()
    row10 = next(row for row in table2 if row["locator"]["row"] == 10)

    assert row10["values"]["composition_as_published"] == {
        "Cs2O": "50", "B2O3": "10", "SiO2": "10"
    }
    assert row10["values"]["composition_unit_as_published"] == "mol.%"
    assert row10["point_conditions"]["composition"]["state"]["reason"] == (
        "printed mol.% composition; reader lacks a mole-percent basis (t-1524); "
        "printed 50/10/10 sums to 70%, remainder not stated"
    )

    for row in table2:
        observation = migrated[row["observation_id"]]
        assert observation.identity.composition.is_unknown
        assert observation.identity.total_pressure_Pa.is_unknown
        assert "total_pressure_Pa" not in observation.point_conditions
        assert observation.identity.fO2_Pa.is_unknown
        assert observation.point_conditions["fO2_Pa"].state.is_unknown


def test_table2_estimated_composition_dashes_stay_printed_dashes():
    _, rows = _table2_rows()
    expected_dashes = {
        1: {"Cs2SiO3"},
        2: {"Cs2SiO3"},
        3: {"Cs2SiO3"},
        4: {"Cs2SiO3"},
        5: {"B2O3"},
        6: {"B2O3", "SiO2"},
    }
    assert len(rows) == 11

    for row_number, oxides in expected_dashes.items():
        row = next(item for item in rows if item["locator"]["row"] == row_number)
        printed = row["values"]["estimated_phase_composition_as_published"]
        for oxide in oxides:
            assert printed[oxide] == "-"
            assert isinstance(printed[oxide], str)


def test_kems_calibration_lifts_to_the_scoped_experiment_apparatus():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = [
        observation
        for species in document["species"].values()
        for observation in species.get("observations", [])
    ]
    result = _migrated_result()
    migrated = {
        observation.observation_id.split("::", 1)[1]: observation
        for observation in result.observations.values()
        if observation.observation_id.startswith(f"{SOURCE_ID}::")
    }

    def observations_for(predicate):
        return [row for row in rows if predicate(row)]

    table2_rows = observations_for(lambda row: row["locator"].get("table") == "2")
    table3_rows = observations_for(lambda row: row["locator"].get("table") == "3")
    ref70_rows = observations_for(
        lambda row: row["locator"].get("table") == "4"
        and row["values"].get("reference_as_published") == "70"
    )
    ref75_rows = observations_for(
        lambda row: row["locator"].get("table") == "4"
        and row["values"].get("reference_as_published") == "75"
    )
    ref78_rows = observations_for(
        lambda row: row["locator"].get("table") == "4"
        and row["values"].get("reference_as_published") == "78"
    )
    assert (len(table2_rows), len(table3_rows), len(ref70_rows), len(ref75_rows), len(ref78_rows)) == (
        11,
        6,
        18,
        6,
        12,
    )

    def apparatus_for(rows):
        experiment_ids = {migrated[row["observation_id"]].experiment_id for row in rows}
        assert len(experiment_ids) == 1
        experiment = result.experiments[experiment_ids.pop()]
        assert experiment.apparatus is not None
        assert experiment.apparatus.calibration is not None
        return experiment.apparatus.calibration

    table2_calibration = apparatus_for(table2_rows)
    for key in ("method", "reference_substance"):
        assert table2_calibration[key].state.is_unknown
        assert table2_calibration[key].state.reason == "not_published"

    ref70_calibration = apparatus_for(table3_rows + ref70_rows)
    assert "reference 69" in ref70_calibration["method"].state.value.casefold()
    assert "gold" in ref70_calibration["reference_substance"].state.value.casefold()

    for rows in (ref75_rows, ref78_rows):
        calibration = apparatus_for(rows)
        for key in ("method", "reference_substance"):
            assert calibration[key].state.is_unknown
            assert calibration[key].state.reason == "not_published"


def test_render_fidelity_pin_rejects_mutated_activity():
    document, rows = _table2_rows()
    assert vle.check_all_fidelity_samples_match(document, label=SOURCE_ID) == []

    rows[0]["values"]["activity_as_published"] = "8.1 × 10^-3"
    errors = vle.check_all_fidelity_samples_match(document, label=SOURCE_ID)
    assert any("fidelity_samples[0]" in error and "mismatch" in error for error in errors)


def test_table3_and_table4_vapour_rows_migrate_as_gas_phase():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = [
        observation
        for species in document["species"].values()
        for observation in species.get("observations", [])
        if observation["locator"].get("table") in {"3", "4"}
    ]
    assert len(rows) == 42
    assert all(row["phase"] == "g" for row in rows)

    migrated = _migrated_by_id()
    assert all(
        migrated[row["observation_id"]].identity.species.phase.value is Phase.G
        for row in rows
    )


def test_table13_is_located_context_not_a_species_observation():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = document["species"]["NaBO2"]["context"]
    assert len(context) == 1
    rowset = context[0]
    assert rowset["locator"]["page"] == 18
    assert rowset["locator"]["table"] == "13"
    assert rowset["attribution"] == (
        "V.L. Stolyarova (2013), ECS Transactions 46, 55, reference 111 in "
        "Stolyarova et al. (2023)."
    )
    assert len(rowset["values"]["rows"]) == 6
    assert rowset["values"]["rows"][0]["composition_as_published"]["SiO2"] == "45.5"
    assert rowset["values"]["rows"][3]["composition_as_published"]["Cs2O"] == "-"
    assert rowset["values"]["rows"][5]["identified_vapour_species_as_published"] == [
        "CsBO2", "NaBO2", "Na"
    ]

    migrator = Migrator(root=ROOT, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    context_records = [
        row
        for rows in migrator.result.context_by_work.values()
        for row in rows
        if row["source_id"] == SOURCE_ID
    ]
    assert len(context_records) == 1
    assert len(migrator.result.observations) == 53
    assert not any(
        "table13" in observation.observation_id
        for observation in migrator.result.observations.values()
    )

