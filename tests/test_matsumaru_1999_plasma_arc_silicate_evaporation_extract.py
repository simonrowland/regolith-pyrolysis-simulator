from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / (
    "data/literature/extracts/"
    "matsumaru-1999-plasma-arc-silicate-evaporation.yaml"
)
SOURCE_ID = "matsumaru-1999-plasma-arc-silicate-evaporation"

# Independent transcription of Tables 1–3 from the 600 dpi physical-page 5–6 renders.
TABLE1_RENDER = (
    ("Al3+-Ca2+", "-154810"),
    ("Al3+-Si4+", "-127610"),
    ("Ca2+-Si4+", "-133890"),
    ("Al3+-Fe2+", "-41000"),
    ("Al3+-Fe3+", "-161080"),
    ("Ca4+-Fe2+", "-31380"),
    ("Ca4+-Fe3+", "-95810"),
    ("Si2+-Fe2+", "-41840"),
    ("Si2+-Fe3+", "+32640"),
)
TABLE2_MEASURED_RENDER = (("Glass", "3.8×10^-7"), ("Fe-Glass", "2.0×10^-7"))
TABLE2_MODEL_RENDER = (
    ("Glass", "3.6×10^-7", None),
    ("Fe-Glass", "1.8×10^-7", "all iron oxide is Fe2O3"),
    ("Fe-Glass", "1.7×10^-7", "all iron oxide is FeO"),
)
TABLE3_RENDER = {
    "Ash A": {
        "SiO2": "20.65", "CaO": "27.33", "Al2O3": "17.6", "Fe2O3": "4.43",
        "K2O": "3.02", "Na2O": "3.42", "P2O5": "1.98", "MgO": "2.7",
        "TiO2": "1.57", "T-S*": "0.32", "T-Cl*": "5.47", "Pb": "0.34",
        "T-Cr*": "0.035", "Mn": "0.18", "Cu": "0.531", "Zn": "0.71", "Ni": "0.021",
    },
    "Ash B": {
        "SiO2": "38.73", "CaO": "20.72", "Al2O3": "12.74", "Fe2O3": "3.71",
        "K2O": "2.5", "Na2O": "5.63", "P2O5": "1.36", "MgO": "2.06",
        "TiO2": "1.01", "T-S*": "0.19", "T-Cl*": "3.33", "Pb": "0.22",
        "T-Cr*": "0.026", "Mn": "0.125", "Cu": "0.847", "Zn": "0.73", "Ni": "0.022",
    },
    "Ash C": {
        "SiO2": "44.95", "CaO": "17.67", "Al2O3": "12.47", "Fe2O3": "3.66",
        "K2O": "2.23", "Na2O": "3.93", "P2O5": "2.32", "MgO": "2.68",
        "TiO2": "1.35", "T-S*": "N.D.", "T-Cl*": "0.4", "Pb": "0.1",
        "T-Cr*": "0.041", "Mn": "0.132", "Cu": "0.136", "Zn": "0.25", "Ni": "0.03",
    },
}
FE_GLASS_COMPOSITION_RENDER = {
    "SiO2": "36", "CaO": "36", "Al2O3": "18", "Fe2O3": "10",
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _context(document: dict, observation_id: str) -> dict:
    return next(
        row
        for group in document["species"].values()
        for row in group["context"]
        if row["observation_id"] == observation_id
    )


def _table3_grid(document: dict) -> dict[str, dict[str, str]]:
    table = _context(document, "matsumaru_1999_table3_quoted_incinerator_ash_compositions")
    return {
        row["sample_id"]: row["values_as_printed"]
        for row in table["values"]["ash_samples"]
    }


def _assert_table3_matches_render(document: dict, render_grid: dict) -> None:
    assert _table3_grid(document) == render_grid


def test_render_transcriptions_and_mutations() -> None:
    document = _document()
    table1 = _context(document, "matsumaru_1999_table1_regular_solution_interaction_energies")
    table2_measured = _context(document, "matsumaru_1999_table2_measured_bulk_mass_loss_rates")
    table2_model = _context(document, "matsumaru_1999_table2_model_calculated_rates")

    assert [
        (row["ion_pair"], row["interaction_energy_J_as_printed"])
        for row in table1["values"]["rows"]
    ] == list(TABLE1_RENDER)
    assert [
        (row["sample_id"], row["measured_rate_kg_per_s_as_printed"])
        for row in table2_measured["values"]["rows"]
    ] == list(TABLE2_MEASURED_RENDER)
    assert [
        (
            row["sample_id"],
            row["calculated_rate_kg_per_s_as_printed"],
            row.get("assumption"),
        )
        for row in table2_model["values"]["rows"]
    ] == list(TABLE2_MODEL_RENDER)
    _assert_table3_matches_render(document, TABLE3_RENDER)
    assert table2_measured["values"]["duplicate_locators"] == [
        {"page": 1, "pdf_page_index": 0, "published_page": 21, "section": "Abstract"},
        {"page": 6, "pdf_page_index": 5, "published_page": 26, "section": "Conclusion"},
    ]

    # Mutating either the independently transcribed render grid or one extract cell fails.
    changed_render = deepcopy(TABLE3_RENDER)
    changed_render["Ash B"]["Na2O"] = "5.64"
    with pytest.raises(AssertionError):
        _assert_table3_matches_render(document, changed_render)
    changed_extract = deepcopy(document)
    _table3_grid(changed_extract)["Ash B"]["Na2O"] = "5.64"
    with pytest.raises(AssertionError):
        _assert_table3_matches_render(changed_extract, TABLE3_RENDER)

    figures = [
        row
        for group in document["species"].values()
        for row in group["context"]
        if row["type"] == "figure_record"
    ]
    assert len(figures) == 8
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all(row["values"]["numeric_points"] == "none" for row in figures)
    marker_counts = {
        row["observation_id"]: [
            series["marker_count"]
            for panel in row["values"].get("panels", [])
            for series in panel["series"]
        ] or [series["marker_count"] for series in row["values"]["series"]]
        for row in figures
    }
    assert marker_counts == {
        "matsumaru_1999_figure3_metadata": [4, 0],
        "matsumaru_1999_figure4_metadata": [0, 0, 0],
        "matsumaru_1999_figure5_metadata": [6, 5, 0, 0],
        "matsumaru_1999_figure6_metadata": [2, 3, 1, 0],
        "matsumaru_1999_figure7_metadata": [2, 0, 0, 0],
        "matsumaru_1999_figure8_metadata": [1, 1, 0, 0, 0, 0],
        "matsumaru_1999_figure9_metadata": [0, 0, 0, 0, 0, 0],
        "matsumaru_1999_figure10_metadata": [0, 0, 0, 0, 0, 0],
    }
    assert all("caption" in row["values"] for row in figures)
    assert all("axes_as_printed" in row["values"] for row in figures)
    assert all(
        {"species", "phase", "temperature"} <= set(row["values"]["identity"])
        for row in figures
    )
    assert all(row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1 for row in figures)


def test_valence_fidelity_and_isolated_migration() -> None:
    document = _document()
    assert check_all_fidelity_samples_match(document, label=SOURCE_ID) == []
    mutated = deepcopy(document)
    mutated["species"]["Fe"]["observations"][0]["values"]["value"] = "0.74"
    assert check_all_fidelity_samples_match(mutated, label=SOURCE_ID)
    assert document["species"]["Fe"]["observations"][0]["point_conditions"][
        "composition"
    ]["state"]["value"]["components"] == FE_GLASS_COMPOSITION_RENDER

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    rows = tuple(migrator.result.observations.values())
    assert len(rows) == 1
    observation = rows[0]
    assert observation.identity.species.formula == "Fe"
    assert observation.identity.quantity.value is Quantity.VALENCE_RATIO
    assert observation.identity.temperature_K is not None
    assert observation.identity.temperature_K.is_unknown
    assert observation.value.point == Decimal("0.75")
    assert observation.point_conditions is not None
    assert observation.point_conditions["temperature_K"].state.is_unknown
    assert observation.point_conditions["temperature_K"].locator.page == 4
    assert observation.point_conditions["time_s"].state.value == Decimal("450")
    assert observation.point_conditions["oxygen_partial_pressure"].state.is_unknown
    assert observation.point_conditions["total_pressure_Pa"].state.is_unknown
    assert observation.point_conditions["quench_state"].state.value == "not_stated"
    composition = observation.point_conditions["composition"]
    assert composition.state.is_value
    assert composition.locator.page == 2
    assert composition.state.value.amount_basis.value == "mole_fraction"
    assert {formula for formula, _amount in composition.state.value.components} == {
        "SiO2", "CaO", "Al2O3", "Fe2O3"
    }
