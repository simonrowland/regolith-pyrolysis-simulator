from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml


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


def test_mathieu_extract_does_not_land_caption_fo2_as_a_run_condition() -> None:
    path = (
        Path(__file__).parents[1]
        / "data/literature/extracts/mathieu-2009-sodium-solubility-thesis.yaml"
    )
    document = yaml.safe_load(path.read_text())

    def mappings(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from mappings(child)
        elif isinstance(value, list):
            for child in value:
                yield from mappings(child)

    conflicting_rows = [
        row["observation_id"]
        for row in mappings(document)
        if isinstance(row.get("values"), dict)
        and "oxygen_partial_pressure" in row["values"]
        and "fO2_not_landed_reason" in row["values"]
    ]
    assert conflicting_rows == []
