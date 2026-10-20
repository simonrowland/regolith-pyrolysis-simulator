"""Independent 600-dpi table pins for Wang et al. (2001)."""

from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import re

import pytest
import yaml

from simulator.battery.enums import Quantity
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
    table1 = extract["species"]["SiO2"]["observations"]
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
            point = next(point for point in points if point["sample"] == sample)
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
    changed_extract["species"]["SiO2"]["observations"][0]["values"]["series"][0]["MgO_wt_pct"] = "999"
    with pytest.raises(AssertionError):
        assert_table1_grid_matches_render(changed_extract, rendered)


def test_table2_and_table4_rendered_rows_are_bound_to_samples() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    printed_rows = {}
    for line in TABLE2_RENDER.splitlines():
        sample, raw = line.split(" ", maxsplit=1)
        printed_rows[sample] = raw.split("|")

    def assert_mass_loss_grid_matches_render(source: dict, rendered: dict[str, list[str]]) -> None:
        for sample, expected in rendered.items():
            observations = source["species"][sample]["observations"]
            observation = next(
                row for row in observations
                if row["observation_id"] == f"wang_2001_table2_{sample.lower()}_mass_loss"
            )
            assert len(observations) == 1
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

    assert_mass_loss_grid_matches_render(extract, printed_rows)
    changed_render = deepcopy(printed_rows)
    changed_render["SC2"][4] = "999"
    with pytest.raises(AssertionError):
        assert_mass_loss_grid_matches_render(extract, changed_render)
    changed_extract = deepcopy(extract)
    changed_extract["species"]["SC2"]["observations"][0]["values"]["points"][0]["mass_loss_wt_pct"] = "999"
    with pytest.raises(AssertionError):
        assert_mass_loss_grid_matches_render(changed_extract, printed_rows)

    sc1 = extract["species"]["SC1"]["observations"][0]["values"]["points"][0]
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
    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 538

    expected_by_quantity = {
        Quantity.RESIDUE_COMPONENT_COMPOSITION: {
            **{formula: 32 for formula in ("MgO", "Al2O3", "SiO2", "CaO", "TiO2", "FeO")},
            "WO3": 16,
            **{formula: 16 for formula in ("La", "Ce", "Pr", "Nd", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu")},
        },
        Quantity.ISOTOPE_DELTA: {"O": 30, "Mg": 30, "Si": 30},
        Quantity.MASS_LOSS_FRACTION: {f"SC{index}": 1 for index in range(1, 17)},
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
        if quantity is Quantity.MASS_LOSS_FRACTION:
            assert formula == sample
            assert row.identity.species.phase.value == "l"
            assert conditions["printed_composition"].state.value.startswith(
                "Table 1 starting comp. b (actual composition calculated from reagent amounts weighed):"
            )
            assert conditions["printed_composition"].locator.page == 4

    for quantity, expected in expected_by_quantity.items():
        assert actual_by_quantity[quantity] == Counter(expected)
    component_formulas = ("MgO", "Al2O3", "SiO2", "CaO", "TiO2", "FeO")
    trace_formulas = "La Ce Pr Nd Sm Eu Gd Tb Dy Ho Er Tm Yb Lu".split()
    for sample in TABLE1_SAMPLES:
        expected = Counter({formula: 2 for formula in component_formulas})
        expected["WO3"] = 1
        expected.update({formula: 1 for formula in trace_formulas})
        expected.update({formula: 2 for formula in ("O", "Mg", "Si")})
        assert actual_by_sample[(Quantity.RESIDUE_COMPONENT_COMPOSITION, sample)] == Counter(
            {formula: amount for formula, amount in expected.items() if formula not in {"O", "Mg", "Si"}}
        )
        if sample in TABLE4_SAMPLES:
            assert actual_by_sample[(Quantity.ISOTOPE_DELTA, sample)] == Counter({"O": 2, "Mg": 2, "Si": 2})
        else:
            assert actual_by_sample[(Quantity.ISOTOPE_DELTA, sample)] == Counter()
