"""Source-grounded pins for Yamaguchi and Goto (1984)."""

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery import migrate as battery_migrate
from tests.battery import factories as battery_factories

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "data/literature/extracts/yamaguchi-1984-na2o-activity-sodium-beta-alumina.yaml"


def test_abstract_activity_migrates_with_printed_conditions() -> None:
    """Pin the abstract point and its identity to the rendered source pages."""
    extract = battery_migrate.load_yaml(SOURCE)
    row = extract["species"]["Na2O"]["observations"][0]
    point = row["values"]["points"][0]

    # The page 43 render gives the reported approximate activity, composition,
    # and temperature; equation (1) is on page 44. Table 1 is not an activity
    # point table: its seven rows are fit coefficients.
    assert point["activity"] == "1e-13"
    assert point["T_K"] == "1573"
    assert point["composition_mol"] == {"Na2O": "0.5", "P2O5": "0.5"}
    assert row["locator"]["published_page"] == 43
    assert row["values"]["reduction_locator"] == {
        "page": 2,
        "published_page": 44,
        "equation": "1",
        "note": "Nernst relation for the cell EMF.",
    }

    migrator = battery_factories.migrate_one_extract(
        REPO_ROOT,
        SOURCE,
        index={extract["source_id"]: dict(extract["source"], source_id=extract["source_id"])},
        aliases={},
    )
    migrator.finalize()
    assert len(migrator.result.observations) == 1
    observation = next(iter(migrator.result.observations.values()))
    assert observation.value.point == Decimal("1e-13")
    assert observation.identity.temperature_K.value == Decimal("1573")
    assert observation.identity.composition.is_value
    assert observation.identity.composition.value.components == (
        ("Na2O", Decimal("0.5")),
        ("P2O5", Decimal("0.5")),
    )
    standard_state = observation.identity.reference_state.value
    assert standard_state.endmember.formula == "Na2O"
    assert standard_state.endmember.phase.value.value == "l"
    assert observation.identity.fO2_Pa.is_unknown
    assert observation.identity.total_pressure_Pa.is_unknown
    assert observation.point_conditions["temperature_K"].state.value == Decimal("1573")
    assert observation.point_conditions["fO2_Pa"].state.is_unknown
    assert "total_pressure_Pa" not in observation.point_conditions
    assert (
        migrator.result.experiments[observation.experiment_id].method.value.value
        == "emf_cell"
    )
