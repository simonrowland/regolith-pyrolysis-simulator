from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import AmountBasis, Engine
from simulator.battery.score import predict_with_engine
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2019-perovskite-evaporation.yaml"


def test_shornikov_2019_perovskite_table_1_migrates_and_pins_printed_pressures():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(
        ROOT,
        EXTRACT,
        index={document["source_id"]: dict(document["source"])},
        aliases={},
    )
    migrator.finalize()

    assert migrator.result.validation is not None
    assert migrator.result.validation.ok
    assert migrator.result.validation.hard_issues == ()
    assert len(migrator.result.observations) == 76
    for row in migrator.result.observations.values():
        assert row.identity.temperature_K.is_value
        assert row.point_conditions is not None
        assert row.point_conditions["temperature_K"].state.is_value
        composition = row.point_conditions["composition"]
        assert composition.state.is_value
        assert composition.locator.published_page == 253
        assert composition.state.value.basis == "printed_formula_stoichiometry"
        assert composition.state.value.components == (
            ("CaO", Decimal("1")),
            ("TiO2", Decimal("1")),
        )
        assert composition.state.value.amount_basis is AmountBasis.MOL_INVENTORY
        assert composition.state.value.proxy_flag == "initial_charge_only"
        measured = row.point_conditions["measurement_state_composition"]
        assert measured.state.is_unknown
        assert measured.locator.published_page == 257
        for field in ("run_order_index", "depletion_evidence", "vaporization_mode"):
            assert row.point_conditions[field].state.is_unknown
            assert row.point_conditions[field].locator.published_page == 257
        oxygen = row.point_conditions["fO2_Pa"]
        assert oxygen.state.is_unknown
        assert oxygen.locator.published_page == 257
        assert row.identity.total_pressure_Pa.is_unknown

    experiment = migrator.result.experiments[
        next(iter(migrator.result.observations.values())).experiment_id
    ]
    assert experiment.sample.initial_composition.state.is_value
    assert experiment.sample.initial_composition.locator.published_page == 253
    assert experiment.sample.initial_composition.state.value.proxy_flag == "initial_charge_only"
    assert experiment.sample.initial_composition.state.value.components == (
        ("CaO", Decimal("1")),
        ("TiO2", Decimal("1")),
    )
    pressure = experiment.pressure_environment.total_pressure_Pa
    assert pressure.state.is_unknown
    assert pressure.locator.published_page == 257

    ca = document["species"]["Ca"]["observations"][0]["values"]["points"]
    cao = document["species"]["CaO"]["observations"][0]["values"]["points"]
    assert (ca[0]["T_K"], ca[0]["pressure_atm"]) == (1791, 2.08e-9)
    assert (cao[0]["T_K"], cao[0]["pressure_atm"]) == (1920, 3.93e-12)


def test_removing_printed_perovskite_composition_restores_scorer_refusal(tmp_path):
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    for block in document["species"].values():
        for observation in block["observations"]:
            observation["point_conditions"].pop("composition")
    document["experiments"][0]["sample"]["initial_composition"] = {
        "state": {"tag": "unknown", "reason": "not_published"},
        "locator": {
            "published_page": 257,
            "pdf_page_index": 4,
            "section": "Experimental",
            "note": "The analyzed starting composition is not reported.",
        },
    }

    root = tmp_path / "mutated"
    path = root / "data/literature/extracts/shornikov-2019-perovskite-evaporation.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    migrator = migrate_one_extract(
        root,
        path,
        index={document["source_id"]: dict(document["source"])},
        aliases={},
    )
    migrator.finalize()

    row = next(iter(migrator.result.observations.values()))
    assert row.identity.composition.is_unknown
    prediction = predict_with_engine(Engine.OPENIMCC, row)
    assert prediction.refusal_detail["reason"] == "composition_not_stated_pure_reservoir"
