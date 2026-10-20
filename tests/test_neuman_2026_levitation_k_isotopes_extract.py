from __future__ import annotations

import copy
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator, REPO_ROOT
from tests.literature_extract_test_helpers import iter_locators


SID = "neuman-2026-levitation-k-isotopes"
EXTRACT = REPO_ROOT / f"data/literature/extracts/{SID}.yaml"
CORPUS_ROOT = Path(
    os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
)
PDF = CORPUS_ROOT / "raw" / SID / f"{SID}.pdf"

if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

import validate_literature_extracts as validator  # noqa: E402


# Independent transcription of the run rows from 600 dpi renders of physical
# PDF pages 3–4 and 6–7. Each tuple joins printed conditions and measurements
# to the same sample id across the main tables.
TABLE_RENDER = (
    {
        "table": "1",
        "sample": "3–5",
        "temperature_C": "2000",
        "duration_s": "100",
        "fO2_target": "IW − 1",
        "gas_mix_1_pct": "1.53",
        "gas_mix_2_pct": "98.47",
        "flow_rate_cm3_min": "374",
        "delta41K_permil": "6.41",
        "n": "8",
        "two_se_permil": "0.10",
        "Delta41K_permil": "6.89",
        "K2O_wt_pct": "0.484",
        "Cu_ppm": "0.105",
    },
    {
        "table": "2",
        "sample": "5–10",
        "temperature_C": "2046",
        "duration_s": "100",
        "fO2_target": "IW − 1",
        "gas_mix_1_pct": "1.53",
        "gas_mix_2_pct": "98.47",
        "flow_rate_cm3_min": "375",
        "delta41K_permil": "2.21",
        "n": "9",
        "two_se_permil": "0.05",
        "Delta41K_permil": "2.66",
        "K2O_wt_pct": "2.85",
        "Cu_ppm": "0.415",
    },
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _source_observations(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        observation["observation_id"]: (formula, observation)
        for formula, block in document["species"].items()
        for observation in block.get("observations", [])
    }


def _series_row(document: dict, observation_id: str, sample: str) -> dict:
    observation = _source_observations(document)[observation_id][1]
    return next(
        point
        for point in observation["values"]["series"]
        if point.get("sample_id") == sample
    )


def _printed_condition(point: dict, key: str) -> str:
    return str(point["point_conditions"][key]["state"]["value"])


def _render_grid(document: dict) -> tuple[dict, ...]:
    grid = []
    for expected in TABLE_RENDER:
        table = expected["table"]
        sample = expected["sample"]
        material = "basalt" if table == "1" else "loess"
        delta = _series_row(
            document,
            f"neuman_2026_table_{table}_{material}_delta41k_nist_srm_3141a",
            sample,
        )
        reduced = _series_row(
            document,
            f"neuman_2026_table_{table}_{material}_delta41k_relative_to_starting",
            sample,
        )
        oxide = _series_row(
            document,
            f"neuman_2026_table_{table}_{material}_residue_oxide_composition",
            sample,
        )
        copper = _series_row(
            document,
            f"neuman_2026_table_{table}_{material}_cu_residue_ppm",
            sample,
        )
        grid.append(
            {
                "table": table,
                "sample": sample,
                "temperature_C": str(delta["T_C"]),
                "duration_s": str(delta["duration_s"]),
                "fO2_target": _printed_condition(delta, "fO2_control"),
                "gas_mix_1_pct": str(oxide["gas1"]),
                "gas_mix_2_pct": str(oxide["gas2"]),
                "flow_rate_cm3_min": _printed_condition(
                    delta, "gas_flow_rate_cm3_min"
                ),
                "delta41K_permil": str(
                    delta["isotope_delta_delta41K_NIST_SRM_3141a_permil"]
                ),
                "n": str(delta["n"]),
                "two_se_permil": str(delta["two_se_permil"]),
                "Delta41K_permil": str(
                    reduced["isotope_delta_Delta41K_sample_minus_starting_permil"]
                ),
                "K2O_wt_pct": str(oxide["K2O_wt_pct"]),
                "Cu_ppm": str(copper["residue_ppm"]),
            }
        )
    return tuple(grid)


def _expected_species_rows(document: dict) -> Counter:
    expected = Counter()
    for container, block in document["species"].items():
        for observation in block.get("observations", []):
            values = observation.get("values", {})
            if not isinstance(values, dict):
                continue
            for point in values.get("series", []):
                sample = point.get("sample_id")
                if sample is None:
                    continue
                if values.get("quantity") == Quantity.RESIDUE_COMPONENT_COMPOSITION.value:
                    components = [
                        field.removesuffix("_wt_pct")
                        for field in point
                        if field.endswith("_wt_pct")
                    ]
                    if components:
                        expected.update(
                            (observation["observation_id"], sample, formula)
                            for formula in components
                        )
                    elif "residue_ppm" in point:
                        expected[(
                            observation["observation_id"],
                            sample,
                            values.get("formula", container),
                        )] += 1
                else:
                    expected[(
                        observation["observation_id"],
                        sample,
                        values.get("formula", container),
                    )] += 1
    return expected


def _migrate(document: dict):
    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    rows = [
        row
        for row in migrator.result.observations.values()
        if row.source_id == SID
    ]
    return rows


def test_render_grid_and_both_cell_mutations_are_detected() -> None:
    document = _document()
    actual = _render_grid(document)
    assert actual == TABLE_RENDER

    changed_render = copy.deepcopy(TABLE_RENDER)
    changed_render[0]["K2O_wt_pct"] = "0.485"
    with pytest.raises(AssertionError):
        assert actual == changed_render

    changed_extract = copy.deepcopy(document)
    row = _series_row(
        changed_extract,
        "neuman_2026_table_1_basalt_residue_oxide_composition",
        "3–5",
    )
    row["K2O_wt_pct"] = "0.485"
    with pytest.raises(AssertionError):
        assert _render_grid(changed_extract) == TABLE_RENDER


def test_printed_loss_summaries_and_registered_supplement_context_are_preserved() -> None:
    document = _document()
    assert "supporting_material" not in document["source"]
    assert document["source"]["supporting_material_sha256"] == (
        "0f745b41c8f76c6b5c77ba7c44275d59f5a910f4e1f236a93c10632c4358720e"
    )
    contexts = {
        observation["observation_id"]: observation
        for block in document["species"].values()
        for observation in block.get("context", [])
    }

    expected_rows = {
        "neuman_2026_main_text_basalt_loss_summaries_p54": [
            ("3–5", "K", "~75% of the initial K"),
            ("3–5", "Cu", "99.8%"),
            ("3–5", "Zn", "94%"),
            ("1–5", "K", "45%"),
            ("2–5", "K", "59%"),
            (None, "Rb", "up to 88%"),
            (None, "Na", "up to 74%"),
            (None, "Ga", "up to 40%"),
            ("4–2", "K", "29%"),
            ("3–4", "K", "56%"),
        ],
        "neuman_2026_main_text_basalt_loss_summaries_p56": [
            ("4–2", "Cu", "97%"),
            ("4–2", "Zn", "94%"),
            ("4–2", "Rb", "42%"),
            ("4–2", "Na", "27%"),
            ("4–2", "Ga", "9.2%"),
        ],
        "neuman_2026_main_text_loess_loss_summaries_p58": [
            ("5–10", "K", "32%"),
            (None, "Zn", "up to 85%"),
            ("5–10", "Cu", "nearly all of the Cu was lost in the most extreme case"),
        ],
    }
    for observation_id, expected in expected_rows.items():
        observation = contexts[observation_id]
        assert observation["type"] == "printed_data_context"
        assert observation["values"]["candidate_tokens"] == [
            "mass_loss_fraction",
            "residue_component_composition",
        ]
        assert [
            (
                row.get("sample_id"),
                row["element"],
                row["loss_as_printed"],
            )
            for row in observation["values"]["rows"]
        ] == expected

    supplement = contexts["neuman_2026_supplementary_table_s1_mass_loss_inputs"]
    assert supplement["type"] == "printed_data_context"
    reason = supplement["values"]["reason"]
    assert "49 rows are transcribed from the registered publisher MMC2 workbook" in reason
    assert "no registered TABLE_CSV asset" in reason
    assert "no mass-loss fraction is computed" in reason
    assert len(supplement["values"]["rows"]) == 49


def test_isolated_migration_preserves_species_and_run_conditions() -> None:
    document = _document()
    source = _source_observations(document)
    rows = _migrate(document)
    assert len(rows) == 2258
    assert not any(
        row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION for row in rows
    )

    actual_species_rows = Counter()
    without_sample = []
    for row in rows:
        parts = row.observation_id.split("::", 2)
        source_id = parts[1]
        container, observation = source[source_id]
        sample_match = re.search(r":row=(.*?):h=", row.observation_id)
        if sample_match and "series" in observation.get("values", {}):
            actual_species_rows[(
                source_id,
                sample_match.group(1),
                row.identity.species.formula,
            )] += 1
            assert row.identity.temperature_K.is_value
            assert row.point_conditions is not None
            for key in (
                "temperature_K",
                "duration_s",
                "fO2_control",
                "atmosphere",
                "composition",
            ):
                condition = row.point_conditions[key]
                assert condition.state.is_value
                assert condition.locator is not None
            assert row.point_conditions["temperature_K"].locator.page > 0
            assert (
                row.point_conditions["temperature_K"].locator.page
                == row.point_conditions["temperature_K"].locator.pdf_page_index + 1
            )
        else:
            without_sample.append(row)
            assert row.identity.species.formula == observation.get("values", {}).get(
                "formula", container
            )

    assert actual_species_rows == _expected_species_rows(document)
    assert len(without_sample) == 2  # The two measured starting-material isotope baselines.
    assert all(row.identity.species.formula == "K" for row in without_sample)
    assert all(row.point_conditions is None for row in without_sample)


def test_per_file_validator_accepts_extract() -> None:
    errors = validator.validate_extract_document(
        _document(),
        path=EXTRACT,
        expected_source_id=SID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert not errors


@pytest.mark.skipif(not PDF.is_file(), reason="regolith-corpus source PDF is absent")
def test_locator_pages_match_the_source_pdf() -> None:
    document = _document()
    page_text: dict[int, str] = {}
    for locator in iter_locators(document):
        if "page" not in locator:
            continue
        page = int(locator["page"])
        assert int(locator["pdf_page_index"]) == page - 1
        if page not in page_text:
            result = subprocess.run(
                ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
                check=True,
                capture_output=True,
                text=True,
            )
            page_text[page] = result.stdout
        text = page_text[page]
        assert text.strip()
        table = locator.get("table")
        if table and not locator.get("source_path"):
            assert f"Table {table}" in text
        elif table and table in {"S1", "S2"}:
            assert f"Supplementary Table {table}" in text
        figure = locator.get("figure")
        if figure and str(figure).startswith("S"):
            cited_range = "Supplementary Figs. S1-S11" if page == 5 else "Supplementary Fig. S12"
            assert cited_range in text
        elif figure:
            assert f"Fig. {figure}" in text
