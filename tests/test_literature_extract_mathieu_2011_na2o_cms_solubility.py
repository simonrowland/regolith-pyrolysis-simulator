from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity, ValueKind
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "mathieu-2011-na2o-cms-solubility"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"


def _rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }


def _assert_render_pins(document: dict) -> None:
    rows = _rows(document)
    assert len(rows) == 48

    cms8 = rows["mathieu_2011_table6_cms8_na2o_wt_percent"]
    assert cms8["values"]["component_amount_as_published"] == "8.04 (0.32) wt%"
    assert cms8["values"]["component_amount"] == "8.04"
    assert cms8["values"]["basis"] == "wt_percent"
    assert cms8["units"] == "%"
    assert cms8["values"]["Na2O_mol_pct_as_published"] == "7.46 (0.29) mol%"
    assert cms8["locator"]["page"] == 11
    assert cms8["locator"]["table"] == "6"

    cms31 = rows["mathieu_2011_table6_cms31_na2o_wt_percent"]
    assert cms31["values"]["component_amount_as_published"] == "6.81 (0.212) wt%"
    assert cms31["values"]["Na2O_mol_pct_as_published"] == "6.17 (0.11) mol%"
    assert cms31["locator"]["page"] == 11
    assert cms31["locator"]["table"] == "6"

    cms34 = rows["mathieu_2011_table6_cms34_gamma_na2o"]
    assert cms34["values"]["gamma_as_printed"] == "4.99"
    assert cms34["values"]["gamma_scale_as_printed"] == "×10^6"
    assert cms34["locator"]["page"] == 11
    assert cms34["locator"]["table"] == "6"
    assert cms34["locator"]["column"] == "gamma Na2O (×10^6)"


def test_mathieu_2011_table6_render_pins_and_migration() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pins(document)

    mutated = deepcopy(document)
    mutated_rows = _rows(mutated)
    mutated_rows["mathieu_2011_table6_cms31_na2o_wt_percent"]["values"][
        "component_amount_as_published"
    ] = "6.18 (0.212) wt%"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)

    assert validate_extract_file(EXTRACT) == []

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 48

    raw_rows = _rows(document)
    migrated_rows = {
        key.split("::", 1)[1]: value
        for key, value in result.observations.items()
    }
    assert set(migrated_rows) == set(raw_rows)
    assert all(row.identity.quantity.is_value for row in migrated_rows.values())
    assert all(row.identity.species.phase.is_value for row in migrated_rows.values())

    composition_ids = [
        observation_id
        for observation_id in raw_rows
        if observation_id.endswith("_na2o_wt_percent")
    ]
    gamma_ids = {
        observation_id
        for observation_id in raw_rows
        if observation_id.endswith("_gamma_na2o")
    }
    assert len(composition_ids) == len(gamma_ids) == 24
    assert all(
        raw_rows[observation_id]["units"] == "%"
        for observation_id in composition_ids
    )

    for observation_id in composition_ids[:3]:
        row = migrated_rows[observation_id]
        assert row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert row.identity.species.phase.value.value == "glass"
        assert row.identity.subtype.is_value
        assert row.identity.subtype.value == "wt_percent"
        assert row.identity.temperature_K.value == Decimal("1673.15")
        assert row.identity.fO2_Pa.value == Decimal("0.21379575")
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.composition.is_value
        assert row.point_conditions["temperature_K"].state.value == Decimal("1673.15")
        assert row.point_conditions["temperature_K"].locator.page == 10
        assert row.point_conditions["fO2_Pa"].state.value == Decimal("0.21379575")
        assert row.point_conditions["fO2_Pa"].locator.page == 14
        assert row.point_conditions["composition"].locator.page == 10
        assert row.point_conditions["composition"].locator.table == "5"
        assert row.point_conditions["p_Na_Pa"].locator.page == 7
        assert row.point_conditions["p_Na_Pa"].locator.pdf_page_index == 6
        assert row.point_conditions["p_Na_Pa"].locator.published_page == 614
        assert row.point_conditions["p_Na_Pa"].locator.table == "4"
        assert row.point_conditions["quench_state"].state.value == "not_stated"
        assert row.point_conditions["quench_state"].locator.page == 3
        assert "total_pressure_Pa" not in row.point_conditions

    # The closed wt_percent phase-composition basis stores the printed percent
    # number. Percent-to-fraction scaling applies to dimensionless fraction
    # quantities, not this typed phase-composition value.
    expected_wt_pct = {
        "mathieu_2011_table6_cms8_na2o_wt_percent": Decimal("8.04"),
        "mathieu_2011_table6_cms31_na2o_wt_percent": Decimal("6.81"),
        "mathieu_2011_table6_cms34_na2o_wt_percent": Decimal("4.68"),
    }
    for observation_id in composition_ids:
        raw = raw_rows[observation_id]
        migrated = migrated_rows[observation_id]
        assert migrated.value.kind is ValueKind.POINT
        assert migrated.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert migrated.identity.subtype.value == "wt_percent"
        assert migrated.value.point == Decimal(raw["values"]["component_amount"])
    for observation_id, value in expected_wt_pct.items():
        assert migrated_rows[observation_id].value.point == value
    cms8 = migrated_rows["mathieu_2011_table6_cms8_gamma_na2o"]
    cms34 = migrated_rows["mathieu_2011_table6_cms34_gamma_na2o"]
    assert cms8.identity.quantity.value is Quantity.ACTIVITY_COEFFICIENT
    assert cms8.value.point == Decimal("2.83E-6")
    assert cms34.value.point == Decimal("4.99E-6")
    assert all(migrated_rows[observation_id].value.kind is ValueKind.POINT for observation_id in gamma_ids)

    assert len(result.queue) == 24
    assert {entry.observation_id.split("::", 1)[1] for entry in result.queue} == gamma_ids
    assert all(entry.axes == ["reference_state"] for entry in result.queue)
    assert all("reference_state" in entry.why for entry in result.queue)
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in result.observations.values()) == 0
