from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/zaitsev-2000-sodium-silicate-thermodynamics.yaml"
)


def test_zaitsev_2000_tables_4_and_6_migrate_with_binary_conditions_and_signs():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(
        root=ROOT,
        index={document["source_id"]: dict(document["source"])},
        aliases={},
    )
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert migrator.result.validation is not None
    assert migrator.result.validation.ok
    assert migrator.result.validation.hard_issues == ()
    assert len(migrator.result.observations) == 110
    assert all(
        all(
            key in (observation.point_conditions or {})
            and (observation.point_conditions or {})[key].state.is_value
            and (observation.point_conditions or {})[key].locator is not None
            for key in ("temperature_K", "composition")
        )
        for observation in migrator.result.observations.values()
    )
    table5 = [
        observation
        for observation in migrator.result.observations.values()
        if "::zaitsev_2000_table5_" in observation.observation_id
    ]
    assert len(table5) == 44
    assert {observation.identity.species.formula for observation in table5} == {
        "Na2O"
    }
    assert all(
        observation.provenance["printed_form"] == "neg_log10"
        and observation.locator.page == 9
        for observation in table5
    )
    reduced_silica_rows = [
        observation
        for observation in migrator.result.observations.values()
        if (
            "::zaitsev_2000_table4_sio2_experimental_activity::"
            in observation.observation_id
        )
    ]
    assert len(reduced_silica_rows) == 29
    assert all(
        len(observation.derived_from or ()) == 29 for observation in reduced_silica_rows
    )

    species = document["species"]
    table5 = {
        observation["observation_id"]: observation["values"]["points"]
        for observation in species["Na2O"]["observations"]
        if observation["observation_id"].startswith("zaitsev_2000_table5_")
    }
    # These Table 5 cells were confirmed against the 600 dpi p. 537 render.
    table5_pins = {
        ("zaitsev_2000_table5_this_work_na2o_activity", "0.103", "1430"): "9.394",
        ("zaitsev_2000_table5_ref39_na2o_activity", "0.103", "1430"): "9.823",
        ("zaitsev_2000_table5_this_work_na2o_activity", "0.254", "1557"): "8.348",
        ("zaitsev_2000_table5_ref39_na2o_activity", "0.254", "1557"): "8.790",
        ("zaitsev_2000_table5_this_work_model_na2o_activity", "0.500", "1255"): "7.080",
    }
    for (observation_id, x_na2o, temperature), expected in table5_pins.items():
        point = next(
            point
            for point in table5[observation_id]
            if point["x_Na2O_as_printed"] == x_na2o
            and point["T_K"] == temperature
        )
        assert point["activity"] == expected

    sio2 = species["SiO2"]["observations"][0]["values"]["points"]
    na2o = next(
        row["values"]["points"]
        for row in species["Na2O"]["observations"]
        if row["observation_id"] == "zaitsev_2000_table4_na2o_experimental_activity"
    )
    # These printed Table 4 cells were confirmed against the 600 dpi p. 535 render.
    assert (sio2[0]["activity"], sio2[0]["locator"]["page"]) == ("0.860", 7)
    assert (sio2[-1]["activity"], sio2[-1]["locator"]["page"]) == ("3.77e-6", 7)
    assert (na2o[0]["activity"], na2o[0]["locator"]["page"]) == ("7.19e-10", 7)

    own_enthalpy = next(
        row
        for row in species["Na2O"]["observations"]
        if row["observation_id"] == "zaitsev_2000_table6_this_work_partial_molar_enthalpy"
    )
    migrated_enthalpies = {
        observation.observation_id.split("row=", 1)[1].split(":", 1)[0]: observation
        for observation in migrator.result.observations.values()
        if (
            "::zaitsev_2000_table6_this_work_partial_molar_enthalpy::"
            in observation.observation_id
        )
    }
    for point in own_enthalpy["values"]["points"]:
        row = point["row_as_printed"]
        observation = migrated_enthalpies[row]
        printed_minus_delta_h = point["printed_minus_delta_H_kJ_per_mol"]
        assert observation.value.point == -Decimal(printed_minus_delta_h)
        assert observation.locator.page == 9
