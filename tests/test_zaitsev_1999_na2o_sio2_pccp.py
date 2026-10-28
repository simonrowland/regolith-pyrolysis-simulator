from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/zaitsev-1999-na2o-sio2-pccp.yaml"


def _migrate():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(
        root=ROOT,
        index={document["source_id"]: dict(document["source"])},
        aliases={},
    )
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return document, migrator


def test_zaitsev_1999_scored_rows_migrate_with_temperature_and_composition():
    _, migrator = _migrate()

    assert migrator.result.validation is not None
    assert migrator.result.validation.ok
    assert migrator.result.validation.hard_issues == ()
    assert len(migrator.result.observations) == 116
    assert all(
        all(
            key in (observation.point_conditions or {})
            and (observation.point_conditions or {})[key].state.is_value
            and (observation.point_conditions or {})[key].locator is not None
            for key in ("temperature_K", "composition")
        )
        for observation in migrator.result.observations.values()
    )
    table4 = [
        observation
        for observation in migrator.result.observations.values()
        if "::zaitsev_1999_table4_" in observation.observation_id
    ]
    assert len(table4) == 44
    assert {observation.identity.species.formula for observation in table4} == {
        "Na2O"
    }
    assert all(
        observation.provenance["printed_form"] == "neg_log10"
        and observation.locator.page == 6
        for observation in table4
    )

    na2o = [
        observation
        for observation in migrator.result.observations.values()
        if "::zaitsev_1999_table1_na2o_activity::" in observation.observation_id
    ]
    sio2 = [
        observation
        for observation in migrator.result.observations.values()
        if "::zaitsev_1999_table1_sio2_activity::" in observation.observation_id
    ]
    assert len(na2o) == 29
    assert len(sio2) == 29
    assert all(len(observation.derived_from or ()) == 29 for observation in sio2)


def test_zaitsev_1999_render_pins_and_table1_row_count():
    document, _ = _migrate()
    species = document["species"]
    table4 = {
        observation["observation_id"]: observation["values"]["points"]
        for observation in species["Na2O"]["observations"]
        if observation["observation_id"].startswith("zaitsev_1999_table4_")
    }
    # These Table 4 cells were confirmed against the 600 dpi p. 1904 render.
    table4_pins = {
        ("zaitsev_1999_table4_present_na2o_activity", "0.103", "1430"): "9.394",
        ("zaitsev_1999_table4_ref16_na2o_activity", "0.103", "1430"): "9.823",
        ("zaitsev_1999_table4_present_na2o_activity", "0.254", "1557"): "8.346",
        ("zaitsev_1999_table4_ref16_na2o_activity", "0.254", "1557"): "8.79",
        ("zaitsev_1999_table4_present_na2o_activity", "0.303", "1344"): "9.261",
    }
    for (observation_id, x_na2o, temperature), expected in table4_pins.items():
        point = next(
            point
            for point in table4[observation_id]
            if point["x_Na2O_as_printed"] == x_na2o
            and point["T_K"] == temperature
        )
        assert point["activity"] == expected

    na2o = next(
        item["values"]["points"]
        for item in species["Na2O"]["observations"]
        if item["observation_id"] == "zaitsev_1999_table1_na2o_activity"
    )
    sio2 = next(
        item["values"]["points"]
        for item in species["SiO2"]["observations"]
        if item["observation_id"] == "zaitsev_1999_table1_sio2_activity"
    )

    assert len(na2o) == len(sio2) == 29
    assert (na2o[0]["activity"], na2o[0]["locator"]["row"]) == ("7.19e-10", "1")
    assert (na2o[-1]["activity"], na2o[-1]["locator"]["row"]) == ("8.62e-5", "29")
    assert (sio2[0]["activity"], sio2[0]["locator"]["row"]) == ("0.860", "1")
    assert (sio2[-1]["activity"], sio2[-1]["locator"]["row"]) == ("3.77e-6", "29")
    assert all(point["locator"]["published_page"] == 1901 for point in na2o + sio2)


def test_zaitsev_1999_partial_enthalpy_and_element_formation_signs():
    document, migrator = _migrate()

    present = next(
        item
        for item in document["species"]["Na2O"]["observations"]
        if item["observation_id"] == "zaitsev_1999_table5_present_partial_molar_enthalpy"
    )
    migrated_present = {
        observation.observation_id.split("row=", 1)[1].split(":", 1)[0]: observation
        for observation in migrator.result.observations.values()
        if "::zaitsev_1999_table5_present_partial_molar_enthalpy::"
        in observation.observation_id
    }
    for point in present["values"]["points"]:
        observation = migrated_present[point["row_as_printed"]]
        assert observation.value.point == -Decimal(
            point["printed_minus_delta_H_kJ_per_mol"]
        )

    quoted = [
        observation
        for observation in migrator.result.observations.values()
        if "::zaitsev_1999_table6_ref17_element_formation_enthalpy_"
        in observation.observation_id
    ]
    assert len(quoted) == 3
    assert {observation.value.point for observation in quoted} == {
        Decimal("-1556.03"),
        Decimal("-2100.8"),
        Decimal("-2467.3"),
    }

    standard_entropies = [
        observation
        for observation in migrator.result.observations.values()
        if "::zaitsev_1999_table6_ref17_standard_entropy_"
        in observation.observation_id
    ]
    assert len(standard_entropies) == 3
    assert {observation.value.point for observation in standard_entropies} == {
        Decimal("113.805"),
        Decimal("195.811"),
        Decimal("164.055"),
    }
    source_entropies = [
        observation
        for species in document["species"].values()
        for observation in species["observations"]
        if observation["observation_id"].startswith(
            "zaitsev_1999_table6_ref17_standard_entropy_"
        )
    ]
    assert len(source_entropies) == 2
    assert all(
        observation["units"] == "J/mol K"
        and "J/(mol K)" in observation["locator"]["note"]
        for observation in source_entropies
    )
