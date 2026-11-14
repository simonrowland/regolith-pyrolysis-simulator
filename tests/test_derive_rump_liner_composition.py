from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import derive_rump_liner_composition as generator
from simulator.diagnostics import achieved_melt_bakeout
from simulator.runner import _completed_runner_status


def test_achieved_bakeout_uses_committed_melt_history():
    achieved = achieved_melt_bakeout(
        (
            SimpleNamespace(temperature_C=1800.0, duration_h=2.0),
            SimpleNamespace(temperature_C=1838.0, duration_h=3.0),
            SimpleNamespace(temperature_C=1843.0, duration_h=4.0),
        ),
        setpoint_C=1843.0,
        tolerance_C=5.0,
    )

    assert achieved == {
        "peak_melt_temperature_C": 1843.0,
        "hold_threshold_C": 1838.0,
        "hold_duration_h": 7.0,
    }


def test_terminal_rump_keeps_bare_species_separate_from_oxide_normalization():
    result = generator._terminal_rump_composition(
        {
            "product_classification": {
                "classification": {
                    "refractory_ceramic_rump": {
                        "rump_kg_by_species": {
                            "SiO2": 9.0,
                            "Al2O3": 1.0,
                            "Na": 0.5,
                            "Ag": 0.2,
                        }
                    }
                }
            }
        }
    )

    assert result["composition_wt_pct"] == {"Al2O3": 10.0, "SiO2": 90.0}
    assert result["unassigned_trace_species"] == {
        "species_kg": {"Ag": 0.2, "Na": 0.5},
        "total_mass_kg": 0.7,
        "fraction_of_reported_rump_mass": pytest.approx(0.7 / 10.7),
        "excluded_from_oxide_normalization": True,
    }


def test_completed_campaign_sequence_status_is_exact_and_does_not_require_full_hours():
    assert _completed_runner_status("ok", "", sequence_complete=True) is True
    assert _completed_runner_status(
        "partial", "", sequence_complete=True
    ) is True
    assert _completed_runner_status("ok", "", sequence_complete=False) is False
    assert _completed_runner_status(
        "partial", "", sequence_complete=False
    ) is False
    assert _completed_runner_status(
        "ok", "stage0_exit", sequence_complete=True
    ) is False
    assert _completed_runner_status("partial", "stage0_exit", sequence_complete=True) is False
    assert _completed_runner_status(
        "ok", "stage0_exit_extra", sequence_complete=True
    ) is False


def test_runner_refusal_preserves_full_status_and_reason_without_document_truncation(
    monkeypatch,
):
    class RefusedRun:
        def __init__(self, **_kwargs):
            pass

        def run(self):
            return {
                "status": "refused",
                "reason": "c4_preheat_wall_clock_exhausted",
                "error_message": "x" * 3000,
            }

    monkeypatch.setattr(generator, "PyrolysisRun", RefusedRun)
    with pytest.raises(generator.GeneratorRefusal) as refused:
        generator.main(
            [
                "--bakeout-temperature-c",
                "1843",
                "--hold-duration-h",
                "160",
            ]
        )

    assert refused.value.reason == "run_refused:c4_preheat_wall_clock_exhausted"
    assert "runner status='refused'" in refused.value.detail
    assert "blocked on b-1049" in refused.value.detail


def test_generator_records_achieved_history_and_accepts_completed_early_sequence(
    monkeypatch, tmp_path: Path
):
    class CompletedSequenceRun:
        def __init__(self, **_kwargs):
            self._last_execution = SimpleNamespace(
                simulator=SimpleNamespace(is_complete=lambda: True),
                snapshots=(
                    SimpleNamespace(temperature_C=1840.0, duration_h=2.0),
                    SimpleNamespace(temperature_C=1843.0, duration_h=1.0),
                )
            )

        def run(self):
            return {
                "status": "partial",
                "reason": "",
                "run_metadata": {"hours_requested": 400, "hours_completed": 3},
                "product_classification": {
                    "classification": {
                        "refractory_ceramic_rump": {
                            "rump_kg_by_species": {
                                "SiO2": 9.0,
                                "Al2O3": 1.0,
                                "Na": 0.5,
                            }
                        }
                    }
                },
            }

    monkeypatch.setattr(generator, "PyrolysisRun", CompletedSequenceRun)
    monkeypatch.setattr(generator, "_repo_sha", lambda: "fixture-sha")
    output = tmp_path / "liner.yaml"

    assert generator.main(
        [
            "--bakeout-temperature-c",
            "1843",
            "--hold-duration-h",
            "3",
            "--output",
            str(output),
        ]
    ) == 0
    generated = yaml.safe_load(output.read_text(encoding="utf-8"))
    provenance = generated["provenance"]
    assert provenance["achieved_peak_melt_temperature_C"] == 1843.0
    assert provenance["achieved_hold_duration_h"] == 3.0
    assert provenance["hold_temperature_tolerance_C"] == 5.0
    assert provenance["requested_hold_duration_h"] == 3.0
    assert generated["unassigned_trace_species"]["total_mass_kg"] == 0.5


def test_generator_refuses_a_completed_run_that_missed_the_requested_hold(monkeypatch):
    class ShortHoldRun:
        def __init__(self, **_kwargs):
            self._last_execution = SimpleNamespace(
                simulator=SimpleNamespace(is_complete=lambda: True),
                snapshots=(SimpleNamespace(temperature_C=1837.0, duration_h=4.0),)
            )

        def run(self):
            return {"status": "partial", "reason": ""}

    monkeypatch.setattr(generator, "PyrolysisRun", ShortHoldRun)
    with pytest.raises(generator.GeneratorRefusal) as refused:
        generator.main(
            [
                "--bakeout-temperature-c",
                "1843",
                "--hold-duration-h",
                "4",
            ]
        )

    assert refused.value.reason == "bakeout_hold_shortfall"
    assert "runner status='partial'" in refused.value.detail
    assert "reason='none'" in refused.value.detail
    assert "achieved_hold_h=0" in refused.value.detail
