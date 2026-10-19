from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "literature"
    / "extracts"
    / "shornikov-2015-cai-mineral-thermodynamics.yaml"
)


def _locators(value: object):
    if isinstance(value, dict):
        locator = value.get("locator")
        if isinstance(locator, dict):
            yield locator
        for child in value.values():
            yield from _locators(child)
    elif isinstance(value, list):
        for child in value:
            yield from _locators(child)


def test_all_locators_map_printed_pdf_page_to_zero_based_index() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    locators = [locator for locator in _locators(document) if "page" in locator]
    assert len(locators) == 93
    assert all(locator["page"] == 2 for locator in locators)
    assert all(locator["pdf_page_index"] == 1 for locator in locators)


def test_shornikov_2015_cai_table_and_deferred_figure_records() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    species = document["species"]
    expected_formulas = {
        "CaAl2O4",
        "CaAl4O7",
        "CaAl12O19",
        "CaTiO3",
        "MgAl2O4",
        "MgSiO3",
        "Mg2SiO4",
        "CaMgSi2O6",
        "Ca2MgSi2O7",
        "Ca2Al2SiO7",
        "CaAl2Si2O8",
        "Ca3Al2Si3O12",
    }
    assert set(species) == expected_formulas

    observations = {
        observation["observation_id"]: observation
        for record in species.values()
        for observation in record["observations"]
    }
    assert len(observations) == 27
    assert Counter(row["type"] for row in observations.values()) == {
        "transition_point": 15,
        "heat_capacity_and_heat_content_series": 12,
    }

    temperature_kinds: Counter[str] = Counter()
    for observation in observations.values():
        temperature = observation["point_conditions"]["temperature_K"]
        assert temperature["locator"]["page"] in {1, 2}
        state = temperature["state"]
        assert state["tag"] == "value"
        kind = state["value"]["kind"]
        assert kind in {"point", "bound"}
        temperature_kinds[kind] += 1
        if kind == "bound":
            assert state["value"]["bound_operator"] == ">"
            assert state["value"]["bound_value"] == "1000"
    assert temperature_kinds == {"point": 27}

    table_values = {
        observation_id: observation["values"]
        for observation_id, observation in observations.items()
    }
    parameter_context = next(
        context
        for context in species["CaAl2O4"]["context"]
        if context["type"] == "oxide_component_formation_thermodynamic_parameters"
    )
    parameter_rows = parameter_context["values"]["rows"]
    assert len(parameter_rows) == 12
    assert {row["formula"] for row in parameter_rows} == expected_formulas
    assert parameter_context["values"]["phase_as_printed"] == "crystalline state"
    assert parameter_context["values"]["method_class"] == "quoted_attributed"
    assert parameter_context["values"]["attribution_as_printed"] == "[11–17]"
    assert parameter_context["values"]["reaction_as_printed"] == (
        "ΔG° = ∑ n_i ΔG°_i + ΔG_T ∑ n_i"
    )
    assert parameter_rows[0]["delta_H_T_kJ_per_mole"] == "-8.91"
    assert parameter_rows[6]["delta_S_T_J_per_mole_K"] == "-0.92"
    assert parameter_rows[11]["delta_S_T_J_per_mole_K"] == "-10.98"
    assert parameter_rows[0]["locator"]["page"] == 2
    assert all(
        observation["values"]["quantity"] not in {"delta_fH", "S"}
        for observation in observations.values()
    )
    assert table_values[
        "shornikov_2015_cai_mineral_thermodynamics_p002_t01_r04_c04_dissociation"
    ]["T_m_K"] == "2123"
    assert observations[
        "shornikov_2015_cai_mineral_thermodynamics_p002_t01_r04_c04_dissociation"
    ]["phase"] == "cr"
    assert table_values[
        "shornikov_2015_cai_mineral_thermodynamics_p002_t01_r04_c04_liquid_phase"
    ]["T_m_K"] == "2148"
    assert observations[
        "shornikov_2015_cai_mineral_thermodynamics_p002_t01_r04_c04_liquid_phase"
    ]["phase"] == "l"
    assert table_values[
        "shornikov_2015_cai_mineral_thermodynamics_p002_t01_r04_c04_liquid_phase"
    ]["property_kind"] == "liquid_phase_temperature"
    assert table_values[
        "shornikov_2015_cai_mineral_thermodynamics_p002_t01_r04_c04_liquid_phase"
    ]["property_kind_as_printed"] == "liquid phase temperature"
    assert table_values[
        "shornikov_2015_cai_mineral_thermodynamics_p002_t01_r07_c04_dissociation"
    ]["T_m_K"] == "1836"
    assert table_values[
        "shornikov_2015_cai_mineral_thermodynamics_p002_t01_r07_c04_liquid_phase"
    ]["T_m_K"] == "1850"
    assert table_values[
        "shornikov_2015_cai_mineral_thermodynamics_p002_t01_r07_c04_liquid_phase"
    ]["property_kind"] == "liquid_phase_temperature"
    assert table_values[
        "shornikov_2015_cai_mineral_thermodynamics_p002_t01_r13_c04_dissociation"
    ]["T_m_K"] == "1373"
    assert table_values[
        "shornikov_2015_cai_mineral_thermodynamics_p002_t01_r13_c04_liquid_phase"
    ]["T_m_K"] == "1673"
    assert table_values[
        "shornikov_2015_cai_mineral_thermodynamics_p002_t01_r13_c04_liquid_phase"
    ]["property_kind"] == "liquid_phase_temperature"

    figure_records = [
        context
        for record in species.values()
        for context in record["context"]
        if context["type"] == "thermodynamic_example"
    ]
    assert len(figure_records) == 12
    all_contexts = [
        context
        for record in species.values()
        for context in record["context"]
    ]
    assert len(all_contexts) == 13
    for figure in figure_records:
        values = figure["values"]
        assert values["quantity"] == "delta_fG"
        assert values["phase"]["state"]["tag"] == "unknown"
        assert values["temperature_range_as_plotted"] == "1000–2200 K"
        assert values["x_axis_as_printed"] == "T, K"
        assert values["y_axis_as_printed"] == "ΔG_T, kJ/mole"
        assert values["attribution"].startswith("not stated in the caption")
        assert len(values["series_list"]) == 12
        assert set(values["marker_count_per_series"].values()) == {0}
        assert values["digitisation"] == "deferred to a figure pass"
        assert "points" not in values and "series" not in values
