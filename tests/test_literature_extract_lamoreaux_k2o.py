from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.enums import Phase
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/lamoreaux-hildenbrand-1984.yaml"


def test_lamoreaux_k2o_rows_match_render_and_preserve_printed_row_counts():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = document["species"]["K2O"]["observations"]
    rows = {row["observation_id"]: row for row in observations}

    assert len(rows) == 9
    assert rows["LH84_K2O_fusion"]["locator"]["published_page"] == 153
    assert rows["LH84_K2O_fusion"]["values"]["T_m_K"] == 1190
    assert rows["LH84_K2O_fusion"]["values"]["Delta_fus_H_over_R_kK"] == 4.8

    solid_gef = rows["LH84_K2O_s_GEF_segments"]
    assert len(solid_gef["values"]["segments"]) == 3
    assert solid_gef["locator"]["published_page"] == 154
    assert solid_gef["values"]["segments"][1]["B_1e3"] == 5.772
    liquid_gef = rows["LH84_K2O_l_GEF_segments"]
    assert len(liquid_gef["values"]["segments"]) == 1
    assert liquid_gef["values"]["segments"][0]["A"] == 0.80
    gas_gef = rows["LH84_K2O_g_GEF_segments"]
    assert len(gas_gef["values"]["segments"]) == 3

    solid_increment = rows["LH84_K2O_s_enthalpy_increment"]
    liquid_increment = rows["LH84_K2O_l_enthalpy_increment"]
    gas_increment = rows["LH84_K2O_g_enthalpy_increment"]
    assert len(solid_increment["values"]["segments"]) == 3
    assert len(liquid_increment["values"]["segments"]) == 1
    assert len(gas_increment["values"]["segments"]) == 2
    for row in (solid_increment, liquid_increment, gas_increment):
        assert row["locator"]["page"] == 4
        assert row["locator"]["published_page"] == 156
        assert row["locator"]["pdf_page_index"] == 3
    assert gas_increment["T_range_K"] == [290, 3000]
    assert gas_increment["values"]["segments"][0]["T_min_K"] == 290
    assert gas_increment["values"]["segments"][0]["a"] == -1.05

    solid_thermo = rows["LH84_K2O_s_formation"]
    gas_thermo = rows["LH84_K2O_g_formation"]
    assert solid_thermo["locator"]["published_page"] == 157
    assert solid_thermo["values"]["Delta_f_H_298_over_R_kK"] == -43.58
    assert gas_thermo["values"]["Delta_f_H_298_over_R_kK"] == -7.05
    assert gas_thermo["values"]["H298_minus_H0_over_R_kK"] == 1.64

    assert not any("point_conditions" in row for row in observations)
    assert not any("total_pressure_Pa" in row for row in observations)


def test_lamoreaux_k2o_condensed_phase_tokens_survive_isolated_migration():
    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert not result.registry_issues
    assert not result.validation.hard_issues
    by_id = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in result.observations.values()
        if row.source_id == "lamoreaux-hildenbrand-1984"
    }
    for observation_id in (
        "LH84_K2O_fusion",
        "LH84_K2O_s_GEF_segments",
        "LH84_K2O_s_enthalpy_increment",
        "LH84_K2O_s_formation",
    ):
        assert by_id[observation_id].identity.species.phase.value is Phase.CR
    for observation_id in (
        "LH84_K2O_l_GEF_segments",
        "LH84_K2O_l_enthalpy_increment",
    ):
        assert by_id[observation_id].identity.species.phase.value is Phase.L
