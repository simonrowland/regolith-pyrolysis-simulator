from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/fujisawa-1994-na-sn-carbonate-thermodynamics.yaml"
)


def test_fujisawa_1994_render_pins_and_row_count() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    sn_rows = doc["species"]["SnO2"]["observations"]
    t2 = [row for row in sn_rows if row["locator"]["table"] == "2"]
    t3 = [row for row in sn_rows if row["locator"]["table"] == "3"]
    na_rows = doc["species"]["NaO0.5"]["observations"]

    assert len(t2) == 8  # Table 2 has eight run rows.
    assert len(t3) == 9  # Table 3 has nine run rows.
    assert len(na_rows) == 3  # Table 2 has three group log(aNaO0.5) summaries.
    assert (t2[0]["values"]["log10_activity_as_printed"], t2[0]["locator"]) == (
        "-1.598",
        {"page": 5, "published_page": 418, "table": "2"},
    )
    assert t2[5]["values"]["reaction_delta_G16_kJ_as_printed"] == "-160.5"
    assert t3[0]["values"]["X_SnO2_as_printed"] == "8.0 x 10^-4"
    assert t3[-1]["values"]["log10_activity_as_printed"] == "-2.437"
    assert all("composition_mol" not in row["values"] for row in t3)
    assert all(row["values"]["status"] == "typed_refusal" for row in sn_rows)
    assert all(row["standard_state"] == "pure solid SnO2(s)" for row in sn_rows)
    assert all(
        row["standard_state"]
        == "Reference melt: NaO0.5-SiO2-FeO1.5; reference activity relation in "
        "eq. (5): log a*_NaO0.5 = -(4770.9/T)+0.0715 (1223-1473 K)."
        for row in na_rows
    )
    assert all(
        row["point_conditions"]["total_pressure_Pa"]["state"]
        == {"tag": "unknown", "reason": "not_published"}
        for row in sn_rows
    )
    assert all(
        row["point_conditions"]["temperature_K"]["state"]["value"] == 1523
        and row["point_conditions"]["CO2_partial_pressure_MPa"]["state"]["tag"]
        == "value"
        and row["point_conditions"]["oxygen_partial_pressure_Pa"]["state"]["tag"] == "value"
        and "fO2_Pa" not in row["point_conditions"]
        for row in sn_rows
    )
    assert all(
        row["point_conditions"]["composition"]["state"]["tag"] == "unknown"
        for row in t2 + t3
    )
    assert [row["values"]["log10_activity_as_printed"] for row in na_rows] == [
        "-1.960", "-1.765", "-1.530"
    ]
    assert all(
        row["point_conditions"]["temperature_K"]["state"]["value"] == 1523
        and row["point_conditions"]["CO2_partial_pressure_MPa"]["state"]["tag"]
        == "value"
        and row["point_conditions"]["oxygen_partial_pressure_Pa"]["state"]["value"] == "20000"
        and "fO2_Pa" not in row["point_conditions"]
        for row in na_rows
    )
    assert all("activity" not in row["values"] for row in sn_rows)


def test_fujisawa_1994_isolated_migration_keeps_conditions_and_locators() -> None:
    migrator = Migrator(root=EXTRACT.parents[3])
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    observations = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == "fujisawa-1994-na-sn-carbonate-thermodynamics"
    ]
    assert len(observations) == 20
    assert len(migrator.result.registry_issues) == 0
    table2 = next(o for o in observations if "fujisawa94_t2_r1" in o.observation_id)
    table3 = next(o for o in observations if "fujisawa94_t3_r1" in o.observation_id)
    assert (table2.locator.page, table2.locator.table) == (5, "2")
    assert (table3.locator.page, table3.locator.table) == (6, "3")
    for observation in observations:
        conditions = observation.point_conditions or {}
        assert conditions["temperature_K"].state.value == 1523
        assert conditions["CO2_partial_pressure_MPa"].state.is_value
        assert conditions["oxygen_partial_pressure_Pa"].state.is_value
        assert conditions["composition"].state.is_unknown
        assert observation.identity.composition.is_unknown
        assert observation.identity.total_pressure_Pa.is_unknown
        assert observation.value.kind.value == "unavailable"
    for experiment_id in (table2.experiment_id, table3.experiment_id):
        experiment = migrator.result.experiments[experiment_id]
        sweep_gas = experiment.pressure_environment.sweep_gas.state.value
        assert sweep_gas.species == "CO2-CO-Ar gas mixture"
        assert sweep_gas.flow_sccm.is_unknown
    na_observation = next(
        observation
        for observation in observations
        if "fujisawa94_t2_group_1_log_a_NaO05" in observation.observation_id
    )
    assert na_observation.identity.reference_state.is_unknown
