from __future__ import annotations

from decimal import Decimal

import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT


EXTRACT = REPO_ROOT / "data/literature/extracts/mysen-kushiro-1988-condensation-evaporation-am-min.yaml"


def test_mysen_kushiro_1988_tables_and_migrated_percent_pins() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    species = document["species"]
    t1 = species["Ag"]["observations"][0]
    t2 = species["SiO2"]["observations"][0]
    t6 = species["Mg2SiO4"]["observations"][0]

    assert len(t1["values"]["points"]) == 2
    assert len(t2["values"]["points"]) + len(
        species["MgO"]["observations"][0]["values"]["points"]
    ) + len(species["Mg2SiO4"]["observations"][0]["values"]["points"]) == 63
    assert len(t6["values"]["points"]) == 9
    assert t1["values"]["points"][0]["mass_loss_pct"] == "29.3"
    assert t1["values"]["points"][0]["locator"]["page"] == 3
    assert t2["values"]["points"][0]["locator"]["table"] == "2"
    assert t6["values"]["points"][0]["SiO2_wt_pct"] == "41.56"
    assert t6["values"]["points"][0]["locator"]["page"] == 9

    migrator = Migrator(root=REPO_ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert len(migrator.result.observations) == 75
    assert not migrator.result.validation.hard_issues

    ag = next(
        row for row in migrator.result.observations.values()
        if row.observation_id.endswith("table1_ag_mass_loss")
    )
    assert ag.value.point == Decimal("0.293")
    assert ag.identity.temperature_K == Decimal("1473.15")
    assert ag.identity.total_pressure_Pa.is_unknown
    assert ag.identity.fO2_Pa.is_unknown
