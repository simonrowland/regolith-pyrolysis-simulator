from copy import deepcopy
from pathlib import Path

import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-016-stolyarova-1992.yaml"


def _context(document: dict, observation_id: str) -> dict:
    return next(
        row
        for block in document["species"].values()
        for row in block.get("context", [])
        if row["observation_id"] == observation_id
    )


def _render_pins(document: dict) -> tuple[object, ...]:
    table1 = _context(document, "stolyarova_1992_gibbs_cas_and_fcm_table1")
    table3 = _context(document, "stolyarova_1992_na2o_k2o_sio2_gibbs_table3")
    table4 = _context(document, "stolyarova_1992_na2o_chemical_potential_nbs_table4")
    return (
        table1["values"]["points"][0]["minus_delta_G_exp_kcal_mol"],
        table1["values"]["points"][0]["minus_delta_G_kohler"],
        table1["values"]["points"][0]["minus_delta_G_wilson"],
        table3["values"]["points"][0]["minus_delta_G_exp_kcal_mol"],
        table3["values"]["points"][0]["minus_delta_G_wilson"],
        table4["values"]["points"][0]["mu_Na2O_wilson_kcal_mol"],
        table4["values"]["points"][0]["mu_Na2O_exp_kcal_mol"],
    )


def test_printed_table_counts_values_and_activity_absence():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table1 = _context(document, "stolyarova_1992_gibbs_cas_and_fcm_table1")
    table2 = _context(document, "stolyarova_1992_binary_wilson_model_parameters_table2")
    table3 = _context(document, "stolyarova_1992_na2o_k2o_sio2_gibbs_table3")
    table4 = _context(document, "stolyarova_1992_na2o_chemical_potential_nbs_table4")
    method = _context(document, "stolyarova_1992_silicate_kems_geometry_activities")

    assert len(table1["values"]["points"]) == 6
    assert len(table2["values"]["parameters"]) == 7
    assert len(table3["values"]["points"]) == 5
    assert len(table4["values"]["points"]) == 5
    render_pins = (8.3, 8.4, 7.4, 8.4, 11.7, 50.6, 56.0)
    assert _render_pins(document) == render_pins
    assert table1["locator"]["page"] == "PDF p. 2 / printed p. 186"
    assert table2["locator"]["page"] == "PDF p. 3 / printed p. 187"
    assert table3["locator"]["page"] == "PDF p. 3 / printed p. 187"
    assert table4["locator"]["page"] == "PDF p. 3 / printed p. 187"
    assert table1["values"]["points"][4]["x"] is None
    assert table1["values"]["points"][4]["x_as_printed"] == "0.50:0.25:25"
    assert method["values"]["numeric_activity_published"] is False

    mutated = deepcopy(document)
    _context(mutated, "stolyarova_1992_gibbs_cas_and_fcm_table1")["values"]["points"][0][
        "minus_delta_G_exp_kcal_mol"
    ] = 8.4
    assert _render_pins(mutated) != render_pins

    source = document["source"]
    migrator = Migrator(
        root=ROOT,
        index={document["source_id"]: dict(source)},
        aliases={},
    )
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 0
    assert sum(observation.value.kind is ValueKind.UNAVAILABLE for observation in observations) == 0
    assert sum(not observation.identity.quantity.is_value for observation in observations) == 0
    assert sum(not observation.identity.species.phase.is_value for observation in observations) == 0
