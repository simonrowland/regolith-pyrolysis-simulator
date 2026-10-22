from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import AdmissionStatus, Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tests.battery.test_migrate import _migrate_real_extract


EXTRACT = REPO_ROOT / "data/literature/extracts/gunther-1958-vapor-pressure-evaporation.yaml"

_TABLE_PINS = {
    "SiO": {
        "count": 18,
        "page": 5,
        "table": "1",
        "pins": {"1": ("1468", "1300"), "10": ("1439", "632"), "17": ("1202", "0.98")},
    },
    "B2O3": {
        "count": 18,
        "page": 6,
        "table": "3",
        "pins": {"10": ("1268", "1.42"), "13": ("1468", "129"), "5": ("1593", "845")},
    },
}


def test_gunther_1958_table_rows_match_render_and_migrate_with_conditions(tmp_path: Path) -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    expected_temps: dict[str, Decimal] = {}

    for species, table in _TABLE_PINS.items():
        observation = doc["species"][species]["observations"][0]
        points = observation["values"]["points"]
        assert len(points) == table["count"] == 18
        assert observation["admission_status"] == "typed_refusal"
        assert observation["values"]["pressure_scale_as_published"].startswith("p·10^4 [Torr]")
        for point in points:
            row = point["experiment_number"]
            key = f"{species}:{row}"
            expected_temps[key] = Decimal(point["T_K"])
            assert point["pressure_as_printed"]
            assert point["P"] == float(point["pressure_as_printed"])
            assert point["value"] == point["P"]
            assert point["locator"]["page"] == table["page"]
            assert point["locator"]["table"] == table["table"]
            assert Decimal(point["point_conditions"]["temperature_K"]["state"]["value"]) == Decimal(point["T_K"])
            assert point["point_conditions"]["temperature_K"]["locator"]["table"] == table["table"]
        for row, (temperature, pressure) in table["pins"].items():
            point = next(point for point in points if point["experiment_number"] == row)
            assert (str(point["T_K"]), point["pressure_as_printed"]) == (temperature, pressure)

    result = _migrate_real_extract(
        tmp_path,
        EXTRACT.name,
        extract_doc=doc,
    )
    migrated = [
        observation for observation in result.observations.values()
        if observation.source_id == "gunther-1958-vapor-pressure-evaporation"
    ]
    assert len(migrated) == 36
    assert not result.validation.hard_issues
    assert len(result.observations_by_source["data/literature/extracts/gunther-1958-vapor-pressure-evaporation.yaml"]) == 36
    assert sum(observation.value.kind is ValueKind.UNAVAILABLE for observation in migrated) == 36
    for observation in migrated:
        assert observation.identity.quantity.value is Quantity.P_SAT
        assert observation.admission.status is AdmissionStatus.REJECTED
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.is_value
        run_number = observation.locator.note.split(";", 1)[0].removeprefix("Versuch ")
        assert temperature.state.value == expected_temps[
            f"{observation.identity.species.formula}:{run_number}"
        ]
        assert temperature.locator is not None
        assert temperature.locator.table in {"1", "3"}
