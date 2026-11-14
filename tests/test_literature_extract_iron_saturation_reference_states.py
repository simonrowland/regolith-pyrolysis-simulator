from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import Authority, Quantity
from simulator.battery.records import Located, StandardState
from simulator.battery.score import iron_saturation_composition, temperature_of
from tests.battery.test_migrate import _migrate_real_extract


SOURCE_COUNTS = {
    "distin-1971-ferrous-silicate-melts": 24,
    "roeder-1974-iron-activity-olivine-basaltic-liquids": 90,
    "doyle-1988-feo-activity-multicomponent-magma": 130,
    "doyle-1989-tio2-feo-activity-magma": 167,
}
TEMPERATURE_CONDITION = {
    "distin-1971-ferrous-silicate-melts": "temperature_K",
    "roeder-1974-iron-activity-olivine-basaltic-liquids": "T_C",
    "doyle-1988-feo-activity-multicomponent-magma": "temperature_K",
    "doyle-1989-tio2-feo-activity-magma": "T_K",
}


def _isolated_migration(tmp_path: Path, source_id: str):
    return _migrate_real_extract(
        tmp_path / source_id,
        f"{source_id}.yaml",
        write=False,
        use_repository_index_row=True,
    )


@pytest.mark.parametrize(("source_id", "expected_count"), SOURCE_COUNTS.items())
def test_reference_sources_migrate_in_isolation_with_conditions(
    tmp_path: Path, source_id: str, expected_count: int
) -> None:
    result = _isolated_migration(tmp_path, source_id)
    assert len(result.observations) == expected_count
    assert not result.validation.hard_issues
    temperature_key = TEMPERATURE_CONDITION[source_id]
    typed = 0
    # Mutation: remove a source's typed state or located temperature coordinate and this fails.
    for row in result.observations.values():
        identity = row.identity
        quoted_stoich = (
            source_id == "doyle-1988-feo-activity-multicomponent-magma"
            and row.observation_id.endswith(
                "doyle_1988_quoted_activity_stoichiometric_liquid_feo_1_93"
            )
        )
        assert identity.reference_state is not None
        row_temperature_key = temperature_key
        if quoted_stoich:
            assert identity.reference_state.is_value
            assert isinstance(identity.reference_state.value, StandardState)
            assert identity.reference_state.value.endmember.formula == "FeO"
            assert identity.reference_state.value.iron_saturation_composition is None
            row_temperature_key = "T_C"
        else:
            assert identity.reference_state.is_value
            assert isinstance(identity.reference_state.value, StandardState)
            assert identity.reference_state.value.endmember.formula == "Fe(1-x)O"
            typed += 1
        assert temperature_of(identity) is not None
        assert row.point_conditions
        temperature = row.point_conditions[row_temperature_key]
        assert isinstance(temperature, Located)
        assert temperature.locator is not None
        assert temperature.state.is_value
        assert identity.quantity.value in {
            Quantity.ACTIVITY,
            Quantity.ACTIVITY_COEFFICIENT,
        }
    assert typed == expected_count - (
        1 if source_id == "doyle-1988-feo-activity-multicomponent-magma" else 0
    )


def test_rule_precedence_uses_roeder_dg_and_doyle_coughlin(tmp_path: Path) -> None:
    # Mutation: changing either payload from D&G/cited to prose breaks its route.
    roeder = _isolated_migration(
        tmp_path,
        "roeder-1974-iron-activity-olivine-basaltic-liquids"
    )
    roeder_row = next(iter(roeder.observations.values()))
    roeder_decision = iron_saturation_composition(roeder_row)
    assert roeder_decision.notice == "fe_saturation_composition_owner_supplied"
    assert roeder_decision.authority is Authority.EXTRAPOLATED
    assert roeder_decision.extrapolated
    assert roeder_decision.n_fe2_plus is not None

    for source_id in (
        "doyle-1988-feo-activity-multicomponent-magma",
        "doyle-1989-tio2-feo-activity-magma",
    ):
        migration = _isolated_migration(tmp_path, source_id)
        row = next(iter(migration.observations.values()))
        decision = iron_saturation_composition(row)
        assert decision.notice == "fe_saturation_composition_from_cited_standard"
        assert decision.n_fe2_plus == Decimal("0.888067581837381203801478352")
        assert decision.band_dex is None
        assert decision.authority is None
