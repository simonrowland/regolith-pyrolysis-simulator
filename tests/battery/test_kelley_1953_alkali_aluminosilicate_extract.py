from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import simulator.battery.migrate as migrate_module
from simulator.battery.enums import Phase, Polymorph, Quantity, ValueKind


_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXTRACT = (
    _REPO_ROOT
    / "data/literature/extracts/kelley-1953-alkali-aluminosilicate-thermodynamics.yaml"
)
_SOURCE_ID = "kelley-1953-alkali-aluminosilicate-thermodynamics"


def _migrated():
    with patch.object(migrate_module, "discover_extracts", return_value=[_EXTRACT]):
        migrator = migrate_module.Migrator(root=_REPO_ROOT)
        migrator.migrate_extracts()
        migrator.finalize()
    return migrator.result


def _point(result, *, observation_fragment: str, temperature: str):
    matches = [
        observation
        for observation in result.observations.values()
        if observation.source_id == _SOURCE_ID
        and observation.observation_id.split("::", 1)[-1].startswith(observation_fragment)
        and observation.point_conditions["temperature_K"].state.value
        == Decimal(temperature)
    ]
    assert len(matches) == 1
    return matches[0]


def test_kelley_printed_thermochemistry_rows_migrate_with_cells_and_conditions():
    result = _migrated()
    observations = [
        observation
        for observation in result.observations.values()
        if observation.source_id == _SOURCE_ID
    ]
    assert len(observations) == 434
    assert all(observation.identity.quantity.is_value for observation in observations)
    assert all(observation.identity.species.phase.is_value for observation in observations)
    assert all(
        observation.point_conditions["temperature_K"].state.is_value
        for observation in observations
    )
    assert all(
        observation.identity.species.phase.value is Phase.CR
        for observation in observations
    )
    assert all(
        observation.identity.standard_pressure_Pa.is_unknown
        for observation in observations
    )
    assert {
        issue.path.rsplit(".", 1)[-1]
        for issue in result.validation.issues
    } == {"standard_pressure_Pa"}
    assert len(result.validation.issues) == len(observations)

    pins = [
        (
            _point(result, observation_fragment="kelley_1953_table09_cp", temperature="53.92"),
            Quantity.CP,
            "36.69368",
            16,
            "9",
        ),
        (
            _point(result, observation_fragment="kelley_1953_table11_cp", temperature="53.84"),
            Quantity.CP,
            "13.200520",
            17,
            "11",
        ),
        (
            _point(result, observation_fragment="kelley_1953_table26_heat_content", temperature="472.7"),
            Quantity.H_MINUS_H298,
            "40.41744",
            28,
            "26",
        ),
        (
            _point(result, observation_fragment="kelley_1953_table32_heat_content", temperature="389.0"),
            Quantity.H_MINUS_H298,
            "11.52692",
            33,
            "32",
        ),
    ]
    for observation, quantity, value, page, table in pins:
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == Decimal(value)
        assert observation.identity.quantity.value is quantity
        assert observation.point_conditions["temperature_K"].state.value is not None
        assert observation.locator.page == page
        assert observation.locator.table == table

    nephelite_transition = [
        observation
        for observation in observations
        if "table31_heat_content" in observation.observation_id
        and observation.point_conditions["temperature_K"].state.value
        == Decimal("467")
    ]
    assert len(nephelite_transition) == 2
    assert {
        observation.identity.species.polymorph.value
        for observation in nephelite_transition
    } == {Polymorph.DELTA, Polymorph.BETA}

    carnegieite_transition = [
        observation
        for observation in observations
        if "table33_heat_content" in observation.observation_id
        and observation.point_conditions["temperature_K"].state.value
        == Decimal("980")
    ]
    assert len(carnegieite_transition) == 2
    assert {
        observation.identity.species.polymorph.value
        for observation in carnegieite_transition
    } == {Polymorph.DELTA, Polymorph.BETA}
