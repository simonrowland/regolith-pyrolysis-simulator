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
    if solver["status"] == "not_converged":
        assert any(
            notice.get("code") == "headspace_partial_solver_not_converged"
            and notice.get("residual_relative") == solver["residual_relative"]
            for notice in sim._last_evaporation_flux_diagnostic["notice_flags"]
        )
    for species, writer_partial_Pa in solver["writer_partials_Pa"].items():
        committed_partial_Pa = float(
            (snapshot.melt_headspace_composition_mbar or {}).get(species, 0.0)
        ) * 100.0
        assert committed_partial_Pa == pytest.approx(
            writer_partial_Pa,
            rel=1.0e-9,
            abs=1.0e-14,
        )
    for species in ("Fe", "Ca"):
        expected = float(
            (snapshot.melt_headspace_composition_mbar or {}).get(species, 0.0)
        ) * 100.0
        assert sim._evaporation_bulk_partial_pressure_pa(species) == pytest.approx(
            expected,
            abs=1.0e-12,
        )
