from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/tyurnina-lopatin-stolyarova-2008-ba-borosilicate-kems.yaml"


def test_tyurnina_activities_migrate_with_temperature_series_and_composition():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows_by_id = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["observations"]
        if row.get("values", {}).get("quantity") == "activity"
    }
    assert len(rows_by_id) == 90
    first_series = rows_by_id["tyurnina_2008_bao_activity_table1_comp_01"]["values"]["series"]
    assert first_series == [
        {"T_K": 1650, "activity": "2.2e-2"},
        {"T_K": 1730, "activity": "1.8e-2"},
    ]
    unsupported = next(
        row
        for row in document["species"]["SiO2"]["context"]
        if row["observation_id"] == "tyurnina_2008_table1_unsupported_potentials_and_gibbs_energies"
    )["values"]["rows_as_printed"]
    assert len(unsupported) == 60
    assert unsupported[34]["negative_excess_chemical_potential_kJ_mol_as_printed"][2] == "−7.57"
    assert unsupported[34]["delta_G_excess_kJ_as_printed"] == "−53.85"
    assert unsupported[46]["negative_excess_chemical_potential_kJ_mol_as_printed"][2] == "−13.03"
    assert unsupported[47]["negative_excess_chemical_potential_kJ_mol_as_printed"][2] == "−2.13"
    assert unsupported[56]["negative_excess_chemical_potential_kJ_mol_as_printed"][2] == "−7.04"
    assert unsupported[58]["negative_excess_chemical_potential_kJ_mol_as_printed"][2] == "−18.75"
    assert unsupported[59]["negative_excess_chemical_potential_kJ_mol_as_printed"][2] == "−14.44"

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
    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 181
    assert any(
        observation.observation_id.endswith("::tyurnina_2008_measured_ion_current_ratios_unreported")
        for observation in observations
    )
    activity_observations = [
        observation
        for observation in observations
        if "_activity_table1_comp_" in observation.observation_id
    ]
    assert len(activity_observations) == 180
    for source_id, source in rows_by_id.items():
        matches = [
            observation
            for observation in activity_observations
            if observation.observation_id.startswith(
                f"{document['source_id']}::{source_id}::T="
            )
        ]
        assert len(matches) == 2
        for observation, point in zip(matches, source["values"]["series"]):
            assert observation.value.point == Decimal(point["activity"])
            assert observation.identity.temperature_K.is_value
            assert observation.identity.temperature_K.value == Decimal(str(point["T_K"]))
            composition = observation.point_conditions["composition"]
            assert composition.state.is_value
            assert composition.state.value.amount_basis.value == "mole_fraction"
            assert len(composition.state.value.components) == 3
            assert composition.locator is not None
