"""Render pins and isolated migration for Frischat & Oel (1966)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, ValueKind
from simulator.battery.migrate import PHASE_MAP
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "frischat-oel-1966-sodium-calcium-silicate-transport"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
TABLE2_ID = "frischat_oel_1966_table2_na_self_diffusion_results"
TABLE1_ID = "frischat_oel_1966_table1_composition_analyses"

# Independently transcribed from the page 54 PDF render after checking the
# MinerU/pdftotext reads. Columns follow the printed left/right layout.
RENDERED_TABLE2 = (
    ("207", "1,23·10⁻¹⁴", "1,15·10⁻¹⁴", "512", "1,48·10⁻¹⁰", None),
    ("305", "1,98·10⁻¹²", "2,70·10⁻¹²", "520", None, "1,29·10⁻¹⁰"),
    ("305", "2,17·10⁻¹²", None, "575", "2,73·10⁻¹⁰", "3,38·10⁻¹⁰"),
    ("375", "2,10·10⁻¹¹", "1,99·10⁻¹¹", "633", "1,83·10⁻⁹", None),
    ("425", None, "1,07·10⁻¹⁰", "643", None, "1,21·10⁻⁹"),
    ("435", "0,92·10⁻¹⁰", "1,21·10⁻¹⁰", "701", None, "2,81·10⁻⁹"),
    ("435", "1,09·10⁻¹⁰", "1,37·10⁻¹⁰", "775", None, "1,11·10⁻⁸"),
    ("517", "8,77·10⁻¹⁰", "9,39·10⁻¹⁰", "790", "1,14·10⁻⁸", None),
    ("520", None, "9,65·10⁻¹⁰", "885", None, "4,49·10⁻⁸"),
    ("575", "3,67·10⁻⁹", "4,11·10⁻⁹", None, None, None),
    ("575", None, "4,70·10⁻⁹", None, None, None),
    ("633", "1,22·10⁻⁸", "1,62·10⁻⁸", None, None, None),
)

RENDERED_TABLE1 = {
    "Sollwerte": ("17,49", "31,65", "50,86", None),
    "1": ("17,55 (+0,06)", "31,67 (+0,02)", "50,55 (−0,31)", "0,15"),
    "2": ("17,80 (+0,31)", "30,98 (−0,33)", "51,18 (+0,32)", "0,16"),
    "3": ("18,89 (+1,40)", "31,11 (−0,54)", "49,98 (−0,88)", "0,10"),
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _context(document: dict, observation_id: str) -> dict:
    records = document["species"]["Na"]["context"]
    return next(record for record in records if record["observation_id"] == observation_id)


def _actual_table2_grid(document: dict) -> tuple[tuple[str | None, ...], ...]:
    rows = _context(document, TABLE2_ID)["values"]["rows"]
    return tuple(
        (
            row["glass"]["T_C"],
            row["glass"].get("column_beta_plus_D_as_published"),
            row["glass"].get("column_gamma_D_as_published"),
            row["polycrystal"].get("T_C"),
            row["polycrystal"].get("column_beta_plus_D_as_published"),
            row["polycrystal"].get("column_gamma_D_as_published"),
        )
        for row in rows
    )


def _assert_rendered_tables(document: dict) -> None:
    table2 = _context(document, TABLE2_ID)
    assert table2["locator"]["page"] == 5
    assert table2["locator"]["table"] == "2"
    assert len(table2["values"]["rows"]) == 12
    assert _actual_table2_grid(document) == RENDERED_TABLE2

    table1 = _context(document, TABLE1_ID)
    assert table1["locator"]["page"] == 2
    assert table1["locator"]["table"] == "1"
    values = table1["values"]
    target = values["target_composition_as_printed"]
    analyses = {
        sample["sample_as_printed"]: sample
        for sample in values["analyzed_compositions"]
    }
    actual = {
        "Sollwerte": (
            target["Na2O"],
            target["CaO"],
            target["SiO2"],
            None if target["R2O3"] == "em_dash" else target["R2O3"],
        ),
    }
    for sample_id, row in analyses.items():
        actual[sample_id] = tuple(
            f"{row[oxide]['value_as_printed']} {row[oxide]['deviation_as_printed']}"
            if "deviation_as_printed" in row[oxide]
            else row[oxide]
            for oxide in ("Na2O", "CaO", "SiO2", "R2O3")
        )
    assert actual == RENDERED_TABLE1


def test_rendered_table_cells_and_context_only_migration() -> None:
    document = _document()
    assert validate_extract_file(EXTRACT) == []
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    migrated = migrator.result.observations
    assert len(migrated) == 0
    assert migrator.result.validation.hard_issues == ()
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated.values()) == 0
    assert {
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.quantity.is_unknown
    } == set()
    assert {
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.species is None or not row.identity.species.phase.is_value
    } == set()

    _assert_rendered_tables(document)
    rows = _context(document, TABLE2_ID)["values"]["rows"]
    assert sum(
        value is not None
        for row in _actual_table2_grid(document)
        for value in row[1:3] + row[4:]
    ) == 30
    assert sum(row[0] is not None for row in _actual_table2_grid(document)) == 12
    assert sum(row[3] is not None for row in _actual_table2_grid(document)) == 9
    assert len(document["typed_absences"]) == 22

    diffusion = _context(document, TABLE2_ID)["values"]
    assert diffusion["uncertainty_as_printed"] == "Die Fehler betragen etwa ± 10 bis 15%."
    assert diffusion["uncertainty_locator"]["page"] == 11
    assert diffusion["overall_reported_D_interval_as_printed"] == "10⁻¹⁴ bis 10⁻⁷ cm²/s"
    assert diffusion["overall_reported_D_interval_locator"]["published_page"] == 60
    prose_restatement = diffusion["prose_restatement"]
    assert prose_restatement["value_as_printed"] == "etwa 4·10⁻⁹ cm²/s"
    assert prose_restatement["T_C"] == "575"
    assert prose_restatement["phase"] == "glass"
    assert prose_restatement["locator"]["page"] == 7

    for row in rows:
        for phase_data, d_keys in (
            (row["glass"], ("column_beta_plus_D_as_published", "column_gamma_D_as_published")),
            (row["polycrystal"], ("column_beta_plus_D_as_published", "column_gamma_D_as_published")),
        ):
            if any(phase_data.get(key) is not None for key in d_keys):
                assert phase_data.get("T_C") is not None
            expected_phase = Phase.GLASS if phase_data["phase"] == "glass" else Phase.CR
            assert PHASE_MAP[phase_data["phase"]] is expected_phase

    conditions = _context(document, TABLE2_ID)["values"]["conditions_as_published"]
    assert conditions["composition"]["state"]["tag"] == "value"
    assert conditions["total_pressure_Pa"]["state"] == "unknown"
    assert conditions["fO2_Pa"]["state"] == "unknown"


def test_mutating_a_rendered_value_is_detected() -> None:
    document = _document()
    _assert_rendered_tables(document)

    mutated = deepcopy(document)
    mutated_row = _context(mutated, TABLE2_ID)["values"]["rows"][0]
    mutated_row["glass"]["column_beta_plus_D_as_published"] = "1,24·10⁻¹⁴"
    with pytest.raises(AssertionError):
        _assert_rendered_tables(mutated)

    mutated = deepcopy(document)
    sample_two = next(
        row
        for row in _context(mutated, TABLE1_ID)["values"]["analyzed_compositions"]
        if row["sample_as_printed"] == "2"
    )
    sample_two["CaO"]["deviation_as_printed"] = "(−0,34)"
    with pytest.raises(AssertionError):
        _assert_rendered_tables(mutated)


def test_fit_transference_and_phase_locators_are_render_pinned() -> None:
    document = _document()
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert migrator.result.validation.hard_issues == ()

    glass_fit = _context(document, "frischat_oel_1966_glass_na_arrhenius_fit")
    assert glass_fit["locator"]["page"] == 5
    assert glass_fit["locator"]["published_page"] == 54
    assert glass_fit["values"]["D0_as_printed"] == "3,67·10⁻² cm²/s"
    assert glass_fit["values"]["activation_energy_as_printed"] == "27,5 kcal/Mol (1,19 eV)"
    assert glass_fit["values"]["fit_temperature_range_C_as_printed"] == "200 bis 600 °C"

    poly_fit = _context(document, "frischat_oel_1966_polycrystal_na_arrhenius_fit")
    assert poly_fit["locator"]["page"] == 5
    assert "table" not in poly_fit["locator"]
    assert poly_fit["values"]["D0_as_printed"] == "1,88·10⁻² cm²/s"
    assert poly_fit["values"]["activation_energy_as_printed"] == "29,9 kcal/Mol (1,30 eV)"
    assert poly_fit["values"]["fit_temperature_range_C_as_printed"] == "500 bis 900 °C"

    conductivity = _context(document, "frischat_oel_1966_conductivity_arrhenius_fits")
    assert conductivity["locator"]["page"] == 11
    assert conductivity["locator"]["published_page"] == 60
    assert conductivity["values"]["fits"][0]["expression_as_printed"] == (
        "σ = 6,98·10² exp[−25,9 kcal/RT] Ω⁻¹ cm⁻¹"
    )
    assert conductivity["values"]["fits"][1]["expression_as_printed"] == (
        "σ = 89,9 exp[−26,2 kcal/RT] Ω⁻¹ cm⁻¹"
    )

    transference = _context(document, "frischat_oel_1966_quoted_sodium_transference_number")
    assert transference["locator"]["page"] == 7
    assert "table" not in transference["locator"]
    assert transference["values"]["value_as_printed"] == "tNa+ = 1"
    assert "[23]" in transference["values"]["attribution_as_printed"]

    factor = _context(document, "frischat_oel_1966_conclusion_correlation_factors")
    assert factor["locator"]["page"] == 10
    assert factor["values"]["glass_value_as_printed"] == "0,16"
    assert factor["values"]["polycrystal_value_as_printed"] == "0,20"

    summary_factor = _context(document, "frischat_oel_1966_summary_correlation_factor")
    assert summary_factor["locator"]["page"] == 11
    assert summary_factor["values"]["value_as_printed"] == "f ≈ 0,2"

    detailed_factor = _context(document, "frischat_oel_1966_detailed_correlation_factors")
    assert detailed_factor["locator"]["page"] == 9
    assert detailed_factor["locator"]["published_page"] == 58
    assert detailed_factor["values"]["glass"]["temperature_range_C_as_printed"] == (
        "270 bis 600 °C"
    )
    assert detailed_factor["values"]["glass"]["value_as_printed"] == "0,16"
    assert detailed_factor["values"]["glass"]["interval_as_printed"] == (
        "0,15 bis 0,17"
    )
    assert detailed_factor["values"]["polycrystal"]["temperature_range_C_as_printed"] == (
        "500 bis 900 °C"
    )
    assert detailed_factor["values"]["polycrystal"]["value_as_printed"] == "0,20"
    assert detailed_factor["values"]["polycrystal"]["interval_as_printed"] == (
        "0,16 bis 0,24"
    )
    assert detailed_factor["values"]["confidence_level"] == "not_stated"

    figure10 = _context(document, "frischat_oel_1966_figure10_diffusion_conductivity_candidate")
    assert figure10["locator"]["page"] == 9
    assert figure10["locator"]["figure"] == "10"
    assert figure10["values"]["legend_as_printed"] == [
        "1: gemessen",
        "2: aus σ berechnet",
        "3: gemessen",
        "4: aus σ berechnet",
    ]
    assert figure10["values"]["digitisation"] == "deferred; no numeric points stored"
