"""Regression pins for the printed Tables 2 and 3 in Gorojovsky and Wood (2026)."""

from __future__ import annotations

import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Phase, PhaseRole, Quantity, ValueKind
from simulator.battery.migrate import _log_fO2_to_pa
from tests.battery.test_migrate import REPO_ROOT, _migrate_real_extract
from tests.helpers.corpus_paths import regolith_corpus_root


SOURCE_ID = "gorojovsky-wood-2026-sulfur-crustal"
EXTRACT_NAME = f"{SOURCE_ID}.yaml"
EXTRACT_PATH = REPO_ROOT / "data" / "literature" / "extracts" / EXTRACT_NAME
PDF_NAME = f"{SOURCE_ID}.pdf"

# These values were read from the 600 dpi renders, independently of the extract generator.
RENDER_PINS = (
    ("t2-r001-capacity", Quantity.SULFIDE_CAPACITY, Decimal("-6.2")),
    ("t2-r001-sio2", Quantity.EQUILIBRIUM_PHASE_COMPOSITION, Decimal("53.0")),
    ("t2-r005-tio2", Quantity.EQUILIBRIUM_PHASE_COMPOSITION, Decimal("0.74")),
    ("t3-r057-k2o", Quantity.EQUILIBRIUM_PHASE_COMPOSITION, Decimal("3.3")),
)


def _source_doc() -> dict:
    doc = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def _row(result, raw_id: str):
    matches = [
        observation
        for observation in result.observations.values()
        if observation.observation_id.endswith(f"::{raw_id}")
    ]
    assert len(matches) == 1, (
        raw_id,
        [observation.observation_id for observation in result.observations.values()],
        [(entry.observation_id, entry.why) for entry in result.queue],
    )
    return matches[0]


def test_table_rows_migrate_and_pin_render_cells(tmp_path: Path) -> None:
    doc = _source_doc()
    result = _migrate_real_extract(tmp_path / "migration", EXTRACT_NAME)
    assert result.validation is not None
    assert not result.validation.hard_issues, result.validation.hard_issues

    all_rows = list(result.observations.values())
    assert len(all_rows) == 983
    capacities = [
        observation
        for observation in all_rows
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.SULFIDE_CAPACITY
    ]
    phase_rows = [
        observation
        for observation in all_rows
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    ]
    assert len(capacities) == 47
    assert len(phase_rows) == 936

    migrated_by_raw_id = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in all_rows
    }
    experiment_by_id = {
        experiment["experiment_id"]: experiment for experiment in doc["experiments"]
    }
    raw_observations = {
        raw["observation_id"]: (formula, raw)
        for formula, container in doc["species"].items()
        for raw in container.get("observations", [])
    }
    assert len(raw_observations) == len(all_rows)

    for raw_id, (formula, raw) in raw_observations.items():
        observation = migrated_by_raw_id[raw_id]
        assert observation.identity.species.formula == raw["values"].get("formula", formula)
        assert observation.identity.temperature_K.is_value, observation.observation_id
        assert observation.identity.temperature_K.value == Decimal(raw["values"]["T_C"]) + Decimal("273.15")
        assert observation.identity.total_pressure_Pa.is_value, observation.observation_id
        assert observation.identity.total_pressure_Pa.value == Decimal("101325")
        assert observation.identity.fO2_Pa.is_value, observation.observation_id
        assert observation.identity.fO2_Pa.value == _log_fO2_to_pa(
            Decimal(raw["values"]["point_conditions"]["fO2_log"]["state"]["value"]),
            Decimal("100000"),
        )
        printed_oxygen = observation.point_conditions["fO2_log"]
        assert printed_oxygen.state.is_value
        assert printed_oxygen.state.value == Decimal(raw["values"]["point_conditions"]["fO2_log"]["state"]["value"])
        assert printed_oxygen.locator is not None
        sample = experiment_by_id[raw["experiment"]].get("sample", {})
        if "printed_composition" in sample:
            assert observation.identity.composition.is_value, raw_id
        else:
            assert observation.identity.composition.is_unknown, raw_id

    for suffix, quantity, expected in RENDER_PINS:
        observation = _row(result, suffix)
        assert observation.identity.quantity.value is quantity
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == expected

    first_capacity = _row(result, "t2-r001-capacity")
    assert first_capacity.identity.species.formula == "S"
    assert first_capacity.identity.phase_role.value is PhaseRole.MELT
    assert first_capacity.identity.temperature_K.value == Decimal("1323.15")
    assert first_capacity.identity.fO2_Pa.value == _log_fO2_to_pa(
        Decimal("-11.2"), Decimal("100000")
    )
    assert first_capacity.evidence.class_.value is EvidenceClass.AUTHOR_ESTIMATE
    assert first_capacity.evidence.model == "author_derived"
    assert first_capacity.derivation is not None
    assert "equation 3, as printed" in first_capacity.derivation.relation
    assert first_capacity.identity.species.phase.value is Phase.L

    first_silica = _row(result, "t2-r001-sio2")
    assert first_silica.identity.species.formula == "SiO2"
    assert first_silica.identity.species.phase.value is Phase.GLASS
    assert first_silica.identity.phase_role.value is PhaseRole.GLASS
    assert first_silica.identity.subtype.value == "wt_percent"
    assert first_silica.experiment_id == first_capacity.experiment_id

    printed_logs = first_silica.point_conditions["fO2_log"]
    assert printed_logs.state.is_value
    assert printed_logs.state.value == Decimal("-11.2")

    row5 = _row(result, "t2-r005-tio2")
    source_row5 = next(
        item
        for item in doc["species"]["TiO2"]["observations"]
        if item["observation_id"] == "t2-r005-tio2"
    )
    assert source_row5["values"]["unparenthesized_adjacent_numeric_cell"] == "0.23"
    assert row5.identity.species.formula == "TiO2"
    assert row5.value.point == Decimal("0.74")

    # Every emitted row is bound to the printed run and carries temperature and fO2.
    assert all(
        observation.experiment_id is not None
        and observation.identity.temperature_K.is_value
        and observation.identity.fO2_Pa.is_value
        for observation in all_rows
    )

    figures = [
        context
        for container in doc["species"].values()
        for context in container.get("context", [])
        if context.get("type") == "figure_only"
    ]
    assert len(figures) == 7
    assert all(
        figure["values"]["digitisation"] == "deferred to a figure pass"
        and "numeric_points" not in figure["values"]
        and figure["locator"]["page"] == figure["locator"]["pdf_page_index"] + 1
        for figure in figures
    )

    source_context = doc["species"]["S"]["context"]
    sulfate_capacities = [
        context
        for context in source_context
        if context["observation_id"].endswith("-sulfate-capacity-no-token")
    ]
    assert len(sulfate_capacities) == 57
    assert sulfate_capacities[0]["values"]["value_as_published"] == "9.7"
    workbook_records = [
        context
        for context in source_context
        if context["observation_id"] == "gw2026-s1-lasso-fit-matrix"
    ]
    assert len(workbook_records) == 1
    workbook = workbook_records[0]
    assert workbook["locator"]["record"].startswith(
        "S1_Calibration_data_and_fitting_results.xlsx"
    )
    assert all(
        cell_range in workbook["locator"]["record"]
        for cell_range in (
            "Info!A1:I22",
            "S1_logCS2_data_for_fitting!A1:V307",
            "S2_logCS6_data_for_fitting!A1:N404",
            "S3-6_Fitting_results_summary!A1:U386",
        )
    )
    assert workbook["values"]["sheets"][:3] == [
        {"name": "Info"},
        {
            "name": "S1_logCS2_data_for_fitting",
            "data_rows": 305,
            "study_row_counts": {"GW": 48, "BW": 61, "OM": 196},
        },
        {
            "name": "S2_logCS6_data_for_fitting",
            "data_rows": 402,
            "study_row_counts": {"GW": 51, "BW": 82, "OM": 269},
        },
    ]
    assert workbook["values"]["discrepancies"]["sulfide_capacity"]["extra_workbook_row"] == 9
    assert workbook["values"]["discrepancies"]["sulfate_capacity"][
        "table_rows_missing_from_GW_fit"
    ] == [f"t3-r{index:03d}" for index in range(43, 50)]


def test_physical_page_locators_match_source_when_corpus_is_available() -> None:
    corpus = regolith_corpus_root()
    pdf = corpus / "raw" / SOURCE_ID / PDF_NAME
    if not pdf.is_file():
        pytest.skip(f"source PDF is absent: {pdf}")

    pages = (
        (4, "sulfide capacity"),
        (8, "at atmospheric pressure"),
        (9, "Figure 1."),
        (12, "Figure 2."),
        (17, "Figure 3."),
        (19, "Figure 4."),
        (22, "Figure 5."),
        (25, "Figure 6."),
        (26, "Figure 7."),
        (39, "Table 1."),
        (40, "Table 2."),
        (41, "Table 3."),
    )
    for page, label in pages:
        result = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert label.lower() in result.stdout.lower(), (page, label)
