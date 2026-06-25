"""E6b: tests for the markdown formatter wrapping the E6a classifier.

Pin:
1. Empty classification renders a well-formed report (no crashes,
   all four class headers present).
2. Class totals snapshot line carries the documented format.
3. Per-class expansion shows the right subtotal labels.
4. Unclassified bin appears ONLY when non-empty.
5. Feedstock/campaign metadata appears when supplied.
6. Defensive: missing dict keys / non-finite values handled.
"""

from __future__ import annotations

import pytest

from simulator.three_product_report_markdown import (
    format_three_product_markdown,
)


def _empty_classification() -> dict:
    return {
        'metals_plus_O2': {
            'metals_kg': {},
            'metals_total_kg': 0.0,
            'O2_kg': 0.0,
            'class_total_kg': 0.0,
        },
        'pure_silica_glass': {
            'stage_3_capture_kg': 0.0,
            'stage_3_kg_by_species': {},
            'class_total_kg': 0.0,
        },
        'industrial_mixed_glass': {
            'mixed_melt_residual_kg': 0.0,
            'note': 'present only if recipe tapped early',
            'class_total_kg': 0.0,
        },
        'refractory_ceramic_rump': {
            'rump_kg_by_species': {},
            'rump_total_kg': 0.0,
            'rump_refractory_oxides_kg': 0.0,
            'rump_silicate_residual_kg': 0.0,
            'rump_unextracted_metals_kg': 0.0,
            'rump_other_kg': 0.0,
            'class_total_kg': 0.0,
        },
        'unclassified': {
            'kg_by_species': {},
            'total_kg': 0.0,
        },
    }


# ---------------------------------------------------------------------------
# 1. Empty classification produces a complete report shape
# ---------------------------------------------------------------------------

def test_empty_classification_renders_all_four_class_headers():
    """The 4 north-star product class headers MUST appear in every
    report, even when all values are zero (so the operator sees the
    class is empty, not missing)."""
    report = format_three_product_markdown(_empty_classification())
    assert "Metals + O₂" in report
    assert "Pure silica glass" in report
    assert "Industrial mixed glass" in report
    assert "Refractory ceramic rump" in report


def test_empty_classification_omits_unclassified_section():
    """The unclassified bin appears ONLY when non-empty — operator
    noise reduction. Empty case must NOT print the warning header."""
    report = format_three_product_markdown(_empty_classification())
    assert "Unclassified species" not in report


def test_class_totals_snapshot_line_present():
    """The 1-line totals snapshot must carry the documented
    pipe-separated format."""
    report = format_three_product_markdown(_empty_classification())
    assert "**Class totals**:" in report
    assert "Metals + O₂ potential:" in report
    assert "Silica glass:" in report
    assert "Mixed glass:" in report
    assert "Rump:" in report


# ---------------------------------------------------------------------------
# 2. Per-class expansion
# ---------------------------------------------------------------------------

def test_metals_class_renders_per_species_breakdown():
    """Metals class shows the per-species kg breakdown plus the
    metals/source-side O2 potential subtotal split."""
    classification = _empty_classification()
    classification['metals_plus_O2'] = {
        'metals_kg': {'Fe': 5.0, 'Na': 1.0, 'K': 0.5},
        'metals_total_kg': 6.5,
        'O2_kg': 2.0,
        'class_total_kg': 8.5,
    }
    report = format_three_product_markdown(classification)
    assert "**Fe**" in report
    assert "**Na**" in report
    assert "**K**" in report
    assert "Metals subtotal" in report
    assert "Source-side O₂ potential subtotal" in report


def test_silica_glass_class_shows_stage_3_capture():
    classification = _empty_classification()
    classification['pure_silica_glass'] = {
        'stage_3_capture_kg': 3.5,
        'stage_3_kg_by_species': {'SiO': 3.0, 'SiO2': 0.5},
        'class_total_kg': 3.5,
    }
    report = format_three_product_markdown(classification)
    assert "Stage 3 capture" in report
    assert "**SiO**" in report
    assert "**SiO2**" in report


def test_rump_class_shows_per_species_breakdown():
    classification = _empty_classification()
    classification['refractory_ceramic_rump'] = {
        'rump_kg_by_species': {'CaO': 50.0, 'REE_oxides': 2.5},
        'rump_total_kg': 52.5,
        'rump_refractory_oxides_kg': 52.5,
        'rump_silicate_residual_kg': 0.0,
        'rump_unextracted_metals_kg': 0.0,
        'rump_other_kg': 0.0,
        'class_total_kg': 52.5,
    }
    report = format_three_product_markdown(classification)
    assert "**CaO**" in report
    assert "**REE_oxides**" in report
    assert "Rump total" in report
    assert "Refractory oxides floor (by physics)" in report
    assert "Silicate residual" in report
    assert "Unextracted metals residue (failure-mode 1)" in report
    assert "Other / unclassified rump" in report


# ---------------------------------------------------------------------------
# 3. Unclassified bin surfaces when non-empty
# ---------------------------------------------------------------------------

def test_unclassified_section_appears_when_present():
    """A future species not in the canonical lists MUST surface in
    a clearly-labeled warning section so operators see the mapping
    gap."""
    classification = _empty_classification()
    classification['unclassified'] = {
        'kg_by_species': {'NewExoticHalide': 1.5},
        'total_kg': 1.5,
    }
    report = format_three_product_markdown(classification)
    assert "Unclassified species" in report
    assert "mapping gap" in report.lower()
    assert "**NewExoticHalide**" in report


# ---------------------------------------------------------------------------
# 4. Optional metadata
# ---------------------------------------------------------------------------

def test_feedstock_and_campaign_metadata_appear_when_supplied():
    classification = _empty_classification()
    report = format_three_product_markdown(
        classification,
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A_continuous",
    )
    assert "lunar_mare_low_ti" in report
    assert "C2A_continuous" in report


def test_title_argument_propagates_to_header():
    classification = _empty_classification()
    report = format_three_product_markdown(
        classification,
        title="C2A Lunar Run — Final Report",
    )
    assert "# C2A Lunar Run — Final Report" in report


# ---------------------------------------------------------------------------
# 5. Defensive paths
# ---------------------------------------------------------------------------

def test_missing_bucket_keys_handled_gracefully():
    """A classification dict missing some bucket keys (e.g. an old
    consumer's snapshot) must still render — the formatter falls
    back to empty dicts."""
    minimal = {
        'metals_plus_O2': {'class_total_kg': 0.0},
        'pure_silica_glass': {'class_total_kg': 0.0},
        'industrial_mixed_glass': {'class_total_kg': 0.0},
        'refractory_ceramic_rump': {'class_total_kg': 0.0},
        'unclassified': {'total_kg': 0.0},
    }
    report = format_three_product_markdown(minimal)
    assert "Metals + O₂" in report
    assert "—" in report or "0.000" in report  # totals rendered


def test_small_values_use_scientific_notation():
    """Values < 1 kg use sci notation; values ≥ 1 kg use 3 decimals.
    Documents the precision contract."""
    classification = _empty_classification()
    classification['metals_plus_O2'] = {
        'metals_kg': {'Fe': 5.0},          # large → decimals
        'metals_total_kg': 5.0,
        'O2_kg': 0.0001,                    # small → sci notation
        'class_total_kg': 5.0001,
    }
    report = format_three_product_markdown(classification)
    # Fe at 5 kg → "5.000"
    assert "5.000" in report
    # O2 at 0.0001 → scientific notation "1.000e-04"
    assert "1.000e-04" in report


def test_below_noise_floor_renders_as_dash():
    """Values below the noise floor (1e-9 kg) render as ``—`` rather
    than a near-zero number — keeps the operator readout clean."""
    classification = _empty_classification()
    classification['metals_plus_O2'] = {
        'metals_kg': {},
        'metals_total_kg': 1.0e-12,
        'O2_kg': 0.0,
        'class_total_kg': 1.0e-12,
    }
    report = format_three_product_markdown(classification)
    # The snapshot line uses "—" for sub-noise values.
    assert "—" in report


# ---------------------------------------------------------------------------
# 6. Output is a single newline-terminated string
# ---------------------------------------------------------------------------

def test_output_is_string_terminated_by_single_newline():
    """The report ends with exactly one trailing newline (file-write-
    friendly)."""
    report = format_three_product_markdown(_empty_classification())
    assert isinstance(report, str)
    assert report.endswith("\n")
    assert not report.endswith("\n\n")
