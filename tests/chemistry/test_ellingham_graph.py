"""Tests for the read-only Ellingham graph query API."""

from __future__ import annotations

import math
import warnings

import pytest

from simulator.chemistry import ellingham_graph
from simulator.chemistry.ellingham_thermo import (
    ellingham_delta_g_kj_per_mol_o2,
    ellingham_stoichiometry,
)
from simulator.state import GAS_CONSTANT
from simulator.vapour_rail.catalog import vapor_pressure_legacy_view


@pytest.fixture(scope="module")
def vapor_pressure_data():
    return ellingham_graph._load_default_vapor_pressure_data()


@pytest.mark.parametrize(
    ("species", "temperature_K"),
    [
        ("Na", 1600.0 + 273.15),
        ("Fe", 1800.0 + 273.15),
        ("Si", 1873.15),
    ],
)
def test_dissociation_delta_g_matches_ellingham_thermo(
    species: str,
    temperature_K: float,
) -> None:
    assert ellingham_graph.dissociation_delta_g(
        species,
        temperature_K,
    ) == ellingham_delta_g_kj_per_mol_o2(species, temperature_K)


@pytest.mark.parametrize(
    ("species", "temperature_K"),
    [
        ("Na", 1500.0),
        ("K", 1600.0),
        ("Fe", 1873.15),
    ],
)
def test_dissociation_pO2_threshold_matches_formation_equilibrium(
    species: str,
    temperature_K: float,
) -> None:
    dG_kJ = ellingham_delta_g_kj_per_mol_o2(species, temperature_K)
    _, n_ox = ellingham_stoichiometry(species)
    expected = math.exp(dG_kJ * 1000.0 / (GAS_CONSTANT * temperature_K)) * (
        1.0**n_ox
    )
    assert ellingham_graph.dissociation_pO2_threshold(
        species,
        temperature_K,
    ) == pytest.approx(expected, rel=0.0, abs=1e-12)


@pytest.mark.parametrize("oxygen", [-1.0, 0.0, float("nan"), float("inf"), -float("inf")])
def test_invalid_pO2_is_a_typed_refusal(vapor_pressure_data, oxygen):
    for call in (
        lambda: ellingham_graph.metal_activity_factor("Na", 1700.0, oxygen),
        lambda: ellingham_graph.effective_equilibrium_pressure_Pa(
            "Na", 1700.0, oxygen, vapor_pressure_data=vapor_pressure_data,
        ),
        lambda: ellingham_graph.evolves(
            "Na", 1700.0, oxygen, vapor_pressure_data=vapor_pressure_data,
        ),
    ):
        with pytest.raises(ellingham_graph.EllinghamPressureRefusal, match="finite and positive"):
            call()


@pytest.mark.parametrize("route", ["helper", "fallback"])
@pytest.mark.parametrize("species,exponent", [("SiO", -0.5), ("Si", -1.0)])
def test_b984_actual_oxygen_reaction_law_and_notice(vapor_pressure_data, route, species, exponent):
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend

    backend = AlphaMELTSBackend()
    answers = {}
    receipts = {}
    for oxygen in (1e-31, 1e-30, 1e-29, 1e-12, 1e-9, 1e-8, 100.0, 1000.0):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            if route == "helper":
                pressure = ellingham_graph.effective_equilibrium_pressure_Pa(
                    species, 1873.15, oxygen, vapor_pressure_data=vapor_pressure_data,
                )
            else:
                pressure = backend._activities_times_antoine(
                    1600.0, {"SiO2": 1.0}, {"SiO2": 100.0}, pO2_bar=oxygen,
                )[species]
                receipts[oxygen] = backend._antoine_floor_inversion_notices.get(species)
        answers[oxygen] = pressure
        if oxygen in (1e-31, 1000.0):
            assert any("pO2_outside_evaluator_band" in str(w.message)
                       and repr(oxygen) in str(w.message) for w in caught)
    # SiO2 -> SiO + 1/2 O2 gives exponent -1/2; SiO2 -> Si + O2 gives -1.
    # Fixed T/activity cancels the equilibrium constant in P/P_ref, leaving
    # (pO2/p_ref)**exponent (bar/bar dimensionless), continuous through p_ref.
    for oxygen, pressure in answers.items():
        assert pressure / answers[1e-9] == pytest.approx((oxygen / 1e-9) ** exponent)
    if route == "fallback":
        for oxygen in (1e-31, 1000.0):
            notice = receipts[oxygen]
            assert notice["reason"] == "pO2_outside_evaluator_band"
            assert notice["pO2_bar_used"] == oxygen
            assert notice["certified_band"]["pO2_bar"] == (1e-30, 100.0)
            assert notice["was_clamped"] is False


def test_evolves_uses_builtin_pressure_floor(vapor_pressure_data) -> None:
    T_K = 1600.0
    pO2_bar = 1e-9
    P_eff = ellingham_graph.effective_equilibrium_pressure_Pa(
        "Na",
        T_K,
        pO2_bar,
        vapor_pressure_data=vapor_pressure_data,
    )
    assert P_eff > ellingham_graph.EVOLUTION_PRESSURE_FLOOR_PA
    assert ellingham_graph.evolves(
        "Na",
        T_K,
        pO2_bar,
        vapor_pressure_data=vapor_pressure_data,
    )
    assert not ellingham_graph.evolves(
        "Ti",
        T_K,
        pO2_bar,
        vapor_pressure_data=vapor_pressure_data,
    )


def test_k_standard_reaction_graph_uses_activity_and_po2_scaling(
    vapor_pressure_data,
) -> None:
    # Schema-v2 is canonical; the Ellingham graph consumes its compiler-owned
    # legacy projection rather than reaching through the four strata directly.
    row = vapor_pressure_legacy_view(vapor_pressure_data)["metals"]["K"]
    T_K = 1429.0
    pO2_bar = 10.0**-7.853
    a_KO0_5 = 3.5e-5
    reference = float.fromhex("0x1.004c838a0bdb7p+10")
    expected = reference * a_KO0_5 * (pO2_bar ** -0.25)

    assert row["fit_target"] == "standard_reaction_term"
    assert ellingham_graph.effective_equilibrium_pressure_Pa(
        "K",
        T_K,
        pO2_bar,
        vapor_pressure_data=vapor_pressure_data,
        a_oxide=a_KO0_5,
    ) == pytest.approx(expected)


def test_evolution_order_qualitative_pyrolysis_sequence(vapor_pressure_data) -> None:
    # Representative mbar-sweep operating point in the Fe/SiO band.
    T_K = 1650.0
    pO2_bar = 1e-6
    species = ("Na", "K", "Fe", "SiO", "Mg", "Ca", "Al", "Ti")
    ranked = ellingham_graph.evolution_order(
        T_K,
        pO2_bar,
        species,
        vapor_pressure_data=vapor_pressure_data,
    )
    order = [entry.species for entry in ranked]

    assert order.index("Na") < order.index("Fe")
    assert order.index("K") < order.index("Fe")
    assert order.index("Fe") < order.index("Ca")
    assert order.index("Fe") < order.index("Al")
    fe_P = next(entry.P_eff_Pa for entry in ranked if entry.species == "Fe")
    ca_P = next(entry.P_eff_Pa for entry in ranked if entry.species == "Ca")
    al_P = next(entry.P_eff_Pa for entry in ranked if entry.species == "Al")
    assert ca_P < fe_P * 1e-2
    assert al_P < fe_P * 1e-2
    assert not ellingham_graph.evolves(
        "Ti",
        T_K,
        pO2_bar,
        vapor_pressure_data=vapor_pressure_data,
    )


def test_evolution_order_sio_rises_under_hard_vacuum(vapor_pressure_data) -> None:
    T_K = 1800.0
    sio_hard = ellingham_graph.effective_equilibrium_pressure_Pa(
        "SiO",
        T_K,
        1e-9,
        vapor_pressure_data=vapor_pressure_data,
        vacuum_floor_bar=1e-9,
    )
    sio_held = ellingham_graph.effective_equilibrium_pressure_Pa(
        "SiO",
        T_K,
        1e-3,
        vapor_pressure_data=vapor_pressure_data,
        vacuum_floor_bar=1e-9,
    )
    fe_hard = ellingham_graph.effective_equilibrium_pressure_Pa(
        "Fe",
        T_K,
        1e-9,
        vapor_pressure_data=vapor_pressure_data,
    )
    assert sio_hard > sio_held * 100.0
    assert sio_hard > ellingham_graph.EVOLUTION_PRESSURE_FLOOR_PA
    assert fe_hard > ellingham_graph.EVOLUTION_PRESSURE_FLOOR_PA
    assert 0.01 < sio_hard / fe_hard < 10.0


def test_crossover_temperature_na_fe_near_mandate() -> None:
    # Anchor = the JANAF-4th MULTIPHASE Na/Fe crossover from the Chase 1998 primary
    # ΔG grid (the 2026-07-09 re-ground fit reproduces that grid to <0.5 kJ/mol_O2).
    # It REPLACES the earlier linear-refit anchor 1173.4 C: the multiphase alkali line
    # changes slope after the Na boiling point (1156 K), so its intersection with the
    # Fe line moves +~8 C. Independent JANAF-4th extraction (grok-68889) corroborates
    # (~1184 C). Mandate CLAUDE.md §4 and chemistry-methods §7.2 updated to match.
    assert ellingham_graph.crossover_temperature("Na", "Fe") == pytest.approx(
        1181.5,
        abs=0.5,
    )


def test_crossover_temperature_k_fe_matches_janaf_anchor() -> None:
    # Anchor = the JANAF-4th MULTIPHASE K/Fe crossover (Chase 1998 primary grid),
    # 2026-07-09 re-ground. Replaces the linear-refit 832.0 C; the K boiling point
    # (1032 K) slope change shifts the Fe crossover +~4 C. K is fitted to 2000 K then
    # fail-closed. Mandate + §7.2 updated to match.
    assert ellingham_graph.crossover_temperature("K", "Fe") == pytest.approx(
        836.25,
        abs=0.5,
    )


def test_to_chart_data_returns_plain_series() -> None:
    chart = ellingham_graph.to_chart_data(
        ("Na", "Fe"),
        T_min_K=1100.0,
        T_max_K=1110.0,
        T_step_K=5.0,
    )
    assert set(chart) == {"Na", "Fe"}
    assert len(chart["Na"]) == 3
    assert chart["Na"][0]["delta_g_kJ_per_mol_O2"] == pytest.approx(
        ellingham_graph.dissociation_delta_g("Na", 1100.0),
    )
