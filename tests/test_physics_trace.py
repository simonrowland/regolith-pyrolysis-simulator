from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from simulator.accounting.queries import (
    AccountingQueries,
    condensation_stage_purity_pct,
    stage_purity,
    wall_deposit_candidate_for_surface_kg,
    wall_deposit_candidate_kg,
    wall_deposit_candidates_by_segment_kg,
)
from simulator.core import CampaignPhase, PyrolysisSimulator
from simulator.mass_balance import MassBalance
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import EvaporationFlux, MOLAR_MASS
from simulator.trace import PhysicsTrace


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_data_yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text()) or {}


def _representative_sim(
    hours: int = 12,
    hold_temperature_C: float = 1500.0,
) -> PyrolysisSimulator:
    feedstocks = _load_data_yaml("feedstocks.yaml")
    setpoints = _load_data_yaml("setpoints.yaml")
    vapor_pressures = _load_data_yaml("vapor_pressures.yaml")
    setpoints = dict(setpoints)
    kernel_config = dict(setpoints.get("chemistry_kernel", {}) or {})
    kernel_config["allow_fallback_vapor"] = True
    kernel_config["allow_unmeasured_alpha_fallback"] = True
    setpoints["chemistry_kernel"] = kernel_config

    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(backend, setpoints, feedstocks, vapor_pressures)
    sim.load_batch("mars_basalt", mass_kg=1000.0, additives_kg={"C": 30.0})
    sim.start_campaign(CampaignPhase.C2A)
    # Post-6bb2c7f transport no longer over-evaporates on the cold early ramp
    # (pre-fix low-T Fe/Ca stage mass was non-physical). Hold inside the
    # C2A extraction window so condensation stages genuinely collect.
    for _ in range(hours):
        sim.melt.temperature_C = hold_temperature_C
        sim.step()
    return sim


def test_accounting_query_facade_matches_legacy_wrappers():
    sim = _representative_sim()
    queries = AccountingQueries(sim)

    # Real legacy-vs-facade parity is pinned by pre-R-F3 goldens in
    # test_runner_smoke and test_web_socket_trace; this stays a cheap smoke.
    assert sim.product_ledger() == queries.product_ledger()
    assert sim._terminal_rump_by_species() == queries.terminal_rump_by_species()
    assert sim._terminal_rump_by_class() == queries.terminal_rump_by_class()
    assert (
        sim._oxygen_terminal_partition_kg()
        == queries.oxygen_terminal_partition_kg()
    )
    assert (
        sim._condensation_totals_with_terminal_oxygen()
        == queries.condensation_totals_with_terminal_oxygen()
    )
    assert sim._actual_rump_elements_kg() == queries.actual_rump_elements_kg()
    for element in sorted(sim._RUMP_ELEMENT_SPECIES):
        assert sim._rump_element_kg(element) == queries.rump_element_kg(element)

    assert stage_purity(sim.train) == MassBalance().stage_purity(sim.train)
    stage = next(
        stage
        for stage in sim.train.stages
        if any(kg > 0.0 for kg in (stage.collected_kg or {}).values())
    )
    species = next(sp for sp, kg in stage.collected_kg.items() if kg > 0.0)
    assert stage.collected_kg[species] > 0.0
    assert stage.purity_pct(species) == condensation_stage_purity_pct(
        stage, species)

    model = sim.condensation_model
    supply = {segment.name: 0.05 for segment in model.pipe_segments}
    assert model._wall_deposit_candidate_kg(
        species="SiO",
        rate_kg_hr=0.05,
        T_cond_C=1100.0,
        melt_temperature_C=1600.0,
    ) == wall_deposit_candidate_kg(
        model,
        species="SiO",
        rate_kg_hr=0.05,
        T_cond_C=1100.0,
        melt_temperature_C=1600.0,
    )
    assert model._wall_deposit_candidates_by_segment_kg(
        species="SiO",
        rate_kg_hr=0.05,
        T_cond_C=1100.0,
        melt_temperature_C=1600.0,
        supply_by_segment_kg=supply,
    ) == wall_deposit_candidates_by_segment_kg(
        model,
        species="SiO",
        rate_kg_hr=0.05,
        T_cond_C=1100.0,
        melt_temperature_C=1600.0,
        supply_by_segment_kg=supply,
    )
    assert model._wall_deposit_candidate_for_surface_kg(
        species="SiO",
        rate_kg_hr=0.05,
        T_cond_C=1100.0,
        melt_temperature_C=1600.0,
        wall_temperature_C=1500.0,
        surface_area_m2=0.25,
    ) == wall_deposit_candidate_for_surface_kg(
        model,
        species="SiO",
        rate_kg_hr=0.05,
        T_cond_C=1100.0,
        melt_temperature_C=1600.0,
        wall_temperature_C=1500.0,
        surface_area_m2=0.25,
    )


def test_snapshot_deltas_sum_to_cumulative_trace_totals():
    sim = _representative_sim()
    trace = PhysicsTrace.from_simulator(sim)
    assert any(s.condensed_by_stage_species_delta for s in trace.snapshots)

    condensed_by_species = defaultdict(float)
    wall_by_segment_species = defaultdict(float)
    for snapshot in trace.snapshots:
        for (_stage, species), kg in (
            snapshot.condensed_by_stage_species_delta.items()
        ):
            condensed_by_species[species] += kg
        for key, kg in snapshot.wall_deposit_by_segment_species_delta.items():
            wall_by_segment_species[key] += kg

    for species, kg in condensed_by_species.items():
        assert trace.condensation_totals_kg.get(species, 0.0) == pytest.approx(
            kg, abs=1e-9)
    for key, kg in wall_by_segment_species.items():
        assert trace.wall_deposit_by_segment_species_kg.get(
            key, 0.0) == pytest.approx(kg, abs=1e-9)


def test_snapshot_o2_delta_cannot_overwrite_terminal_oxygen_trace_total():
    sim = _representative_sim()
    baseline_o2 = AccountingQueries(
        sim
    ).condensation_totals_with_terminal_oxygen().get("O2")
    snapshots = list(sim.record.snapshots)
    assert snapshots
    poisoned_delta = dict(snapshots[-1].condensed_by_stage_species_delta)
    poisoned_delta[(999, "O2")] = 12345.0
    snapshots[-1] = replace(
        snapshots[-1],
        condensed_by_stage_species_delta=poisoned_delta,
    )
    sim.record.snapshots = tuple(snapshots)

    trace = PhysicsTrace.from_simulator(sim)

    if baseline_o2 is None:
        assert "O2" not in trace.condensation_totals_kg
    else:
        assert trace.condensation_totals_kg["O2"] == pytest.approx(
            baseline_o2,
            abs=1e-12,
        )


def test_wall_deposit_delta_matches_route_projection_before_commit():
    """Fails if route -> kernel -> ledger drops, doubles, or misroutes walls."""

    sim = _representative_sim(hours=0)
    model = sim.condensation_model
    model.configure_operating_conditions(
        wall_temperature_C=900.0,
        pipe_segment_temperatures_C={
            segment.name: 900.0 for segment in model.pipe_segments
        },
    )
    sim.melt.temperature_C = 1700.0
    routes = []
    original_route = model.route

    def capture_route(evap_flux, melt):
        route = original_route(evap_flux, melt)
        routes.append(route)
        return route

    model.route = capture_route
    sim._last_wall_deposit_by_segment_species_delta = {}
    sim._route_to_condensation(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0)
    )

    route_projection = {
        (segment, species): kg
        for segment, species_kg in (
            routes[0].wall_deposit_by_segment_species.items())
        for species, kg in species_kg.items()
        if kg > 1e-12
    }
    expected_committed_delta = {}
    for (segment, species), kg in route_projection.items():
        if species == "SiO":
            expected_committed_delta[(segment, "Si")] = (
                kg * 0.5 * MOLAR_MASS["Si"] / MOLAR_MASS["SiO"]
            )
            expected_committed_delta[(segment, "SiO2")] = (
                kg * 0.5 * MOLAR_MASS["SiO2"] / MOLAR_MASS["SiO"]
            )
        else:
            expected_committed_delta[(segment, species)] = kg
    committed_delta = {
        key: kg
        for key, kg in sim._last_wall_deposit_by_segment_species_delta.items()
        if kg > 1e-12
    }

    assert route_projection
    assert committed_delta.keys() == expected_committed_delta.keys()
    for key, kg in expected_committed_delta.items():
        assert committed_delta[key] == pytest.approx(kg, abs=1e-12)


def test_physics_trace_exposes_per_tick_delta_maps():
    sim = _representative_sim()
    trace = PhysicsTrace.from_simulator(sim)

    assert len(trace.condensed_by_stage_species_delta) == len(trace.snapshots)
    assert (
        len(trace.wall_deposit_by_segment_species_delta)
        == len(trace.snapshots)
    )
    assert len(trace.impurity_delta) == len(trace.snapshots)
    assert all(
        isinstance(key, tuple) and len(key) == 2
        for per_tick in trace.condensed_by_stage_species_delta
        for key in per_tick
    )
    assert all(
        isinstance(key, tuple) and len(key) == 2
        for per_tick in trace.wall_deposit_by_segment_species_delta
        for key in per_tick
    )
    with pytest.raises(TypeError):
        trace.product_ledger_kg["SiO"] = 0.0
    with pytest.raises(TypeError):
        trace.condensed_by_stage_species_delta[0][(0, "SiO")] = 0.0


def test_from_simulator_provenance_gate_matches_legacy_verdict():
    """grok S2C-FOLDCHECK regression: from_simulator populated the provenance
    surface WITHOUT completeness_fraction (it lives in the sibling
    completeness_by_target_species dict), so the routed optimizer gate
    fail-closed (feasible=False, "no result") on healthy runs the legacy
    path scored feasible. The merged payload must make both paths agree.

    The completeness diagnostic only populates when the campaign config
    declares target_species, so this builds its own C2A sim with Na/K
    targets instead of using _representative_sim()."""
    from simulator.optimize.physics import PhysicsConstraintSet

    feedstocks = _load_data_yaml("feedstocks.yaml")
    setpoints = dict(_load_data_yaml("setpoints.yaml"))
    vapor_pressures = _load_data_yaml("vapor_pressures.yaml")
    kernel_config = dict(setpoints.get("chemistry_kernel", {}) or {})
    kernel_config["allow_fallback_vapor"] = True
    kernel_config["allow_unmeasured_alpha_fallback"] = True
    setpoints["chemistry_kernel"] = kernel_config
    campaigns = dict(setpoints.get("campaigns", {}) or {})
    c2a = dict(campaigns.get("C2A", {}) or {})
    c2a["target_species"] = ["Na", "K"]
    campaigns["C2A"] = c2a
    setpoints["campaigns"] = campaigns

    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(backend, setpoints, feedstocks, vapor_pressures)
    sim.load_batch("mars_basalt", mass_kg=1000.0, additives_kg={"C": 30.0})
    sim.start_campaign(CampaignPhase.C2A)
    for _ in range(8):
        sim.step()
    trace = PhysicsTrace.from_simulator(sim)

    provenance = dict(trace.extraction_completeness_by_target)
    assert provenance, "representative run must emit per-target provenance"
    # The bug signature: entries lacked the fraction key entirely.
    for target, entry in provenance.items():
        assert "completeness_fraction" in entry, target

    targets = tuple(sorted(provenance))
    constraints = PhysicsConstraintSet(target_species=targets)
    provenance_margin = constraints.extraction_completeness(trace)
    legacy_trace = replace(trace, extraction_completeness_by_target={})
    legacy_margin = constraints.extraction_completeness(legacy_trace)

    assert "fail-closed: no result" not in provenance_margin.detail
    assert provenance_margin.feasible == legacy_margin.feasible
