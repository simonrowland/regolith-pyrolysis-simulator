from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.test_migrate import _migrate_real_extract


EXTRACT = "wahlbeck-1971-effusion-thermodynamics-final-report-coo-1029-39.yaml"
OBSERVATION_PREFIX = "wahlbeck_1971_table_ix_tio_run"
TABLE_IX = (
    (2, "2010", "3.1848e-7"),
    (3, "2022", "3.6856e-7"),
    (4, "1995", "2.7056e-7"),
    (5, "2032", "4.3096e-7"),
    (6, "1977", "1.6208e-7"),
    (7, "2043", "5.6656e-7"),
    (8, "1968", "1.3344e-7"),
    (9, "1957", "1.3328e-7"),
    (10, "1947", "0.8944e-7"),
    (11, "1985", "2.4624e-7"),
    (12, "1936", "0.8704e-7"),
)


def test_wahlbeck_table_ix_tio_pressures_migrate_with_printed_conditions(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    document = yaml.safe_load(
        (repo_root / "data" / "literature" / "extracts" / EXTRACT).read_text()
    )
    observations = document["species"]["TiO"]["observations"]
    assert len(observations) == 11
    assert [row["locator"]["table"] for row in observations] == ["IX"] * 11
    assert [row["locator"]["page"] for row in observations] == [36] * 11
    assert all(
        row["point_conditions"]["temperature_K"]["locator"]["table"] == "IX"
        and row["point_conditions"]["temperature_K"]["locator"]["page"] == 36
        for row in observations
    )
    assert all(
        "total_pressure_Pa" not in row["point_conditions"]
        and "fO2_Pa" not in row["point_conditions"]
        for row in observations
    )
    assert "pressure_environment" not in document["experiments"][0]

    result = _migrate_real_extract(tmp_path, EXTRACT)
    assert len(result.observations) == 11
    assert not result.validation.hard_issues
    migrated = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in result.observations.values()
    }

    for run, temperature, pressure_atm in TABLE_IX:
        observation_id = f"{OBSERVATION_PREFIX}{run:02d}"
        row = migrated[observation_id]
        assert row.value.kind.value == "point"
        assert row.value.point == Decimal(pressure_atm) * Decimal("101325")
        temperature_condition = row.point_conditions["temperature_K"]
        assert temperature_condition.state.value == Decimal(temperature)
        assert temperature_condition.locator is not None
        assert temperature_condition.locator.page == 36
        assert temperature_condition.locator.table == "IX"
        assert row.identity.temperature_K.value == Decimal(temperature)
        assert row.identity.composition.is_unknown
        assert row.identity.fO2_Pa.is_unknown
        assert row.identity.total_pressure_Pa.is_unknown

    experiment = next(iter(result.experiments.values()))
    area = experiment.apparatus.geometry.orifice_area_m2
    assert area is not None and area.state.value.point == Decimal("1.445e-6")
    assert area.locator is not None and area.locator.page == 33
