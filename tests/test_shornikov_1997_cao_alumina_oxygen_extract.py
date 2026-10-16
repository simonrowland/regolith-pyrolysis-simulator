"""Regression pins for Shornikov (1997) calculated oxygen columns."""

from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass
from tests.battery.test_migrate import _migrate_real_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT_NAME = "shornikov-1997-cao-alumina-vapor.yaml"
EXTRACT = ROOT / "data/literature/extracts" / EXTRACT_NAME


def test_table2_oxygen_columns_keep_calculated_evidence_and_conditions(tmp_path):
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    p_o_eq1, p_o_eq2 = document["species"]["O"]["observations"]
    p_o2 = document["species"]["O2"]["observations"][0]

    assert p_o_eq1["values"]["method_class"] == "quoted_attributed"
    assert p_o_eq2["values"]["method_class"] == "quoted_attributed"
    assert p_o2["values"]["method_class"] == "quoted_attributed"
    assert p_o_eq1["values"]["points"][0]["P_O_by_eq1_times_10^9_atm_as_printed"] == "2.11"
    assert p_o2["values"]["points"][-1]["P_O2_times_10^11_atm_as_printed"] == "8.07"
    assert "thermodynamic data [40]" in p_o_eq1["values"]["method_as_printed"]
    assert "does not identify which atomic-O estimate" in p_o2["values"]["method_as_printed"]

    migrated = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    assert migrated.validation is not None
    assert migrated.validation.hard_issues == ()
    rows = [
        observation
        for observation in migrated.observations.values()
        if any(
            identifier in observation.observation_id
            for identifier in (
                "shornikov_1997_o_pressure_eq1_table2_1933k",
                "shornikov_1997_o_pressure_eq2_table2_1933k",
                "shornikov_1997_o2_pressure_table2_1933k",
            )
        )
    ]
    assert len(rows) == 26
    for observation in rows:
        assert observation.evidence.class_.is_value
        assert observation.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        assert observation.identity.temperature_K.is_value
        assert observation.identity.temperature_K.value == 1933
        for key in ("temperature_K", "composition"):
            condition = observation.point_conditions[key]
            assert condition.state.is_value
            assert condition.locator is not None
            assert condition.locator.published_page == 20
