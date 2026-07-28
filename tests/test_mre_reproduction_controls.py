from __future__ import annotations

from dataclasses import replace

import pytest

import simulator.mre_ladder as mre_ladder
import simulator.mre_reproduction as reproduction_module
import simulator.session as session_module
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.planner import ChemistryKernel
from simulator.config import load_config_bundle
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.mre_reproduction import (
    load_mre_reproduction_program,
    run_mre_reproduction,
)
from simulator.state import CampaignPhase, MOLAR_MASS
from tests.mre_reproduction_fixtures import (
    DURATIONS_H,
    synthetic_program,
    synthetic_voltage_documents,
)


def _run(case_id: str) -> dict:
    bundle = load_config_bundle()
    return run_mre_reproduction(
        synthetic_program(case_id, max_interval_min=60.0),
        feedstocks=bundle.feedstocks,
        setpoints=bundle.setpoints,
        vapor_pressures=bundle.vapor_pressures,
        materials=bundle.materials,
        backend_name="internal-analytical",
    )


@pytest.mark.parametrize(
    ("case_id", "expected_charge_C"),
    (
        ("one_hour", 1800.0),
        ("three_hour", 5400.0),
        ("twelve_hour", 21600.0),
    ),
)
def test_yu_cases_execute_only_paper_scale_controls(
    case_id: str,
    expected_charge_C: float,
) -> None:
    result = _run(case_id)
    reproduction = result["mre_reproduction"]
    intervals = reproduction["intervals"]

    assert result["status"] == "ok"
    assert reproduction["execution_origin"] == "literature-reproduction"
    assert reproduction["case_id"] == case_id
    assert reproduction["temperature_C"] == pytest.approx(1600.0)
    assert reproduction["gas_boundary"]["carrier_gas"] == "Ar"
    assert reproduction["gas_boundary"]["carrier_flow_sccm"] == pytest.approx(100.0)
    assert intervals
    assert {row["applied_current_A"] for row in intervals} == {0.5}
    assert all(
        row["applied_current_A"] not in {1000.0, 3000.0}
        for row in intervals
    )
    assert {row["temperature_C"] for row in intervals} == {1600.0}
    assert sum(row["dt_h"] for row in intervals) == pytest.approx(
        DURATIONS_H[case_id]
    )
    assert reproduction["cumulative"]["applied_charge_C"] == pytest.approx(
        expected_charge_C
    )
    assert max(
        abs(row["mass_balance_error_pct"]) for row in intervals
    ) < 1e-9


def test_yu_driver_isolated_from_plant_policy_and_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("plant policy leaked into literature reproduction")

    monkeypatch.setattr(mre_ladder, "C5_LIMITED_MRE_CURRENT_A", forbidden)
    monkeypatch.setattr(mre_ladder, "build_mre_voltage_sequence", forbidden)
    monkeypatch.setattr(session_module, "normalize_mre_policy", forbidden)
    monkeypatch.setattr(
        PyrolysisSimulator,
        "_select_plant_mre_interval_control",
        forbidden,
    )

    result = _run("one_hour")

    assert result["status"] == "ok"
    assert {
        row["applied_current_A"]
        for row in result["mre_reproduction"]["intervals"]
    } == {0.5}


def test_primary_runtime_contains_no_expected_observation_values() -> None:
    result = _run("one_hour")

    assert "expected_value" not in repr(result)
    assert "measurement" not in result
    assert "comparison" not in result


def test_event_grid_is_strict_for_near_coincident_voltage_and_observation_knots(
) -> None:
    preset, observations = synthetic_voltage_documents()
    five_minutes_h = 5.0 / 60.0
    trajectory = observations["measurements"][
        "yu_2025_hollow_anode_measurements"
    ]["control_trajectories"]["yu_figure_2b_cell_potential"]
    trajectory["cases"]["one_hour"]["points"].insert(
        1,
        {
            "time_h": round(five_minutes_h, 12),
            "voltage_V": 0.8,
            "unit": "V",
            "status": "published_digitized",
            "digitization_uncertainty_V": 0.01,
            "source_locator": {
                "fixture": "near_coincident_voltage_knot",
            },
        },
    )
    preset["sampling"]["observation_times_h"] = [
        five_minutes_h + 2.0e-13,
    ]

    program = load_mre_reproduction_program(preset, observations, "one_hour")
    grid = program.sampling_times_h

    assert grid == tuple(sorted(set(grid)))
    assert all(left < right for left, right in zip(grid, grid[1:]))
    assert grid.count(round(five_minutes_h, 12)) == 1
    assert len(program.intervals()) == len(grid) - 1


def test_gas_basis_metal_stays_in_interval_and_cumulative_mol_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = load_config_bundle()
    feedstocks = dict(bundle.feedstocks)
    feedstocks["mgo_reproduction_poison"] = {
        "label": "MgO reproduction poison",
        "composition_wt_pct": {"MgO": 100.0},
    }
    program = synthetic_program("one_hour", max_interval_min=30.0)
    voltage_program = tuple(
        {
            **dict(point),
            "voltage_V": 3.0,
        }
        for point in program.voltage_program
    )
    program = replace(
        program,
        feedstock_id="mgo_reproduction_poison",
        voltage_program=voltage_program,
    )
    routed_gas_products = []
    original_route = PyrolysisSimulator._route_mre_gas_products_to_condensation

    def tracking_route(self, gas_products_kg, **kwargs):
        routed_gas_products.append(dict(gas_products_kg))
        return original_route(self, gas_products_kg, **kwargs)

    monkeypatch.setattr(
        PyrolysisSimulator,
        "_route_mre_gas_products_to_condensation",
        tracking_route,
    )

    result = run_mre_reproduction(
        program,
        feedstocks=feedstocks,
        setpoints=bundle.setpoints,
        vapor_pressures=bundle.vapor_pressures,
        materials=bundle.materials,
        backend_name="internal-analytical",
    )
    intervals = result["mre_reproduction"]["intervals"]
    cumulative = result["mre_reproduction"]["cumulative"]

    assert len(intervals) > 1
    assert all(route.get("Mg", 0.0) > 0.0 for route in routed_gas_products)
    interval_mg_mol = sum(
        row["metals_delta_mol_by_species"].get("Mg", 0.0)
        for row in intervals
    )
    assert interval_mg_mol > 0.0
    for row in intervals:
        assert row["metals_delta_kg_by_species"].get("Mg", 0.0) == pytest.approx(
            row["metals_delta_mol_by_species"].get("Mg", 0.0)
            * MOLAR_MASS["Mg"]
            / 1000.0
        )
    assert cumulative["metals_mol_by_species"]["Mg"] == pytest.approx(
        interval_mg_mol
    )
    assert cumulative["metals_kg_by_species"]["Mg"] == pytest.approx(
        cumulative["metals_mol_by_species"]["Mg"] * MOLAR_MASS["Mg"] / 1000.0
    )


def test_every_reproduction_substep_commits_through_kernel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    committed_proposals = []
    original_commit_batch = ChemistryKernel.commit_batch

    def tracking_commit_batch(self, intent, proposal, **kwargs):
        if intent == ChemistryIntent.ELECTROLYSIS_STEP:
            committed_proposals.append(proposal)
        return original_commit_batch(self, intent, proposal, **kwargs)

    monkeypatch.setattr(ChemistryKernel, "commit_batch", tracking_commit_batch)
    bundle = load_config_bundle()
    result = run_mre_reproduction(
        synthetic_program("one_hour", max_interval_min=5.0),
        feedstocks=bundle.feedstocks,
        setpoints=bundle.setpoints,
        vapor_pressures=bundle.vapor_pressures,
        materials=bundle.materials,
        backend_name="internal-analytical",
    )
    intervals = result["mre_reproduction"]["intervals"]

    assert len(committed_proposals) == len(intervals)
    assert any(
        not proposal.debits and not proposal.credits
        for proposal in committed_proposals
    )
    assert all(
        row["kernel_commit_disposition"]
        in {"committed-transition", "committed-empty-transition"}
        for row in intervals
    )


@pytest.mark.parametrize("campaign", (CampaignPhase.C5, CampaignPhase.MRE_BASELINE))
def test_plant_mre_isolated_from_reproduction_loader_and_driver(
    monkeypatch: pytest.MonkeyPatch,
    campaign: CampaignPhase,
) -> None:
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {
            "campaigns": {},
            "mre_voltage_sequence": {
                "sequence": [
                    {
                        "species": "FeO",
                        "decomposition_V": 0.8,
                        "min_hold_hours": 1,
                    }
                ]
            },
        },
        {"x": {"label": "X", "composition_wt_pct": {"FeO": 100.0}}},
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("x", mass_kg=0.02)
    sim.start_campaign(campaign)
    sim.melt.temperature_C = 1600.0
    if campaign == CampaignPhase.C5:
        sim.melt.c5_enabled = True
        sim.melt.mre_target_species = "FeO"
        sim.melt.mre_max_voltage_V = 0.8

    def forbidden(*_args, **_kwargs):
        raise AssertionError("reproduction loader/driver leaked into plant MRE")

    monkeypatch.setattr(
        reproduction_module,
        "load_mre_reproduction_program",
        forbidden,
    )
    monkeypatch.setattr(reproduction_module, "run_mre_reproduction", forbidden)

    sim._step_mre()

    assert not hasattr(sim, "mre_reproduction")
    assert "yu_2025_hollow_anode" not in repr(sim.__dict__)
