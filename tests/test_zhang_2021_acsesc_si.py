from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator

EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/zhang-2021-acsesc-si.yaml"
)


def _assert_source_pins(doc: dict) -> None:
    observations = doc["species"]["unknown"]["observations"]
    by_id = {row["observation_id"]: row for row in observations}
    weights = by_id["zhang_si_table_s1_published_weights"]["values"]["runs"]
    compositions = [row for row in observations if "residue_composition" in row["observation_id"]]
    mass_loss = [row for row in observations if row["observation_id"].endswith("_mass_loss")]

    assert len(weights) == 12
    assert len(compositions) == 12
    assert len(mass_loss) == 12
    assert weights[0]["mass_before_mg"] == 40.4  # SI Table S1, PDF p. 9
    assert weights[2]["mass_after_mg"] == 54.3  # SI Table S1, PDF p. 9
    comp_by_id = {row["observation_id"]: row for row in compositions}
    assert comp_by_id["zhang_2021_table1_bas1400c15m_residue_composition"]["values"]["composition_wt_pct"]["composition_wt_pct"]["Na2O"] == 0.66  # Table 1, PDF p. 71
    assert comp_by_id["zhang_2021_table1_bas1200c40h_residue_composition"]["values"]["composition_wt_pct"]["composition_wt_pct"]["Rb2O"] == 0.67  # Table 1, PDF p. 71
    assert doc["scope_assessment"]["measured_reduced_bulk_mass_loss_points"] == 12
    for row in mass_loss:
        conditions = row["point_conditions"]
        assert conditions["fO2_Pa"]["state"]["tag"] == "unknown"
        assert conditions["total_pressure_Pa"]["state"]["value"]["point"] == "0.0001"


def test_zhang_si_render_pins_rows_and_rejects_value_mutation() -> None:
    doc = yaml.safe_load(EXTRACT.read_text())
    _assert_source_pins(doc)

    mutated = deepcopy(doc)
    mutated["species"]["unknown"]["observations"][0]["values"]["runs"][0]["mass_before_mg"] = 40.5
    with pytest.raises(AssertionError):
        _assert_source_pins(mutated)


def test_zhang_si_migrates_percent_and_keeps_unprinted_fo2_unknown() -> None:
    root = EXTRACT.parents[3]
    migrator = Migrator(root)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    rows = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in migrator.result.observations.values()
        if observation.source_id == "zhang-2021-acsesc-si"
    }
    assert rows["zhang_si_table_s1_bas1400c0m1_mass_loss"].value.point == Decimal("0.00495")
    assert rows["zhang_si_table_s1_bas1400c15m_mass_loss"].value.point == Decimal("0.03894")
    assert rows["zhang_si_table_s1_bas1200c40h_mass_loss"].value.point == Decimal("0.05539")
    assert len([key for key in rows if key.endswith("_mass_loss")]) == 12

    for key in (
        "zhang_si_table_s1_bas1400c15m_mass_loss",
        "zhang_si_table_s1_bas1400c60m_mass_loss",
        "zhang_si_table_s1_bas1200c10h_mass_loss",
    ):
        identity = rows[key].identity
        assert identity.temperature_K.is_value
        assert identity.composition.is_value
        assert identity.total_pressure_Pa.is_value
        assert identity.total_pressure_Pa.value == Decimal("0.0001")
        assert identity.fO2_Pa.is_unknown
