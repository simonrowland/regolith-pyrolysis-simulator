from __future__ import annotations

from decimal import Decimal


def test_mathieu_2009_extract_migrates_and_pins_printed_activity_values(
    tmp_path,
) -> None:
    from tests.battery.test_migrate import _migrate_real_extract

    result = _migrate_real_extract(
        tmp_path, "mathieu-2009-sodium-solubility-thesis.yaml"
    )
    rows = {
        row.observation_id.split("::", 1)[-1]: row
        for row in result.observations.values()
        if row.source_id == "mathieu-2009-sodium-solubility-thesis"
    }

    assert len(rows) == 421
    # Table IV.1, p. 136: 0.85 × 10^-6 for ACMAS5 / AS1.
    assert rows["mathieu_2009_t38_acmas5_as1_gamma_na2o"].value.point == Decimal(
        "8.5e-7"
    )
    # Table IV.7, pp. 158–159: 1.12 × 10^-7 for ACMAS5 / Na1.
    assert rows["mathieu_2009_t44_acmas5_na1_gamma_na2o"].value.point == Decimal(
        "1.12e-7"
    )
