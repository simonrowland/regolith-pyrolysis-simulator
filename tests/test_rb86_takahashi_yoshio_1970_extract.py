"""Source-pinned checks for the Takahashi and Yoshio context-only extract."""

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
EXTRACT = ROOT / "data/literature/extracts/rb86-takahashi-yoshio-1970.yaml"
SOURCE_ID = "rb86-takahashi-yoshio-1970"
CORPUS_ROOT = Path(
    os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
)
PDF = CORPUS_ROOT / "raw" / SOURCE_ID / f"{SOURCE_ID}.pdf"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _context_by_id(document: dict) -> dict:
    return {
        row["observation_id"]: row
        for row in document["species"]["SiO2"]["context"]
    }


def _assert_source_pins(document: dict) -> None:
    species = document["species"]["SiO2"]
    assert species["observations"] == []
    context = _context_by_id(document)

    table1 = context["takahashi_yoshio_1970_table_1_crystallization_conditions"]
    assert len(table1["values"]["rows"]) == 12
    assert table1["values"]["rows"][0] == ["Li2O·2 SiO2", "590", "10"]
    assert table1["values"]["rows"][-1] == ["Rb2O·2 SiO2", "690", "35"]

    table2 = context["takahashi_yoshio_1970_table_2_hf_concentration"]
    assert table2["values"]["rows"] == [
        ["3", "0.962", "99.9"],
        ["4", "0.954", "102.7"],
        ["5", "0.947", "103.3"],
        ["10", "0.908", "105.8"],
        ["20", "0.834", "105.0"],
        ["39", "0.690", "99.3"],
    ]

    table3 = context["takahashi_yoshio_1970_table_3_hf_dissolution"]
    assert len(table3["values"]["rows"]) == 14
    assert table3["values"]["rows"][0] == [
        "SiO2",
        "glass",
        "-557.4",
        "-33.5",
        "blank",
    ]
    assert table3["values"]["rows"][-1] == [
        "2 Na2O·SiO2",
        "crystal",
        "-460.6",
        "-84.8",
        "-42.4",
    ]

    table4 = context["takahashi_yoshio_1970_table_4_water_dissolution"]
    assert len(table4["values"]["rows"]) == 6
    assert table4["values"]["rows"][2] == [
        "Na2O·SiO2",
        "glass",
        "-118.2",
        "-14.5",
        "-14.5",
    ]

    table5 = context["takahashi_yoshio_1970_table_5_other_alkali_dissolution"]
    assert len(table5["values"]["rows"]) == 9
    assert table5["values"]["row_fields_as_printed"][-1] == (
        "heat of solution (kcal/Na2O)"
    )
    assert table5["values"]["rows"][1] == [
        "2 Li2O·3 SiO2",
        "glass",
        "-728.6",
        "-58.3",
        "-87.4",
    ]

    table6 = context["takahashi_yoshio_1970_table_6_quoted_heats_of_formation"]
    assert [row[-1] for row in table6["values"]["rows"]] == [
        "568",
        "575",
        "356",
        "365",
        "529",
        "536",
    ]
    assert table6["values"]["method_class"] == "quoted_attributed"

    table7 = context["takahashi_yoshio_1970_table_7_poly_silicic_acid_size"]
    assert table7["values"]["rows"] == [
        ["Na2O·2 SiO2", "4.0", "6.0", "0.67"],
        ["Na2O·SiO2", "1.7", "2.0", "0.85"],
    ]

    table8 = context["takahashi_yoshio_1970_table_8_solution_heat_differences"]
    assert table8["values"]["rows"] == [
        ["Na2O·2 SiO2", "3.7", "0.9", "0.24"],
        ["Na2O·SiO2", "8.6", "7.7", "0.90"],
        ["2 Na2O·SiO2", "6.7", "7.9", "1.18"],
    ]

    table9 = context["takahashi_yoshio_1970_table_9_polysilicic_acid_heats"]
    assert table9["values"]["rows"] == [
        ["Na2O·2 SiO2", "glass", "561", "29"],
        ["Na2O·2 SiO2", "crystal", "532", "shared value 29 from glass row"],
        ["Na2O·SiO2", "glass", "336", "12"],
        ["Na2O·SiO2", "crystal", "324", "shared value 12 from glass row"],
    ]

    table10 = context["takahashi_yoshio_1970_table_10_alkali_heat_differences"]
    assert [row[0] for row in table10["values"]["rows"]] == [
        "2 Li2O·3 SiO2",
        "2 Na2O·3 SiO2",
        "Na2O·2 SiO2",
        "K2O·2 SiO2",
        "Na2O·SiO2",
        "K2O·SiO2",
    ]
    assert table10["values"]["rows"][0] == [
        "2 Li2O·3 SiO2",
        "-58.3",
        "-50.8",
        "7.5",
    ]
    assert table10["values"]["rows"][-1] == [
        "K2O·SiO2",
        "-70.0",
        "-67.9",
        "2.1",
    ]

    figures = [row for row in species["context"] if row["type"] == "figure_record"]
    assert len(figures) == 6
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("points" not in row["values"] for row in figures)
    figure2a = context["takahashi_yoshio_1970_figure_2a_hf_solution_heats"]
    assert [series["marker_count"] for series in figure2a["values"]["series"]] == [
        6,
        6,
        3,
        3,
    ]
    figure4 = context["takahashi_yoshio_1970_figure_4_alkali_solution_heats"]
    assert [series["marker_count"] for series in figure4["values"]["series"]] == [
        1,
        3,
        6,
        6,
        2,
        2,
        1,
    ]
    figure5 = context["takahashi_yoshio_1970_figure_5_crystallization_heats"]
    assert [series["marker_count"] for series in figure5["values"]["series"]] == [
        6,
        0,
        0,
        0,
        0,
    ]


def test_context_records_preserve_printed_values_and_validators() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=SOURCE_ID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    _assert_source_pins(document)


def test_mutating_a_printed_table_cell_breaks_the_source_pins() -> None:
    mutated = deepcopy(_document())
    row = _context_by_id(mutated)["takahashi_yoshio_1970_table_3_hf_dissolution"]
    row["values"]["rows"][0][2] = "-557.5"
    with pytest.raises(AssertionError):
        _assert_source_pins(mutated)


def test_context_only_extract_migrates_with_no_scored_observations() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert migrator.result.observations == {}
    assert sum(len(rows) for rows in migrator.result.context_by_work.values()) == 16
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues


def test_all_table_and_figure_locators_match_physical_pdf_pages() -> None:
    if not PDF.is_file():
        pytest.skip(f"corpus source PDF is absent: {PDF}")

    labels_by_page = {
        2: [r"Fig[.]\s*1"],
        3: [r"Table\s*1", r"Table\s*2", r"Table\s*3"],
        4: [r"Table\s*4", r"Fig[.,]\s*2", r"Fig[.,]\s*3"],
        5: [r"Table\s*5", r"Table\s*6"],
        7: [r"Table\s*7", r"Table\s*8", r"Table\s*9"],
        8: [r"Fig[.,]\s*4", r"Fig[.,]\s*5"],
        9: [r"Table\s*10"],
    }
    for page, patterns in labels_by_page.items():
        result = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        for pattern in patterns:
            assert re.search(pattern, result.stdout, re.IGNORECASE), (page, pattern)
