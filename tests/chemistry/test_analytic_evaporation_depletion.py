"""Regression tests for sub-tick analytic evaporation depletion."""

from __future__ import annotations

import math

import pytest

import simulator.chemistry.phase_context as phase_context_module
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


@pytest.mark.parametrize(
    ("available_kg", "rate_kg_hr", "production_hex", "battery_hex"),
    (
        (2.0, 2.0, "0x1.f3e58f03a254ep-1", "0x1.f3e58f03a254fp-1"),
        (2e-12, 2e-12, "0x1.12d235385dd85p-40", "0x1.12d235385dd86p-40"),
        (2.0, 1e6, "0x1.34c1e7d008215p+0", "0x1.34c1e7d008216p+0"),
    ),
)
def test_finite_parent_numeric_pin(
    vapor_pressure_data, feedstocks_data, setpoints_data, monkeypatch,
    available_kg, rate_kg_hr, production_hex, battery_hex,
):
    """SC333-16 output pin; intentional policy moves are listed in the report."""
    from simulator.accounting.formulas import parse_formula
    from simulator.battery.residue import ResidueChannel, integrate_residue_inventory

    sim = _build_sim(
        "lunar_mare_low_ti", vapor_pressure_data, feedstocks_data, setpoints_data
    )
    production = sim._analytic_evaporation_depletion_rates(
        {"Mg": rate_kg_hr}, dt_hr=1.0, phase_scalar=1.0,
        cleaned_melt_kg={"MgO": available_kg}, available_o2_kg=0.0,
    )
    monkeypatch.setattr(
        "simulator.battery.residue._finite_step_fluxes",
        lambda *args, **kwargs: {"Mg": rate_kg_hr / 3600.0},
    )
    battery = integrate_residue_inventory(
        {"MgO": available_kg / parse_formula("MgO").molar_mass_kg_per_mol()},
        (ResidueChannel("Mg", "Mg", "MgO", 1.0, "numeric pin"),),
        lambda inventory, logp: {"Mg": 1.0},
        temperature_K=2000.0, duration_s=3600.0,
        area_evolution_m2=(1.0,), buffered_fO2_log=-6.0,
    )
    battery_kg = (
        battery.evaporated_mol["Mg"]
        * parse_formula("Mg").molar_mass_kg_per_mol()
    )
    assert production.get("Mg", 0.0).hex() == production_hex
    assert battery_kg.hex() == battery_hex


def test_finite_parent_limits_keep_all_representable_mass():
    from simulator.chemistry.depletion import _finite_parent_debit

    # The first-order half-life definition supplies an independent anchor.
    assert _finite_parent_debit(2e-12, 2e-12 * math.log(2.0), 1.0) == pytest.approx(
        1e-12, rel=1e-15, abs=0.0
    )
    # Differential limit: for k*dt << 1, debit tends to requested_rate*dt.
    assert _finite_parent_debit(2.0, 2e-20, 1.0) == pytest.approx(
        2e-20, rel=1e-15, abs=0.0
    )
    assert _finite_parent_debit(2e-12, 2e-12, 1e6) == 2e-12
    assert _finite_parent_debit(2e-12, 0.0, 1.0) == 0.0


def test_trace_parent_depletion_matches_battery_across_old_cutoffs(
    vapor_pressure_data, feedstocks_data, setpoints_data, monkeypatch,
):
    from simulator.accounting.formulas import parse_formula
    from simulator.battery.residue import ResidueChannel, integrate_residue_inventory

    sim = _build_sim(
        "lunar_mare_low_ti", vapor_pressure_data, feedstocks_data, setpoints_data
    )
    mg_mass = parse_formula("Mg").molar_mass_kg_per_mol()
    mgo_mass = parse_formula("MgO").molar_mass_kg_per_mol()
    ratio = sim._evaporation_stoich("Mg", _species_data(sim, "Mg"))[
        "oxide_per_product_kg"
    ]
    for available_kg in (2e-12, 1e-12, 5e-13):
        # Product-rate sweep plus the brief's separate parent-demand k=1 case.
        for rate_kg_hr in (2e-12, 1.5e-12, 1e-12, 5e-13, available_kg / ratio):
            production = sim._analytic_evaporation_depletion_rates(
                {"Mg": rate_kg_hr}, dt_hr=1.0, phase_scalar=1.0,
                cleaned_melt_kg={"MgO": available_kg}, available_o2_kg=0.0,
            ).get("Mg", 0.0)
            monkeypatch.setattr(
                "simulator.battery.residue._finite_step_fluxes",
                lambda *args, **kwargs: {"Mg": rate_kg_hr / 3600.0},
            )
            battery = integrate_residue_inventory(
                {"MgO": available_kg / mgo_mass},
                (ResidueChannel("Mg", "Mg", "MgO", 1.0, "matched demand"),),
                lambda inventory, logp: {"Mg": 1.0},
                temperature_K=2000.0, duration_s=3600.0,
                area_evolution_m2=(1.0,), buffered_fO2_log=-6.0,
            )
            assert production > 0.0
            assert production == pytest.approx(
                battery.evaporated_mol["Mg"] * mg_mass, rel=1e-14, abs=0.0
            )
            if rate_kg_hr == available_kg / ratio:
                assert production * ratio / available_kg == pytest.approx(
                    1.0 - 1.0 / math.e, rel=1e-15, abs=0.0
                )


def test_trace_product_commits_coupled_mass_and_zero_product_keeps_parent(
    vapor_pressure_data, feedstocks_data, setpoints_data,
):
    from simulator.accounting.formulas import parse_formula
    from simulator.condensation import CondensationRouteResult

    sim = _build_sim(
        "lunar_mare_low_ti", vapor_pressure_data, feedstocks_data, setpoints_data
    )
    available_kg = 2e-12
    for raw_rate in (0.0, 2e-12, 1.5e-12, 1e-12, 5e-13):
        sim.atom_ledger = sim._new_atom_ledger()
        sim.atom_ledger.load_external_mol(
            "process.cleaned_melt",
            {"MgO": available_kg / parse_formula("MgO").molar_mass_kg_per_mol()},
            source="trace parent", material_origin="feedstock",
        )
        sim._chem_kernel = sim._build_chemistry_kernel()
        requested = _flux({"Mg": raw_rate})
        requested.alpha_authority_status_by_species["Mg"] = "extrapolated"
        depleted = sim._apply_analytic_evaporation_depletion(requested)
        rate = depleted.species_kg_hr.get("Mg", 0.0)
        if rate > 0.0:
            assert depleted.alpha_authority_status_by_species["Mg"] == "extrapolated"
            before = sim.atom_ledger.mol_by_account()
            refused = sim._route_evaporated_species_to_condensation(
                CondensationRouteResult(
                    remaining_by_species={"Mg": rate},
                    condensation_authority_by_species={"Mg": {"status": "refused"}},
                ),
                "Mg", rate, apply_evaporative_redox_source_terms=False,
            )
            assert refused["evaporation_transition"] is None
            assert sim.atom_ledger.mol_by_account() == before
        outcome = sim._route_evaporated_species_to_condensation(
            CondensationRouteResult(remaining_by_species={"Mg": rate}), "Mg", rate,
            apply_evaporative_redox_source_terms=False,
        )
        melt = sim.atom_ledger.kg_by_account("process.cleaned_melt")
        overhead = sim.atom_ledger.kg_by_account("process.overhead_gas")
        oxygen = sim.atom_ledger.kg_by_account("reservoir.fo2_buffer")
        if raw_rate == 0.0:
            assert outcome.get("evaporation_transition") is None
            assert melt["MgO"] == available_kg
            assert overhead == oxygen == {}
        else:
            assert outcome.get("evaporation_transition") is not None
            assert overhead["Mg"] == pytest.approx(rate, rel=1e-14, abs=0.0)
            assert oxygen["O2"] > 0.0
            assert 0.0 < melt["MgO"] < available_kg
        total = sum(melt.values()) + sum(overhead.values()) + sum(oxygen.values())
        assert abs(total - available_kg) / available_kg * 100.0 <= 5e-12


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


def test_three_titanium_carriers_share_one_parent_debit_and_o2_overhead(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    carriers = ("Ti", "TiO", "TiO2_gas")
    stoich = {
        species: sim._evaporation_stoich(species, _species_data(sim, species))
        for species in carriers
    }
    available_tio2_kg = sim.atom_ledger.kg_by_account(
        "process.cleaned_melt"
    )["TiO2"]
    # Give every channel one full-parent-equivalent raw demand. The grouped
    # limiter must see total exposure=3 and allocate one smoothed parent pool,
    # not let each channel independently debit the same inventory.
    raw_rates = {
        species: available_tio2_kg / row["oxide_per_product_kg"]
        for species, row in stoich.items()
    }
    smoothed = sim._apply_analytic_evaporation_depletion(_flux(raw_rates))
    expected_parent_draw_kg = available_tio2_kg * (-math.expm1(-3.0))
    actual_parent_draw_kg = sum(
        smoothed.species_kg_hr[species]
        * stoich[species]["oxide_per_product_kg"]
        for species in carriers
    )
    assert actual_parent_draw_kg == pytest.approx(
        expected_parent_draw_kg, rel=1e-12
    )
    assert actual_parent_draw_kg < available_tio2_kg

    parent_before_mol = sim.atom_ledger.mol_by_account(
        "process.cleaned_melt"
    )["TiO2"]
    overhead_before_kg = sim.atom_ledger.kg_by_account(
        "process.overhead_gas"
    ).get("O2", 0.0)
    buffer_before_kg = sim.atom_ledger.kg_by_account(
        "reservoir.fo2_buffer"
    ).get("O2", 0.0)
    sim._configure_condensation_operating_conditions(smoothed)
    sim._route_to_condensation(smoothed)
    sim._update_melt_composition(smoothed)
    parent_after_mol = sim.atom_ledger.mol_by_account(
        "process.cleaned_melt"
    ).get("TiO2", 0.0)
    overhead_after_kg = sim.atom_ledger.kg_by_account(
        "process.overhead_gas"
    ).get("O2", 0.0)
    buffer_after_kg = sim.atom_ledger.kg_by_account(
        "reservoir.fo2_buffer"
    ).get("O2", 0.0)

    # One Ti per parent formula and one Ti per gas carrier: parent Ti removed
    # must equal the SUM of carrier Ti, with no hidden per-row full debit.
    carrier_ti_mol = sum(
        sim._atom_moles_for_kg(species, smoothed.species_kg_hr[species])["Ti"]
        for species in carriers
    )
    assert parent_before_mol - parent_after_mol == pytest.approx(
        carrier_ti_mol, rel=1e-12
    )
    expected_o2_kg = sum(
        smoothed.species_kg_hr[species]
        * stoich[species]["O2_per_product_kg"]
        for species in carriers
    )
    projected_source = sim._project_evaporation_overhead_source_mol_hr(
        smoothed.species_kg_hr,
        stoich,
    )
    o2_molar_mass = sim.species_formula_registry["O2"].molar_mass_kg_per_mol()
    assert projected_source["O2"] == pytest.approx(
        expected_o2_kg / o2_molar_mass,
        rel=1e-12,
    )
    # All three competing Ti channels credit their different oxygen yields to
    # one overhead reservoir; the fO2 buffer must not hide the elemental branch.
    actual_o2_kg = overhead_after_kg - overhead_before_kg
    assert actual_o2_kg == pytest.approx(
        expected_o2_kg, rel=1e-12
    )
    assert buffer_after_kg == pytest.approx(buffer_before_kg, abs=1e-15)
    transitions = {
        transition.name: transition
        for transition in sim.atom_ledger.transitions
        if transition.name in {
            "evaporate_Ti",
            "evaporate_TiO",
            "evaporate_TiO2_gas",
        }
    }
    assert set(transitions) == {
        "evaporate_Ti",
        "evaporate_TiO",
        "evaporate_TiO2_gas",
    }
    ti_terms = sim._evaporative_redox_source_terms_from_transition(
        transitions["evaporate_Ti"]
    )
    tio_terms = sim._evaporative_redox_source_terms_from_transition(
        transitions["evaporate_TiO"]
    )
    tio2_terms = sim._evaporative_redox_source_terms_from_transition(
        transitions["evaporate_TiO2_gas"]
    )
    assert "redox_source:evaporative_metal_loss" not in ti_terms
    assert ti_terms["redox_source:evaporative_oxygen_loss"] < 0.0
    assert tio_terms["redox_source:evaporative_oxygen_loss"] < 0.0
    assert tio2_terms == {}
    assert abs(sim._make_snapshot().mass_balance_error_pct) <= 5e-12


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


@pytest.mark.parametrize("available_o2_kg", (0.001, 5e-13))
def test_o2_consuming_vapors_share_overhead_o2_reactant(
    vapor_pressure_data, feedstocks_data, setpoints_data, available_o2_kg
):
    from simulator.chemistry.depletion import _finite_parent_debit

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
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
        * (1.0 - 1.0 / math.e**3)
        / stoich["oxide_per_product_kg"]
    )
    parent_only_o2_draw_kg = parent_only_product_kg * abs(
        stoich["O2_per_product_kg"])
    expected_o2_draw_kg = _finite_parent_debit(
        available_o2_kg, parent_only_o2_draw_kg, 1.0,
    )

    assert required_o2_kg <= available_o2_kg
    assert required_o2_kg < parent_only_o2_draw_kg
    assert required_o2_kg == pytest.approx(expected_o2_draw_kg, rel=1e-12, abs=0.0)


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
