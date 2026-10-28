"""Migration pins for Renggli et al. (2022), Table 1."""

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/renggli-2022-te-evaporation.yaml"


def test_table1_residue_concentrations_and_conditions_migrate() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 36
    assert all(o.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION for o in observations)
    db05 = [o for o in observations if "row=DB05" in o.locator.note]
    assert len(db05) == 1
    observation = db05[0]
    assert observation.value.point == Decimal("5874")
    assert observation.identity.temperature_K.value == Decimal("1141.15")
    assert observation.point_conditions["log_fO2_nominal"].state.value == Decimal("-0.68")
    assert observation.locator.published_page == 40
    assert not migrator.result.validation.hard_issues


def test_all_figure_records_are_deferred_with_identity() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    figures = [
        row for row in extract["species"]["Te"]["context"]
        if row.get("type") == "figure_only"
    ]
    assert len(figures) == 5
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all(row["values"]["identity"]["temperature_as_printed"] for row in figures)
    assert all(row["values"]["panel_series_marker_counts"] for row in figures)
