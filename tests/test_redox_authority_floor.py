"""Melt fO2 authority when the ferric ratio is only the Kress floor."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
import yaml

from simulator.core import OXYGEN_RESERVOIR_NOOP_MOL, PyrolysisSimulator
from simulator.accounting.formulas import resolve_species_formula
from simulator.state import MOLAR_MASS
from simulator.fe_redox import (
    KRESS91_FERRIC_FRACTION_EPSILON,
    KRESS91_LN_FO2_COEFFICIENT,
    _kress91_ln_ratio,
    calphad_ferrous_feo_activity_diagnostic,
    feo_iw_log10_fO2_bar,
    floor_vacuum_pressure_bar,
    kress91_fe3_over_sigma_fe,
    kress91_log_fO2_from_fe3_over_sigma_fe,
    melt_mol_fractions_for_kress91,
)
from simulator.melt_backend.base import InternalAnalyticalBackend


ROOT = Path(__file__).resolve().parents[1]
TRANSPORT_PO2_BAR = 1.0e-8


def _load_yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / "data" / name).read_text())


def _sim_with_oxides(
    *,
    feo_wt: float,
    fe2o3_wt: float,
    temperature_C: float,
    mass_kg: float = 1.0,
) -> PyrolysisSimulator:
    setpoints = _load_yaml("setpoints.yaml")
    setpoints.setdefault("chemistry_kernel", {})["allow_fallback_vapor"] = True
    setpoints["chemistry_kernel"]["allow_unmeasured_alpha_fallback"] = True
    silica_wt = 100.0 - feo_wt - fe2o3_wt
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        setpoints,
        {
            "redox_authority_case": {
                "label": "redox authority case",
                "composition_wt_pct": {
                    "SiO2": silica_wt,
                    "FeO": feo_wt,
                    "Fe2O3": fe2o3_wt,
                },
            }
        },
        _load_yaml("vapor_pressures.yaml"),
    )
    sim.load_batch("redox_authority_case", mass_kg=mass_kg)
    sim.melt.temperature_C = temperature_C
    sim.melt.p_total_mbar = 10.0
    sim._melt_redox_ledger_initialized = True
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = TRANSPORT_PO2_BAR
    return sim


def _oxide_mol(sim: PyrolysisSimulator, species: str) -> float:
    melt = sim.atom_ledger.project_account_mol("process.cleaned_melt")
    return max(0.0, float(melt.get(species, 0.0) or 0.0))


def _pressure_bar(sim: PyrolysisSimulator) -> float:
    return floor_vacuum_pressure_bar(
        float(sim.melt.p_total_mbar) / 1000.0,
        floor_bar=sim._vacuum_floor_bar(),
    )


def _assert_saturation_bound(
    sim: PyrolysisSimulator,
    *,
    temperature_C: float,
    fO2_log: float,
) -> None:
    domain = sim._last_redox_domain
    temperature_K = temperature_C + 273.15
    pressure_bar = _pressure_bar(sim)
    composition = melt_mol_fractions_for_kress91(sim._melt_oxide_wt_pct())
    floor_inverse = kress91_log_fO2_from_fe3_over_sigma_fe(
        fe3_over_sigma_fe=KRESS91_FERRIC_FRACTION_EPSILON,
        mol_fractions=composition,
        T_K=temperature_K,
        pressure_bar=pressure_bar,
    )
    activity = calphad_ferrous_feo_activity_diagnostic(
        comp_wt=sim._melt_oxide_wt_pct(),
        fO2_log=fO2_log,
        T_K=temperature_K,
        pressure_bar=pressure_bar,
    )
    a_feo = float(activity["a_FeO_authoritative"])
    iw = feo_iw_log10_fO2_bar(temperature_K, a_feo=1.0)
    expected = iw + 2.0 * math.log10(a_feo) - 2.0 * math.log10(1.0)
    extent = sim._compute_native_fe_saturation_extent(
        fO2_log=fO2_log,
        T_K=temperature_K,
        pressure_bar=pressure_bar,
    )

    assert domain["basis"] == "fe_saturation_bound"
    assert domain["status"] == "out_of_domain"
    assert domain["authority"] == "extrapolated"
    assert "kress91_inverse_not_evaluated" in domain["reason"]
    assert "ferric_inventory_absent" in domain["reason"]
    assert fO2_log == pytest.approx(expected, abs=1.0e-6)
    assert fO2_log != pytest.approx(math.log10(TRANSPORT_PO2_BAR))
    assert abs(fO2_log - floor_inverse) > 1.0
    assert extent["native_fe_frac"] == 0.0
    assert extent["native_fe_mol"] == 0.0


def test_absent_fe3_sits_on_the_fe_saturation_bound() -> None:
    """R1: FeO remains, Fe3+ is absent, native metal has left."""

    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1220.0)
    assert _oxide_mol(sim, "FeO") > 1.0e-6
    assert _oxide_mol(sim, "Fe2O3") == 0.0
    assert sim.atom_ledger.project_account_mol("process.metal_phase").get(
        "Fe", 0.0
    ) == 0.0

    fO2_log = sim._melt_fO2_from_ledger()
    _assert_saturation_bound(sim, temperature_C=1220.0, fO2_log=fO2_log)


def test_depleted_feo_without_fe3_stays_on_the_saturation_bound() -> None:
    """R2: trace FeO and no Fe3+ is still the saturation bound, not Kress."""

    sim = _sim_with_oxides(feo_wt=1.0e-4, fe2o3_wt=0.0, temperature_C=1400.0)
    assert _oxide_mol(sim, "FeO") > 0.0
    assert _oxide_mol(sim, "Fe2O3") == 0.0

    fO2_log = sim._melt_fO2_from_ledger()
    _assert_saturation_bound(sim, temperature_C=1400.0, fO2_log=fO2_log)


def test_zero_transfer_below_the_bound_does_not_follow_the_gas() -> None:
    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1220.0)
    bound = sim._melt_fO2_from_ledger()
    assert sim._last_redox_domain["basis"] == "fe_saturation_bound"
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = 10.0 ** (
        bound - 1.0
    )
    sim.melt.oxygen_reservoir.exchange_o2_mol = 0.0

    fO2_log = sim._melt_fO2_from_ledger()

    assert sim._last_redox_domain["basis"] == "fe_saturation_bound"
    assert fO2_log == pytest.approx(bound, abs=1.0e-8)


def test_nonzero_release_with_no_ferric_inventory_follows_the_gas() -> None:
    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1220.0)
    bound = sim._melt_fO2_from_ledger()
    transport_pO2_bar = 10.0 ** (bound - 1.0)
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = transport_pO2_bar
    sim.melt.oxygen_reservoir.exchange_o2_mol = -1.0e-6

    fO2_log = sim._melt_fO2_from_ledger()

    assert sim._last_redox_domain["basis"] == "no_melt_redox_buffer"
    assert "kress91_inverse_not_evaluated" in sim._last_redox_domain["reason"]
    assert fO2_log == pytest.approx(math.log10(transport_pO2_bar))


def test_interior_ferric_ratio_still_inverts_kress() -> None:
    sim = _sim_with_oxides(feo_wt=8.0, fe2o3_wt=2.0, temperature_C=1400.0)
    feo_mol = _oxide_mol(sim, "FeO")
    fe2o3_mol = _oxide_mol(sim, "Fe2O3")
    total_fe = feo_mol + 2.0 * fe2o3_mol
    raw_q = (2.0 * fe2o3_mol) / total_fe
    assert KRESS91_FERRIC_FRACTION_EPSILON < raw_q < (
        1.0 - KRESS91_FERRIC_FRACTION_EPSILON
    )

    fO2_log = sim._melt_fO2_from_ledger()
    composition = melt_mol_fractions_for_kress91(sim._melt_oxide_wt_pct())
    expected = kress91_log_fO2_from_fe3_over_sigma_fe(
        fe3_over_sigma_fe=raw_q,
        mol_fractions=composition,
        T_K=1400.0 + 273.15,
        pressure_bar=_pressure_bar(sim),
    )

    assert sim._last_redox_domain["basis"] == "kress91_inverse"
    assert fO2_log == pytest.approx(expected, abs=1.0e-8)


def test_reducing_respeciation_does_not_mint_the_ferric_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1220.0)
    monkeypatch.setattr(
        sim,
        "_melt_redox_exchange_is_liquid",
        lambda *_args, **_kwargs: True,
    )
    composition = melt_mol_fractions_for_kress91(sim._melt_oxide_wt_pct())
    unclamped = kress91_fe3_over_sigma_fe(
        fO2_log=-40.0,
        mol_fractions=composition,
        T_K=1220.0 + 273.15,
        pressure_bar=_pressure_bar(sim),
    )
    assert unclamped < KRESS91_FERRIC_FRACTION_EPSILON
    before = sim.atom_ledger.mol_by_account()

    diagnostic = sim._apply_fe_redox_respeciation(fO2_log_override=-40.0)

    assert diagnostic["respeciation_status"] == "endpoint_not_a_measurement"
    assert _oxide_mol(sim, "Fe2O3") == 0.0
    assert sim.atom_ledger.mol_by_account() == before


def _feo_wt_pct_for_mol(n_feo_mol: float, mass_kg: float) -> float:
    feo_kg = n_feo_mol * MOLAR_MASS["FeO"] / 1000.0
    return 100.0 * feo_kg / mass_kg


def _set_melt_iron_oxides(
    sim: PyrolysisSimulator,
    *,
    n_feo_mol: float,
    n_fe2o3_mol: float,
) -> None:
    """Place an exact FeO/Fe2O3 inventory on the cleaned melt."""

    balances = sim.atom_ledger._balances["process.cleaned_melt"]
    if n_feo_mol > 0.0:
        balances["FeO"] = n_feo_mol
    else:
        balances.pop("FeO", None)
    if n_fe2o3_mol > 0.0:
        balances["Fe2O3"] = n_fe2o3_mol
    else:
        balances.pop("Fe2O3", None)


def _ferrous_free_lower_bound_log10(
    sim: PyrolysisSimulator,
    *,
    n_fe2o3_mol: float,
    temperature_C: float,
) -> float:
    """Edge of log10(fO2/bar) > [ln(r_floor) - b] / (0.196 ln 10)."""

    composition = melt_mol_fractions_for_kress91(
        sim._cleaned_melt_ledger_wt_pct()
    )
    b_term = _kress91_ln_ratio(
        mol_fractions=composition,
        T_K=temperature_C + 273.15,
        pressure_bar=_pressure_bar(sim),
    )
    r_floor = n_fe2o3_mol / OXYGEN_RESERVOIR_NOOP_MOL
    return (math.log(r_floor) - b_term) / (
        KRESS91_LN_FO2_COEFFICIENT * math.log(10.0)
    )


def _set_melt_feo_mol(sim: PyrolysisSimulator, n_feo_mol: float) -> None:
    """Place an exact FeO inventory on the cleaned melt.

    1e-13 mol FeO is 7.18e-15 kg, below the ledger's 1e-12 kg load
    tolerance, so a feedstock weight percent cannot retain it.  The
    classifier reads the stored mol.
    """

    balances = sim.atom_ledger._balances["process.cleaned_melt"]
    balances["FeO"] = n_feo_mol
    balances.pop("Fe2O3", None)


def test_astra_feo_inventory_keeps_its_own_authority() -> None:
    """2358.22 mol FeO cannot be exhausted by a 0.2 mol O2 uptake.

    capacity_O2 = n_FeO / 4 = 589.555 mol, which is far above 0.2 mol.
    """

    n_feo = 2358.22
    uptake = 0.2
    mass_kg = 250.0
    sim = _sim_with_oxides(
        feo_wt=_feo_wt_pct_for_mol(n_feo, mass_kg),
        fe2o3_wt=0.0,
        temperature_C=1220.0,
        mass_kg=mass_kg,
    )
    feo_mol = _oxide_mol(sim, "FeO")
    capacity_o2 = feo_mol / 4.0
    assert feo_mol == pytest.approx(n_feo, rel=1.0e-9)
    assert capacity_o2 == pytest.approx(589.555, rel=1.0e-9)
    assert capacity_o2 > uptake
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = 1.0
    sim.melt.oxygen_reservoir.exchange_o2_mol = -uptake

    fO2_log = sim._melt_fO2_from_ledger()

    assert sim._last_redox_domain["basis"] == "fe_saturation_bound"
    assert f"capacity_O2={capacity_o2:.17g}" in sim._last_redox_domain["reason"]
    assert f"per_tick_o2_transfer_mol={-uptake:.17g}" in (
        sim._last_redox_domain["reason"]
    )
    assert fO2_log != pytest.approx(math.log10(1.0))


def test_trace_feo_finite_uptake_hands_the_melt_to_the_gas() -> None:
    """1e-13 mol FeO has capacity_O2 = 2.5e-14 mol.

    A finite uptake above that capacity, and above the 1e-15 mol noop,
    is more O2 than the inventory can take.  The melt follows the gas.
    """

    n_feo = 1.0e-13
    uptake = 1.0e-6
    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1220.0)
    _set_melt_feo_mol(sim, n_feo)
    feo_mol = _oxide_mol(sim, "FeO")
    capacity_o2 = feo_mol / 4.0
    assert feo_mol == pytest.approx(n_feo, rel=0.0, abs=1.0e-18)
    assert capacity_o2 == pytest.approx(n_feo / 4.0, rel=0.0, abs=1.0e-18)
    assert capacity_o2 > 1.0e-15
    assert capacity_o2 <= uptake
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = 1.0
    sim.melt.oxygen_reservoir.exchange_o2_mol = -uptake

    fO2_log = sim._melt_fO2_from_ledger()

    assert sim._last_redox_domain["basis"] == "no_melt_redox_buffer"
    assert "kress91_inverse_not_evaluated" in sim._last_redox_domain["reason"]
    assert f"capacity_O2={capacity_o2:.17g}" in sim._last_redox_domain["reason"]
    assert fO2_log == pytest.approx(math.log10(1.0))


def test_zero_transfer_on_trace_feo_never_follows_the_gas() -> None:
    """A zero interface transfer does not hand the melt to the gas."""

    n_feo = 1.0e-13
    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1220.0)
    _set_melt_feo_mol(sim, n_feo)
    feo_mol = _oxide_mol(sim, "FeO")
    capacity_o2 = feo_mol / 4.0
    assert feo_mol == pytest.approx(n_feo, rel=0.0, abs=1.0e-18)
    assert capacity_o2 == pytest.approx(2.5e-14, rel=0.0, abs=1.0e-18)
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = 1.0
    sim.melt.oxygen_reservoir.exchange_o2_mol = 0.0

    fO2_log = sim._melt_fO2_from_ledger()

    assert sim._last_redox_domain["basis"] == "fe_saturation_bound"
    assert f"capacity_O2={capacity_o2:.17g}" in sim._last_redox_domain["reason"]
    assert "per_tick_o2_transfer_mol=0" in sim._last_redox_domain["reason"]
    assert fO2_log != pytest.approx(math.log10(1.0))


def test_iron_free_melt_zero_transfer_is_flagged_not_aborted() -> None:
    """No FeO and no Fe2O3: directional capacity is 0, and a zero transfer
    predicts the interface pressure instead of aborting.
    """

    sim = _sim_with_oxides(feo_wt=0.0, fe2o3_wt=0.0, temperature_C=1600.0)
    assert _oxide_mol(sim, "FeO") == 0.0
    assert _oxide_mol(sim, "Fe2O3") == 0.0

    fO2_log = sim._melt_fO2_from_ledger()
    domain = sim._last_redox_domain

    assert domain["basis"] == "no_modelled_redox_couple"
    assert domain["status"] == "out_of_domain"
    assert domain["authority"] == "extrapolated"
    assert "both_iron_oxides_absent" in domain["reason"]
    assert fO2_log == pytest.approx(math.log10(TRANSPORT_PO2_BAR))


def test_iron_free_melt_nonzero_transfer_follows_the_gas() -> None:
    """Capacity is 0 in both directions, so a non-zero transfer hands the
    melt to the gas. Uptake is n_FeO/4 and release is n_Fe2O3/2.
    """

    sim = _sim_with_oxides(feo_wt=0.0, fe2o3_wt=0.0, temperature_C=1600.0)
    sim._redox_handover_transfer_override = 1.0e-6

    fO2_log = sim._melt_fO2_from_ledger()
    domain = sim._last_redox_domain

    assert domain["basis"] == "no_melt_redox_buffer"
    assert domain["status"] == "out_of_domain"
    assert "both_iron_oxides_absent" in domain["reason"]
    assert fO2_log == pytest.approx(math.log10(TRANSPORT_PO2_BAR))


def test_ledger_feo_bounds_when_the_inventory_projection_is_empty() -> None:
    """The Fe authority is the ledger. An empty inventory wt% projection
    must not abort the saturation bound.
    """

    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1600.0)
    sim.inventory.melt_oxide_kg.clear()
    assert sim._melt_oxide_wt_pct() == {}
    assert _oxide_mol(sim, "FeO") > 0.0

    fO2_log = sim._melt_fO2_from_ledger()

    assert sim._last_redox_domain["basis"] == "fe_saturation_bound"
    ledger_wt = sim._cleaned_melt_ledger_wt_pct()
    temperature_K = 1600.0 + 273.15
    activity = calphad_ferrous_feo_activity_diagnostic(
        comp_wt=ledger_wt,
        fO2_log=fO2_log,
        T_K=temperature_K,
        pressure_bar=_pressure_bar(sim),
    )
    a_feo = float(activity["a_FeO_authoritative"])
    expected = feo_iw_log10_fO2_bar(temperature_K, a_feo=1.0) + 2.0 * math.log10(a_feo)
    assert a_feo > 0.0
    assert fO2_log == pytest.approx(expected, abs=1.0e-6)


def test_evaporative_coproduct_oxygen_oxidises_remaining_feo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Congruent FeO evaporation oxidises Fe2+ only up to Kress at the
    interface fO2, not up to the saturation-bound ratio.
    """

    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1600.0)
    monkeypatch.setattr(
        sim,
        "_melt_redox_exchange_is_liquid",
        lambda *_args, **_kwargs: True,
    )
    sim.melt.oxygen_reservoir.interface_pO2_bar = TRANSPORT_PO2_BAR
    assert sim._melt_fO2_from_ledger() < math.log10(TRANSPORT_PO2_BAR)
    assert sim._last_redox_domain["basis"] == "fe_saturation_bound"
    composition = melt_mol_fractions_for_kress91(sim._cleaned_melt_ledger_wt_pct())
    target_q = kress91_fe3_over_sigma_fe(
        fO2_log=math.log10(TRANSPORT_PO2_BAR),
        mol_fractions=composition,
        T_K=1600.0 + 273.15,
        pressure_bar=_pressure_bar(sim),
    )
    assert KRESS91_FERRIC_FRACTION_EPSILON < target_q < (
        1.0 - KRESS91_FERRIC_FRACTION_EPSILON
    )
    feo_before = _oxide_mol(sim, "FeO")
    oxygen_mol = 5.0
    sim.atom_ledger.load_external_mol(
        "reservoir.fo2_buffer",
        {"O2": oxygen_mol},
        source="test evaporative oxygen coproduct",
        material_origin="feedstock",
    )
    monkeypatch.setattr(
        sim,
        "_headspace_evaporative_o2_source_mol",
        lambda _index: (oxygen_mol, 0.0),
    )

    diagnostic = sim._apply_post_evaporation_fe_respeciation(
        transition_start_index=0,
    )

    fe2o3 = _oxide_mol(sim, "Fe2O3")
    expected_fe2o3 = min(0.5 * target_q * feo_before, 2.0 * oxygen_mol)
    assert diagnostic["oxygen_source"] == "evaporative_metal_loss_internal"
    assert fe2o3 == pytest.approx(expected_fe2o3, rel=1.0e-6)
    assert fe2o3 > 0.0
    buffer_o2 = float(
        sim.atom_ledger.mol_by_account("reservoir.fo2_buffer").get("O2", 0.0)
        or 0.0
    )
    assert buffer_o2 == pytest.approx(oxygen_mol - 0.5 * fe2o3, rel=1.0e-6)


def test_unconstrained_respeciation_does_not_mint_from_the_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1220.0)
    monkeypatch.setattr(
        sim,
        "_melt_redox_exchange_is_liquid",
        lambda *_args, **_kwargs: True,
    )
    before = sim.atom_ledger.mol_by_account()

    diagnostic = sim._apply_fe_redox_respeciation()

    assert diagnostic["respeciation_status"] == "skipped_fe_saturation_bound"
    assert _oxide_mol(sim, "Fe2O3") == 0.0
    assert sim.atom_ledger.mol_by_account() == before


def _ferric_state(sim: PyrolysisSimulator) -> tuple[float, float, float, float]:
    feo = _oxide_mol(sim, "FeO")
    fe2o3 = _oxide_mol(sim, "Fe2O3")
    total = feo + 2.0 * fe2o3
    fraction = 0.0 if total <= 0.0 else (2.0 * fe2o3) / total
    return fraction, feo, fe2o3, total


def _oxygen_atom_mol(sim: PyrolysisSimulator) -> float:
    total = 0.0
    for account, species_mol in sim.atom_ledger.mol_by_account().items():
        del account
        for species, mol in species_mol.items():
            formula = resolve_species_formula(species, sim.species_formula_registry)
            total += float(mol) * float(formula.elements.get("O", 0.0))
    return total


def _flush_evaporative_credit(sim: PyrolysisSimulator, credit_mol: float) -> None:
    from simulator.state import EvaporationFlux

    sim._headspace_evaporative_o2_source_mol = lambda _index: (credit_mol, 0.0)
    flux = EvaporationFlux(species_kg_hr={})
    flux.update_totals()
    sim._set_headspace_transport_source_rates(flux, transition_start_index=0)
    sim._flush_evaporative_o2_buffer_to_headspace()


def test_trace_feo_evaporative_oxygen_stops_at_interface_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1e-13 mol FeO plus a large O2 credit ends at the interface target.

    The starting ferric fraction is the Kress endpoint, the measured
    trace-iron state. An uncapped path treats that endpoint as not a
    measurement and leaves the fraction there. Congruent evaporation
    must bring it back to the interface target.
    """

    from simulator.state import MOLAR_MASS

    feo_mol = 1.0e-13
    fe2o3_mol = 5.0e-8
    feo_wt = feo_mol * float(MOLAR_MASS["FeO"]) / 10.0
    fe2o3_wt = fe2o3_mol * float(MOLAR_MASS["Fe2O3"]) / 10.0
    sim = _sim_with_oxides(
        feo_wt=feo_wt,
        fe2o3_wt=fe2o3_wt,
        temperature_C=1600.0,
    )
    monkeypatch.setattr(
        sim,
        "_melt_redox_exchange_is_liquid",
        lambda *_args, **_kwargs: True,
    )
    sim.melt.oxygen_reservoir.interface_pO2_bar = TRANSPORT_PO2_BAR
    fraction, feo, fe2o3_before, _total = _ferric_state(sim)
    assert feo == pytest.approx(feo_mol, rel=1.0e-6)
    assert fraction >= 1.0 - KRESS91_FERRIC_FRACTION_EPSILON
    composition = melt_mol_fractions_for_kress91(sim._cleaned_melt_ledger_wt_pct())
    target_q = kress91_fe3_over_sigma_fe(
        fO2_log=math.log10(TRANSPORT_PO2_BAR),
        mol_fractions=composition,
        T_K=1600.0 + 273.15,
        pressure_bar=_pressure_bar(sim),
    )
    assert KRESS91_FERRIC_FRACTION_EPSILON < target_q < 0.5
    oxygen_mol = 2.0
    sim.atom_ledger.load_external_mol(
        "reservoir.fo2_buffer",
        {"O2": oxygen_mol},
        source="test evaporative oxygen coproduct",
        material_origin="feedstock",
    )
    oxygen_before = _oxygen_atom_mol(sim)
    monkeypatch.setattr(
        sim,
        "_headspace_evaporative_o2_source_mol",
        lambda _index: (oxygen_mol, 0.0),
    )

    sim._apply_post_evaporation_fe_respeciation(transition_start_index=0)
    _flush_evaporative_credit(sim, oxygen_mol)

    fraction_after, _feo_after, fe2o3_after, total_after = _ferric_state(sim)
    headspace_o2 = float(
        sim.atom_ledger.mol_by_account("process.overhead_gas").get("O2", 0.0)
        or 0.0
    )
    buffer_o2 = float(
        sim.atom_ledger.mol_by_account("reservoir.fo2_buffer").get("O2", 0.0)
        or 0.0
    )
    assert fraction_after == pytest.approx(target_q, rel=1.0e-6)
    assert fraction_after < 0.5
    assert fe2o3_after == pytest.approx(0.5 * target_q * total_after, rel=1.0e-6)
    released_o2 = 0.5 * (fe2o3_before - fe2o3_after)
    assert headspace_o2 == pytest.approx(oxygen_mol + released_o2, abs=1.0e-12)
    assert buffer_o2 == pytest.approx(0.0, abs=1.0e-12)
    assert _oxygen_atom_mol(sim) == pytest.approx(oxygen_before, abs=1.0e-12)


def test_melt_already_at_interface_target_flushes_all_evaporative_o2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A melt already at the interface target does not respeciate.

    Coexisting native Fe would otherwise pull the ratio to the Fe-FeO
    buffer. Evaporative oxygen does not apply that second mutation.
    """

    sim = _sim_with_oxides(feo_wt=8.0, fe2o3_wt=2.0, temperature_C=1600.0)
    monkeypatch.setattr(
        sim,
        "_melt_redox_exchange_is_liquid",
        lambda *_args, **_kwargs: True,
    )
    sim.melt.oxygen_reservoir.interface_pO2_bar = TRANSPORT_PO2_BAR
    from simulator.accounting import LedgerTransition, MaterialLot
    from simulator.accounting.formulas import resolve_species_formula

    feo_kg = resolve_species_formula("FeO", {}).molar_mass_kg_per_mol()
    fe2o3_kg = resolve_species_formula("Fe2O3", {}).molar_mass_kg_per_mol()
    o2_kg = resolve_species_formula("O2", {}).molar_mass_kg_per_mol()
    for _ in range(4):
        composition = melt_mol_fractions_for_kress91(
            sim._cleaned_melt_ledger_wt_pct()
        )
        target_q = kress91_fe3_over_sigma_fe(
            fO2_log=math.log10(TRANSPORT_PO2_BAR),
            mol_fractions=composition,
            T_K=1600.0 + 273.15,
            pressure_bar=_pressure_bar(sim),
        )
        _fraction, feo, fe2o3, total = _ferric_state(sim)
        target_fe2o3 = 0.5 * target_q * total
        delta = target_fe2o3 - fe2o3
        if abs(delta) <= 1.0e-12:
            break
        if delta > 0.0:
            sim.atom_ledger.apply(
                LedgerTransition(
                    name="test_set_interface_target",
                    debits=(
                        MaterialLot("process.cleaned_melt", {"FeO": 2.0 * delta * feo_kg}),
                        MaterialLot("reservoir.fo2_buffer", {"O2": 0.5 * delta * o2_kg}),
                    ),
                    credits=(
                        MaterialLot(
                            "process.cleaned_melt",
                            {"Fe2O3": delta * fe2o3_kg},
                        ),
                    ),
                )
            )
        else:
            released = -delta
            sim.atom_ledger.apply(
                LedgerTransition(
                    name="test_set_interface_target",
                    debits=(
                        MaterialLot(
                            "process.cleaned_melt",
                            {"Fe2O3": released * fe2o3_kg},
                        ),
                    ),
                    credits=(
                        MaterialLot("process.cleaned_melt", {"FeO": 2.0 * released * feo_kg}),
                        MaterialLot("reservoir.fo2_buffer", {"O2": 0.5 * released * o2_kg}),
                    ),
                )
            )
    else:
        raise AssertionError("interface target did not converge")
    composition = melt_mol_fractions_for_kress91(sim._cleaned_melt_ledger_wt_pct())
    target_q = kress91_fe3_over_sigma_fe(
        fO2_log=math.log10(TRANSPORT_PO2_BAR),
        mol_fractions=composition,
        T_K=1600.0 + 273.15,
        pressure_bar=_pressure_bar(sim),
    )
    fraction, _feo, fe2o3_before, _total = _ferric_state(sim)
    assert fraction == pytest.approx(target_q, abs=1.0e-8)
    sim.atom_ledger.load_external_mol(
        "process.metal_phase",
        {"Fe": 0.05},
        source="test native Fe coexisting with the melt",
        material_origin="feedstock",
    )
    # The ratio adjustment can credit O2. That oxygen is not this tick's
    # evaporative credit, so the flush must leave it in the buffer.
    leftover = float(
        sim.atom_ledger.mol_by_account("reservoir.fo2_buffer").get("O2", 0.0) or 0.0
    )
    oxygen_mol = 3.0
    sim.atom_ledger.load_external_mol(
        "reservoir.fo2_buffer",
        {"O2": oxygen_mol},
        source="test evaporative oxygen coproduct",
        material_origin="feedstock",
    )
    oxygen_before = _oxygen_atom_mol(sim)
    monkeypatch.setattr(
        sim,
        "_headspace_evaporative_o2_source_mol",
        lambda _index: (oxygen_mol, 0.0),
    )

    diagnostic = sim._apply_post_evaporation_fe_respeciation(transition_start_index=0)
    _flush_evaporative_credit(sim, oxygen_mol)

    _fraction_after, _feo_after, fe2o3_after, _total_after = _ferric_state(sim)
    headspace_o2 = float(
        sim.atom_ledger.mol_by_account("process.overhead_gas").get("O2", 0.0) or 0.0
    )
    buffer_o2 = float(
        sim.atom_ledger.mol_by_account("reservoir.fo2_buffer").get("O2", 0.0) or 0.0
    )
    assert diagnostic.get("direction") in {None, "none"}
    assert fe2o3_after == pytest.approx(fe2o3_before, abs=1.0e-12)
    assert headspace_o2 == pytest.approx(oxygen_mol, rel=1.0e-12)
    assert buffer_o2 == pytest.approx(leftover, abs=1.0e-12)
    assert _oxygen_atom_mol(sim) == pytest.approx(oxygen_before, abs=1.0e-12)


def _fully_ferric_sim() -> PyrolysisSimulator:
    """2 mol Fe2O3, 0 FeO. Release capacity is n_Fe2O3/2 = 1 mol O2."""

    sim = _sim_with_oxides(feo_wt=0.0, fe2o3_wt=20.0, temperature_C=1400.0)
    _set_melt_iron_oxides(sim, n_feo_mol=0.0, n_fe2o3_mol=2.0)
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = TRANSPORT_PO2_BAR
    sim.melt.oxygen_reservoir.exchange_o2_mol = 0.0
    return sim


def _near_ferric_sim(n_feo_mol: float) -> PyrolysisSimulator:
    sim = _fully_ferric_sim()
    _set_melt_iron_oxides(
        sim,
        n_feo_mol=n_feo_mol,
        n_fe2o3_mol=2.0,
    )
    return sim


def _near_ferric_lower_bound_log10(sim: PyrolysisSimulator) -> float:
    composition = melt_mol_fractions_for_kress91(
        sim._cleaned_melt_ledger_wt_pct()
    )
    return kress91_log_fO2_from_fe3_over_sigma_fe(
        fe3_over_sigma_fe=1.0 - KRESS91_FERRIC_FRACTION_EPSILON,
        mol_fractions=composition,
        T_K=float(sim.melt.temperature_C) + 273.15,
        pressure_bar=_pressure_bar(sim),
    )


def test_fully_ferric_zero_transfer_stays_on_the_lower_bound() -> None:
    """A zero transfer does not copy the gas into a ferrous-free melt."""

    sim = _fully_ferric_sim()
    assert _oxide_mol(sim, "FeO") == 0.0
    assert _oxide_mol(sim, "Fe2O3") == pytest.approx(2.0)
    expected = _ferrous_free_lower_bound_log10(
        sim,
        n_fe2o3_mol=2.0,
        temperature_C=1400.0,
    )
    clamped = kress91_log_fO2_from_fe3_over_sigma_fe(
        fe3_over_sigma_fe=1.0,
        mol_fractions=melt_mol_fractions_for_kress91(
            sim._cleaned_melt_ledger_wt_pct()
        ),
        T_K=1400.0 + 273.15,
        pressure_bar=_pressure_bar(sim),
    )

    fO2_log = sim._melt_fO2_from_ledger()
    domain = sim._last_redox_domain

    assert domain["basis"] == "ferrous_free_lower_bound"
    assert domain["status"] == "out_of_domain"
    assert domain["authority"] == "extrapolated"
    assert "one-sided lower bound, not an equality" in domain["reason"]
    assert "the fO2 scalar is absent" in domain["reason"]
    assert "kress91_inverse_not_evaluated" in domain["reason"]
    assert "capacity_O2=1" in domain["reason"]
    assert "per_tick_o2_transfer_mol=0" in domain["reason"]
    assert fO2_log is None
    assert domain["derived_fO2_log"] is None
    assert domain["fO2_log_lower_bound"] == pytest.approx(expected, abs=1.0e-8)
    assert domain["fO2_log_lower_bound"] != pytest.approx(
        math.log10(TRANSPORT_PO2_BAR)
    )
    assert abs(domain["fO2_log_lower_bound"] - clamped) > 1.0


def test_fully_ferric_release_below_capacity_stays_on_the_bound() -> None:
    """0.1 mol O2 is below the 1 mol release capacity of 2 mol Fe2O3."""

    sim = _fully_ferric_sim()
    expected = _ferrous_free_lower_bound_log10(
        sim,
        n_fe2o3_mol=2.0,
        temperature_C=1400.0,
    )
    sim.melt.oxygen_reservoir.exchange_o2_mol = 0.1

    fO2_log = sim._melt_fO2_from_ledger()

    assert sim._last_redox_domain["basis"] == "ferrous_free_lower_bound"
    assert "capacity_O2=1" in sim._last_redox_domain["reason"]
    assert "per_tick_o2_transfer_mol=0.1" in sim._last_redox_domain["reason"]
    assert fO2_log is None
    assert sim._last_redox_domain["fO2_log_lower_bound"] == pytest.approx(
        expected, abs=1.0e-8
    )
    assert sim._last_redox_domain["fO2_log_lower_bound"] != pytest.approx(
        math.log10(TRANSPORT_PO2_BAR)
    )


@pytest.mark.parametrize("n_feo_mol", [1.0e-12, 1.0e-9, 4.0e-6])
def test_near_ferric_zero_transfer_stays_on_the_lower_bound(
    n_feo_mol: float,
) -> None:
    sim = _near_ferric_sim(n_feo_mol)
    expected = _near_ferric_lower_bound_log10(sim)

    fO2_log = sim._melt_fO2_from_ledger()
    domain = sim._last_redox_domain

    assert domain["basis"] == "ferrous_free_lower_bound"
    assert domain["status"] == "out_of_domain"
    assert domain["fO2_log_lower_bound"] == pytest.approx(expected, abs=1.0e-8)
    assert "capacity_O2=1" in domain["reason"]
    assert "per_tick_o2_transfer_mol=0" in domain["reason"]
    assert fO2_log is None
    assert domain["derived_fO2_log"] is None


def test_near_ferric_release_below_capacity_stays_on_the_lower_bound() -> None:
    sim = _near_ferric_sim(1.0e-9)
    expected = _near_ferric_lower_bound_log10(sim)
    sim.melt.oxygen_reservoir.exchange_o2_mol = 0.1

    fO2_log = sim._melt_fO2_from_ledger()
    domain = sim._last_redox_domain

    assert domain["basis"] == "ferrous_free_lower_bound"
    assert domain["fO2_log_lower_bound"] == pytest.approx(expected, abs=1.0e-8)
    assert "capacity_O2=1" in domain["reason"]
    assert "per_tick_o2_transfer_mol=0.1" in domain["reason"]
    assert fO2_log is None


def test_near_ferric_release_above_capacity_follows_the_gas() -> None:
    sim = _near_ferric_sim(1.0e-9)
    sim.melt.oxygen_reservoir.exchange_o2_mol = 2.0

    fO2_log = sim._melt_fO2_from_ledger()

    assert sim._last_redox_domain["basis"] == "no_melt_redox_buffer"
    assert sim._last_redox_domain["authority"] == "gas_interface_controlled"
    assert fO2_log == pytest.approx(math.log10(TRANSPORT_PO2_BAR))


def test_near_ferric_interface_holds_transport_pressure() -> None:
    sim = _near_ferric_sim(1.0e-9)
    _finite_gas_film(sim)

    assert sim._melt_fO2_from_ledger() is None
    sim._sync_oxygen_reservoir_mirror()
    state = sim._oxygen_interface_state(TRANSPORT_PO2_BAR)

    assert sim._last_redox_domain["basis"] == "ferrous_free_lower_bound"
    assert state["interface_pO2_bar"] == pytest.approx(TRANSPORT_PO2_BAR)
    assert state["limiting_regime"] == "gas_side_ferrous_free_lower_bound"
    assert state["melt_intrinsic_pO2_bar"] is None


def test_near_ferric_inventory_inside_kress_interval_is_unchanged() -> None:
    sim = _near_ferric_sim(1.0e-4)
    ferric_fraction, *_ = _ferric_state(sim)
    expected = kress91_log_fO2_from_fe3_over_sigma_fe(
        fe3_over_sigma_fe=ferric_fraction,
        mol_fractions=melt_mol_fractions_for_kress91(
            sim._melt_oxide_wt_pct()
        ),
        T_K=float(sim.melt.temperature_C) + 273.15,
        pressure_bar=_pressure_bar(sim),
    )

    fO2_log = sim._melt_fO2_from_ledger()

    assert sim._last_redox_domain["basis"] == "kress91_inverse"
    assert not sim._last_redox_domain["endpoint_clamped"]
    assert fO2_log == pytest.approx(expected, abs=1.0e-8)


def test_fully_ferric_release_above_capacity_follows_the_gas() -> None:
    """2 mol O2 exceeds the 1 mol release capacity, so the gas owns the melt."""

    sim = _fully_ferric_sim()
    sim.melt.oxygen_reservoir.exchange_o2_mol = 2.0

    fO2_log = sim._melt_fO2_from_ledger()

    assert sim._last_redox_domain["basis"] == "no_melt_redox_buffer"
    assert sim._last_redox_domain["authority"] == "gas_interface_controlled"
    assert "kress91_inverse_not_evaluated" in sim._last_redox_domain["reason"]
    assert "capacity_O2=1" in sim._last_redox_domain["reason"]
    assert fO2_log == pytest.approx(math.log10(TRANSPORT_PO2_BAR))


def test_fully_ferric_respeciation_does_not_mint_feo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = _fully_ferric_sim()
    monkeypatch.setattr(
        sim,
        "_melt_redox_exchange_is_liquid",
        lambda *_args, **_kwargs: True,
    )
    before = sim.atom_ledger.mol_by_account()

    diagnostic = sim._apply_fe_redox_respeciation()
    evaporative = sim._apply_fe_redox_respeciation(
        oxygen_source="evaporative_metal_loss_internal",
        fO2_log_override=math.log10(TRANSPORT_PO2_BAR),
        internal_o2_capacity_mol=1.0,
    )

    assert diagnostic["respeciation_status"] == "skipped_ferrous_free_lower_bound"
    assert evaporative["respeciation_status"] == "skipped_ferrous_free_lower_bound"
    assert "respeciation_would_mint_feo" in evaporative["reason"]
    assert _oxide_mol(sim, "FeO") == 0.0
    assert _oxide_mol(sim, "Fe2O3") == pytest.approx(2.0)
    assert sim.atom_ledger.mol_by_account() == before


def _finite_gas_film(sim: PyrolysisSimulator) -> None:
    """100 mbar N2 puts the duct on a finite Sherwood film, not k_g = inf."""

    sim.melt.p_total_mbar = 100.0
    sim._melt_headspace_composition_mbar = {"N2": 1.0}
    sim.overhead.composition = {"N2": 1.0e6}
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = TRANSPORT_PO2_BAR


def test_ferrous_free_interface_holds_transport_pressure_with_a_finite_gas_film() -> None:
    """The edge is not a melt pressure. A finite gas film must not solve [p_g, 100 bar]."""

    sim = _fully_ferric_sim()
    _finite_gas_film(sim)
    expected = _ferrous_free_lower_bound_log10(
        sim,
        n_fe2o3_mol=2.0,
        temperature_C=1400.0,
    )

    assert sim._melt_fO2_from_ledger() is None
    sim._sync_oxygen_reservoir_mirror()
    state = sim._oxygen_interface_state(TRANSPORT_PO2_BAR)

    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log is None
    assert sim._current_melt_redox_fO2_log() is None
    assert math.isfinite(state["gas_side_k_m_s"])
    assert state["gas_side_k_m_s"] > 0.0
    assert not math.isinf(state["gas_side_k_m_s"])
    assert state["interface_pO2_bar"] == pytest.approx(TRANSPORT_PO2_BAR)
    assert state["limiting_regime"] == "gas_side_ferrous_free_lower_bound"
    assert state["melt_intrinsic_pO2_bar"] is None
    assert sim._last_redox_domain["fO2_log_lower_bound"] == pytest.approx(
        expected, abs=1.0e-8
    )
    assert expected > 70.0


def test_ferrous_free_sio_factor_uses_transport_pressure() -> None:
    """SiO applies sqrt(p_ref / p_i). At this edge p_i is p_g, not 100 bar."""

    sim = _fully_ferric_sim()
    _finite_gas_film(sim)
    p_ref_bar = 1.0e-9
    sim._melt_fO2_from_ledger()

    live = sim._internal_analytical_equilibrium()
    interface_pO2_bar = float(live.diagnostics["interface_pO2_bar"])
    live_sio = float(live.vapor_pressures_Pa["SiO"])
    factor = math.sqrt(p_ref_bar / interface_pO2_bar)

    assert interface_pO2_bar == pytest.approx(TRANSPORT_PO2_BAR)
    assert factor == pytest.approx(math.sqrt(p_ref_bar / TRANSPORT_PO2_BAR))
    assert factor > math.sqrt(p_ref_bar / 100.0) * 1.0e3

    sim._interface_pO2_bar = lambda: 100.0  # type: ignore[method-assign]
    clamped = sim._internal_analytical_equilibrium()
    clamped_sio = clamped.vapor_pressures_Pa.get("SiO")
    if clamped_sio is None:
        assert live_sio > 0.0
    else:
        assert live_sio / float(clamped_sio) == pytest.approx(
            math.sqrt(100.0 / TRANSPORT_PO2_BAR),
            rel=1.0e-6,
        )


def test_ferrous_free_split_and_per_hour_summary_publish_the_bound() -> None:
    """The +78 edge is a bound field. The summary writer must not treat it as fO2_log."""

    from simulator.runner import build_per_hour_summary
    from simulator.state import CampaignPhase, HourSnapshot

    sim = _fully_ferric_sim()
    expected = _ferrous_free_lower_bound_log10(
        sim,
        n_fe2o3_mol=2.0,
        temperature_C=1400.0,
    )
    assert sim._melt_fO2_from_ledger() is None
    sim._sync_oxygen_reservoir_mirror()
    split = sim._compute_fe_redox_split_diagnostic()

    assert split["fO2_log"] is None
    assert split["fO2_log_lower_bound"] == pytest.approx(expected, abs=1.0e-8)
    assert split["redox_domain"]["basis"] == "ferrous_free_lower_bound"
    assert split["redox_domain"]["derived_fO2_log"] is None
    assert "fO2_log_lower_bound" in split["redox_domain"]

    snapshot = HourSnapshot(hour=1, campaign=CampaignPhase.C2A)
    snapshot.fe_redox_split = split
    summary = build_per_hour_summary(sim, snapshot)
    exported = summary["fe_redox_split"]

    assert exported["fO2_log"] is None
    assert exported["fO2_log_lower_bound"] == pytest.approx(expected, abs=1.0e-8)
    assert exported["redox_domain"]["basis"] == "ferrous_free_lower_bound"
    json.dumps(summary, allow_nan=False)


def test_ferrous_free_hour_does_not_float_the_absent_scalar() -> None:
    """One step must finish, and every absent-scalar caller must stay flagged."""

    from simulator.evaporation import EvaporationFluxRefusal
    from simulator.reduced_real_determinism import (
        PT0InvalidControls,
        _authoritative_melt_fO2_log,
    )
    from simulator.runner import build_per_hour_summary

    sim = _fully_ferric_sim()
    expected = _ferrous_free_lower_bound_log10(
        sim,
        n_fe2o3_mol=2.0,
        temperature_C=1400.0,
    )
    assert sim._melt_fO2_from_ledger() is None

    snapshot = sim.step()
    summary = build_per_hour_summary(sim, snapshot)
    json.dumps(summary, allow_nan=False)

    reservoir = sim.melt.oxygen_reservoir
    exported = summary["fe_redox_split"]
    assert sim._current_melt_redox_fO2_log() is None
    assert exported["fO2_log"] is None
    assert exported["fO2_log_lower_bound"] == pytest.approx(expected, abs=1.0e-8)
    assert exported["redox_domain"]["basis"] == "ferrous_free_lower_bound"
    assert sim._last_melt_redox_liquidus_gate_diagnostic["source"] == (
        "none:ferrous_free_lower_bound"
    )
    assert reservoir.interface_pO2_limiting_regime == (
        "gas_side_ferrous_free_lower_bound"
    )
    assert reservoir.interface_pO2_bar == pytest.approx(
        reservoir.headspace_transport_pO2_bar
    )
    assert reservoir.interface_pO2_bar != pytest.approx(100.0)
    assert reservoir.melt_intrinsic_fO2_log is None
    assert sim._last_oxygen_interface_diagnostic[
        "redox_buffer_capacity_mol_per_ln_fO2"
    ] is None
    assert sim._last_native_fe_saturation_event["native_fe_event"] == (
        "skipped_ferrous_free_lower_bound"
    )
    assert _oxide_mol(sim, "FeO") == 0.0
    assert _oxide_mol(sim, "Fe2O3") == pytest.approx(2.0)

    with pytest.raises(EvaporationFluxRefusal) as curve_refusal:
        sim._freeze_gate_curve()
    assert curve_refusal.value.diagnostic["source"] == (
        "none:ferrous_free_lower_bound"
    )
    with pytest.raises(EvaporationFluxRefusal) as key_refusal:
        sim._freeze_gate_redox_key_fO2_log()
    assert key_refusal.value.diagnostic["source"] == (
        "none:ferrous_free_lower_bound"
    )
    with pytest.raises(EvaporationFluxRefusal) as factor_refusal:
        sim._melt_redox_liquid_fraction_factor(1400.0 + 273.15)
    assert factor_refusal.value.diagnostic["source"] == (
        "none:ferrous_free_lower_bound"
    )

    shadow = sim._oxygen_shadow_transfer()
    assert shadow["status"] == "ferrous_free_lower_bound"
    assert shadow["transfer_o2_mol"] == 0.0

    extent = sim._compute_native_fe_saturation_extent()
    assert extent["native_fe_saturation"] is False
    assert extent["native_fe_frac"] == 0.0

    sim._melt_redox_gate_authority_tick_hour = int(sim.melt.hour)
    sim._melt_redox_gate_authority_this_tick = None
    sim._record_phase_context_diagnostic("ferrous_free_probe")
    phase = sim._last_phase_context_diagnostic["ferrous_free_probe"]
    assert phase["source"] == "none:ferrous_free_lower_bound"
    assert phase["status"] == "unavailable"

    sim.campaign_mgr.o2_bubbler_controls = lambda _campaign: {
        "o2_bubbler_kg_per_hr": 1.0,
        "o2_bubbler_eta_absorb_default": 1.0,
        "o2_bubbler_target_fO2_log": -5.0,
    }
    bubbler = sim._apply_o2_bubbler()
    assert bubbler["reason"] == "ferrous_free_lower_bound"
    assert bubbler["injected_mol"] == 0.0

    source = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:test": 1.0}
    )
    assert source.redox_source_skip_reason == "ferrous_free_lower_bound"
    assert _oxide_mol(sim, "FeO") == 0.0

    class _Result:
        def __init__(self, liquid_fraction):
            self.liquid_fraction = liquid_fraction
            self.warnings: list[str] = []

    sim._stage0_sulfur_input_ppm = lambda: 10.0
    sim._attach_post_equilibrium_sulfsat(_Result(None))
    assert sim._last_melt_redox_liquid_fraction_diagnostic["source"] == (
        "none:ferrous_free_lower_bound"
    )
    assert sim._last_sulfur_saturation_result.calibration_status == (
        "not_evaluated"
    )
    sim._attach_post_equilibrium_sulfsat(_Result(1.0))
    assert sim._last_sulfur_saturation_result.not_evaluated_reason == (
        "ferrous_free_lower_bound"
    )

    with pytest.raises(PT0InvalidControls, match="ferrous_free_lower_bound"):
        _authoritative_melt_fO2_log(sim)

    fresh = _fully_ferric_sim()
    assert fresh._melt_fO2_from_ledger() is None
    calls: list[dict] = []

    def _equilibrate(**kwargs):
        calls.append(kwargs)
        raise AssertionError("phase engine must not receive an invented fO2")

    fresh.backend.is_available = lambda: True
    fresh.backend.equilibrate = _equilibrate
    fresh._get_equilibrium()
    assert calls == []
    assert fresh._current_melt_redox_fO2_log() is None

    from simulator.mre_reproduction import MREReproductionInterval
    from simulator.runner import _apply_sio_wall_sweep_controls

    captured_mre: list[dict] = []

    def _stop_mre(
        intent,
        *,
        control_inputs,
        fO2_log=None,
        fe_redox_policy="intrinsic",
        temperature_C_override=None,
    ):
        captured_mre.append({
            "melt_fO2_log": control_inputs.get("melt_fO2_log", "MISSING"),
            "fO2_log": fO2_log,
        })
        raise RuntimeError("mre_dispatch_stop")

    mre_sim = _fully_ferric_sim()
    mre_sim._dispatch_only = _stop_mre
    with pytest.raises(RuntimeError, match="mre_dispatch_stop"):
        mre_sim._execute_mre_interval(
            MREReproductionInterval(
                start_h=0.0,
                end_h=1.0,
                dt_h=1.0,
                applied_current_A=0.0,
                applied_voltage_V=0.0,
                temperature_C=float(mre_sim.melt.temperature_C),
                pO2_bar=1.0e-8,
                source_locator="r7b-ferrous-free",
            ),
            execution_origin="literature-reproduction",
        )
    assert captured_mre[0]["melt_fO2_log"] is None
    assert captured_mre[0]["fO2_log"] is None

    sweep = _fully_ferric_sim()
    _apply_sio_wall_sweep_controls(sweep, pO2_mbar=1.0)
    assert sweep.melt.oxygen_reservoir.melt_intrinsic_fO2_log is None
    assert sweep._current_melt_redox_fO2_log() is None

    assert reservoir.shadow_oxygen_transfer["status"] == (
        "ferrous_free_lower_bound"
    )
    assert reservoir.shadow_oxygen_transfer["requested_transfer_o2_mol"] == 0.0
    assert reservoir.shadow_oxygen_transfer["capacity_mol_per_ln_fO2"] is None
