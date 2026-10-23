from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import AdmissionStatus, Quantity, ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/ivanova-2018-spinel-rich-cai-evaporation.yaml"


def test_ivanova_2018_residue_series_migrates_and_pins_table_1_values():
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
    assert len(migrator.result.observations) == 32
    for observation in migrator.result.observations.values():
        assert observation.identity.quantity.is_value
        assert (
            observation.identity.quantity.value
            is Quantity.RESIDUE_COMPONENT_COMPOSITION
        )
        assert observation.value.kind is ValueKind.POINT
        assert observation.admission.status is AdmissionStatus.ADMITTED
        assert observation.identity.temperature_K.is_value
        assert observation.identity.temperature_K.value == Decimal("2173.15")
        assert observation.point_conditions is not None
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal("2173.15")
        assert temperature.locator is not None
        printed_composition = observation.point_conditions["printed_composition"]
        assert printed_composition.state.is_value
        assert printed_composition.locator is not None
        elapsed = observation.point_conditions["time_min"]
        assert elapsed.state.is_value
        assert elapsed.locator is not None
    series = document["species"]["CaO"]["observations"][0]["values"]["series"]
    oxides = ("SiO2", "Al2O3", "MgO", "CaO")
    rows_by_composition = {
        tuple(
            (oxide, Decimal(str(row[f"{oxide}_wt_pct"])))
            for oxide in oxides
        ): row
        for row in series
    }
    for observation in migrator.result.observations.values():
        printed = observation.point_conditions["printed_composition"].state.value
        composition_key = tuple(
            (oxide, Decimal(str(printed[oxide]))) for oxide in oxides
        )
        source_row = rows_by_composition[composition_key]
        formula = observation.identity.species.formula
        assert observation.value.point == Decimal(
            str(source_row[f"{formula}_wt_pct"])
        )

    migrated_pins = {
        ("SiO2", Decimal("33.71")),
        ("MgO", Decimal("16.02")),
        ("SiO2", Decimal("13.95")),
        ("CaO", Decimal("23.29")),
    }
    assert migrated_pins <= {
        (observation.identity.species.formula, observation.value.point)
        for observation in migrator.result.observations.values()
    }
    first = next(row for row in series if row["sample"] == "5aN-3")
    later = next(row for row in series if row["sample"] == "5aN-2")
    assert first["MgO_wt_pct"] == 16.02
    assert later["SiO2_wt_pct"] == 13.95
