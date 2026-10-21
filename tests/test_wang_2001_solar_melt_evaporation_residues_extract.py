"""Independent 600-dpi table pins for Wang et al. (2001)."""

from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import re

import pytest
import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/wang-2001-solar-melt-evaporation-residues.yaml"
TABLE1_SAMPLES = [
    "SC12", "SC11", "SC5", "SC13", "SC8", "SC4", "SC14", "SC1",
    "SC7", "SC2", "SC16", "SC6", "SC3", "SC15", "SC10", "SC9",
]
TABLE1_RENDER = """
EM.MgO 24.4~0.3|25.3~0.4|26.1~0.5|29.7~0.2|32.6~0.1|35.2~0.1|42.7~0.1|43.2~0.1|44.9~0.1|43.0~0.1|42.7~0.1|37.2~0.1|30.8~0.2|26.7~0.2|26.0~0.1|0.001
EM.Al2O3 2.73~0.08|2.64~0.07|2.90~0.09|3.61~0.04|3.34~0.02|4.00~0.02|5.60~0.02|6.75~0.04|6.69~0.05|8.49~0.07|8.65~0.05|13.8~0.1|17.4~0.2|20.2~0.2|22.3~0.2|53.0~0.1
EM.SiO2 35.1~0.2|34.8~0.2|36.2~0.1|40.5~0.1|40.6~0.1|44.9|46.7|45.0|43.0|41.9|41.7|37.3~0.1|36.7~0.1|35.4~0.1|32.2~0.1|4.33~0.01
EM.CaO 2.24~0.08|2.08~0.06|2.27~0.08|2.58~0.03|2.70~0.01|3.09~0.01|4.00~0.01|4.79~0.03|4.88~0.02|6.29~0.03|6.59~0.03|11.2~0.1|14.5~0.1|17.0~0.2|18.7~0.2|41.1~0.1
EM.TiO2 0.097~0.004|0.097~0.003|0.106~0.004|0.141~0.002|0.150~0.001|0.173~0.001|0.218~0.002|0.252~0.002|0.260~0.002|0.311~0.002|0.325~0.002|0.506~0.004|0.597~0.006|0.681~0.004|0.779~0.004|1.57~0.01
EM.FeO 35.5~0.3|35.2~0.4|32.4~0.4|23.5~0.2|20.6~0.2|12.6|0.808~0.003|0.069~0.002|0.284~0.002|0.012~0.002|0.007~0.001|0.015~0.002|0.007~0.002|0.005~0.002|0.013~0.002|0.003~0.002
IM.MgO 24.0~0.4|25.3~1.8|23.8~1.5|28.1~0.2|33.7~0.2|34.1~0.2|39.6~0.4|42.0~0.2|43.3~0.7|41.7~0.2|40.3~0.5|36.4~0.9|30.2~0.2|24.4~0.1|25.2~0.5|0.00244~0.00022
IM.Al2O3 2.28~0.15|2.51~0.49|3.26~0.19|3.61~0.23|3.95~0.26|5.26~0.05|6.90~0.40|7.16~0.25|7.41~0.21|9.29~0.17|9.91~0.57|14.2|18.48~0.55|21.1~0.7|23.9~0.5|55.3~1.7
IM.SiO2 35.1|34.6|36.1|40.1|40.3|44.4|46.3|44.8|42.7|41.7|41.4|37.0|36.4|34.9|31.8|4.20~0.01
IM.CaO 1.72~0.13|2.23~0.55|2.49~0.01|2.87~0.17|2.91~0.15|3.70~0.27|5.29~0.09|5.20~0.07|5.31~0.43|6.41~0.37|7.27~0.15|11.1~0.9|13.39~0.67|17.4~0.8|17.1~0.7|36.4~1.7
IM.TiO2 0.090~0.010|0.084~0.022|0.094~0.018|0.167~0.007|0.163~0.011|0.202~0.032|0.286~0.010|0.270~0.010|0.264~0.032|0.302~0.019|0.365~0.014|0.462~0.039|0.493~0.037|0.652~0.026|0.647~0.027|1.11~0.07
IM.FeO 36.7~0.3|34.9~0.8|34.0~1.4|24.1~0.2|18.2~0.3|11.3|0.803~0.033|0.114~0.014|0.259~0.009|0.059~0.023|0.076~0.016|0.073~0.027|0.055~0.030|0.0853~0.0087|0.080~0.020|0.066~0.016
IM.WO3 0.010~0.002|0.201~0.010|0.096~0.008|0.729~0.070|0.592~0.074|0.781|0.357~0.030|0.174~0.007|0.355~0.006|0.119~0.013|0.092~0.006|0.119~0.017|0.061~0.002|0.069~0.003|0.103~0.011|0.061~0.010
REE.La 80.3~4.1|97~18|100~13|123~7|121~6|152~2|293~27|237~11|247~1|293~12|401~32|539~6|640~41|975~53|914~89|2128~131
REE.Ce 88.2~3.1|112~21|114~19|128~5|117~5|135~4|158~15|73.2~3.5|85.4~1.3|10.3~0.4|7.47~0.56|5.20~0.49|1.10~0.51|0.289~0.036|1.87~0.38|0.83~0.18
REE.Pr 83.8~4.9|101~18|108~16|126~7|122~6|154~6|285~26|225~10|231~2|263~12|362~28|429~10|500~37|744~44|643~65|1182~89
REE.Nd 67.7~3.6|82~15|86~14|104~6|100~4|123~4|238~23|188~9|196|235~9|326~25|417~9|512~38|792~47|698~70|1679~121
REE.Sm 72.1~3.4|89~17|88.1~7.8|114~6|103~3|123~3|240~16|189~3|196~11|236~3|340~19|440~12|530~11|856~30|754~76|1908~112
REE.Eu 74.4~4.0|93~19|89.9~9.1|116~6|103~3|122~3|231~15|181~3|188~13|232~6|338~21|443~12|536~8|869~32|771~81|2017~124
REE.Gd 75.1~3.6|92~16|90.2~8.1|117~6|108~5|134|273~22|219~4|221~7|261~1|379~26|469~4|571~24|908~36|795~75|1924~91
REE.Tb 93.2~4.7|113~21|109~11|145~6|134~4|162~2|326~23|259~7|261~10|309~1|440~27|538~2|657~22|1033~39|895~86|2092~97
REE.Dy 90.6~3.8|110~21|104~9|142~6|128~3|155~3|310~21|246~4|246~9|293~2|421~24|507~6|623~12|1007~35|862~83|2081~99
REE.Ho 86.9~4.5|109~21|103~10|137~6|124~4|153~3|301~24|237~4|235~9|277~2|412~25|491~3|604~18|963~39|819~81|1964~98
REE.Er 97.7~5.1|119~22|115~12|149~9|140~6|168~3|329~25|262~4|259~7|303|443~29|533~1|652~26|1039~47|891~82|2060~103
REE.Tm 91.8~3.8|110~19|108~14|137~7|129~6|157~2|292~26|232~7|231~5|274~9|397~33|471~10|615~70|934~53|800~73|1909~113
REE.Yb 92.3~4.0|111~20|107~16|135~7|124~6|152~6|274~28|214~7|217~1|260~11|382~35|451~17|602~74|898~63|770~66|1897~126
REE.Lu 103~6|123~17|125~24|150~10|147~10|180~12|330~41|264~12|257~11|299~27|430~46|498~38|678~114|984~83|842~69|1941~145
AGG.SigmaLn2O3 0.138~0.006|0.169~0.031|0.167~0.021|0.210~0.011|0.196~0.008|0.239~0.002|0.447~0.038|0.349~0.010|0.354~0.007|0.409~0.008|0.585~0.043|0.718~0.007|0.890~0.057|1.38~0.07|1.20~0.11|2.86~0.16
""".strip()
TABLE2_RENDER = """
SC1 1800|60|165.3|70.5|57.29
SC2 1800|180|161.2|49.7|69.17
SC3 1800|300|161.0|22.0|86.34
SC4 1800|15|160.3|101.5|36.68
SC5 1800|0|166.3|153.7|7.58
SC6 2000|15|161.5|27.6|82.91
SC7 2000|5|170.1|67.4|60.38
SC8 2000|0|166.1|112.7|32.15
SC9 2000|25|165.8|7.7|95.36
SC10 2000|20|169.4|17.6|89.61
SC11 1500|0|139.8|135.0|3.43
SC12 1400|0|182.7|182.0|0.38
SC13 1800|7|182.6|142.8|21.80
SC14 1800|40|173.2|82.2|52.54
SC15 1800|220|176.1|20.1|88.59
SC16 1800|120|179.7|50.2|72.06
""".strip()
TABLE4_SAMPLES = [
    "SC12", "SC11", "SC5", "SC13", "SC8", "SC4", "SC14", "SC1",
    "SC7", "SC2", "SC16", "SC6", "SC3", "SC15", "SC10",
]
TABLE4_EVAP = "0.38 3.43 7.58 21.80 32.15 36.68 52.54 57.29 60.38 69.17 72.06 82.91 86.34 88.59 89.61".split()
STARTING_COMPOSITION_WT_PCT = {
    "MgO": "24.8",
    "Al2O3": "2.46",
    "SiO2": "33.8",
    "CaO": "2.04",
    "TiO2": "0.110",
    "FeO": "36.7",
}
STARTING_COMPOSITION_DETAILS = {
    "SigmaLn2O3_wt_pct": "0.142",
    "trace_elements_ppm": {
        "La": "87.1",
        "Ce": "82.3",
        "Pr": "85.1",
        "Nd": "86.4",
        "Sm": "85.6",
        "Eu": "86.9",
        "Gd": "88.6",
        "Tb": "91.3",
        "Dy": "88.6",
        "Ho": "86.1",
        "Er": "90.2",
        "Tm": "87.7",
        "Yb": "88.4",
        "Lu": "88.5",
    },
}
TABLE3_TI_JC_RENDER = {
    ("1800", "120–220"): "7.-9×10^-7",
    ("2000", "20–25"): "4.41×10^-6",
}
TABLE4_RENDER = """
17O/16O 0.0|0.5|1.2|3.1|4.0|4.8|8.4|8.3|10.2|12.6|13.8|19.4|22.1|22.8|28.0
18O/16O 0.0|0.4|1.6|5.2|7.2|8.9|16.0|15.6|19.3|24.1|26.3|37.4|43.0|44.5|53.1
25Mg/24Mg -1.6~0.2|0.0~1.7|3.6~0.4|-0.8~0.4|5.8~1.0|0.8~1.1|-1.0~0.4|7.7~0.5|6.3~1.4|11.6~1.5|7.6~0.5|25.3~0.4|29.2~1.2|27.0~0.9|37.8~0.7
26Mg/24Mg 0.4~0.5|0.0~1.4|1.6~0.7|0.6~0.5|4.4~2.0|-0.8~1.0|0.7~0.2|9.3~1.3|8.3~2.2|19.4~1.9|17.5~0.3|44.2~1.7|53.4~2.1|57.4~1.6|70.5~1.3
29Si/28Si 0.0|0.2|1.5|1.4|2.2|2.3|4.4|7.5|6.7|9.3|8.9|16.1|17.6|18.0|20.3
30Si/28Si -0.6|0.6|1.6|2.8|4.0|5.2|8.3|11.1|12.8|16.9|17.9|28.7|33.4|35.6|40.3
""".strip()


def cells_by_label(raw_table: str) -> dict[str, list[str]]:
    rows = {}
    for line in raw_table.splitlines():
        label, cells = line.split(" ", maxsplit=1)
        rows[label] = cells.split("|")
    return rows


def split_value_and_error(raw_cell: str) -> tuple[str, str | None]:
    value, separator, error = raw_cell.partition("~")
    return value, error if separator else None


def assert_table1_grid_matches_render(extract: dict, rendered: dict[str, list[str]]) -> None:
    table1 = [
        row
        for row in extract["species"]["SiO2"]["observations"]
        if row["values"].get("quantity") == "residue_composition_vs_time"
    ]
    composition = {
        row["values"]["analytical_method"]: row["values"]["series"]
        for row in table1
    }
    for label, expected_cells in rendered.items():
        group, formula = label.split(".", maxsplit=1)
        if group == "REE":
            points = next(
                row["values"]["series"]
                for row in extract["species"][formula]["observations"]
                if row["observation_id"] == f"wang_2001_table1_{formula.lower()}_residue_concentration"
            )
            value_field, error_field = "residue_ppm", "uncertainty_ppm"
        elif group == "AGG":
            points = next(row["values"]["rows_as_printed"] for row in extract["species"]["SiO2"]["context"] if row["observation_id"] == "wang_2001_table1_ln2o3_aggregate_no_formula")
            value_field, error_field = "SigmaLn2O3_wt_pct", "standard_error_wt_pct"
        else:
            method = {"EM": "electron microprobe", "IM": "ion microprobe"}[group]
            points = composition[method]
            value_field, error_field = f"{formula}_wt_pct", None
        assert len(expected_cells) == len(TABLE1_SAMPLES)
        for sample, raw_cell in zip(TABLE1_SAMPLES, expected_cells, strict=True):
            expected_value, expected_error = split_value_and_error(raw_cell)
            point = next(point for point in points if point.get("sample") == sample)
            assert point[value_field] == expected_value, (label, sample)
            if error_field is not None:
                assert point.get(error_field) == expected_error, (label, sample)
            else:
                assert point.get("standard_errors", {}).get(formula) == expected_error, (label, sample)


def test_rendered_table_grids_and_cell_mutations() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    rendered = cells_by_label(TABLE1_RENDER)
    assert_table1_grid_matches_render(extract, rendered)

    changed_render = deepcopy(rendered)
    changed_render["EM.MgO"][0] = "999"
    with pytest.raises(AssertionError):
        assert_table1_grid_matches_render(extract, changed_render)

    changed_extract = deepcopy(extract)
    changed_sample = next(
        row
        for row in changed_extract["species"]["SiO2"]["observations"][0]["values"]["series"]
        if row.get("sample") == "SC12"
    )
    changed_sample["MgO_wt_pct"] = "999"
    with pytest.raises(AssertionError):
        assert_table1_grid_matches_render(changed_extract, rendered)


def test_starting_composition_is_separate_and_pinned_to_table1() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    observation = next(
        row
        for row in extract["species"]["SiO2"]["observations"]
        if row["observation_id"] == "wang_2001_table1_electron_microprobe_residue_compositions"
    )
    values = observation["values"]
    composition = values["composition_wt_pct"]
    assert {key: value for key, value in composition.items() if key != "locator"} == STARTING_COMPOSITION_WT_PCT
    assert set(composition) == {*STARTING_COMPOSITION_WT_PCT, "locator"}
    assert composition["locator"] == {
        "page": 4,
        "published_page": 482,
        "pdf_page_index": 3,
        "table": "1",
        "row": "Starting comp.",
        "column": "b",
    }
    assert values["starting_composition_details"] == STARTING_COMPOSITION_DETAILS

    initial_rows = [
        row for row in values["series"] if row.get("T_C_is_initial_composition") is True
    ]
    assert len(initial_rows) == 1
    initial_row = initial_rows[0]
    assert initial_row["mass_loss_pct"] == "0"
    assert not {"T_C", "T_K", "temperature_C", "temperature_K"}.intersection(initial_row)
    assert all(
        "T_C_is_initial_composition" not in row
        for row in values["series"]
        if row.get("sample") in TABLE1_SAMPLES
    )


def test_table3_ti_volatilities_match_printed_cells() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    table3 = next(
        row
        for row in extract["species"]["SiO2"]["context"]
        if row["observation_id"] == "wang_2001_table3_calculated_evaporation_parameters"
    )
    rows = table3["values"]["rows_as_printed"]
    cells = {
        (row["temperature_C"], row["time_min"]): row["J_over_C_cm_s"]["Ti"]
        for row in rows
        if "Ti" in row.get("J_over_C_cm_s", {})
    }
    assert {key: cells[key] for key in TABLE3_TI_JC_RENDER} == TABLE3_TI_JC_RENDER
    note = table3["values"]["note"]
    assert "7.-9 × 10^-7" in note
    assert "1800 °C / 120–220 min Ti J/C cell" in note


def test_table2_and_table4_rendered_rows_are_bound_to_samples() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    printed_rows = {}
    for line in TABLE2_RENDER.splitlines():
        sample, raw = line.split(" ", maxsplit=1)
        printed_rows[sample] = raw.split("|")

    def mass_loss_observation(source: dict, sample: str) -> dict:
        matches = [
            row
            for row in source["species"]["SiO2"]["observations"]
            if row["observation_id"] == f"wang_2001_table2_{sample.lower()}_mass_loss"
        ]
        assert len(matches) == 1
        return matches[0]

    def assert_mass_loss_grid_matches_render(source: dict, rendered: dict[str, list[str]]) -> None:
        assert not any(f"SC{index}" in source["species"] for index in range(1, 17))
        mass_loss_rows = [
            row
            for row in source["species"]["SiO2"]["observations"]
            if row["values"].get("quantity") == "mass_loss_fraction"
        ]
        assert len(mass_loss_rows) == 16
        for sample, expected in rendered.items():
            observation = mass_loss_observation(source, sample)
            point = observation["values"]["points"][0]
            assert point["sample"] == sample
            assert [
                point["T_C"], point["time_min"], point["initial_mass_mg_as_printed"],
                point["final_mass_mg_as_printed"], point["mass_loss_wt_pct"],
            ] == expected
            assert point["locator"]["page"] == 6
            assert point["locator"]["pdf_page_index"] == 5
            assert point["locator"]["table"] == "2"
            assert point["locator"]["row"] == sample
            assert point["oxides_wt_pct"] == {
                "MgO": "24.8",
                "Al2O3": "2.46",
                "SiO2": "33.8",
                "CaO": "2.04",
                "TiO2": "0.110",
                "FeO": "36.7",
            }

    assert_mass_loss_grid_matches_render(extract, printed_rows)
    changed_render = deepcopy(printed_rows)
    changed_render["SC2"][4] = "999"
    with pytest.raises(AssertionError):
        assert_mass_loss_grid_matches_render(extract, changed_render)
    changed_extract = deepcopy(extract)
    mass_loss_observation(changed_extract, "SC2")["values"]["points"][0]["mass_loss_wt_pct"] = "999"
    with pytest.raises(AssertionError):
        assert_mass_loss_grid_matches_render(changed_extract, printed_rows)

    sc1 = mass_loss_observation(extract, "SC1")["values"]["points"][0]
    assert "Table 1 prints 57.35; values disagree; the source does not reconcile them" in sc1["locator"]["note"]
    assert sc1["locator"]["note"].endswith("physical PDF page 4, published page 482, Table 1, row SC1.")

    rendered = cells_by_label(TABLE4_RENDER)
    for ratio, expected_cells in rendered.items():
        formula = {"17O/16O": "O", "18O/16O": "O", "25Mg/24Mg": "Mg", "26Mg/24Mg": "Mg", "29Si/28Si": "Si", "30Si/28Si": "Si"}[ratio]
        row = next(
            row for row in extract["species"][formula]["observations"]
            if row["values"].get("isotope_ratio") == ratio
        )
        points = {point["sample_as_printed"]: point for point in row["values"]["series"]}
        for index, (sample, raw_cell) in enumerate(zip(TABLE4_SAMPLES, expected_cells, strict=True)):
            value, error = split_value_and_error(raw_cell)
            point = points[sample]
            assert point["isotope_delta_isotope_delta"] == value, (ratio, sample)
            assert point.get("uncertainty_permil") == error, (ratio, sample)
            assert point["VF_wt_pct"] == TABLE4_EVAP[index]


def test_migrated_species_temperature_and_point_conditions() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    all_observations = tuple(migrator.result.observations.values())
    initial_marker_observations = [
        row
        for row in all_observations
        if row.locator.note and "Starting comp." in row.locator.note
    ]
    assert len(initial_marker_observations) == 1
    assert initial_marker_observations[0].admission.reason == (
        "initial composition is not a run-temperature mass-loss measurement"
    )
    observations = tuple(
        row for row in all_observations if row not in initial_marker_observations
    )
    assert len(observations) == 490
    residue_observations = [
        row
        for row in observations
        if row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    assert len(residue_observations) == 384
    isotope_observations = [
        row
        for row in observations
        if row.identity.quantity.value is Quantity.ISOTOPE_DELTA
    ]
    assert len(isotope_observations) == 90
    assert all(row.identity.species.phase.value == "l" for row in isotope_observations)
    experiment_initial_compositions = [
        experiment.sample.initial_composition
        for experiment in migrator.result.experiments.values()
        if experiment.sample.initial_composition is not None
        and experiment.sample.initial_composition.state.is_value
    ]
    assert len(experiment_initial_compositions) == 1
    expected_initial = experiment_initial_compositions[0].state.value
    expected_initial_wt_pct = {
        formula: Decimal(value)
        for formula, value in STARTING_COMPOSITION_WT_PCT.items()
    }
    assert all(
        row.identity.composition is not None and row.identity.composition.is_value
        for row in residue_observations
    )
    assert all(
        row.identity.composition.value == expected_initial
        or row.identity.composition.value.as_map() == expected_initial_wt_pct
        for row in residue_observations
    )

    expected_by_quantity = {
        Quantity.RESIDUE_COMPONENT_COMPOSITION: {
            **{formula: 32 for formula in ("MgO", "Al2O3", "SiO2", "CaO", "FeO")},
            **{formula: 16 for formula in ("La", "Ce", "Pr", "Nd", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu")},
        },
        Quantity.ISOTOPE_DELTA: {"O": 30, "Mg": 30, "Si": 30},
        Quantity.MASS_LOSS_FRACTION: {"unknown": 16},
    }
    actual_by_quantity: dict[Quantity, Counter[str]] = defaultdict(Counter)
    actual_by_sample: dict[tuple[Quantity, str], Counter[str]] = defaultdict(Counter)
    temperature_c = {
        "SC1": "1800", "SC2": "1800", "SC3": "1800", "SC4": "1800",
        "SC5": "1800", "SC6": "2000", "SC7": "2000", "SC8": "2000",
        "SC9": "2000", "SC10": "2000", "SC11": "1500", "SC12": "1400",
        "SC13": "1800", "SC14": "1800", "SC15": "1800", "SC16": "1800",
    }
    for row in observations:
        quantity = row.identity.quantity.value
        formula = row.identity.species.formula
        assert quantity in expected_by_quantity
        actual_by_quantity[quantity][formula] += 1
        note = row.locator.note or ""
        match = re.search(r"row=(SC\d+)(?!\d)", note)
        sample = match.group(1) if match else None
        assert sample is not None, note
        actual_by_sample[(quantity, sample)][formula] += 1

        expected_temperature = Decimal(temperature_c[sample]) + Decimal("273.15")
        assert row.identity.temperature_K.value == expected_temperature, (quantity, sample, formula)
        assert row.locator.page == row.locator.pdf_page_index + 1
        conditions = row.point_conditions or {}
        required_conditions = (
            ("temperature_K", "time_min", "total_pressure_Pa", "printed_composition")
            if quantity is Quantity.MASS_LOSS_FRACTION
            else ("temperature_K", "time_min", "VF_wt_pct", "composition", "total_pressure_Pa")
        )
        for name in required_conditions:
            assert name in conditions and conditions[name].locator is not None, (quantity, sample, formula, name)
        assert all(condition.locator is not None for condition in conditions.values())
        pressure = conditions["total_pressure_Pa"]
        assert pressure.state.is_value
        assert pressure.state.value.kind is ValueKind.POINT
        assert pressure.state.value.point == Decimal("0.00013332236842105")
        assert pressure.state.value.approximate is True
        assert pressure.locator.page == 3
        if quantity is Quantity.MASS_LOSS_FRACTION:
            assert formula == "unknown"
            assert row.identity.species.phase.value == "l"
            assert conditions["printed_composition"].state.value == {
                "MgO": "24.8",
                "Al2O3": "2.46",
                "SiO2": "33.8",
                "CaO": "2.04",
                "TiO2": "0.11",
                "FeO": "36.7",
            }

    for quantity, expected in expected_by_quantity.items():
        assert actual_by_quantity[quantity] == Counter(expected)
    component_formulas = ("MgO", "Al2O3", "SiO2", "CaO", "FeO")
    trace_formulas = "La Ce Pr Nd Sm Eu Gd Tb Dy Ho Er Tm Yb Lu".split()
    for sample in TABLE1_SAMPLES:
        expected = Counter({formula: 2 for formula in component_formulas})
        expected.update({formula: 1 for formula in trace_formulas})
        assert actual_by_sample[(Quantity.RESIDUE_COMPONENT_COMPOSITION, sample)] == Counter(
            expected
        )
        if sample in TABLE4_SAMPLES:
            assert actual_by_sample[(Quantity.ISOTOPE_DELTA, sample)] == Counter({"O": 2, "Mg": 2, "Si": 2})
        else:
            assert actual_by_sample[(Quantity.ISOTOPE_DELTA, sample)] == Counter()

    rendered = cells_by_label(TABLE1_RENDER)
    expected_residue_points = {}
    for label, raw_cells in rendered.items():
        method, formula = label.split(".", maxsplit=1)
        # The source cells remain transcribed and render-pinned, but the
        # read-only residue selector has no migrated value field for TiO2/WO3.
        if method == "AGG" or formula in {"TiO2", "WO3"}:
            continue
        for sample, raw_cell in zip(TABLE1_SAMPLES, raw_cells, strict=True):
            if Decimal(temperature_c[sample]) + Decimal("273.15") < Decimal("1950"):
                continue
            expected_value, _ = split_value_and_error(raw_cell)
            method_key = "REE" if method == "REE" else method
            expected_residue_points[(method_key, formula, sample)] = Decimal(expected_value)

    expected_isotope_points = {}
    for ratio, raw_cells in cells_by_label(TABLE4_RENDER).items():
        for sample, raw_cell in zip(TABLE4_SAMPLES, raw_cells, strict=True):
            if Decimal(temperature_c[sample]) + Decimal("273.15") < Decimal("1950"):
                continue
            expected_value, _ = split_value_and_error(raw_cell)
            expected_isotope_points[(ratio, sample)] = Decimal(expected_value)

    actual_residue_points = {}
    actual_isotope_points = {}
    actual_mass_loss_points = {}
    in_band_counts: Counter[Quantity] = Counter()
    for row in observations:
        quantity = row.identity.quantity.value
        sample_match = re.search(r"row=(SC\d+)(?!\d)", row.locator.note or "")
        assert sample_match is not None, row.locator.note
        sample = sample_match.group(1)
        in_band = Decimal("1950") <= row.identity.temperature_K.value <= Decimal("2473")
        if not in_band:
            continue
        assert row.value.kind is ValueKind.POINT
        assert row.identity.temperature_K.value in {Decimal("2073.15"), Decimal("2273.15")}
        in_band_counts[quantity] += 1
        if quantity is Quantity.RESIDUE_COMPONENT_COMPOSITION:
            assert row.identity.species.phase.value == "l"
            formula = row.identity.species.formula
            if formula in trace_formulas:
                method_key = "REE"
            elif "electron_microprobe" in row.observation_id:
                method_key = "EM"
            else:
                assert "ion_microprobe" in row.observation_id
                method_key = "IM"
            actual_residue_points[(method_key, formula, sample)] = row.value.point
        elif quantity is Quantity.ISOTOPE_DELTA:
            ratio = re.search(
                r"table4_(.+?)_relative_to_starting_material", row.observation_id
            ).group(1).replace("_", "/")
            actual_isotope_points[(ratio, sample)] = row.value.point
        elif quantity is Quantity.MASS_LOSS_FRACTION:
            actual_mass_loss_points[sample] = row.value.point

    assert actual_residue_points == expected_residue_points
    assert actual_isotope_points == expected_isotope_points
    expected_mass_loss_points = {}
    for line in TABLE2_RENDER.splitlines():
        sample, raw = line.split(" ", maxsplit=1)
        fields = raw.split("|")
        if Decimal(temperature_c[sample]) + Decimal("273.15") >= Decimal("1950"):
            expected_mass_loss_points[sample] = Decimal(fields[4]) / Decimal("100")
    assert actual_mass_loss_points == expected_mass_loss_points
    assert in_band_counts == Counter({
        Quantity.RESIDUE_COMPONENT_COMPOSITION: 336,
        Quantity.ISOTOPE_DELTA: 78,
        Quantity.MASS_LOSS_FRACTION: 14,
    })
