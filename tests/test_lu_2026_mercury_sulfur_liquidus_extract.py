"""Lu et al. (2026): independent table grids, figure deferrals and migration."""

from __future__ import annotations

import copy
import os
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests.battery.test_migrate import _migrate_real_extract


ROOT = Path(__file__).resolve().parents[1]
SID = "lu-2026-mercury-sulfur-liquidus"
EXTRACT_NAME = f"{SID}.yaml"
EXTRACT = ROOT / "data" / "literature" / "extracts" / EXTRACT_NAME
CORPUS = Path(os.environ.get("REGOLITH_CORPUS_ROOT", Path.home() / "Repos" / "regolith-corpus"))
PDF = CORPUS / "raw" / SID / f"{SID}.pdf"
TEXT = CORPUS / "text" / SID / "pdftotext-layout.txt"

# Independent transcription from the 600 dpi render of physical PDF page 3.
# Columns: sample|T_C|P_GPa|Fe-or-S source|time_min|printed ΔIW|phases|S(Sil) wt.%
TABLE2_RENDER = tuple(tuple(line.split("|")) for line in """\
A21|1650|3|0.2 FeS|30|−6.5|Gl (83), Ens (17), FeSi, FeS, FeMgS|6.26*
A22|1675|3|0.2 FeS|30|−6.5|Gl, FeSi, FeS, FeMgS|6*
A23|1700|3|0.2 FeS|20|−6.5|Gl, FeSi, FeS, FeMgS|6.79*
A11|1675|3|0.1 FeS|30|−7.4|Gl (92), Ens (8), FeSi|4.82
A12|1700|3|0.1 FeS|30|−7.3|Gl, FeSi|3.46
A13|1725|3|0.1 FeS|30|−7.3|Gl, FeSi|3.86
A051|1725|3|0.05 FeS|60|−7.4|Gl (99), Fo (<1), FeSi|1.44
A052|1750|3|0.05 FeS|46|−7.3|Gl, FeSi|1.35
A052|1775|3|0.05 FeS|30|−7.2|Gl, FeSi|1.46
A01|1750|3|0.14 Fe|30|−6.8|Gl (99), Fo (<1), FeSi|0
A02|1775|3|0.14 Fe|40|−6.8|Gl, FeSi|0
a21|1775|5|0.2 FeS|30|−6.7|Gl (79), Ens (21), FeSi, FeS|5.88*
a22|1800|5|0.2 FeS|20|−6.5|Gl, FeSi, FeS|5.31*
a23|1825|5|0.2 FeS|20|−6.6|Gl, FeSi, FeS|5.77*
a11|1800|5|0.1 FeS|60|−7.5|Gl (66), Ens (34), FeSi|4.41
a12|1825|5|0.1 FeS|30|−7.3|Gl, FeSi|2.95
a13|1850|5|0.1 FeS|20|−7.1|Gl, FeSi|3.7
a051|1825|5|0.05 FeS|60|−7.4|Gl (62), Ens (38), Fo**, FeSi|1.68
a052|1850|5|0.05 FeS|60|−7.4|Gl, FeSi|1.52
a053|1875|5|0.05 FeS|60|−7.2|Gl, FeSi|1.17
a01|1850|5|0.14 Fe|30|−6.9|Gl (67), Ens (33), Fo**, FeSi|0
a02|1875|5|0.14 Fe|50|−6.7|Gl, FeSi|0
a03|1900|5|0.14 Fe|40|−6.7|Gl, FeSi|0
B21|1650|3|0.2 FeS|30|−6.5|Gl (76), Fo (24), FeSi, FeS, FeMgS|6.36*
B22|1675|3|0.2 FeS|30|−6.2|Gl (79), Fo (21), FeSi, FeS, FeMgS|5.74*
B23|1700|3|0.2 FeS|20|−6.1|Gl, FeSi, FeS, FeMgS|5.9*
B11|1675|3|0.1 FeS|30|−7.1|Gl (81), Fo (19), FeSi,|5.63
B12|1700|3|0.1 FeS|30|−7.1|Gl (94), Fo (6), FeSi|5.17
B13|1725|3|0.1 FeS|30|−6.5|Gl, FeSi|5.5
B14|1750|3|0.1 FeS|30|−5.8|Gl, FeSi, FeS|4.89*
B051|1725|3|0.05 FeS|60|−7.6|Gl (74), Fo (26), FeSi|2.84
B052|1750|3|0.05 FeS|46|−7.5|Gl (84), Fo (16), FeSi|2.9
B052|1775|3|0.05 FeS|30|−7.4|Gl (90), Fo (10), FeSi|2.3
B054|1800|3|0.05 FeS|20|−7.3|Gl, FeSi|2.25
B055|1825|3|0.05 FeS|20|−7.1|Gl, FeSi|1.99
B01|1750|3|0.14 Fe|30|−6.9|Gl (63), Fo (37), FeSi|0
B02|1775|3|0.14 Fe|40|−6.7|Gl (73), Fo (27), FeSi|0
B03|1800|3|0.14 Fe|30|−6.8|Gl (77), Fo (23), FeSi|0
B04|1825|3|0.14 Fe|30|−7.1|Gl (85), Fo (15), FeSi|0
B05|1850|3|0.14 Fe|30|−6.6|Gl (98), Fo (2), FeSi|0
B06|1875|3|0.14 Fe|20|−6.3|Gl (99), Fo (<1), FeSi|0
B07|1900|3|0.14 Fe|20|−6.1|Gl, FeSi|0
b21|1775|5|0.2 FeS|30|−6.7|Gl (86), Fo (14), FeSi, FeS|5.75*
b22|1800|5|0.2 FeS|20|−6.2|Gl (88), Fo (12), FeSi, FeS|5.16*
b23|1825|5|0.2 FeS|20|−5.9|Gl, FeSi, FeS|5.21*
b24|1850|5|0.2 FeS|20|−6.2|Gl, FeSi, FeS|5.95*
b11|1800|5|0.1 FeS|60|−7.0|Gl (82), Fo (18), FeSi|5.22
b12|1825|5|0.1 FeS|30|−6.7|Gl (88), Fo (12), FeSi, FeS|4.99*
b13|1850|5|0.1 FeS|20|−6.1|Gl (93), Fo (7), FeSi, FeS|4.19*
b14|1870|5|0.1 FeS|20|−6.3|Gl, FeSi, FeS|4.57*
b051|1825|5|0.05 FeS|60|−7.5|Gl (75), Fo (25), FeSi|2.58
b052|1850|5|0.05 FeS|60|−7.3|Gl (87), Fo (13), FeSi|2.53
b053|1875|5|0.05 FeS|60|−7.2|Gl (99), Fo (<1), FeSi|1.88
b054|1900|5|0.05 FeS|20|−7.3|Gl, FeSi|2.19
b055|1925|5|0.05 FeS|20|−7.1|Gl, FeSi|1.87
b01|1850|5|0.14 Fe|30|−7.1|Gl (68), Fo (32), FeSi|0
b02|1875|5|0.14 Fe|50|−6.9|Gl (81), Fo (19), FeSi|0
b03|1900|5|0.14 Fe|40|−6.7|Gl (85), Fo (15), FeSi|0
b04|1950|5|0.14 Fe|20|−6.4|Gl (94), Fo (6), FeSi|0
b05|1975|5|0.14 Fe|20|−6.2|Gl, FeSi|0
b06|2000|5|0.14 Fe|20|−6.4|Gl, FeSi|0
""".strip().splitlines())

TABLE1_RENDER = (
    ("EH-EL", "61.21", "0.12", "3.23", "0.45", "0.25", "32.64", "1.48", "1.21", "0.11", "0.32", "101.02"),
    ("Mer10", "54.82", "0.14", "3.7", "0.52", "0.28", "37.32", "1.62", "1.37", "0.13", "0.10", "100.00"),
    ("Mer20", "47.91", "0.16", "4.26", "0.60", "0.33", "43.04", "1.87", "1.58", "0.15", "0.10", "100.00"),
)
BLEND_RENDER = (
    ("0.2S", "0.80", "0.00", "0.20", "7.30"),
    ("0.1S", "0.90", "0.00", "0.10", "3.60"),
    ("0.05S", "0.95", "0.00", "0.05", "1.80"),
    ("0S", "0.86", "0.14", "0.00", "0.00"),
)

# Independent transcription of prose liquidus estimates and their state from pp. 5–6.
LIQUIDUS_RENDER = (
    ("Mer10", 3, "0", "0", "1762.5", "forsterite", 5),
    ("Mer10", 5, "0", "0", "1862.5", "forsterite + enstatite", 5),
    ("Mer20", 3, "0", "0", "1887.5", "forsterite", 5),
    ("Mer20", 5, "0", "0", "1962.5", "forsterite", 5),
    ("Mer10", 3, "1.8", "1.35", "1737.5", "forsterite", 5),
    ("Mer10", 5, "1.8", "1.52", "1837.5", "forsterite + enstatite", 5),
    ("Mer20", 3, "1.8", "2.25", "1787.5", "forsterite", 5),
    ("Mer20", 5, "1.8", "2.19", "1887.5", "forsterite", 5),
    ("Mer10", 3, "3.6", "3.46", "1687.5", "enstatite", 5),
    ("Mer10", 5, "3.6", "2.95", "1812.5", "enstatite", 5),
    ("Mer20", 3, "3.6", "5.5", "1712.5", "forsterite", 6),
    ("Mer20", 5, "3.6", "4.57", "1860", "forsterite", 6),
    ("Mer10", 3, "7.3", "6", "1662.5", "enstatite", 6),
    ("Mer10", 5, "7.3", "5.31", "1787.5", "enstatite", 6),
    ("Mer20", 3, "7.3", "5.9", "1687.5", "forsterite", 6),
    ("Mer20", 5, "7.3", "5.21", "1812.5", "forsterite", 6),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_table2_grid(document: dict, render_grid) -> None:
    observations = {
        (row["values"]["sample_id"], row["values"]["T_C_as_printed"]): (container, row)
        for container, species in document["species"].items()
        for row in species.get("observations") or []
        if row.get("type") == "equilibrium_phase_composition"
    }
    assert len(render_grid) == 61
    assert len(observations) == 61
    for index, row in enumerate(render_grid):
        sample, temperature, pressure, source, duration, diw, phases, sulfur = row
        system = "Mer10" if index < 23 else "Mer20"
        container, record = observations[(sample, temperature)]
        expected_cells = [system, sample, temperature, pressure, source, duration, diw, phases, sulfur]
        values = record["values"]
        conditions = record["point_conditions"]
        assert values["table_row_as_published"] == expected_cells
        assert record["locator"]["row"] == f"{index + 1}: {sample} at {temperature} °C"
        assert values["formula"] == "S"
        assert values["component_as_published"] == "S"
        assert values["value_as_published"] == sulfur
        assert values["value"] == float(sulfur.removesuffix("*"))
        assert values["T_C_as_printed"] == temperature
        assert values["source_as_published"] == source
        assert values["phase_assemblage_as_published"] == phases
        assert conditions["sample_id"]["state"]["value"] == sample
        assert conditions["pressure_GPa"]["state"]["value"]["value_as_published"] == pressure
        assert conditions["duration_min"]["state"]["value"]["value_as_published"] == duration
        assert conditions["fO2_delta_IW"]["state"]["value"]["delta_as_published"] == diw
        assert conditions["fO2_delta_IW"]["state"]["value"]["reference_buffer"] == "iron-wüstite (IW)"
        assert conditions["assemblage_as_published"]["state"]["value"] == phases
        expected_container = "MgSiO3" if "Ens" in phases and "Fo" not in phases else "Mg2SiO4" if "Fo" in phases and "Ens" not in phases else "SiO2"
        assert container == expected_container


def test_independent_render_grids_bind_each_table2_cell_and_mutations_fail() -> None:
    document = _document()
    _assert_table2_grid(document, TABLE2_RENDER)

    table1 = next(row for row in document["species"]["SiO2"]["context"] if row["observation_id"] == "lu26_table1_starting_compositions")
    assert tuple((row["sample"], *row["cells_as_published"]) for row in table1["values"]["rows"]) == TABLE1_RENDER
    assert tuple((row["starting_blend"], row["silicate_fraction"], row["Fe_fraction"], row["FeS_fraction"], row["bulk_S_content_wt_pct"]) for row in table1["values"]["starting_mixtures"]) == BLEND_RENDER

    changed_grid = list(TABLE2_RENDER)
    changed_grid[0] = (*changed_grid[0][:-1], "6.27*")
    with pytest.raises(AssertionError):
        _assert_table2_grid(document, changed_grid)

    changed_document = copy.deepcopy(document)
    changed_observation = next(
        row
        for species in changed_document["species"].values()
        for row in species.get("observations") or []
        if row.get("observation_id") == "lu26_mer10_3gpa_a21_1650c_01_s_in_glass"
    )
    changed_observation["values"]["value_as_published"] = "6.27*"
    with pytest.raises(AssertionError):
        _assert_table2_grid(changed_document, TABLE2_RENDER)


def test_liquidus_estimates_and_deferred_figure_records_are_complete() -> None:
    document = _document()
    observations = {
        row["observation_id"]: (container, row)
        for container, species in document["species"].items()
        for row in species.get("observations") or []
    }
    assert len(LIQUIDUS_RENDER) == 16
    for system, pressure, bulk_s, melt_s, temperature, liquidus_phase, page in LIQUIDUS_RENDER:
        row = next(
            row for _container, row in observations.values()
            if row.get("values", {}).get("starting_S_wt_pct_as_printed") == bulk_s
            and row.get("values", {}).get("composition_as_published") == system
            and row.get("values", {}).get("silicate_melt_S_wt_pct_as_reported") == melt_s
            and row.get("values", {}).get("T_C_as_printed") == f"{temperature} ± 12.5"
            and row.get("values", {}).get("points", [{}])[0].get("point_conditions", {}).get("pressure_GPa", {}).get("state", {}).get("value", {}).get("value_as_published") == str(pressure)
        )
        assert row["type"] == "transition_point"
        assert row["locator"]["page"] == page
        assert row["locator"]["pdf_page_index"] == page - 1
        assert row["values"]["property_kind"] == "liquidus"
        assert row["values"]["method_class"] == "author_estimate"
        assert row["values"]["T_C"] == float(temperature)
        assert row["values"]["T_m_uncertainty_K"] == 12.5
        assert row["values"]["liquidus_phase_as_published"] == liquidus_phase
        assert row["values"]["secondary_locator"]["figure"] == "5"

    figures = [
        row for row in document["species"]["SiO2"]["context"]
        if row.get("type") == "figure_record"
    ]
    assert {row["locator"]["figure"] for row in figures} == {str(n) for n in range(1, 10)}
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("points" not in row["values"] for row in figures)
    assert all(isinstance(series["marker_count"], int) for row in figures for series in row["values"]["series"])


def test_isolated_migration_keeps_species_temperature_and_conditions(tmp_path: Path) -> None:
    document = _document()
    result = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert len(result.observations) == 77

    raw_rows = {
        f"{SID}::{row['observation_id']}": (container, row)
        for container, species in document["species"].items()
        for row in species.get("observations") or []
    }
    assert len(raw_rows) == 77
    for observation_id, observation in result.observations.items():
        parent_id = observation_id.split("::T=", 1)[0]
        container, raw = raw_rows[parent_id]
        expected_formula = raw.get("values", {}).get("formula", container)
        assert observation.identity.species.formula == expected_formula
        assert observation.point_conditions["pressure_GPa"].state.is_value
        assert observation.point_conditions["pressure_GPa"].locator is not None
        assert observation.point_conditions["total_pressure_Pa"].locator is not None
        if raw["type"] == "equilibrium_phase_composition":
            assert observation.identity.temperature_K.is_value
            assert observation.point_conditions["temperature_K"].state.is_value
            assert observation.point_conditions["temperature_K"].locator is not None
            assert observation.value.point == Decimal(str(raw["values"]["value"]))
        else:
            assert observation.value.point == Decimal(str(raw["values"]["T_C"])) + Decimal("273.15")

    sulfur_rows = [
        observation for observation in result.observations.values()
        if observation.identity.species.formula == "S"
    ]
    assert len(sulfur_rows) == 61
    assert all(observation.point_conditions["fO2_delta_IW"].state.is_value for observation in sulfur_rows)
    assert all(observation.point_conditions["fO2_delta_IW"].locator is not None for observation in sulfur_rows)


def test_source_locators_resolve_on_the_cited_physical_pages() -> None:
    if not PDF.is_file() or not TEXT.is_file():
        pytest.skip("Lu et al. corpus PDF/text is absent")
    page_text = {}
    for page in range(2, 12):
        completed = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        page_text[page] = completed.stdout
    assert "Table 1" in page_text[2]
    assert "61.21" in page_text[2] and "54.82" in page_text[2]
    assert "Table 2" in page_text[3]
    assert all(row[0] in page_text[3] and row[1] in page_text[3] and row[-1].removesuffix("*") in page_text[3] for row in TABLE2_RENDER)
    for row in LIQUIDUS_RENDER:
        assert row[4] in page_text[row[6]]
    document = _document()
    figures = [
        row for row in document["species"]["SiO2"]["context"]
        if row.get("type") == "figure_record"
    ]
    for figure in figures:
        number = figure["locator"]["figure"]
        page = figure["locator"]["page"]
        assert figure["locator"]["pdf_page_index"] == page - 1
        assert f"Fig. {number}." in page_text[page]
