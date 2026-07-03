from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml
import pytest

from simulator.state import CampaignPhase
from tests.chemistry.conftest import _build_sim


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EPS = 1.0e-12


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text())


def _diagnostic_sim():
    setpoints = deepcopy(_load_yaml("setpoints.yaml"))
    setpoints["freeze_gate"] = dict(setpoints.get("freeze_gate", {}) or {})
    setpoints["freeze_gate"]["enabled"] = False
    c2a = setpoints["campaigns"]["C2A_continuous"]
    c2a["target_yield_threshold"] = 0.99
    c2a["max_hold_hr"] = 99
    sim = _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        setpoints,
    )
    sim.start_campaign(CampaignPhase.C2A)
    return sim


def test_step_emits_extraction_completeness_side_channel() -> None:
    sim = _diagnostic_sim()

    sim.step()

    diag = sim._last_extraction_completeness_diagnostic
    assert diag["campaign"] == "C2A"
    assert "SiO" in diag["completeness_by_target_species"]
    assert diag["completeness_by_target_species"]["SiO"] is not None
    values = diag["completeness_by_target_species"]
    aggregate = diag["aggregate_completeness_fraction"]
    assert aggregate == pytest.approx(min(values.values()))
    assert values[diag["aggregate_worst_target_species"]] == pytest.approx(
        aggregate
    )
    assert diag["aggregate_policy"] == "min_all_targets"
    assert diag["aggregate_status"] == "ok"
    assert diag["would_be_soft_advance_by_target_species"]["SiO"][
        "would_advance"
    ] is False
    assert diag["would_be_soft_advance_aggregate"]["would_advance"] is False
    assert diag["would_be_hard_floor_advance"] is None
    assert diag["would_be_cap_advance"] is False
    assert "extraction_completeness" not in sim.record.snapshots[-1].__dict__


def test_completeness_diagnostic_does_not_change_campaign_advancement() -> None:
    with_diagnostic = _diagnostic_sim()
    without_diagnostic = _diagnostic_sim()
    without_diagnostic._update_extraction_completeness_diagnostic = lambda: None
    for sim in (with_diagnostic, without_diagnostic):
        sim.melt.campaign_hour = 30

    with_diagnostic.step()
    without_diagnostic.step()

    assert (
        with_diagnostic.melt.campaign,
        with_diagnostic.melt.hour,
        with_diagnostic.melt.campaign_hour,
        len(with_diagnostic.record.snapshots),
        with_diagnostic.paused_for_decision,
    ) == (
        without_diagnostic.melt.campaign,
        without_diagnostic.melt.hour,
        without_diagnostic.melt.campaign_hour,
        len(without_diagnostic.record.snapshots),
        without_diagnostic.paused_for_decision,
    )
    assert (
        with_diagnostic._last_extraction_completeness_diagnostic["campaign"]
        == "C2A"
    )


def test_c2a_completion_contracts_and_aggregate_are_monotonic() -> None:
    sim = _diagnostic_sim()
    sim.melt.temperature_C = 1450.0
    previous_by_target: dict[str, float] = {}
    previous_aggregate: float | None = None
    aggregate_values: list[float] = []

    for _ in range(8):
        sim.step()
        diag = sim._last_extraction_completeness_diagnostic
        targets = tuple(diag["target_species"])
        assert targets == ("Na", "K", "Fe", "CrO2", "SiO")

        values = diag["completeness_by_target_species"]
        for target in targets:
            fraction = values[target]
            assert fraction is not None
            assert (
                diag["detail_by_target_species"][target]["contract_id"]
            )
            assert (
                diag["detail_by_target_species"][target][
                    "denominator_basis_source"
                ]
                == "feedstock_derived_product_residual_wall_excluding_credit_line_"
                "and_external_additives"
            )
            assert (
                diag["detail_by_target_species"][target][
                    "credit_line_reagent_target_equiv_mol"
                ]
                == pytest.approx(0.0)
            )
            if target in previous_by_target:
                assert fraction + EPS >= previous_by_target[target]
            previous_by_target[target] = fraction

        aggregate = diag["aggregate_completeness_fraction"]
        assert aggregate is not None
        aggregate_values.append(aggregate)
        assert aggregate == pytest.approx(min(values[target] for target in targets))
        assert values[diag["aggregate_worst_target_species"]] == pytest.approx(
            aggregate
        )
        if previous_aggregate is not None:
            assert aggregate + EPS >= previous_aggregate
        previous_aggregate = aggregate

    assert aggregate_values[-1] > aggregate_values[0]


def test_c2a_overlap_diagnostic_reports_off_target_without_gating() -> None:
    sim = _diagnostic_sim()
    sim.melt.temperature_C = 1600.0
    sim.step()

    completeness = sim._last_extraction_completeness_diagnostic
    overlap = sim._last_overlap_evaporation_diagnostic
    assert "Mg" not in completeness["completeness_by_target_species"]
    assert completeness["target_species"] == ("Na", "K", "Fe", "CrO2", "SiO")
    assert overlap["campaign"] == "C2A"
    assert overlap["completion_target_species"] == completeness["target_species"]
    assert overlap["endpoint_species_monitored"] == ("Fe", "K", "Na", "SiO")

    for species, row in overlap["off_target_evaporation"].items():
        assert species not in completeness["target_species"]
        assert row["gates_completion"] is False
        assert row["rate_kg_hr"] > 0.0


def test_overlap_diagnostic_does_not_change_campaign_advancement() -> None:
    with_overlap = _diagnostic_sim()
    without_overlap = _diagnostic_sim()
    without_overlap._update_overlap_evaporation_diagnostic = lambda _flux: None
    for sim in (with_overlap, without_overlap):
        sim.melt.campaign_hour = 30
        sim.melt.temperature_C = 1600.0

    with_overlap.step()
    without_overlap.step()

    assert (
        with_overlap.melt.campaign,
        with_overlap.melt.hour,
        with_overlap.melt.campaign_hour,
        len(with_overlap.record.snapshots),
        with_overlap.paused_for_decision,
    ) == (
        without_overlap.melt.campaign,
        without_overlap.melt.hour,
        without_overlap.melt.campaign_hour,
        len(without_overlap.record.snapshots),
        without_overlap.paused_for_decision,
    )
    assert with_overlap._last_overlap_evaporation_diagnostic["campaign"] == "C2A"
