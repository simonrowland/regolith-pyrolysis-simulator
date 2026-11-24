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
from simulator.vapour_rail.catalog import (
    compiled_catalog_for,
    vapor_pressure_legacy_view,
)


@pytest.fixture(scope="module")
def vapor_pressure_data():
    return vapor_pressure_legacy_view(
        ellingham_graph._load_default_vapor_pressure_data()
    )


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
def test_b984_actual_oxygen_reaction_law_and_notice(
    vapor_pressure_data, route, species, exponent, monkeypatch,
):
    from simulator import engine_local_config
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend

    monkeypatch.setattr(engine_local_config, "_WARNING_REPEAT_COUNTS", {})
    backend = AlphaMELTSBackend()
    saturation_pressure_data = (
        ellingham_graph._load_default_vapor_pressure_data()
        if species == "Si"
        else vapor_pressure_data
    )
    helper_pressure_data = (
        saturation_pressure_data if route == "helper" else vapor_pressure_data
    )
    answers = {}
    receipts = {}
    warning_messages = []
    for oxygen in (1e-31, 1e-30, 1e-29, 1e-12, 1e-9, 1e-8, 100.0, 1000.0):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            if route == "helper":
                # The legacy projection carries Si's reference pressure model
                # but not the complete source inputs needed by this evaluator.
                pressure = ellingham_graph.effective_equilibrium_pressure_Pa(
                    species,
                    1873.15,
                    oxygen,
                    vapor_pressure_data=helper_pressure_data,
                )
            else:
                pressure = backend._activities_times_antoine(
                    1600.0, {"SiO2": 1.0}, {"SiO2": 100.0}, pO2_bar=oxygen,
                )[species]
                receipts[oxygen] = backend._antoine_floor_inversion_notices.get(species)
        warning_messages.extend(str(item.message) for item in caught)
        answers[oxygen] = pressure
    assert warning_messages
    assert all(
        "pO2_outside_evaluator_band" in message
        for message in warning_messages
    )
    assert all("pO2_bar_used" in message for message in warning_messages)
    assert any("1e-31" in message for message in warning_messages)
    assert any("1000.0" in message for message in warning_messages)
    # SiO2 -> SiO + 1/2 O2 gives exponent -1/2; SiO2 -> Si + O2 gives -1.
    # Fixed T/activity cancels the equilibrium constant in P/P_ref, leaving
    # (pO2/p_ref)**exponent (bar/bar dimensionless), continuous through p_ref.
    # The Si elemental rail cannot exceed its pure-component saturation
    # pressure: its source reaction implies metal activity at most unity. Use
    # the owner lookup, which resolves the catalog shape used by production.
    saturation_Pa = (
        ellingham_graph.metal_saturation_pressure_Pa(
            species, 1873.15, vapor_pressure_data=saturation_pressure_data,
        )
        if species == "Si"
        else math.inf
    )
    # At 1000 bar, the Si power-law pressure is still below saturation; at
    # 1e-31 bar its uncapped value (~8.84e14 Pa) greatly exceeds P_sat ~0.894 Pa.
    assert answers[1000.0] < saturation_Pa
    if species == "Si":
        assert answers[1e-31] == pytest.approx(saturation_Pa)
    for oxygen, pressure in answers.items():
        expected = min(
            answers[1000.0] * (oxygen / 1000.0) ** exponent,
            saturation_Pa,
        )
        assert pressure == pytest.approx(expected)
    if route == "fallback":
        for oxygen in (1e-31, 1000.0):
            notice = receipts[oxygen]
            assert notice["reason"] == "pO2_outside_evaluator_band"
            assert notice["pO2_bar_used"] == oxygen
            assert notice["certified_band"]["pO2_bar"] == (1e-30, 100.0)
            assert notice["was_clamped"] is False


def test_every_catalog_metal_saturation_pressure_resolves(
    vapor_pressure_data,
) -> None:
    """Each metals-catalog row must provide an elemental saturation rail."""

    from engines.builtin.sodium_saturation import (
        sodium_saturation_block,
        sodium_saturation_pressure,
    )

    temperature_K = 1423.15
    metals = vapor_pressure_data["metals"]
    assert "Na" in metals
    for species, row in metals.items():
        pressure = ellingham_graph.metal_saturation_pressure_Pa(
            species,
            temperature_K,
            vapor_pressure_data=vapor_pressure_data,
        )
        assert math.isfinite(pressure) and pressure >= 0.0, species
        block = sodium_saturation_block(row)
        if block is not None:
            assert pressure == sodium_saturation_pressure(
                block, temperature_K
            ).pressure_pa


@pytest.mark.parametrize("species", ("Mg", "Na", "K"))
def test_elemental_metal_pressure_is_capped_at_pure_saturation(
    vapor_pressure_data,
    species,
) -> None:
    from engines.builtin.vapor_pressure import (
        COEFF_BLOCK_PURE_COMPONENT,
        vapor_pressure_antoine_coefficients,
        wall_condensation_antoine_coefficients,
    )

    temperature_K = 1423.15
    cap_notices = []
    pressure = ellingham_graph._effective_equilibrium_pressure_Pa_with_notices(
        species,
        temperature_K,
        1e-28,
        vapor_pressure_data=vapor_pressure_data,
        cap_notices=cap_notices,
    )
    pure_metal_saturation = ellingham_graph.metal_saturation_pressure_Pa(
        species,
        temperature_K,
        vapor_pressure_data=vapor_pressure_data,
    )
    pure_antoine, coefficient_block = wall_condensation_antoine_coefficients(
        vapor_pressure_data["metals"][species], temperature_K,
    )
    if species in {"Na", "K"}:
        runtime_antoine, runtime_block = vapor_pressure_antoine_coefficients(
            vapor_pressure_data["metals"][species], temperature_K,
        )
        assert runtime_block != coefficient_block
        assert runtime_antoine != pure_antoine

    assert coefficient_block == COEFF_BLOCK_PURE_COMPONENT
    if species == "Na":
        from engines.builtin.sodium_saturation import (
            sodium_saturation_block,
            sodium_saturation_pressure,
        )

        sodium_block = sodium_saturation_block(
            vapor_pressure_data["metals"][species]
        )
        assert sodium_block is not None
        assert pure_metal_saturation == sodium_saturation_pressure(
            sodium_block, temperature_K
        ).pressure_pa
    else:
        assert pure_metal_saturation == ellingham_graph._antoine_reference_pressure_Pa(
            pure_antoine,
            temperature_K,
        )
    if species == "Mg":
        # The JANAF Mg(g)/MgO fit gives ΔG≈−877 kJ/mol O2 at 1423.15 K,
        # hence K≈6.4e−33 bar. At pO2=1e-28 bar, a_M≈0.008 and p_Mg is
        # below the pure-metal sidecar cap, so no cap notice is emitted.
        assert pressure == pytest.approx(
            799.6039375676731,
            rel=1e-12,
            abs=1e-12,
        )
        assert pressure < pure_metal_saturation
        assert cap_notices == []
    else:
        assert pressure == pytest.approx(pure_metal_saturation)
        assert type(pressure) is float
        assert len(cap_notices) == 1
        notice = cap_notices[0]
        assert notice["code"] == "elemental_metal_saturation_cap"
        assert notice["species"] == species
        assert notice["uncapped_pressure_Pa"] > notice["capped_pressure_Pa"]
        assert notice["capped_pressure_Pa"] == pressure


@pytest.mark.parametrize(
    ("species", "expected_pressure_pa"),
    (("Cr", 48.022), ("Al", 3.495e-4), ("Ti", 1.685e-6), ("Mn", None)),
    ids=("Cr", "Al", "Ti", "Mn"),
)
def test_liquid_oxide_source_rows_use_catalog_evaluator_or_refuse(
    species,
    expected_pressure_pa,
):
    catalog_payload = ellingham_graph._load_default_vapor_pressure_data()
    evaluator = compiled_catalog_for(catalog_payload).evaluator_for_hot_train(species)
    expected = evaluator.evaluate(
        1873.15,
        source_activity=1.0,
        pO2_bar=1e-12,
    ).pressure_pa
    if expected_pressure_pa is not None:
        assert expected == pytest.approx(expected_pressure_pa, rel=1e-3)
    assert ellingham_graph.effective_equilibrium_pressure_Pa(
        species,
        1873.15,
        1e-12,
        vapor_pressure_data=catalog_payload,
        a_oxide=1.0,
    ) == pytest.approx(expected)

    legacy_view = vapor_pressure_legacy_view(catalog_payload)
    with pytest.raises(
        ellingham_graph.EllinghamPressureRefusal,
        match="requires a catalog melt-source evaluator",
    ):
        ellingham_graph.effective_equilibrium_pressure_Pa(
            species,
            1873.15,
            1e-12,
            vapor_pressure_data=legacy_view,
            a_oxide=1.0,
        )


def test_cr_catalog_source_uses_callers_low_fo2_and_caps_pure_metal():
    catalog_payload = ellingham_graph._load_default_vapor_pressure_data()
    temperature_K = 1423.15
    caller_pO2_bar = 1e-28
    source_activity = 1.0
    evaluator = compiled_catalog_for(catalog_payload).evaluator_for_hot_train(
        "Cr"
    )
    uncapped_pressure = evaluator.evaluate(
        temperature_K,
        source_activity=source_activity,
        pO2_bar=caller_pO2_bar,
    ).pressure_pa
    pure_metal_cap = ellingham_graph.metal_saturation_pressure_Pa(
        "Cr",
        temperature_K,
        vapor_pressure_data=catalog_payload,
    )

    # The Cr2O3(s) Ellingham segment gives ΔG≈−508.0 kJ/mol O2 at 1423.15 K,
    # so K≈2.3e−19 bar and the caller's 1e−28 bar input drives the liquid-Cr
    # source reaction above its independent pure-metal saturation rail.
    assert uncapped_pressure > pure_metal_cap
    cap_notices = []
    pressure = ellingham_graph._effective_equilibrium_pressure_Pa_with_notices(
        "Cr",
        temperature_K,
        caller_pO2_bar,
        vapor_pressure_data=catalog_payload,
        a_oxide=source_activity,
        cap_notices=cap_notices,
    )
    assert pure_metal_cap == pytest.approx(0.10679639619291863)
    assert pressure == pytest.approx(pure_metal_cap)
    assert type(pressure) is float
    assert len(cap_notices) == 1
    assert cap_notices[0]["uncapped_pressure_Pa"] == uncapped_pressure
    assert cap_notices[0]["capped_pressure_Pa"] == pure_metal_cap


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
    with pytest.raises(ellingham_graph.EllinghamPressureRefusal):
        ellingham_graph.evolves(
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


def test_evolution_order_qualitative_pyrolysis_sequence() -> None:
    # Representative mbar-sweep operating point in the Fe/SiO band.
    T_K = 1650.0
    pO2_bar = 1e-6
    catalog_payload = ellingham_graph._load_default_vapor_pressure_data()
    species = ("Na", "K", "Fe", "SiO", "Mg", "Ca", "Al", "Ti")
    ranked = ellingham_graph.evolution_order(
        T_K,
        pO2_bar,
        species,
        vapor_pressure_data=catalog_payload,
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
        vapor_pressure_data=catalog_payload,
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
    # JANAF Na-013 liquid rows 1400/1500 K (-192.041/-167.596 kJ/mol)
    # replace the unconstrained Na liquid fit in b-959. Their intersection
    # with the Fe-020 rail is near 1184 C, consistent with the independent
    # JANAF extraction, rather than the old fit's 1181.5 C.
    assert ellingham_graph.crossover_temperature("Na", "Fe") == pytest.approx(
        1184.0,
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
