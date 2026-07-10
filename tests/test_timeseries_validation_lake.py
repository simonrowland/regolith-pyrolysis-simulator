from __future__ import annotations

import math

import pytest

from simulator.diagnostic_helpers.timeseries_validation_lake import (
    DEFAULT_DATA_ROOT,
    load_catalog,
    render_markdown_report,
    validate_lake,
)


def _by_dataset():
    return {report.dataset_id: report for report in validate_lake()}


def test_catalog_keeps_unavailable_datasets_as_explicit_gaps() -> None:
    catalog = load_catalog()
    by_id = {entry["id"]: entry for entry in catalog["datasets"]}

    assert by_id["DS-002"]["status"] == "unavailable-automation"
    assert "403" in by_id["DS-002"]["condition_gap"]
    assert by_id["DS-010"]["status"] == "validated-direct-flux"
    assert by_id["DS-010"]["file"] == "DS-010.csv"
    assert "SiO volatile proxy" in by_id["DS-010"]["condition_gap"]
    assert by_id["DS-012"]["status"] == "unavailable-automation"
    assert "session-gated" in by_id["DS-012"]["condition_gap"]


def test_validation_lake_reports_dimensionally_honest_comparisons() -> None:
    reports = _by_dataset()

    assert reports["DS-001"].rows_evaluated == 3
    assert reports["DS-003"].rows_evaluated == 12
    assert reports["DS-006"].rows_evaluated == 4
    assert reports["DS-007"].median_abs_dex_error == pytest.approx(
        0.16188054473371416
    )
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
    assert all(math.isfinite(item.error_factor) for item in direct_flux)


def test_endpoint_rank_metric_is_not_labeled_as_kinetic_ordering() -> None:
    report = _by_dataset()["DS-003"]

    payload = report.as_dict()
    assert "endpoint_rank_disagreement_fraction" in payload
    assert "ordering_inversion_fraction" not in payload
    # JANAF-4th multiphase re-ground moves the model Mg/SiO endpoint ordering;
    # the observed KEMS rows are unchanged, and this derived residual records it.
    assert report.endpoint_rank_disagreement_fraction == pytest.approx(
        0.08823529411764706
    )


def test_markdown_report_contains_summary_and_json_pointer_for_long_skips() -> None:
    reports = validate_lake(DEFAULT_DATA_ROOT)
    markdown = render_markdown_report(reports, catalog=load_catalog())

    assert "endpoint rank disagreement" in markdown
    assert "ordering inversions" not in markdown
    assert "| DS-007 | validated | 12 | 0.162 | 1.097 | - | - |" in markdown
    assert "| DS-010 | validated | 7 | 1.392 | 1.920 | 0.222 |" in markdown
    assert "more in JSON" in markdown
    assert "DS-010: fetch-dns-blocked" not in markdown
