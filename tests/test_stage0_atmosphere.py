from copy import deepcopy
import json
from pathlib import Path
import threading
from types import ModuleType, SimpleNamespace

import pytest
import yaml

from simulator.core import (
    Atmosphere,
    CampaignPhase,
    PyrolysisSimulator,
)
from simulator.campaigns import c0_exit_record, c0_exit_run_notice
from simulator.accounting.ledger import snapshot_atom_ledger
from simulator.chemistry.kernel import ChemistryIntent
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


C0_ENDPOINT_SETPOINTS = {
    "temp_range_C": [20, 950],
    "dT_dt_C_per_hr": 50,
    "max_hold_hr": 25,
    "soft_endpoint": {
        "min_hold_hr": 10,
        "temperature_min_C": 940,
    },
}


def _sim(feedstocks, *, finite_headspace=False, c0_setpoints=None):
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
        {
            "campaigns": {
                "C0": deepcopy(c0_setpoints or C0_SETPOINTS)
            },
            "overhead_headspace": {
                "enabled": finite_headspace,
                "volume_m3": 1.0,
            },
        },
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


def test_stage0_lunar_feedstock_uses_hard_vacuum():
    sim = _sim(
        {"lunar": {"label": "Lunar", "composition_wt_pct": {"SiO2": 100.0}}},
        c0_setpoints=C0_ENDPOINT_SETPOINTS,
    )

    sim.load_batch("lunar")
    sim.start_campaign(CampaignPhase.C0)

    assert sim.melt.atmosphere is Atmosphere.HARD_VACUUM
    assert sim.melt.p_total_mbar == 0.0
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
    assert prediction["forecast_engine"] == "internal-analytical"
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
    sim.configure_pt0_determinism_store(PT0DeterminismStore("capture"))
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
    sulfur_module = _attach_mutable_sulfsat_module(sim)
    mutable_exports_before = {
        name: deepcopy(getattr(sulfur_module, name))
        for name in _PYSULFSAT_MUTABLE_EXPORTS
    }

    context, status = sim._build_c0_forecast_context()

    assert status == ""
    assert context is not None
    assert context._sulfsat_gate is None
    assert all(value is not sulfur_module for value in context.__dict__.values())
    assert {
        name: getattr(sulfur_module, name)
        for name in _PYSULFSAT_MUTABLE_EXPORTS
    } == mutable_exports_before


def test_c0_forecast_rebuilds_registry_around_live_engine_pool_locks():
    feedstocks = yaml.safe_load(
        (Path(__file__).parent.parent / "data" / "feedstocks.yaml").read_text()
    )
    sim = _sim(feedstocks)
    sim.load_batch("lunar_mare_low_ti", 1000.0)
    sim._register_freeze_gate_liquid_fraction_providers()

    fallback = sim._chem_registry.fallback_for(
        ChemistryIntent.GATE_LIQUID_FRACTION
    )
    vaporock_shadow = sim._chem_registry.shadows_for(
        ChemistryIntent.VAPOR_PRESSURE
    )[0]
    subprocess_pool = SimpleNamespace(
        _active_requests_lock=threading.Lock(),
        _lifecycle_lock=threading.Lock(),
        _queue=SimpleNamespace(mutex=threading.Lock()),
    )
    warm_pool = SimpleNamespace(
        _active_requests_lock=threading.Lock(),
        _lifecycle_lock=threading.Lock(),
    )
    fallback._backend = SimpleNamespace(_subprocess_pool=subprocess_pool)
    fallback._backend_initialised = True
    vaporock_shadow._backend = SimpleNamespace(_warm_pool=warm_pool)
    vaporock_shadow._backend_initialised = True

    context, status = sim._build_c0_forecast_context()

    assert status == ""
    assert context is not None
    assert context._chem_registry is not sim._chem_registry
    assert context._chem_registry.fallback_for(
        ChemistryIntent.GATE_LIQUID_FRACTION
    ) is not fallback
    assert context._chem_registry.shadows_for(
        ChemistryIntent.VAPOR_PRESSURE
    ) == ()
    assert sim._chem_registry.shadows_for(
        ChemistryIntent.VAPOR_PRESSURE
    )[0] is vaporock_shadow
    assert fallback._backend._subprocess_pool is subprocess_pool
    assert vaporock_shadow._backend._warm_pool is warm_pool


def test_c0_forecast_does_not_touch_vaporock_session_backend_cache(monkeypatch):
    from simulator.melt_backend import vaporock as vaporock_backend

    class SentinelBackend:
        def __init__(self):
            self._warm_pool = SimpleNamespace(
                _active_requests_lock=threading.Lock(),
                _lifecycle_lock=threading.Lock(),
            )
            self.availability_checks = 0
            self.pool_calls = 0

        def is_available(self):
            self.availability_checks += 1
            return True

        def equilibrate(self, **_kwargs):
            with self._warm_pool._active_requests_lock:
                self.pool_calls += 1
            raise RuntimeError("sentinel live VapoRock pool was reached")

    monkeypatch.setenv(vaporock_backend.SESSION_WARM_ENV, "1")
    cache_key = vaporock_backend._session_backend_cache_key(
        {"warm_worker": True}
    )
    sentinel = SentinelBackend()
    monkeypatch.setitem(
        vaporock_backend._SESSION_BACKEND_CACHE,
        cache_key,
        sentinel,
    )

    sim = _sim(
        {"lunar": {"label": "Lunar", "composition_wt_pct": {"SiO2": 100.0}}}
    )
    sim.load_batch("lunar")
    context, status = sim._build_c0_forecast_context()

    assert status == ""
    assert context is not None
    context.melt.temperature_C = 900.0
    context._get_equilibrium(run_post_equilibrium_sulfsat=False)

    assert sentinel.availability_checks == 0
    assert sentinel.pool_calls == 0
    assert not sentinel._warm_pool._active_requests_lock.locked()
    assert context._chem_registry.shadows_for(ChemistryIntent.VAPOR_PRESSURE) == ()


def test_c0_forecast_context_refusal_reports_snapshot_exception_detail():
    sim = _sim(
        {"lunar": {"label": "Lunar", "composition_wt_pct": {"SiO2": 100.0}}}
    )
    sim.load_batch("lunar")
    sim._forecast_uncopyable_state = threading.Lock()

    prediction = sim._predict_c0_exit()

    assert prediction["prediction_status"] == "unavailable_forecast_context"
    assert prediction["forecast_context_refusal_detail"]["exception_class"] == (
        "TypeError"
    )
    assert "cannot pickle" in prediction["forecast_context_refusal_detail"][
        "exception_message"
    ]


def test_c0_forecast_backend_factory_refusal_preserves_exception_detail():
    sim = _sim(
        {"lunar": {"label": "Lunar", "composition_wt_pct": {"SiO2": 100.0}}}
    )
    sim.load_batch("lunar")
    sim.backend = SimpleNamespace(backend_name="alphamelts")

    def fail_backend_factory():
        raise RuntimeError("forecast backend factory failed")

    sim._c0_forecast_backend_factory = fail_backend_factory
    prediction = sim._predict_c0_exit()

    assert prediction["prediction_status"] == "unavailable_forecast_backend"
    assert prediction["forecast_context_refusal_detail"] == {
        "exception_class": "RuntimeError",
        "exception_message": "forecast backend factory failed",
    }
    notice = c0_exit_record(
        "test",
        backstop="maximum_hold",
        ramp_C_per_hr=200.0,
        prediction=prediction,
    )
    assert "forecast_context_refusal_detail" not in notice
    assert "RuntimeError: forecast backend factory failed" in notice["message"]


def test_c0_forecast_cache_clone_refusal_preserves_exception_detail():
    class FailingForecastStore(PT0DeterminismStore):
        def clone_for_forecast(self):
            raise LookupError("forecast PT0 cache clone failed")

    sim = _sim(
        {"lunar": {"label": "Lunar", "composition_wt_pct": {"SiO2": 100.0}}}
    )
    sim.load_batch("lunar")
    sim.configure_pt0_determinism_store(FailingForecastStore("capture"))

    prediction = sim._predict_c0_exit()

    assert prediction["prediction_status"] == "unavailable_forecast_cache"
    assert prediction["forecast_context_refusal_detail"] == {
        "exception_class": "LookupError",
        "exception_message": "forecast PT0 cache clone failed",
    }
    notice = c0_exit_record(
        "test",
        backstop="maximum_hold",
        ramp_C_per_hr=200.0,
        prediction=prediction,
    )
    assert "forecast_context_refusal_detail" not in notice
    assert "LookupError: forecast PT0 cache clone failed" in notice["message"]


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

        def clone_for_forecast(self):
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


@pytest.mark.parametrize("finite_headspace", [False, True])
def test_stage0_mars_feedstock_uses_surface_co2_backpressure(finite_headspace):
    sim = _sim(
        {
            "mars": {
                "label": "Mars",
                "composition_wt_pct": {"SiO2": 100.0},
                "surface_pressure_mbar": 6,
                "atmosphere": "96% CO2",
            }
        },
        finite_headspace=finite_headspace,
        c0_setpoints=C0_ENDPOINT_SETPOINTS,
    )

    sim.load_batch("mars")
    sim.start_campaign(CampaignPhase.C0)
    snapshot = sim.step()

    assert sim.melt.atmosphere is Atmosphere.CO2_BACKPRESSURE
    assert sim.melt.p_total_mbar == 6.0
    assert sim.melt.pO2_mbar == 0.0
    assert snapshot.overhead.pressure_mbar == 6.0
    assert snapshot.overhead.composition["CO2"] == 5.76
    # Pin both overhead implementations before consolidating the cover-gas rule.
    assert snapshot.overhead.composition["CO2"].hex() == (5.76).hex()


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


@pytest.mark.parametrize('finite_headspace', [False, True])
def test_controlled_background_pressure_pin_before_owner_reuse(finite_headspace):
    from simulator.overhead import OverheadGasModel
    from simulator.state import CondensationTrain, EvaporationFlux, MeltState

    melt = MeltState()
    melt.atmosphere = Atmosphere.CONTROLLED_O2
    melt.p_total_mbar = 1000.0
    melt.pO2_mbar = 900.0
    melt.background_gas_species = 'CO2'
    melt.background_gas_mole_fraction = 1.0
    model = OverheadGasModel({'enabled': finite_headspace, 'volume_m3': 1.0})
    gas = model.update(EvaporationFlux(), melt, CondensationTrain.create_default())
    # Independent Dalton-law anchor: the commanded oxygen occupies 900 of
    # 1000 mbar. Pin before reusing the two existing background projections.
    assert gas.composition['CO2'].hex() == (100.0).hex()
    assert gas.composition['O2'].hex() == (900.0).hex()


@pytest.mark.parametrize('finite_headspace', [False, True])
def test_stage0_air_projects_oxygen_fraction_without_assuming_co2(finite_headspace):
    sim = _sim({'earth': {
        'composition_wt_pct': {'SiO2': 100.0},
        'body': 'earth',
        'environment': {'surface_pressure_mbar': 1000.0, 'atmosphere': 'air'},
    }}, finite_headspace=finite_headspace)
    sim.load_batch('earth')
    sim.start_campaign(CampaignPhase.C0)
    snapshot = sim.step()
    assert snapshot.overhead.composition['O2'] == 210.0
    assert snapshot.overhead.composition['N2'] == 790.0
    assert 'CO2' not in snapshot.overhead.composition
    assert sim._headspace_transport_pO2_bar_from_ledger(0.0) == pytest.approx(0.21)
    assert sim._commanded_pO2_bar() == pytest.approx(0.21)
