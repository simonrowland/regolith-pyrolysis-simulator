from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal

import pytest
import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT, convert_pressure_to_pa
from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root
from tests.helpers.pdf_text import pdftotext_page_text
from tools.validate_literature_extracts import check_all_fidelity_samples_match


SID = "georges-2000-potassium-evaporation-thesis"
EXTRACT = REPO_ROOT / "data/literature/extracts" / f"{SID}.yaml"
COMPONENTS = ("MgO", "Al2O3", "SiO2", "K2O", "CaO")
POTASSIUM_PRESSURE_QUANTITY = "potassium_partial_pressure_as_published"
ANNEX_III_SAMPLES = {
    "CS": "georges-2000-annex-iii-19CMS-n-198.48-CS-r003",
    "CMS1": "georges-2000-annex-iii-19CMS-n-198.48-CMS1-r005",
    "CMS2": "georges-2000-annex-iii-19CMS-n-198.48-CMS2-r007",
    "CMS3": "georges-2000-annex-iii-19CMS-n-198.48-CMS3-r009",
    "CMS4": "georges-2000-annex-iii-19CMS-n-198.48-CMS4-r011",
}
ANNEX_III_GRID = {
    "CS": {"MgO": "5.77", "Al2O3": "11.60", "SiO2": "36.24", "K2O": "0.49", "CaO": "45.11"},
    "CMS1": {"MgO": "9.18", "Al2O3": "0.02", "SiO2": "42.98", "K2O": "0.70", "CaO": "45.20"},
    "CMS2": {"MgO": "9.70", "Al2O3": "0.01", "SiO2": "47.65", "K2O": "2.08", "CaO": "39.24"},
    "CMS3": {"MgO": "9.38", "Al2O3": "0.02", "SiO2": "52.23", "K2O": "4.38", "CaO": "33.72"},
    "CMS4": {"MgO": "9.17", "Al2O3": "0.02", "SiO2": "55.01", "K2O": "7.44", "CaO": "27.31"},
}


def _annex_iii_grid(document: dict) -> dict[str, dict[str, str]]:
    sample_names = {sample_id: composition for composition, sample_id in ANNEX_III_SAMPLES.items()}
    grid: dict[str, dict[str, str]] = {}
    for row in document["species"]["SiO2"]["observations"]:
        values = row["values"]
        if values["quantity"] != Quantity.EQUILIBRIUM_PHASE_COMPOSITION.value:
            continue
        sample_id = values.get("sample_id")
        if sample_id not in sample_names:
            continue
        composition = sample_names[sample_id]
        formula = values["formula"]
        assert formula in COMPONENTS
        assert values["run_id"] == "19CMS-n"
        assert values["composition_id"] == composition
        assert values["T_C"] == "1410"
        conditions = values["point_conditions"]
        assert conditions["duration_min"]["state"]["value"] == "198.48"
        assert conditions["temperature_C"]["state"]["value"] == "1410"
        assert values["log_fO2"]["value"] == "-13.9"
        grid.setdefault(composition, {})[formula] = values["component_amount"]
    return grid


def _assert_annex_iii_grid(document: dict, expected: dict[str, dict[str, str]]) -> None:
    actual = _annex_iii_grid(document)
    assert actual == expected
    assert all(set(row) == set(COMPONENTS) for row in actual.values())


def _expected_measurements(document: dict) -> Counter:
    expected = Counter()
    for container_formula, container in document["species"].items():
        for row in container.get("observations", []):
            values = row["values"]
            quantity = values["quantity"]
            if quantity == Quantity.RESIDUE_COMPONENT_COMPOSITION.value:
                formula = values["analyte"]
                assert formula == container_formula
                for point in values["points"]:
                    expected[(quantity, formula, Decimal(point[f"{formula}_wt_pct"]), point["locator"]["page"], point["log_fO2"]["value"])] += 1
            elif quantity == Quantity.EQUILIBRIUM_PHASE_COMPOSITION.value:
                formula = values["formula"]
                assert container_formula == "SiO2"
                expected[(quantity, formula, Decimal(values["component_amount"]), row["locator"]["page"], values["log_fO2"]["value"])] += 1
            elif quantity == POTASSIUM_PRESSURE_QUANTITY:
                formula = values["formula"]
                assert container_formula == "K"
                pressure, relation = convert_pressure_to_pa(values["P_K_atm_as_published"], "atm")
                assert relation == "atm_to_Pa"
                expected[("p_partial", formula, pressure, row["locator"]["page"], values["log_fO2"]["value"])] += 1
    return expected


def test_georges_measurements_migrate_with_values_species_temperature_and_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues

    observations = [row for row in result.observations.values() if row.source_id == SID]
    assert len(observations) == 1226
    counts = Counter(row.identity.quantity.value for row in observations)
    assert counts == {
        Quantity.RESIDUE_COMPONENT_COMPOSITION: 616,
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION: 602,
        Quantity.P_PARTIAL: 8,
    }

    migrated = Counter()
    for observation in observations:
        quantity = observation.identity.quantity.value
        formula = observation.identity.species.formula
        assert observation.value.kind is ValueKind.POINT, observation.observation_id
        assert observation.locator is not None
        assert observation.identity.temperature_K.is_value
        assert observation.identity.temperature_K.value == Decimal("1683.15")
        conditions = observation.point_conditions or {}
        temperature = conditions["temperature_K"]
        assert temperature.state.is_value and temperature.state.value == Decimal("1683.15")
        assert temperature.locator is not None
        oxygen = conditions["fO2_log"]
        assert oxygen.state.is_value
        assert oxygen.state.value in {Decimal("-13.9"), Decimal("-16.2")}
        assert oxygen.locator is not None
        assert not any(notice.kind.value == "source_value_outside_physical_range" for notice in observation.notices)
        migrated[(quantity.value, formula, observation.value.point, observation.locator.page, str(oxygen.state.value))] += 1

    assert migrated == _expected_measurements(document)
    assert all(row.identity.species.phase.value == "l" for row in observations if row.identity.quantity.value in {Quantity.RESIDUE_COMPONENT_COMPOSITION, Quantity.EQUILIBRIUM_PHASE_COMPOSITION})
    pressure_rows = [row for row in observations if row.identity.quantity.value is Quantity.P_PARTIAL]
    assert len(pressure_rows) == 8
    assert pressure_rows[0].value.point == Decimal("99.602475")
    assert all(row.identity.species.formula == "K" for row in pressure_rows)
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []

    figures = [
        row
        for container in document["species"].values()
        for row in container.get("context", [])
        if row.get("type") == "figure_record"
    ]
    assert len(figures) == 8
    assert all(row["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all(row["values"]["numeric_points"] == [] for row in figures)


def test_annex_iii_table_grid_is_render_pinned_and_both_mutations_fail() -> None:
    pdf = regolith_corpus_root() / "raw" / SID / f"{SID}.pdf"
    if not pdf.is_file():
        pytest.skip(f"corpus PDF unavailable: {pdf}")

    page = pdftotext_page_text(pdf, 209)
    assert "19CMS-n" in page
    assert "CS" in page and "CMS2" in page
    assert "5.77" in page

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_annex_iii_grid(document, ANNEX_III_GRID)

    changed_grid = deepcopy(ANNEX_III_GRID)
    changed_grid["CMS2"]["SiO2"] = "999"
    with pytest.raises(AssertionError):
        _assert_annex_iii_grid(document, changed_grid)

    changed_extract = deepcopy(document)
    changed_row = next(
        row
        for row in changed_extract["species"]["SiO2"]["observations"]
        if row["observation_id"] == "georges_2000_annex_iii_r007_sio2"
    )
    changed_row["values"]["component_amount"] = "999"
    with pytest.raises(AssertionError):
        _assert_annex_iii_grid(changed_extract, ANNEX_III_GRID)

    for page_number, expected_labels in {
        79: ("Tableau", "1410°C"),
        83: ("Tableau", "1410°C"),
        203: ("Expérience",),
        204: ("Expérience",),
        205: ("Expérience",),
        206: ("Expérience",),
        208: ("ANNEXE III", "300 cc/min"),
        209: ("Expérience",),
        210: ("Expérience",),
        211: ("Expérience",),
        212: ("Expérience",),
    }.items():
        page_text = pdftotext_page_text(pdf, page_number)
        assert all(label in page_text for label in expected_labels), page_number
