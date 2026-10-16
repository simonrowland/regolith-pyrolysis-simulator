from __future__ import annotations

from decimal import Decimal

from simulator.battery.enums import Phase


def test_shornikov_2020_extract_migrates_and_pins_printed_values(tmp_path) -> None:
    from tests.battery.test_migrate import _migrate_real_extract

    result = _migrate_real_extract(
        tmp_path, "shornikov-2020-cao-tio2-melts.yaml"
    )
    rows = {
        row.observation_id.split("::", 1)[-1]: row
        for row in result.observations.values()
        if row.source_id == "shornikov-2020-cao-tio2-melts"
    }

    assert len(rows) == 302
    assert result.validation.ok
    assert sum(
        issue.reason.value == "referential_integrity"
        for issue in result.validation.hard_issues
    ) == 0
    assert all(row.identity.temperature_K.is_value for row in rows.values())
    assert all(
        row.point_conditions["temperature_K"].state.is_value
        and row.point_conditions["temperature_K"].locator is not None
        for row in rows.values()
    )
    # Table 3, p. 1295: pCa = 2.33 × 10^-5 atm, converted to Pa by migration.
    assert rows["shornikov_2020_table3_ca_partial_pressure_2278k"].value.point == Decimal(
        "2.3608725"
    )
    gas = rows["shornikov_2020_table3_ca_partial_pressure_2278k"]
    assert gas.identity.species.phase.value is Phase.G
    # Table 5, p. 1296: a(CaO) = 0.246, dimensionless.
    assert rows["shornikov_2020_table5_cao_eq17_2278k"].value.point == Decimal(
        "0.246"
    )
    activity = rows["shornikov_2020_table5_tio2_eq22_2278k"]
    assert activity.identity.species.phase.value is Phase.L
    assert activity.identity.reference_state.value.endmember.phase.is_unknown
