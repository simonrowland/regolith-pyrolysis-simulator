from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL, parse_formula
from simulator.diagnostic_helpers.vacuum_pyrolysis import (
    VacuumPyrolysisComparisonError,
    evaluate_vacuum_pyrolysis_comparison,
    resolve_vacuum_observable,
)


REPO_ROOT = Path(__file__).resolve().parent.parent


def _runtime() -> dict:
    return {
        "status": "ok",
        "run_metadata": {
            "feedstock_id": "synthetic_silica",
            "mass_kg": 2.0,
        },
        "per_hour_summary": [
            {
                "hour": 1,
                "O2_source_side_potential_kg_cumulative": 0.01,
                "vapor_species_kg_hr": {"SiO": 0.001},
            },
            {
                "hour": 2,
                "O2_source_side_potential_kg_cumulative": 0.03,
                "vapor_species_kg_hr": {"SiO": 0.004},
            },
        ],
        "final": {
            "pump_outlet_by_species_kg": {
                "SiO": 0.1,
                "Na": 0.02,
            }
        },
    }


def _feedstocks() -> dict:
    return {
        "synthetic_silica": {
            "composition_wt_pct": {
                "SiO2": 100.0,
            }
        }
    }


@pytest.mark.parametrize(
    ("selector", "coordinate", "expected"),
    [
        (
            {"kind": "final_o2_mass_kg"},
            {"time_h": 2.0},
            0.03,
        ),
        (
            {"kind": "window_o2_mass_kg"},
            {"start_h": 1.0, "end_h": 2.0},
            0.02,
        ),
        (
            {"kind": "o2_mass_yield_fraction"},
            {"start_h": 0.0, "end_h": 2.0},
            0.015,
        ),
        (
            {"kind": "non_condensed_mass_loss_fraction"},
            {"start_h": 0.0, "end_h": 2.0},
            0.06,
        ),
        (
            {
                "kind": "species_time_series_kg_hr",
                "species": "SiO",
                "aggregation": "point",
            },
            {"time_h": 2.0},
            0.004,
        ),
        (
            {
                "kind": "species_time_series_kg_hr",
                "species": "SiO",
                "aggregation": "max",
            },
            {"start_h": 0.0, "end_h": 2.0},
            0.004,
        ),
    ],
)
def test_resolve_vacuum_observable(
    selector: dict,
    coordinate: dict,
    expected: float,
) -> None:
    actual, unsupported_speciation = resolve_vacuum_observable(
        selector,
        coordinate,
        _runtime(),
        feedstocks=_feedstocks(),
    )

    assert actual == pytest.approx(expected)
    assert unsupported_speciation is False


def test_feed_oxygen_selector_uses_feed_oxide_oxygen_mass() -> None:
    actual, unsupported_speciation = resolve_vacuum_observable(
        {"kind": "feed_oxygen_extraction_fraction"},
        {"start_h": 0.0, "end_h": 2.0},
        _runtime(),
        feedstocks=_feedstocks(),
    )
    silica = parse_formula("SiO2")
    oxygen_fraction = (
        2.0 * ATOMIC_WEIGHTS_G_PER_MOL["O"] / silica.molar_mass_g_mol
    )

    assert actual == pytest.approx(0.03 / (2.0 * oxygen_fraction))
    assert unsupported_speciation is False


def test_non_condensed_selector_refuses_runtime_pump_outlet_sentinel() -> None:
    runtime = _runtime()
    runtime["final"]["pump_outlet_by_species_kg"] = "not_applicable_until_p0"

    actual, unsupported_speciation = resolve_vacuum_observable(
        {"kind": "non_condensed_mass_loss_fraction"},
        {"start_h": 0.0, "end_h": 2.0},
        runtime,
        feedstocks=_feedstocks(),
    )

    assert actual is None
    assert unsupported_speciation is False


def test_non_condensed_selector_represents_empty_pump_outlet_as_zero() -> None:
    runtime = _runtime()
    runtime["final"]["pump_outlet_by_species_kg"] = {}

    actual, unsupported_speciation = resolve_vacuum_observable(
        {"kind": "non_condensed_mass_loss_fraction"},
        {"start_h": 0.0, "end_h": 2.0},
        runtime,
        feedstocks=_feedstocks(),
    )

    assert actual == pytest.approx(0.0)
    assert unsupported_speciation is False


def test_species_selector_reports_unsupported_speciation() -> None:
    actual, unsupported_speciation = resolve_vacuum_observable(
        {
            "kind": "species_time_series_kg_hr",
            "species": "Si",
            "aggregation": "point",
        },
        {"time_h": 2.0},
        _runtime(),
        feedstocks=_feedstocks(),
    )

    assert actual is None
    assert unsupported_speciation is True


def test_comparison_marks_assumed_recipe_inputs_and_carries_digests() -> None:
    preset = {
        "paper_id": "synthetic_paper",
        "paper_citation_id": "synthetic_source",
        "measurement_id": "synthetic_measurement",
        "lab_schedule": {
            "source_class": "assumption_with_sensitivity_marker",
        },
        "measurement_selectors": [
            {
                "observable_id": "synthetic_o2",
                "kind": "final_o2_mass_kg",
                "species": "O2",
                "units": "kg",
                "evidence_scope": "source_side_test",
                "certification": {
                    "status": "certifiable",
                    "blocked_by": [],
                },
            }
        ],
    }
    observations = {
        "schema_version": "vacuum_pyrolysis_measurements.v1",
        "measurements": {
            "synthetic_measurement": {
                "paper_citation": {
                    "citation_id": "synthetic_source",
                },
                "comparison_points": [
                    {
                        "observable_id": "synthetic_o2",
                        "coordinate": {"time_h": 2.0},
                        "expected_value": 0.03,
                        "uncertainty": {
                            "kind": "absolute",
                            "value": 0.0,
                        },
                        "units": "kg",
                        "status": "reported",
                        "source_locator": {
                            "table": "synthetic",
                        },
                    }
                ],
                "qualitative_comparison_observations": [],
            }
        },
    }

    comparison = evaluate_vacuum_pyrolysis_comparison(
        preset,
        observations,
        _runtime(),
        feedstocks=_feedstocks(),
    )

    assert comparison.records[0].status == "assumed-input"
    assert comparison.records[0].residual == pytest.approx(0.0)
    assert comparison.recipe_digest == comparison.records[0].recipe_digest
    assert comparison.source_digest == comparison.records[0].observation_digest
    assert comparison.result_digest == comparison.records[0].runtime_digest


def test_robinot_distribution_preset_emits_analyzer_scope_mismatch() -> None:
    preset = yaml.safe_load(
        (
            REPO_ROOT
            / "data/presets/vacuum_pyrolysis/robinot_2026.yaml"
        ).read_text(encoding="utf-8")
    )
    observations = yaml.safe_load(
        (
            REPO_ROOT
            / "data/literature/vacuum_pyrolysis_measurements.yaml"
        ).read_text(encoding="utf-8")
    )
    feedstocks = yaml.safe_load(
        (REPO_ROOT / "data/feedstocks.yaml").read_text(encoding="utf-8")
    )
    runtime = _runtime()
    runtime["run_metadata"].update(
        {
            "feedstock_id": preset["pair"]["faithful"]["feedstock_id"],
            "mass_kg": preset["lab_geometry"]["sample"]["mass_g"] / 1000.0,
        }
    )

    comparison = evaluate_vacuum_pyrolysis_comparison(
        preset,
        observations,
        runtime,
        feedstocks=feedstocks,
    )

    assert [record.evidence_scope for record in comparison.records] == [
        "source_side_o2_potential_vs_analyzer_visible_o2",
        (
            "source_side_o2_potential_vs_analyzer_visible_o2_"
            "divided_by_reported_initial_feed_mass"
        ),
        (
            "source_side_o2_potential_vs_analyzer_visible_o2_"
            "divided_by_feed_oxide_oxygen_mass"
        ),
    ]


def test_qualitative_observation_rejects_fake_numeric_score() -> None:
    preset = {
        "paper_id": "synthetic_paper",
        "paper_citation_id": "synthetic_source",
        "measurement_id": "synthetic_measurement",
        "measurement_selectors": [],
    }
    observations = {
        "schema_version": "vacuum_pyrolysis_measurements.v1",
        "measurements": {
            "synthetic_measurement": {
                "paper_citation": {
                    "citation_id": "synthetic_source",
                },
                "comparison_points": [],
                "qualitative_comparison_observations": [
                    {
                        "observation_id": "spatial_deposit",
                        "status": "observed",
                        "representation_status": "not-representable",
                        "coordinate": {"surface": "window"},
                        "score": 1.0,
                    }
                ],
            }
        },
    }

    with pytest.raises(
        VacuumPyrolysisComparisonError,
        match="cannot carry fake numerics",
    ):
        evaluate_vacuum_pyrolysis_comparison(
            preset,
            observations,
            _runtime(),
            feedstocks=_feedstocks(),
        )
