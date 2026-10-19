from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import os
from pathlib import Path
import subprocess

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "borisov-behrens-holtz-2013-ti-p-fe-redox"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
OXIDES = ("SiO2", "TiO2", "Al2O3", "Fe2O3", "MgO", "CaO", "P2O5")

# Independently transcribed from the 600 dpi render of physical PDF page 5.
# Temperature and duration ditto marks are expanded to the preceding printed
# condition so each sample row has an explicit binding in this test grid.
RENDER_TABLE2 = """
1|DAF-5|1502|24|45.49|0.02|14.08|9.75|9.11|21.22|–|99.67|1.871
2|DAFT10-5|1502|24|40.96|9.10|12.54|9.35|8.08|18.88|–|98.91|1.75
3|DAFT20-5|1502|24|35.12|19.46|10.73|9.59|7|16.26|–|98.16|1.67
4|DAFT30-5|1502|24|30.47|28.14|9.42|9.6|6.11|14.29|–|98.04|1.60
5|DAFS-5|1502|24|66.25|0.01|7.57|9.99|4.82|11.41|–|100.05|1.42
6|DAFST15-5|1502|24|55.93|14.23|6.22|9.33|4.01|9.48|–|99.2|1.28
7|DAFST25-5|1502|24|48.33|24.61|5.39|9.67|3.44|8.3|–|99.74|1.20
8|DAF-6|1503|14.5|45.45|0.03|14.1|9.67|9.11|21.35|–|99.7|1.90
9|DAFS-6|1503|14.5|66.8|0.01|7.61|9.66|4.82|11.34|–|100.24|1.41
10|DAF-7|1503|8|45.41|0.01|14.03|9.64|9.06|21.36|–|99.5|1.93
11|DAFS-7|1503|8|67.01|0|7.48|9.45|4.84|11.22|–|100.01|1.40
12|DAF-9|1501|2|45.53|0.02|14.19|9.89|9.12|21.4|–|100.15|1.87
13|DAFS-9|1501|2|64.35|0|7.82|10.84|5.17|11.85|–|100.23|1.41
14|DAF-10|1451|70|45.58|0.02|14.23|9.59|9.08|21.56|–|100.06|2.551
15|DAFT10-10|1451|70|40.54|9.62|12.49|9.47|8.12|18.84|–|99.08|2.43
16|DAFT20-10|1451|70|35.05|19.51|10.83|9.47|6.99|16.46|–|98.3|2.27
17|DAFT30-10|1451|70|30.44|28.47|9.39|9.5|6.14|14.42|–|98.36|2.12
18|DAFS-10a|1451|70|65.82|0.01|7.72|9.58|4.93|11.57|–|99.64|1.87
19|DAFST15-10a|1451|70|54.94|14.55|6.38|9.41|4.12|9.61|–|99|1.73
20|DAFST25-10a|1451|70|46.33|25.56|5.64|9.88|3.58|8.67|–|99.89|1.62
21|DAF-13|1400|72|45.64|0.02|14.14|9.56|9.13|21.45|–|99.94|3.17
22|DAFT10-13|1400|72|40.9|9.18|12.56|9.34|8.06|18.89|–|98.92|2.99
23|DAFT20-13|1400|72|34.96|19.54|10.71|9.48|6.96|16.36|–|98|2.73
24|DAFT30-13|1400|72|30.31|28.62|9.39|9.63|6.06|14.28|–|98.3|2.55
25|DAF-30|1451|9.0|45.52|–|14.13|9.81|9.54|21.33|0.58|100.92|2.31
26|DAFP05-30|1451|9.0|43.11|–|13.40|9.75|9.08|20.16|5.45|100.94|1.86
27|DAFP10-30|1451|9.0|40.81|–|12.65|9.71|8.48|18.96|10.50|101.11|1.47
28|DAFP15-30|1451|9.0|38.43|–|12.00|9.80|7.97|17.80|15.23|101.23|1.18
29|DAFP20-30|1451|9.0|36.81|–|11.40|9.72|7.41|16.66|19.33|101.33|0.98
30|DAF-28|1501|2.0|45.78|–|14.25|9.73|9.57|21.45|0.31|101.09|1.92
31|DAFP05-28|1501|2.0|43.47|–|13.48|9.75|9.03|20.22|5.38|101.32|1.55
32|DAFP10-28|1501|2.0|40.81|–|12.68|9.75|8.52|18.94|10.57|101.27|1.23
33|DAFP15-28|1501|2.0|38.46|–|12.03|9.70|7.96|17.71|15.61|101.46|0.99
34|DAFP20-28|1501|2.0|35.92|–|11.22|9.64|7.44|16.63|20.54|101.39|0.82
35|DAFSP08-39|1501|2.0|50.81|–|10.12|9.77|6.75|15.06|8.00|100.51|1.13
36|DAFSP08-40|1502|4.0|50.88|–|10.18|9.85|6.79|15.16|7.72|100.58|1.15
37|DAFSP08-42|1502|8.0|51.44|–|10.28|9.73|6.84|15.38|7.15|100.82|1.17
38|DAF-33|1552|1.08|45.69|–|14.27|9.57|9.54|21.47|0.34|100.89|1.59
39|DAFP05-33|1552|1.08|43.31|–|13.49|9.72|9.06|20.23|5.31|101.11|1.29
40|DAFP10-33|1552|1.08|40.68|–|12.70|9.68|8.52|18.97|10.49|101.04|1.02
41|DAFP15-33|1552|1.08|38.35|–|12.02|9.74|7.93|17.69|15.51|101.24|0.83
42|DAFP20-33|1552|1.08|36.55|–|11.43|9.71|7.30|16.41|19.94|101.35|0.69
43|DAFSP08-41|1552|1.5|51.10|–|10.10|9.82|6.75|15.08|7.89|100.75|0.99
"""

RENDER_TABLE1 = (
    ("DAFP10 air (present study)", "2", "2", "1500", "10.2", "0.1", "10.8"),
    ("DAFP10 air (present study)", "7", "7", "1500", "9.8", "0.1", "15.2"),
    ("DAFP10 air (present study)", "17.2", "17.2", "1500", "8.8", "0.1", "13.9"),
    ("DAFP10 air (present study)", "2", "2", "1450", "10.4", "0.1", "12.2"),
    ("DAFP10 air (present study)", "7", "7", "1450", "10.1", "0.1", "13.6"),
    ("DAFP10 air (present study)", "15.5", "15.5", "1450", "9.5", "0.1", "13.1"),
    ("DAFP10 air (present study)", "66", "66", "1450", "7.8", "0.1", "15.0"),
    ("DAP IW (Borisov, unpublished data)", "3", "3", "1450", "10.9", "0.3", "15.2"),
    ("DAP IW (Borisov, unpublished data)", "8", "8", "1450", "9.1", "0.2", "12.6"),
    ("DAP IW (Borisov, unpublished data)", "15", "15", "1450", "7.3", "0.2", "12.6"),
    ("DAP IW (Borisov, unpublished data)", "24", "24", "1350", "10.6", "0.2", "18.1"),
    ("DAP IW (Borisov, unpublished data)", "53", "53", "1350", "8.6", "0.3", "16.2"),
    ("DAP IW (Borisov, unpublished data)", "71", "71", "1350", "8.1", "0.1", "17.2"),
)


def _render_grid() -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(line.split("|"))
        for line in RENDER_TABLE2.strip().splitlines()
    )


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _migrate():
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator.result


def _assert_table2_grid(document: dict, expected: tuple[tuple[str, ...], ...]) -> None:
    source_rows = {
        item["values"]["row_as_printed"]: item
        for item in document["species"]["Fe"]["observations"]
        if item["observation_id"].startswith("borisov_2013_table2_fe3_fe2_row_")
    }
    assert len(expected) == 43
    assert len(source_rows) == 43
    for row in expected:
        (row_number, sample, temp, duration, *cells) = row
        obs = source_rows[row_number]
        values = obs["values"]
        assert values["sample_as_published"] == sample
        assert values["T_C"] == temp
        assert values["duration_h_as_printed"] == duration
        assert values["Total_wt_pct_as_published"] == cells[-2]
        assert values["value"] == cells[-1]
        printed_composition = {
            **values["composition_wt_pct_as_published"],
            **values["unreported_components_as_published"],
        }
        assert printed_composition == dict(zip(OXIDES, cells[:7], strict=True))


def test_table2_independent_render_grid_and_mutations() -> None:
    document = _document()
    expected = _render_grid()
    _assert_table2_grid(document, expected)

    grid_mutation = [list(row) for row in expected]
    grid_mutation[0][4] = "45.48"
    with pytest.raises(AssertionError):
        _assert_table2_grid(document, tuple(tuple(row) for row in grid_mutation))

    extract_mutation = deepcopy(document)
    first_row = next(
        item for item in extract_mutation["species"]["Fe"]["observations"]
        if item["observation_id"].endswith("_row_01_daf_5")
    )
    first_row["values"]["composition_wt_pct_as_published"]["SiO2"] = "45.48"
    with pytest.raises(AssertionError):
        _assert_table2_grid(extract_mutation, expected)

    dap = next(
        item for item in document["species"]["P2O5"]["context"]
        if item["observation_id"] == "borisov_2013_table2_dap_initial_composition_context"
    )
    assert dap["values"]["composition_as_published"] == {
        "SiO2": "38.43", "TiO2": "–", "Al2O3": "12.58", "Fe2O3": "–",
        "MgO": "7.59", "CaO": "28.74", "P2O5": "12.93", "Total": "100.26",
    }
    assert dap["values"]["Fe3_over_Fe2"] == "not reported"


def test_table1_post_run_p2o5_and_sample_weights_match_render() -> None:
    document = _document()
    observations = document["species"]["P2O5"]["observations"]
    actual = {
        (item["values"]["sample_as_published"], item["locator"]["row"], item["values"]["T_C"]): item
        for item in observations
    }
    sample_weights = {
        (item["values"]["sample_as_printed"], item["values"]["duration_h_as_printed"], item["values"]["T_C_as_printed"]): item["values"]["sample_weight_mg_as_printed"]
        for item in document["species"]["P2O5"]["context"]
        if item["observation_id"].endswith("_sample_weight_context")
    }
    assert len(observations) == len(RENDER_TABLE1) == len(sample_weights)
    for sample, row, duration, temp, p2o5, sd, weight in RENDER_TABLE1:
        item = actual[(sample, row, temp)]
        values = item["values"]
        assert values["duration_h_as_published"] == duration
        assert values["P2O5_wt_pct"] == p2o5
        assert values["standard_deviation_wt_pct_as_printed"] == sd
        assert sample_weights[(sample, duration, temp)] == weight
        if sample.startswith("DAP"):
            assert values["method_class"] == "quoted_attributed"
            assert values["attribution"] == "Borisov, unpublished data"
            assert item["point_conditions"]["fO2_control"]["state"]["value"] == "approximately IW"
            assert item["point_conditions"]["total_pressure_Pa"]["state"]["tag"] == "unknown"
        else:
            assert values["method_class"] == "measured_direct"
            assert item["point_conditions"]["total_pressure_Pa"]["state"]["value"] == "101325"


def test_migration_types_every_quantity_and_keeps_conditions_and_quench() -> None:
    document = _document()
    result = _migrate()
    records = list(result.observations.values())
    assert len(records) == 62
    assert not result.registry_issues
    assert len(result.queue) == 6
    assert all(
        item.axes == ["method"] and item.why == "source does not state method"
        for item in result.queue
    )

    source_formula = {
        item["observation_id"]: formula
        for formula, body in document["species"].items()
        for item in body.get("observations", [])
    }
    assert all(
        record.identity.species.formula == source_formula[
            record.observation_id.split("::", 1)[1]
        ]
        for record in records
    )

    iron_rows = [r for r in records if "table2_fe3_fe2_row_" in r.observation_id]
    assert len(iron_rows) == 43
    assert all(r.identity.quantity.value is Quantity.VALENCE_RATIO for r in iron_rows)
    assert all(r.identity.species.formula == "Fe" for r in iron_rows)
    assert all(r.value.kind is ValueKind.POINT for r in iron_rows)
    assert all(r.identity.species.phase.value.value == Phase.L for r in iron_rows)
    for record in iron_rows:
        conditions = record.point_conditions
        assert conditions is not None
        for key in ("T_C", "duration_h", "atmosphere", "fO2_log", "total_pressure_Pa", "composition"):
            assert key in conditions and conditions[key].locator is not None
        assert conditions["T_C"].state.value + Decimal("273.15") == record.identity.temperature_K.value
        assert conditions["fO2_log"].state.is_unknown
        assert conditions["total_pressure_Pa"].state.value == Decimal("101325")
        assert conditions["composition"].state.is_unknown
        assert conditions["quench_state"].state.value.value == "not_stated"
        assert record.quench_state.value == "not_stated"

    phosphorus = [r for r in records if r.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION]
    assert len(phosphorus) == 13
    assert all(r.identity.species.formula == "P2O5" for r in phosphorus)
    assert all(r.point_conditions["T_C"].locator is not None for r in phosphorus)
    assert all(r.identity.temperature_K.is_value for r in phosphorus)

    for record in records:
        conditions = record.point_conditions
        assert conditions is not None
        assert all(item.locator is not None for item in conditions.values())
        assert "T_C" in conditions or "temperature_K" in conditions
        assert "fO2_log" in conditions
        assert "composition" in conditions


def test_figure_records_are_deferred_and_keep_printed_oxygen_condition() -> None:
    document = _document()
    figures = [
        row
        for body in document["species"].values()
        for row in body.get("context", [])
        if row.get("type") == "figure_record"
    ]
    assert len(figures) == 11
    for figure in figures:
        values = figure["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["identity"]["species"]
        assert values["identity"]["phase"]
        assert values["axes_as_printed"]
        assert values["caption_as_printed"]
        assert "series" in values
        assert all("marker_count" in series for series in values["series"])
        assert "points" not in values
        assert figure["locator"]["page"] == figure["locator"]["pdf_page_index"] + 1

    figure6 = next(row for row in figures if row["observation_id"] == "borisov_2013_figure_6_deferred")
    oxygen = figure6["values"]["point_conditions"]["oxygen_partial_pressure"]
    assert oxygen["state"]["value"] == "1e-10"
    assert oxygen["units"] == "atm"
    assert oxygen["locator"]["page"] == 8
    assert figure6["values"]["identity"]["temperature_as_printed"] == "1400 °C"

    figure1 = next(row for row in figures if row["observation_id"] == "borisov_2013_figure_1_deferred")
    series = figure1["values"]["series"]
    assert series[0]["point_conditions"]["atmosphere"]["state"]["value"] == "air"
    assert series[2]["point_conditions"]["fO2_control"]["state"]["value"] == "IW"
    assert series[2]["point_conditions"]["fO2_log"]["state"]["tag"] == "unknown"


def test_all_extract_locators_use_physical_pdf_pages() -> None:
    document = _document()
    locators: list[dict] = []

    def collect(value: object) -> None:
        if isinstance(value, dict):
            if "page" in value and "pdf_page_index" in value:
                locators.append(value)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(document)
    assert locators
    assert all(item["page"] == item["pdf_page_index"] + 1 for item in locators)


def test_cited_table_pages_are_the_physical_pdf_pages() -> None:
    corpus = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))
    pdf = corpus / "raw" / SOURCE_ID / f"{SOURCE_ID}.pdf"
    if not pdf.is_file():
        pytest.skip(f"corpus PDF absent: {pdf}")

    pages: dict[int, str] = {}
    for page in range(2, 14):
        completed = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        pages[page] = completed.stdout
    assert "0.07 ± 0.03" in pages[2]
    assert "0.16 ± 0.01" in pages[2]
    assert "one-atmosphere" in pages[3].lower()
    assert "withdraw" in pages[3].lower()
    assert "Table 1" in pages[4]
    assert "P2O5" in pages[4]
    assert "1.47 ± 0.08" in pages[4]
    assert "1.60 ± 0.11" in pages[4]
    assert "Table 2" in pages[5]
    assert "ferric/ferrous ratios" in pages[5]
    for figure, page in ((1, 6), (2, 6), (3, 7), (4, 7), (5, 8), (6, 8), (7, 10), (8, 10), (9, 11), (10, 12), (11, 13)):
        assert f"Fig. {figure}" in pages[page]
    for equation, page in ((6, 6), (10, 7), (11, 9), (12, 12)):
        assert f"Eq. ({equation})" in pages[page]
    assert "constant fO2 = 10-10 atm" in pages[8]
