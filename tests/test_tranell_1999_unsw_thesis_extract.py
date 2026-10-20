"""Render-pinned Table 4.2 data and isolated migration for Tranell's thesis."""

from copy import deepcopy
from decimal import Decimal
import os
from pathlib import Path
import subprocess

import pytest
import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/tranell-1999-unsw-thesis.yaml"
TABLE42_RENDER = """
Sample | Mole fraction Si, XSi (ppm) | gamma Si
1 | 1051 | 0.0079
2 | 1602 | 0.0148
""".strip()
APPENDIX_RENDER_GRID = """
table|sample|time|x_tiox|T_K|log10_pO2_atm|percent_first|percent_repeat|ratio_first|ratio_repeat
A2a|CST5a||0.1071|1843|-7|1.13|-|0.0114|-
A2a|CST6||0.1075|1843|-7|1.64|-|0.0167|-
A2a|CST7a||0.1111|1843|-7|3.16|3.35|0.0326|0.0357
A2a|CST8||0.1128|1843|-7|3.73|-|0.0387|-
A2b|CST1||0.0542|1843|-8|2.14|2.35|0.0219|0.0241
A2b|CST2||0.0561|1843|-8|2.72|2.89|0.0280|0.0298
A2b|CST3||0.0581|1843|-8|4.47|-|0.0468|-
A2b|CST4a||0.0553|1843|-8|6.54|6.3|0.0700|0.0672
A2b|CST5a||0.1071|1843|-8|1.73|-|0.0176|-
A2b|CST6||0.1075|1843|-8|2.63|2.42|0.0270|0.0248
A2b|CST7a||0.1111|1843|-8|4.72|4.8|0.0495|0.0504
A2b|CST8||0.1128|1843|-8|5.78|-|0.0613|-
A2b|CST9||0.1603|1843|-8|2.34|2.24|0.0240|0.0229
A2b|CST10||0.1704|1843|-8|2.81|-|0.0289|-
A2b|CST11||0.1722|1843|-8|4.28|-|0.0447|-
A2b|CST12||0.1691|1843|-8|5.63|-|0.0597|-
A2c|CST5a||0.1071|1783|-8|1.08|1.25|0.01092|0.0127
A2c|CST7a||0.1111|1783|-8|3.32|3.20|0.0343|0.0331
A2d|CST13||0.4336|1873|-8|2.91|-|0.0300|-
A2d|CST14||0.4320|1873|-8|4.82|-|0.0506|-
A2e|CST5a||0.1071|1903|-8|1.96|-|0.0100|-
A2e|CST7a||0.1111|1903|-8|6.35|-|0.0678|-
A2f|CST1||0.0542|1843|-9|2.94|2.85|0.0303|0.0293
A2f|CST2||0.0561|1843|-9|5.28|5.46|0.0557|0.0578
A2f|CST3||0.0581|1843|-9|5.97|5.88|0.0635|0.0625
A2f|CST4a||0.0553|1843|-9|9.03|9.11|0.0993|0.1002
A2f|CST5a||0.1071|1843|-9|2.68|-|0.0275|-
A2f|CST6||0.1075|1843|-9|3.71|-|0.0385|-
A2f|CST7b||0.1117|1843|-9|7.10|-|0.0764|-
A2f|CST8||0.1128|1843|-9|8.13|-|0.0885|-
A2f|CST9||0.1603|1843|-9|2.85|-|0.0293|-
A2f|CST10||0.1704|1843|-9|5.14|-|0.0542|-
A2f|CST11||0.1722|1843|-9|7.03|-|0.0756|-
A2f|CST12||0.1691|1843|-9|8.11|-|0.0883|-
A2f|CST13||0.4336|1843|-9|4.27|-|0.0446|-
A2f|CST14||0.4320|1843|-9|7.48|-|0.0808|-
A2g|CST13||0.4336|1873|-9|5.59|-|0.0592|-
A2g|CST14||0.4320|1873|-9|8.79|-|0.0897|-
A2h|CST13||0.4336|1903|-9|6.04|-|0.0643|
A2h|CST14||0.4320|1903|-9|10.92|-|0.1226|
A2i|CST5a||0.1071|1843|-10|4.47|4.58|0.046792|0.0480
A2i|CST6||0.1075|1843|-10|6.48|-|0.06929|-
A2i|CST7b||0.1117|1843|-10|10.95|-|0.122965|-
A2i|CST8||0.1128|1843|-10|13.96|-|0.16225|-
A2j|CST1||0.0542|1873|-12|16.97|15.01|0.2044|0.1766
A2j|CST2||0.0561|1873|-12|23.27|19.47|0.3033|0.2418
A2j|CST3||0.0581|1873|-12|31.28|-|0.4552|-
A2j|CST4b||0.0552|1873|-12|38.73|-|0.63212|-
A2j|CST5b||0.1107|1873|-12|15.26|12.26|0.18008|0.1397
A2j|CST6||0.1075|1873|-12|17.91|18.06|0.218175|0.2217
A2j|CST7b||0.1117|1873|-12|28.57|-|0.399972|-
A2j|CST8||0.1128|1873|-12|31.93|-|0.469076|-
A2j|CST9||0.1603|1873|-12|15.93|-|0.189485|-
A2j|CST10||0.1704|1873|-12|19.46|19.54|0.241619|0.2428
A2j|CST11||0.1722|1873|-12|29.21|-|0.412629|-
A2j|CST12||0.1691|1873|-12|34.04|-|0.51607|-
A2j|CST13||0.4336|1873|-12|23.25|-|0.302932|-
A2j|CST14||0.4320|1873|-12|31.13|-|0.452011|-
A3a|CST12|2h||1843|-9|8.01||0.087|
A3a|CST12|4h||1843|-9|8.15||0.089|
A3a|CST12|6h||1843|-9|8.11||0.088|
A3a|CST12|11h||1843|-9|8.09||0.088|
A3b|CST12|6||1873|-12|34.9||0.536|
A3b|CST12|8||1873|-12|34.04||0.516|
A3b|CST12|24||1873|-12|33.92||0.513|
A4a|CSTM1||0.1069|1843|-9|6.14|6.29|0.0654|0.0671
A4a|CSTM2||0.1033|1843|-9|5.04|5.18|0.0531|0.0546
A4a|CSTM3||0.1199|1843|-9|8.12|-|0.0884|-
A4b|CSTM1||0.1069|1873|-12|25.31|-|0.3389|-
A4b|CSTM2||0.1033|1873|-12|19.61|-|0.2439|-
A5a|CSTA1||0.1117|1843|-9|7.17|7.05|0.0772|0.0758
A5a|CSTA2||0.1137|1843|-9|7.85|7.99|0.0852|0.0868
A5a|CSTA3||0.1034|1843|-9|3.01|-|0.0310|-
A5b|CSTA1||0.1117|1873|-12|25.05|-|0.3342|-
A5b|CSTA2||0.1137|1873|-12|26.78|-|0.3657|-
""".strip()
A1_RENDER_GRID = """
sample|SiO2|CaO|TiO2|Al2O3|Fe2O3|MgO|Na2O|K2O|MnO|SrO|LOI
CST1|40.5|51.6|7.17|0.05|0.19|<0.01|<0.01|0.01|<0.01|<0.01|0.62
CST2|45.8|46.4|7.52|0.02|0.17|<0.01|<0.01|<0.01|<0.01|<0.01|0.22
CST3|52.1|40|7.78|0.01|0.17|<0.01|<0.01|<0.01|<0.01|<0.01|0.09
CST4|60.3|31.8|7.33|0.22|0.19|0.27|<0.01|0.02|0.01|<0.01|0.05
CST5a|38.9|46|14.6|0.14|0.22|0.39|<0.01|<0.01|<0.01|<0.01|0.04
CST5b|38.3|47.4|14.4|<0.01|0.26|<0.01|<0.01|<0.01|<0.01|<0.01|-0.05
CST6|42.6|43.1|14.2|<0.01|0.15|<0.01|<0.01|<0.01|<0.01|<0.01|0.22
CST7a|50.7|34.2|14.6|0.18|0.2|0.28|<0.01|0.02|<0.01|0.01|0.05
CST7b|48.3|36.9|14.6|<0.01|0.14|<0.01|<0.01|<0.01|<0.01|<0.01|-0.12
CST8|54.7|30.7|14.8|0.03|0.11|0.01|<0.01|<0.01|<0.01|<0.01|-0.17
CST9|35.6|43.6|20.8|<0.01|0.14|<0.01|<0.01|<0.01|<0.01|<0.01|0.14
CST10|38.8|39.2|22|0.08|0.13|<0.01|<0.01|<0.01|<0.01|<0.01|0.02
CST11|44.4|33.5|22.2|<0.01|0.13|<0.01|<0.01|<0.01|<0.01|<0.01|<0.01
CST12|50.2|28|21.7|0.03|0.13|<0.01|<0.01|<0.01|<0.01|<0.01|0.06
CST13|20.9|28.4|52.3|0.15|0.08|0.27|0.24|0.03|<0.01|<0.01|0.15
CST14|24.4|24.4|51.1|0.15|<0.01|0.25|<0.01|<0.01|0.01|0.01|0.25
CSTM1|47.70|33.40|14.50|0.23|<0.01|5.10|<0.01|<0.01|<0.01|0.01|0.20
CSTM2|42.20|29.50|14.50|0.10|<0.01|14.00|<0.01|<0.01|<0.01|0.01|0.19
CSTM3|51.30|17.80|14.50|0.33|<0.01|16.25|<0.01|<0.01|<0.01|0.01|0.17
CSTA1|47.60|33.10|14.40|5.20|0.11|0.15|<0.01|<0.01|<0.01|0.01|0.15
CSTA2|43.20|29.00|14.10|14.30|0.09|0.29|<0.01|<0.01|<0.01|0.01|0.12
CSTA3|24.80|34.60|14.00|26.14|<0.01|0.39|0.08|<0.01|<0.01|0.01|0.18
""".strip()
RENDER_CONFIRMED_TEXT_LAYER_LABELS = {
    "CST10": "CSTl0",
    "CST11": "CSTl1",
    "CSTM1": "CSTMl",
}


def document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def source_label_candidates(sample: str) -> set[str]:
    """Permit only label OCR confusions resolved against the 600 dpi renders."""
    return {sample, RENDER_CONFIRMED_TEXT_LAYER_LABELS.get(sample, sample)}


def table42_rows(extract: dict) -> list[list[str]]:
    observations = [
        row
        for row in extract["species"]["Si"]["observations"]
        if row["type"] == "activity_coefficient"
    ]
    return [
        [
            row["values"]["sample_as_printed"],
            row["values"]["silicon_mole_fraction_ppm_as_printed"],
            row["values"]["gamma"],
        ]
        for row in observations
    ]


def assert_table42_matches_render(extract: dict, rendered: str) -> None:
    expected_rows = [line.split(" | ") for line in rendered.splitlines()][1:]
    assert table42_rows(extract) == expected_rows


def appendix_grid_rows(extract: dict) -> dict[tuple[str, str, str], tuple[str, ...]]:
    rows: dict[tuple[str, str, str], dict] = {}

    def row_for(table: str, sample: str, time: str, *, x: str, temperature: str, oxygen: str):
        key = table, sample, time
        row = rows.setdefault(
            key,
            {
                "x": x or "",
                "temperature": temperature,
                "oxygen": oxygen.removesuffix(" atm").removeprefix("10^").lstrip("^")[-3:],
                "percent": {"first": "", "repeat": ""},
                "ratio": {"first": "", "repeat": ""},
            },
        )
        assert row["x"] == (x or "")
        assert row["temperature"] == temperature
        assert row["oxygen"] == oxygen.removesuffix(" atm").removeprefix("10^").lstrip("^")[-3:]
        return row

    def put(table: str, sample: str, time: str, quantity: str, slot: str, value: str, *, x: str, temperature: str, oxygen: str) -> None:
        row = row_for(table, sample, time, x=x, temperature=temperature, oxygen=oxygen)
        row[quantity][slot] = value

    for observation in extract["species"]["Ti"]["observations"]:
        quantity_name = observation["values"].get("quantity")
        quantity = "percent" if quantity_name == "Ti3plus_over_total_Ti" else "ratio"
        for point in observation["values"].get("points", []):
            time = str(point.get("time_as_printed", ""))
            label = str(point.get("label", ""))
            slot = "first" if time else label.lower()
            condition = point["point_conditions"]["oxygen_partial_pressure"]["state"]["value"]
            oxygen = str(condition)
            put(
                str(point["appendix_table"]),
                str(point["sample"]),
                time,
                quantity,
                slot,
                str(point["value_as_printed"]),
                x=str(point.get("TiOx_mole_fraction_as_printed", "")),
                temperature=str(point["T_K"]),
                oxygen=oxygen,
            )

    for context in extract["species"]["Ti"].get("context", []):
        values = context.get("values", {})
        context_type = context.get("type")
        if context_type in {"replicate_evidence", "duplicate_table_record"}:
            table = str(values["source_table"])
            sample = str(values["sample_as_printed"])
            time = str(values.get("time_as_printed", ""))
            oxygen = str(values["oxygen_partial_pressure_as_printed"])
            for name, quantity in (("percent_readings", "percent"), ("ratio_readings", "ratio")):
                readings = values[name]
                for slot in ("first", "repeat"):
                    reading = readings.get(slot)
                    if reading is None:
                        continue
                    put(
                        table,
                        sample,
                        time,
                        quantity,
                        slot,
                        str(reading["as_printed"]),
                        x=str(values.get("TiOx_mole_fraction_as_printed") or ""),
                        temperature=str(values["temperature_K_as_printed"]),
                        oxygen=oxygen,
                    )
        elif context_type == "unavailable_measurement_cell":
            table = str(values["source_table"])
            sample = str(values["sample_as_printed"])
            time = str(values.get("time_as_printed", ""))
            quantity = "percent" if values["quantity"] == "Ti3plus_over_total_Ti" else "ratio"
            slot = "first" if "kinetic" in str(values.get("reading", "")) else str(values["reading"]).lower()
            row = next(
                row for key, row in rows.items() if key[:2] == (table, sample) and (not time or key[2] == time)
            )
            row[quantity][slot] = str(values["as_printed"])

    return {
        key: (
            row["x"],
            row["temperature"],
            row["oxygen"],
            row["percent"]["first"],
            row["percent"]["repeat"],
            row["ratio"]["first"],
            row["ratio"]["repeat"],
        )
        for key, row in rows.items()
    }


def assert_appendix_grid_matches_render(extract: dict, rendered: str) -> None:
    lines = [line.split("|") for line in rendered.splitlines()]
    expected = {}
    for table, sample, time, *cells in lines[1:]:
        key = table, sample, time
        assert key not in expected
        expected[key] = tuple(cells)
    assert len(expected) == 75
    assert appendix_grid_rows(extract) == expected


def a1_grid_rows(rendered: str) -> dict[str, tuple[str, ...]]:
    lines = [line.split("|") for line in rendered.splitlines()]
    return {row[0].strip(): tuple(cell.strip() for cell in row[1:]) for row in lines[1:]}


def assert_a1_grid_matches_render(extract: dict, rendered: str) -> None:
    context = next(
        entry
        for entry in extract["species"]["Ti"]["context"]
        if entry["observation_id"] == "tranell_1999_appendix_a1_master_slag_assays_context"
    )
    assert len(a1_grid_rows(rendered)) == 22
    assert a1_grid_rows(context["values"]["rows_as_printed"]) == a1_grid_rows(rendered)


def test_table42_grid_is_bound_to_render_and_both_mutations_fail() -> None:
    extract = document()
    assert_table42_matches_render(extract, TABLE42_RENDER)

    changed_render = TABLE42_RENDER.replace("1051", "9999", 1)
    with pytest.raises(AssertionError):
        assert_table42_matches_render(extract, changed_render)

    changed_extract = deepcopy(extract)
    changed_extract["species"]["Si"]["observations"][2]["values"]["gamma"] = "0.9999"
    with pytest.raises(AssertionError):
        assert_table42_matches_render(changed_extract, TABLE42_RENDER)


def test_appendix_a2_a5_grids_bind_all_cells_and_both_mutations_fail() -> None:
    extract = document()
    assert_appendix_grid_matches_render(extract, APPENDIX_RENDER_GRID)

    changed_render = APPENDIX_RENDER_GRID.replace("A2a|CST5a||0.1071|1843|-7|1.13|-", "A2a|CST5a||0.1071|1843|-7|9.99|-", 1)
    with pytest.raises(AssertionError):
        assert_appendix_grid_matches_render(extract, changed_render)

    changed_extract = deepcopy(extract)
    point = next(
        point
        for observation in changed_extract["species"]["Ti"]["observations"]
        for point in observation["values"].get("points", [])
        if point.get("appendix_table") == "A2a"
        and point.get("sample") == "CST5a"
        and point.get("label") == "First"
        and observation["values"]["quantity"] == "Ti3plus_over_total_Ti"
    )
    point["value"] = point["value_as_printed"] = "9.99"
    with pytest.raises(AssertionError):
        assert_appendix_grid_matches_render(changed_extract, APPENDIX_RENDER_GRID)


def test_a1_full_assay_grid_and_sample_composition_binding() -> None:
    extract = document()
    assert_a1_grid_matches_render(extract, A1_RENDER_GRID)

    changed_render = A1_RENDER_GRID.replace("CST1|40.5|51.6", "CST1|49.5|51.6", 1)
    with pytest.raises(AssertionError):
        assert_a1_grid_matches_render(extract, changed_render)

    changed_extract = deepcopy(extract)
    context = next(
        entry
        for entry in changed_extract["species"]["Ti"]["context"]
        if entry["observation_id"] == "tranell_1999_appendix_a1_master_slag_assays_context"
    )
    context["values"]["rows_as_printed"] = context["values"]["rows_as_printed"].replace(
        "CST1 | 40.5 | 51.6", "CST1 | 49.5 | 51.6", 1
    )
    with pytest.raises(AssertionError):
        assert_a1_grid_matches_render(changed_extract, A1_RENDER_GRID)

    grid = a1_grid_rows(A1_RENDER_GRID)
    oxide_names = ("SiO2", "CaO", "TiO2", "Al2O3", "Fe2O3", "MgO", "Na2O", "K2O", "MnO", "SrO", "LOI")
    for observation in extract["species"]["Ti"]["observations"]:
        for point in observation["values"]["points"]:
            sample = point["sample"]
            raw_composition = point["point_conditions"]["master_slag_composition_as_printed"]["state"]
            composition = point["point_conditions"]["composition"]["state"]
            assert composition["tag"] == "unknown"
            if sample in grid:
                expected = "; ".join(
                    f"{oxide} {value} wt%" for oxide, value in zip(oxide_names, grid[sample], strict=True)
                )
                assert raw_composition == {"tag": "value", "value": expected}
            else:
                assert raw_composition["tag"] == "unknown"
                assert sample in raw_composition["reason"]


def test_thesis_replicates_link_to_published_tranell_2002_observations() -> None:
    extract = document()
    published = yaml.safe_load(
        (ROOT / "data/literature/extracts/tranell-2002-titanium-valence.yaml").read_text(
            encoding="utf-8"
        )
    )
    published_ids = {
        observation["observation_id"]
        for observation in published["species"]["Ti"]["observations"]
    }
    duplicates = [
        row
        for row in extract["species"]["Ti"]["context"]
        if row["type"] in {"replicate_evidence", "duplicate_table_record"}
    ]

    assert len(duplicates) == 43
    assert sum(row["type"] == "replicate_evidence" for row in duplicates) == 41
    for row in duplicates:
        values = row["values"]
        assert values["duplicate_source_id"] == "tranell-2002-titanium-valence"
        assert values["duplicate_observation_id"] in published_ids
        if row["type"] == "replicate_evidence":
            assert set(values["percent_readings"]) == {"unit", "first", "repeat", "spread"}
            assert set(values["ratio_readings"]) == {"unit", "first", "repeat", "spread"}
        else:
            assert values["percent_readings"]["spread"] == "not applicable; one kinetic reading is printed"
            assert values["ratio_readings"]["spread"] == "not applicable; one kinetic reading is printed"


def test_all_deferred_figures_have_identity_series_and_marker_inventory() -> None:
    extract = document()
    figures = [
        row
        for species in extract["species"].values()
        for row in species.get("context", [])
        if row["type"] == "figure_record"
    ]
    expected_ids = {
        "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9",
        "2.10", "2.11", "2.12", "2.13", "2.14", "2.15", "2.16", "2.17",
        "2.18", "2.19", "2.20", "2.21a", "2.21b", "2.21c", "3.1", "3.2",
        "3.3", "3.4", "3.5", "3.6", "3.7", "4.1", "4.2", "4.3a", "4.3b",
        "4.3c", "4.4a", "4.4b", "4.4c", "4.4d", "4.5", "4.6", "4.7", "4.8",
        "4.9", "4.10", "4.11", "4.12", "4.13", "4.14", "4.15", "4.16",
        "4.17", "4.18", "4.19", "5.1", "5.2", "5.3", "5.4", "5.5", "5.6",
        "5.7", "5.8", "5.9", "5.10", "5.11", "5.12", "5.13", "A1", "A2",
    }
    assert {row["locator"]["figure"] for row in figures} == expected_ids

    for figure in figures:
        locator = figure["locator"]
        values = figure["values"]
        assert locator["page"] == locator["pdf_page_index"] + 1
        assert values["species_as_printed"]
        assert values["phase_as_printed"]
        assert values["temperature_as_printed"]
        assert values["axes_as_printed"]
        assert values["caption_as_printed"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert "points" not in values
        series_names = {series["name"] for series in values["series_list"]}
        assert set(values["marker_count_per_series"]) == series_names
        assert all(
            isinstance(count, int) and count >= 0
            or count == "not enumerated; numeric points deferred"
            for count in values["marker_count_per_series"].values()
        )


def test_deferred_figure_locators_are_on_their_physical_pages() -> None:
    pdf_path = thesis_pdf_or_skip()
    extract = document()
    for species in extract["species"].values():
        for figure in species.get("context", []):
            if figure["type"] != "figure_record":
                continue
            locator = figure["locator"]
            source = "".join(page_text(pdf_path, locator["page"]).lower().split())
            source = "".join(character for character in source if character.isalnum())
            label = "figure" + "".join(
                character for character in locator["figure"].lower() if character.isalnum()
            )
            assert label in source, (locator["figure"], locator["page"])


def test_a1_table_locator_is_present_on_physical_page() -> None:
    pdf_path = thesis_pdf_or_skip()
    source = page_text(pdf_path, 195)
    normalized = "".join(source.split())
    assert "APPENDIXA1" in normalized
    for sample in a1_grid_rows(A1_RENDER_GRID):
        assert any(label in normalized for label in source_label_candidates(sample))


def test_table42_keeps_silicon_basis_unknown_and_infers_silica_reservoir_phase() -> None:
    rows = [
        row
        for row in document()["species"]["Si"]["observations"]
        if row["type"] == "activity_coefficient"
    ]
    assert len(rows) == 2
    for row in rows:
        standard_state = row["standard_state"]
        assert standard_state["state"]["tag"] == "unknown"
        assert standard_state["inferred"] is False
        assert "not itself the Si-in-copper coefficient reference" in standard_state["state"]["reason"]

        oxide_reference = row["oxide_saturation_reference"]
        assert oxide_reference["formula"] == "SiO2"
        assert oxide_reference["phase"] == "high cristobalite (cr)"
        assert oxide_reference["inferred"] is True
        assert "NIST-JANAF Fourth Edition" in oxide_reference["basis"]
        assert "1873 K" in oxide_reference["basis"]


def thesis_pdf_or_skip() -> Path:
    corpus_root = Path(
        os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
    )
    pdf_path = corpus_root / "raw/tranell-1999-unsw-thesis/tranell-1999-unsw-thesis.pdf"
    if not pdf_path.is_file():
        pytest.skip(f"Tranell thesis PDF is absent from {corpus_root}")
    return pdf_path


def page_text(pdf_path: Path, page: int) -> str:
    return subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf_path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_table42_source_locator_is_present_on_physical_page() -> None:
    pdf_path = thesis_pdf_or_skip()

    text = page_text(pdf_path, 118)
    assert "Table 4.2" in text
    assert "1051" in text and "0.0079" in text
    assert "1602" in text and "0.0148" in text


def test_appendix_physical_pages_and_row_locators_are_bound() -> None:
    pdf_path = thesis_pdf_or_skip()
    source_pages = {page: page_text(pdf_path, page) for page in range(199, 204)}
    expected_page_content = {
        199: ("Table A2", "CST5a", "CST12"),
        200: ("CST14", "CST5a", "CST12"),
        201: ("CST13", "CST14", "CST12"),
        202: ("CST14", "Table A3", "Table A4"),
        203: ("CSTA1", "CSTA2", "25.05"),
    }
    for page, labels in expected_page_content.items():
        assert all(label in source_pages[page] for label in labels)

    extract = document()
    normalized_pages = {page: "".join(text.split()) for page, text in source_pages.items()}
    for observation in extract["species"]["Ti"]["observations"]:
        for point in observation["values"]["points"]:
            if point["appendix_table"] not in {f"A2{letter}" for letter in "abcdefghij"} | {
                "A3a", "A3b", "A4a", "A4b", "A5a", "A5b"
            }:
                continue
            locator = point["locator"]
            assert locator["page"] == locator["pdf_page_index"] + 1
            assert point["value_as_printed"] in normalized_pages[locator["page"]]

    for row in extract["species"]["Ti"]["context"]:
        values = row.get("values", {})
        if values.get("source_table") not in {
            *(f"A2{letter}" for letter in "abcdefghij"),
            "A3a", "A3b", "A4a", "A4b", "A5a", "A5b",
        }:
            continue
        locator = row["locator"]
        assert locator["page"] == locator["pdf_page_index"] + 1
        sample = values.get("sample_as_printed")
        if sample:
            assert any(
                label in normalized_pages[locator["page"]]
                for label in source_label_candidates(sample)
            )


def test_isolated_migration_preserves_species_temperature_and_conditions() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert not migrator.result.validation.hard_issues
    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 92
    assert sum(row.identity.species.formula == "Si" for row in observations) == 4
    assert sum(row.identity.species.formula == "Ti" for row in observations) == 88
    for row in observations:
        assert row.identity.species.formula in {"Si", "Ti"}
        assert row.identity.temperature_K is not None
        assert row.point_conditions is not None
        assert row.locator.page == row.locator.pdf_page_index + 1
    si_rows = [row for row in observations if row.identity.species.formula == "Si"]
    for row in si_rows:
        assert row.identity.temperature_K.value == Decimal("1873")
        oxygen = row.point_conditions["oxygen_partial_pressure"]
        assert oxygen.state.is_value and str(oxygen.state.value) == "10^-12"
        assert oxygen.locator.page == 118
    ti_rows = [row for row in observations if row.identity.species.formula == "Ti"]
    for row in ti_rows:
        assert row.identity.temperature_K.is_value
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["oxygen_partial_pressure"].state.is_value
        assert row.point_conditions["composition"].state is not None
        assert row.point_conditions["quench_state"].state.is_value
