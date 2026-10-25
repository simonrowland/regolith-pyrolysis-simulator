from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase
from simulator.battery.migrate import Migrator
from tools import mineru_table_draft as draft


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/lamoreaux-hildenbrand-1984.yaml"
MAPPING = ROOT / "data/literature/extracts/audits/lamoreaux-hildenbrand-1984-t383.mapping.yaml"
CORPUS_ROOT = Path.home() / "Repos" / "regolith-corpus"
CONTENT_LIST = (
    CORPUS_ROOT
    / "text/lamoreaux-hildenbrand-1984-t383/mineru/p001-p005/out/"
    "lamoreaux-hildenbrand-1984-t383-p001-p005/vlm/"
    "lamoreaux-hildenbrand-1984-t383-p001-p005_content_list.json"
)


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


def test_lamoreaux_table3_mapping_emits_the_landed_cells(tmp_path):
    if not CONTENT_LIST.is_file():
        pytest.skip(f"Lamoreaux MinerU content list is absent: {CONTENT_LIST}")

    draft_index = draft.convert_source(CONTENT_LIST, tmp_path / "converted")
    emitted = draft.emit_extract(
        draft_index.parent,
        MAPPING,
        tmp_path / "lamoreaux-table3-fragment.yaml",
    )
    committed_extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mapping = yaml.safe_load(MAPPING.read_text(encoding="utf-8"))
    coefficient_fields = {
        2: "a",
        3: "b_1e3",
        4: "c_1e6",
        5: "d_1e9",
    }
    suffix_by_mapping_phase = {"solid": "s", "liquid": "l", "gas": "g"}
    expected = {}
    for table in mapping["tables"]:
        if table["published_page"] != 156:
            continue
        suffix = suffix_by_mapping_phase[table["phase"]]
        observation_id = f"LH84_K2O_{suffix}_enthalpy_increment"
        segments = next(
            row["values"]["segments"]
            for row in committed_extract["species"]["K2O"]["observations"]
            if row["observation_id"] == observation_id
        )
        assert len(segments) == len(table["rows"])
        for row_number, segment in zip(table["rows"], segments, strict=True):
            for value in table["values"]:
                column = value["column"]
                expected[(row_number, column)] = Decimal(
                    str(segment[coefficient_fields[column]])
                )

    actual = {}
    for block in emitted["species"].values():
        for observation in block["observations"]:
            if observation["locator"].get("published_page") != 156:
                continue
            match = re.search(
                r"_p003_t06_r(?P<row>\d+)_c(?P<column>\d+)$",
                observation["observation_id"],
            )
            assert match is not None
            locator = observation["locator"]
            assert locator["page"] == 3
            assert locator["pdf_page_index"] == 2
            assert locator["published_page"] == 156
            coordinate = (int(match["row"]), int(match["column"]))
            actual[coordinate] = Decimal(str(observation["values"]["value"]))

    assert len(actual) == 24
    assert actual == expected
    for observation_id in (
        "LH84_K2O_s_enthalpy_increment",
        "LH84_K2O_l_enthalpy_increment",
        "LH84_K2O_g_enthalpy_increment",
    ):
        locator = next(
            row["locator"]
            for row in committed_extract["species"]["K2O"]["observations"]
            if row["observation_id"] == observation_id
        )
        assert locator["page"] == 4
        assert locator["pdf_page_index"] == 3
        assert locator["published_page"] == 156
