from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/farah-1998-vanadium-silicate-melts-thesis.yaml"

# Independent transcription from the 600 dpi table renders on physical PDF pages 96, 118, 128, 171, and 185.
TABLE_4_1_GRID = [
    ("A", "0.5", "-", "1", "-", "-", "-"),
    ("B", "0.5", "-", "5", "-", "-", "-"),
    ("C", "1.0", "-", "5", "-", "-", "-"),
    ("D", "0.5", "-", "10", "-", "-", "-"),
    ("E", "-", "0.7", "5", "-", "-", "-"),
    ("F", "-", "0.9", "5", "-", "-", "-"),
    ("G", "-", "1.23", "5", "-", "-", "-"),
    ("H", "-", "0.7", "1", "-", "-", "-"),
    ("I", "-", "0.9", "1", "-", "-", "-"),
    ("J", "-", "1.21", "3", "-", "-", "-"),
    ("K", "-", "1.23", "5", "3.22", "-", "-"),
    ("L", "-", "1.22", "5", "5.44", "-", "-"),
    ("M", "-", "1.22", "5", "7.87", "-", "-"),
    ("N", "-", "1.57", "5", "-", "3.50", "-"),
    ("O", "-", "1.51", "5", "-", "4.87", "-"),
    ("P*", "-", "0.71", "4", "-", "-", "-"),
    ("Q*", "-", "0.71", "10", "-", "-", "-"),
    ("R*", "-", "1.14", "10", "-", "-", "-"),
    ("S*", "-", "1.66", "10", "-", "-", "-"),
    ("T*", "-", "1.25", "10", "-", "-", "10"),
    ("U*", "-", "1.56", "10", "-", "-", "10"),
]
TABLE_4_1_STEEL = {
    "Fe(total)_wt_percent": "12.72",
    "CaO_wt_percent": "51.41",
    "SiO2_wt_percent": "9.85",
    "Al2O3_wt_percent": "2.35",
    "MgO_wt_percent": "8.77",
    "V2O3_wt_percent": "4.11",
    "TiO2_wt_percent": "3.72",
    "Cr2O3_wt_percent": "0.324",
    "MnO_wt_percent": "2.16",
    "P2O5_wt_percent": "1.365",
    "S_wt_percent": "0.185",
}
TABLE_5_1_GRID = [
    ("A", "1125", "0.535", "-"),
    ("A", "1225", "1.138", "0.355"),
    ("A", "1325", "4.345", "0.331"),
    ("B", "1225", "3.926", "-"),
    ("C", "1225", "0.111", "-"),
    ("D", "1225", "2.911", "0.344"),
]
TABLE_5_2_GRID = [
    ("-2", ["4.964", "4.948", "4.909", "4.969", "4.999", "4.957"]),
    ("-4", ["4.942", "4.849", ["4.797", "4.714"], "4.946", "4.998", "4.930"]),
    ("-6", ["4.725", "4.804", "4.608", "4.881", ["4.993", "4.982"], "4.851"]),
    ("-8", ["4.804", "4.702", "4.412", "4.662", ["4.986", "4.981"], "4.679"]),
    ("-10", ["4.639", "4.243", ["3.935", "3.935"], "4.203", ["4.90", "4.933"], "4.00"]),
    ("-12", ["4.421", "3.390", "3.485", "3.988", ["4.75", "4.602"], "-"]),
    ("-14", ["3.491", "3.365", "3.338", "-", "-", "-"]),
]
TABLE_7_1_GRID = [
    ("F", "0.9", "5", "-", "-", "0.765", "2.71"),
    ("G", "1.23", "5", "-", "-", "0.573", "1.653"),
    ("J", "1.21", "3", "-", "-", "1.005", "0.719"),
    ("K", "1.23", "5", "3.22", "-", "0.403", "1.500"),
    ("L", "1.22", "5", "5.44", "-", "0.444", "1.803"),
    ("M", "1.22", "5", "7.87", "-", "0.574", "2.072"),
    ("N", "1.57", "5", "-", "3.50", "0.140", "1.438"),
    ("O", "1.51", "5", "-", "4.87", "0.107", "2.769"),
]
TABLE_7_2_GRID = [
    ("-2", ["4.567", "4.636", "4.499", "4.693", "4.692", "4.635", "4.877", "4.903"]),
    ("-4", ["4.000", "4.121", "4.398", "4.139", "4.139", "4.219", "4.692", "4.743"]),
    ("-6", ["3.653", "4.000", "4.017", "4.016", "4.016", "4.000", "4.559", "4.606"]),
    ("-8", ["3.264", "3.377", "3.582", "3.357", "3.357", "3.326", "3.410", "3.369"]),
    ("-9", ["3.151", "3.251", "3.401", "3.234", "3.233", "3.195", "3.410", "3.369"]),
]


def _load(path: Path = EXTRACT):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_rendered_table_grids_match_independent_transcription() -> None:
    source = _load()
    contexts = {entry["observation_id"]: entry for entry in source["species"]["V"]["context"]}

    master = contexts["farah_1998_table_4_1_master_melt_compositions"]["values"]["printed_data"]
    assert [tuple(row) for row in master["rows"]] == TABLE_4_1_GRID
    assert master["new_zealand_steel_slag_bulk_wt_percent"] == TABLE_4_1_STEEL

    t51 = contexts["farah_1998_table_5_1_equilibrium_redox_ratios"]["values"]["printed_data"]["rows"]
    assert [tuple(row[k] for k in ("sample", "temperature_C", "V4+/V5+", "V3+/V4+")) for row in t51] == TABLE_5_1_GRID
    assert contexts["farah_1998_table_5_1_equilibrium_redox_ratios"]["values"]["printed_data"]["printed_condition"] == "pO2 = 10^-10 atm"

    t52 = contexts["farah_1998_table_5_2_average_vanadium_valency"]["values"]["printed_data"]["rows"]
    assert [(row["log10_pO2_atm"], row["values"]) for row in t52] == TABLE_5_2_GRID

    t71 = contexts["farah_1998_table_7_1_slag_redox_ratios"]["values"]["printed_data"]["rows"]
    assert [tuple(row[k] for k in ("slag", "CaO/SiO2_molar_ratio", "V2O5_mol_percent", "Al2O3_wt_percent", "MgO_wt_percent", "V4+/V5+_at_log10_pO2_atm_-2", "V3+/V4+_at_log10_pO2_atm_-8")) for row in t71] == TABLE_7_1_GRID

    t72 = contexts["farah_1998_table_7_2_average_slag_vanadium_valency"]["values"]["printed_data"]["rows"]
    assert [(row["log10_pO2_atm"], row["values"]) for row in t72] == TABLE_7_2_GRID
    assert len(TABLE_4_1_GRID) == 21 and len(TABLE_5_2_GRID) == 7 and len(TABLE_7_1_GRID) == 8 and len(TABLE_7_2_GRID) == 5


def test_independent_grid_and_extract_cell_mutations_are_detected() -> None:
    source = _load()
    actual = source["species"]["V"]["context"][0]["values"]["printed_data"]["rows"]
    expected = deepcopy(TABLE_4_1_GRID)
    actual_grid = [list(row) for row in actual]
    expected[0] = ("A", "0.5", "-", "99", "-", "-", "-")
    with pytest.raises(AssertionError):
        assert [tuple(row) for row in actual_grid] == expected

    damaged_extract = deepcopy(actual_grid)
    damaged_extract[0][3] = "99"
    with pytest.raises(AssertionError):
        assert [tuple(row) for row in damaged_extract] == TABLE_4_1_GRID


def test_deferred_figures_and_context_only_extract_migrate_without_hard_issues() -> None:
    source = _load()
    contexts = source["species"]["V"]["context"]
    figures = [entry for entry in contexts if entry["type"] == "figure_record"]
    assert len(figures) == 36
    assert all(entry["values"]["digitisation"] == "deferred to a figure pass" for entry in figures)
    assert all(entry["values"]["quantity_token"] is None for entry in figures)
    assert all(entry["values"]["points"] == [] for entry in figures)
    assert all(all(series["marker_count"] is None for series in entry["values"]["series"]) for entry in figures)
    figure_pages = {entry["locator"]["figure"]: entry["locator"]["page"] for entry in figures}
    assert figure_pages["5.3"] == 113
    assert figure_pages["5.16"] == 129
    assert figure_pages["7.20"] == 181
    assert figure_pages["7.25"] == 188

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.observations
    assert len(contexts) == 42
