import pytest

from scripts.reproduce_b773_evap_oscillation import (
    _NONZERO_KG_HR,
    _cases,
    _run_case,
)


def _lunar_simulator():
    from simulator.runner import PyrolysisRun

    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=24,
        mass_kg=1000.0,
        engines={"vapor_pressure": "builtin-antoine"},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        force_builtin_vapor_pressure=True,
        run_metadata_overrides={
            "started_at_utc": "2026-10-10T00:00:00Z",
            "kernel_commit_sha": "b773-evap-osc",
        },
    )
    sim = run._start_session().simulator
    sim.melt.temperature_C = 1050.0
    return sim


def test_lunar_c2a_hot_entry_has_no_empty_hours_or_period_two_species():
    """Keep the old pin in history and assert the live hourly solve stays on."""
    case = _cases("reproduction")[0]
    result = _run_case(case)
    rows = result["hours"]

    assert all(row["flux_kg_hr"] for row in rows)
    for species in ("Na", "K", "Fe"):
        started = False
        stopped = False
        for row in rows:
            present = (
                float(row["flux_kg_hr"].get(species, 0.0)) > _NONZERO_KG_HR
            )
            if present:
                assert not stopped, f"{species} restarted after an off hour"
                started = True
            elif started:
                stopped = True


def test_committed_headspace_is_the_state_read_by_fe_and_ca_consumers(
    monkeypatch,
):
    sim = _lunar_simulator()
    sim._melt_headspace_composition_mbar = {"O2": 0.0005}
    monkeypatch.setattr(sim, "_freeze_gate_enabled", lambda: True)
    monkeypatch.setattr(
        sim,
        "_freeze_gate_liquid_fraction_factor",
        lambda **_kwargs: 0.5,
    )
    original_depletion = sim._analytic_evaporation_depletion_rates

    def inventory_limited_rates(raw_rates, **kwargs):
        rates = original_depletion(raw_rates, **kwargs)
        return {species: rate * 0.5 for species, rate in rates.items()}

    monkeypatch.setattr(
        sim,
        "_analytic_evaporation_depletion_rates",
        inventory_limited_rates,
    )
    snapshot = sim.step()

    assert snapshot.evap_flux.total_kg_hr > 0.0
    solver = sim._last_evaporation_flux_diagnostic["headspace_solver"]
    assert solver["status"] == "converged"
    assert solver["residual_relative"] <= 1.0e-9
    assert all(
        residual <= 1.0e-9
        for residual in solver["residual_relative_by_species"].values()
    )
    assert solver["residual_relative_by_species"]["O2"] == 0.0
    assert snapshot.melt_headspace_composition_mbar["O2"] == pytest.approx(
        0.0005,
        abs=1.0e-14,
    )
    assert solver["published_from_final_committed_inlet"]
    for species in ("Fe", "Ca"):
        expected = float(
            (snapshot.melt_headspace_composition_mbar or {}).get(species, 0.0)
        ) * 100.0
        assert sim._evaporation_bulk_partial_pressure_pa(species) == pytest.approx(
            expected,
            abs=1.0e-12,
        )


def test_runtime_ar_n2_cover_keeps_held_total_pressure_in_gas_film(monkeypatch):
    sim = _lunar_simulator()
    sim.melt.p_total_mbar = 100.0
    sim._melt_headspace_composition_mbar = {"Ar": 20.0, "N2": 30.0}
    observed = []
    original_dispatch = sim._dispatch_only

    def capture_evaporation_controls(intent, **kwargs):
        controls = kwargs.get("control_inputs") or {}
        if "overhead_partials_Pa" in controls:
            observed.append((dict(controls["overhead_partials_Pa"]), float(
                controls["overhead_pressure_pa"]
            )))
        return original_dispatch(intent, **kwargs)

    monkeypatch.setattr(sim, "_dispatch_only", capture_evaporation_controls)
    sim.step()

    assert observed
    held_total_Pa = 10000.0
    for partials_Pa, pressure_Pa in observed:
        expected_Pa = max(sum(partials_Pa.values()), held_total_Pa)
        assert pressure_Pa == pytest.approx(expected_Pa, rel=1.0e-12)


def test_solved_melt_partials_do_not_replace_downstream_overhead_report(
    monkeypatch,
):
    sim = _lunar_simulator()
    post_condensation_reports = []
    original_update = sim.overhead_model.update

    def capture_post_condensation_report(*args, **kwargs):
        overhead = original_update(*args, **kwargs)
        post_condensation_reports.append(dict(overhead.composition))
        return overhead

    monkeypatch.setattr(
        sim.overhead_model, "update", capture_post_condensation_report
    )
    snapshot = sim.step()

    assert post_condensation_reports
    expected = post_condensation_reports[-1]
    assert snapshot.melt_headspace_composition_mbar
    assert snapshot.overhead.composition == expected
    assert sim.overhead.composition == expected


def test_binding_c0_pipe_cap_publishes_partials_for_final_committed_flux():
    from simulator.state import CampaignPhase

    sim = _lunar_simulator()
    sim.campaign_mgr.campaigns["C0"]["p_total_mbar_default"] = 0.0
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    cap_changes = []
    original_cap = sim._cap_c0_joint_release_to_pipe_capacity

    def capture_binding_cap(flux):
        before = float(flux.total_kg_hr)
        result = original_cap(flux)
        cap_changes.append((before, float(flux.total_kg_hr)))
        return result

    sim._cap_c0_joint_release_to_pipe_capacity = capture_binding_cap
    snapshot = sim.step()

    assert any(after < before for before, after in cap_changes)
    assert "headspace_solver" in sim._last_evaporation_flux_diagnostic
    final_inlet = sim._subsolidus_transport_inlet_flux(snapshot.evap_flux)
    capacity = sim._effective_transport_capacity_this_tick
    transport = sim.overhead_model.estimate_transport_state(
        final_inlet,
        sim.melt,
        p_downstream_bar=sim._headspace_downstream_pressure_bar(capacity),
        effective_transport_capacity=capacity,
    )
    expected = sim.overhead_model.species_partial_pressures(
        final_inlet,
        transport["vapor_pressure_mbar"],
        sim.species_formula_registry,
    )

    assert snapshot.melt_headspace_composition_mbar == pytest.approx(expected)


def test_closed_line_runtime_publishes_equilibrium_partials_with_zero_flux(
    monkeypatch,
):
    sim = _lunar_simulator()
    monkeypatch.setattr(
        sim,
        "_compute_fe_redox_split_diagnostic",
        lambda: {"ferric_frac": 0.0},
    )
    monkeypatch.setattr(
        sim.overhead_model,
        "_pipe_conductance",
        lambda *_args, **_kwargs: 0.0,
    )
    monkeypatch.setattr(
        sim.overhead_model,
        "_pipe_molar_flow_mol_s",
        lambda *_args, **_kwargs: 0.0,
    )

    snapshot = sim.step()

    assert snapshot.evap_flux.total_kg_hr == 0.0
    solver = sim._last_evaporation_flux_diagnostic["headspace_solver"]
    assert solver["status"] != "not_converged"
    equilibrium_partials = solver["partials_Pa"]
    assert equilibrium_partials
    for species, partial_Pa in equilibrium_partials.items():
        assert snapshot.melt_headspace_composition_mbar[species] * 100.0 == pytest.approx(
            partial_Pa,
            rel=1.0e-9,
            abs=1.0e-14,
        )
