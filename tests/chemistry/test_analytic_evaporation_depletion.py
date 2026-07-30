"""Regression tests for sub-tick analytic evaporation depletion."""

from __future__ import annotations

import math

import pytest

import simulator.chemistry.phase_context as phase_context_module
from simulator.account_ids import SPENT_REDUCTANT_RESIDUE_ACCOUNT
from simulator.state import CampaignPhase, EvaporationFlux
from tests.chemistry.conftest import _build_sim


def _species_data(sim, species: str) -> dict:
    return (
        sim.vapor_pressures.get("metals", {}).get(species, {})
        or sim.vapor_pressures.get("oxide_vapors", {}).get(species, {})
    )


def _flux(species_kg_hr: dict[str, float]) -> EvaporationFlux:
    flux = EvaporationFlux(species_kg_hr=dict(species_kg_hr))
    flux.update_totals()
    return flux


def test_parent_grouped_analytic_depletion_is_shared_and_deterministic(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    available_sio2_kg = sim.atom_ledger.kg_by_account(
        "process.cleaned_melt"
    )["SiO2"]
    stoich_si = sim._evaporation_stoich("Si", _species_data(sim, "Si"))
    stoich_sio = sim._evaporation_stoich("SiO", _species_data(sim, "SiO"))
    raw_rates = {
        "Si": 3.0 * available_sio2_kg / stoich_si["oxide_per_product_kg"],
        "SiO": 2.0 * available_sio2_kg / stoich_sio["oxide_per_product_kg"],
    }

    first = sim._apply_analytic_evaporation_depletion(_flux(raw_rates))
    second = sim._apply_analytic_evaporation_depletion(
        _flux({"SiO": raw_rates["SiO"], "Si": raw_rates["Si"]})
    )

    assert first.species_kg_hr == pytest.approx(second.species_kg_hr)
    parent_draw_kg = (
        first.species_kg_hr["Si"] * stoich_si["oxide_per_product_kg"]
        + first.species_kg_hr["SiO"] * stoich_sio["oxide_per_product_kg"]
    )
    expected_parent_draw_kg = available_sio2_kg * (-math.expm1(-5.0))
    assert parent_draw_kg == pytest.approx(expected_parent_draw_kg, rel=1e-12)
    assert parent_draw_kg < available_sio2_kg
    assert (
        first.species_kg_hr["Si"] * stoich_si["oxide_per_product_kg"]
        / parent_draw_kg
    ) == pytest.approx(3.0 / 5.0, rel=1e-12)


def test_depletion_output_ignores_tier_one_phase_context_fields(
    vapor_pressure_data, feedstocks_data, setpoints_data, monkeypatch,
):
    baseline = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    migrated = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    flux = _flux({"Na": 0.01})
    expected = baseline._apply_analytic_evaporation_depletion(flux)

    monkeypatch.setattr(
        phase_context_module,
        "PhaseContext",
        lambda *args, **kwargs: {
            "Na2O": {
                "liquid_fraction": 0.0,
                "activity_basis": "forbidden_tier_one_value",
                "provenance": {"selected_tier": "grind_cache_assemblage"},
            }
        },
    )
    actual = migrated._apply_analytic_evaporation_depletion(_flux({"Na": 0.01}))

    assert actual.species_kg_hr == expected.species_kg_hr
    assert actual.total_kg_hr == expected.total_kg_hr


def test_analytic_depletion_includes_melt_resident_spent_reductant_oxide(
    vapor_pressure_data, feedstocks_data, setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    spent_na2o_kg = 10.0
    sim.atom_ledger.load_external(
        "process.raw_feedstock",
        {"Na2O": spent_na2o_kg},
        source="spent reductant depletion regression seed",
        material_origin="reagent",
    )
    sim.atom_ledger.move(
        "spent_reductant_depletion_regression_seed",
        "process.raw_feedstock",
        SPENT_REDUCTANT_RESIDUE_ACCOUNT,
        {"Na2O": spent_na2o_kg},
        reason="spent reductant depletion regression seed",
    )
    cleaned_na2o_kg = sim.atom_ledger.kg_by_account(
        "process.cleaned_melt"
    )["Na2O"]
    total_na2o_kg = cleaned_na2o_kg + spent_na2o_kg
    stoich = sim._evaporation_stoich("Na", _species_data(sim, "Na"))
    raw_rate = total_na2o_kg / stoich["oxide_per_product_kg"]

    smoothed = sim._apply_analytic_evaporation_depletion(
        _flux({"Na": raw_rate})
    )

    expected_parent_draw_kg = total_na2o_kg * (-math.expm1(-1.0))
    actual_parent_draw_kg = (
        smoothed.species_kg_hr["Na"] * stoich["oxide_per_product_kg"]
    )
    cleaned_only_draw_kg = cleaned_na2o_kg * (
        -math.expm1(-(total_na2o_kg / cleaned_na2o_kg))
    )
    assert actual_parent_draw_kg == pytest.approx(
        expected_parent_draw_kg,
        rel=1e-12,
    )
    assert actual_parent_draw_kg > cleaned_only_draw_kg


def test_condensation_route_depletes_spent_only_parent_oxide_pool(
    vapor_pressure_data, feedstocks_data, setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    cleaned_na2o_kg = sim.atom_ledger.kg_by_account(
        "process.cleaned_melt"
    ).get("Na2O", 0.0)
    if cleaned_na2o_kg > 1e-12:
        sim.atom_ledger.move(
            "spent_only_route_remove_native_na2o",
            "process.cleaned_melt",
            "terminal.slag",
            {"Na2O": cleaned_na2o_kg},
            reason="spent-only evaporation route regression setup",
        )
    sim.atom_ledger.load_external(
        SPENT_REDUCTANT_RESIDUE_ACCOUNT,
        {"Na2O": 10.0},
        source="spent-only evaporation route regression seed",
        material_origin="reagent",
    )
    sim._project_cleaned_melt_from_atom_ledger()
    smoothed = sim._apply_analytic_evaporation_depletion(
        _flux({"Na": 1.0})
    )
    before_kg = sim.atom_ledger.kg_by_account(
        SPENT_REDUCTANT_RESIDUE_ACCOUNT
    )["Na2O"]
    transition_count_before = len(sim.atom_ledger.transitions)

    sim._configure_condensation_operating_conditions(smoothed)
    sim._route_to_condensation(smoothed)

    after_kg = sim.atom_ledger.kg_by_account(
        SPENT_REDUCTANT_RESIDUE_ACCOUNT
    ).get("Na2O", 0.0)
    assert after_kg < before_kg
    assert any(
        any(
            lot.account == SPENT_REDUCTANT_RESIDUE_ACCOUNT
            and lot.species_kg.get("Na2O", 0.0) > 0.0
            for lot in transition.debits
        )
        for transition in sim.atom_ledger.transitions[
            transition_count_before:
        ]
    )


def test_o2_consuming_vapors_share_overhead_o2_reactant(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    available_o2_kg = 0.001
    sim.atom_ledger.load_external(
        "process.overhead_gas", {"O2": available_o2_kg},
        source="test O2 reactant",
        material_origin="feedstock",
    )
    stoich = sim._evaporation_stoich("CrO2", _species_data(sim, "CrO2"))
    available_cr2o3_kg = sim.atom_ledger.kg_by_account(
        "process.cleaned_melt"
    )["Cr2O3"]
    raw_rate = 3.0 * available_cr2o3_kg / stoich["oxide_per_product_kg"]

    parent_smoothed = sim._apply_analytic_evaporation_depletion(
        _flux({"CrO2": raw_rate})
    )
    required_o2_kg = parent_smoothed.species_kg_hr["CrO2"] * abs(
        stoich["O2_per_product_kg"])
    parent_only_product_kg = (
        available_cr2o3_kg
        * (-math.expm1(-3.0))
        / stoich["oxide_per_product_kg"]
    )
    parent_only_o2_draw_kg = parent_only_product_kg * abs(
        stoich["O2_per_product_kg"])
    expected_fraction = max(
        0.0,
        min(
            math.nextafter(1.0, 0.0),
            -math.expm1(-(parent_only_o2_draw_kg / available_o2_kg)),
        ),
    )
    expected_o2_draw_kg = available_o2_kg * expected_fraction

    assert required_o2_kg <= available_o2_kg
    assert required_o2_kg < parent_only_o2_draw_kg
    assert required_o2_kg == pytest.approx(expected_o2_draw_kg, rel=1e-12)


def test_representative_c2a_parent_pool_depletes_as_tail_not_dump(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.start_campaign(CampaignPhase.C2A)
    sim.melt.temperature_C = 1400.0
    sp_data = _species_data(sim, "SiO")
    stoich = sim._evaporation_stoich("SiO", sp_data)
    available_sio2_kg = sim.atom_ledger.kg_by_account(
        "process.cleaned_melt"
    )["SiO2"]
    raw_rate = 1.5 * available_sio2_kg / stoich["oxide_per_product_kg"]
    effective_rates = []
    mass_balance = []

    for _ in range(2):
        smoothed = sim._apply_analytic_evaporation_depletion(
            _flux({"SiO": raw_rate})
        )
        effective_rates.append(smoothed.species_kg_hr.get("SiO", 0.0))
        sim._configure_condensation_operating_conditions(smoothed)
        sim._route_to_condensation(smoothed)
        sim._update_melt_composition(smoothed)
        mass_balance.append(abs(sim._make_snapshot().mass_balance_error_pct))

    assert effective_rates[0] > effective_rates[1] > 1e-12
    assert effective_rates[0] / effective_rates[1] < 10.0
    assert max(mass_balance) <= 5e-12
