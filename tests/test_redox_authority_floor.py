"""Melt fO2 authority when the ferric ratio is only the Kress floor."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
import yaml

from simulator.core import PyrolysisSimulator
from simulator.state import MOLAR_MASS
from simulator.fe_redox import (
    KRESS91_FERRIC_FRACTION_EPSILON,
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
    """FeO -> Fe(g) + 1/2 O2 leaves real O2. At the saturation bound the
    unclamped Kress fraction is interior, so that O2 oxidises Fe2+.
    """

    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1600.0)
    monkeypatch.setattr(
        sim,
        "_melt_redox_exchange_is_liquid",
        lambda *_args, **_kwargs: True,
    )
    fO2_log = sim._melt_fO2_from_ledger()
    assert sim._last_redox_domain["basis"] == "fe_saturation_bound"
    composition = melt_mol_fractions_for_kress91(sim._cleaned_melt_ledger_wt_pct())
    target_q = kress91_fe3_over_sigma_fe(
        fO2_log=fO2_log,
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
