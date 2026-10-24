from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import os
from pathlib import Path
import subprocess

import pytest
import yaml

from simulator.battery.enums import FO2Channel, Phase, Quantity, QuenchState, UncertaintyKind, ValueKind
from simulator.battery.migrate import Migrator
from tests.literature_extract_test_helpers import iter_locators


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "borisov-2017-network-modifiers-fe-redox"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
RENDER_GRID = ROOT / "tests/fixtures/borisov_2017_table1_render_grid.psv"
OXIDES = ("SiO2", "Al2O3", "Fe2O3", "MgO", "CaO", "Na2O", "K2O")


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _render_grid() -> list[dict[str, str]]:
    # Independently transcribed from the 600 dpi renders of physical PDF pages 4–5.
    lines = RENDER_GRID.read_text(encoding="utf-8").splitlines()
    headers = lines[0].split("|")
    return [dict(zip(headers, line.split("|"), strict=True)) for line in lines[1:]]


def _assert_table1_grid(document: dict, expected: list[dict[str, str]]) -> None:
    observations = {
        item["values"]["row_as_printed"]: item
        for item in document["species"]["Fe"]["observations"]
        if item["observation_id"].startswith("borisov_2017_table1_fe3_fe2_row_")
    }
    assert len(expected) == 54
    assert len(observations) == 54
    inherited_t: str | None = None
    inherited_duration: str | None = None
    active_group: str | None = None
    for expected_row in expected:
        row_number = expected_row["row"]
        observation = observations[row_number]
        values = observation["values"]
        assert values["sample_as_published"] == expected_row["sample"]
        assert values["T_cell_as_published"] == expected_row["T_cell"]
        assert values["duration_cell_as_published"] == expected_row["duration_cell"]
        if expected_row["T_cell"] != '"':
            inherited_t = expected_row["T_cell"]
        if expected_row["duration_cell"] != '"':
            inherited_duration = expected_row["duration_cell"]
        if expected_row["T_cell"] != '"' or expected_row["duration_cell"] != '"':
            active_group = expected_row["sample"].rsplit("-", 1)[-1]
        assert inherited_t is not None and inherited_duration is not None
        assert active_group is not None
        assert values["T_C"] == inherited_t
        assert values["duration_h"] == inherited_duration
        assert observation["experiment"] == f"borisov_2017_table1_run_{active_group}"
        assert values["composition_wt_pct_as_published"] == {
            oxide: expected_row[oxide] for oxide in OXIDES
        }
        assert values["Total_wt_pct_as_published"] == expected_row["Total"]
        assert values["value"] == expected_row["Fe3+/Fe2+"]
        assert values["composition_status"] == "unknown_incomplete_as_published"
        assert values["composition_missing_components"]
        assert observation["locator"]["page"] == (4 if int(row_number) <= 49 else 5)
        assert observation["locator"]["pdf_page_index"] == observation["locator"]["page"] - 1
        assert observation["locator"]["row"] == row_number
        if expected_row["T_cell"] == '"':
            assert "ditto mark" in observation["point_conditions"]["T_C"]["locator"]["note"]
        if expected_row["duration_cell"] == '"':
            assert "ditto mark" in observation["point_conditions"]["duration_h"]["locator"]["note"]


def test_render_grid_binds_all_table1_cells_and_catches_both_mutations() -> None:
    document = _document()
    expected = _render_grid()
    _assert_table1_grid(document, expected)

    grid_mutation = deepcopy(expected)
    grid_mutation[0]["SiO2"] = "56.17"
    with pytest.raises(AssertionError):
        _assert_table1_grid(document, grid_mutation)

    extract_mutation = deepcopy(document)
    first_row = next(
        item for item in extract_mutation["species"]["Fe"]["observations"]
        if item["observation_id"].endswith("_row_01_haf_6")
    )
    first_row["values"]["composition_wt_pct_as_published"]["SiO2"] = "56.17"
    with pytest.raises(AssertionError):
        _assert_table1_grid(extract_mutation, expected)


def test_migration_types_all_rows_and_binds_temperature_atmosphere_and_quench() -> None:
    document = _document()
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    records = list(result.observations.values())
    assert len(records) == 58
    assert not result.registry_issues

    source_observations = {
        item["observation_id"]: item
        for body in document["species"].values()
        for item in body.get("observations", [])
    }
    assert all(record.identity.species.formula == "Fe" for record in records)
    assert all(record.identity.quantity.value is Quantity.VALENCE_RATIO for record in records)
    assert all(record.value.kind is ValueKind.POINT for record in records)

    experimental = [record for record in records if "table1_fe3_fe2_row_" in record.observation_id]
    assert len(experimental) == 54
    for record in experimental:
        source = source_observations[record.observation_id.split("::", 1)[-1]]
        values = source["values"]
        conditions = record.point_conditions
        assert conditions is not None
        for key in ("T_C", "duration_h", "atmosphere", "fO2_log", "total_pressure_Pa", "composition", "quench_state"):
            assert key in conditions and conditions[key].locator is not None
        assert record.identity.temperature_K.value == Decimal(values["T_C"]) + Decimal("273.15")
        assert record.identity.total_pressure_Pa.value == Decimal("101325")
        assert record.identity.fO2_Pa.is_unknown
        assert conditions["T_C"].state.value == Decimal(values["T_C"])
        assert conditions["duration_h"].state.value == Decimal(values["duration_h"])
        assert conditions["atmosphere"].state.value == "air"
        assert conditions["fO2_log"].state.is_unknown
        assert conditions["total_pressure_Pa"].state.value == Decimal("101325")
        assert conditions["composition"].state.is_unknown
        assert record.identity.composition.is_unknown
        assert conditions["quench_state"].state.value is QuenchState.NOT_STATED
        assert record.quench_state is QuenchState.NOT_STATED
        assert record.identity.species.phase.value is Phase.L
        assert record.uncertainty.kind is UncertaintyKind.PRINTED
        assert "approximately ±0.08" in record.uncertainty.verbatim["verbatim"]

    standards = [record for record in records if "pu3_" in record.observation_id]
    assert len(standards) == 4
    assert sum(record.evidence.class_.value.value == "measured_direct" for record in standards) == 3
    quoted = next(record for record in standards if "schuessler" in record.observation_id)
    assert quoted.evidence.class_.value.value == "quoted_attributed"
    assert quoted.uncertainty.kind is UncertaintyKind.PRINTED
    assert "1σ" in quoted.uncertainty.verbatim["verbatim"]


def test_fugacity_channel_and_context_records_are_not_observations() -> None:
    document = _document()
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    experiments = [
        experiment for experiment in migrator.result.experiments.values()
        if "::experiment::borisov_2017_table1_run_" in experiment.experiment_id
    ]
    assert len(experiments) == 7
    assert all(experiment.fO2_control is not None for experiment in experiments)
    assert all(experiment.fO2_control.channel.value is FO2Channel.GAS_MIX for experiment in experiments)

    contexts = document["species"]["Fe"]["context"]
    model_tables = [item for item in contexts if item.get("type") == "model_fit_context"]
    assert {item["locator"]["table"] for item in model_tables} == {"2", "3"}
    assert all(item["values"]["method_class"] == "model_derived" for item in model_tables)
    figures = [item for item in contexts if item.get("type") == "figure_record"]
    assert len(figures) == 8
    for figure in figures:
        values = figure["values"]
        assert values["quantity"]
        assert values["identity"]["species"]
        assert values["identity"]["phase"] == "l"
        assert values["identity"]["temperature_as_printed"]
        assert values["axes_as_printed"]
        assert values["caption_as_printed"]
        assert values["series"]
        assert all(isinstance(item["marker_count"], int) for item in values["series"])
        assert values["digitisation"] == "deferred to a figure pass"
        assert "points" not in values
        if values["quantity"] == "unavailable":
            assert values["schema_gap"]


def test_all_locators_use_one_based_physical_pages() -> None:
    locators = list(iter_locators(_document()))
    assert locators
    assert all(locator["page"] == locator["pdf_page_index"] + 1 for locator in locators)


def test_cited_source_pages_are_present_and_skip_when_corpus_is_absent() -> None:
    corpus = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))
    pdf = corpus / "raw" / SOURCE_ID / f"{SOURCE_ID}.pdf"
    if not pdf.is_file():
        pytest.skip(f"corpus PDF absent: {pdf}")

    pages = {}
    for page in (3, 4, 5, 11, 12):
        completed = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        pages[page] = completed.stdout

    assert "one atmosphere" in pages[3].lower()
    assert "quenched by quickly withdrawing" in pages[3]
    assert "1.52 ± 0.08" in pages[3]
    assert "1.60 ± 0.11" in pages[3]
    assert "Table 1" in pages[4] and "1.152" in pages[4]
    assert "Table 1" in pages[5] and "2.112" in pages[5]
    assert "All series were made in air" in pages[5]
    assert "Table 2" in pages[11]
    assert "Table 3" in pages[12]
    assert "Fig. 8" in pages[12]
