"""Migration pins for Mendybaev et al. (2013), Table 1 residue analyses."""

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/mendybaev-2013-fun-cai-evaporation.yaml"


def test_table_1_fun1_3_residue_composition_and_conditions_migrate() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    row = [
        observation
        for observation in migrator.result.observations.values()
        if observation.locator.note == "row=FUN1-3"
    ]
    assert len(row) == 4
    assert all(o.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION for o in row)
    assert {o.identity.species.formula: o.value.point for o in row} == {
        "MgO": Decimal("50.01"),
        "Al2O3": Decimal("4.04"),
        "SiO2": Decimal("42.99"),
        "CaO": Decimal("2.96"),
    }
    assert all(o.identity.temperature_K.value == Decimal("2173.15") for o in row)
    assert all(
        o.point_conditions["total_pressure_Pa"].state.value.bound_value
        == Decimal("0.0001")
        for o in row
    )
    assert all(o.point_conditions["time_min"].state.value == Decimal("0") for o in row)
    assert not migrator.result.validation.hard_issues


def test_bounded_identity_conditions_stay_on_mendybaev_points(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    row = next(
        observation
        for observation in document["species"]["SiO2"]["observations"]
        if observation["observation_id"]
        == "mendybaev_2013_table1_fun1_residue_composition_series"
    )
    row["point_conditions"]["sample_mass_kg"] = {
        "state": {
            "tag": "value",
            "value": {
                "kind": "bound",
                "bound_operator": "<=",
                "bound_value": "0.0001",
            },
        },
        "locator": {"table": "1", "row": "FUN1-3"},
    }
    extract = tmp_path / EXTRACT.name
    extract.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(extract)
    migrator.finalize()

    observations = tuple(migrator.result.observations.values())
    row_points = [
        observation
        for observation in observations
        if observation.locator.note == "row=FUN1-3"
    ]
    assert len(observations) == 96
    assert len(row_points) == 4
    for observation in row_points:
        pressure = observation.point_conditions["total_pressure_Pa"]
        mass = observation.point_conditions["sample_mass_kg"]
        assert pressure.state.is_value
        assert pressure.state.value.kind is ValueKind.BOUND
        assert pressure.state.value.bound_operator == "<"
        assert observation.identity.total_pressure_Pa.is_unknown
        assert mass.state.is_value
        assert mass.state.value.kind is ValueKind.BOUND
        assert mass.state.value.bound_operator == "<="
        assert observation.identity.sample_mass_kg.is_unknown


def test_table_1_fun2_residues_migrate_to_the_fun2_experiment() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = tuple(migrator.result.observations.values())
    fun1 = [o for o in observations if o.locator.note and o.locator.note.startswith("row=FUN1-")]
    fun2 = [o for o in observations if o.locator.note and o.locator.note.startswith("row=FUN2-")]
    assert len(fun1) == 56
    assert len(fun2) == 40
    assert all(o.experiment_id.endswith("mendybaev-fun1-vacuum-evaporation") for o in fun1)
    assert all(o.experiment_id.endswith("mendybaev-fun2-vacuum-evaporation") for o in fun2)
    fun2_10_mgo = [
        o for o in fun2
        if o.locator.note == "row=FUN2-10"
        and o.identity.species.formula == "MgO"
    ]
    assert len(fun2_10_mgo) == 1
    assert fun2_10_mgo[0].value.point == Decimal("1.55")
    assert sum(o.experiment_id == fun2_10_mgo[0].experiment_id for o in observations) == 40
    assert not migrator.result.validation.hard_issues


def test_all_deferred_figure_records_have_separate_captions() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    figures = [
        row for row in extract["species"]["SiO2"]["context"]
        if row.get("type") == "figure_only"
    ]
    assert len(figures) == 12
    figure7 = next(row for row in figures if row["locator"]["figure"] == "7")
    figure8 = next(row for row in figures if row["locator"]["figure"] == "8")
    assert "Fig. 8." not in figure7["values"]["caption_as_printed"]
    assert "as a function of the amount of 24Mg remaining" in figure7["values"]["caption_as_printed"]
    assert "Fig. 7." not in figure8["values"]["caption_as_printed"]
    assert "Same as Fig. 7 but for 29Si/28Si" in figure8["values"]["caption_as_printed"]
    for number in (1, 3, 10, 11, 12):
        figure = next(row for row in figures if row["locator"]["figure"] == str(number))
        assert figure["values"]["caption_as_printed"].startswith(f"Fig. {number}.")
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
