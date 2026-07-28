from __future__ import annotations

from pathlib import Path

import pytest

from simulator.diagnostic_helpers.mre import evaluate_mre_comparison
from simulator.diagnostic_helpers.reproduction_compare import compare_values
from tests.mre_reproduction_fixtures import synthetic_program, synthetic_voltage_documents


def _runtime_result() -> dict:
    program = synthetic_program("one_hour", max_interval_min=60.0)
    return {
        "status": "ok",
        "reason": "",
        "error_message": "",
        "run_metadata": {
            "execution_origin": "literature-reproduction",
            "case_id": "one_hour",
        },
        "mre_reproduction": {
            "schema_version": "mre_reproduction.v1",
            "execution_origin": "literature-reproduction",
            "case_id": "one_hour",
            "controls_digest": program.controls_digest,
            "intervals": [
                {
                    "start_h": 0.0,
                    "end_h": 1.0,
                    "dt_h": 1.0,
                    "applied_current_A": 0.5,
                    "applied_voltage_V": 0.8,
                }
            ],
            "cumulative": {
                "applied_charge_C": 1800.0,
                "committed_electron_charge_C": 846.0,
                "mre_anode_o2_mol": 0.002,
                "mre_anode_o2_kg": 0.000064,
                "metals_mol_by_species": {"Fe": 0.004},
                "metals_kg_by_species": {"Fe": 0.00022338},
                "mass_balance_error_pct": 0.0,
            },
        },
    }


def test_mre_comparison_preserves_domain_and_typed_gaps(tmp_path: Path) -> None:
    preset, observations = synthetic_voltage_documents()
    comparison = evaluate_mre_comparison(
        preset,
        observations,
        _runtime_result(),
    )
    payload = comparison.as_payload(
        sidecar_path="data/literature/mre_measurements.yaml",
        markdown_path=str(tmp_path / "result.comparison.md"),
    )

    assert payload["schema_version"] == 2
    assert payload["domain"] == "mre"
    assert payload["preset_kind"] == "mre_reproduction"
    assert payload["execution_scope"] == "literature_reproduction_only"
    assert payload["paper_id"] == "yu_2025_hollow_anode"
    assert payload["case_id"] == "one_hour"
    assert set(payload["digests"]) == {
        "recipe_sha256",
        "source_sha256",
        "result_sha256",
        "controls_sha256",
    }
    records = {row["observable_id"]: row for row in payload["records"]}
    assert records["mre_applied_charge_C"]["status"] == "match"
    assert records["mre_anode_o2_mass_kg"]["status"] == "out-of-domain"
    assert records["mre_anode_o2_mass_kg"]["actual_value"] == pytest.approx(
        0.000064
    )
    assert records["mre_faradaic_efficiency_fraction"]["status"] == "out-of-domain"
    qualitative = {
        row["observable_id"]: row for row in payload["qualitative_observations"]
    }
    assert qualitative["cathodic_element_presence:P"]["status"] == (
        "unsupported-speciation"
    )
    assert qualitative["cathodic_element_presence:P"]["representation_status"] == (
        "not-representable"
    )
    assert qualitative["cathodic_element_presence:Mn"]["status"] == "out-of-domain"
    assert qualitative["cathodic_element_presence:Mn"]["authority_disposition"] == (
        "diagnostic-only"
    )
    assert qualitative["cathodic_element_presence:Mn"][
        "diagnostic_comparison"
    ] == "match"
    assert any(
        row["observable_id"] == "cathodic_eds_atomic_fraction"
        for row in payload["unsupported_observables"]
    )


def test_mre_markdown_disclaims_voltage_replay_and_o2_domain(tmp_path: Path) -> None:
    preset, observations = synthetic_voltage_documents()
    comparison = evaluate_mre_comparison(
        preset,
        observations,
        _runtime_result(),
    )

    markdown = comparison.markdown(
        comparison_artifact_path=tmp_path / "result.comparison.json"
    )

    assert "# MRE literature comparison: yu_2025_hollow_anode / one_hour" in markdown
    assert "measured response, not a model prediction" in markdown
    assert "exterior-RGA collected O2" in markdown
    assert "## Unsupported observables" in markdown
    assert "Controls: `sha256:" in markdown


def test_shared_status_precedence_keeps_out_of_domain_first() -> None:
    record = compare_values(
        case_id="precedence",
        source_id="source",
        observable_id="observable",
        species="P",
        coordinate={"time_h": 1.0},
        expected_value=1.0,
        expected_uncertainty={"kind": "absolute", "value": 0.1},
        actual_value=None,
        units="mol",
        evidence_scope="test",
        source_locator={"fixture": "precedence"},
        recipe={"a": 1},
        observation={"b": 2},
        runtime={"c": 3},
        unsupported_speciation=True,
        out_of_domain=True,
        assumed_input=True,
    )

    assert record.status == "out-of-domain"
