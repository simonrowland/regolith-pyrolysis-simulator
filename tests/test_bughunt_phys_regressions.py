from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from engines.builtin.metallothermic_step import _time_integrated_inventory_fraction
from engines.builtin.overhead_bleed import BuiltinOverheadBleedProvider
from simulator.accounting.queries import AccountingQueries
from simulator.accounting.ledger import KNOWN_LEDGER_ACCOUNTS
from simulator.chemistry.kernel.config import normalize_chemistry_kernel_config
from simulator.condensation import knudsen_regime_diagnostic
from simulator.core import (
    CampaignPhase,
    FLOW_MASS_ACCOUNTS,
    PyrolysisSimulator,
)
from simulator.chemistry.kernel import ProviderUnavailableError
from simulator.equilibrium import EquilibriumMixin
from simulator.evaporation import EvaporationFluxRefusal, EvaporationMixin
from simulator.interpolation_uncertainty import _nonlinearity_component
from simulator.optimize.evaluate import _trace_with_optimizer_coating_report
from simulator.optimize.physics import GateMargin, PhysicsConstraintSet, ThresholdSpec
from simulator.optimize.strategy.bayesian import _constraint_values
from simulator.overhead import OverheadConfigurationError, OverheadGasModel
from simulator.state import Atmosphere, EvaporationFlux, HourSnapshot
from simulator.thermal_train import (
    integrate_molar_sensible_enthalpy_j_per_mol,
    oxygen_cp_shomate_j_per_mol_k,
)


def test_authority_opt_ins_reject_truthy_strings() -> None:
    for key in ("allow_fallback_vapor", "allow_unmeasured_alpha_fallback"):
        with pytest.raises(TypeError, match=rf"{key} must be bool"):
            normalize_chemistry_kernel_config({key: "false"})


def test_evaporation_typed_refusal_rolls_back_entire_hour() -> None:
    class FakeLedger:
        def __init__(self) -> None:
            self._balances = {}
            self._policies = {}
            self._transitions = []
            self._terminal_debit_authorized_transition_ids = set()
            self._external_loads = []

        @property
        def transitions(self):
            return self._transitions

    sim = object.__new__(PyrolysisSimulator)
    sim._poisoned_hour = None
    sim._pending_shuttle_bakeout_cycle_increment = ""
    sim.melt = SimpleNamespace(hour=4)
    sim.overhead = SimpleNamespace(pressure_mbar=2.0)
    sim.record = SimpleNamespace(snapshots=[])
    sim.atom_ledger = FakeLedger()
    sim._chem_registry = object()
    sim._chem_kernel = object()
    sim._build_chemistry_kernel = lambda: object()

    def refuse_after_commit() -> None:
        sim.atom_ledger._transitions.append(object())
        sim.melt.hour = 5
        sim.overhead.pressure_mbar = 99.0
        sim.record.snapshots.append(object())
        raise EvaporationFluxRefusal("missing_alpha", {"missing_alpha": ["CrO2"]})

    sim._step_one_hour = refuse_after_commit
    with pytest.raises(ProviderUnavailableError) as refusal:
        sim.step()
    assert sim._poisoned_hour is None
    assert isinstance(refusal.value, EvaporationFluxRefusal)
    assert sim.atom_ledger.transitions == []
    assert sim.melt.hour == 4
    assert sim.overhead.pressure_mbar == pytest.approx(2.0)
    assert sim.record.snapshots == []


def test_oxygen_sensible_integral_uses_nist_high_temperature_shomate_band() -> None:
    total = integrate_molar_sensible_enthalpy_j_per_mol(
        "O2", 1900.0, 2023.15, segment_K=10.0, allow_low_temperature_o2=True
    )
    below = integrate_molar_sensible_enthalpy_j_per_mol(
        "O2", 1900.0, 2000.0, segment_K=10.0, allow_low_temperature_o2=True
    )
    assert oxygen_cp_shomate_j_per_mol_k(2023.15) == pytest.approx(
        37.802125563128,
        rel=1.0e-12,
    )
    assert total - below == pytest.approx(874.5559135898663, rel=2.0e-6)


@pytest.mark.parametrize("one_hour_fraction", [1.0 / 3.0, 0.01, 0.20, 0.25])
def test_metallothermic_inventory_fraction_is_cadence_invariant(
    one_hour_fraction: float,
) -> None:
    half = _time_integrated_inventory_fraction(one_hour_fraction, 0.5)
    full = _time_integrated_inventory_fraction(one_hour_fraction, 1.0)
    assert 1.0 - (1.0 - half) ** 2 == pytest.approx(full, rel=1.0e-14)


def test_pressure_curvature_is_invariant_to_pressure_units() -> None:
    query = {"controls": {"T_K": 1000.0}}

    def neighbors(scale: float) -> list[dict]:
        return [
            {
                "key": {"controls": {"T_K": temperature}},
                "payload": {
                    "equilibrium_result": {
                        "vapor_pressures_Pa": {
                            "SiO": scale * math.exp((temperature - 1000.0) ** 2 / 1.0e5)
                        }
                    }
                },
            }
            for temperature in (900.0, 1000.0, 1100.0)
        ]

    pascals = _nonlinearity_component(query, neighbors(1.0e5))
    bars = _nonlinearity_component(query, neighbors(1.0))
    assert pascals["value"] == pytest.approx(bars["value"], rel=1.0e-12)


def test_thermal_train_report_uses_upstream_state_with_upstream_flow(monkeypatch) -> None:
    snapshot = HourSnapshot(temperature_C=1700.0)
    snapshot.evap_flux = EvaporationFlux(species_kg_hr={"Fe": 2.0}, total_kg_hr=2.0)
    snapshot.melt_headspace_composition_mbar = {"Fe": 3.0, "O2": 2.0}
    snapshot.overhead.pressure_mbar = 0.01
    snapshot.overhead.composition = {"Fe": 0.001}
    captured: dict[str, object] = {}

    def capture(*_args, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr("simulator.thermal_train.report_from_recorded_series", capture)
    sim = SimpleNamespace(
        atom_ledger=None,
        record=SimpleNamespace(snapshots=[snapshot]),
        setpoints={},
    )
    AccountingQueries(sim).thermal_train_report()
    state = captured["overhead_state_series"][0]
    assert state["pressure_Pa"] == pytest.approx(500.0)
    assert state["composition_mbar"] == {"Fe": 3.0, "O2": 2.0}
    assert state["temperature_K"] == pytest.approx(1973.15)


def test_finite_headspace_requires_physical_volume_and_strict_bool() -> None:
    model = OverheadGasModel({"enabled": True, "volume_m3": None})
    with pytest.raises(OverheadConfigurationError, match="volume_m3"):
        model._resolve_headspace_volume(None)
    with pytest.raises(OverheadConfigurationError, match="volume_m3"):
        model._resolve_headspace_volume(0.0)
    with pytest.raises(OverheadConfigurationError, match="enabled must be bool"):
        OverheadGasModel({"enabled": "false", "volume_m3": 0.085})


def test_all_declared_terminal_accounts_have_flow_closure_disposition() -> None:
    expected = {
        "terminal.oxygen_bubbler_external_vented_to_vacuum",
        "terminal.stage0_residual_carbonate_carbon",
        "terminal.stage0_residual_refractory_carbon",
    }
    assert expected <= set(FLOW_MASS_ACCOUNTS)
    assert not {
        account
        for account in KNOWN_LEDGER_ACCOUNTS
        if account.startswith("terminal.") and account not in FLOW_MASS_ACCOUNTS
    }


def test_bleed_provider_does_not_apply_downstream_capacity_twice() -> None:
    base = {
        "bleed_conductance_kg_s": 1.0e-6,
        "p_total_bar": 1.0,
        "dt_hr": 1.0,
    }
    vacuum = BuiltinOverheadBleedProvider._bled_species_mol(
        {"Fe": 1.0}, total_mol=1.0, total_kg=0.055845,
        controls={**base, "p_downstream_bar": 0.0},
    )
    finite = BuiltinOverheadBleedProvider._bled_species_mol(
        {"Fe": 1.0}, total_mol=1.0, total_kg=0.055845,
        controls={**base, "p_downstream_bar": 0.8},
    )
    assert finite["Fe"] == pytest.approx(vacuum["Fe"] * (1.0 - 0.8 ** 2))


def test_core_headspace_volume_refuses_missing_or_invalid_live_volume() -> None:
    sim = object.__new__(PyrolysisSimulator)
    sim._equipment = None
    sim._overhead_headspace_config = {"enabled": True, "volume_m3": None}

    def size_equipment() -> None:
        sim._equipment = SimpleNamespace(headspace_volume_m3=0.123)

    sim._get_turbine_spec = size_equipment
    assert sim._headspace_volume_m3() == pytest.approx(0.123)

    sim._equipment = None
    sim._get_turbine_spec = lambda: None
    with pytest.raises(OverheadConfigurationError):
        sim._headspace_volume_m3()

    sim._overhead_headspace_config["volume_m3"] = 0.0
    with pytest.raises(OverheadConfigurationError):
        sim._headspace_volume_m3()


def test_evaporation_total_pressure_ignores_downstream_residual() -> None:
    sim = SimpleNamespace(
        melt=SimpleNamespace(p_total_mbar=5.0),
        overhead=SimpleNamespace(pressure_mbar=900.0),
    )
    first = EvaporationMixin._evaporation_overhead_total_pressure_Pa(
        sim,
        {"Fe": 300.0},
    )
    sim.overhead.pressure_mbar = 0.001
    second = EvaporationMixin._evaporation_overhead_total_pressure_Pa(
        sim,
        {"Fe": 300.0},
    )
    assert first == second == pytest.approx(500.0)


def test_uncontrolled_equilibrium_po2_ignores_downstream_residual() -> None:
    sim = SimpleNamespace(
        melt=SimpleNamespace(
            atmosphere=Atmosphere.HARD_VACUUM,
            pO2_mbar=0.0,
        ),
        overhead=SimpleNamespace(composition={"O2": 500.0}),
        _melt_headspace_composition_mbar={"O2": 2.0},
        _overhead_headspace_enabled=lambda: False,
        _vacuum_floor_bar=lambda: 1.0e-12,
    )
    first = EquilibriumMixin._commanded_pO2_bar(sim)
    sim.overhead.composition["O2"] = 0.001
    second = EquilibriumMixin._commanded_pO2_bar(sim)
    assert first == second == pytest.approx(0.002)


@pytest.mark.parametrize("gate", ["coating", "knudsen_viscous"])
def test_continuous_negative_margin_reaches_optimizer_constraint(gate: str) -> None:
    margin = GateMargin(
        gate=gate,
        feasible=True,
        margin=-9.8,
        threshold=ThresholdSpec(
            id="test",
            value=0.0,
            units="dimensionless",
            source="engineering_envelope",
            source_ref="mutation-sensitive regression",
        ),
        observed=9.8,
        detail="continuous test",
        status_payload={"constraint_mode": "continuous"},
    )
    names, values = _constraint_values(
        SimpleNamespace(feasibility_margins={gate: margin}, feasible=True)
    )
    assert names == (gate,)
    assert values == pytest.approx((9.8,))


def test_first_snapshot_volatile_throttle_ignores_downstream_partials() -> None:
    sim = object.__new__(PyrolysisSimulator)
    sim.campaign_mgr = SimpleNamespace(
        get_temp_target=lambda *_args: (100.0, 10.0)
    )
    sim.melt = SimpleNamespace(
        campaign=CampaignPhase.C0,
        campaign_hour=0,
        temperature_C=0.0,
    )
    sim.overhead = SimpleNamespace(
        transport_saturation_pct=0.0,
        turbine_limited=False,
        turbine_utilization_pct=0.0,
        composition={"Na": 1.0e9, "K": 1.0e9},
    )
    sim._volatiles_train_spec = SimpleNamespace(max_throughput_kg_hr=1.0)
    sim.record = SimpleNamespace(snapshots=[])

    sim._update_temperature()

    assert sim._last_actual_ramp == pytest.approx(10.0)
    assert sim.melt.temperature_C == pytest.approx(10.0)


def test_free_molecular_knudsen_is_continuous_warning_not_refusal() -> None:
    diagnostic = knudsen_regime_diagnostic(
        overhead_pressure_mbar=1.0e-8,
        gas_temperature_C=1700.0,
        pipe_diameter_m=0.12,
    )
    assert diagnostic["regime"] == "free_molecular"
    assert diagnostic["status"] == "warning"
    assert 0.0 < diagnostic["regime_factor"] <= 1.0


def test_knudsen_optimizer_margin_is_finite_continuous_not_boolean_exclusion() -> None:
    snapshot = SimpleNamespace(
        knudsen_regime_summary={
            "segments": [
                {"name": "hot_wall", "knudsen_number": 0.02, "regime": "transition"}
            ]
        }
    )
    margin = PhysicsConstraintSet().knudsen_viscous(
        SimpleNamespace(snapshots=(snapshot,))
    )
    assert margin.feasible
    assert math.isfinite(margin.margin)
    assert margin.margin < 0.0


def test_null_resinter_threshold_emits_finite_deposition_constraint() -> None:
    trace = SimpleNamespace(
        wall_deposit_by_segment_species_kg={("hot_wall", "SiO"): 0.5},
        wall_deposit_sticking_authority={},
    )
    overlay = _trace_with_optimizer_coating_report(
        SimpleNamespace(trace=trace, campaigns_elapsed=1.0),
        PhysicsConstraintSet(active_gates=("coating",)),
    )
    margin = PhysicsConstraintSet().coating(overlay)
    assert margin.feasible
    assert margin.margin == pytest.approx(-0.5)
    assert margin.status_payload["coating_constraint_mode"] == (
        "no_unqualified_deposition"
    )
    names, values = _constraint_values(
        SimpleNamespace(feasibility_margins={"coating": margin}, feasible=True)
    )
    assert names == ("coating",)
    assert values == pytest.approx((0.5,))
