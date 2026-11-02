from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from tests.battery.test_migrate import _migrate_real_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2022-meteorite-redox-evaporation.yaml"
SOURCE_ID = "shornikov-2022-meteorite-redox-evaporation"

EXPECTED_TABLE1_ROWS = [
    ("1", ("31.6–49.2", "–", "–", "–", "50.8–68.4", "–"), "2278", "Iᵃ", "W", "xᵢ(t,T)", "[21]"),
    ("2", ("50.00", "–", "–", "–", "50.00", "–"), "1791–2398", "IIᵇ", "Mo", "pᵢ, aᵢ", "[22, 23]"),
    ("3", ("6.8–62.1", "–", "–", "–", "37.9–93.2", "–"), "2241–2441", "II", "Mo", "pᵢ, aᵢ", "[24]"),
    ("4", ("–", "22.0–79.5", "–", "–", "–", "21.5–78.0"), "1973", "II", "Pt–Ir, Pt–Rh", "aᵢ", "[42, 43]"),
    ("5", ("–", "66.67", "–", "–", "33.33", "–"), "1873–2323", "I", "W", "p_tot", "[36], [37]"),
    ("6", ("2.58", "33.90", "1.78", "27.95", "–", "33.79"), "1973–2173", "I", "W", "xᵢ(t,T)", "[25]"),
    ("7", ("19.44", "7.26", "18.72", "3.98", "0.25", "50.35"), "1574–2502", "II", "Re", "pᵢ, xᵢ(t,T)", "[27]"),
    ("8", ("1.95", "36.50", "1.47", "20.71", "0.07", "39.31"), "1438–2378", "II", "Re", "pᵢ, xᵢ(t,T)", "[28, 30]"),
    ("9", ("6.51", "47.47", "2.35", "5.15", "–", "38.52"), "1580–2156", "II", "Re", "xᵢ(t,T)", "[29]"),
    ("10", ("15.00", "10.03", "12.22", "6.08", "–", "56.67"), "1749–2063", "II", "Re", "xᵢ(t,T)", "[29]"),
    ("11", ("2.20", "34.82", "1.76", "10.31", "0.07", "50.83"), "1406–2147", "II", "Re", "pᵢ, xᵢ(t,T)", "[30]"),
    ("12", ("3.11", "33.16", "1.77", "29.60", "0.07", "32.30"), "1500–2237", "II", "Re", "pᵢ, xᵢ(t,T)", "[30]"),
    ("13", ("2.08", "35.15", "29.18", "1.38", "0.08", "32.13"), "1673–2273", "I", "W", "xᵢ(t,T)", "[26]"),
    ("14", ("26.76", "17.05", "–", "11.41", "–", "44.78"), "2173", "I", "W", "xᵢ(t,T)", "[31]"),
    ("15", ("2.03", "63.49", "–", "1.40", "–", "33.08"), "2173", "I", "W", "xᵢ(t,T)", "[32]"),
    ("16", ("12.94", "44.39", "–", "8.06", "–", "34.61"), "2173", "I", "W", "xᵢ(t,T)", "[32]"),
    ("17", ("8.68", "45.52", "–", "7.35", "–", "38.45"), "2173", "I", "W", "xᵢ(t,T)", "[33]"),
    ("18", ("23.41", "23.88", "–", "16.44", "–", "36.27"), "2173", "I", "W", "xᵢ(t,T)", "[34]"),
    ("19", ("14.43", "25.60", "–", "23.83", "–", "36.14"), "2173", "I", "W", "xᵢ(t,T)", "[35]"),
]

EXPECTED_K_CELLS = {
    "10": ("–", "7.267 × 10⁻⁹"),
    "11": ("–", "2.372 × 10⁻⁶"),
    "12": ("–", "3.573 × 10⁻⁴"),
    "13": ("–", "1.917 × 10⁻⁷"),
    "14": ("–", "5.351 × 10⁻¹⁰"),
    "15": ("–", "3.197 × 10⁻³"),
    "16": ("–", "2.092 × 10⁻⁸"),
    "17": ("5.102 × 10⁻⁶", "1.872 × 10⁻⁵"),
    "18": ("3.955 × 10⁻¹", "4.920 × 10⁻¹"),
    "19": ("1.848 × 10⁷", "4.637 × 10⁶"),
    "20": ("2.605 × 10¹³", "1.436 × 10¹²"),
    "21": ("1.234 × 10¹⁶", "2.895 × 10¹⁴"),
    "22": ("1.657 × 10⁻⁴", "–"),
    "23": ("1.705 × 10⁻⁴", "–"),
    "24": ("3.038 × 10⁻¹³", "–"),
    "25": ("5.345 × 10⁻¹", "–"),
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _context(document: dict, observation_id: str) -> dict:
    return next(
        record
        for species in document["species"].values()
        for record in species.get("context", [])
        if record["observation_id"] == observation_id
    )


def _assert_render_pins(document: dict) -> None:
    table1 = _context(document, "shornikov_2022_table1_conditions")["values"]
    rows = table1["rows"]
    assert len(rows) == len(EXPECTED_TABLE1_ROWS) == 19
    actual_rows = [
        (
            row["row"],
            tuple(row["composition_mol_percent"][component] for component in ("CaO", "MgO", "FeO", "Al2O3", "TiO2", "SiO2")),
            str(row["T_K_as_printed"]),
            row["experimental_approach_as_printed"],
            row["cell_heater_as_printed"],
            row["research_results_as_printed"],
            row["references_as_printed"],
        )
        for row in rows
    ]
    assert actual_rows == EXPECTED_TABLE1_ROWS
    assert table1["absent_cell_mark_as_printed"] == "–"
    assert rows[0]["experimental_approach_as_printed"] == "Iᵃ"
    assert rows[1]["experimental_approach_as_printed"] == "IIᵇ"
    assert "2023–2323 K" in table1["notes"][0]
    assert "1873–2323 K" in table1["notes"][0]

    table2 = _context(document, "shornikov_2022_table2_linear_reaction_constants_refusal")
    refused = table2["values"]["refused"]
    assert len(refused) == len(EXPECTED_K_CELLS) == 16
    actual_k = {
        row["reaction_number"]: (row["K_2173_as_printed"], row["K_2278_as_printed"])
        for row in refused
    }
    assert actual_k == EXPECTED_K_CELLS
    assert sum(value != "–" for row in actual_k.values() for value in row) == 21
    assert sum(value == "–" for row in actual_k.values() for value in row) == 11
    assert table2["values"]["printed_temperature_columns_K"] == ["2173", "2278"]
    assert table2["locator"] == {"page": 6, "published_page": 355, "table": "2"}

    perovskite_pressure = _context(document, "shornikov_2022_perovskite_component_vapor_pressure_cao")
    forsterite_pressure = _context(document, "shornikov_2022_forsterite_component_vapor_pressure_sio2")
    assert perovskite_pressure["values"]["pressure_value_as_printed"] == "1.514 × 10⁻⁵"
    assert forsterite_pressure["values"]["pressure_value_as_printed"] == "2.771 × 10⁻⁴"
    assert perovskite_pressure["locator"]["page"] == 5
    assert forsterite_pressure["locator"]["page"] == 7
    assert _context(document, "shornikov_2022_perovskite_component_vapor_pressure_tio2")["values"]["pressure_value_as_printed"] == "2.702 × 10⁻⁵"
    assert _context(document, "shornikov_2022_forsterite_component_vapor_pressure_mgo")["values"]["pressure_value_as_printed"] == "5.831 × 10⁻⁵"

    assert document["species"]["CaTiO3"]["observations"][0]["values"]["transition_as_printed"] == "2241 ± 10 K"
    assert document["species"]["Mg2SiO4"]["observations"][0]["values"]["transition_as_printed"] == "2171 ± 11 K"
    neutral_endpoints = _context(
        document, "shornikov_2022_perovskite_neutral_model_endpoints"
    )["values"]["residual_composition_results_as_printed"]
    reducing_endpoints = _context(
        document, "shornikov_2022_perovskite_reducing_model_endpoints"
    )["values"]["residual_composition_results_as_printed"]
    assert neutral_endpoints == [
        "neutral classical Eq. (5): 41.25 mol. % TiO2; then congruent evaporation",
        "neutral with complex gas phase using relations (8) and (9): 44.83 mol. % TiO2; then congruent evaporation",
    ]
    assert reducing_endpoints == [
        "reducing Mo effusion cell: 55.18 mol. % TiO2",
        "reducing W-heated vacuum furnace: 68.30 mol. % TiO2",
    ]
    figures = [
        record
        for species in document["species"].values()
        for record in species.get("context", [])
        if record["type"] == "figure_record"
    ]
    assert len(figures) == 14
    assert all("digitisation" in row["values"] for row in figures)
    figure_13 = _context(document, "shornikov_2022_figure_13")["values"]
    assert figure_13["series_as_printed"][-2:] == ["8: CAI FUN1", "9: CAI FUN2"]
    assert "40% weight loss" in figure_13["qualitative_trend_as_printed"]

    neutral_endpoints = _context(document, "shornikov_2022_perovskite_neutral_model_endpoints")
    reducing_endpoints = _context(document, "shornikov_2022_perovskite_reducing_model_endpoints")
    assert neutral_endpoints["locator"]["page"] == 5
    assert reducing_endpoints["locator"]["page"] == 6
    assert len(neutral_endpoints["values"]["residual_composition_results_as_printed"]) == 2
    assert len(reducing_endpoints["values"]["residual_composition_results_as_printed"]) == 2
    for record in (neutral_endpoints, reducing_endpoints):
        assert record["values"]["initial_composition_locator"] == {
            "page": 4,
            "published_page": 353,
            "table": "1",
            "row": "2",
        }
    forsterite_endpoints = _context(document, "shornikov_2022_forsterite_model_endpoints")
    forsterite_difference = _context(
        document, "shornikov_2022_forsterite_initial_to_final_composition_difference"
    )
    assert forsterite_endpoints["locator"]["page"] == 7
    assert forsterite_difference["locator"]["page"] == 8
    assert "were differed slightly" in forsterite_endpoints["values"]["source_wording_for_67_20"]
    assert forsterite_difference["values"]["initial_to_final_composition_difference_as_printed"] == (
        "does not exceed 0.6 mol. %"
    )

    equations_5 = _context(document, "shornikov_2022_hertz_knudsen_equation_and_no_numeric_rate")
    equations_6_9 = _context(document, "shornikov_2022_partial_pressure_and_flux_relations_no_numeric_rates")
    assert equations_5["locator"]["page"] == 4
    assert equations_5["values"]["equation_as_printed"] == "J = φ a p° / √(2πRTM)"
    assert equations_6_9["locator"]["page"] == 5
    assert equations_6_9["values"]["equations_as_printed"]["equation_8"] == "p ≈ p_d/x_d°"
    assert equations_6_9["values"]["equations_as_printed"]["equation_9"] == (
        "J = φ p / √(2πRTM)"
    )


def _assert_source_observations(document: dict) -> None:
    perovskite = document["species"]["CaTiO3"]["observations"]
    forsterite = document["species"]["Mg2SiO4"]["observations"]
    assert len(perovskite) == 2
    assert len(forsterite) == 1
    expected_temperatures = (
        (perovskite[0], "2241", 5),
        (forsterite[0], "2171", 6),
        (perovskite[1], "2278", 7),
    )
    for row, temperature, condition_page in expected_temperatures:
        assert row["locator"]["page"] == {"2241": 5, "2171": 6, "2278": 6}[temperature]
        point = row["point_conditions"]["temperature_K"]
        assert point["state"]["tag"] == "value"
        assert point["state"]["value"]["point"] == temperature
        assert point["locator"]["page"] == condition_page
        assert "total_pressure_Pa" not in row["point_conditions"]
        assert "fO2_Pa" not in row["point_conditions"]
        assert "carrier_gas" not in row["point_conditions"]
    assert perovskite[0]["values"]["value"] == "2241"
    assert perovskite[0]["uncertainty"]["value"] == "10"
    assert forsterite[0]["values"]["value"] == "2171"
    assert forsterite[0]["uncertainty"]["value"] == "11"
    assert perovskite[1]["values"]["mass_loss_wt_pct"] == ">80"
    assert perovskite[1]["units"] == "wt%"


def _assert_migrated_observations(result) -> None:
    rows = {
        row.observation_id.split("::", 1)[-1]: row
        for row in result.observations.values()
        if row.source_id == SOURCE_ID
    }
    expected = {
        "shornikov_2022_perovskite_melting_point": (Decimal("2241"), 5, Phase.CR),
        "shornikov_2022_forsterite_melting_point": (Decimal("2171"), 6, Phase.CR),
    }
    assert set(rows) == {
        *expected,
        "shornikov_2022_perovskite_residue_mass_loss_bound",
    }
    for observation_id, (value, page, phase) in expected.items():
        row = rows[observation_id]
        assert row.locator.page == page
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == value
        assert row.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE
        assert row.identity.species.phase.value is phase
        assert row.identity.composition.tag.value == "not_applicable"
        assert row.identity.fO2_Pa.tag.value == "not_applicable"
        assert row.identity.temperature_K.tag.value == "not_applicable"
        condition = row.point_conditions["temperature_K"]
        assert condition.state.value == value
        assert condition.locator.page == page
        assert condition.locator is not None
        assert condition.locator.page == page
        assert row.identity.total_pressure_Pa.is_unknown
        assert "total_pressure_Pa" not in row.point_conditions
        assert "fO2_Pa" not in row.point_conditions

    mass_loss = rows["shornikov_2022_perovskite_residue_mass_loss_bound"]
    assert mass_loss.locator.page == 6
    assert mass_loss.value.kind is ValueKind.BOUND
    assert mass_loss.value.bound_value == Decimal("0.8")
    assert mass_loss.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
    assert mass_loss.identity.species.phase.value is Phase.L
    assert mass_loss.identity.composition.is_unknown
    assert mass_loss.identity.temperature_K.value == Decimal("2278")
    assert mass_loss.point_conditions["temperature_K"].state.value == Decimal("2278")
    assert mass_loss.point_conditions["temperature_K"].locator.page == 7
    assert mass_loss.point_conditions["composition"].state.is_unknown
    assert mass_loss.identity.total_pressure_Pa.is_unknown
    assert mass_loss.identity.fO2_Pa.is_unknown
    assert "total_pressure_Pa" not in mass_loss.point_conditions
    assert "fO2_Pa" not in mass_loss.point_conditions
    assert all(row.identity.quantity.tag.value == "value" for row in rows.values())
    assert all(row.identity.species.phase.tag.value == "value" for row in rows.values())


def test_render_cells_conditions_and_migration_are_pinned_and_mutation_sensitive(tmp_path: Path) -> None:
    document = _document()
    _assert_render_pins(document)
    _assert_source_observations(document)
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []

    mutated = deepcopy(document)
    mutated_table1 = _context(mutated, "shornikov_2022_table1_conditions")["values"]["rows"]
    mutated_table1[8]["composition_mol_percent"]["CaO"] = "6.52"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)

    mutated = deepcopy(document)
    mutated_k = _context(mutated, "shornikov_2022_table2_linear_reaction_constants_refusal")["values"]["refused"]
    mutated_k[7]["K_2173_as_printed"] = "5.103 × 10⁻⁶"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)

    mutated = deepcopy(document)
    mutated["species"]["Mg2SiO4"]["observations"][0]["point_conditions"]["temperature_K"]["state"]["value"]["point"] = "2172"
    with pytest.raises(AssertionError):
        _assert_source_observations(mutated)

    result = _migrate_real_extract(
        tmp_path,
        EXTRACT.name,
        write=True,
        extract_doc=document,
    )
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    _assert_render_pins(document)
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []
    _assert_migrated_observations(result)
    observations = list(result.observations.values())
    assert len(observations) == 3
    assert sum(row.value.kind in {ValueKind.POINT, ValueKind.BOUND} for row in observations) == 3
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in observations) == 0

    axes = {
        row.observation_id.rsplit("::", 1)[-1]: {
            "composition": row.identity.composition,
            "total_pressure_Pa": row.identity.total_pressure_Pa,
            "fO2_Pa": row.identity.fO2_Pa,
            "temperature_K": row.identity.temperature_K,
        }
        for row in observations
    }
    assert set(axes) == {
        "shornikov_2022_perovskite_melting_point",
        "shornikov_2022_forsterite_melting_point",
        "shornikov_2022_perovskite_residue_mass_loss_bound",
    }
    for observation_id in (
        "shornikov_2022_perovskite_melting_point",
        "shornikov_2022_forsterite_melting_point",
    ):
        assert axes[observation_id]["composition"].tag.value == "not_applicable"
        assert axes[observation_id]["total_pressure_Pa"].tag.value == "unknown"
        assert axes[observation_id]["fO2_Pa"].tag.value == "not_applicable"
        assert axes[observation_id]["temperature_K"].tag.value == "not_applicable"
    mass_loss_axes = axes["shornikov_2022_perovskite_residue_mass_loss_bound"]
    assert mass_loss_axes["composition"].tag.value == "unknown"
    assert mass_loss_axes["total_pressure_Pa"].tag.value == "unknown"
    assert mass_loss_axes["fO2_Pa"].tag.value == "unknown"
    assert mass_loss_axes["temperature_K"].value == Decimal("2278")
    assert axes["shornikov_2022_perovskite_residue_mass_loss_bound"]["temperature_K"].value == Decimal("2278")

    mutated = deepcopy(document)
    mutated["species"]["CaTiO3"]["observations"][1]["values"]["mass_loss_wt_pct"] = ">81"
    with pytest.raises(AssertionError):
        _assert_migrated_observations(
            _migrate_real_extract(tmp_path / "mutated", EXTRACT.name, write=False, extract_doc=mutated)
        )
