import math
from pathlib import Path

import pytest
import yaml

from simulator import condensation as condensation_module
from simulator.condensation import (
    CondensationModel,
    KnudsenRegime,
    KnudsenRegimeRefusal,
)
from simulator.core import PyrolysisSimulator
from simulator.state import CondensationTrain, EvaporationFlux, MeltState
from simulator.state import Atmosphere, CampaignPhase
from simulator.runner import PyrolysisRun


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def test_knudsen_number_matches_kinetic_theory_known_case():
    pressure_pa = 1000.0
    temperature_K = 1773.15
    pipe_diameter_m = 0.12
    collision_cross_section_m2 = (
        math.pi * condensation_module.N2_COLLISION_DIAMETER_M ** 2
    )

    expected_mean_free_path_m = (
        condensation_module.BOLTZMANN_CONSTANT_J_K
        * temperature_K
        / (math.sqrt(2.0) * collision_cross_section_m2 * pressure_pa)
    )
    expected_knudsen = expected_mean_free_path_m / pipe_diameter_m

    assert condensation_module._mean_free_path_m(
        pressure_pa, temperature_K
    ) == pytest.approx(expected_mean_free_path_m)
    assert condensation_module._knudsen_number(
        pressure_pa, temperature_K, pipe_diameter_m
    ) == pytest.approx(expected_knudsen)


def test_pc_extract_na_profile_knudsen_order_matches_hand_literal():
    pressure_pa = 1000.0
    temperature_K = 1873.15
    pipe_diameter_m = 0.12

    # BUG-013: N2 collision diameter grounded to BSL Table E.1 sigma
    # (3.7e-10 -> 3.798e-10 m); MFP/Kn scale by (3.7/3.798)**2 = 0.94906.
    expected_mean_free_path_m = 4.035348507948186e-5
    expected_knudsen = 3.3627904232901555e-4

    assert condensation_module._mean_free_path_m(
        pressure_pa, temperature_K
    ) == pytest.approx(expected_mean_free_path_m)
    assert condensation_module._knudsen_number(
        pressure_pa, temperature_K, pipe_diameter_m
    ) == pytest.approx(expected_knudsen)
    assert 2.0e-4 < expected_knudsen < 8.0e-4


def test_pressure_band_min_knudsen_order_matches_hand_literal():
    pressure_pa = 500.0
    temperature_K = 1873.15
    pipe_diameter_m = 0.12

    # BUG-013: N2 collision diameter grounded to BSL Table E.1 sigma
    # (3.7e-10 -> 3.798e-10 m); MFP/Kn scale by (3.7/3.798)**2 = 0.94906.
    expected_mean_free_path_m = 8.070697015896373e-5
    expected_knudsen = 6.725580846580311e-4

    assert condensation_module._mean_free_path_m(
        pressure_pa, temperature_K
    ) == pytest.approx(expected_mean_free_path_m)
    assert condensation_module._knudsen_number(
        pressure_pa, temperature_K, pipe_diameter_m
    ) == pytest.approx(expected_knudsen)
    assert 6.0e-4 < expected_knudsen < 8.0e-4


@pytest.mark.parametrize("campaign_name", ["C4", "C6"])
def test_continuum_pressure_bounds_are_derived_from_operating_point(campaign_name):
    setpoints = yaml.safe_load((DATA_DIR / "setpoints.yaml").read_text())
    campaign = setpoints["campaigns"][campaign_name]
    continuum = campaign["continuum_pressure_bounds"]
    lower_mbar, upper_mbar = continuum["pN2_mbar"]
    temperature_K = float(continuum["gas_temperature_C"]) + 273.15
    pipe_diameter_m = float(continuum["pipe_diameter_m"])
    knudsen_max = float(continuum["knudsen_max"])

    expected_lower_mbar = (
        condensation_module.BOLTZMANN_CONSTANT_J_K
        * temperature_K
        / (
            math.sqrt(2.0)
            * math.pi
            * condensation_module.N2_COLLISION_DIAMETER_M ** 2
            * knudsen_max
            * pipe_diameter_m
        )
        / 100.0
    )

    assert continuum["carrier_gas"] == "N2"
    assert "p_total_mbar" not in continuum
    assert continuum["derived_pN2_floor_mbar"] == pytest.approx(
        expected_lower_mbar,
        rel=2.0e-6,
    )
    assert [lower_mbar, upper_mbar] == pytest.approx([5.0, 15.0])
    assert lower_mbar >= expected_lower_mbar
    pO2_min_mbar, pO2_max_mbar = campaign["pO2_mbar"]
    derived_total_bounds = [
        pO2_min_mbar + lower_mbar,
        pO2_max_mbar + upper_mbar,
    ]
    assert derived_total_bounds[0] - pO2_min_mbar == pytest.approx(lower_mbar)
    assert derived_total_bounds[1] - pO2_max_mbar == pytest.approx(upper_mbar)
    assert condensation_module._knudsen_number(
        lower_mbar * 100.0,
        temperature_K,
        pipe_diameter_m,
    ) <= knudsen_max


@pytest.mark.parametrize(
    ("knudsen_number", "expected"),
    [
        (0.0, KnudsenRegime.VISCOUS),
        (0.009999, KnudsenRegime.VISCOUS),
        (0.01, KnudsenRegime.TRANSITIONAL),
        (9.999999, KnudsenRegime.TRANSITIONAL),
        (10.0, KnudsenRegime.FREE_MOLECULAR),
        (math.inf, KnudsenRegime.FREE_MOLECULAR),
    ],
)
def test_knudsen_regime_classification_boundaries(knudsen_number, expected):
    assert condensation_module.classify_knudsen_regime(knudsen_number) is expected


def test_true_vacuum_mean_free_path_is_infinite_and_configured_route_refuses():
    assert math.isinf(condensation_module._mean_free_path_m(0.0, 1773.15))
    assert math.isinf(
        condensation_module._knudsen_number(0.0, 1773.15, 0.12)
    )

    diagnostic = condensation_module.knudsen_regime_diagnostic(
        overhead_pressure_mbar=0.0,
        gas_temperature_C=1500.0,
        pipe_diameter_m=0.12,
    )
    assert diagnostic["status"] == "refused"
    assert diagnostic["regime"] == KnudsenRegime.FREE_MOLECULAR.value
    assert diagnostic["knudsen_number"] is None

    model = CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        overhead_pressure_mbar=0.0,
        pipe_diameter_m=0.12,
        gas_temperature_C=1500.0,
    )
    melt = MeltState()
    melt.temperature_C = 1500.0
    flux = EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0)

    with pytest.raises(KnudsenRegimeRefusal) as exc_info:
        model.route(flux, melt)
    assert exc_info.value.diagnostic["status"] == "refused"
    assert exc_info.value.diagnostic["regime"] == KnudsenRegime.FREE_MOLECULAR.value


def test_c2a_knudsen_pressure_floor_recovers_stranded_setpoint():
    model = CondensationModel(CondensationTrain.create_default())

    adjustment = model.adjust_c2a_pressure_setpoint(
        requested_p_total_mbar=1.0e-6,
        pO2_mbar=0.0,
        gas_temperature_C=1600.0,
        pipe_diameter_m=0.12,
        pN2_min_mbar=5.0,
        pN2_max_mbar=15.0,
        carrier_gas="N2",
    )

    assert adjustment["status"] == "applied"
    assert adjustment["requested_p_total_mbar"] == pytest.approx(1.0e-6)
    assert adjustment["minimum_pressure_mbar"] < 5.0
    assert adjustment["applied_pN2_mbar"] == pytest.approx(5.0)
    assert adjustment["applied_p_total_mbar"] == pytest.approx(5.0)
    assert adjustment["formula"] == (
        "k_B*T/(sqrt(2)*pi*d^2*L*Kn_ceiling)"
    )
    applied_kn = condensation_module._knudsen_number(
        adjustment["applied_p_total_mbar"] * 100.0,
        1600.0 + 273.15,
        adjustment["controlling_characteristic_length_m"],
    )
    assert applied_kn < condensation_module.FREE_MOLECULAR_KNUDSEN_MIN


def test_c2a_knudsen_pressure_floor_retains_typed_refusal_for_empty_band():
    model = CondensationModel(CondensationTrain.create_default())

    with pytest.raises(KnudsenRegimeRefusal) as exc_info:
        model.adjust_c2a_pressure_setpoint(
            requested_p_total_mbar=1.0e-6,
            pO2_mbar=0.0,
            gas_temperature_C=1600.0,
            pipe_diameter_m=1.0e-8,
            pN2_min_mbar=5.0,
            pN2_max_mbar=15.0,
            carrier_gas="N2",
        )

    adjustment = exc_info.value.diagnostic["pressure_adjustment"]
    assert adjustment["status"] == "refused"
    assert adjustment["reason"] == "c2a_knudsen_pressure_window_empty"
    assert adjustment["required_pN2_mbar"] > 15.0
    assert exc_info.value.reason == "knudsen_outside_viscous_flow"


def test_unknown_carrier_knudsen_diagnostic_fails_loud():
    with pytest.raises(ValueError, match="Unsupported condensation carrier_gas"):
        condensation_module.knudsen_regime_diagnostic(
            overhead_pressure_mbar=10.0,
            gas_temperature_C=1500.0,
            pipe_diameter_m=0.12,
            carrier_gas="pHe",
        )


def test_blank_carrier_knudsen_diagnostic_fails_loud():
    with pytest.raises(ValueError, match="carrier_gas must be non-empty"):
        condensation_module.knudsen_regime_diagnostic(
            overhead_pressure_mbar=10.0,
            gas_temperature_C=1500.0,
            pipe_diameter_m=0.12,
            carrier_gas="",
        )


def test_blank_explicit_carrier_gas_fails_loud():
    model = CondensationModel(CondensationTrain.create_default())

    with pytest.raises(ValueError, match="carrier_gas must be non-empty"):
        model.configure_operating_conditions(carrier_gas="")


@pytest.mark.parametrize(
    ("carrier_gas", "expected"),
    [
        (None, "N2"),
        ("N2", "N2"),
        ("pN2", "N2"),
        ("N2 sweep", "N2"),
        ("pAr", "Ar"),
        ("pO2", "O2"),
        ("pCO2", "CO2"),
        ("CO2_BACKPRESSURE", "CO2"),
        ("96% CO2", "CO2"),
    ],
)
def test_supported_carrier_gas_aliases_resolve_without_fallback(carrier_gas, expected):
    diagnostic = condensation_module.knudsen_regime_diagnostic(
        overhead_pressure_mbar=10.0,
        gas_temperature_C=1500.0,
        pipe_diameter_m=0.12,
        carrier_gas=carrier_gas,
    )

    assert diagnostic["status"] == "ok"
    assert diagnostic["warnings"] == []
    assert diagnostic["carrier_collision_diameter_m"] == pytest.approx(
        condensation_module._carrier_collision_diameter_m(expected)
    )


@pytest.mark.parametrize("carrier_gas", ["pHe", "badCO2"])
def test_invalid_campaign_carrier_gas_fails_before_defaulting_to_n2(carrier_gas):
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = MeltState(campaign=CampaignPhase.C2A)
    sim.setpoints = {
        "campaigns": {
            "C2A_continuous": {
                "carrier_gas": carrier_gas,
            },
        },
    }

    with pytest.raises(ValueError, match="Unsupported condensation carrier_gas"):
        sim._resolve_condensation_carrier_gas()


def test_controlled_o2_atmosphere_uses_o2_transport_properties():
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = MeltState(
        campaign=CampaignPhase.C2B,
        atmosphere=Atmosphere.CONTROLLED_O2,
    )
    sim.setpoints = {"campaigns": {"C2B": {}}}

    assert sim._resolve_condensation_carrier_gas() == "O2"


def test_explicit_campaign_pn2_carrier_precedes_controlled_o2_inference():
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = MeltState(
        campaign=CampaignPhase.C2A,
        atmosphere=Atmosphere.CONTROLLED_O2,
    )
    sim.setpoints = {
        "campaigns": {"C2A_continuous": {"carrier_gas": "pN2"}}
    }

    assert sim._resolve_condensation_carrier_gas() == "N2"


def test_default_setpoint_recipes_are_viscous():
    setpoints = yaml.safe_load((DATA_DIR / "setpoints.yaml").read_text())
    hot_wall_pipe = setpoints["furnace"]["hot_wall_pipe"]
    pipe_diameter_m = float(hot_wall_pipe["typical_cm"]) / 100.0
    failures = []

    for campaign_name, config in setpoints["campaigns"].items():
        if config.get("flow_regime") != "viscous":
            continue
        pressure_mbar = _midpoint(config["p_total_mbar"])
        diagnostic = condensation_module.knudsen_regime_diagnostic(
            overhead_pressure_mbar=pressure_mbar,
            gas_temperature_C=1500.0,
            pipe_diameter_m=pipe_diameter_m,
        )
        if diagnostic["regime"] != KnudsenRegime.VISCOUS.value:
            failures.append({
                "campaign": campaign_name,
                "pressure_mbar": pressure_mbar,
                "pipe_diameter_m": pipe_diameter_m,
                "diagnostic": diagnostic,
            })

    assert failures == []


def test_c2a_recipe_viscous_run_has_no_knudsen_warning():
    run = PyrolysisRun(
        feedstock_id="mars_basalt",
        campaign="C2A",
        hours=1,
        additives_kg={"C": 30.0},
        force_builtin_vapor_pressure=True,
        run_metadata_overrides={
            "started_at_utc": "2026-05-15T00:00:00Z",
            "kernel_commit_sha": "knudsen-regime-test",
        },
    )
    session = run._start_session()
    session.simulator.melt.temperature_C = 1700.0

    document = run._run_session(session)
    diagnostic = document["run_metadata"]["knudsen_regime_diagnostic"]

    assert document["status"] in {"ok", "partial"}
    assert diagnostic["status"] == "ok"
    assert diagnostic["warnings"] == []
    assert {
        segment["regime"] for segment in diagnostic["segments"]
    } == {KnudsenRegime.VISCOUS.value}


def test_pressure_coating_pareto_diagnostic_uses_actual_kn_gate_and_length():
    run = PyrolysisRun(
        feedstock_id="mars_basalt",
        campaign="C2A",
        hours=1,
        additives_kg={"C": 30.0},
        force_builtin_vapor_pressure=True,
        run_metadata_overrides={
            "started_at_utc": "2026-05-15T00:00:00Z",
            "kernel_commit_sha": "pressure-pareto-test",
        },
    )
    session = run._start_session()
    session.simulator.melt.temperature_C = 1700.0

    document = run._run_session(session)
    diagnostic = document["run_metadata"]["pressure_coating_pareto_diagnostic"]
    gate = diagnostic["gate"]

    assert diagnostic["schema_version"] == "pressure-coating-pareto-v1"
    assert gate["no_warning_knudsen_threshold"] == pytest.approx(
        condensation_module.VISCOUS_KNUDSEN_MAX
    )
    assert gate["hard_refusal_knudsen_threshold"] == pytest.approx(
        condensation_module.FREE_MOLECULAR_KNUDSEN_MIN
    )
    assert gate["controlling_characteristic_length_m"] == pytest.approx(0.12)
    assert gate["characteristic_length_source"] == (
        "knudsen_regime_diagnostic.segments[*].characteristic_length_m"
    )
    assert diagnostic["current"]["distance_from_no_warning_gate_pressure_factor"] > 1.0
    assert set(diagnostic["by_species"]).issuperset({"Na", "K", "SiO", "Fe"})


def test_c2a_recipe_free_molecular_transport_is_refused(monkeypatch):
    run = PyrolysisRun(
        feedstock_id="mars_basalt",
        campaign="C2A",
        hours=1,
        additives_kg={"C": 30.0},
        force_builtin_vapor_pressure=True,
        run_metadata_overrides={
            "started_at_utc": "2026-05-15T00:00:00Z",
            "kernel_commit_sha": "knudsen-regime-test",
        },
    )
    session = run._start_session()
    sim = session.simulator
    sim.melt.temperature_C = 1700.0
    original_estimate = sim.overhead_model.estimate_transport_state

    def low_pressure_transport(evap_flux, melt):
        state = dict(original_estimate(evap_flux, melt))
        state["pressure_mbar"] = 1.0e-6
        return state

    monkeypatch.setattr(
        sim.overhead_model, "estimate_transport_state", low_pressure_transport
    )

    document = run._run_session(session)
    diagnostic = document["run_metadata"]["knudsen_regime_diagnostic"]

    assert document["status"] == "failed"
    assert document["reason"] == "poisoned_hour"
    assert "knudsen_outside_viscous_flow" in document["error_message"]
    assert "PoisonedHourError" in document["error_message"]
    assert diagnostic["status"] == "refused"
    assert diagnostic["reason"] == "knudsen_outside_viscous_flow"
    assert any(
        segment["regime"] == KnudsenRegime.FREE_MOLECULAR.value
        for segment in diagnostic["segments"]
    )


def test_direct_condensation_model_without_pressure_reports_unconfigured():
    train = CondensationTrain.create_default()
    model = CondensationModel(train, wall_temperature_C=1800.0)
    melt = MeltState()
    melt.temperature_C = 1700.0
    flux = EvaporationFlux(species_kg_hr={"Fe": 1.0}, total_kg_hr=1.0)

    route = model.route(flux, melt)

    # Direct callers with no pressure policy get telemetry, not a refusal.
    assert route.knudsen_regime_diagnostic["status"] == "unconfigured"
    assert (
        route.knudsen_regime_diagnostic["reason"]
        == "knudsen_policy_unconfigured"
    )


def _midpoint(value):
    if isinstance(value, (list, tuple)):
        return sum(float(item) for item in value) / len(value)
    return float(value)
