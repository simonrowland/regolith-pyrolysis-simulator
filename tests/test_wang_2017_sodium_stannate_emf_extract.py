from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery import migrate

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/wang-2017-sodium-stannate-formation-emf.yaml"


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _table_rows(document, observation_id):
    for context in document["species"]["Na2O"]["context"]:
        if context["observation_id"] == observation_id:
            return context["values"]["rows_as_printed"]
    raise AssertionError(f"missing context row {observation_id}")


def test_html_tables_and_equation_fits_match_independent_transcription():
    document = _document()
    table1 = _table_rows(document, "wang_2017_table1_electrolyte_phase_content")
    table2 = _table_rows(document, "wang_2017_table2_electrolyte_parameters")
    assert len(table1) == len(table2) == 4
    assert table1[2] == {
        "Na2O_content_wt_percent": "10.0",
        "I_beta_026": "1307",
        "I_beta_double_prime_2010": "9640",
        "beta_double_prime_phase_wt_percent": "86.24",
    }
    assert table2[2] == {
        "Na2O_content_wt_percent": "10.0",
        "resistance_ohm": "4.62",
        "thickness_cm": "0.29",
        "area_cm2": "0.2826",
        "resistivity_ohm_cm": "4.51",
        "density_g_cm3": "3.25",
    }

    fits = {
        row["observation_id"]: row
        for row in document["species"]["Electromotive_force"]["context"]
    }
    assert fits["wang_2017_eq4_reference_emf_fit_schema_gap"]["values"]["fit_as_printed"] == "153.24 + 0.1078T ± 2.32"
    assert fits["wang_2017_eq8_test_cell_emf_fit_schema_gap"]["values"]["fit_as_printed"] == "589.94 − 0.2660T ± 13.143"

    formation = document["species"]["Na2SnO3"]["observations"][0]
    assert formation["T_range_K"] == [773, 1273]
    assert formation["values"]["delta_fG_function_as_printed"] == "−1040.83 + 0.2221T ± 7.54"
    assert formation["values"]["delta_fG_intercept_as_printed"] == "−1040.83"
    assert formation["values"]["delta_fG_slope_as_printed"] == "0.2221"
    assert formation["values"]["uncertainty_as_printed"] == "± 7.54"
    assert formation["values"]["equation_10_cell_reaction_relation"] == "ΔG° = −nE⁰F = −nEF"
    assert formation["values"]["equation_11_relation_as_printed"] == "ΔG° = 2Δ_fG°(NaCrO2) + Δ_fG°(SnO2) − Δ_fG°(Cr2O3) − Δ_fG°(Na2SnO3)"
    assert formation["values"]["method_class"] == "quoted_attributed"
    assert formation["values"]["printed_uncertainty_note"] == "confidence level not stated"


def test_isolated_migration_preserves_fit_and_html_context_without_assumed_conditions():
    migrator = migrate.Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == 1
    assert result.validation.hard_issues == ()

    observation = next(iter(result.observations.values()))
    assert observation.source_id == "wang-2017-sodium-stannate-formation-emf"
    assert observation.evidence.original_method_class == "quoted_attributed"
    assert observation.value.kind.value == "unavailable"
    assert observation.identity.temperature_K.tag.value == "unknown"
    assert observation.point_conditions is None
    contexts = [row for rows in result.context_by_work.values() for row in rows]
    assert len(contexts) == 4
    assert len([row for row in contexts if row["source_id"] == observation.source_id]) == 4

    source = _document()
    gaps = [
        row for row in source["species"]["Electromotive_force"]["context"]
        if row["values"]["status"] == "typed_refused_schema_gap"
    ]
    assert len(gaps) == 2
    assert all("reference_Na2O_activity" not in row["values"] or "not numerically stated" in row["values"]["reference_Na2O_activity"] for row in gaps)
