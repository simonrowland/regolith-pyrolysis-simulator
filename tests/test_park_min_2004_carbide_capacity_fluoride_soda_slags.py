"""Regression pins for Park and Min (2004) Table 1."""

from __future__ import annotations

import re
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import MethodToken, Phase, PhaseRole, Quantity, ValueKind
from tests.battery.test_migrate import REPO_ROOT, _migrate_real_extract
from tests.helpers.corpus_paths import regolith_corpus_root


SOURCE_ID = "park-min-2004-carbide-capacity-fluoride-soda-slags"
EXTRACT_NAME = f"{SOURCE_ID}.yaml"
EXTRACT_PATH = REPO_ROOT / "data" / "literature" / "extracts" / EXTRACT_NAME
PDF_NAME = f"{SOURCE_ID}.pdf"

# Read independently from the 600 dpi render of physical PDF page 2, Table 1.
RENDER_PINS = (
    ("t1-f1-cao", "CaO", Decimal("20.1")),
    ("t1-f1-caf2", "CaF2", Decimal("65.6")),
    ("t1-f1-sio2", "SiO2", Decimal("0.0")),
    ("t1-f2-sio2", "SiO2", Decimal("16.1")),
    ("t1-n12-na2o", "Na2O", Decimal("1.8")),
)


def test_table1_rows_migrate_and_pin_render_cells(tmp_path: Path) -> None:
    doc = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    result = _migrate_real_extract(tmp_path / "migration", EXTRACT_NAME)
    assert result.validation is not None
    assert not result.validation.hard_issues, result.validation.hard_issues

    all_rows = list(result.observations.values())
    assert len(all_rows) == 111
    assert all(row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION for row in all_rows)
    assert all(row.value.kind is ValueKind.POINT for row in all_rows)
    assert all(not row.notices for row in all_rows)

    source_rows = {
        observation["observation_id"]: (formula, observation)
        for formula, container in doc["species"].items()
        for observation in container.get("observations", [])
    }
    assert len(source_rows) == 111
    migrated_by_id = {
        row.observation_id.rsplit("::", 1)[-1]: row for row in all_rows
    }
    expected_counts = {"CaO": 33, "CaF2": 33, "SiO2": 33, "Na2O": 12}
    assert {
        formula: len(container["observations"])
        for formula, container in doc["species"].items()
    } == expected_counts

    for raw_id, (formula, raw) in source_rows.items():
        row = migrated_by_id[raw_id]
        assert row.identity.species.formula == formula
        assert row.identity.phase_role.value is PhaseRole.SLAG
        assert row.identity.species.phase.value is Phase.L
        assert row.identity.subtype.value == "wt_percent"
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("1773.00")
        assert row.identity.composition.is_unknown
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa.is_unknown
        assert row.value.point == Decimal(raw["values"]["component_amount"])
        assert row.locator is not None
        assert "1,733 K" in (row.locator.note or "")
        assert "1,773 K" in (row.locator.note or "")
        gas_mix = row.point_conditions["gas_mix"]
        assert gas_mix.state.is_value and gas_mix.state.value == "CO atmosphere"
        assert gas_mix.locator is not None
        assert row.experiment_id.endswith(f"::t1-{raw['values']['sample_name_as_printed']}")
        experiment = result.experiments[row.experiment_id]
        assert experiment.method.is_value
        assert experiment.method.value is MethodToken.QUENCH_EQUILIBRATION
        quench = experiment.thermal_schedule.cooling_or_quench
        assert quench is not None and quench.state.is_value
        assert "quenched by Ar gas blowing" in quench.state.value
        assert quench.locator is not None and quench.locator.page == 2

    for raw_id, formula, expected in RENDER_PINS:
        row = migrated_by_id[raw_id]
        assert row.identity.species.formula == formula
        assert row.value.point == expected
        assert row.locator is not None and row.locator.page == 2

    context = doc["species"]["SiO2"]["context"]
    table_context = [item for item in context if item["observation_id"] == "park-min-table1-carbide-capacity-no-token"]
    assert len(table_context) == 1
    assert len(table_context[0]["values"]["samples"]) == 33
    assert table_context[0]["values"]["samples"][0] == {
        "sample_name_as_printed": "F-1",
        "log_C_carbide_as_printed": "-8.17",
    }
    assert len([row for row in all_rows if row.identity.quantity.value in {Quantity.SULFIDE_CAPACITY, Quantity.CHLORIDE_CAPACITY}]) == 0
    figures = [item for item in context if item.get("type") == "figure_only"]
    assert len(figures) == 10
    assert all(item["values"]["digitisation"] == "deferred to a figure pass" for item in figures)
    assert all("numeric_points" not in item["values"] for item in figures)
    assert all(item["locator"]["page"] == item["locator"]["pdf_page_index"] + 1 for item in figures)


def test_physical_page_locators_match_source_when_corpus_is_available() -> None:
    corpus = regolith_corpus_root()
    pdf = corpus / "raw" / SOURCE_ID / PDF_NAME
    if not pdf.is_file():
        pytest.skip(f"source PDF is absent: {pdf}")

    pages = (
        (1, "Carbide Capacity of CaO"),
        (2, "Table 1."),
        (2, "1 733 K."),
        (2, "Fig. 1."),
        (2, "1 773 K."),
        (2, "under a CO atmosphere"),
        (2, "quenched"),
        (2, "by Ar gas blowing"),
        (3, "Fig. 2."),
        (3, "Fig. 3."),
        (3, "Fig. 4."),
        (4, "Fig. 5."),
        (4, "Fig. 6."),
        (4, "Fig. 7."),
        (5, "Fig. 8."),
        (5, "Fig. 9."),
        (5, "Fig. 10."),
    )
    for page, label in pages:
        result = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        page_text = re.sub(r"\s+", " ", result.stdout.lower())
        assert label.lower() in page_text, (page, label)
