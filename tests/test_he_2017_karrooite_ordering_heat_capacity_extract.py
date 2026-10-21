"""Source pins for He et al. (2017) MgTi2O5 thermochemistry context."""

from __future__ import annotations

from copy import deepcopy
import csv
from decimal import Decimal
import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from tests.literature_extract_test_helpers import iter_locators
from simulator.battery.migrate import Migrator, load_index
from data.literature.build_index import corpus_pointers, sha256_file
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "he-2017-karrooite-ordering-heat-capacity"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
CORPUS_ROOT = Path(
    os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
)
PDF = CORPUS_ROOT / "raw" / SOURCE_ID / f"{SOURCE_ID}.pdf"
TABLE5_CSV = CORPUS_ROOT / "tables" / SOURCE_ID / "table-5.csv"
TABLE1_RENDER_GRID = [
    ("milling_time_h", (0, 0, 3, 3, 3, 3, 3)),
    ("sintering_temperature_K", (1373, 1473, 1173, 1273, 1373, 1473, 1473)),
    ("sintering_time_h", (4, 4, 15, 4, 4, 4, 4)),
    ("annealing_temperature_K", ("-", "-", "-", "-", "-", "-", 873)),
    ("annealing_time_h", ("-", "-", "-", "-", "-", "-", 1500)),
    ("purity_wt_pct", ("98(1)", "99(1)", 100, 100, 100, 100, 100)),
    ("a_A", ("3.7439(1)", "3.7433(1)", "3.7448(1)", "3.7437(1)", "3.7444(1)", "3.7456(1)", "3.7386(1)")),
    ("b_A", ("9.7384(1)", "9.7381(1)", "9.7422(1)", "9.7422(1)", "9.7432(1)", "9.7463(1)", "9.7192(1)")),
    ("c_A", ("9.9956(1)", "10.0012(1)", "9.9958(1)", "9.9989(1)", "9.9960(1)", "9.9939(1)", "10.0160(1)")),
    ("volume_A3", ("364.44(1)", "364.57(1)", "364.67(1)", "364.67(1)", "364.71(1)", "364.84(1)", "363.95(1)")),
    ("M1_O1_length_A", ("2.014(1)", "2.025(3)", "2.023(1)", "2.026(1)", "2.017(1)", "1.975(2)", "2.076(2)")),
    ("M2_O1_length_A", ("2.067(1)", "2.054(4)", "2.065(1)", "2.059(1)", "2.075(1)", "2.109(2)", "2.011(2)")),
    ("O1_M1_O1_angle_deg", ("136.8(1)", "135.1(4)", "135.4(2)", "134.9(1)", "135.5(4)", "143.1(3)", "128.4(2)")),
    ("Rp_pct", (16.7, 12.9, 15.1, 13.6, 13.5, 15.1, 15.9)),
    ("Rwp_pct", (26.4, 20.8, 22.2, 21.7, 20.5, 24.9, 25.8)),
    ("chi2", (1.10, 1.09, 0.98, 1.04, 1.01, 1.15, 1.15)),
    ("disorder_parameter_X", ("0.30(1)", "0.30(1)", "0.33(1)", "0.33(1)", "0.34(1)", "0.37(1)", "0.14(1)")),
]


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _context_by_id(document: dict) -> dict:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["context"]
    }


def _table5_total_entropies() -> tuple[Decimal, ...]:
    with TABLE5_CSV.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    return tuple(
        Decimal(re.match(r"[+-]?[0-9]+(?:\.[0-9]+)?", row[
            "S_total_J_mol-1_K-1_as_printed"
        ]).group())
        for row in rows
    )


def _assert_source_pins(document: dict, table1_grid=TABLE1_RENDER_GRID) -> None:
    assert document["source"]["corpus_sha256"] == (
        "9d60b405b98cd3f3bef3e95e527e48c38d563a8752b0915d236985a67323cf25"
    )
    contexts = _context_by_id(document)
    table1 = contexts["he2017_table1_synthesis_and_refinement"]["values"][
        "rows_as_printed"
    ]
    assert len(table1_grid) == 17
    assert all(tuple(table1[key]) == values for key, values in table1_grid)
    assert all(len(table1[key]) == 7 for key, _ in table1_grid)
    assert table1["disorder_parameter_X"][-1] == "0.14(1)"

    table2 = contexts["he2017_table2_raman_modes"]["values"]["rows_as_printed"]
    assert len(table2) == 25
    assert table2[0][3:7] == ["155.4(5)", "160.3(5)", "165.3(3)", "168(1)"]
    assert table2[20][1:7] == [604, 790, "640.1(5)", "649.4(5)", "632(1)", "653(3)"]

    table3 = contexts["he2017_table3_infrared_modes"]["values"]["rows_as_printed"]
    assert len(table3) == 18
    assert table3[10] == ["B3u (3)", 396, 166, "-", "425.99"]
    assert table3[15] == ["B2u (6)", 620, 6, "641.2", "648.03"]

    table4 = contexts["he2017_table4_elastic_stiffness"]["values"]
    assert table4["cij_as_printed"] == {
        "c11": "300(1)",
        "c22": "269(1)",
        "c33": "244(1)",
        "c44": "76(1)",
        "c55": "85(1)",
        "c66": "53(1)",
        "c12": "145(1)",
        "c13": "105(1)",
        "c23": "87(1)",
    }

    table5 = contexts["he2017_table5_components_and_nonscorable_values"]["values"][
        "rows_as_printed"
    ]
    assert table5["S_total_X014_exp_J_mol-1_K-1"] == "132.4(4)"
    assert table5["S_total_X037_exp_J_mol-1_K-1"] == "142.8(2)"
    assert table5["S_vib_Xunknown_Todd_1952_J_mol-1_K-1"] == "127.2(8)"
    assert document["species"]["MgTi2O5"]["observations"][0]["values"][
        "S_as_printed"
    ] == "132.4(4)"
    assert document["species"]["MgTi2O5"]["observations"][1]["values"][
        "S_as_printed"
    ] == "142.8(2)"

    prose_context = {
        "he2017_prose_vibrational_entropy_difference": ("4.6(2)", ["S"], 16),
        "he2017_prose_excess_vibrational_enthalpy": (
            "0.66(5)", ["delta_fH", "H_minus_H298"], 17
        ),
        "he2017_prose_quoted_total_excess_enthalpy": (
            "5.8(5)", ["delta_fH", "H_minus_H298"], 17
        ),
        "he2017_prose_extrapolated_ordered_standard_entropy": ("126.1", ["S"], 17),
    }
    for observation_id, (printed_value, candidates, page) in prose_context.items():
        row = contexts[observation_id]
        assert row["type"] == "table_record"
        assert row["locator"]["page"] == page
        assert row["values"]["value_as_printed"] == printed_value
        assert row["values"]["candidate_tokens_considered"] == candidates
        assert row["values"]["admission_status"] == "context_only"
    extrapolated_total = contexts[
        "he2017_prose_extrapolated_ordered_standard_entropy"
    ]["values"]
    assert extrapolated_total["value_as_printed"] == "126.1"
    table5_scope = next(
        table["reason"]
        for table in document["scope_assessment"]["tables"]
        if table["table"] == "5"
    )
    assert "126.1" in table5_scope

    figures = [record for record in contexts.values() if record["type"] == "figure_record"]
    assert len(figures) == 13
    assert {figure["locator"]["figure"] for figure in figures} == {
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"
    }
    assert all(
        figure["values"]["digitisation"] == "deferred to a figure pass"
        and "points" not in figure["values"]
        for figure in figures
    )
    assert validate_extract_document(
        document,
        expected_source_id=SOURCE_ID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []


def test_extract_preserves_printed_tables_and_deferred_figures() -> None:
    _assert_source_pins(_document())


def test_mutating_an_independent_render_grid_cell_breaks_the_source_pins() -> None:
    mutated_grid = list(TABLE1_RENDER_GRID)
    key, values = mutated_grid[7]
    mutated_values = list(values)
    mutated_values[5] = "9.7462(1)"
    mutated_grid[7] = (key, tuple(mutated_values))
    with pytest.raises(AssertionError):
        _assert_source_pins(_document(), mutated_grid)


def test_mutating_an_extract_grid_cell_breaks_the_source_pins() -> None:
    mutated = deepcopy(_document())
    rows = _context_by_id(mutated)["he2017_table1_synthesis_and_refinement"][
        "values"
    ]["rows_as_printed"]
    rows["b_A"][5] = "9.7462(1)"
    with pytest.raises(AssertionError):
        _assert_source_pins(mutated)


def test_mutating_an_extrapolated_entropy_context_value_breaks_the_source_pins() -> None:
    mutated = deepcopy(_document())
    row = _context_by_id(mutated)["he2017_prose_extrapolated_ordered_standard_entropy"]
    row["values"]["value_as_printed"] = "126.2"
    with pytest.raises(AssertionError):
        _assert_source_pins(mutated)


def test_isolated_migration_types_both_experimental_entropy_rows() -> None:
    index = load_index()
    index[SOURCE_ID] = {
        "source_id": SOURCE_ID,
        "corpus": corpus_pointers(
            CORPUS_ROOT,
            SOURCE_ID,
            sha256_file(PDF) if PDF.is_file() else None,
            EXTRACT,
            "c607a766",
            extract_doc=_document(),
        ),
    }
    assert index[SOURCE_ID]["corpus"]["tables"] == {
        "path": f"tables/{SOURCE_ID}",
        "exists": True,
        "file_count": 2,
    }
    migrator = Migrator(root=ROOT, index=index, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = migrator.result.observations
    assert len(observations) == 2
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    assert sum(len(rows) for rows in migrator.result.context_by_work.values()) == 22
    by_id = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in observations.values()
    }
    table_totals = _table5_total_entropies()
    assert by_id["he2017_table5_standard_entropy_x014"].value.point == table_totals[0]
    assert by_id["he2017_table5_standard_entropy_x037"].value.point == table_totals[1]
    expected_parent = (f"tables:{SOURCE_ID}",)
    assert all(
        observation.derived_from == expected_parent
        for observation in observations.values()
    )
    assert all(
        observation.derivation.inputs == expected_parent
        for observation in observations.values()
    )
    assert all(
        observation.identity.temperature_K.value == 298
        for observation in observations.values()
    )
    if index[SOURCE_ID]["corpus"]["raw"]["present"]:
        assert all(
            observation.read_from == f"pdf:{SOURCE_ID}"
            for observation in observations.values()
        )


def test_all_locators_match_physical_pdf_pages() -> None:
    if not PDF.is_file():
        pytest.skip(f"corpus source PDF is absent: {PDF}")

    for locator in iter_locators(_document()):
        page = locator["page"]
        assert locator["pdf_page_index"] == page - 1
        label = locator.get("table") or locator.get("figure")
        if label is None:
            continue
        result = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        marker = "Table" if locator.get("table") else r"Fig(?:ure)?\.?"
        assert re.search(rf"{marker}\s*{re.escape(str(label))}", result.stdout, re.I), (
            page,
            label,
        )

    paragraph_pins = {
        16: [r"difference in the vibrational entropy is 4\.6\(2\)"],
        17: [r"excess vibrational enthalpy of\s+0\.66\(5\)", r"standard entropy of 126\.1"],
    }
    for page, patterns in paragraph_pins.items():
        result = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert all(re.search(pattern, result.stdout, re.I) for pattern in patterns), page
