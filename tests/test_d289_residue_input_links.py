from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import re

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator, wt_pct_to_mole_fraction


ROOT = Path(__file__).resolve().parents[1]
EXTRACTS = ROOT / "data/literature/extracts"


def _document(name: str) -> dict:
    return yaml.safe_load((EXTRACTS / name).read_text(encoding="utf-8"))


def _observations(name: str):
    path = EXTRACTS / name
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    source_id = _document(name)["source_id"]
    assert not [
        issue for issue in migrator.result.validation.hard_issues
        if source_id in issue.path
    ]
    return [
        row for row in migrator.result.observations.values()
        if row.source_id == source_id
    ]


def _composition_condition(row):
    return row.point_conditions["composition"]


def test_wang_residue_runs_and_mass_loss_inputs_migrate() -> None:
    name = "wang-2001-solar-melt-evaporation-residues.yaml"
    source = _document(name)
    table1 = next(
        row for row in source["species"]["SiO2"]["observations"]
        if row["observation_id"] == "wang_2001_table1_electron_microprobe_residue_compositions"
    )
    sc12 = next(row for row in table1["values"]["series"] if row["sample"] == "SC12")
    starting_wt_pct = {
        "MgO": "24.8",
        "Al2O3": "2.46",
        "SiO2": "33.8",
        "CaO": "2.04",
        "TiO2": "0.110",
        "FeO": "36.7",
    }
    assert sc12["T_C"] == "1400"
    assert sc12["VF_wt_pct"] == "0.38"
    assert sc12["point_conditions"]["run_id"]["state"]["value"] == "SC12"
    assert sc12["point_conditions"]["run_id"]["locator"]["row"] == "SC12"
    assert sc12["point_conditions"]["run_id"]["locator"]["table"] == "1"
    assert sc12["point_conditions"]["composition"]["state"]["value"]["components"] == [
        [oxide, amount] for oxide, amount in starting_wt_pct.items()
    ]
    assert "run SC12" in sc12["point_conditions"]["composition"]["locator"]["note"]

    table1_run_rows = [
        row
        for species in source["species"].values()
        for observation in species["observations"]
        if observation["observation_id"].startswith("wang_2001_table1_")
        for row in observation["values"].get("series", [])
    ]
    assert len(table1_run_rows) == 256
    assert all(
        row["point_conditions"]["run_id"]["state"]["value"] == row["sample"]
        for row in table1_run_rows
    )

    mass_loss = [
        row for row in source["species"]["SiO2"]["observations"]
        if row["observation_id"].startswith("wang_2001_table2_sc")
        and row["observation_id"].endswith("_mass_loss")
    ]
    assert len(mass_loss) == 16
    sc1_point = mass_loss[0]["values"]["points"][0]
    assert sc1_point["sample"] == "SC1"
    assert sc1_point["mass_loss_wt_pct"] == "57.29"
    assert sc1_point["initial_mass_mg_as_printed"] == "165.3"
    assert sc1_point["final_mass_mg_as_printed"] == "70.5"
    sc1_run_id = mass_loss[0]["point_conditions"]["run_id"]
    assert sc1_run_id["state"]["value"] == "SC1"
    assert sc1_run_id["locator"]["row"] == "SC1"
    assert sc1_run_id["locator"]["table"] == "2"
    assert all(
        "initial_mass_mg_as_printed" in row["values"]["points"][0]
        and "final_mass_mg_as_printed" in row["values"]["points"][0]
        and row["point_conditions"]["run_id"]["state"]["value"]
        == row["values"]["points"][0]["sample"]
        for row in mass_loss
    )

    migrated = _observations(name)
    residue_rows = [
        row for row in migrated
        if row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and "wang_2001_table1_" in row.observation_id
    ]
    assert len(residue_rows) == 432
    for row in residue_rows:
        assert row.identity.temperature_K.is_value
        assert row.point_conditions["VF_wt_pct"].state.is_value
        run_id = row.point_conditions["run_id"]
        assert run_id.state.is_value and run_id.state.value.startswith("SC")
        assert run_id.locator is not None and run_id.locator.table == "1"
        sample_match = re.search(r"row=(SC\d+)", row.locator.note or "")
        assert sample_match is not None
        assert run_id.state.value == sample_match.group(1)
        condition = _composition_condition(row)
        assert condition.state.is_value
        assert condition.state.value.components == wt_pct_to_mole_fraction(
            {oxide: Decimal(value) for oxide, value in starting_wt_pct.items()}
        ).components
        assert condition.locator is not None and "run SC" in condition.locator.note
        assert row.point_conditions["atmosphere"].state.value == "vacuum furnace"
        assert row.point_conditions["total_pressure_Pa"].state.is_value

    mass_loss_rows = [
        row for row in migrated
        if row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        and "wang_2001_table2_" in row.observation_id
    ]
    assert len(mass_loss_rows) == 16
    endpoint_run_ids = [
        row.point_conditions["run_id"].state.value for row in mass_loss_rows
    ]
    assert len(set(endpoint_run_ids)) == 16
    assert all(
        row.identity.temperature_K.is_value
        and row.value.kind.value == "point"
        and _composition_condition(row).state.is_value
        and row.point_conditions["atmosphere"].state.value == "vacuum furnace"
        and row.point_conditions["run_id"].state.is_value
        and row.point_conditions["run_id"].locator.table == "2"
        for row in mass_loss_rows
    )
    # The d289-runjoin consumer will use printed run_id before time to keep equal-duration runs distinct.
    for row in residue_rows:
        run_id = row.point_conditions["run_id"].state.value
        assert endpoint_run_ids.count(run_id) == 1
    sc1 = next(row for row in mass_loss_rows if "row=sc1:" in row.observation_id)
    assert sc1.identity.temperature_K.value == Decimal("2073.15")
    assert sc1.value.point == Decimal("0.5729")
