"""Source and migration pins for the 1997 alkali-fluoride slag fit."""

import json
from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/tsukihashi-sano-1997-alkali-fluoride-slag-activities.yaml"
OBSERVATION_ID = "tsukihashi_sano_1997_eq12_na2o_gamma_fit"


def test_eq12_fit_and_conditions_migrate_with_rendered_values() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    row = document["species"]["Na2O"]["observations"][0]
    assert len(document["species"]["Na2O"]["observations"]) == 1
    assert row["locator"]["page"] == 4
    assert row["locator"]["equation"] == "12"
    assert row["T_range_K"] == [1423, 1623]
    assert row["values"]["fit"]["slope_numerator_as_published"] == "18,600"
    assert row["values"]["fit"]["slope_uncertainty_as_published"] == "1,100"
    assert row["values"]["fit"]["intercept_as_published"] == "7.23"
    assert row["values"]["fit"]["intercept_uncertainty_as_published"] == "0.08"
    assert row["values"]["fit"]["abstract_intercept_conflict_as_published"] == "7.53"
    assert row["point_conditions"]["total_pressure_Pa"]["state"]["tag"] == "unknown"
    assert row["standard_state"]["locator"]["page"] == 2
    assert row["point_conditions"]["composition"]["locator"]["page"] == 4
    assert row["point_conditions"]["atmosphere"]["locator"]["page"] == 3
    assert "paper labels slope numerator A and intercept B" in row["values"]["notes_printed"]

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    migrated = migrator.result.observations[f"{EXTRACT.stem}::{OBSERVATION_ID}"]
    assert len(migrator.result.observations) == 1
    assert not migrator.result.validation.hard_issues
    assert migrated.locator.page == 4
    assert migrated.identity.quantity.value == "activity_coefficient_temperature_fit"
    assert migrated.identity.reference_state.is_value
    assert dict(migrated.value.expression_parameters or ()) == {
        "A": Decimal("7.23"),
        "B": Decimal("-18600"),
    }
    assert json.loads(migrated.value.expression_domain)["validity_range_K"] == [1423, 1623]
    assert not migrated.identity.total_pressure_Pa.is_value
    assert migrated.point_conditions is not None
    assert migrated.point_conditions["composition"].state.is_unknown
    assert migrated.point_conditions["atmosphere"].state.value == "CO2-CO gas; P_CO2/P_CO varied from 0.01 to 10"
    assert "total_pressure_Pa" not in migrated.point_conditions
    assert migrated.point_conditions["fO2_Pa"].state.is_unknown

    audit = yaml.safe_load(
        (ROOT / "data/literature/extracts/audits/tsukihashi-sano-1997-alkali-fluoride-slag-activities.mapping.yaml").read_text(encoding="utf-8")
    )
    assert audit["printed_numeric_table_rows"] == 0
    assert audit["isolated_migration"]["observations"] == len(migrator.result.observations)
    assert {
        entry["locator"]["page"] for entry in audit["source_conflict"]
    } == {1, 4}
    assert "0.4 mass% K2O" in audit["source_conflict"][-1]["reading"]
    assert "0.4 mass% Na2O" in audit["source_conflict"][-1]["reading"]
