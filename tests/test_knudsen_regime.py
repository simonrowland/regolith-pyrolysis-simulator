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
from simulator.state import CampaignPhase
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

    assert document["status"] == "refused"
    assert document["reason"] == "knudsen_outside_viscous_flow"
    assert document["error_message"] == "knudsen_outside_viscous_flow"
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
