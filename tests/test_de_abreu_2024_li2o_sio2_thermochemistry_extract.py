"""Source-pinned extraction checks for de Abreu and Fabrichnaya (2024)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "de-abreu-2024-li2o-sio2-thermochemistry"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"
CORPUS_ROOT = Path(
    os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
)
PDF = CORPUS_ROOT / "raw" / SOURCE_ID / f"{SOURCE_ID}.pdf"

# Independent transcription of Table 2 from the 600 dpi render on physical PDF pages 5–6.
# Empty strings are the visibly blank table cells; dashes remain literal dashes.
RENDER_TABLE2_GRID = [
    ("LSO_1", "0.20", "0.80", "1073 K/8 h", "Li8SiO6", "62.2", "5.4179", "", "10.6507", ""),
    ("LSO_1", "0.20", "0.80", "1073 K/8 h", "Li4SiO4", "37.8", "11.4244", "6.1667", "16.6477", "98.5"),
    ("LSO_1", "0.20", "0.80", "1173 K/4 h", "Li4SiO4", "100.0", "11.4301", "6.1535", "16.7126", "98.5"),
    ("LSO_2", "0.17", "0.83", "1073 K/8 h", "Li8SiO6", "63.4", "5.4211", "", "10.6454", ""),
    ("LSO_2", "0.17", "0.83", "1073 K/8 h", "Li4SiO4", "36.6", "11.4291", "6.1676", "16.6618", "98.5"),
    ("LSO_2", "0.17", "0.83", "1173 K/4 h", "Li4SiO4", "100.0", "11.4208", "6.1642", "16.7116", "98.5"),
    ("LSO_3", "0.13", "0.87", "1073 K/8 h", "Li8SiO6", "65.9", "5.4216", "", "10.6472", ""),
    ("LSO_3", "0.13", "0.87", "1073 K/8 h", "Li4SiO4", "34.1", "11.4252", "6.1664", "16.6618", "98.5"),
    ("LSO_4", "0.40", "0.60", "1273 K/2 h", "Li2SiO3", "43.2", "9.4042", "5.4010", "4.6629", ""),
    ("LSO_4", "0.40", "0.60", "1273 K/2 h", "Li4SiO4", "56.8", "11.4586", "6.1436", "16.6299", "98.6"),
    ("LSO_5", "0.45", "0.55", "1273 K/2 h", "Li2SiO3", "47.5", "9.3937", "5.3940", "4.6614", ""),
    ("LSO_5", "0.45", "0.55", "1273 K/2 h", "Li4SiO4", "51.1", "11.4293", "6.1348", "16.7132", "98.2"),
    ("LSO_5", "0.45", "0.55", "1288 K/2 h", "Li2SiO3", "43.57", "9.3914", "5.3962", "4.6624", ""),
    ("LSO_5", "0.45", "0.55", "1288 K/2 h", "Li4SiO4", "56.43", "11.4261", "6.1504", "16.6805", "98.4"),
    ("LSO_5", "0.45", "0.55", "melted - DTA", "Li2SiO3", "-", "-", "-", "-", ""),
    ("LSO_5", "0.45", "0.55", "melted - DTA", "Li4SiO4", "-", "-", "-", "-", ""),
    ("LSO_7", "0.35", "0.65", "1273 K/2 h", "Li2SiO3", "32.0", "9.3976", "5.3991", "4.6655", ""),
    ("LSO_7", "0.35", "0.65", "1273 K/2 h", "Li4SiO4", "68.0", "11.4266", "6.1680", "16.6824", "98.4"),
    ("LSO_7", "0.35", "0.65", "1288 K/2 h", "Li2SiO3", "31.9", "9.3976", "5.3991", "4.6655", ""),
    ("LSO_7", "0.35", "0.65", "1288 K/2 h", "Li4SiO4", "68.1", "11.4266", "6.1680", "16.6824", "98.4"),
]

EXPECTED_FORMULA_BY_OBSERVATION_ID = {
    "deabreu2024_table5_li4sio4_delta_fH_1": "Li4SiO4",
    "deabreu2024_table5_li2sio3_delta_fH_2": "Li2SiO3",
    "deabreu2024_table5_li2sio3_delta_fH_3": "Li2SiO3",
    "deabreu2024_table5_li2sio3_delta_fH_4": "Li2SiO3",
    "deabreu2024_table5_li2sio3_delta_fH_5": "Li2SiO3",
    "deabreu2024_table5_li2sio3_S_7": "Li2SiO3",
    "deabreu2024_table5_li2si2o5_delta_fH_6": "Li2Si2O5",
    "deabreu2024_table5_li2si2o5_S_8": "Li2Si2O5",
    "deabreu2024_figure3_li4sio4_second_order_transition": "Li4SiO4",
}
EXPECTED_FORMULA_BY_OBSERVATION_ID.update(
    {f"deabreu2024_table6_g6_r{i}": "Li4SiO4" for i in range(1, 5)}
)
EXPECTED_FORMULA_BY_OBSERVATION_ID.update(
    {f"deabreu2024_table6_g7_r{i}": "Li2SiO3" for i in range(1, 7)}
)
EXPECTED_FORMULA_BY_OBSERVATION_ID.update(
    {f"deabreu2024_table6_g8_r{i}": "Li2Si2O5" for i in range(1, 4)}
)
EXPECTED_FORMULA_BY_OBSERVATION_ID.update(
    {f"deabreu2024_table6_g9_r{i}": "Li2Si2O5" for i in range(1, 5)}
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _contexts(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["context"]
    }


def _flatten_table2_grid(document: dict) -> list[tuple[str, ...]]:
    context = _contexts(document)["deabreu2024_table2_xrd_rietveld_sample_grid"]
    flattened = []
    for sample in context["values"]["samples"]:
        nominal = sample["nominal_composition_mole_fraction"]
        for treatment in sample["conditions"]:
            for phase in treatment["phase_rows"]:
                lattice = phase["lattice_parameters_A_as_printed"]
                flattened.append(
                    (
                        sample["sample_id"],
                        nominal["SiO2"],
                        nominal["Li2O"],
                        treatment["annealing_as_printed"],
                        phase["phase_as_printed"],
                        phase["volume_fraction_percent_as_printed"],
                        lattice["a"],
                        lattice["b"],
                        lattice["c"],
                        phase["beta_degrees_as_printed"],
                    )
                )
    return flattened


def _assert_grid_matches_render(document: dict, expected=RENDER_TABLE2_GRID) -> None:
    actual = _flatten_table2_grid(document)
    assert len(actual) == 20
    assert actual == expected


def test_extract_validates_and_keeps_every_table_disposition() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=SOURCE_ID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    context = _contexts(document)
    assert len(context["deabreu2024_table1_crystallographic_data"]["values"]["rows"]) == 11
    assert context["deabreu2024_table2_xrd_rietveld_sample_grid"]["values"]["phase_row_count"] == 20
    assert context["deabreu2024_table3_thermodynamic_models"]["values"]["as_printed"].startswith("Table 3.")
    assert context["deabreu2024_table4_thermodynamic_functions"]["values"]["as_printed"].startswith("Table 4.")
    assert context["deabreu2024_table5_optimized_thermochemistry"]["values"]["model_row_count"] == 16
    assert context["deabreu2024_table6_calphad_optimized_transition_rows"]["values"]["optimized_row_count"] == 18
    figures = [row for row in context.values() if row["type"] == "figure_record"]
    assert len(figures) == 20
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("points" not in row["values"] for row in figures)
    assert all(
        "marker_count" in series
        for row in figures
        for series in row["values"]["series"]
    )


def test_table2_grid_matches_independent_render_transcription() -> None:
    _assert_grid_matches_render(_document())


def test_mutating_either_independent_grid_or_extract_cell_is_detected() -> None:
    document = _document()
    mutated_render_grid = deepcopy(RENDER_TABLE2_GRID)
    mutated_render_grid[0] = (*mutated_render_grid[0][:5], "62.3", *mutated_render_grid[0][6:])
    with pytest.raises(AssertionError):
        _assert_grid_matches_render(document, mutated_render_grid)

    mutated_extract = deepcopy(document)
    sample = mutated_extract["species"]["SiO2"]["context"]
    table = next(row for row in sample if row["observation_id"] == "deabreu2024_table2_xrd_rietveld_sample_grid")
    table["values"]["samples"][0]["conditions"][0]["phase_rows"][0]["volume_fraction_percent_as_printed"] = "62.3"
    with pytest.raises(AssertionError):
        _assert_grid_matches_render(mutated_extract)


def test_measured_table_values_and_render_pins_are_preserved() -> None:
    document = _document()
    contexts = _contexts(document)
    obs = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["observations"]
    }
    assert obs["deabreu2024_table5_li4sio4_delta_fH_1"]["values"]["delta_f_H_as_printed"] == "-2328.9"
    assert obs["deabreu2024_table5_li2sio3_delta_fH_2"]["values"]["delta_f_H_as_printed"] == "-1646.1"
    assert obs["deabreu2024_table5_li2si2o5_S_8"]["values"]["entropy_as_printed"] == "122.2"
    assert obs["deabreu2024_table6_g8_r2"]["values"]["T_K"] == "1215"
    assert obs["deabreu2024_figure3_li4sio4_second_order_transition"]["values"]["T_K"] == "873.4"
    g8 = obs["deabreu2024_table6_g8_r2"]["values"]
    assert g8["table_type_as_printed"] == "Congruent"
    assert "discussion calls this reaction polymorphic" in g8["classification_note"]
    assert "Table 6 prints Congruent" in g8["classification_note"]
    assert contexts["deabreu2024_table6_g2_r4"]["values"]["same_measurement_also_reported_as"]["value_as_printed"] == "1289 K"
    for observation_id in EXPECTED_FORMULA_BY_OBSERVATION_ID:
        assert obs[observation_id]["values"].get("formula") == EXPECTED_FORMULA_BY_OBSERVATION_ID[observation_id]
    for observation_id in (
        *(f"deabreu2024_table6_g6_r{i}" for i in range(1, 5)),
        *(f"deabreu2024_table6_g7_r{i}" for i in range(1, 7)),
        *(f"deabreu2024_table6_g9_r{i}" for i in range(1, 5)),
    ):
        assert obs[observation_id]["phase"] == "cr"
    for observation_id, row in obs.items():
        if observation_id.startswith("deabreu2024_table5_"):
            assert "standard_pressure_Pa" not in row["values"]
            assert "standard_pressure_Pa" not in row.get("point_conditions", {})
    assert "1 bar reference" in document["extraction"]["method"]


def test_multiphase_transition_rows_are_retained_in_context_only() -> None:
    expected = {
        "deabreu2024_table6_g1_r1": (
            "1263", "Liq. ⇌ Li2O + Li4SiO4", "DTA", "Liquid + Li2O + Li4SiO4"
        ),
        "deabreu2024_table6_g2_r1": (
            "1297", "Liq. ⇌ Li4SiO4 + Li2SiO3", "DTA", "Liquid + Li4SiO4 + Li2SiO3"
        ),
        "deabreu2024_table6_g2_r2": (
            "1297", "Liq. ⇌ Li4SiO4 + Li2SiO3", "DTA", "Liquid + Li4SiO4 + Li2SiO3"
        ),
        "deabreu2024_table6_g2_r4": (
            "1289.4", "Liq. ⇌ Li4SiO4 + Li2SiO3", "DTA", "Liquid + Li4SiO4 + Li2SiO3"
        ),
        "deabreu2024_table6_g3_r1": (
            "1307", "Liq. ⇌ Li2SiO3 + h-Li2Si2O5", "DTA", "Liquid + Li2SiO3 + h-Li2Si2O5"
        ),
        "deabreu2024_table6_g4_r1": (
            "1301", "Liq. ⇌ h-Li2Si2O5 + Trd-SiO2", "quenching method", "Liquid + h-Li2Si2O5 + tridymite-SiO2"
        ),
        "deabreu2024_table6_g5_r1": (
            "1103", "Li2O + Li4SiO4 ⇌ Li8SiO6", "DTA", "Li2O + Li4SiO4 + Li8SiO6"
        ),
    }
    document = _document()
    contexts = _contexts(document)
    observed_ids = {
        row["observation_id"]
        for species in document["species"].values()
        for row in species["observations"]
    }
    for observation_id, (temperature, reaction, technique, assemblage) in expected.items():
        assert observation_id not in observed_ids
        row = contexts[observation_id]
        values = row["values"]
        assert values["quantity_token"] == "unavailable"
        assert values["candidate_tokens_considered"] == ["transition_temperature"]
        assert values["temperature_as_printed"] == temperature
        assert values["phase_reaction_as_printed"] == reaction
        assert values["technique_as_printed"] == technique
        assert values["assemblage_as_printed"] == assemblage
    optimized_ids = set(
        contexts["deabreu2024_table6_calphad_optimized_transition_rows"]["values"][
            "typed_observation_ids"
        ]
    )
    assert optimized_ids == {
        observation_id
        for observation_id in EXPECTED_FORMULA_BY_OBSERVATION_ID
        if observation_id.startswith("deabreu2024_table6_g")
    }


def test_isolated_migration_preserves_formula_temperature_conditions_and_values() -> None:
    document = _document()
    expected_by_id = {
        row["observation_id"]: (
            EXPECTED_FORMULA_BY_OBSERVATION_ID[row["observation_id"]],
            row["values"]["T_K"],
        )
        for species in document["species"].values()
        for row in species["observations"]
    }
    assert set(expected_by_id) == set(EXPECTED_FORMULA_BY_OBSERVATION_ID)
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    records = list(migrator.result.observations.values())
    assert len(records) == 26
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    assert sum(len(rows) for rows in migrator.result.context_by_work.values()) == 33
    assert all(record.value.kind is not ValueKind.UNAVAILABLE for record in records)
    for record in records:
        raw_id = record.observation_id.split("::")[-1]
        expected_formula, expected_temperature = expected_by_id[raw_id]
        source_observation = next(
            row
            for species in document["species"].values()
            for row in species["observations"]
            if row["observation_id"] == raw_id
        )
        assert source_observation["values"].get("formula") == expected_formula
        assert record.identity.species.formula == expected_formula
        temperature = record.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal(str(expected_temperature))
        assert temperature.locator is not None
        assert temperature.locator.page == record.locator.page
        assert record.value.kind.value == "point"
        assert record.identity.quantity.value in {
            Quantity.DELTA_FH,
            Quantity.S,
            Quantity.TRANSITION_TEMPERATURE,
        }


def test_each_table_and_figure_locator_resolves_on_its_physical_pdf_page() -> None:
    if not PDF.is_file():
        pytest.skip(f"corpus source PDF is absent: {PDF}")

    labels_by_page = {
        3: [r"Table\s*1"],
        5: [r"Table\s*2"],
        6: [r"Table\s*2\.\s*Cont"],
        7: [r"Figure\s*1"],
        8: [r"Figure\s*2"],
        9: [r"Figure\s*3"],
        10: [r"Figure\s*4"],
        11: [r"Table\s*3"],
        12: [r"Table\s*3\.\s*Cont", r"Table\s*4"],
        13: [r"Table\s*5"],
        14: [r"Figure\s*5", r"Table\s*6"],
        15: [r"Table\s*6\.\s*Cont", r"Figure\s*6", r"Figure\s*7"],
    }
    for page, patterns in labels_by_page.items():
        result = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert all(re.search(pattern, result.stdout, re.IGNORECASE) for pattern in patterns), page
