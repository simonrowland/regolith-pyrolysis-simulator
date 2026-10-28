from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Phase, Quantity
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
    assert [row["locator"]["page"] for row in contexts] == [5, 5]
    assert [row["locator"]["page"] for row in observations] == [6]
    assert [row["locator"]["published_page"] for row in (*contexts, *observations)] == [1897, 1897, 1898]
    assert point["locator"]["section"] == "VI. Results for the Sodium Oxide Activity"
    assert point["values"]["evidence_class"] == "measured_tabulated"
    assert "attribution" not in point["values"]
    assert "Ka = exp(-78616/T + 28.195)" in point["values"]["derivation_as_published"]
    assert point["point_conditions"]["pO2_Pa"]["state"]["value"] == 33000
    assert point["point_conditions"]["pSO2_Pa"]["state"]["value"] == 66600
    assert contexts[0]["values"]["standard_state_as_published"].endswith("PDF p. 1 / published p. 1893, §II).")
    assert [row["values"]["temperature_range_K_as_published"] for row in contexts] == [
        "between about 1600 and 1800 K",
        "between about 1600 and 1800 K",
    ]
    assert point["values"]["estimated_error_component_1_as_published"] == "15-20 %"
    assert point["values"]["estimated_error_component_2_as_published"] == "15 %"
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
    assert scalar.identity.species.phase.is_value
    assert scalar.identity.species.phase.value is Phase.L
    assert scalar.value.point == Decimal("1.7E-8")
    assert scalar.value.approximate
    assert scalar.evidence.class_.value is EvidenceClass.MEASURED_TABULATED
    assert scalar.point_conditions["pO2_Pa"].state.value == Decimal("33000")
    assert scalar.point_conditions["pSO2_Pa"].state.value == Decimal("66600")
    assert scalar.identity.temperature_K.is_value
    assert scalar.identity.temperature_K.value == Decimal("1673")
    assert scalar.identity.composition.is_value
    assert dict(scalar.identity.composition.value.components)["Na2O"] == Decimal(
        "0.1297636248391307182437973734"
    )
    assert migrator.result.validation.hard_issues == ()
    assert sum(len(rows) for rows in migrator.result.context_by_work.values()) == 2
    assert all(row.identity.quantity.is_value for row in migrator.result.observations.values())
    assert all(row.identity.species.phase.is_value for row in migrator.result.observations.values())
