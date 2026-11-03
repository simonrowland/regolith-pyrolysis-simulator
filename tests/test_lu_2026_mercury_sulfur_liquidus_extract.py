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
        assert values["phase_label"] == "melt"
        assert values["phase_label_as_printed"] == "Gl"
        assert values["basis"] == "wt_percent"
        assert values["source_as_published"] == source
        assert values["phase_assemblage_as_published"] == phases
        assert conditions["sample_id"]["state"]["value"] == sample
        assert conditions["pressure_GPa"]["state"]["value"]["value_as_published"] == pressure
        assert conditions["duration_min"]["state"]["value"]["value_as_published"] == duration
        assert Decimal(str(conditions["fO2_delta_IW"]["state"]["value"])) == Decimal(
            diw.replace("−", "-")
        )
        assert conditions["fO2_delta_IW"]["units"] == "delta_IW"
        assert conditions["fO2_delta_IW"]["reference_buffer_as_published"] == (
            "iron-wüstite (IW)"
        )
        assert values["oxygen_buffer_as_published"] == "iron-wüstite (IW)"
        assert values["fO2_delta_IW_method_as_published"] == (
            "calculated from Si-SiO2 equilibrium according to Cartier et al. (2014)"
        )
        composition = conditions["composition"]
        assert composition["state"]["tag"] == "unknown"
        assert composition["state"]["reason"] == (
            "the combined oxide plus Fe/FeS starting mixture is given as separate "
            "silicate composition and source fractions; no final component map is "
            "printed and none is reconstructed"
        )
        assert composition["locator"]["table"] == "1"
        assert composition["locator"]["row"] == system
        starting = conditions["starting_silicate_composition_as_published"]
        assert starting["state"]["tag"] == "value"
        assert starting["locator"]["table"] == "1"
        assert starting["locator"]["row"] == system
        assert conditions["source_blend_as_published"]["state"]["tag"] == "value"
        assert conditions["source_blend_as_published"]["locator"]["table"] == "1"
        assert conditions["assemblage_as_published"]["state"]["value"] == phases
        assert conditions["assemblage_as_published"]["locator"]["table"] == "2"
        sulfide_phases = [
            phase for phase in phases.split(", ") if phase in {"FeS", "FeMgS"}
        ]
        sulfide = conditions["sulfide_phase_as_published"]
        if sulfide_phases:
            assert sulfide["state"]["tag"] == "value"
            assert sulfide["state"]["value"] == ", ".join(sulfide_phases)
        else:
            assert sulfide["state"]["tag"] == "unknown"
            assert sulfide["state"]["reason"] == (
                "no sulfide phase is listed in the Table 2 experimental-phases cell for this run"
            )
        assert sulfide["locator"]["table"] == "2"
        expected_container = (
            "MgSiO3"
            if "Ens" in phases and "Fo" not in phases
            else "Mg2SiO4"
            if "Fo" in phases and "Ens" not in phases
            else "SiO2"
        )
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

    deletion_paths = (
        ("values", "phase_label"),
        ("values", "phase_label_as_printed"),
        ("point_conditions", "composition"),
        ("point_conditions", "fO2_delta_IW"),
        ("point_conditions", "sulfide_phase_as_published"),
    )
    for section, key in deletion_paths:
        missing_binding = copy.deepcopy(document)
        first_sulfur_row = next(
            row
            for species in missing_binding["species"].values()
            for row in species.get("observations") or []
            if row.get("observation_id") == "lu26_mer10_3gpa_a21_1650c_01_s_in_glass"
        )
        del first_sulfur_row[section][key]
        with pytest.raises(KeyError):
            _assert_table2_grid(missing_binding, TABLE2_RENDER)


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
            assert raw["uncertainty"]["T_m_uncertainty_K"] == 12.5
            assert observation.uncertainty.kind.value == "printed"
            assert observation.uncertainty.verbatim == raw["uncertainty"]

    sulfur_rows = [
        observation for observation in result.observations.values()
        if observation.identity.species.formula == "S"
    ]
    assert len(sulfur_rows) == 61
    for observation in sulfur_rows:
        composition = observation.point_conditions["composition"]
        assert composition.state.is_unknown
        assert composition.state.reason == (
            "the combined oxide plus Fe/FeS starting mixture is given as separate "
            "silicate composition and source fractions; no final component map is "
            "printed and none is reconstructed"
        )
        assert composition.locator is not None
        assert composition.locator.table == "1"
        assert composition.locator.page == 2
        starting = observation.point_conditions["starting_silicate_composition_as_published"]
        assert starting.state.is_value
        assert starting.locator is not None
        assert starting.locator.table == "1"
        source_blend = observation.point_conditions["source_blend_as_published"]
        assert source_blend.state.is_value
        assert source_blend.locator is not None
        assert source_blend.locator.table == "1"
        assemblage = observation.point_conditions["assemblage_as_published"]
        assert assemblage.state.is_value
        assert assemblage.locator is not None
        assert assemblage.locator.table == "2"
        raw_id = observation.observation_id.split("::T=", 1)[0]
        raw = raw_rows[raw_id][1]
        raw_phases = raw["values"]["phase_assemblage_as_published"]
        listed_sulfides = [
            phase for phase in raw_phases.split(", ") if phase in {"FeS", "FeMgS"}
        ]
        sulfide = observation.point_conditions["sulfide_phase_as_published"]
        assert sulfide.locator is not None
        assert sulfide.locator.table == "2"
        if listed_sulfides:
            assert sulfide.state.is_value
            assert sulfide.state.value == ", ".join(listed_sulfides)
        else:
            assert sulfide.state.is_unknown
            assert sulfide.state.reason == (
                "no sulfide phase is listed in the Table 2 experimental-phases cell for this run"
            )
        delta_iw = observation.point_conditions["fO2_delta_IW"]
        assert delta_iw.state.is_value
        assert delta_iw.locator is not None
        raw_delta = raw_rows[raw_id][1]["point_conditions"]["fO2_delta_IW"]["state"]["value"]
        assert delta_iw.state.value == Decimal(str(raw_delta).replace("−", "-"))
        assert observation.identity.phase_role.is_value
        assert observation.identity.phase_role.value.value == "melt"
        assert observation.identity.fO2_Pa.is_unknown
        assert "buffer-relative fO2 (iron-wüstite (IW))" in observation.identity.fO2_Pa.reason


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
