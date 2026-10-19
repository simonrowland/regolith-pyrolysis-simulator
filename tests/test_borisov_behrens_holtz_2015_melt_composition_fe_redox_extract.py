from __future__ import annotations

import csv
from copy import deepcopy
from decimal import Decimal
import os
from pathlib import Path
import subprocess

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, QuenchState, UncertaintyKind, ValueKind
from simulator.battery.migrate import Migrator
from tests.literature_extract_test_helpers import iter_locators


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "borisov-behrens-holtz-2015-melt-composition-fe-redox"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
RENDER_GRID = ROOT / "tests/fixtures/borisov_2015_table1_render_grid.csv"
OXIDES = ("SiO2", "TiO2", "Al2O3", "Fe2O3", "MgO", "CaO", "P2O5")


@pytest.fixture(scope="module")
def document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _render_grid() -> list[dict[str, str]]:
    # Independently transcribed from the 600 dpi renders of physical pages 4–5.
    # Ditto cells remain as printed; their expansion follows Table 1 footnote a.
    with RENDER_GRID.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _assert_table1_grid(document: dict, expected: list[dict[str, str]]) -> None:
    observations = {
        item["values"]["row_as_printed"]: item
        for item in document["species"]["Fe"]["observations"]
        if item["observation_id"].startswith("borisov_2015_table1_fe3_fe2_row_")
    }
    assert len(expected) == 67
    assert len(observations) == 67
    prior_conditions: dict[str, str] = {}
    for expected_row in expected:
        row_number = expected_row["row"]
        observation = observations[row_number]
        values = observation["values"]
        assert values["sample_as_published"] == expected_row["sample"]
        assert values["sample_footnote_as_published"] == (expected_row["footnote"] or "none")
        assert values["analytical_session_as_published"] == expected_row["session"]
        assert values["minus_log_fO2_form_as_published"] == "−log fO2"

        for cell, value_key, cell_key in (
            ("T_cell", "T_C", "T_cell_as_published"),
            ("minus_log_fO2_cell", "minus_log_fO2_as_published", "minus_log_fO2_cell_as_published"),
            ("duration_cell", "duration_h_as_published", "duration_cell_as_published"),
        ):
            printed_cell = expected_row[cell]
            assert values[cell_key] == printed_cell
            if printed_cell != "a":
                prior_conditions[cell] = printed_cell
            assert values[value_key] == prior_conditions[cell]

        assert values["composition_wt_pct_as_published"] == {
            oxide: expected_row[oxide] for oxide in OXIDES
        }
        if values["composition_status"] == "complete":
            assert values["composition_wt_pct"] == {
                oxide: expected_row[oxide] for oxide in OXIDES
            }
        assert values["Total_wt_pct_as_published"] == expected_row["Total"]
        assert values["value"] == expected_row["Fe3+/Fe2+"]
        fO2_condition = observation["point_conditions"]["fO2_log"]
        assert fO2_condition["state"]["tag"] == "value"
        canonical_log_fO2 = -Decimal(
            values["minus_log_fO2_as_published"]
        )
        assert Decimal(str(fO2_condition["state"]["value"])) == canonical_log_fO2
        assert fO2_condition["locator"]["note"] == (
            "pressure reference not printed; stored on the bar convention "
            "(atm/bar offset log10(1.01325)=0.0057 is below printed precision)"
        )
        assert observation["locator"]["page"] == (4 if int(row_number) <= 48 else 5)
        assert observation["locator"]["pdf_page_index"] == observation["locator"]["page"] - 1
        assert observation["locator"]["row"] == row_number


def test_render_grid_binds_all_table1_cells_and_catches_both_mutations(
    document: dict,
) -> None:
    expected = _render_grid()
    _assert_table1_grid(document, expected)

    grid_mutation = deepcopy(expected)
    grid_mutation[0]["SiO2"] = "45.72"
    with pytest.raises(AssertionError):
        _assert_table1_grid(document, grid_mutation)

    extract_mutation = deepcopy(document)
    first_row = next(
        item for item in extract_mutation["species"]["Fe"]["observations"]
        if item["observation_id"].endswith("_row_01_daf_52")
    )
    first_row["values"]["composition_wt_pct_as_published"]["SiO2"] = "45.72"
    with pytest.raises(AssertionError):
        _assert_table1_grid(extract_mutation, expected)

    fO2_sign_mutation = deepcopy(document)
    first_fO2 = next(
        item for item in fO2_sign_mutation["species"]["Fe"]["observations"]
        if item["observation_id"].startswith("borisov_2015_table1_fe3_fe2_row_")
    )
    first_fO2["point_conditions"]["fO2_log"]["state"]["value"] = first_fO2[
        "values"
    ]["minus_log_fO2_as_published"]
    with pytest.raises(AssertionError):
        _assert_table1_grid(fO2_sign_mutation, expected)


def test_migration_types_all_rows_and_binds_conditions_quench_and_species(
    document: dict,
) -> None:
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    records = list(result.observations.values())
    assert len(records) == 72
    assert not result.registry_issues

    source_observations = {
        item["observation_id"]: item
        for body in document["species"].values()
        for item in body.get("observations", [])
    }
    assert all(
        record.identity.species.formula == "Fe"
        for record in records
    )
    assert all(record.identity.quantity.value is Quantity.VALENCE_RATIO for record in records)
    assert all(record.value.kind is ValueKind.POINT for record in records)

    experimental = [r for r in records if "table1_fe3_fe2_row_" in r.observation_id]
    assert len(experimental) == 67
    for record in experimental:
        source = source_observations[record.observation_id.split("::", 1)[-1]]
        values = source["values"]
        conditions = record.point_conditions
        assert conditions is not None
        for key in ("T_C", "duration_h", "fO2_log", "total_pressure_Pa", "composition", "quench_state"):
            assert key in conditions and conditions[key].locator is not None
        assert record.identity.temperature_K.value == Decimal(values["T_C"]) + Decimal("273.15")
        assert record.identity.total_pressure_Pa.value == Decimal("101325")
        assert record.identity.fO2_Pa.is_value
        assert conditions["fO2_log"].state.is_value
        assert conditions["fO2_log"].state.value == -Decimal(
            values["minus_log_fO2_as_published"]
        )
        assert conditions["quench_state"].state.value is QuenchState.NOT_STATED
        assert record.quench_state is QuenchState.NOT_STATED
        assert record.identity.species.phase.value is Phase.L
        assert record.uncertainty.kind is UncertaintyKind.PRINTED
        assert "approximately ±0.08" in record.uncertainty.verbatim["verbatim"]

        comp_state = conditions["composition"].state
        if values["composition_status"] == "complete":
            assert comp_state.is_value
            assert record.identity.composition.is_value
            assert record.identity.composition == comp_state
        else:
            assert comp_state.is_unknown
            assert record.identity.composition.is_unknown
        assert conditions["T_C"].state.value == Decimal(values["T_C"])
        assert conditions["duration_h"].state.value == Decimal(values["duration_h_as_published"])

    standards = [r for r in records if "pu3_" in r.observation_id]
    assert len(standards) == 5
    assert sum(r.evidence.class_.value.value == "measured_direct" for r in standards) == 4
    quoted = next(r for r in standards if "schuessler" in r.observation_id)
    assert quoted.evidence.class_.value.value == "quoted_attributed"
    assert quoted.uncertainty.kind is UncertaintyKind.PRINTED
    assert "1σ" in quoted.uncertainty.verbatim["verbatim"]


def test_published_tables_figures_and_quench_context_are_disposed_by_type(
    document: dict,
) -> None:
    observations = document["species"]["Fe"]["observations"]
    table_rows = [row for row in observations if "table1_fe3_fe2_row_" in row["observation_id"]]
    assert len(table_rows) == 67
    assert all(row["values"]["method_class"] == "measured_direct" for row in table_rows)
    assert all(row["values"]["sample_as_published"] for row in table_rows)
    assert all("quench_statement" in row for row in table_rows)
    assert all("preservation" not in row["quench_statement"] for row in table_rows)
    assert all(row["quench_statement"]["quench_direction"] == "not_stated" for row in table_rows)

    contexts = document["species"]["Fe"]["context"]
    model_tables = [row for row in contexts if row.get("type") == "model_fit_context" and "table" in row["locator"]]
    assert {row["locator"]["table"] for row in model_tables} == {"2", "3"}
    assert all(row["values"]["method_class"] == "model_derived" for row in model_tables)
    figures = [row for row in contexts if row.get("type") == "figure_record"]
    assert len(figures) == 7
    for figure in figures:
        values = figure["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["identity"]["species"] == "Fe"
        assert values["identity"]["phase"] == "l"
        assert values["identity"]["temperature_as_printed"]
        assert values["axes_as_printed"]
        assert values["caption_as_printed"]
        assert values["series"]
        assert all("marker_count" in item for item in values["series"])
        assert "points" not in values


def test_all_locators_use_one_based_physical_pages(document: dict) -> None:
    locators = list(iter_locators(document))
    assert locators
    assert all(locator["page"] == locator["pdf_page_index"] + 1 for locator in locators)


def test_cited_source_pages_are_present_and_guarded_when_corpus_is_absent() -> None:
    corpus = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))
    pdf = corpus / "raw" / SOURCE_ID / f"{SOURCE_ID}.pdf"
    if not pdf.is_file():
        pytest.skip(f"corpus PDF absent: {pdf}")

    pages = {}
    for page in (2, 3, 4, 5, 6, 9, 10, 11):
        completed = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        pages[page] = completed.stdout

    assert "one-atmosphere" in pages[2].lower()
    assert "quickly withdrawing" in pages[3].lower()
    assert "1.51 ± 0.08" in pages[3]
    assert "1.60 ± 0.11" in pages[3]
    assert "Table 1" in pages[4] and "2.41" in pages[4]
    assert "Table 1" in pages[5] and "0.59" in pages[5]
    assert "Fig. 1" in pages[5] and "Fig. 2" in pages[5]
    assert "Fig. 3" in pages[6] and "Fig. 4" in pages[6]
    assert "Fig. 5" in pages[9]
    assert "Table 2" in pages[10] and "Table 3" in pages[10]
    assert "Fig. 6" in pages[11] and "Fig. 7" in pages[11]
