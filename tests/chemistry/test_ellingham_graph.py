"""Tests for the read-only Ellingham graph query API."""

from __future__ import annotations

import math

import pytest

from simulator.chemistry import ellingham_graph
from simulator.chemistry.ellingham_thermo import (
    ellingham_delta_g_kj_per_mol_o2,
    ellingham_stoichiometry,
)
from simulator.state import GAS_CONSTANT


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
    row = vapor_pressure_data["metals"]["K"]
    coeff = row["antoine"]
    T_K = 1429.0
    pO2_bar = 10.0**-7.853
    a_KO0_5 = 3.5e-5
    reference = 10.0 ** (coeff["A"] - coeff["B"] / (T_K + coeff["C"]))
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
