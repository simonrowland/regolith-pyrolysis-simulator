"""Regression pins for Su (2024) context tables."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from tests.battery.test_migrate import REPO_ROOT, _migrate_real_extract
from tests.helpers.corpus_paths import regolith_corpus_root


SOURCE_ID = "su-2024-lunar-volatiles-thesis"
EXTRACT_NAME = f"{SOURCE_ID}.yaml"
EXTRACT_PATH = REPO_ROOT / "data" / "literature" / "extracts" / EXTRACT_NAME


def _context_by_id(doc: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for container in doc["species"].values()
        for row in container.get("context", [])
    }


def test_context_tables_migrate_without_scored_observations(tmp_path: Path) -> None:
    doc = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    migrated = _migrate_real_extract(tmp_path / "migration", EXTRACT_NAME)

    assert migrated.validation is not None
    assert not migrated.validation.hard_issues, migrated.validation.hard_issues
    assert migrated.observations == {}
    rows = [row for work_rows in migrated.context_by_work.values() for row in work_rows]
    assert len(rows) == 6
    assert {row["observation_id"].rsplit("::", 1)[-1] for row in rows} == set(
        _context_by_id(doc)
    )
    assert all(row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1 for row in rows)


def test_context_table_values_pin_rendered_cells() -> None:
    doc = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    rows = _context_by_id(doc)

    # These expected cells were read independently from the 600 dpi page renders.
    t21 = rows["su-table-2-1-whole-rock-and-quench-rates"]["values"]["samples"]
    assert len(t21) == 3
    assert t21[0]["SiO2_wt_percent"] == "48.1"
    assert t21[1]["q_K_per_h_as_printed"] == "3600–360000"
    assert t21[2]["q_K_per_h_as_printed"] == "unknown"

    t22_record = rows["su-table-2-2-natural-ohmi-compositions"]["values"]
    assert len(t22_record["sample_columns"]) == 9
    t22 = t22_record["printed_table_and_notes_as_published"]
    assert "OL11-2" in t22 and "352" in t22 and "H2O/Ce" in t22
    assert "907" in t22 and "67.1" in t22

    t23_record = rows["su-table-2-3-natural-phmi-and-groundmass-compositions"]["values"]
    assert len(t23_record["sample_columns"]) == 7
    t23 = t23_record["printed_table_and_notes_as_published"]
    assert "bst-27-py8" in t23 and "54.1" in t23 and "994" in t23
    assert "Original concentrations before deconvolution" in t23
    assert "10.1" in t23 and "39.6" in t23

    t24 = rows["su-table-2-4-deconvolved-natural-phmi-ree"]["values"]["printed_table_as_read_from_600dpi_render"]
    assert "24.6±5.4" in t24
    assert "91(+37/−20)" in t24
    assert "10.8±1.4)" in t24

    t41 = rows["su-table-4-1-diffusion-model-fit-parameters"]["values"]
    assert len(t41["Na_profile_fitting"]) == 4
    assert t41["Na_profile_fitting"][2]["tau1_s"] == "575(67)"
    assert t41["sulfur_profile_fitting"]["aD"] == "-5.15"

    t42 = rows["su-table-4-2-modelled-sulfur-and-sodium-loss-gain"]["values"]
    assert len(t42["sulfur_rows"]) == 4
    assert len(t42["sodium_rows"]) == 5
    assert t42["initial_model_conditions_as_printed"]["T0_K"] == "1640"
    assert t42["sulfur_rows"][1]["fS_ingassed_percent"] == "16%"
    assert t42["sodium_rows"][3]["Ttr_K"] == "1539"
    assert all("T0_K" not in row for row in t42["sulfur_rows"] + t42["sodium_rows"])


def test_physical_page_locators_match_source_when_corpus_is_available() -> None:
    pdf = regolith_corpus_root() / "raw" / SOURCE_ID / f"{SOURCE_ID}.pdf"
    if not pdf.is_file():
        pytest.skip(f"source PDF is absent: {pdf}")

    for page, caption in (
        (26, "Table 2.1:"),
        (31, "Table 2.2:"),
        (32, "Table 2.3:"),
        (33, "Table 2.4:"),
        (104, "Table 4.1:"),
        (106, "Table 4.2:"),
    ):
        result = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert caption in result.stdout, (page, caption)


def test_context_disposition_and_candidate_tokens_are_explicit() -> None:
    doc = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    rows = _context_by_id(doc)
    for observation_id in (
        "su-table-2-1-whole-rock-and-quench-rates",
        "su-table-2-2-natural-ohmi-compositions",
        "su-table-2-3-natural-phmi-and-groundmass-compositions",
        "su-table-2-4-deconvolved-natural-phmi-ree",
    ):
        assert rows[observation_id]["type"] == "measured_table_without_store_token"
        assert rows[observation_id]["candidate_tokens"]

    for observation_id in (
        "su-table-4-1-diffusion-model-fit-parameters",
        "su-table-4-2-modelled-sulfur-and-sodium-loss-gain",
    ):
        assert rows[observation_id]["type"] == "model_derived_table"
        assert rows[observation_id]["values"]["method_class"] == "model_derived"
