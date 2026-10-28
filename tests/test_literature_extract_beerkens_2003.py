from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/beerkens-2003-sulfate-decomposition-na2o-activity.yaml"


def _assert_source_pins(document: dict) -> None:
    species = document["species"]["Na2O"]
    contexts = species["context"]
    observations = species["observations"]
    assert len(contexts) == 2
    assert len(observations) == 1

    model_fit = contexts[0]["values"]["fit"]
    float_fit = contexts[1]["values"]["fit"]
    point = observations[0]
    assert model_fit == {
        "inverse_temperature_coefficient_as_printed": "-43552",
        "intercept_as_printed": "8.33",
    }
    assert float_fit == {
        "inverse_temperature_coefficient_as_printed": "-46000",
        "intercept_as_printed": "9.50",
    }
    assert point["values"]["activity"] == 1.7e-8
    assert all(row["locator"]["page"] == 6 for row in (*contexts, *observations))
    assert [row["observation_id"] for row in (*contexts, *observations)] == [
        "beerkens_2003_model_glass_activity_temperature_fit",
        "beerkens_2003_float_glass_activity_temperature_fit",
        "beerkens_2003_float_glass_activity_at_1673K",
    ]


def test_beerkens_source_pins_reject_a_mutated_render_value() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_source_pins(document)
    mutated = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated["species"]["Na2O"]["context"][0]["values"]["fit"][
        "inverse_temperature_coefficient_as_printed"
    ] = "-43553"
    try:
        _assert_source_pins(mutated)
    except AssertionError:
        pass
    else:
        raise AssertionError("mutating the rendered fit coefficient did not fail the pin")


def test_beerkens_activity_identity_survives_isolated_migration() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_source_pins(document)
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert migrator.result.registry_issues == []
    assert len(migrator.result.observations) == 1
    scalar = next(iter(migrator.result.observations.values()))
    assert scalar.observation_id.endswith(
        "::beerkens_2003_float_glass_activity_at_1673K"
    )
    assert scalar.identity.quantity.value is Quantity.ACTIVITY
    assert scalar.value.point == Decimal("1.7E-8")
    assert scalar.value.approximate
    assert scalar.identity.temperature_K.is_value
    assert scalar.identity.temperature_K.value == Decimal("1673")
    assert scalar.identity.composition.is_value
    assert Decimal("0.12") < dict(scalar.identity.composition.value.components)["Na2O"] < Decimal("0.13")
    assert migrator.result.validation.hard_issues == ()
    assert sum(len(rows) for rows in migrator.result.context_by_work.values()) == 2
