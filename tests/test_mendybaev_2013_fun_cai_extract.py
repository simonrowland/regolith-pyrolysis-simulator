"""Migration pins for Mendybaev et al. (2013), Table 1 residue analyses."""

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
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
    assert {o.value.point for o in row} == {
        Decimal("50.01"), Decimal("4.04"), Decimal("42.99"), Decimal("2.96")
    }
    assert all(o.identity.temperature_K.value == Decimal("2173.15") for o in row)
    assert all(
        o.point_conditions["total_pressure_Pa"].state.value.bound_value
        == Decimal("0.0001")
        for o in row
    )
    assert all(o.point_conditions["time_min"].state.value == Decimal("0") for o in row)
    assert not migrator.result.validation.hard_issues


def test_all_deferred_figure_records_have_separate_captions() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    figures = [
        row for row in extract["species"]["SiO2"]["context"]
        if row.get("type") == "figure_only"
    ]
    assert len(figures) == 12
    figure7 = next(row for row in figures if row["locator"]["figure"] == "7")
    assert "Fig. 8." not in figure7["values"]["caption_as_printed"]
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
