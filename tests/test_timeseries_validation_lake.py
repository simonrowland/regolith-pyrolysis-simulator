from __future__ import annotations

import math

import pytest

from simulator.diagnostic_helpers.timeseries_validation_lake import (
    DEFAULT_DATA_ROOT,
    load_catalog,
    render_markdown_report,
    validate_lake,
)


@pytest.fixture(scope="module")
def validation_lake_catalog():
    """Session-safe catalog load — pure YAML, no mutable simulator state."""
    return load_catalog()


@pytest.fixture(scope="module")
def validation_lake_reports():
    """One validate_lake() pass for the module.

    Three consumers previously re-ran the full lake (~35 s each). The lake is
    pure read-only validation over static datasets — module scope is
    golden-neutral.
    """
    return validate_lake(DEFAULT_DATA_ROOT)


@pytest.fixture(scope="module")
def validation_lake_by_dataset(validation_lake_reports):
    return {report.dataset_id: report for report in validation_lake_reports}


def test_catalog_keeps_unavailable_datasets_as_explicit_gaps(
    validation_lake_catalog,
) -> None:
    catalog = validation_lake_catalog
    by_id = {entry["id"]: entry for entry in catalog["datasets"]}

    assert by_id["DS-002"]["status"] == "unavailable-automation"
    assert "403" in by_id["DS-002"]["condition_gap"]
    assert by_id["DS-010"]["status"] == "validated-direct-flux"
    assert by_id["DS-010"]["file"] == "DS-010.csv"
    assert by_id["DS-007"]["source_id"] == "kems-005-fedkin-2006"
    assert "SiO volatile proxy" in by_id["DS-010"]["condition_gap"]
    assert by_id["DS-012"]["status"] == "unavailable-automation"
    assert "session-gated" in by_id["DS-012"]["condition_gap"]


def test_validation_lake_reports_dimensionally_honest_comparisons(
    validation_lake_by_dataset,
) -> None:
    reports = validation_lake_by_dataset

    assert reports["DS-001"].rows_evaluated == 3
    assert reports["DS-003"].rows_evaluated == 12
    assert reports["DS-006"].rows_evaluated == 4
    assert reports["DS-007"].rows_evaluated == 12
    assert reports["DS-007"].median_abs_dex_error == 0.06993954320061825
    assert reports["DS-007"].median_abs_dex_error_measured_only is None
    assert (
        reports["DS-007"].median_abs_dex_error_excluding_same_input
        == 0.1406575917694785
    )
    assert {item.evidence_tier for item in reports["DS-007"].species} == {
        "literature-model"
    }
    ds007_payload = reports["DS-007"].as_dict()
    assert ds007_payload["median_abs_dex_error"] == 0.06993954320061825
    assert ds007_payload["median_abs_dex_error_excluding_same_input"] == (
        0.1406575917694785
    )
    assert ds007_payload["median_abs_dex_error_measured_only"] is None
    assert all(
        item["evidence_tier"] == "literature-model"
        for item in ds007_payload["species"]
    )
    assert reports["DS-001"].median_abs_dex_error_measured_only == (
        reports["DS-001"].median_abs_dex_error
    )
    assert {item.evidence_tier for item in reports["DS-001"].species} == {
        "measured"
    }
    assert tuple(
        abs(item.dex_error_model_minus_observed)
        for item in reports["DS-007"].species
    ) == (
        0.018483405694013133,
        0.017728766960431602,
        0.0,
        0.018483405694013133,
        0.0791812460476248,
        0.021189299069938095,
        0.146128035678238,
        0.0606978403536117,
        0.17431584757931465,
        0.13518714786071903,
        0.32150855070082623,
        0.14944524188811364,
    )
    same_input_rows = [
        item for item in reports["DS-007"].species
        if item.source_relation == "SAME_INPUT"
    ]
    assert len(same_input_rows) == 4
    assert {item.species for item in same_input_rows} == {"Fe"}
    assert {item.source_relation for item in reports["DS-007"].species} <= {
        "SAME_INPUT",
        "INDEPENDENT",
        "UNKNOWN",
    }
    assert all(
        item.source_relation == "UNKNOWN"
        for item in reports["DS-007"].species
        if item.source_relation != "SAME_INPUT"
    )
    assert reports["DS-007"].as_dict()[
        "median_abs_dex_error_excluding_same_input"
    ] == pytest.approx(0.1406575917694785)
    assert reports["DS-010"].status == "validated"
    assert reports["DS-010"].rows_evaluated == 7

    all_items = [item for report in reports.values() for item in report.species]
    assert not any(item.observed_floor_applied for item in all_items)
    assert not any("observed floor" in item.notes for item in all_items)

    # K is runtime-supported through the builtin provider. It must not be
    # skipped by the raw pseudo-Antoine guard.
    assert any(item.model_species == "K" for item in reports["DS-001"].species)
    assert any(item.model_species == "K" for item in reports["DS-005"].species)
    assert not any(
        "K flux model unavailable" in reason
        for report in reports.values()
        for reason in report.skipped_reasons
    )

    # Non-positive depletion is a below-detection comparison gap, not a fake
    # floored dex error.
    assert not any(
        item.dataset_id == "DS-001" and item.species == "Ti"
        for item in all_items
    )
    assert any(
        "DS-001:Ti residue_wt_pct below-detection / not-comparable" == reason
        for reason in reports["DS-001"].skipped_reasons
    )

    # Si-bearing oxide residues route to the runtime SiO volatile species.
    assert {
        item.model_species
        for item in reports["DS-003"].species
        if item.species == "SiO2"
    } == {"SiO"}

    direct_flux = [
        item
        for item in reports["DS-010"].species
        if item.signal_type == "evaporation_flux_molecules_cm2_s"
    ]
    assert len(direct_flux) == 7
    assert {item.model_species for item in direct_flux} == {"SiO"}
    assert all(
        item.modeled_value > 0.0 and item.observed_value > 0.0
        for item in direct_flux
    )
    assert {item.evidence_tier for item in direct_flux} == {"measured"}
    assert all(math.isfinite(item.error_factor) for item in direct_flux)


def test_endpoint_rank_metric_is_not_labeled_as_kinetic_ordering(
    validation_lake_by_dataset,
) -> None:
    report = validation_lake_by_dataset["DS-003"]

    payload = report.as_dict()
    assert "endpoint_rank_disagreement_fraction" in payload
    assert "ordering_inversion_fraction" not in payload
    # JANAF-4th multiphase re-ground moves the model Mg/SiO endpoint ordering;
    # the observed KEMS rows are unchanged, and this derived residual records it.
    # 2026-07-11 0.5.10 E-MOVE: current phase-basis/two-rail/Kress ordering
    # surface raises the endpoint-rank disagreement fraction.
    # 2026-07-21 B1 vapor-package regen: corrected P_sat fits invert one more
    # DS-003 endpoint pair (2/17 -> 3/17); observed KEMS rows unchanged —
    # this residual is a recorded reproduction error, never a retune target.
    # 2026-08-07 b-147 Mg TE-μ0 demotion: corrected Mg Pref (JANAF gas_fugacity,
    # ~+0.55 dex) restores one DS-003 endpoint pair to the observed KEMS order
    # (3/17 -> 2/17 = 0.1176). SIGN CHECK: agreement with the unchanged
    # observed rows IMPROVES under the physically-audited Pref — recorded
    # movement, not a retune.
    assert report.endpoint_rank_disagreement_fraction == pytest.approx(
        0.11764705882352941
    )


def test_markdown_report_contains_summary_and_json_pointer_for_long_skips(
    validation_lake_reports,
    validation_lake_catalog,
) -> None:
    reports = validation_lake_reports
    markdown = render_markdown_report(reports, catalog=validation_lake_catalog)

    assert "endpoint rank disagreement" in markdown
    assert "median abs dex (measured only)" in markdown
    assert "| evidence tier |" in markdown
    assert "ordering inversions" not in markdown
    assert (
        "| DS-007 | validated | 12 | 0.070 | - | 0.141 | 0.322 | - | - |"
    ) in markdown
    assert (
        "| DS-007 | Fe | Fe | evaporation_coefficient | literature-model |"
        in markdown
    )
    assert "| SAME_INPUT | direct alpha comparison |" in markdown
    # 2026-07-21 B1: DS-010 median/max reproduction error re-measured under
    # the corrected fits (1.392/1.920 -> 1.199/3.309); rank cell unchanged.
    assert (
        "| DS-010 | validated | 7 | 1.199 | 1.199 | 1.199 | 3.309 | 0.222 |"
        in markdown
    )
    assert "more in JSON" in markdown
    assert "DS-010: fetch-dns-blocked" not in markdown


def test_markdown_report_tables_have_consistent_column_counts(
    validation_lake_reports,
    validation_lake_catalog,
) -> None:
    # A GFM table renders only when its separator row has one cell per header
    # cell; a short separator silently degrades the table to plain text.
    markdown = render_markdown_report(
        validation_lake_reports, catalog=validation_lake_catalog
    )
    lines = markdown.splitlines()
    tables = 0
    for index, line in enumerate(lines[:-1]):
        separator = lines[index + 1]
        if not (line.startswith("|") and separator.startswith("|---")):
            continue
        tables += 1
        width = len(line.strip().strip("|").split("|"))
        assert len(separator.strip().strip("|").split("|")) == width, line
        for row in lines[index + 2 :]:
            if not row.startswith("|"):
                break
            assert len(row.strip().strip("|").split("|")) == width, row
    assert tables >= 2
