from __future__ import annotations

from decimal import Decimal


def test_basu_2008_extract_migrates_and_pins_printed_activities(tmp_path) -> None:
    from tests.battery.test_migrate import _migrate_real_extract

    result = _migrate_real_extract(
        tmp_path, "basu-2008-feo-steelmaking.yaml"
    )
    rows = {
        row.observation_id.split("::", 1)[-1]: row
        for row in result.observations.values()
        if row.source_id == "basu-2008-feo-steelmaking"
    }

    assert len(rows) == 216
    # Table II, p. 452: a(FeO) = 0.524 for Solution 64.
    assert rows["basu_2008_tii_s64_a_feo_1873k"].value.point == Decimal("0.524")
    # Table III, p. 452: a(FeO) = 0.36 for Solution 20.
    assert rows["basu_2008_tiii_s20_a_feo_1923k"].value.point == Decimal("0.36")
