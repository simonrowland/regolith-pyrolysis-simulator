from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal

import pytest
import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root
from tests.helpers.pdf_text import pdftotext_page_text
from tools.validate_literature_extracts import check_all_fidelity_samples_match


SID = "zhang-2024-alkali-evaporation-ti-thesis"
EXTRACT = REPO_ROOT / "data/literature/extracts" / f"{SID}.yaml"

TABLE_3_4_GRID = {
    "HCHMg 2-2": {"Li2O":"0.93","Na2O":"0.85","MgO":"13.01","Al2O3":"13.98","SiO2":"64.06","K2O":"0.43","CaO":"7.01","Cs2O":"0.66","Rb2O":"0.33"},
    "HCHMg 1-8": {"Li2O":"0.97","Na2O":"0.46","MgO":"13.18","Al2O3":"14.32","SiO2":"63.96","K2O":"0.36","CaO":"7.15","Cs2O":"0.58","Rb2O":"0.29"},
    "HCHMg 1-6": {"Li2O":"0.93","Na2O":"0.38","MgO":"13.27","Al2O3":"14.28","SiO2":"63.74","K2O":"0.36","CaO":"7.31","Cs2O":"0.66","Rb2O":"0.26"},
    "HCHMg 1-7": {"Li2O":"0.85","Na2O":"0.12","MgO":"13.14","Al2O3":"14.27","SiO2":"64.58","K2O":"0.26","CaO":"7.20","Cs2O":"0.43","Rb2O":"0.19"},
    "HCHMg 2-3": {"Li2O":"0.85","Na2O":"0.11","MgO":"13.17","Al2O3":"14.32","SiO2":"64.61","K2O":"0.23","CaO":"7.20","Cs2O":"0.36","Rb2O":"0.17"},
}


def _table34_grid(document: dict) -> dict[str, dict[str, str]]:
    grid: dict[str, dict[str, str]] = {}
    typed = {"MgO", "Al2O3", "SiO2", "CaO"}
    for formula in typed:
        observation = next(
            row for row in document["species"][formula]["observations"]
            if row["observation_id"] == f"zhang_2024_table_3_4_{formula.lower()}_residue_composition"
        )
        for point in observation["values"]["points"]:
            grid.setdefault(point["sample_id"], {})[formula] = point[f"{formula}_wt_pct"]
    unsupported = next(
        row for row in document["species"]["SiO2"]["context"]
        if row["observation_id"] == "zhang_2024_table_3_4_unsupported_residue_components"
    )
    for point in unsupported["values"]["points"]:
        grid.setdefault(point["sample_id"], {}).update(point["composition_wt_pct_as_published"])
    return grid


def _assert_table34_grid(document: dict, expected: dict[str, dict[str, str]]) -> None:
    assert _table34_grid(document) == expected


def test_zhang_thesis_residues_migrate_with_species_temperature_and_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues, result.validation.hard_issues
    source_observations = [row for row in result.observations.values() if row.source_id == SID]
    assert len(source_observations) == 257
    counts = Counter((row.identity.species.formula, row.identity.quantity.value) for row in source_observations)
    assert sum(n for (_, quantity), n in counts.items() if quantity is Quantity.RESIDUE_COMPONENT_COMPOSITION) == 220
    assert sum(n for (_, quantity), n in counts.items() if quantity is Quantity.ISOTOPE_DELTA) == 37
    expected_points = Counter()
    for formula, container in document["species"].items():
        for row in container["observations"]:
            quantity = row["values"]["quantity"]
            for point in row["values"].get("points", []):
                if quantity == Quantity.RESIDUE_COMPONENT_COMPOSITION.value:
                    expected_points[(formula, Decimal(point[f"{formula}_wt_pct"]))] += 1
                elif quantity == Quantity.ISOTOPE_DELTA.value:
                    expected_points[(formula, Decimal(point["isotope_delta_isotope_delta"]))] += 1
    migrated_points = Counter(
        (row.identity.species.formula, row.value.point)
        for row in source_observations
        if row.value.kind is ValueKind.POINT
    )
    assert migrated_points == expected_points
    for observation in source_observations:
        assert observation.value.kind is ValueKind.POINT, observation.observation_id
        assert observation.identity.species.formula
        assert observation.identity.temperature_K is not None
        assert observation.identity.temperature_K.is_value
        assert not any(notice.kind.value == "source_value_outside_physical_range" for notice in observation.notices)
        conditions = observation.point_conditions or {}
        assert "temperature_K" in conditions, observation.observation_id
        assert conditions["temperature_K"].state.is_value
        assert conditions["temperature_K"].locator is not None
        assert conditions["temperature_K"].state.value == observation.identity.temperature_K.value
        assert "duration_min" in conditions, observation.observation_id
        assert conditions["duration_min"].state.is_value
        assert conditions["duration_min"].locator is not None

    for formula, container in document["species"].items():
        for row in container["observations"]:
            assert row["values"].get("analyte", formula) == formula
            assert row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1
            for point in row["values"].get("points", []):
                assert point["locator"]["page"] == point["locator"]["pdf_page_index"] + 1
                assert point["point_conditions"]["temperature_C"]["state"]["value"] == point["T_C"]
                assert point["point_conditions"]["duration_min"]["state"]["tag"] == "value"

    # Pressure bounds from Table 2.4 stay bounds, while the vacuum method's approximate pressure stays approximate.
    t21 = next(
        row["values"]["points"] for row in document["species"]["MgO"]["observations"]
        if row["observation_id"] == "zhang_2024_table_2_1_mgo_residues"
    )
    pressure = t21[0]["point_conditions"]["chamber_pressure_Pa"]["state"]["value"]
    assert pressure == {"kind": "bound", "bound_operator": "<", "bound_value": "0.0001"}
    vacuum_point = next(
        point for row in document["species"]["MgO"]["observations"]
        if row["observation_id"] == "zhang_2024_table_3_4_mgo_residue_composition"
        for point in row["values"]["points"]
    )
    assert vacuum_point["point_conditions"]["total_pressure_Pa"]["state"]["value"]["approximate"] is True
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []


def test_table_3_4_full_grid_is_render_pinned_and_both_mutations_fail() -> None:
    pdf = regolith_corpus_root() / "raw" / SID / f"{SID}.pdf"
    if not pdf.is_file():
        pytest.skip(f"corpus PDF unavailable: {pdf}")
    page = pdftotext_page_text(pdf, 165)
    assert "Table 3.4." in page
    assert "HCHMg 2-2" in page and "HCHMg 2-3" in page
    assert "64.06" in page and "14.32" in page and "13.68" in page

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_table34_grid(document, TABLE_3_4_GRID)

    changed_grid = deepcopy(TABLE_3_4_GRID)
    changed_grid["HCHMg 1-8"]["Na2O"] = "999"
    with pytest.raises(AssertionError):
        _assert_table34_grid(document, changed_grid)

    changed_extract = deepcopy(document)
    sample = changed_extract["species"]["SiO2"]["context"]
    points = next(
        row["values"]["points"] for row in sample
        if row["observation_id"] == "zhang_2024_table_3_4_unsupported_residue_components"
    )
    next(point for point in points if point["sample_id"] == "HCHMg 1-8")["composition_wt_pct_as_published"]["Na2O"] = "999"
    with pytest.raises(AssertionError):
        _assert_table34_grid(changed_extract, TABLE_3_4_GRID)


def test_context_links_prior_paper_and_figures_remain_deferred() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table21 = [row for row in document["species"]["SiO2"]["context"]
               if row["observation_id"].startswith("zhang_2024_table_2_1_bas")]
    assert len(table21) == 12
    linked = [row for row in table21 if row["values"].get("related_observation_id")]
    assert len(linked) == 10
    assert all(row["type"] == "context_link" for row in linked)
    figures = [row for container in document["species"].values() for row in container["context"]
               if row["type"] == "figure_record"]
    assert figures
    for record in figures:
        assert record["digitisation"] == "deferred to a figure pass"
        assert record["values"]["numeric_points"] == []
        assert record["values"]["series"]
        assert all(series["marker_count"] >= 0 for series in record["values"]["series"])
