from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import subprocess

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator
from tests.helpers.corpus_paths import regolith_corpus_root

ROOT = Path(__file__).resolve().parents[1]
SID = "dohmen-2003-coupled-kinetics"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
CORPUS_ROOT = regolith_corpus_root()
PDF = CORPUS_ROOT / "raw" / SID / f"{SID}.pdf"

# Independent transcription from the 600-dpi physical-page-3 render.
TABLE1_RENDER_TRANSCRIPTION = (
    ("SC1", "0.5", "10.52", "10.53", "-0.10", "1.26", "1.86", "1.37", "0.000", "concave profiles"),
    ("SC2", "2", "10.84", "10.86", "-0.18", "1.26", "1.86", "1.40", "-0.001", "concave profiles"),
    ("SC3", "5", "11.03", "11.02", "0.09", "1.30", "1.87", "1.38", "0.000", "convex profiles"),
    ("SC4", "10", "10.77", "10.68", "0.84", "1.28", "1.86", "1.39", "0.004", "convex profiles"),
    ("SC5", "15", "11.44", "11.30", "1.22", "1.32", "1.91", "1.39", "0.006", "melt formation; complex profiles"),
    ("SC6", "20", "11.15", "10.84", "2.78", "1.30", "1.90", "1.38", "0.014", "melt formation; complex profiles"),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _table1_grid(document: dict) -> tuple[tuple[str, ...], ...]:
    record = next(
        item
        for item in document["species"]["Mg"]["context"]
        if item.get("type") == "table_record" and item["locator"].get("table") == "1"
    )
    fields = (
        "run",
        "time_h",
        "m0_mg",
        "m1_mg",
        "delta_m_percent",
        "a0_mm",
        "b0_mm",
        "c0_mm",
        "delta_l_calculated_mm",
        "comments",
    )
    return tuple(tuple(str(row[field]) for field in fields) for row in record["values"]["rows"])


def _assert_table_matches_render(document: dict, expected=TABLE1_RENDER_TRANSCRIPTION) -> None:
    assert _table1_grid(document) == expected


def test_table1_full_grid_is_pinned_to_independent_render_transcription() -> None:
    document = _document()
    _assert_table_matches_render(document)
    assert len(_table1_grid(document)) == 6

    changed_fixture = list(TABLE1_RENDER_TRANSCRIPTION)
    changed_row = list(changed_fixture[2])
    changed_row[2] = "11.04"
    changed_fixture[2] = tuple(changed_row)
    with pytest.raises(AssertionError):
        _assert_table_matches_render(document, tuple(changed_fixture))

    changed_extract = deepcopy(document)
    table = next(
        item
        for item in changed_extract["species"]["Mg"]["context"]
        if item.get("type") == "table_record" and item["locator"].get("table") == "1"
    )
    table["values"]["rows"][2]["m0_mg"] = "11.04"
    with pytest.raises(AssertionError):
        _assert_table_matches_render(changed_extract)


def test_isolated_migration_types_printed_pressures_and_conditions() -> None:
    document = _document()
    migrator = Migrator(root=ROOT, index={SID: document["source"]}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 2
    expected = {
        "dohmen_2003_text_pfe_approximate": ("Fe", Decimal("4e-6")),
        "dohmen_2003_text_psio_approximate": ("SiO", Decimal("1e-8")),
    }
    for local_id, (formula, pressure_bar) in expected.items():
        row = result.observations[f"{SID}::{local_id}"]
        assert row.identity.species.formula == formula
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value is Phase.G
        assert row.identity.quantity.value is Quantity.P_PARTIAL
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == pressure_bar * Decimal("100000")
        assert row.value.approximate is True
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("1673.15")
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.total_pressure_Pa.reason == "in_cell_total_pressure_not_derivable"
        assert row.point_conditions is not None
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal("1673.15")
        assert temperature.locator is not None
        assert temperature.locator.pdf_page_index == row.locator.pdf_page_index
        experiment = result.experiments[row.experiment_id]
        chamber_pressure = experiment.pressure_environment.total_pressure_Pa
        assert chamber_pressure.state.is_value
        assert chamber_pressure.state.value.point == Decimal("0.00001")
        assert chamber_pressure.locator is not None
        assert chamber_pressure.locator.page == 3
        assert not any(
            notice.kind.value == "source_value_outside_physical_range"
            for notice in row.notices
        )


def test_all_fourteen_figures_are_deferred_and_have_series_metadata() -> None:
    figures = [
        item
        for item in _document()["species"]["Mg"]["context"]
        if item.get("what") == "figure_record"
    ]
    assert {item["locator"]["figure"] for item in figures} == {
        str(number) for number in range(1, 15)
    }
    for figure in figures:
        values = figure["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert "points" not in values
        assert all(
            values.get(field)
            for field in ("species", "phase", "axes_as_printed", "caption_as_printed", "attribution")
        )
        assert isinstance(values.get("series"), list)
        assert all(isinstance(series.get("marker_count"), int) for series in values["series"])
        assert figure["locator"]["page"] == figure["locator"]["pdf_page_index"] + 1


@pytest.mark.skipif(not PDF.is_file(), reason="source corpus PDF is absent")
def test_table_and_figure_labels_are_on_their_physical_pdf_pages() -> None:
    document = _document()
    expected_pages = {
        "1": 3,
        "2": 4,
        "3": 4,
        "4": 4,
        "5": 5,
        "6": 5,
        "7": 6,
        "8": 7,
        "9": 9,
        "10": 9,
        "11": 10,
        "12": 11,
        "13": 11,
        "14": 12,
    }
    figures = [
        item
        for item in document["species"]["Mg"]["context"]
        if item.get("what") == "figure_record"
    ]
    for figure in figures:
        number = figure["locator"]["figure"]
        page = figure["locator"]["page"]
        assert page == expected_pages[number]
        text = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert f"Figure {number}." in text

    table_page = subprocess.run(
        ["pdftotext", "-f", "3", "-l", "3", "-layout", str(PDF), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "Table 1." in table_page
