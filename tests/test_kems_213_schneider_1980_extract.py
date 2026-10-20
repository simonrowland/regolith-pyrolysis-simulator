from __future__ import annotations

import os
import subprocess
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity
from simulator.battery.migrate import Migrator, REPO_ROOT
from tests.literature_extract_test_helpers import iter_locators


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/kems-213-schneider-1980-nbsir-80-2139.yaml"
)
SOURCE_ID = "kems-213-schneider-1980-nbsir-80-2139"
CORPUS_ROOT = Path(
    os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
)
PDF = CORPUS_ROOT / "raw" / SOURCE_ID / f"{SOURCE_ID}.pdf"

# Independent transcription from the 600 dpi render of Table 1, physical PDF
# page 8. Table 1 reports starting composition and listed mineral components.
TABLE1_RENDER = (
    (
        "KCAS-VP-1",
        (("K2O", "14.8"), ("CaO", "27.5"), ("Al2O3", "22.2"), ("SiO2", "35.5")),
        "Mechanical mixture of KAlSiO4, Ca2SiO4, Ca3Si2O7, and Ca2Al2SiO7",
    ),
    (
        "KCAS-VP-2",
        (("K2O", "11.5"), ("CaO", "17.6"), ("Al2O3", "33.7"), ("SiO2", "37.2")),
        "Mechanical mixture of KAlSiO4, KAlSi2O6, Ca2Al2SiO7, and CaAl2Si2O8",
    ),
    (
        "KCAS-VP-3",
        (("K2O", "14.8"), ("CaO", "24.3"), ("Al2O3", "16.0"), ("SiO2", "44.9")),
        "Mechanical mixture of KAlSiO4 and CaSiO3",
    ),
)

# Independent transcription from the 600 dpi render of Table 2, physical PDF
# page 9. The fitted coefficients and their standard deviations stay in context.
TABLE2_RENDER = (
    ("KCAS-VP-1", "3/13/80", "14.8–12.4", "-20047", "391", "8.144", ".255"),
    ("KCAS-VP-1", "3/14/80", "12.4–9.3", "-19071", "524", "7.278", ".234"),
    ("KCAS-VP-1", "3/18/80", "9.3–7.2", "-17429", "452", "6.058", ".289"),
    ("KCAS-VP-1", "3/18/80", "6.2–4.8", "-16372", "210", "5.068", ".129"),
    ("KCAS-VP-1", "3/21/80", "4.8–4.0", "-16896", "279", "5.289", ".178"),
    ("KCAS-VP-2", "5/14/80", "11.4–10.7", "-14632", "434", "4.200", ".278"),
    ("KCAS-VP-2", "5/15/80", "10.7–8.6", "-16366", "180", "5.097", ".112"),
    ("KCAS-VP-2", "5/16/80", "8.6–6.5", "-17571", "257", "5.656", ".158"),
    ("KCAS-VP-2", "5/19/80", "6.5–6.2", "-16606", "526", "4.992", ".321"),
)

# Independent transcription from the 600 dpi render of Table 3, physical PDF
# page 10. Each row gives approximate mole-percent components of a binary melt.
TABLE3_RENDER = (
    ("KAlSiO4/CaSiO3", (("KAlSiO4", "35"), ("CaSiO3", "65"))),
    ("KAlSiO4/CaAl2Si2O8", (("KAlSiO4", "19"), ("CaAl2Si2O8", "81"))),
    ("KAlSiO4/Ca2Al2SiO7", (("KAlSiO4", "37"), ("Ca2Al2SiO7", "63"))),
    ("KAlSiO4/Ca2SiO4", (("KAlSiO4", "17"), ("Ca2SiO4", "83"))),
    ("KAlSi2O6/CaSiO3", (("KAlSi2O6", "39"), ("CaSiO3", "61"))),
    ("KAlSi2O6/Ca2Al2SiO7", (("KAlSi2O6", "43"), ("Ca2Al2SiO7", "57"))),
    ("KAlSi2O6/CaAl2Si2O8", (("KAlSi2O6", "30"), ("CaAl2Si2O8", "70"))),
)


def _document() -> dict:
    loaded = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _table1_grid(document: dict) -> tuple:
    rows = []
    for experiment in document["experiments"]:
        sample = experiment["sample"]
        composition = sample["initial_composition"]["state"]["value"]["components"]
        rows.append(
            (
                experiment["locator"]["row"],
                tuple((name, str(amount)) for name, amount in composition),
                sample["form"]["state"]["value"],
            )
        )
    return tuple(rows)


def _table2_rows(document: dict) -> tuple:
    table = document["species"]["K"]["context"][0]
    return tuple(
        (
            row["sample_id"],
            row["date"],
            row["K2O_wt_pct_range"],
            row["A_as_printed"],
            row["SD_A_as_printed"],
            row["B_as_printed"],
            row["SD_B_as_printed"],
        )
        for row in table["values"]["rows"]
    )


def _table3_grid(document: dict) -> tuple:
    rows_by_pair: dict[str, list[tuple[str, str]]] = {}
    for observation in document["species"]["SiO2"]["observations"]:
        if observation["locator"].get("table") != "3":
            continue
        rows_by_pair.setdefault(observation["locator"]["row"], []).append(
            (observation["values"]["formula"], observation["values"]["value"])
        )
    return tuple(
        (row, tuple(rows_by_pair[row]))
        for row, _components in TABLE3_RENDER
    )


def _assert_table3_matches(expected: tuple, actual: tuple) -> None:
    assert actual == expected


def test_render_transcriptions_pin_all_three_tables() -> None:
    document = _document()
    assert _table1_grid(document) == TABLE1_RENDER
    assert _table2_rows(document) == TABLE2_RENDER
    _assert_table3_matches(TABLE3_RENDER, _table3_grid(document))
    table3_rows = document["species"]["SiO2"]["observations"]
    assert all(
        row["values"]["approximate"] is True
        for row in table3_rows
        if row.get("type") == "equilibrium_phase_composition"
    )
    liquid_onset = table3_rows[0]
    assert liquid_onset["values"]["points"][0]["T_C"] == "1310"
    assert liquid_onset["values"]["property_kind"] == (
        "temperature at which a liquid phase first forms"
    )


def test_table3_render_pin_rejects_a_changed_cell() -> None:
    document = deepcopy(_document())
    target = next(
        row
        for row in document["species"]["SiO2"]["observations"]
        if row.get("type") == "equilibrium_phase_composition"
    )
    changed_render_pin = list(TABLE3_RENDER)
    changed_render_pin[0] = (
        "KAlSiO4/CaSiO3",
        (("KAlSiO4", "34"), ("CaSiO3", "65")),
    )
    with pytest.raises(AssertionError):
        _assert_table3_matches(tuple(changed_render_pin), _table3_grid(document))

    target["values"]["value"] = "36"
    with pytest.raises(AssertionError):
        _assert_table3_matches(TABLE3_RENDER, _table3_grid(document))


def test_every_typed_component_keeps_its_species_and_conditions_in_migration() -> None:
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 15
    assert not result.validation.hard_issues
    assert not result.registry_issues
    assert all(row.value.kind.value == "point" for row in result.observations.values())
    source_rows_by_id = {
        observation["observation_id"]: (formula, observation)
        for formula, block in _document()["species"].items()
        for observation in block.get("observations", [])
    }
    for observation in result.observations.values():
        row_id = observation.observation_id.rsplit("::", 1)[-1]
        if observation.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE:
            filing_formula, source_row = next(
                (formula, row)
                for formula, row in source_rows_by_id.values()
                if row["type"] == "transition_point"
            )
        else:
            filing_formula, source_row = source_rows_by_id[row_id]
        assert observation.identity.species.formula == source_row["values"].get(
            "formula", filing_formula
        )

    composition_rows = [
        observation
        for observation in result.observations.values()
        if observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    ]
    assert len(composition_rows) == 14
    for observation in composition_rows:
        row_id = observation.observation_id.rsplit("::", 1)[-1]
        _filing_formula, source_row = source_rows_by_id[row_id]
        assert observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert observation.identity.species.phase.value is Phase.L
        assert observation.identity.temperature_K.is_unknown
        assert observation.point_conditions is not None
        conditions = observation.point_conditions
        assert conditions["temperature_K"].state.is_unknown
        assert conditions["temperature_K"].locator is not None
        assert conditions["composition"].state.is_value
        assert conditions["composition"].locator is not None
        assert conditions["assemblage_id"].state.is_value
        assert conditions["full_assemblage"].state.is_value

    liquid_onset = next(
        observation
        for observation in result.observations.values()
        if observation.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE
    )
    assert liquid_onset.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE
    assert liquid_onset.point_conditions is not None
    temperature = liquid_onset.point_conditions["temperature_K"]
    assert temperature.state.is_value
    assert temperature.state.value == Decimal("1583.15")
    assert temperature.locator is not None and temperature.locator.page == 6
    assert liquid_onset.point_conditions["composition"].state.is_unknown


@pytest.mark.skipif(not PDF.is_file(), reason="regolith-corpus source PDF is absent")
def test_liquid_onset_locator_cites_the_page_with_the_1310_c_sentence() -> None:
    document = _document()
    liquid_onset = next(
        observation
        for observation in document["species"]["SiO2"]["observations"]
        if observation["observation_id"] == "schneider_1980_kcas_vp_1_liquid_phase_first_forms"
    )
    locator = liquid_onset["locator"]
    assert (locator["page"], locator["pdf_page_index"], locator["published_page"]) == (
        6,
        5,
        2,
    )
    page_text = {}
    for page in (6, 7):
        result = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        page_text[page] = result.stdout
    assert "1310 °C" in page_text[6]
    assert "1310 °C" not in page_text[7]


def test_figure_records_are_deferred_and_locators_use_physical_pages() -> None:
    document = _document()
    figures = [
        row
        for block in document["species"].values()
        for row in block.get("context", [])
        if row.get("type") == "figure_record"
    ]
    assert len(figures) == 4
    expected_counts = {
        "1": (16, 0, 0),
        "2": (8, 6, 0, 0),
        "3": (0, 17, 0),
        "4": (2, 0, 0),
    }
    for figure in figures:
        values = figure["values"]
        assert values["quantity"] == "partial_pressure"
        assert values["gas_species"] == "K"
        assert values["formula"] == "K"
        assert values["phase"] == "gas"
        assert values["temperature_as_printed"]
        assert values["axes_as_printed"]
        assert values["caption_as_printed"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert "points" not in values
        assert tuple(series["marker_count"] for series in values["series"]) == expected_counts[
            figure["locator"]["figure"]
        ]

    locators = list(iter_locators(document))
    assert locators
    assert all(locator["page"] == locator["pdf_page_index"] + 1 for locator in locators)
    assert all(locator["published_page"] == locator["page"] - 4 for locator in locators)


def test_qualitative_activity_reference_phase_remains_explicitly_unknown() -> None:
    document = _document()
    record = next(
        item
        for item in document["species"]["K"]["context"]
        if item["observation_id"] == "schneider_1980_qualitative_k2o_activity_effect"
    )
    reference = record["values"]["activity_reference_state"]
    assert reference["state"] == "unknown"
    assert "no reference measurement temperature" in reference["reason"]
