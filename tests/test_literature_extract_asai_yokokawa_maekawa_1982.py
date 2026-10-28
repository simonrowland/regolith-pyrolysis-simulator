from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/asai-yokokawa-maekawa-1982-na-borate-activity.yaml"
)


def _observations(document):
    return document["species"]["Na2O"]["observations"]


def _assert_rendered_table_2(document):
    observations = _observations(document)
    assert len(observations) == 32

    pins = {
        "asai_1982_table_2_b2o3_na2o_2_al2o3_na2o_0p4": ("-1.1", "0.2"),
        "asai_1982_table_2_b2o3_na2o_3_al2o3_na2o_0": ("-24.9", "0.7"),
        "asai_1982_table_2_b2o3_na2o_4_al2o3_na2o_0": ("-59.6", "0.4"),
        "asai_1982_table_2_b2o3_na2o_15_al2o3_na2o_0": ("-117.3", "0.2"),
        "asai_1982_table_2_b2o3_na2o_15_al2o3_na2o_1p0": ("-144.6", "0.4"),
    }
    by_id = {row["observation_id"]: row for row in observations}
    for observation_id, (delta_g, uncertainty) in pins.items():
        row = by_id[observation_id]
        assert row["values"]["relative_delta_G_Na2O_kJ_mol"] == delta_g
        assert row["values"]["relative_delta_G_uncertainty_kJ_mol"] == uncertainty
        assert row["locator"]["page"] == 4
        assert row["locator"]["published_page"] == 1008
        assert row["locator"]["table"] == "2"


def test_asai_1982_table_2_pins_rendered_relative_gibbs_values_and_rows():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_rendered_table_2(document)


def test_asai_1982_render_pin_rejects_mutated_table_value():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    row = next(
        row
        for row in _observations(mutated)
        if row["observation_id"]
        == "asai_1982_table_2_b2o3_na2o_2_al2o3_na2o_0p4"
    )
    row["values"]["relative_delta_G_Na2O_kJ_mol"] = "-1.2"
    with pytest.raises(AssertionError):
        _assert_rendered_table_2(mutated)


def test_asai_1982_isolated_migration_binds_printed_conditions_and_refuses_absolute_activity():
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    assert len(result.observations) == 32
    assert result.validation.hard_issues == ()
    migrated = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in result.observations.values()
    }
    for observation_id, row in migrated.items():
        assert row.value.kind.value == "unavailable"
        assert row.admission.status.value == "rejected"
        assert row.admission.reason == (
            "reference-melt absolute activity not printed"
        )
        assert row.identity.quantity.value.value == "activity"
        assert row.identity.temperature_K.value == Decimal("1173")
        assert row.identity.composition.is_value
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa.is_unknown
        assert "total_pressure_Pa" not in row.point_conditions or row.point_conditions[
            "total_pressure_Pa"
        ].state.is_unknown
        assert "fO2_Pa" not in row.point_conditions or row.point_conditions[
            "fO2_Pa"
        ].state.is_unknown

    first = migrated["asai_1982_table_2_b2o3_na2o_2_al2o3_na2o_0p4"]
    composition = dict(first.identity.composition.value.components)
    assert composition == {
        "Na2O": Decimal("0.294117647"),
        "B2O3": Decimal("0.588235294"),
        "Al2O3": Decimal("0.117647059"),
    }
