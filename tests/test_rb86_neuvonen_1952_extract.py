"""Source-pinned checks for Neuvonen (1952) context-only extract."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/rb86-neuvonen-1952.yaml"
SOURCE_ID = "rb86-neuvonen-1952"
CORPUS_ROOT = Path(
    os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
)
PDF = CORPUS_ROOT / "raw" / SOURCE_ID / f"{SOURCE_ID}.pdf"
EXPECTED_NATURAL_MELILITE_COMPOSITION_GRID = [
    ["Åkermanite-gehlenite solid solution", "68.28", "94.63"],
    ["alkali melilite", "15.61", "1.30"],
    ["FeSiO3", "5.62", "1.68"],
    ["MnSiO3", "0.14", "0.04"],
    ["Fe2O3", "1.90", "0.94"],
    ["Ca2SiO4", "8.02", "0.93"],
    ["H2O+", "0.43", "0.48"],
    ["Total", "100.00", "100.00"],
]


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _context_by_id(document: dict) -> dict:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["context"]
    }


def _assert_source_pins(document: dict) -> None:
    assert all(not block["observations"] for block in document["species"].values())
    context = _context_by_id(document)

    table_i = context["neuvonen_1952_table_i_natural_melilite_analyses"]
    assert len(table_i["values"]["rows"]) == 26
    assert table_i["values"]["rows"][0] == ["SiO2", "42.81", "32.60"]
    assert table_i["values"]["rows"][13] == ["Ca", "5933", "7147"]
    assert table_i["values"]["rows"][-1] == ["Total", "99.82", "100.45"]

    table_ii = context["neuvonen_1952_table_ii_synthetic_melilite_indices"]
    assert len(table_ii["values"]["rows"]) == 6
    assert table_ii["values"]["rows"][3] == ["A50", "1.653", "1.671", "1.638"]

    table_iii = context["neuvonen_1952_table_iii_synthetic_cell_dimensions"]
    assert len(table_iii["values"]["rows"]) == 5
    assert table_iii["values"]["rows"][2] == ["50", "5.045", "7.760", "0.650"]

    table_iv = context["neuvonen_1952_table_iv_quoted_diopside_analysis"]
    assert table_iv["values"]["rows"][0] == ["SiO2", "54.66", ""]
    assert table_iv["values"]["calculated_endmember_composition"]["rows"][0] == [
        "CaSiO3",
        "52.70",
        "49.22",
    ]
    assert table_iv["values"]["other_printed_properties"]["d20_4_specific_gravity"] == "3.276"

    table_v = context["neuvonen_1952_table_v_quoted_brucite_analysis"]
    assert table_v["values"]["raw_analysis_rows"][3] == ["MgO", "68.29"]
    assert table_v["values"]["calculated_composition_rows"][0] == ["Mg(OH)2", "98.85"]

    quartz = context["neuvonen_1952_quartz_solution_heats_and_ice_reference"]
    assert len(quartz["values"]["quartz_run_rows"]) == 5
    assert quartz["values"]["quartz_run_rows"][0] == ["1", "0.3987", "-554.34"]
    assert quartz["values"]["water_average_as_printed"] == (
        "+30.38 ± 0.70 cal/gram or +547 ± 13 cal/mole"
    )
    assert quartz["values"]["ice_fusion_value_as_printed"] == "+79.71 cal/gram"

    calcium = context["neuvonen_1952_calcium_oxide_solution_heats"]
    assert len(calcium["values"]["run_rows"]) == 5
    assert calcium["values"]["run_rows"][-1] == ["5", "0.1730", "-884.60"]
    assert calcium["values"]["average_as_printed"] == (
        "-883.99 ± 0.50 cal/gram or -49,570 ± 30 cal/mole"
    )

    alumina = context["neuvonen_1952_alumina_solution_heats"]
    assert len(alumina["values"]["rows"]) == 5
    assert alumina["values"]["rows"][-1] == [
        "0.2019",
        "3.55",
        "-797.68",
        "-831.09",
    ]

    melilite_glass = context["neuvonen_1952_table_vi_glassy_melilite_solution_heats"]
    assert len(melilite_glass["values"]["run_rows"]) == 24
    assert melilite_glass["values"]["average_rows"][1] == [
        "A74",
        "-646.04 ± 1.40 cal/gram",
        "-176,380 ± 380 cal/mole",
    ]

    crystalline_melilite = context[
        "neuvonen_1952_table_vii_crystalline_melilite_solution_heats"
    ]
    assert len(crystalline_melilite["values"]["run_rows"]) == 25
    assert len(crystalline_melilite["values"]["average_rows"]) == 6

    table_ix = context["neuvonen_1952_table_ix_endmember_mixing_heats"]
    assert len(table_ix["values"]["rows"]) == 4
    assert table_ix["values"]["rows"][1] == ["A74", "-190", "340"]
    assert table_ix["values"]["blank_cells_note"].startswith("A100 and A0 are blank")

    table_xi = context["neuvonen_1952_table_xi_formation_from_component_oxides"]
    assert len(table_xi["values"]["rows"]) == 6
    assert table_xi["values"]["rows"][2] == [
        "A74",
        "2.00",
        "0.74",
        "0.26",
        "1.74",
        "-41,510",
        "290",
    ]
    assert "not from elements" in table_xi["values"]["schema_gap"]

    natural_composition = context["neuvonen_1952_natural_melilite_correction_composition"]
    assert natural_composition["values"]["rows"] == EXPECTED_NATURAL_MELILITE_COMPOSITION_GRID

    honolulu_heats = context["neuvonen_1952_honolulu_melilite_solution_heats"]
    assert honolulu_heats["values"]["rows"] == [
        ["1", "0.2601", "-553.66"],
        ["2", "0.2316", "-554.56"],
        ["3", "0.2553", "-554.78"],
        ["4", "0.2375", "-556.48"],
    ]
    assert honolulu_heats["locator"]["page"] == 30
    luna_heats = context["neuvonen_1952_luna_melilite_solution_heats"]
    assert luna_heats["values"]["rows"] == [
        ["1", "0.4079", "-606.28"],
        ["2", "0.4374", "-606.36"],
        ["3", "0.3675", "-607.00"],
        ["4", "0.3920", "-607.26"],
    ]

    filter_paper_cp = context["neuvonen_1952_filter_paper_heat_capacity"]
    assert filter_paper_cp["values"]["rows"][-1] == ["4", "0.221"]
    assert filter_paper_cp["values"]["average_as_printed"] == (
        "0.217 ± 0.003 cal/gram/degree"
    )

    corrected_diopside = context["neuvonen_1952_diopside_correction_composition"]
    assert corrected_diopside["values"]["rows"][0] == ["CaMg(SiO3)2", "98.25"]
    assert corrected_diopside["locator"]["page"] == 31

    table_xii = context["neuvonen_1952_table_xii_hydrothermal_decomposition"]
    assert len(table_xii["values"]["rows"]) == 3
    assert table_xii["values"]["rows"][0][2:] == [
        "400 700",
        "4",
        "2.97; 3.32, 3.22, 3.15, 2.67; 1.67",
    ]

    figures = [row for row in context.values() if row["type"] == "figure_record"]
    assert len(figures) == 10
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("points" not in row["values"] for row in figures)
    assert all(
        isinstance(series["marker_count"], int)
        for row in figures
        for series in row["values"]["series"]
    )


def test_context_records_preserve_printed_values_and_validate() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=SOURCE_ID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    _assert_source_pins(document)


def test_mutating_a_printed_cell_breaks_the_source_pins() -> None:
    mutated = deepcopy(_document())
    context = _context_by_id(mutated)
    context["neuvonen_1952_natural_melilite_correction_composition"]["values"]["rows"][0][1] = "68.82"
    with pytest.raises(AssertionError):
        _assert_source_pins(mutated)


def test_mutating_the_independently_transcribed_composition_grid_fails() -> None:
    document = _document()
    context = _context_by_id(document)
    mutated_grid = deepcopy(EXPECTED_NATURAL_MELILITE_COMPOSITION_GRID)
    mutated_grid[0][1] = "68.82"
    with pytest.raises(AssertionError):
        assert context["neuvonen_1952_natural_melilite_correction_composition"]["values"]["rows"] == mutated_grid


def test_isolated_migration_lifts_only_context_and_no_typed_rows() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert migrator.result.observations == {}
    context_count = sum(len(block["context"]) for block in _document()["species"].values())
    assert sum(len(rows) for rows in migrator.result.context_by_work.values()) == context_count
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues


def test_all_context_locators_match_the_cited_physical_pdf_pages() -> None:
    if not PDF.is_file():
        pytest.skip(f"corpus source PDF is absent: {PDF}")

    content_by_page = {
        page: subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for page in sorted(
            {
                row["locator"]["page"]
                for species in _document()["species"].values()
                for row in species["context"]
            }
        )
    }
    patterns_by_page = {
        13: [r"Fig\.?\s*1", r"Fig\.?\s*2"],
        14: [r"Heat of solution of gelatin", r"CORREGTIONS APPLIED"],
        15: [r"Mean heat cap", r"platinum"],
        16: [r"Table\s+I"],
        17: [r"gehlenite"],
        18: [r"Series"],
        19: [r"Fig\.?\s*4"],
        20: [r"Table\s+IV"],
        22: [r"Table\s+V"],
        23: [r"heat of solution of quartz"],
        24: [r"49,570"],
        25: [r"BRUCITE"],
        27: [r"HEAT OF SOLUTION OF MAGNESIUM OXIDE"],
        28: [r"Table\s+VI"],
        29: [r"Table\s+VII"],
        30: [r"HEATS OF SOLUTION OP THE TWO NATURAL MELILITE SAMPLES"],
        31: [r"diopside"],
        33: [r"Fig\.?\s*6"],
        38: [r"Table\s+IX"],
        39: [r"INTERPRETATION OF HEAT", r"599[.]5"],
        40: [r"Table\s+X"],
        42: [r"Table\s+XI"],
        45: [r"Fig\.?\s*7", r"2[, ]?310"],
        47: [r"Table\s+XII"],
    }
    for page, patterns in patterns_by_page.items():
        for pattern in patterns:
            assert re.search(pattern, content_by_page[page], re.IGNORECASE), (page, pattern)

    # The Table VIII page is scan-only in pdftotext; its locator and cells were
    # checked against the 600 dpi render instead of noisy OCR output.
    assert content_by_page[36].strip()

