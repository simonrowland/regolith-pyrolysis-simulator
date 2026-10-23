from copy import deepcopy
import json
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from simulator.core import (
    Atmosphere,
    CampaignPhase,
    PyrolysisSimulator,
)
from simulator.campaigns import c0_exit_run_notice
from simulator.accounting.ledger import snapshot_atom_ledger
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.reduced_real_determinism import PT0DeterminismStore
from simulator.runner import build_per_hour_summary


C0_SETPOINTS = {
    "temp_range_C": [20, 950],
    "dT_dt_C_per_hr": 200,
    "max_hold_hr": 25,
    "carrier_gas": "Ar",
    "p_total_mbar_default": 1.0,
    "pO2_mbar_default": 0.0,
    "alkali_retention_budget_fraction": 1.0e-3,
}


def _sim(feedstocks):
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    # Stepping crosses the strict VR-11 vapour boundary even though these tests
    # assert only Stage-0 atmosphere state. Use the canonical schema-v2 catalog
    # instead of the retired empty schema-v1 compatibility shape.
    vapor_pressures = yaml.safe_load(
        (Path(__file__).parent.parent / "data" / "vapor_pressures.yaml").read_text()
    )
    return PyrolysisSimulator(
        backend,
        {"campaigns": {"C0": deepcopy(C0_SETPOINTS)}},
        feedstocks,
        vapor_pressures,
    )


def test_stage0_lunar_feedstock_uses_configured_argon_cover():
    sim = _sim(
        {"lunar": {"label": "Lunar", "composition_wt_pct": {"SiO2": 100.0}}}
    )

    sim.load_batch("lunar")
    sim.start_campaign(CampaignPhase.C0)

    assert sim.melt.atmosphere is Atmosphere.ARGON_FLOW
    assert sim.melt.background_gas_species == "Ar"
    assert sim.melt.p_total_mbar == 1.0
    assert sim.melt.pO2_mbar == 0.0


def test_c0_start_predicts_exit_from_live_flux_and_caps_the_ramp():
    feedstocks = yaml.safe_load(
        (Path(__file__).parent.parent / "data" / "feedstocks.yaml").read_text()
    )
    sim = _sim(feedstocks)
    sim.load_batch("lunar_mare_low_ti", 1000.0)
    sim.campaign_mgr.last_c0_exit = {"backstop": "maximum_hold"}
    sim.start_campaign(CampaignPhase.C0)

    prediction = sim.campaign_mgr.c0_exit_prediction
    assert sim.campaign_mgr.last_c0_exit is None
    assert sim._equipment is not None
    assert sim.melt.melt_surface_area_m2 == (
        sim._equipment.crucible.melt_surface_area_m2
    )
    assert sim._overhead_condenser_geometry_config["initial_throat_area_m2"] == (
        sim._equipment.pipe.initial_throat_area_m2
    )
    assert prediction["prediction_status"] == "ok"
    assert prediction["T_exit_C"] == 950.0
    assert prediction["t_evac_hr"] == 0.0
    assert prediction["alkali_retention_budget_fraction"] == 1.0e-3
    assert prediction["Na_plus_K_feed_loss_fraction"] <= 1.0e-3
    predicted_temperature_C = sim.melt.temperature_C
    sim.melt.temperature_C = prediction["C_evac_temperature_C"]
    sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
    inlet = sim._subsolidus_transport_inlet_flux(
        sim._calculate_evaporation(sim._get_equilibrium())
    )
    expected_capacity_kg_hr = sim._c0_pipe_capacity_kg_hr(inlet)
    sim.melt.temperature_C = predicted_temperature_C
    sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
    assert prediction["C_evac_kg_hr"] == expected_capacity_kg_hr
    target_C, _ramp = sim.campaign_mgr.get_temp_target(
        CampaignPhase.C0, 0, sim.melt
    )
    assert target_C == prediction["T_exit_C"]


def test_c0_forecast_reports_internal_analytical_when_live_backend_is_none():
    feedstocks = yaml.safe_load(
        (Path(__file__).parent.parent / "data" / "feedstocks.yaml").read_text()
    )
    sim = _sim(feedstocks)
    sim.load_batch("lunar_mare_low_ti", 1000.0)
    sim.backend = None

    sim.start_campaign(CampaignPhase.C0)

    prediction = sim.campaign_mgr.c0_exit_prediction
    assert prediction["prediction_status"] == "ok"
    context, status = sim._build_c0_forecast_context()
    assert status == ""
    assert context._c0_forecast_engine == "internal-analytical"


def _canonical_bytes(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


_PYSULFSAT_MUTABLE_EXPORTS = (
    "oxide_mass_liq_anhyd",
    "cation_num_liq_anhyd",
    "oxide_mass_liq_anhyd_df",
    "cation_num_liq_anhyd_df",
)


class _ForecastReplayStore:
    replay_enabled = True

    def __init__(self):
        self.replay_calls = 0
        self.entries = {"equilibrium": []}

    def clone_for_replay(self):
        clone = _ForecastReplayStore()
        clone.replay_calls = self.replay_calls
        clone.entries = deepcopy(self.entries)
        return clone

    def replay_equilibrium(self, forecast_sim):
        self.replay_calls += 1
        return forecast_sim._internal_analytical_equilibrium()


def _attach_mutable_sulfsat_module(sim):
    module = ModuleType("forecast_test_sulfsat")
    module.oxide_mass_liq_anhyd = {"SiO2_Liq": [60.0]}
    module.cation_num_liq_anhyd = {"Si": [1.0]}
    module.oxide_mass_liq_anhyd_df = [[60.0]]
    module.cation_num_liq_anhyd_df = [[1.0]]
    sim._sulfsat_gate._module = module
    sim._sulfsat_gate._available = True
    return module


def _forecast_owned_state_digest(sim, per_hour_summaries):
    ledger = snapshot_atom_ledger(sim.atom_ledger)
    store = getattr(sim, "_pt0_determinism_store", None)
    sulfur_module = getattr(sim._sulfsat_gate, "_module", None)
    sulfur_exports = {
        name: deepcopy(getattr(sulfur_module, name))
        for name in _PYSULFSAT_MUTABLE_EXPORTS
        if sulfur_module is not None and hasattr(sulfur_module, name)
    }
    return _canonical_bytes(
        {
            "atom_ledger_snapshot": ledger.close_report(),
            "notices": {
                "c0_exit": c0_exit_run_notice(sim),
                "sulfur": sim.sulfur_saturation_run_notice(),
                "commissioning": sim.engine_commissioning_run_notice(),
                "high_t_melt_activity": sim.high_t_melt_activity_run_notice(),
            },
            "backend_vars": (
                vars(sim.backend) if sim.backend is not None else None
            ),
            "phase_context_diagnostics": deepcopy(
                getattr(sim, "_last_phase_context_diagnostic", None)
            ),
            "per_hour_summaries": deepcopy(per_hour_summaries),
            "pt0_replay_state": deepcopy(vars(store)) if store is not None else None,
            "pysulfsat_mutable_exports": sulfur_exports,
        }
    )


def _short_c0_trajectory(feedstocks, prediction=None):
    sim = _sim(feedstocks)
    sim.load_batch("lunar_mare_low_ti", 1000.0)
    _attach_mutable_sulfsat_module(sim)
    sim.configure_pt0_determinism_store(_ForecastReplayStore())
    if prediction is not None:
        sim._predict_c0_exit = lambda **_kwargs: deepcopy(prediction)
    sim.start_campaign(CampaignPhase.C0)
    startup_phase_context = deepcopy(
        getattr(sim, "_last_phase_context_diagnostic", None)
    )
    per_hour_payloads = []
    for _ in range(3):
        snapshot = sim.step()
        per_hour_payloads.append(build_per_hour_summary(sim, snapshot))
    return sim, startup_phase_context, per_hour_payloads


def test_c0_forecast_and_supplied_decision_have_identical_realized_trajectory():
    feedstocks = yaml.safe_load(
        (Path(__file__).parent.parent / "data" / "feedstocks.yaml").read_text()
    )
    forecasted, forecast_phase_context, forecast_payloads = _short_c0_trajectory(
        feedstocks
    )
    assert forecasted.campaign_mgr.c0_exit_prediction["prediction_status"] == "ok"
    forecast_context, forecast_status = forecasted._build_c0_forecast_context()
    assert forecast_status == ""
    assert forecast_context._c0_forecast_engine == "internal-analytical"
    decision = deepcopy(forecasted.campaign_mgr.c0_exit_prediction)
    supplied, supplied_phase_context, supplied_payloads = _short_c0_trajectory(
        feedstocks,
        prediction=decision,
    )

    assert _canonical_bytes(forecast_phase_context) == _canonical_bytes(
        supplied_phase_context
    )
    assert _canonical_bytes(forecasted.atom_ledger.close_report()) == (
        _canonical_bytes(supplied.atom_ledger.close_report())
    )
    assert _canonical_bytes(
        {
            "c0_exit": c0_exit_run_notice(forecasted),
            "sulfur": forecasted.sulfur_saturation_run_notice(),
            "commissioning": forecasted.engine_commissioning_run_notice(),
        }
    ) == _canonical_bytes(
        {
            "c0_exit": c0_exit_run_notice(supplied),
            "sulfur": supplied.sulfur_saturation_run_notice(),
            "commissioning": supplied.engine_commissioning_run_notice(),
        }
    )
    assert _canonical_bytes(vars(forecasted.backend)) == _canonical_bytes(
        vars(supplied.backend)
    )
    assert _canonical_bytes(forecast_payloads) == _canonical_bytes(
        supplied_payloads
    )
    assert _forecast_owned_state_digest(
        forecasted, forecast_payloads
    ) == _forecast_owned_state_digest(supplied, supplied_payloads)


def test_c0_forecast_context_does_not_retain_loaded_sulfsat_module():
    sim = _sim(
        {"lunar": {"label": "Lunar", "composition_wt_pct": {"SiO2": 100.0}}}
    )
    sim.load_batch("lunar")
    sulfur_module = ModuleType("forecast_test_sulfsat")
    sim._sulfsat_gate._module = sulfur_module
    sim._sulfsat_gate._available = True

    context, status = sim._build_c0_forecast_context()

    assert status == ""
    assert context is not None
    assert context._sulfsat_gate is None
    assert sulfur_module.oxide_mass_liq_anhyd == {"SiO2_Liq": [60.0]}


def test_c0_forecast_refuses_post_equilibrium_sulfsat_path():
    sim = _sim(
        {"lunar": {"label": "Lunar", "composition_wt_pct": {"SiO2": 100.0}}}
    )
    sim.load_batch("lunar")
    sim.start_campaign(CampaignPhase.C0)
    context, status = sim._build_c0_forecast_context()

    assert status == ""
    assert context is not None
    with pytest.raises(RuntimeError) as refusal:
        context._get_equilibrium(run_post_equilibrium_sulfsat=True)
    assert type(refusal.value).__name__ == "_C0ForecastSulfsatRefusal"


def test_c0_forecast_digest_is_unchanged_by_forecast():
    feedstocks = yaml.safe_load(
        (Path(__file__).parent.parent / "data" / "feedstocks.yaml").read_text()
    )
    sim, _phase_context, per_hour_summaries = _short_c0_trajectory(feedstocks)
    before = _forecast_owned_state_digest(sim, per_hour_summaries)

    prediction = sim._predict_c0_exit()

    assert prediction["prediction_status"] == "ok"
    assert _forecast_owned_state_digest(sim, per_hour_summaries) == before


def test_c0_forecast_digest_preserves_production_pt0_replay_state():
    feedstocks = yaml.safe_load(
        (Path(__file__).parent.parent / "data" / "feedstocks.yaml").read_text()
    )
    sim, _phase_context, per_hour_summaries = _short_c0_trajectory(feedstocks)
    sim.configure_pt0_determinism_store(PT0DeterminismStore("replay"))
    before = _forecast_owned_state_digest(sim, per_hour_summaries)

    prediction = sim._predict_c0_exit()

    assert prediction["prediction_status"].startswith("unavailable")
    assert _forecast_owned_state_digest(sim, per_hour_summaries) == before


def test_c0_cached_replay_forecast_preserves_live_sulfur_and_authority_state():
    feedstocks = yaml.safe_load(
        (Path(__file__).parent.parent / "data" / "feedstocks.yaml").read_text()
    )
    sim = _sim(feedstocks)
    sim.load_batch("lunar_mare_low_ti", 1000.0)
    sulfur_result = object()
    sim._last_sulfur_saturation_result = sulfur_result
    sim._backend_authoritative = True

    class ReplayStore:
        replay_enabled = True

        def clone_for_replay(self):
            return ReplayStore()

        def replay_equilibrium(self, forecast_sim):
            forecast_sim._last_sulfur_saturation_result = "forecast replay"
            forecast_sim._backend_authoritative = False
            return forecast_sim._internal_analytical_equilibrium()

    sim.configure_pt0_determinism_store(ReplayStore())
    sim._predict_c0_exit()

    assert sim._last_sulfur_saturation_result is sulfur_result
    assert sim._backend_authoritative is True


def test_c0_comet_missing_alkali_inventory_does_not_predict_zero_loss():
    feedstocks = yaml.safe_load(
        (Path(__file__).parent.parent / "data" / "feedstocks.yaml").read_text()
    )
    sim = _sim(feedstocks)
    sim.load_batch("comet_nucleus", 1000.0)
    sim.start_campaign(CampaignPhase.C0)

    prediction = sim.campaign_mgr.c0_exit_prediction
    assert prediction["prediction_status"] == (
        "unavailable_no_initial_NaK_inventory"
    )
    assert prediction["T_exit_C"] is None
    assert prediction["M_C0_gas_kg"] > 0.0


def test_c0_prediction_flags_unmet_evacuation_and_remaining_load_gas():
    feedstocks = yaml.safe_load(
        (Path(__file__).parent.parent / "data" / "feedstocks.yaml").read_text()
    )
    sim = _sim(feedstocks)
    sim.load_batch("ci_carbonaceous_chondrite", 1000.0)
    sim.start_campaign(CampaignPhase.C0)

    prediction = sim.campaign_mgr.c0_exit_prediction

    assert prediction["evacuation_bound_unmet"] is True
    assert prediction["evacuation_max_hold_hr"] == 25.0
    assert prediction["evacuation_bound_unmet_hr"] == pytest.approx(
        31.135503658452762
    )
    assert prediction["evacuation_unreleased_gas_mass_kg"] == pytest.approx(
        98.10306159203087
    )
    assert prediction["evacuation_unreleased_gas_inventory_kg"] == pytest.approx(
        {
            "CH4": 1.5071132259442865,
            "CO": 4.633943479528104,
            "CO2": 6.298982890899267,
            "H2": 1.5666077480278489,
            "H2O": 82.98988248121516,
            "N2": 1.106531766416202,
        }
    )


def test_stage0_mars_feedstock_uses_configured_argon_cover():
    sim = _sim(
        {
            "mars": {
                "label": "Mars",
                "composition_wt_pct": {"SiO2": 100.0},
                "surface_pressure_mbar": 6,
                "atmosphere": "96% CO2",
            }
        }
    )

    sim.load_batch("mars")
    sim.start_campaign(CampaignPhase.C0)
    snapshot = sim.step()

    assert sim.melt.atmosphere is Atmosphere.ARGON_FLOW
    assert sim.melt.background_gas_species == "Ar"
    assert sim.melt.p_total_mbar == 1.0
    assert sim.melt.pO2_mbar == 0.0
    assert snapshot.overhead.pressure_mbar >= 1.0
    assert snapshot.overhead.composition["Ar"] == 1.0


def test_all_builtin_mars_feedstocks_define_co2_environment():
    data_path = Path(__file__).parent.parent / "data" / "feedstocks.yaml"
    feedstocks = yaml.safe_load(data_path.read_text())

    for key, feedstock in feedstocks.items():
        if not key.startswith("mars_"):
            continue
        sim = _sim({key: feedstock})
        required_c = 0.0
        if PyrolysisSimulator._uses_mars_carbon_cleanup(feedstock):
            required_c = PyrolysisSimulator._carbon_reductant_required_kg(
                feedstock, 1000.0)
        additives = {"C": required_c} if required_c > 0.0 else None
        sim.load_batch(key, additives_kg=additives)

        assert sim.melt.ambient_pressure_mbar > 0.0
        assert sim.melt.ambient_atmosphere == "96% CO2"
