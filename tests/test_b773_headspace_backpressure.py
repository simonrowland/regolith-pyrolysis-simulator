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


def test_headspace_inventory_projection_numeric_baseline(monkeypatch):
    from simulator import overhead

    sim = _lunar_simulator()
    committed_probes = []
    original_solver = overhead.solve_quasi_steady_headspace_partials_Pa

    def capture_committed_probe(**kwargs):
        original_commit = kwargs["commit_flux"]

        def capture_commit(raw_flux):
            committed = original_commit(raw_flux)
            if not committed_probes:
                committed_probes.append(dict(committed.species_kg_hr))
            return committed

        kwargs["commit_flux"] = capture_commit
        return original_solver(**kwargs)

    monkeypatch.setattr(
        overhead,
        "solve_quasi_steady_headspace_partials_Pa",
        capture_committed_probe,
    )
    sim.step()

    assert committed_probes
    # Golden pin from the first committed probe of lunar C2A at 1050 °C;
    # the value is the current frozen-inventory projection in kg/hr.
    assert committed_probes[0]["Na"] == pytest.approx(
        7.955657572410583e-08,
        rel=1.0e-12,
        abs=1.0e-20,
    )


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
    batch_builds = 0
    original_build_batch = sim.build_vapour_batch

    def count_batch_builds(*args, **kwargs):
        nonlocal batch_builds
        batch_builds += 1
        return original_build_batch(*args, **kwargs)

    monkeypatch.setattr(sim, "build_vapour_batch", count_batch_builds)
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

    assert batch_builds == 1
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


def test_headspace_probe_diagnostic_and_engagement_follow_selected_flux(
    monkeypatch,
):
    from simulator import overhead

    sim = _lunar_simulator()
    base_partials = {}
    original_bulk_partial = sim._evaporation_bulk_partial_pressure_pa

    def capture_base_partial(species):
        value = original_bulk_partial(species)
        base_partials[str(species)] = float(value)
        return value

    monkeypatch.setattr(
        sim,
        "_evaporation_bulk_partial_pressure_pa",
        capture_base_partial,
    )
    original_resolve = sim._resolve_evaporation_batch_flux_state

    def mark_extrapolated_species(*args, **kwargs):
        state = list(original_resolve(*args, **kwargs))
        report = dict(state[1])
        report["extrapolated_flux_species"] = ("Na",)
        state[1] = report
        return tuple(state)

    monkeypatch.setattr(
        sim,
        "_resolve_evaporation_batch_flux_state",
        mark_extrapolated_species,
    )
    from simulator.overhead import QuasiSteadyHeadspaceSolve

    def select_first_probe(**kwargs):
        pressure = kwargs.get("fixed_pressure_Pa")
        if pressure is None:
            pressure = max(
                sum(base_partials.values()),
                float(sim.melt.p_total_mbar) * 100.0,
                1.0,
            )
        first_partials = {
            species: 0.5 * float(value)
            for species, value in kwargs["equilibrium_Pa"].items()
        }
        later_partials = {
            species: 0.25 * float(value)
            for species, value in kwargs["equilibrium_Pa"].items()
        }
        first_flux = kwargs["evaluate_flux"](first_partials, pressure)
        kwargs["evaluate_flux"](later_partials, pressure)
        return QuasiSteadyHeadspaceSolve(
            partials_Pa=first_partials,
            flux=first_flux,
            residual_kg_s_by_species={},
            residual_relative_by_species={},
            residual_relative=0.0,
            iterations=2,
            converged=True,
            total_pressure_Pa=float(pressure),
            pressure_residual_Pa=0.0,
        )

    monkeypatch.setattr(
        overhead,
        "solve_quasi_steady_headspace_partials_Pa",
        select_first_probe,
    )
    original_calculate = sim._calculate_evaporation
    probes = []

    def mark_probe_diagnostic(equilibrium, **kwargs):
        flux = original_calculate(equilibrium, **kwargs)
        if kwargs.get("overhead_partials_override_Pa") is None:
            return flux
        partials = dict(kwargs.get("overhead_partials_override_Pa") or {})
        diagnostic = dict(sim._last_evaporation_flux_diagnostic or {})
        diagnostic["probe_partials_Pa"] = partials
        sim._last_evaporation_flux_diagnostic = diagnostic
        probes.append((partials, dict(flux.species_kg_hr)))
        return flux

    monkeypatch.setattr(sim, "_calculate_evaporation", mark_probe_diagnostic)
    sim.step()

    assert len(probes) > 1
    selected_partials = sim._last_evaporation_flux_diagnostic[
        "probe_partials_Pa"
    ]
    assert "Na" in selected_partials, (
        sorted(selected_partials),
        sorted(sim._solved_headspace_partials_Pa_this_tick),
        len(probes),
        [partials.get("Na") for partials, _flux in probes],
    )
    assert selected_partials["Na"] == pytest.approx(
        sim._solved_headspace_partials_Pa_this_tick["Na"],
        rel=1.0e-12,
        abs=1.0e-14,
    )
    engagement = sim._degraded_path_engagement_summary()[
        "vapour_pressure_extrapolation"
    ]
    assert engagement["total_count"] == 1
    assert engagement["by_hour"][-1]["count"] == 1


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


def test_binding_c0_pipe_cap_publishes_partials_for_final_committed_flux(
):
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
    # Hand balance for the final Na release: P_eq=2.694467309265198 Pa,
    # J=0.00023648331278732654 kg/hr = 6.568980910758e-8 kg/s, and
    # beta=0.00021023306288411915 kg/(s Pa). Thus p=P_eq-J/beta
    # =2.694154847427814 Pa =0.02694154847427814 mbar.
    expected_mbar = {"Na": 0.02694154847427814}
    published_mbar = {
        "Na": snapshot.melt_headspace_composition_mbar["Na"]
    }
    assert published_mbar == pytest.approx(
        expected_mbar,
        rel=1.0e-10,
        abs=1.0e-14,
    )


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
