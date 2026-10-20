from __future__ import annotations

from copy import deepcopy
import re
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Quantity
from tests.battery.test_migrate import _migrate_real_extract
from tests.literature_extract_test_helpers import iter_locators, source_page_text
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "romero-serrano-1999-binary-ternary-silicates"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _invariant_rows(document: dict) -> list[dict]:
    return document["species"]["SiO2"]["observations"]


def test_four_cited_invariant_temperatures_keep_partial_compositions() -> None:
    document = _document()
    rows = _invariant_rows(document)
    assert [row["values"]["points"][0]["T_C"] for row in rows] == [1480, 1185, 1470, 1305]
    assert [row["values"]["attribution"] for row in rows] == [
        "Glasser and Osborn [19]",
        "Riboud and Muan [20]",
        "Osborn and Muan [22]",
        "Osborn and Muan [22]",
    ]
    expected_partial = [
        {"SiO2": "55", "MnO": "25"},
        {"SiO2": "39.9", "MnO": "23.9"},
        {"SiO2": "56.4", "FeO": "20.0"},
        {"SiO2": "45.3", "FeO": "45.7"},
    ]
    for row, printed_components in zip(rows, expected_partial, strict=True):
        point = row["values"]["points"][0]
        assert point["composition_as_printed_partial_wt_percent"] == printed_components
        condition = point["point_conditions"]["composition"]
        assert condition["state"]["tag"] == "unknown"
        assert "omitted" in condition["state"]["reason"]
        assert "remainder is inferred" in condition["state"]["reason"]
        assert row["values"]["method_class"] == "quoted_attributed"
        assert row["values"]["evidence_class"] == "quoted_attributed"

    changed = deepcopy(document)
    changed["species"]["SiO2"]["observations"][0]["values"]["points"][0]["T_C"] = 1481
    assert check_all_fidelity_samples_match(changed, label=SOURCE_ID)


def test_every_locator_is_physical_and_cited_text_is_on_that_page() -> None:
    document = _document()
    assert all(
        locator["page"] == locator["pdf_page_index"] + 1
        for locator in iter_locators(document)
        if "page" in locator and "pdf_page_index" in locator
    )

    page6 = source_page_text(SOURCE_ID, 6)
    assert "1480" in page6 and "Glasser and Osborn" in page6
    page7 = source_page_text(SOURCE_ID, 7)
    assert "I 185" in page7 and "Riboud and Muan" in page7
    page8 = source_page_text(SOURCE_ID, 8)
    assert "1470" in page8 and "1305" in page8 and "Osborn and Muan" in page8

    figure_page_markers = {
        3: ("Optimized MgO", "Optimized FeO"),
        4: ("Optimized MnO", "Optimized MgO"),
        5: ("Optimized FeO-MnO", "Fig."),
        6: ("Optimized Si02", "Fig."),
        7: ("Glasser", "standard state"),
        8: ("Bowen", "Optimized Si02"),
        9: ("Fig.", "Gibbs free energy"),
    }
    for page, markers in figure_page_markers.items():
        text = re.sub(r"\s+", " ", source_page_text(SOURCE_ID, page))
        for marker in markers:
            assert marker.lower() in text.lower()

    table_page = source_page_text(SOURCE_ID, 2)
    assert "Table l." in table_page and "ThermodynamicProperties" in table_page


def test_isolated_migration_keeps_species_temperature_and_unknown_conditions(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, EXTRACT.name)
    assert result.validation is not None
    assert not result.validation.hard_issues

    rows = [
        row
        for row in result.observations.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE
    ]
    assert len(rows) == 4
    expected_temperatures = {1480, 1185, 1470, 1305}
    expected_by_observation = {
        "romero_1999_glasser_osborn_invariant_a": 1480,
        "romero_1999_riboud_muan_invariant_a": 1185,
        "romero_1999_osborn_muan_invariant_a": 1470,
        "romero_1999_osborn_muan_invariant_b": 1305,
    }
    assert {
        int(row.point_conditions["T_C"].state.value)
        for row in rows
        if row.point_conditions is not None
        and row.point_conditions["T_C"].state.is_value
    } == expected_temperatures

    for row in rows:
        assert row.identity.species.formula == "SiO2"
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value.value == "l"
        assert row.point_conditions is not None
        conditions = row.point_conditions
        assert conditions["T_C"].state.is_value
        assert conditions["T_C"].locator is not None
        assert conditions["T_C"].locator.page == conditions["T_C"].locator.pdf_page_index + 1
        source_observation_id = row.observation_id.split("::")[1]
        assert int(conditions["T_C"].state.value) == expected_by_observation[source_observation_id]
        assert conditions["composition"].state.is_unknown
        assert conditions["total_pressure_Pa"].state.is_unknown
        assert conditions["fO2_log"].state.is_unknown
        assert row.evidence.class_.is_value
        assert row.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED


def test_table_and_figure_context_preserve_dispositions_without_digitized_points() -> None:
    context = _document()["species"]["SiO2"]["context"]
    table1 = next(item for item in context if item["observation_id"] == "romero_1999_table_1_pure_phase_model_inputs")
    assert table1["candidate_tokens_considered"] == ["delta_fH", "S", "cp"]
    assert table1["values"]["numeric_rows_printed"] == 20
    assert "not duplicated" in table1["reason_unscored"]

    figures = [item for item in context if item["type"] == "figure_record"]
    assert len(figures) == 13
    assert all(
        item["values"]["digitisation"] == "deferred to a figure pass"
        and item["values"]["numeric_points"] == "none"
        and "species" in item["values"]
        and "phase" in item["values"]
        and "temperature_as_printed" in item["values"]
        and "axes_as_printed" in item["values"]
        and "caption" in item["values"]
        and "series" in item["values"]
        for item in figures
    )
    assert all(
        "marker_count" in series
        for figure in figures
        for series in figure["values"]["series"]
    )
