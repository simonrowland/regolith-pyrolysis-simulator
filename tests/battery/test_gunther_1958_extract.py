from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import (
    AdmissionStatus,
    EvidenceClass,
    Quantity,
    ValueKind,
)
from simulator.battery.migrate import REPO_ROOT
from tests.battery.test_migrate import _migrate_real_extract


EXTRACT = REPO_ROOT / "data/literature/extracts/gunther-1958-vapor-pressure-evaporation.yaml"
TORR_TO_PA = Decimal(101325) / Decimal(760)

_TABLE_PINS = {
    "SiO": {
        "page": 5,
        "table": "1",
        "cells": {
            "1": ("1468", "1300"),
            "2": ("1520", "3690"),
            "3": ("1307", "22.4"),
            "4": ("1366", "110"),
            "5": ("1392", "195"),
            "6": ("1339", "50.7"),
            "7": ("1329", "41.0"),
            "8": ("1359", "89.0"),
            "9": ("1419", "377"),
            "10": ("1439", "632"),
            "11": ("1309", "20.7"),
            "12": ("1335", "44.1"),
            "13": ("1361", "99.0"),
            "14": ("1303", "23.2"),
            "15": ("1245", "3.44"),
            "16": ("1249", "4.35"),
            "17": ("1202", "0.98"),
            "18": ("1257", "5.26"),
        },
    },
    "B2O3": {
        "page": 6,
        "table": "3",
        "cells": {
            "1": ("1388", "21.6"),
            "2": ("1453", "67.1"),
            "3": ("1510", "177"),
            "4": ("1560", "436"),
            "5": ("1593", "845"),
            "6": ("1498", "186"),
            "7": ("1418", "35.3"),
            "8": ("1350", "9.65"),
            "9": ("1300", "3.56"),
            "10": ("1268", "1.42"),
            "11": ("1348", "13.4"),
            "12": ("1382", "33.3"),
            "13": ("1468", "129"),
            "14": ("1533", "384"),
            "15": ("1433", "66.8"),
            "16": ("1368", "16.4"),
            "17": ("1313", "4.75"),
            "18": ("1273", "1.63"),
        },
    },
}

_TABLE2_PINS = {
    "gunther_1958_table2_cc_schaefer_hoernle": {
        "coefficients": {"A": Decimal("11.00"), "B": Decimal("16800")},
        "method_class": "quoted_attributed",
        "attribution": "SCHÄFER u. HÖRNLE [9]",
        "evidence": EvidenceClass.QUOTED_ATTRIBUTED,
    },
    "gunther_1958_table2_cc_geld_kochnev": {
        "coefficients": {"A": Decimal("10.203"), "B": Decimal("16660")},
        "method_class": "quoted_attributed",
        "attribution": "GELD u. KOCHNEV [10]",
        "evidence": EvidenceClass.QUOTED_ATTRIBUTED,
    },
    "gunther_1958_table2_cc_tombs_welch": {
        "coefficients": {"A": Decimal("8.42"), "B": Decimal("12730")},
        "method_class": "quoted_attributed",
        "attribution": "TOMBS u. WELCH [11]",
        "evidence": EvidenceClass.QUOTED_ATTRIBUTED,
    },
    "gunther_1958_table2_cc_own_measurements": {
        "coefficients": {"A": Decimal("13.28"), "B": Decimal("20800")},
        "method_class": "model_derived",
        "attribution": None,
        "evidence": EvidenceClass.MODEL_DERIVED,
    },
}


def test_gunther_1958_pressure_cells_scale_and_migrate_with_conditions(tmp_path: Path) -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    expected_temps: dict[str, Decimal] = {}
    expected_pa: dict[str, Decimal] = {}

    for species, table in _TABLE_PINS.items():
        observation = doc["species"][species]["observations"][0]
        points = observation["values"]["points"]
        assert len(points) == len(table["cells"]) == 18
        assert observation["units"] == "Torr"
        assert observation["values"]["pressure_scale_as_published"].startswith("p·10^4 [Torr]")
        for point in points:
            row = point["experiment_number"]
            temperature, printed_pressure = table["cells"][row]
            assert str(point["T_K"]) == temperature
            assert point["pressure_as_printed"] == printed_pressure
            printed = Decimal(printed_pressure)
            pressure_torr = printed * Decimal("1e-4")
            assert Decimal(str(point["P"])) == pressure_torr
            assert Decimal(str(point["value"])) == pressure_torr
            assert point["locator"]["page"] == table["page"]
            assert point["locator"]["table"] == table["table"]
            assert Decimal(point["point_conditions"]["temperature_K"]["state"]["value"]) == Decimal(temperature)
            assert point["point_conditions"]["temperature_K"]["locator"]["table"] == table["table"]
            key = f"{species}:{row}"
            expected_temps[key] = Decimal(temperature)
            expected_pa[key] = pressure_torr * TORR_TO_PA

    result = _migrate_real_extract(
        tmp_path,
        EXTRACT.name,
        extract_doc=doc,
    )
    migrated = [
        observation for observation in result.observations.values()
        if observation.source_id == "gunther-1958-vapor-pressure-evaporation"
    ]
    assert len(migrated) == 40
    assert not result.validation.hard_issues
    assert len(result.observations_by_source["data/literature/extracts/gunther-1958-vapor-pressure-evaporation.yaml"]) == 40

    points = [observation for observation in migrated if observation.value.kind is ValueKind.POINT]
    assert len(points) == 36
    assert all(observation.admission.status is AdmissionStatus.ADMITTED for observation in points)
    for observation in points:
        assert observation.identity.quantity.value is Quantity.P_SAT
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.is_value
        run_number = observation.locator.note.split(";", 1)[0].removeprefix("Versuch ")
        key = f"{observation.identity.species.formula}:{run_number}"
        assert temperature.state.value == expected_temps[key]
        assert observation.value.point == expected_pa[key]
        assert temperature.locator is not None
        assert temperature.locator.table in {"1", "3"}

    first = next(observation for observation in points if observation.locator.note.startswith("Versuch 1;") and observation.locator.table == "1")
    assert first.value.point == Decimal("17.33190789473684210526315789")

    table2_rows = {
        observation["observation_id"]: observation
        for observation in doc["species"]["SiO"]["observations"][1:]
    }
    assert set(table2_rows) == set(_TABLE2_PINS)
    for observation_id, pin in _TABLE2_PINS.items():
        raw = table2_rows[observation_id]
        values = raw["values"]
        assert {key: Decimal(str(value)) for key, value in values["coefficients"].items()} == pin["coefficients"]
        assert values["source_form"] == "log10(P_Torr) = A - B/T_K"
        assert values["printed_equation"] == "log p = A − B/T"
        assert values["method_class"] == pin["method_class"]
        assert values["evidence_class"] == pin["evidence"].value
        assert raw["locator"]["table"] == "2"
        assert raw["locator"]["page"] == 5
        if pin["attribution"] is not None:
            assert values["attribution"] == pin["attribution"]

    own_fit = next(
        observation for observation in migrated
        if observation.observation_id.endswith("::gunther_1958_table2_cc_own_measurements")
    )
    table1_ids = {observation.observation_id for observation in points if observation.locator.table == "1"}
    assert set(own_fit.derived_from) == table1_ids
    assert own_fit.derivation is not None
    assert set(own_fit.derivation.inputs) == table1_ids
    assert own_fit.value.kind is ValueKind.UNAVAILABLE
    assert own_fit.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    for observation_id, pin in _TABLE2_PINS.items():
        row = next(observation for observation in migrated if observation.observation_id.endswith(f"::{observation_id}"))
        assert row.identity.quantity.value is Quantity.P_SAT
        assert row.evidence.class_.value is pin["evidence"]
