"""Fidelity and migration pins for Takigawa et al. (2015), Table 2."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery import factories

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = REPO_ROOT / "data/literature/extracts/takigawa-2015-corundum-kinetics.yaml"


# PDF page 10 render: the 0.032 / 0.046 / 0.099 cells are the //c, //a,
# and //m columns in the β=0 row. The six printed pressures belong to Al
# locations u1-u3 and d1-d3; none is total pressure.
def test_table2_render_values_and_all_18_rows_migrate_without_invented_pressure():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = document["species"]["Al"]["observations"]
    assert len(rows) == 18
    expected = {
        "takigawa_2015_table2_beta_0_axis_c_alpha_c": Decimal("0.032"),
        "takigawa_2015_table2_beta_0_axis_a_alpha_c": Decimal("0.046"),
        "takigawa_2015_table2_beta_0_axis_m_alpha_c": Decimal("0.099"),
    }
    extracted = {row["observation_id"]: Decimal(row["values"]["alpha"]) for row in rows}
    assert {key: extracted[key] for key in expected} == expected

    expected_pressures = {
        "u1": "3.32", "u2": "1.80", "u3": "1.13",
        "d1": "2.99", "d2": "1.47", "d3": "0.80",
    }
    beta_zero = [row for row in rows if row["values"]["beta_as_printed"] == "0"]
    assert len(beta_zero) == 3
    for row in beta_zero:
        printed = row["values"]["printed_conditions_as_table"]
        assert printed["Al_pressure_values_as_printed"] == expected_pressures
        assert printed["Al_pressure_units_as_printed"] == "10^-9 atm"
        assert not {"P_total", "pressure", "vacuum"} & row["values"].keys()
        assert row["T_C"] == "1575"
        assert row["point_conditions"]["temperature_C"]["state"]["value"] == "1575"
        assert row["point_conditions"]["temperature_C"]["locator"]["page"] == 10

    migrator = factories.migrate_one_extract(REPO_ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    migrated = list(migrator.result.observations.values())
    assert len(migrated) == 18
    assert not migrator.result.validation.hard_issues
    assert sum(obs.value.kind.value == "unavailable" for obs in migrated) == 0
    by_id = {obs.observation_id.rsplit("::", 1)[-1]: obs for obs in migrated}
    for row in rows:
        obs = by_id[row["observation_id"]]
        assert obs.value.kind.value == "point"
        assert obs.value.point == Decimal(row["values"]["alpha"])
        assert obs.identity.temperature_K.is_value
        assert obs.identity.temperature_K.value == Decimal("1848.15")
        assert obs.point_conditions["temperature_K"].state.value == Decimal("1848.15")
        assert obs.identity.total_pressure_Pa.is_unknown
        assert "total pressure" in obs.identity.total_pressure_Pa.reason
