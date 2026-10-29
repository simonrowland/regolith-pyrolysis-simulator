from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/farah-1998-vanadium-silicate-melts-thesis.yaml"

# Independent transcription from the 600 dpi table renders on physical PDF pages 96, 118, 128, 171, 185, 249, 250,
# 257, 258, and 259.
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
APPENDIX_IX_249_GRID = [
    ("A", "1125", "10^-2", ("-",), ("0.095", "0.06", "0.06"), ("1.56", "1.47", "1.469"), ("-1.325",), ("-",)),
    ("A", "1125", "10^-4", ("-",), ("0.161", "0.161", "0.178"), ("1.476", "1.370", "1.371"), ("-0.927",), ("-",)),
    ("A", "1125", "10^-6", ("-",), ("0.116", "0.27", "0.27"), ("1.382", "1.260", "1.261"), ("-0.768",), ("-",)),
    ("A", "1125", "10^-8", ("-",), ("0.178", "0.45", "0.45"), ("1.370", "1.080", "1.081"), ("-0.494",), ("-",)),
    ("A", "1125", "10^-10", ("-",), ("0.41", "0.70", "0.70"), ("1.07", "0.830", "1.07"), ("-0.272",), ("-",)),
    ("A", "1125", "10^-12", ("0.177", "0.06", "0.06"), ("0.518", "0.80", "0.80"), ("0.835", "0.72", "0.719"), ("-0.023",), ("-0.775",)),
    ("A", "1125", "10^-14", ("0.578", "0.880", "0.879"), ("0.952", "0.650", "0.651"), ("-",), ("-",), ("0.043",)),
    ("A", "1225", "10^-2", ("-",), ("0.60", "0.90", "0.91"), ("1.51", "1.45", "1.45"), ("-1.263",), ("-",)),
    ("A", "1225", "10^-4", ("-",), ("0.169", "0.210", "0.322"), ("1.431", "1.33", "1.218"), ("-0.744",), ("-",)),
    ("A", "1225", "10^-6", ("-",), ("0.168", "0.370", "0.37"), ("1.392", "1.17", "1.19"), ("-0.600",), ("-",)),
    ("A", "1225", "10^-8", ("-",), ("0.356", "0.510", "0.510"), ("1.184", "1.03", "1.03"), ("-0.366",), ("-",)),
    ("A", "1225", "10^-10", ("0.263", "0.175", "0.263"), ("0.623", "0.800", "0.623"), ("0.624", "0.565", "0.623"), ("0.056",), ("-0.450",)),
    ("A", "1225", "10^-12", ("0.983", "0.860", "0.86"), ("0.398", "0.680", "0.680"), ("-",), ("-",), ("0.222",)),
]
APPENDIX_IX_250_GRID = [
    ("A", "1225", "10^-14", ("0.105", "0.910", "0.9"), ("0.402", "0.630", "0.630"), ("-",), ("-",), ("0.263",)),
    ("A", "1325", "10^-2", ("-",), ("0.140",), ("1.39",), ("-0.997",), ("-",)),
    ("A", "1325", "10^-4", ("-",), ("0.438", "0.310"), ("1.092", "1.22"), ("-0.397", "-0.595"), ("-",)),
    ("A", "1325", "10^-6", ("-",), ("0.600",), ("0.93",), ("-0.190",), ("-",)),
    ("A", "1325", "10^-8", ("-",), ("0.900",), ("0.63",), ("0.155",), ("-",)),
    ("A", "1325", "10^-10", ("0.325", "0.447"), ("0.980", "0.735"), ("0.226", "0.348"), ("0.638", "0.325"), ("-0.480", "-0.216")),
    ("A", "1325", "10^-12", ("0.788", "0.55"), ("0.741", "0.98"), ("-",), ("-",), ("0.027", "-0.251")),
    ("A", "1325", "10^-14", ("1.013",), ("0.517",), ("-",), ("-",), ("0.292",)),
    ("B", "1225", "10^-2", ("-", "-"), ("0.23", "0.14"), ("7.17", "7.06"), ("-1.494", "-1.5"), ("-", "-")),
    ("B", "1225", "10^-4", ("-",), ("0.40",), ("7.0",), ("-1.243",), ("-",)),
    ("B", "1225", "10^-6", ("-",), ("0.88",), ("6.52",), ("-0.870",), ("-",)),
    ("B", "1225", "10^-8", ("-",), ("2.50",), ("4.9",), ("-0.292",), ("-",)),
    ("B", "1225", "10^-10", ("-", "-"), ("5.90", "3.2"), ("1.5", "4.0"), ("0.594", "-0.096"), ("-", "-")),
    ("B", "1225", "10^-12", ("1.595",), ("4.30",), ("1.505",), ("0.456",), ("-0.431",)),
    ("C", "not_printed", "10^-2", ("-",), ("0.010",), ("6.99",), ("-2.844",), ("-",)),
    ("C", "not_printed", "10^-4", ("-",), ("0.015",), ("6.985",), ("-2.668",), ("-",)),
    ("C", "not_printed", "10^-6", ("-", "-"), ("0.05", "0.10"), ("6.95", "6.87"), ("-2.143", "-1.829"), ("-", "-")),
    ("C", "not_printed", "10^-8", ("-", "-"), ("0.100", "0.071"), ("6.9", "6.89"), ("-1.839", "-1.989"), ("-", "-")),
    ("C", "not_printed", "10^-10", ("-", "-"), ("0.70", "0.378"), ("6.3", "6.58"), ("-0.954", "-1.241"), ("-", "-")),
    ("C", "not_printed", "10^-12", ("-", "-"), ("1.75", "1.879"), ("5.25", "5.06"), ("-0.477", "-0.430"), ("-", "-")),
    ("D", "not_printed", "10^-2", ("-",), ("0.595",), ("13.21",), ("-1.346",), ("-",)),
    ("D", "not_printed", "10^-4", ("-",), ("0.960",), ("12.84",), ("-1.126",), ("-",)),
    ("D", "not_printed", "10^-6", ("-",), ("2.060",), ("11.74",), ("-0.756",), ("-",)),
    ("D", "not_printed", "10^-8", ("-",), ("4.420",), ("9.38",), ("-0.327",), ("-",)),
    ("D", "not_printed", "10^-10", ("2.81",), ("8.18",), ("2.81",), ("0.464",), ("-0.464",)),
]
APPENDIX_XX_257_GRID = [
    ("F", "10^-2", "-", "3.45", "4.51", "-0.116", "-"),
    ("F", "10^-4", "1.23", "5.5", "1.23", "0.651", "-0.651"),
    ("F", "10^-6", "2.76", "5.2", "-", "-", "-0.275"),
    ("F", "10^-8", "5.86", "2.1", "-", "-", "0.446"),
    ("F", "10^-9", "6.76", "1.2", "-", "-", "0.751"),
    ("G", "10^-2", "-", "2.9", "5.06", "-0.242", "-"),
    ("G", "10^-4", "1.0", "5.0", "1.96", "0.407", "-0.699"),
    ("G", "10^-6", "1.63", "4.7", "1.63", "0.459", "-0.459"),
    ("G", "10^-8", "4.96", "3.0", "-", "-", "0.218"),
    ("G", "10^-9", "5.96", "2.0", "-", "-", "0.474"),
    ("J", "10^-2", "-", "2.5", "2.487", "0.002", "-"),
    ("J", "10^-4", "-", "3.0", "1.987", "0.179", "-"),
    ("J", "10^-6", "0.80", "3.3", "0.887", "0.571", "-0.615"),
    ("J", "10^-8", "2.087", "2.9", "-", "", "-0.143"),
    ("J", "10^-9", "2.987", "2.0", "-", "", "0.174"),
    ("K", "10^-2", "-", "2.8", "6.95", "-0.95", "-"),
    ("K", "10^-4", "0.75", "6.0", "3.0", "0.301", "-0.903"),
    ("K", "10^-6", "1.75", "5.6", "2.45", "0.359", "-0.518"),
    ("K", "10^-8", "5.85", "3.9", "-", "-", "0.176"),
    ("K", "10^-9", "6.55", "3.2", "-", "-", "0.311"),
    ("L", "10^-2", "-", "2.5", "5.63", "-0.353", "-"),
    ("L", "10^-4", "0.95", "5.1", "2.08", "0.389", "-0.729"),
    ("L", "10^-6", "1.6", "4.8", "1.73", "0.443", "-0.477"),
    ("L", "10^-8", "5.23", "2.9", "-", "-", "0.256"),
    ("L", "10^-9", "6.23", "1.9", "-", "-", "0.516"),
    ("M", "10^-2", "-", "2.8", "4.88", "-0.241", "-"),
    ("M", "10^-4", "0.85", "4.3", "2.53", "0.230", "-0.704"),
    ("M", "10^-6", "1.955", "3.77", "1.955", "0.285", "-0.285"),
    ("M", "10^-8", "5.18", "2.5", "-", "-", "0.316"),
    ("M", "10^-9", "6.18", "1.5", "-", "-", "0.615"),
    ("N", "10^-2", "-", "1.2", "8.55", "-0.853", "-"),
    ("N", "10^-4", "-", "3.0", "6.75", "-0.352", "-"),
    ("N", "10^-6", "-", "4.3", "5.45", "-0.103", "-"),
    ("N", "10^-8", "5.75", "4.0", "-", "-", "0.158"),
    ("N", "10^-9", "5.75", "4.0", "-", "-", "0.158"),
]
APPENDIX_XX_258_GRID = [
    ("O", "10^-2", "-", "0.79", "7.34", "-0.968", "-"),
    ("O", "10^-4", "-", "2.09", "6.04", "-0.461", "-"),
    ("O", "10^-6", "-", "3.2", "4.93", "-0.188", "-"),
    ("O", "10^-8", "5.13", "3.0", "-", "-", "0.233"),
    ("O", "10^-9", "5.13", "3.0", "-", "-", "0.233"),
]
APPENDIX_XXI_GRID = [
    ("1125", "2", "0.005", "1.525", "2.50"),
    ("1125", "4", "0.04", "1.49", "1.60"),
    ("1125", "6", "0.07", "1.46", "1.30"),
    ("1125", "8", "0.07", "1.46", "1.30"),
    ("1125", "10", "0.10", "1.43", "1.15"),
    ("1125", "12", "0.15", "1.38", "1.15"),
    ("1125", "14", "0.11", "1.42", "1.10"),
    ("1125", "16", "0.17", "1.36", "0.90"),
    ("1225", "2", "0.002", "1.528", "2.90"),
    ("1225", "4", "0.073", "1.457", "1.30"),
    ("1225", "6", "0.09", "1.44", "1.20"),
    ("1225", "8", "0.10", "1.43", "1.15"),
    ("1225", "10", "0.10", "1.43", "1.15"),
    ("1225", "12", "0.11", "1.42", "1.10"),
    ("1225", "14", "0.11", "1.42", "1.10"),
    ("1225", "16", "0.11", "1.42", "1.10"),
]
APPENDIX_XXII_GRID = [
    ("G", "1", "2.54", "5.42", "0.468"),
    ("G", "2", "2.25", "5.71", "0.393"),
    ("G", "3.5", "2.12", "5.84", "0.363"),
    ("G", "4", "2.17", "5.79", "0.376"),
    ("G", "5", "2.02", "5.94", "0.341"),
    ("G", "6", "2.13", "5.83", "0.366"),
    ("O", "1", "7.99", "7.333", "0.109"),
    ("O", "2", "7.37", "7.393", "0.099"),
    ("O", "3.2", "7.35", "7.395", "0.09"),
    ("O", "4", "6.89", "7.440", "0.093"),
    ("O", "5", "7.36", "7.394", "0.09"),
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

    ix_columns = (
        "V3+_wt_percent",
        "V4+_wt_percent",
        "V5+_wt_percent",
        "log10_V4_over_V5_mean",
        "log10_V3_over_V4_mean",
    )
    ix249 = contexts["farah_1998_appendix_ix_melts_redox_page_225"]["values"]["printed_data"]["blocks"]
    ix250 = contexts["farah_1998_appendix_ix_melts_redox_page_226"]["values"]["printed_data"]["blocks"]
    assert [
        (block["melt"], block["temperature_C"], block["pO2_atm"], *(tuple(block["cell_values"][key]) for key in ix_columns))
        for block in ix249
    ] == APPENDIX_IX_249_GRID
    assert [
        (block["melt"], block["temperature_C"], block["pO2_atm"], *(tuple(block["cell_values"][key]) for key in ix_columns))
        for block in ix250
    ] == APPENDIX_IX_250_GRID

    ix_context = contexts["farah_1998_appendix_ix_melts_redox_page_225"]["values"]["printed_data"]
    assert ix_context["columns"] == [
        "melt", "temperature_C", "pO2_atm", *ix_columns
    ]
    xx257 = contexts["farah_1998_appendix_xx_slags_redox_page_233"]["values"]["printed_data"]["rows"]
    xx258 = contexts["farah_1998_appendix_xx_slags_redox_page_234"]["values"]["printed_data"]["rows"]
    assert [tuple(row) for row in xx257] == APPENDIX_XX_257_GRID
    assert [tuple(row) for row in xx258] == APPENDIX_XX_258_GRID

    xxi = contexts["farah_1998_appendix_xxi_na2o_2sio2_v2o5_redox"]["values"]["printed_data"]["rows"]
    xxii = contexts["farah_1998_appendix_xxii_slags_g_o_redox"]["values"]["printed_data"]["rows"]
    assert [tuple(row) for row in xxi] == APPENDIX_XXI_GRID
    assert [tuple(row) for row in xxii] == APPENDIX_XXII_GRID
    assert [len(APPENDIX_IX_249_GRID), len(APPENDIX_IX_250_GRID), len(APPENDIX_XX_257_GRID), len(APPENDIX_XX_258_GRID), len(APPENDIX_XXI_GRID), len(APPENDIX_XXII_GRID)] == [13, 25, 35, 5, 16, 11]


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

    appendix = _load()["species"]["V"]["context"]
    appendix_xx_rows = next(
        entry for entry in appendix if entry["observation_id"] == "farah_1998_appendix_xx_slags_redox_page_233"
    )["values"]["printed_data"]["rows"]
    expected_appendix = deepcopy(APPENDIX_XX_257_GRID)
    actual_appendix = [tuple(row) for row in appendix_xx_rows]
    changed_grid = deepcopy(expected_appendix)
    changed_grid[0] = ("F", "10^-2", "-", "99", "4.51", "-0.116", "-")
    with pytest.raises(AssertionError):
        assert actual_appendix == changed_grid

    damaged_appendix_extract = deepcopy(actual_appendix)
    damaged_appendix_extract[0] = ("F", "10^-2", "-", "99", "4.51", "-0.116", "-")
    with pytest.raises(AssertionError):
        assert damaged_appendix_extract == APPENDIX_XX_257_GRID


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
    assert len(contexts) == 48
