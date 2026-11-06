from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import PerBasis, Quantity, ValueKind
from tests.battery.factories import migrate_one_extract
from tools import validate_literature_extracts as vle

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "king-1954-cafe2o4-ca2fe2o5-low-t-cp"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"


def test_king_1954_calcium_ferrite_tables_and_migration() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    ca_cp, ca_s = document["species"]["CaFe2O4"]["observations"]
    ca2_cp, ca2_s = document["species"]["Ca2Fe2O5"]["observations"]

    # Render pins from pp. 5849–5850, Tables I and II.
    assert len(ca_cp["values"]["points"]) == 44
    assert len(ca2_cp["values"]["points"]) == 30
    assert ca_cp["values"]["points"][0] == {"T_K": "52.99", "value": "3.458"}
    assert ca_cp["values"]["points"][41] == {
        "T_K": "298.16", "value": "36.72", "printed_value": "(36.72)", "extrapolated": True, "approximate": True
    }
    assert ca2_cp["values"]["points"][29] == {
        "T_K": "298.16", "value": "46.10", "printed_value": "(46.10)", "extrapolated": True, "approximate": True
    }
    assert ca_s["values"]["entropy"] == 34.7
    assert ca_s["values"]["uncertainty_cal_per_deg_mol"] == 0.2
    assert ca2_s["values"]["entropy"] == 45.1
    assert ca2_s["values"]["uncertainty_cal_per_deg_mol"] == 0.3
    assert ca2_s["values"]["S0_51_extrapolated_cal_per_deg_mol"] == "2.50"
    assert vle.validate_extract_document(document, expected_source_id=SOURCE_ID) == []
    assert vle.check_all_fidelity_samples_match(document, label=SOURCE_ID) == []

    changed = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    changed["species"]["CaFe2O4"]["observations"][0]["values"]["points"][0]["value"] = "3.459"
    assert any("fidelity_samples" in e and "mismatch" in e for e in vle.check_all_fidelity_samples_match(changed, label="mutated"))

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    rows = migrator.result.observations
    assert len(rows) == 76
    assert not migrator.result.validation.hard_issues
    assert not [row for row in rows.values() if row.value.kind is ValueKind.UNAVAILABLE]
    assert all(row.identity.quantity.is_value for row in rows.values())
    assert all(row.identity.species.phase.is_value for row in rows.values())
    assert all(row.identity.temperature_K.is_value for row in rows.values())
    assert all(row.identity.per.is_value and row.identity.per.value is PerBasis.MOL_SPECIES for row in rows.values())
    assert all(row.identity.standard_pressure_Pa.is_unknown for row in rows.values())
    assert all(row.point_conditions["temperature_K"].state.is_value for row in rows.values())
    assert all(row.identity.temperature_K.value == row.point_conditions["temperature_K"].state.value for row in rows.values())
    assert all(not row.identity.total_pressure_Pa.is_value and not row.identity.fO2_Pa.is_value for row in rows.values())
    cp_rows = [row for row in rows.values() if row.identity.quantity.value is Quantity.CP]
    entropy_rows = [row for row in rows.values() if row.identity.quantity.value is Quantity.S]
    assert len(cp_rows) == 74
    assert len(entropy_rows) == 2
    assert sum(r.identity.species.formula == "CaFe2O4" for r in cp_rows) == 44
    assert sum(r.identity.species.formula == "Ca2Fe2O5" for r in cp_rows) == 30
    assert {r.identity.species.formula for r in entropy_rows} == {"CaFe2O4", "Ca2Fe2O5"}
    pins = {(r.identity.species.formula, str(r.identity.temperature_K.value)): r.value.point for r in cp_rows}
    approximate = {(r.identity.species.formula, str(r.identity.temperature_K.value)): r.value.approximate for r in cp_rows}
    assert pins[("CaFe2O4", "52.99")] == Decimal("14.468272")
    assert pins[("CaFe2O4", "298.16")] == Decimal("153.63648")
    assert pins[("Ca2Fe2O5", "298.16")] == Decimal("192.88240")
    assert approximate[("CaFe2O4", "298.16")] is True
    assert approximate[("Ca2Fe2O5", "298.16")] is True
    assert approximate[("CaFe2O4", "296.08")] is False
    assert approximate[("Ca2Fe2O5", "296.09")] is False
    assert {r.value.point for r in entropy_rows} == {Decimal("145.1848"), Decimal("188.6984")}
