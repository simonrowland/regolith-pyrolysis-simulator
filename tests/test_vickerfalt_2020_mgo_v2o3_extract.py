from __future__ import annotations

from copy import deepcopy

import pytest
import yaml

from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract

EXTRACT = REPO_ROOT / "data/literature/extracts/vickerfalt-2020-mgo-v2o3.yaml"
SOURCE_ID = "vickerfalt-2020-mgo-v2o3"

# Independent cell transcription from the 600 dpi render of physical page 3.
TABLE1_RENDER_GRID = """Sample|V2O3_wt_percent|T_K|pO2_Pa|XCO2
1|31.9|1661|(3.55 ± 0.2) × 10^-6|0.092
2|55.3|1661|(3.55 ± 0.2) × 10^-6|0.092
3|65.0|1661|(3.55 ± 0.2) × 10^-6|0.092
4|84.8|1661|(3.55 ± 0.2) × 10^-6|0.092
5|88.3|1661|(3.55 ± 0.2) × 10^-6|0.092
6|91.2|1661|(3.55 ± 0.2) × 10^-6|0.092
7|55.3|1761|(3.55 ± 0.2) × 10^-6|0.031
8|65.0|1761|(3.55 ± 0.2) × 10^-6|0.031
9|84.7|1761|(3.55 ± 0.2) × 10^-6|0.031
10|88.2|1761|(3.55 ± 0.2) × 10^-6|0.031
11|91.4|1761|(3.55 ± 0.2) × 10^-6|0.031
12|17.1|1873|(3.55 ± 0.2) × 10^-6|0.01
13|47.9|1873|(3.55 ± 0.2) × 10^-6|0.01
14|74.4|1873|(3.55 ± 0.2) × 10^-6|0.01
15|78.1|1873|(3.55 ± 0.2) × 10^-6|0.01
16|84.8|1873|(3.55 ± 0.2) × 10^-6|0.01
17|91.3|1873|(3.55 ± 0.2) × 10^-6|0.01
18|55.3|1661|(3.55 ± 0.3) × 10^-5|0.242
19|65.0|1661|(3.55 ± 0.3) × 10^-5|0.242
20|84.8|1661|(3.55 ± 0.3) × 10^-5|0.242
21|88.2|1661|(3.55 ± 0.3) × 10^-5|0.242
22|55.4|1761|(3.55 ± 0.3) × 10^-5|0.092
23|65.0|1761|(3.55 ± 0.3) × 10^-5|0.092
24|84.9|1761|(3.55 ± 0.3) × 10^-5|0.092
25|91.4|1761|(3.55 ± 0.3) × 10^-5|0.092
26|17.0|1873|(3.55 ± 0.3) × 10^-5|0.031
27|38.2|1873|(3.55 ± 0.3) × 10^-5|0.031
28|65.1|1873|(3.55 ± 0.3) × 10^-5|0.031
29|78.2|1873|(3.55 ± 0.3) × 10^-5|0.031
30|84.7|1873|(3.55 ± 0.3) × 10^-5|0.031
31|88.2|1873|(3.55 ± 0.3) × 10^-5|0.031"""

TABLE2_RENDER_GRID = """T_K|pO2_Pa|MgO_boundary|MgO_saturated_spinel|V2O3_saturated_spinel|V2O3_corundum_boundary
1661|(3.55 ± 0.2) × 10^-6|9.4 ± 0.5|77.2 ± 0.6|80.0 ± 0.3|98.8 ± 0.1
1761|(3.55 ± 0.2) × 10^-6|17.4 ± 0.6|77.7 ± 0.3|80.8 ± 0.3|99.1 ± 0.1
1873|(3.55 ± 0.2) × 10^-6|31.3 ± 1.4|78.5 ± 0.7|82.1 ± 0.5|99.4 ± 0.0
1661|(3.55 ± 0.3) × 10^-5|9.4 ± 1.6|76.1 ± 0.2|79.3 ± 0.4|98.0 ± 0.1
1761|(3.55 ± 0.3) × 10^-5|14.6 ± 0.7|76.8 ± 0.3|80.1 ± 0.2|98.5 ± 0.0
1873|(3.55 ± 0.3) × 10^-5|25.7 ± 0.6|77.3 ± 0.4|81.2 ± 0.3|99.0 ± 0.0"""

TABLE3_RENDER_GRID = """weighed_in_V2O3_wt|T_K|pO2_Pa|phase|EPMA_V2O3_wt
17.1|1873|(3.55 ± 0.2) × 10^-6|MgO|16.6 ± 0.3
78.1|1873|(3.55 ± 0.2) × 10^-6|Spinel|79.2 ± 0.2
17.0|1873|(3.55 ± 0.3) × 10^-5|MgO|16.5 ± 0.3
78.2|1873|(3.55 ± 0.3) × 10^-5|Spinel|78.6 ± 0.1"""

TABLE4_RENDER_GRID = """weighed_in_V2O3_wt|analysis_1|analysis_2|analysis_3|analysis_4|analysis_5|analysis_6|average
84.8|79.56|79.95|79.46|80.08|79.52|79.21|79.63
88.2|79.04|79.05|78.65|79.29|79.07|79.22|79.05"""


def _table_context(document: dict[str, object], number: str) -> dict[str, object]:
    species = document["species"]
    assert isinstance(species, dict)
    body = species["V2O3"]
    assert isinstance(body, dict)
    matches = [
        item for item in body["context"]
        if item.get("type") == "table_context" and item["locator"].get("table") == number
    ]
    assert len(matches) == 1
    return matches[0]


def _assert_table_matches_render(
    document: dict[str, object], number: str, grid: str,
) -> None:
    lines = [line.split("|") for line in grid.strip().splitlines()]
    expected = lines[1:]
    context = _table_context(document, number)
    actual = context["values"]["rows_as_printed"]
    assert len(actual) == len(expected)
    assert actual == expected


def test_all_tables_match_independent_600dpi_transcription() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    for number, grid in (
        ("1", TABLE1_RENDER_GRID),
        ("2", TABLE2_RENDER_GRID),
        ("3", TABLE3_RENDER_GRID),
        ("4", TABLE4_RENDER_GRID),
    ):
        _assert_table_matches_render(document, number, grid)


def test_wide_table_render_grid_and_extract_cell_mutations_fail() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    changed_grid = TABLE1_RENDER_GRID.replace("31.9", "31.8", 1)
    with pytest.raises(AssertionError):
        _assert_table_matches_render(document, "1", changed_grid)

    mutated = deepcopy(document)
    table = _table_context(mutated, "1")
    table["values"]["rows_as_printed"][0][1] = "31.8"
    with pytest.raises(AssertionError):
        _assert_table_matches_render(mutated, "1", TABLE1_RENDER_GRID)


def test_solid_phase_tables_remain_context_only_and_migration_emits_no_observations() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    for number in ("2", "3", "4"):
        context = _table_context(document, number)
        assert context["values"]["candidate_tokens"] == ["equilibrium_phase_composition"]
        assert "PhaseRole enum is closed" in context["values"]["schema_gap_or_disposition"]
    assert _table_context(document, "1")["values"]["candidate_tokens"] == []

    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index=None, aliases={})
    migrator.finalize()
    assert not migrator.result.registry_issues
    assert len(migrator.result.observations) == 0


def test_figure_records_defer_points_and_retain_axes_and_attribution() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    body = document["species"]["V2O3"]
    figures = [row for row in body["context"] if row.get("type") == "figure_context"]
    assert len(figures) == 7
    assert {row["locator"]["figure"] for row in figures} == {"1", "2", "3", "4", "5", "6a", "6b"}
    for figure in figures:
        assert figure["values"]["digitisation"] == "deferred to a figure pass"
        assert figure["values"]["axes_as_printed"]
        assert figure["values"]["attribution"]
        assert "numeric_points" not in figure["values"]


def test_related_thesis_oxygen_discrepancy_preserves_both_printed_sets() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table = _table_context(document, "2")
    thesis = table["values"]["related_thesis_Table9_pO2_comparison"]
    assert thesis["related_source"] == "vickerfalt-2024-autogenous-slag-thesis, Table 9, physical PDF page 35"
    assert thesis["article_pO2_as_printed"] == [
        "(3.55 ± 0.2) × 10^-6 Pa", "(3.55 ± 0.3) × 10^-5 Pa"
    ]
    assert thesis["thesis_pO2_as_printed"] == [
        "1.75×10^-11 atm", "1.75×10^-10 atm"
    ]
