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
    assert len(observations) == 51
    assert Counter(row["type"] for row in observations.values()) == {
        "gibbs_table": 24,
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
    assert temperature_kinds == {"point": 27, "bound": 24}

    table_values = {
        observation_id: observation["values"]
        for observation_id, observation in observations.items()
    }
    assert table_values[
        "shornikov_2015_cai_mineral_thermodynamics_p002_t01_r02_c02"
    ]["value"] == "-8.91"
    assert table_values[
        "shornikov_2015_cai_mineral_thermodynamics_p002_t01_r08_c03"
    ]["value"] == "-0.92"
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
        "shornikov_2015_cai_mineral_thermodynamics_p002_t01_r07_c04_dissociation"
    ]["T_m_K"] == "1836"
    assert table_values[
        "shornikov_2015_cai_mineral_thermodynamics_p002_t01_r07_c04_liquid_phase"
    ]["T_m_K"] == "1850"
    assert table_values[
        "shornikov_2015_cai_mineral_thermodynamics_p002_t01_r13_c04_dissociation"
    ]["T_m_K"] == "1373"
    assert table_values[
        "shornikov_2015_cai_mineral_thermodynamics_p002_t01_r13_c04_liquid_phase"
    ]["T_m_K"] == "1673"

    figure_records = [
        context
        for record in species.values()
        for context in record["context"]
    ]
    assert len(figure_records) == 12
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
