from pathlib import Path

import yaml

from simulator.battery.enums import Quantity, StateTag, ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/ferguson-nuth-2012-sio-vapor-pressure-mixture.yaml"


def test_ferguson_nuth_table_rows_and_printed_alpha_migrate() -> None:
    raw = yaml.safe_load(EXTRACT.read_text())
    observations = raw["species"]["SiO"]["observations"]
    table1 = observations[0]["values"]["points"]
    table2_pressure = observations[1]["values"]["points"]

    # Table 1 has 59 rows on PDF p. 4 and 12 continuation rows on p. 5.
    assert len(table1) == 71
    assert sum(row["locator"]["page"] == 4 for row in table1) == 59
    assert sum(row["locator"]["page"] == 5 for row in table1) == 12
    # Render pins from Table 1, PDF pp. 4–5.
    assert table1[0]["T_K"] == "1433"
    assert table1[0]["mass_loss_rate_mg_min_as_printed"] == "0.0439 ± 0.0050"
    assert table1[0]["apparent_pressure_Pa_as_printed"] == "2.15 ± 0.27"
    assert table1[-1]["effusion_orifice_area_cm2"] == "0.01217"
    assert table1[-1]["mass_loss_mg"] == "15.13"
    assert table1[-1]["apparent_pressure_Pa_as_printed"] == "28.0 ± 1.3"

    # Table 2 prints 19 fitted equilibrium pressure rows; these stay refused
    # because the current p_sat identity cannot represent the two-solid system.
    assert len(table2_pressure) == 19
    assert table2_pressure[0]["equilibrium_pressure_Pa_as_printed"] == "5.7 ± 2.2"
    assert table2_pressure[-1]["equilibrium_pressure_Pa_as_printed"] == "128 ± 74"
    alpha_source_rows = observations[2:]
    assert len(alpha_source_rows) == 19
    assert alpha_source_rows[0]["values"]["alpha_as_printed"] == "0.0089 ± 0.0040"
    assert alpha_source_rows[-1]["values"]["alpha_as_printed"] == "0.0054 ± 0.0027"

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    migrated = list(migrator.result.observations.values())
    assert len(migrated) == 109
    assert not migrator.result.validation.hard_issues
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated) == 90

    table1_rows = [row for row in migrated if "table1_raw_effusion_rows" in row.observation_id]
    alpha_rows = [row for row in migrated if "ferguson_nuth_2012_alpha_T" in row.observation_id]
    pressure_rows = [row for row in migrated if "table2_equilibrium_pressure_rows" in row.observation_id]
    assert (len(table1_rows), len(alpha_rows), len(pressure_rows)) == (71, 19, 19)
    assert all(row.identity.quantity.tag is StateTag.UNKNOWN for row in table1_rows)
    assert all(row.identity.quantity.tag is StateTag.UNKNOWN for row in pressure_rows)
    assert all(row.identity.quantity.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA for row in alpha_rows)

    # Every run carries only printed T and orifice geometry as conditions.
    # Apparent pressure and fitted equilibrium pressure are not total pressure.
    for row in table1_rows:
        assert row.identity.temperature_K.tag is StateTag.VALUE
        assert "temperature_K" in row.point_conditions
        assert "orifice_diameter_m" in row.point_conditions
        assert "total_pressure_Pa" not in row.point_conditions
        assert "fO2_Pa" not in row.point_conditions
        assert "composition" not in row.point_conditions
        assert row.identity.total_pressure_Pa is None
        assert row.identity.fO2_Pa is None
    for row in alpha_rows:
        assert row.identity.temperature_K.tag is StateTag.VALUE
        assert set(row.point_conditions) == {"temperature_K"}
        assert row.identity.total_pressure_Pa.tag is StateTag.UNKNOWN
        assert row.identity.fO2_Pa.tag is StateTag.UNKNOWN
        assert row.identity.composition.tag is StateTag.UNKNOWN
