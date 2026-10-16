from __future__ import annotations

from decimal import Decimal


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
    # Table 3, p. 1295: pCa = 2.33 × 10^-5 atm, converted to Pa by migration.
    assert rows["shornikov_2020_table3_ca_partial_pressure_2278k"].value.point == Decimal(
        "2.3608725"
    )
    # Table 5, p. 1296: a(CaO) = 0.246, dimensionless.
    assert rows["shornikov_2020_table5_cao_eq17_2278k"].value.point == Decimal(
        "0.246"
    )
