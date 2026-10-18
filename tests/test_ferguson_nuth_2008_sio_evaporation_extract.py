from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import REPO_ROOT
from simulator.battery.validate import validate_corpus
from tests.battery.test_migrate import _migrate_real_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


EXTRACT_NAME = "ferguson-nuth-2008-sio-vapor-pressure-evaporation-coefficient.yaml"
EXTRACT = REPO_ROOT / "data/literature/extracts" / EXTRACT_NAME
OBSERVATION_ID = "ferguson_nuth_2008_table1_mass_loss_rate"
ROW_PREFIX = (
    "ferguson-nuth-2008-sio-vapor-pressure-evaporation-coefficient::"
    f"{OBSERVATION_ID}::"
)


def test_table_rows_migrate_temperature_conditions_and_source_pins(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []
    points = document["species"]["SiO"]["observations"][0]["values"]["points"]
    assert len(points) == 42

    changed_pin = deepcopy(document)
    changed_pin["species"]["SiO"]["observations"][0]["values"]["points"][0][
        "mass_loss_rate"
    ] = 0.0072
    assert check_all_fidelity_samples_match(changed_pin, label=str(EXTRACT))

    result = _migrate_real_extract(tmp_path / "base", EXTRACT_NAME)
    rows = {
        int(observation_id.split(":row=", 1)[1].split(":", 1)[0]): observation
        for observation_id, observation in result.observations.items()
        if observation_id.startswith(ROW_PREFIX)
    }
    assert len(rows) == 42

    for point in points:
        row = point["row"]
        observation = rows[row]
        assert observation.identity.temperature_K.is_value
        assert observation.identity.temperature_K.value == Decimal(
            str(point["T_C"])
        ) + Decimal("273.15")
        assert observation.value.point == Decimal(str(point["mass_loss_rate"]))
        assert observation.uncertainty.kind.value == "printed"
        condition = observation.point_conditions["orifice_diameter_mm"]
        assert condition.state.is_value
        assert condition.state.value == Decimal(str(point["d_mm"]))
        assert condition.locator is not None

    validation = validate_corpus(
        result.works,
        result.experiments,
        result.observations,
        residuals=None,
    )
    assert validation.hard_issues == ()

    changed_temperature = deepcopy(document)
    changed_temperature["species"]["SiO"]["observations"][0]["values"]["points"][0][
        "T_K_as_printed"
    ] = 1310
    changed_result = _migrate_real_extract(
        tmp_path / "temperature-precedence",
        EXTRACT_NAME,
        extract_doc=changed_temperature,
    )
    changed_row = next(
        observation
        for observation_id, observation in changed_result.observations.items()
        if observation_id.startswith(ROW_PREFIX) and ":row=1" in observation_id
    )
    assert changed_row.identity.temperature_K.value == Decimal("1301.00")
