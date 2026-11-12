"""Stage C: the hour evaluates vapour at the interface oxygen root.

The closed-form and bisection oracles live in the Stage B test module.
This file checks that the hour passes those same inputs and books the
film exchange. It does not change the solver.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.oxygen_interface import (
    InterfaceOxygenChannel,
    MissingInterfaceOxygenPotential,
    require_evaporation_oxygen_potential_log10,
    solve_interface_oxygen,
)
from simulator.core import (
    FO2_BUFFER_ACCOUNT,
    OXYGEN_RESERVOIR_NOOP_MOL,
    PyrolysisSimulator,
)
from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.physical_constants import GAS_CONSTANT, PA_PER_BAR
from tests.chemistry.test_oxygen_interface import (
    AREA_M2,
    K_BAR,
    M_FE_KG_PER_MOL,
    TEMPERATURE_K,
    _closed_form_pressure_bar,
    _expected_fe_flux,
    _independent_bisection,
    _independent_sources,
)

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


class _Ledger:
    def __init__(self, accounts: dict[str, dict[str, float]]) -> None:
        self._accounts = accounts

    def mol_by_account(self, name: str) -> dict[str, float]:
        return dict(self._accounts.get(name, {}))


def _fe_channel() -> InterfaceOxygenChannel:
    return InterfaceOxygenChannel(
        species="Fe",
        molar_mass_kg_per_mol=M_FE_KG_PER_MOL,
        oxygen_atoms=0.0,
        parent_oxygen_demand=1.0,
        pO2_exponent=-0.5,
        alpha=1.0,
        r_gas_m_s=0.0,
    )


def _hour_sim(
    *,
    capacity: float,
    reservoir_log: float,
    overhead: float,
    k_bar: float = K_BAR,
):
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = type("Melt", (), {})()
    sim.melt.temperature_C = TEMPERATURE_K - 273.15
    sim.melt.melt_surface_area_m2 = AREA_M2
    sim.melt.oxygen_reservoir = type("Reservoir", (), {})()
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = reservoir_log
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = overhead
    calls: list[float] = []

    def pressures(log10_pO2_bar: float, _species) -> dict[str, float]:
        calls.append(float(log10_pO2_bar))
        pO2_bar = 10.0 ** float(log10_pO2_bar)
        return {"Fe": k_bar / math.sqrt(pO2_bar) * PA_PER_BAR}

    channel = _fe_channel()
    sim._interface_oxygen_pressures_pa = pressures
    sim._interface_oxygen_channels = lambda _temperature_K: (channel,)
    sim._melt_redox_source_capacity_mol_per_ln_fO2 = (
        lambda **_kwargs: capacity
    )
    sim._oxygen_exchange_k_m_s = lambda _temperature_K: (1.0, "test")
    sim._oxygen_exchange_effective_melt_depth_m = lambda: 1.0
    sim._commanded_pO2_bar = lambda: 0.0
    return sim, calls


def _solve_hour(**kwargs):
    sim, calls = _hour_sim(**kwargs)
    sim._ensure_hour_interface_oxygen()
    return sim, calls


def test_buffered_hour_requests_vapour_at_reservoir_fo2() -> None:
    reservoir_log = -2.0
    sim, calls = _solve_hour(
        capacity=1.0e8,
        reservoir_log=reservoir_log,
        overhead=0.0,
    )

    solution = sim._hour_interface_oxygen
    assert solution.surface_log10_pO2_bar == pytest.approx(reservoir_log, abs=1e-6)
    assert calls[-1] == pytest.approx(reservoir_log, abs=1e-6)
    assert math.log10(sim._headspace_transport_pO2_bar()) == pytest.approx(
        reservoir_log, abs=1e-6
    )


def test_zero_capacity_hour_reproduces_analytic_langmuir_root() -> None:
    sim, _calls = _solve_hour(capacity=0.0, reservoir_log=-2.0, overhead=0.0)
    solution = sim._hour_interface_oxygen

    assert solution.surface_pO2_bar == pytest.approx(
        _closed_form_pressure_bar(), rel=1e-10
    )
    assert solution.capacity_label == "none:no_melt_redox_capacity"
    assert solution.product_fluxes_mol_m2_s["Fe"] == pytest.approx(
        _expected_fe_flux(solution), rel=1e-10
    )
    assert sim._headspace_transport_pO2_bar() == pytest.approx(
        solution.surface_pO2_bar, rel=1e-12
    )
    assert solution.surface_pO2_bar != pytest.approx(DEFAULT_VACUUM_FLOOR_BAR)


def test_hour_passes_effusion_only(monkeypatch) -> None:
    seen: dict[str, object] = {}
    real = solve_interface_oxygen

    def spy(*args, **kwargs):
        seen["channels"] = args[1]
        seen["kwargs"] = kwargs
        return real(*args, **kwargs)

    monkeypatch.setattr(
        "simulator.chemistry.oxygen_interface.solve_interface_oxygen",
        spy,
    )
    sim, _calls = _solve_hour(capacity=0.0, reservoir_log=-2.0, overhead=0.0)

    assert seen["kwargs"]["oxygen_r_gas_m_s"] == 0.0
    assert seen["kwargs"]["oxygen_alpha"] == 1.0
    assert all(channel.r_gas_m_s == 0.0 for channel in seen["channels"])
    assert sim._hour_interface_oxygen is not None


def test_overhead_above_root_stays_on_the_signed_balance() -> None:
    overhead = 10.0 * _closed_form_pressure_bar()
    root_log10 = _independent_bisection(
        capacity=0.0,
        reservoir_log10_fO2=-2.0,
        overhead_pO2_bar=overhead,
    )
    root_flux = _independent_sources(
        root_log10,
        capacity=0.0,
        reservoir_log10_fO2=-2.0,
        overhead_pO2_bar=overhead,
    )[0]
    sim, _calls = _solve_hour(
        capacity=0.0,
        reservoir_log=-2.0,
        overhead=overhead,
    )
    solution = sim._hour_interface_oxygen

    assert solution.surface_pO2_bar == pytest.approx(10.0**root_log10, rel=1e-10)
    assert solution.surface_pO2_bar > overhead
    assert solution.label is None
    assert solution.product_fluxes_mol_m2_s["Fe"] == pytest.approx(
        root_flux, rel=1e-10
    )
    assert solution.product_fluxes_mol_m2_s["Fe"] > 0.0


def test_overhead_above_one_bar_predicts_at_the_partial_without_the_solver() -> None:
    # The solver domain ends at 1 bar. A measured partial above that bound
    # is the overhead floor: the surface cannot undercut it, the film source
    # is zero, and the pressure model is not called.
    sim, calls = _solve_hour(capacity=0.0, reservoir_log=-2.0, overhead=2.0)
    solution = sim._hour_interface_oxygen

    assert calls == []
    assert solution.surface_pO2_bar == pytest.approx(2.0)
    assert solution.surface_log10_pO2_bar == pytest.approx(math.log10(2.0))
    assert solution.label == "overhead_oxygen_floor"
    assert solution.gas_source_mol_s == 0.0
    assert solution.product_fluxes_mol_m2_s == {}
    assert sim._headspace_transport_pO2_bar() == pytest.approx(2.0)
    notice = sim._interface_oxygen_snapshot_notice()
    assert notice["solved"] is True
    assert notice["bracket_edge"] is False
    assert notice["label"] == "overhead_oxygen_floor"


def test_lower_edge_predicts_at_the_bracket_and_does_not_zero() -> None:
    sim, _calls = _solve_hour(
        capacity=0.0,
        reservoir_log=-2.0,
        overhead=0.0,
        k_bar=1.0e-45,
    )
    solution = sim._hour_interface_oxygen

    assert solution.surface_pO2_bar == 1.0e-30
    assert solution.label == "bracket_boundary=lower"
    assert solution.product_fluxes_mol_m2_s["Fe"] == pytest.approx(
        _expected_fe_flux(solution, k_bar=1.0e-45), rel=1e-10
    )
    assert sim._headspace_transport_pO2_bar() == 1.0e-30
    notice = sim._interface_oxygen_snapshot_notice()
    assert notice["bracket_edge"] is True
    assert notice["xi_s"] == pytest.approx(-30.0 * math.log(10.0), rel=1e-12)
    assert notice["solved"] is True


def test_evaporation_publish_refuses_a_missing_surface() -> None:
    with pytest.raises(MissingInterfaceOxygenPotential, match="xi_s"):
        require_evaporation_oxygen_potential_log10(None)
    with pytest.raises(MissingInterfaceOxygenPotential, match="dissociation"):
        require_evaporation_oxygen_potential_log10(
            SimpleNamespace(surface_log10_pO2_bar=-47.88)
        )


def test_evaporation_publish_uses_the_solved_surface_not_the_reservoir() -> None:
    # Reservoir −40 would be rewritten to 1e-30 bar by the dissociation
    # clamp. The publish must hand the vapour law the solved surface.
    sim, _calls = _solve_hour(capacity=0.0, reservoir_log=-40.0, overhead=0.0)
    solution = sim._hour_interface_oxygen
    assert require_evaporation_oxygen_potential_log10(solution) == pytest.approx(
        solution.surface_log10_pO2_bar
    )
    assert solution.surface_log10_pO2_bar > -30.0

    seen: dict[str, float] = {}

    def dispatch(_intent, **kwargs):
        seen["fO2_log"] = float(kwargs["fO2_log"])
        seen["intrinsic"] = float(kwargs["control_inputs"]["intrinsic_fO2_log"])
        seen["pO2_bar"] = float(kwargs["control_inputs"]["pO2_bar"])
        raise RuntimeError("stop-after-publish-inputs")

    sim._dispatch_only = dispatch
    sim._last_backend_diagnostics = {}
    sim._oxygen_log_guard_bar = lambda: 1.0e-30
    sim.melt.ambient_pressure_mbar = 10.0
    sim.melt.campaign = SimpleNamespace(name="C2A")
    sim.liquid_feo_domain_at = lambda _temperature_K: None
    result = SimpleNamespace(
        vapor_pressures_Pa={"Fe": 1.0},
        vapor_pressures_source={},
        warnings=[],
        liquid_fraction=1.0,
        diagnostics={},
    )
    with pytest.raises(RuntimeError, match="stop-after-publish-inputs"):
        sim._refresh_vapor_pressures_from_kernel(
            result,
            publish_interface_oxygen=True,
        )

    assert seen["intrinsic"] == pytest.approx(solution.surface_log10_pO2_bar)
    assert seen["fO2_log"] == pytest.approx(solution.surface_log10_pO2_bar)
    assert seen["pO2_bar"] == pytest.approx(solution.surface_pO2_bar)
    assert seen["intrinsic"] != pytest.approx(-40.0)


def test_direct_refresh_keeps_the_reservoir_log() -> None:
    sim, _calls = _solve_hour(capacity=0.0, reservoir_log=-2.0, overhead=0.0)
    seen: dict[str, float] = {}

    def dispatch(_intent, **kwargs):
        seen["intrinsic"] = float(kwargs["control_inputs"]["intrinsic_fO2_log"])
        raise RuntimeError("stop-after-publish-inputs")

    sim._dispatch_only = dispatch
    sim._last_backend_diagnostics = {}
    sim._oxygen_log_guard_bar = lambda: 1.0e-30
    sim.melt.ambient_pressure_mbar = 10.0
    sim.melt.campaign = SimpleNamespace(name="C2A")
    sim.liquid_feo_domain_at = lambda _temperature_K: None
    result = SimpleNamespace(
        vapor_pressures_Pa={"Fe": 1.0},
        vapor_pressures_source={},
        warnings=[],
        liquid_fraction=1.0,
        diagnostics={},
    )
    with pytest.raises(RuntimeError, match="stop-after-publish-inputs"):
        sim._refresh_vapor_pressures_from_kernel(result)

    assert seen["intrinsic"] == pytest.approx(-2.0)


def test_same_temperature_reuses_the_surface() -> None:
    sim, calls = _solve_hour(capacity=0.0, reservoir_log=-2.0, overhead=0.0)
    first = len(calls)
    sim._ensure_hour_interface_oxygen()
    assert len(calls) == first
    sim.melt.temperature_C += 10.0
    sim._ensure_hour_interface_oxygen()
    assert len(calls) > first


def _booking_sim(gas_source_mol_s: float, *, buffer_mol: float, head_mol: float, floor_mol: float):
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim._hour_interface_oxygen = type("Solution", (), {
        "gas_source_mol_s": gas_source_mol_s,
        "surface_pO2_bar": 1.0e-6,
        "surface_log10_pO2_bar": -6.0,
        "label": None,
        "capacity_label": None,
        "melt_source_mol_s": 0.0,
        "evaporative_source_mol_s": gas_source_mol_s,
    })()
    sim.atom_ledger = _Ledger({
        "process.overhead_gas": {"O2": head_mol},
        FO2_BUFFER_ACCOUNT: {"O2": buffer_mol},
    })
    sim._headspace_floor_o2_mol = lambda: floor_mol
    calls: dict[str, list] = {"dispatch": [], "redox": []}

    def dispatch(intent, **kwargs):
        calls["dispatch"].append((intent, kwargs))

    def redox(terms):
        calls["redox"].append(dict(terms))

    sim._dispatch_and_commit = dispatch
    sim._apply_oxygen_reservoir_redox_source_terms = redox
    return sim, calls


def test_booking_clamps_positive_film_to_buffer_inventory() -> None:
    sim, calls = _booking_sim(1.0 / 3600.0, buffer_mol=0.25, head_mol=1.0, floor_mol=0.01)
    sim._book_hour_interface_oxygen()

    assert calls["dispatch"][0][0] is ChemistryIntent.OXYGEN_RESERVOIR_EXCHANGE
    assert calls["dispatch"][0][1]["control_inputs"]["dn_to_headspace_mol"] == pytest.approx(0.25)
    # No solve-time melt leg was stashed, so the film atom move does not
    # also enter the fO2 tangent.
    assert calls["redox"] == []
    booking = sim._hour_interface_oxygen_booking
    assert booking["exchange_o2_mol"] == pytest.approx(0.25)
    assert booking["exchange_unbacked_o2_mol"] == pytest.approx(0.75)
    assert booking["exchange_direction"] == "melt_to_headspace"


def test_booking_clamps_inward_film_to_overhead_above_the_log_guard() -> None:
    sim, calls = _booking_sim(
        -1.0 / 3600.0, buffer_mol=2.0, head_mol=0.4, floor_mol=0.1,
    )
    sim._book_hour_interface_oxygen()

    booked = calls["dispatch"][0][1]["control_inputs"]["dn_to_headspace_mol"]
    assert booked == pytest.approx(-0.3)
    assert calls["redox"] == []
    booking = sim._hour_interface_oxygen_booking
    assert booking["exchange_direction"] == "headspace_to_melt"
    assert booking["exchange_unbacked_o2_mol"] == pytest.approx(-0.7)


def test_booking_below_noop_does_not_dispatch() -> None:
    sim, calls = _booking_sim(
        OXYGEN_RESERVOIR_NOOP_MOL / 3600.0 / 10.0,
        buffer_mol=1.0,
        head_mol=1.0,
        floor_mol=0.0,
    )
    sim._book_hour_interface_oxygen()

    assert calls["dispatch"] == []
    assert calls["redox"] == []
    assert sim._hour_interface_oxygen_booking["exchange_direction"] == "none:below_threshold"
    assert sim._hour_interface_oxygen_booking["exchange_o2_mol"] == 0.0


def _stash_melt_leg(sim, *, current_log: float, capacity_now: float) -> None:
    sim._hour_interface_oxygen.surface_log10_pO2_bar = -12.0
    sim._hour_interface_oxygen_capacity_mol_per_ln = 3.0
    sim._hour_interface_oxygen_exchange_time_s = 10000.0
    sim._hour_interface_oxygen_reservoir_ln = -8.0 * math.log(10.0)
    sim.melt = SimpleNamespace(temperature_C=1600.0, p_total_mbar=10.0)
    sim._vacuum_floor_bar = lambda: 1.0e-9
    sim._current_melt_redox_fO2_log = lambda: current_log
    sim._melt_redox_source_capacity_mol_per_ln_fO2 = (
        lambda **kwargs: capacity_now
    )


def test_booking_redox_closes_the_gap_to_xi_s_and_not_the_film() -> None:
    # Film is 10 mol. The melt leg from log10 -8 toward -12 over
    # Δt/τ = 0.36 is 1.44 dex, independent of that film count.
    sim, calls = _booking_sim(
        10.0 / 3600.0, buffer_mol=100.0, head_mol=1.0, floor_mol=0.0,
    )
    _stash_melt_leg(sim, current_log=-2.0, capacity_now=3.0)
    sim._book_hour_interface_oxygen()

    delta_ln = -4.0 * math.log(10.0) * 0.36
    target_log = -8.0 + delta_ln / math.log(10.0)
    expected = (target_log - (-2.0)) * math.log(10.0) * 3.0
    assert calls["dispatch"][0][1]["control_inputs"]["dn_to_headspace_mol"] == pytest.approx(10.0)
    assert calls["redox"][0]["interface_oxygen_exchange"] == pytest.approx(expected)
    assert calls["redox"][0]["interface_oxygen_exchange"] != pytest.approx(-10.0)


def test_booking_collapsed_capacity_does_not_integrate_the_film() -> None:
    sim, calls = _booking_sim(
        10.0 / 3600.0, buffer_mol=100.0, head_mol=1.0, floor_mol=0.0,
    )
    _stash_melt_leg(sim, current_log=-8.0, capacity_now=0.0)
    sim._book_hour_interface_oxygen()

    assert calls["dispatch"][0][1]["control_inputs"]["dn_to_headspace_mol"] == pytest.approx(10.0)
    assert calls["redox"] == []


def test_unsolved_snapshot_notice_is_explicit() -> None:
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim._hour_interface_oxygen = None
    sim._hour_interface_oxygen_booking = None

    notice = sim._interface_oxygen_snapshot_notice()

    assert notice["solved"] is False
    assert notice["bracket_edge"] is False


def _yaml(name: str):
    return yaml.safe_load((DATA / name).read_text(encoding="utf-8"))


def _loaded(feedstocks: dict):
    sim = PyrolysisSimulator(
        InternalAnalyticalBackend(),
        _yaml("setpoints.yaml"),
        feedstocks,
        _yaml("vapor_pressures.yaml"),
    )
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.melt.temperature_C = 1600.0
    sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
    return sim


def test_mars_hour_passes_surface_oxygen_and_log_guard_to_backend(monkeypatch):
    feedstocks = copy.deepcopy(_yaml("feedstocks.yaml"))
    feedstocks["lunar_mare_low_ti"].update({
        "body": "mars",
        "environment": {"surface_pressure_mbar": 6.1, "atmosphere": "CO2"},
    })
    sim = _loaded(feedstocks)

    class RecordingBackend:
        def __init__(self):
            self.kwargs = None

        def is_available(self):
            return True

        def equilibrate(
            self, *, temperature_C, composition_mol, species_formula_registry,
            fO2_log, pressure_bar, composition_mol_by_account=None,
            vapor_transport_pO2_bar=None, vapor_transport_floor_bar=None,
            vapor_transport_pO2_admission="environment",
        ):
            self.kwargs = {
                "pressure_bar": pressure_bar,
                "vapor_transport_pO2_bar": vapor_transport_pO2_bar,
                "vapor_transport_floor_bar": vapor_transport_floor_bar,
                "vapor_transport_pO2_admission": vapor_transport_pO2_admission,
            }
            return SimpleNamespace(
                ledger_transition=None,
                phase_species_mol={},
                phase_species_kg={},
                phase_masses_kg={},
            )

    backend = RecordingBackend()
    sim.backend = backend
    monkeypatch.setattr(
        sim, "_record_equilibrium_status", lambda result, **_kwargs: result,
    )

    sim._get_equilibrium()

    surface_pO2_bar = sim._hour_interface_oxygen.surface_pO2_bar
    mars_ambient_bar = sim._vacuum_floor_bar()
    assert mars_ambient_bar == pytest.approx(6.1e-3)
    assert backend.kwargs["pressure_bar"] == pytest.approx(mars_ambient_bar)
    assert backend.kwargs["vapor_transport_pO2_bar"] == pytest.approx(
        surface_pO2_bar,
    )
    assert backend.kwargs["vapor_transport_floor_bar"] == pytest.approx(
        DEFAULT_VACUUM_FLOOR_BAR,
    )
    assert backend.kwargs["vapor_transport_pO2_admission"] == "environment"
    for oxygen_slot in ("vapor_transport_pO2_bar", "vapor_transport_floor_bar"):
        assert backend.kwargs[oxygen_slot] != pytest.approx(mars_ambient_bar)


def test_production_fe_channel_uses_catalog_stoichiometry() -> None:
    sim = _loaded(_yaml("feedstocks.yaml"))
    channels = {
        channel.species: channel
        for channel in sim._interface_oxygen_channels(1600.0 + 273.15)
    }
    iron = channels["Fe"]
    silica = channels["SiO"]

    assert iron.oxygen_atoms == 0.0
    assert iron.parent_oxygen_demand == pytest.approx(1.0)
    assert iron.pO2_exponent == pytest.approx(-0.5)
    assert iron.r_gas_m_s == 0.0
    assert silica.oxygen_atoms == pytest.approx(1.0)
    assert silica.parent_oxygen_demand == pytest.approx(2.0)
    assert silica.pO2_exponent == pytest.approx(-0.5)
    assert silica.r_gas_m_s == 0.0


def test_enclosed_furnace_interface_is_body_independent(tmp_path: Path) -> None:
    unset_feed = _yaml("feedstocks.yaml")
    moon_feed = copy.deepcopy(unset_feed)
    moon_feed["lunar_mare_low_ti"]["body"] = "moon"
    scratch = tmp_path / "feedstocks-moon.yaml"
    scratch.write_text(yaml.safe_dump(moon_feed), encoding="utf-8")
    moon_loaded = yaml.safe_load(scratch.read_text(encoding="utf-8"))

    unset = _loaded(unset_feed)
    moon = _loaded(moon_loaded)
    assert unset.melt.body == ""
    assert moon.melt.body == "moon"
    assert moon._vacuum_floor_bar() < unset._vacuum_floor_bar()
    assert moon._oxygen_numerical_floor_bar() < unset._oxygen_numerical_floor_bar()
    assert moon._oxygen_log_guard_bar() == pytest.approx(DEFAULT_VACUUM_FLOOR_BAR)
    assert unset._oxygen_log_guard_bar() == pytest.approx(DEFAULT_VACUUM_FLOOR_BAR)

    unset_eq = unset._get_equilibrium()
    moon_eq = moon._get_equilibrium()

    assert moon._hour_interface_oxygen.surface_pO2_bar == (
        unset._hour_interface_oxygen.surface_pO2_bar
    )
    assert moon._hour_interface_oxygen.label == unset._hour_interface_oxygen.label
    assert moon._commanded_pO2_bar() == unset._commanded_pO2_bar()
    for species in ("Fe", "SiO"):
        assert moon_eq.vapor_pressures_Pa[species] == unset_eq.vapor_pressures_Pa[species]
