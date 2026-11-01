from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import yaml

import simulator.battery.migrate as migrate_module
from simulator.battery.enums import Quantity, ValueKind
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
    assert len(observations) == 2
    assert audit["tables"][0]["printed_body_rows"] == 1
    assert audit["tables"][1]["printed_body_rows"] == 14

    # Render-verified prose values (PDF pages 2597) and Table 2 compositions
    # (PDF page 2594); the paper prints these values only approximately.
    values_by_id = {row["observation_id"]: row["values"] for row in observations}
    expected_values = {
        "willenborg_2006_alsi8na1_na2o_activity_1400": Decimal("5e-14"),
        "willenborg_2006_alsi8mg8na1_na2o_activity_1400": Decimal("2e-13"),
    }
    for observation in observations:
        values = observation["values"]
        assert values["quantity"] == "activity"
        assert "activity_coefficient" not in values
        assert "phase" not in observation
        assert "standard_state" not in observation
        if observation["observation_id"] == "willenborg_2006_alsi8na1_na2o_activity_1400":
            assert "partly molten" in observation["note"]
            assert "mullite and cristobalite" in observation["note"]
        assert values["approximate"] is True
        assert values["reference_state_as_published"].startswith("Eq. 3 defines aNa2O from Na2O(l,s)")
        point = values["points"][0]
        assert Decimal(str(values["activity"])) == expected_values[observation["observation_id"]]
        assert Decimal(str(point["activity"])) == expected_values[observation["observation_id"]]
        assert point["approximate"] is True
        assert "activity_coefficient" not in point
    assert values_by_id["willenborg_2006_alsi8na1_na2o_activity_1400"]["composition_wt_pct"]["SiO2"] == "81.12"
    assert values_by_id["willenborg_2006_alsi8mg8na1_na2o_activity_1400"]["composition_wt_pct"]["MgO"] == "7.43"
    contexts = extract["species"]["Na2O"]["context"]
    comparison = next(row for row in contexts if row["observation_id"] == "willenborg_2006_activity_ratio_sorbent_comparison_typed_refusal")
    assert "AlSi8Mg8Na5 is about 2 times higher" in comparison["values"]["comparison_as_published"]
    assert "activity_coefficient_relation" not in str(extract)
    slag_summary = next(row for row in contexts if row["observation_id"] == "willenborg_2006_slag_additive_activity_summaries_typed_refusal")
    assert slag_summary["values"]["quantity"] == "activity"
    assert slag_summary["values"]["potassium_oxide_activity_as_published"] == (
        "Untreated slag about 2.5 × 10^-12; slag with 5% SiO2 or TiO2 1.2 × 10^-12; "
        "slag with 10% SiO2 or TiO2 5 × 10^-13."
    )

    migrated = _migrated_source()
    source_rows = [
        row for row in migrated.result.observations.values()
        if row.source_id == "willenborg-2006-alkali-removal-thermodynamics"
    ]
    source_count = next(iter(migrated.result.source_counts.values()))
    assert source_count.rows_in == 2
    assert source_count.observations_out == 2
    assert len(source_rows) == 2
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in source_rows) == 0
    assert migrated.result.validation is not None
    assert len(migrated.result.validation.issues) == 2
    assert all(issue.path.endswith(".identity.reference_state") for issue in migrated.result.validation.issues)

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
        assert row.value.approximate is True
        assert row.identity.quantity.value is Quantity.ACTIVITY
        assert row.identity.reference_state.is_unknown
        assert row.identity.species.phase.is_unknown
        assert row.identity.temperature_K.value == Decimal("1673.15")
        assert row.identity.composition.is_value
        assert row.identity.composition.value == wt_pct_to_mole_fraction(
            {oxide: Decimal(amount) for oxide, amount in composition_wt_pct.items()}
        )
        assert row.identity.fO2_Pa.is_unknown
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.point_conditions["temperature_K"].locator.page == 5
        assert row.point_conditions["composition"].locator.table == "2"
