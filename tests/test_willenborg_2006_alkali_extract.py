from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import yaml

import simulator.battery.migrate as migrate_module
from simulator.battery.enums import ValueKind
from simulator.battery.migrate import wt_pct_to_mole_fraction


_ROOT = Path(__file__).resolve().parents[1]
_EXTRACT = _ROOT / "data/literature/extracts/willenborg-2006-alkali-removal-thermodynamics.yaml"
_AUDIT = _ROOT / "data/literature/extracts/audits/willenborg-2006-alkali-removal-thermodynamics.mapping.yaml"


def _migrated_source():
    migrator = migrate_module.Migrator(root=_ROOT)
    with patch.object(migrate_module, "discover_extracts", return_value=[_EXTRACT]):
        migrator.migrate_extracts()
        migrator.finalize()
    return migrator


def test_willenborg_text_activity_values_and_table_row_counts() -> None:
    extract = yaml.safe_load(_EXTRACT.read_text())
    audit = yaml.safe_load(_AUDIT.read_text())
    observations = extract["species"]["Na2O"]["observations"]
    assert len(observations) == 3
    assert audit["tables"][0]["printed_body_rows"] == 1
    assert audit["tables"][1]["printed_body_rows"] == 14

    # Render-verified prose values (PDF pages 2597) and Table 2 compositions
    # (PDF page 2594); the paper prints these values only approximately.
    values_by_id = {row["observation_id"]: row["values"] for row in observations}
    assert Decimal(str(values_by_id["willenborg_2006_alsi8na1_na2o_activity_1400"]["activity_coefficient"])) == Decimal("5e-14")
    assert Decimal(str(values_by_id["willenborg_2006_alsi8mg8na1_na2o_activity_1400"]["activity_coefficient"])) == Decimal("2e-13")
    assert values_by_id["willenborg_2006_alsi8na1_na2o_activity_1400"]["composition_wt_pct"]["SiO2"] == "81.12"
    assert values_by_id["willenborg_2006_alsi8mg8na1_na2o_activity_1400"]["composition_wt_pct"]["MgO"] == "7.43"
    assert observations[2]["values"]["admission_status"] == "typed_refusal"

    migrated = _migrated_source()
    source_rows = [
        row for row in migrated.result.observations.values()
        if row.source_id == "willenborg-2006-alkali-removal-thermodynamics"
    ]
    source_count = next(iter(migrated.result.source_counts.values()))
    assert source_count.rows_in == 3
    assert source_count.observations_out == 3
    assert len(source_rows) == 3
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in source_rows) == 1
    assert migrated.result.validation is not None
    assert len(migrated.result.validation.issues) == 3  # explained by the typed-refused comparison

    numeric = {row.observation_id.split("::")[1].split("::T=")[0]: row for row in source_rows if row.value.kind is ValueKind.POINT}
    expected = {
        "willenborg_2006_alsi8na1_na2o_activity_1400": (
            Decimal("5e-14"), {"SiO2": "81.12", "Al2O3": "17.67", "Na2O": "1.21"}
        ),
        "willenborg_2006_alsi8mg8na1_na2o_activity_1400": (
            Decimal("2e-13"), {"SiO2": "74.06", "Al2O3": "17.10", "MgO": "7.43", "Na2O": "1.02"}
        ),
    }
    assert set(numeric) == set(expected)
    for observation_id, (value, composition_wt_pct) in expected.items():
        row = numeric[observation_id]
        assert row.value.point == value
        assert row.identity.temperature_K.value == Decimal("1673.15")
        assert row.identity.composition.is_value
        assert row.identity.composition.value == wt_pct_to_mole_fraction(
            {oxide: Decimal(amount) for oxide, amount in composition_wt_pct.items()}
        )
        assert row.identity.fO2_Pa.is_unknown
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.point_conditions["temperature_K"].locator.page == 5
        assert row.point_conditions["composition"].locator.table == "2"

    refused = next(row for row in source_rows if row.value.kind is ValueKind.UNAVAILABLE)
    assert refused.identity.temperature_K.is_unknown
    assert refused.identity.composition.is_unknown
    print("migrated identity axes:")
    for row in source_rows:
        print(
            row.observation_id,
            {
                "composition": row.identity.composition,
                "total_pressure_Pa": row.identity.total_pressure_Pa,
                "fO2_Pa": row.identity.fO2_Pa,
                "temperature_K": row.identity.temperature_K,
            },
        )
