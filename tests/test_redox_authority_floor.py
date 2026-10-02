"""Melt fO2 authority when the ferric ratio is only the Kress floor."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
import yaml

import simulator.core as core_module
from simulator.core import OXYGEN_RESERVOIR_NOOP_MOL, PyrolysisSimulator
from simulator.accounting.formulas import resolve_species_formula
from simulator.physical_constants import MELT_DISSOCIATION_PO2_MAX_BAR
from simulator.reduced_real_determinism import _authoritative_melt_fO2_log
from simulator.state import MOLAR_MASS
from simulator.fe_redox import (
    KRESS91_FERRIC_FRACTION_EPSILON,
    KRESS91_LN_FO2_COEFFICIENT,
    Kress91InvalidControls,
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
) -> None:
    domain = sim._last_redox_domain
    fO2_log = domain["fO2_log_lower_bound"]
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
    assert "this_hour_no_metal_nucleation_by_model_limit" in domain["reason"]
    assert sim._melt_redox_equality_is_absent()
    assert sim._current_melt_redox_fO2_log() is None
    assert domain["derived_fO2_log"] is None
    assert domain["equivalent_pO2_bar"] is None
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

    assert sim._melt_fO2_from_ledger() is None
    _assert_saturation_bound(sim, temperature_C=1220.0)


def test_depleted_feo_without_fe3_stays_on_the_saturation_bound() -> None:
    """R2: trace FeO and no Fe3+ is still the saturation bound, not Kress."""

    sim = _sim_with_oxides(feo_wt=1.0e-4, fe2o3_wt=0.0, temperature_C=1400.0)
    assert _oxide_mol(sim, "FeO") > 0.0
    assert _oxide_mol(sim, "Fe2O3") == 0.0

    assert sim._melt_fO2_from_ledger() is None
    _assert_saturation_bound(sim, temperature_C=1400.0)


def test_zero_transfer_below_the_bound_does_not_follow_the_gas() -> None:
    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1220.0)
    assert sim._melt_fO2_from_ledger() is None
    bound = sim._last_redox_domain["fO2_log_lower_bound"]
    assert sim._last_redox_domain["basis"] == "fe_saturation_bound"
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = 10.0 ** (
        bound - 1.0
    )
    sim.melt.oxygen_reservoir.exchange_o2_mol = 0.0

    fO2_log = sim._melt_fO2_from_ledger()

    assert sim._last_redox_domain["basis"] == "fe_saturation_bound"
    assert fO2_log is None
    assert sim._last_redox_domain["fO2_log_lower_bound"] == pytest.approx(
        bound, abs=1.0e-8
    )


def test_unavailable_fe_feo_buffer_returns_absent_without_gas_or_kress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1400.0)
    _set_melt_iron_oxides(sim, n_feo_mol=1.0, n_fe2o3_mol=0.0)
    sim.atom_ledger.load_external_mol(
        "process.metal_phase",
        {"Fe": 1.0},
        source="test retained native Fe",
        material_origin="feedstock",
    )
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = TRANSPORT_PO2_BAR
    sim.melt.oxygen_reservoir.exchange_o2_mol = 0.0
    initial_interface = 4.0e-7
    sim.melt.oxygen_reservoir.interface_pO2_bar = initial_interface

    monkeypatch.setattr(
        core_module,
        "calphad_ferrous_feo_activity_diagnostic",
        lambda **_kwargs: {"a_FeO_authoritative": 0.0},
    )

    def unexpected_kress_inverse(*_args: Any, **_kwargs: Any) -> float:
        raise AssertionError("Kress inverse must not run without buffer activity")

    monkeypatch.setattr(
        core_module,
        "kress91_log_fO2_from_fe3_over_sigma_fe",
        unexpected_kress_inverse,
    )

    fO2_log = sim._melt_fO2_from_ledger()

    assert _oxide_mol(sim, "FeO") == pytest.approx(1.0)
    metal_mol = sim.atom_ledger.project_account_mol("process.metal_phase")["Fe"]
    assert metal_mol == pytest.approx(1.0)
    assert fO2_log is None, (
        f"reported={fO2_log!r}, basis={sim._last_redox_domain.get('basis')!r}"
    )
    assert fO2_log != -8.0
    assert sim._last_redox_domain["derived_fO2_log"] is None
    assert sim._last_redox_domain["basis"] == "fe_feo_buffer_activity_unavailable"
    assert sim._last_redox_domain["basis"] != "no_melt_redox_buffer"
    assert sim.melt.oxygen_reservoir.interface_pO2_bar == initial_interface


def test_uncommitted_reservoir_construction_publishes_transport_interface() -> None:
    sim = _fully_ferric_sim()

    reservoir = sim._refresh_oxygen_reservoir_without_exchange()

    assert reservoir.interface_pO2_bar == reservoir.headspace_transport_pO2_bar


@pytest.mark.parametrize("cached_fO2_log", [-8.0, None])
def test_unavailable_buffer_absence_does_not_fall_back_to_cached_scalar(
    monkeypatch: pytest.MonkeyPatch,
    cached_fO2_log: float | None,
) -> None:
    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1400.0)
    _set_melt_iron_oxides(sim, n_feo_mol=1.0, n_fe2o3_mol=0.0)
    sim.atom_ledger.load_external_mol(
        "process.metal_phase",
        {"Fe": 1.0},
        source="test retained native Fe",
        material_origin="feedstock",
    )
    reservoir = sim.melt.oxygen_reservoir
    reservoir.headspace_transport_pO2_bar = TRANSPORT_PO2_BAR
    reservoir.exchange_o2_mol = 0.0
    reservoir.melt_intrinsic_fO2_log = cached_fO2_log
    sim.melt.melt_fO2_log = cached_fO2_log
    monkeypatch.setattr(
        core_module,
        "calphad_ferrous_feo_activity_diagnostic",
        lambda **_kwargs: {"a_FeO_authoritative": 0.0},
    )

    current_fO2_log = sim._current_melt_redox_fO2_log()

    assert current_fO2_log is None, f"stale fallback returned {current_fO2_log!r}"
    key_fO2_log, authority, regime, flag = sim._melt_redox_speciation_key()
    assert key_fO2_log == pytest.approx(math.log10(TRANSPORT_PO2_BAR))
    assert authority == "no_couple"
    assert regime == "fe_feo_buffer"
    assert flag["code"] == "melt_redox_speciation_from_interface"
    assert _authoritative_melt_fO2_log(sim) == pytest.approx(key_fO2_log)


def test_unavailable_buffer_activity_failure_is_predicted_and_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1400.0)
    _set_melt_iron_oxides(sim, n_feo_mol=1.0, n_fe2o3_mol=0.0)
    sim.atom_ledger.load_external_mol(
        "process.metal_phase",
        {"Fe": 1.0},
        source="test retained native Fe",
        material_origin="feedstock",
    )
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = TRANSPORT_PO2_BAR
    sim.melt.oxygen_reservoir.exchange_o2_mol = 0.0
    monkeypatch.setattr(
        core_module,
        "calphad_ferrous_feo_activity_diagnostic",
        lambda **_kwargs: {"a_FeO_authoritative": 0.0},
    )
    visited_unavailable_basis = False
    derive = sim._melt_fO2_from_ledger

    def track_unavailable_basis(**kwargs: Any) -> float | None:
        nonlocal visited_unavailable_basis
        fO2_log = derive(**kwargs)
        visited_unavailable_basis = visited_unavailable_basis or (
            sim._last_redox_domain.get("basis")
            == "fe_feo_buffer_activity_unavailable"
        )
        return fO2_log

    monkeypatch.setattr(sim, "_melt_fO2_from_ledger", track_unavailable_basis)

    drift_before = sim.atom_ledger.element_atom_drift_report()
    sim._step_one_hour()
    assert visited_unavailable_basis
    shadow = sim.melt.oxygen_reservoir.shadow_oxygen_transfer
    assert shadow["status"] == "ok"
    assert shadow["transfer_o2_mol"] == 0.0
    assert any(
        item["flag"] == "oxygen_exchange_activity_fixed_point_nonconverged"
        for item in shadow["prediction_flags"]
    )
    drift_after = sim.atom_ledger.element_atom_drift_report()
    for report_key in (
        "accepted_transition_residual_mol_atoms",
        "whole_run_boundary_residual_mol_atoms",
    ):
        for element, value in drift_after[report_key].items():
            assert value == pytest.approx(
                drift_before[report_key][element], abs=5.0e-12
            )


def test_nonzero_release_with_no_ferric_inventory_keeps_the_bound() -> None:
    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1220.0)
    assert sim._melt_fO2_from_ledger() is None
    bound = sim._last_redox_domain["fO2_log_lower_bound"]
    transport_pO2_bar = 10.0 ** (bound - 1.0)
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = transport_pO2_bar
    sim.melt.oxygen_reservoir.exchange_o2_mol = -1.0e-6

    fO2_log = sim._melt_fO2_from_ledger()

    assert sim._last_redox_domain["basis"] == "fe_saturation_bound"
    assert fO2_log is None
    assert sim._last_redox_domain["fO2_log_lower_bound"] == pytest.approx(bound)
    assert sim._last_redox_domain["fO2_log_lower_bound"] != pytest.approx(
        math.log10(transport_pO2_bar)
    )
    _assert_saturation_bound(sim, temperature_C=1220.0)


def test_interior_ferric_ratio_still_inverts_kress() -> None:
    sim = _sim_with_oxides(feo_wt=8.0, fe2o3_wt=2.0, temperature_C=1400.0)
    feo_mol = _oxide_mol(sim, "FeO")
    fe2o3_mol = _oxide_mol(sim, "Fe2O3")
    total_fe = feo_mol + 2.0 * fe2o3_mol
    raw_q = (2.0 * fe2o3_mol) / total_fe
    assert 0.0 < raw_q < 1.0

    fO2_log = sim._melt_fO2_from_ledger()
    expected = _mole_log_fO2_from_ledger(sim)

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

    assert diagnostic["respeciation_status"] == "skipped_fe_saturation_bound"
    assert "fe_saturation_bound_has_no_melt_equality" in diagnostic["reason"]
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


def _mole_log_fO2_from_ledger(sim: PyrolysisSimulator) -> float:
    feo_mol = _oxide_mol(sim, "FeO")
    fe2o3_mol = _oxide_mol(sim, "Fe2O3")
    composition = melt_mol_fractions_for_kress91(
        sim._cleaned_melt_ledger_wt_pct()
    )
    b_term = _kress91_ln_ratio(
        mol_fractions=composition,
        T_K=float(sim.melt.temperature_C) + 273.15,
        pressure_bar=_pressure_bar(sim),
    )
    ln_ratio = math.log(fe2o3_mol) - math.log(feo_mol)
    return (ln_ratio - b_term) / (
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
    assert sim._melt_fO2_from_ledger() is None
    bound = sim._last_redox_domain["fO2_log_lower_bound"]
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = 1.0
    sim.melt.oxygen_reservoir.exchange_o2_mol = -uptake

    fO2_log = sim._melt_fO2_from_ledger()

    assert sim._last_redox_domain["basis"] == "fe_saturation_bound"
    assert fO2_log is None
    assert sim._last_redox_domain["fO2_log_lower_bound"] == pytest.approx(bound)
    assert sim._last_redox_domain["fO2_log_lower_bound"] != pytest.approx(
        math.log10(1.0)
    )


def test_trace_feo_finite_uptake_keeps_the_saturation_bound() -> None:
    """1e-13 mol FeO has capacity_O2 = 2.5e-14 mol.

    A finite transfer request cannot rename the M4 ledger regime.
    """

    n_feo = 1.0e-13
    uptake = 1.0e-6
    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1220.0)
    _set_melt_feo_mol(sim, n_feo)
    feo_mol = _oxide_mol(sim, "FeO")
    capacity_o2 = feo_mol / 4.0
    assert sim._melt_fO2_from_ledger() is None
    bound = sim._last_redox_domain["fO2_log_lower_bound"]
    assert feo_mol == pytest.approx(n_feo, rel=0.0, abs=1.0e-18)
    assert capacity_o2 == pytest.approx(n_feo / 4.0, rel=0.0, abs=1.0e-18)
    assert capacity_o2 > 1.0e-15
    assert capacity_o2 <= uptake
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = 1.0
    sim.melt.oxygen_reservoir.exchange_o2_mol = -uptake

    fO2_log = sim._melt_fO2_from_ledger()

    assert sim._last_redox_domain["basis"] == "fe_saturation_bound"
    assert fO2_log is None
    assert sim._last_redox_domain["fO2_log_lower_bound"] == pytest.approx(bound)


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

    assert sim._melt_fO2_from_ledger() is None
    assert sim._last_redox_domain["basis"] == "fe_saturation_bound"
    assert sim._last_redox_domain["fO2_log_lower_bound"] != pytest.approx(
        math.log10(1.0)
    )


def test_iron_free_melt_zero_transfer_publishes_no_equality() -> None:
    """No FeO and no Fe2O3 has no melt equality; pressure stays on interface.
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
    assert fO2_log is None
    assert sim._current_melt_redox_fO2_log() is None


def test_iron_free_melt_nonzero_transfer_keeps_equality_absent() -> None:
    """No modelled couple means no melt equality at either transfer size.
    """

    sim = _sim_with_oxides(feo_wt=0.0, fe2o3_wt=0.0, temperature_C=1600.0)
    sim._redox_handover_transfer_override = 1.0e-6

    fO2_log = sim._melt_fO2_from_ledger()
    domain = sim._last_redox_domain

    assert domain["basis"] == "no_modelled_redox_couple"
    assert domain["status"] == "out_of_domain"
    assert "both_iron_oxides_absent" in domain["reason"]
    assert fO2_log is None


@pytest.mark.parametrize(
    ("regime", "sim_factory"),
    [
        (
            "no_modelled_redox_couple",
            lambda: _sim_with_oxides(
                feo_wt=0.0, fe2o3_wt=0.0, temperature_C=1400.0
            ),
        ),
        ("ferrous_free_lower_bound", lambda: _fully_ferric_sim()),
        (
            "fe_saturation_bound",
            lambda: _sim_with_oxides(
                feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1400.0
            ),
        ),
    ],
)
def test_absent_equality_consumers_share_regime_behavior(
    monkeypatch: pytest.MonkeyPatch,
    regime: str,
    sim_factory,
) -> None:
    from types import SimpleNamespace

    sim = sim_factory()
    sim.melt.temperature_C = 1400.0
    monkeypatch.setattr(
        sim,
        "_dispatch_only",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="ok",
            diagnostic={"solidus_T_C": 1000.0, "liquidus_T_C": 1600.0},
        ),
    )
    assert sim._melt_fO2_from_ledger() is None
    domain = sim._last_redox_domain
    assert sim._melt_redox_equality_is_absent()
    assert sim._melt_redox_absence_regime() == regime
    assert domain["derived_fO2_log"] is None
    assert domain["equivalent_pO2_bar"] is None
    if regime == "fe_saturation_bound":
        _assert_saturation_bound(sim, temperature_C=1400.0)
    elif regime == "ferrous_free_lower_bound":
        assert math.isfinite(float(domain["fO2_log_lower_bound"]))
    else:
        assert "fO2_log_lower_bound" not in domain

    key_fO2_log, key_authority, key_regime, key_flag = (
        sim._melt_redox_speciation_key()
    )
    assert math.isfinite(key_fO2_log)
    assert key_regime == regime
    if regime == "fe_saturation_bound":
        assert key_authority == "bound"
        assert key_fO2_log == pytest.approx(domain["fO2_log_lower_bound"])
        assert key_flag["code"] == "melt_redox_speciation_from_bound"
        assert "ferric inventory <= NOOP" in key_flag["reason"]
    elif regime == "ferrous_free_lower_bound":
        assert key_authority == "bound"
        assert key_fO2_log == pytest.approx(
            sim._freeze_gate_liquidus_fO2_log(domain["fO2_log_lower_bound"])
        )
        assert key_flag["code"] == "melt_redox_speciation_from_bound"
    else:
        assert key_authority == "no_couple"
        assert key_flag["code"] == "melt_redox_speciation_from_interface"

    curve = sim._freeze_gate_curve()
    assert math.isfinite(float(curve["liquidus_T_C"]))
    liquid_fraction = sim._melt_redox_liquid_fraction_factor(1400.0 + 273.15)
    assert math.isfinite(liquid_fraction)
    assert 0.0 <= liquid_fraction <= 1.0
    gate_diagnostic = sim._last_melt_redox_liquidus_gate_diagnostic
    expected_source = (
        f"bound:{regime}"
        if key_authority == "bound"
        else f"no_couple:{regime}"
    )
    assert gate_diagnostic["source"] == expected_source
    assert gate_diagnostic["melt_redox_speciation_flag"]["code"] == (
        key_flag["code"]
    )
    assert sim._melt_redox_exchange_is_liquid(1400.0 + 273.15)
    assert sim._last_melt_redox_liquid_fraction_diagnostic["source"] == (
        expected_source
    )
    assert math.isfinite(
        sim._last_melt_redox_liquid_fraction_diagnostic["liquid_fraction"]
    )
    assert sim._compute_native_fe_saturation_extent()["native_fe_frac"] == 0.0
    assert _authoritative_melt_fO2_log(sim) == pytest.approx(key_fO2_log)
    assert sim._last_pt0_melt_redox_speciation_diagnostic["flag"]["code"] == (
        key_flag["code"]
    )

    _finite_gas_film(sim)
    sim.melt.oxygen_reservoir.exchange_o2_mol = 0.0
    sim._sync_oxygen_reservoir_mirror()
    state = sim._oxygen_interface_state(TRANSPORT_PO2_BAR)
    assert state["interface_pO2_bar"] == pytest.approx(TRANSPORT_PO2_BAR)
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log is None
    if regime == "fe_saturation_bound":
        assert state["melt_intrinsic_pO2_bar"] == pytest.approx(
            TRANSPORT_PO2_BAR
        )


def test_ledger_feo_bounds_when_the_inventory_projection_is_empty() -> None:
    """The Fe authority is the ledger. An empty inventory wt% projection
    must not abort the saturation bound.
    """

    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1600.0)
    sim.inventory.melt_oxide_kg.clear()
    assert sim._melt_oxide_wt_pct() == {}
    assert _oxide_mol(sim, "FeO") > 0.0

    assert sim._melt_fO2_from_ledger() is None
    fO2_log = sim._last_redox_domain["fO2_log_lower_bound"]

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
    assert sim._melt_fO2_from_ledger() is None
    assert sim._last_redox_domain["fO2_log_lower_bound"] < math.log10(
        TRANSPORT_PO2_BAR
    )
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


def test_ferrous_free_bound_is_not_used_for_activity_or_sulfsat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import simulator.equilibrium as equilibrium_module
    from simulator.chemistry.kernel import ChemistryIntent
    from simulator.melt_backend.sulfsat import SulfurSaturationResult

    sim = _fully_ferric_sim()
    key_fO2_log, authority, regime, _flag = sim._melt_redox_speciation_key()
    assert key_fO2_log == 30.0
    assert authority == "bound"
    assert regime == "ferrous_free_lower_bound"

    activity_calls: list[dict[str, Any]] = []
    original_activity = equilibrium_module.calphad_ferrous_feo_activity_diagnostic

    def observe_activity(**kwargs: Any) -> dict[str, Any]:
        activity_calls.append(kwargs)
        return original_activity(**kwargs)

    monkeypatch.setattr(
        equilibrium_module,
        "calphad_ferrous_feo_activity_diagnostic",
        observe_activity,
    )
    equilibrium = sim._internal_analytical_equilibrium()
    melt_activity = equilibrium.diagnostics["a_FeO_calphad"]
    assert melt_activity["status"] == "unavailable"
    assert "ferrous_free_lower_bound" in melt_activity["reason"]
    assert all(call["fO2_log"] != 30.0 for call in activity_calls)

    vapor_dispatches: list[dict[str, Any]] = []
    original_dispatch = sim._dispatch_only

    def observe_vapor_dispatch(intent, **kwargs):
        if intent == ChemistryIntent.VAPOR_PRESSURE:
            vapor_dispatches.append(kwargs)
        return original_dispatch(intent, **kwargs)

    monkeypatch.setattr(sim, "_dispatch_only", observe_vapor_dispatch)
    sim._refresh_vapor_pressures_from_kernel(equilibrium)
    assert len(vapor_dispatches) == 1
    vapor_inputs = vapor_dispatches[0]
    assert vapor_inputs["fO2_log"] is None
    assert vapor_inputs["control_inputs"]["intrinsic_fO2_log"] is None
    vapor_activity = sim._last_vapor_pressure_diagnostic["a_FeO_calphad"]
    assert vapor_activity["status"] == "unavailable"
    assert "ferrous_free_lower_bound" in vapor_activity["reason"]

    sulfur_calls: list[dict[str, Any]] = []

    def unexpected_sulfsat(**kwargs: Any) -> SulfurSaturationResult:
        sulfur_calls.append(kwargs)
        return SulfurSaturationResult(calibration_status="in_range")

    monkeypatch.setattr(
        sim._sulfsat_gate,
        "compute_sulfur_saturation",
        unexpected_sulfsat,
    )
    sim._stage0_sulfur_input_ppm = lambda: 10.0
    sulfur_equilibrium = SimpleNamespace(liquid_fraction=0.5, warnings=[])
    sim._attach_post_equilibrium_sulfsat(sulfur_equilibrium)
    assert sulfur_calls == []
    assert sulfur_equilibrium.sulfur_saturation.calibration_status == (
        "not_evaluated"
    )
    assert sulfur_equilibrium.sulfur_saturation.not_evaluated_reason == (
        "ferrous_free_lower_bound"
    )

    m4 = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1400.0)
    assert m4._melt_fO2_from_ledger() is None
    m4_bound = m4._last_redox_domain["fO2_log_lower_bound"]
    m4_equilibrium = m4._internal_analytical_equilibrium()
    assert m4_equilibrium.diagnostics["a_FeO_calphad"]["status"] == "ok"
    assert m4_equilibrium.diagnostics["a_FeO_calphad"][
        "a_FeO_authoritative"
    ] > 0.0

    m4_sulfur_calls: list[dict[str, Any]] = []

    def evaluate_m4_sulfsat(**kwargs: Any) -> SulfurSaturationResult:
        m4_sulfur_calls.append(kwargs)
        return SulfurSaturationResult(calibration_status="in_range")

    monkeypatch.setattr(
        m4._sulfsat_gate,
        "compute_sulfur_saturation",
        evaluate_m4_sulfsat,
    )
    m4._stage0_sulfur_input_ppm = lambda: 10.0
    m4._attach_post_equilibrium_sulfsat(
        SimpleNamespace(liquid_fraction=0.5, warnings=[])
    )
    assert len(m4_sulfur_calls) == 1
    assert m4_sulfur_calls[0]["fO2_log"] == pytest.approx(m4_bound)


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
    with pytest.raises(Kress91InvalidControls, match=r"\(0, 1\)"):
        kress91_log_fO2_from_fe3_over_sigma_fe(
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
    assert fO2_log is None
    assert domain["derived_fO2_log"] is None
    assert domain["fO2_log_lower_bound"] == pytest.approx(expected, abs=1.0e-8)


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
    assert fO2_log is None
    assert sim._last_redox_domain["fO2_log_lower_bound"] == pytest.approx(
        expected, abs=1.0e-8
    )
    assert sim._last_redox_domain["fO2_log_lower_bound"] > math.log10(
        TRANSPORT_PO2_BAR
    )


@pytest.mark.parametrize("n_feo_mol", [1.0e-12, 1.0e-9, 4.0e-6])
def test_near_ferric_zero_transfer_uses_the_mole_log_inverse(
    n_feo_mol: float,
) -> None:
    sim = _near_ferric_sim(n_feo_mol)
    expected = _mole_log_fO2_from_ledger(sim)

    fO2_log = sim._melt_fO2_from_ledger()
    domain = sim._last_redox_domain

    assert domain["basis"] == "kress91_inverse"
    assert domain["derived_fO2_log"] == pytest.approx(expected, abs=1.0e-8)
    assert fO2_log == pytest.approx(expected, abs=1.0e-8)


def test_near_ferric_release_below_capacity_keeps_m5_equality() -> None:
    sim = _near_ferric_sim(1.0e-9)
    expected = _mole_log_fO2_from_ledger(sim)
    sim.melt.oxygen_reservoir.exchange_o2_mol = 0.1

    fO2_log = sim._melt_fO2_from_ledger()
    domain = sim._last_redox_domain

    assert domain["basis"] == "kress91_inverse"
    assert domain["derived_fO2_log"] == pytest.approx(expected, abs=1.0e-8)
    assert fO2_log == pytest.approx(expected, abs=1.0e-8)


def test_near_ferric_exhausted_release_keeps_m5_equality() -> None:
    sim = _near_ferric_sim(1.0e-9)
    expected = _mole_log_fO2_from_ledger(sim)
    sim.melt.oxygen_reservoir.exchange_o2_mol = 2.0

    fO2_log = sim._melt_fO2_from_ledger()

    assert sim._last_redox_domain["basis"] == "kress91_inverse"
    assert fO2_log == pytest.approx(expected, abs=1.0e-8)


def test_near_ferric_interface_uses_m5_equality_without_committing_transfer() -> None:
    sim = _near_ferric_sim(1.0e-9)
    _finite_gas_film(sim)

    expected = _mole_log_fO2_from_ledger(sim)
    assert sim._melt_fO2_from_ledger() == pytest.approx(expected, abs=1.0e-8)
    sim._sync_oxygen_reservoir_mirror()
    state = sim._oxygen_interface_state(TRANSPORT_PO2_BAR)

    assert sim._last_redox_domain["basis"] == "kress91_inverse"
    assert math.isfinite(state["interface_pO2_bar"])
    assert expected > math.log10(MELT_DISSOCIATION_PO2_MAX_BAR)
    assert state["melt_intrinsic_pO2_bar"] == MELT_DISSOCIATION_PO2_MAX_BAR
    assert state["limiting_regime"] != "gas_side_ferrous_free_lower_bound"


def test_near_ferric_inventory_inside_kress_interval_is_unchanged() -> None:
    sim = _near_ferric_sim(1.0e-4)
    expected = _mole_log_fO2_from_ledger(sim)

    fO2_log = sim._melt_fO2_from_ledger()

    assert sim._last_redox_domain["basis"] == "kress91_inverse"
    assert not sim._last_redox_domain["endpoint_clamped"]
    assert fO2_log == pytest.approx(expected, abs=1.0e-8)


def test_m5_near_ferric_inventory_uses_the_mole_log_inverse() -> None:
    sim = _sim_with_oxides(feo_wt=8.0, fe2o3_wt=2.0, temperature_C=1400.0)
    _set_melt_iron_oxides(sim, n_feo_mol=5.0e-7, n_fe2o3_mol=0.5)
    expected = _mole_log_fO2_from_ledger(sim)

    fO2_log = sim._melt_fO2_from_ledger()

    assert _oxide_mol(sim, "FeO") > OXYGEN_RESERVOIR_NOOP_MOL
    assert _oxide_mol(sim, "Fe2O3") > OXYGEN_RESERVOIR_NOOP_MOL
    assert sim._last_redox_domain["basis"] == "kress91_inverse"
    assert fO2_log == pytest.approx(expected, abs=1.0e-8)


def test_m5_finite_equality_survives_binary64_ferric_fraction_one() -> None:
    sim = _sim_with_oxides(feo_wt=8.0, fe2o3_wt=2.0, temperature_C=1400.0)
    _set_melt_iron_oxides(sim, n_feo_mol=1.0e-14, n_fe2o3_mol=1000.0)
    ln_ratio = math.log(_oxide_mol(sim, "Fe2O3")) - math.log(
        _oxide_mol(sim, "FeO")
    )
    expected = _mole_log_fO2_from_ledger(sim)

    fO2_log = sim._melt_fO2_from_ledger()

    assert ln_ratio == pytest.approx(39.14394658089878, abs=1.0e-14)
    assert sim._ledger_fe3_over_sigma_fe() == 1.0
    assert sim._last_redox_domain["basis"] == "kress91_inverse"
    assert fO2_log is not None and math.isfinite(fO2_log)
    assert fO2_log == pytest.approx(expected, abs=1.0e-8)


@pytest.mark.parametrize("n_feo_mol", [0.0, OXYGEN_RESERVOIR_NOOP_MOL])
def test_ferrous_inventory_at_or_below_noop_stays_m3(n_feo_mol: float) -> None:
    sim = _fully_ferric_sim()
    _set_melt_iron_oxides(sim, n_feo_mol=n_feo_mol, n_fe2o3_mol=2.0)

    fO2_log = sim._melt_fO2_from_ledger()

    assert sim._last_redox_domain["basis"] == "ferrous_free_lower_bound"
    assert fO2_log is None
    assert sim._last_redox_domain["fO2_log_lower_bound"] is not None


def test_zero_ferric_inventory_reports_raw_zero_ratio() -> None:
    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1400.0)

    assert sim._ledger_fe3_over_sigma_fe() == 0.0
    assert sim._ledger_ferric_fraction_diagnostic()[
        "ledger_ferric_fraction"
    ] == 0.0


@pytest.mark.parametrize("q", [0.0, 1.0, -0.1, 1.1])
def test_fraction_inverse_refuses_values_outside_the_open_interval(q: float) -> None:
    sim = _sim_with_oxides(feo_wt=8.0, fe2o3_wt=2.0, temperature_C=1400.0)
    composition = melt_mol_fractions_for_kress91(sim._melt_oxide_wt_pct())

    with pytest.raises(Kress91InvalidControls, match=r"\(0, 1\)"):
        kress91_log_fO2_from_fe3_over_sigma_fe(
            fe3_over_sigma_fe=q,
            mol_fractions=composition,
            T_K=1400.0 + 273.15,
            pressure_bar=_pressure_bar(sim),
        )


def test_fully_ferric_exhausted_release_keeps_the_lower_bound() -> None:
    """Transfer request does not change the M3 ledger classification."""

    sim = _fully_ferric_sim()
    expected = _ferrous_free_lower_bound_log10(
        sim,
        n_fe2o3_mol=2.0,
        temperature_C=1400.0,
    )
    sim.melt.oxygen_reservoir.exchange_o2_mol = 2.0

    fO2_log = sim._melt_fO2_from_ledger()

    assert sim._last_redox_domain["basis"] == "ferrous_free_lower_bound"
    assert fO2_log is None
    assert sim._last_redox_domain["fO2_log_lower_bound"] == pytest.approx(
        expected, abs=1.0e-8
    )


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


def test_zero_exchange_vapour_reads_gas_without_a_fresh_interface_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=2.0, temperature_C=1400.0)
    _set_melt_iron_oxides(sim, n_feo_mol=1.0, n_fe2o3_mol=0.2)
    _finite_gas_film(sim)
    melt_pO2 = 10.0 ** float(sim._melt_fO2_from_ledger())
    gas_pO2 = sim.melt.oxygen_reservoir.headspace_transport_pO2_bar
    assert melt_pO2 != gas_pO2
    finite_state = sim._oxygen_interface_state(gas_pO2)
    assert math.isfinite(float(finite_state["gas_side_k_m_s"]))
    assert float(finite_state["gas_side_k_m_s"]) > 0.0

    # Keep this reader test on the accepted zero-commit branch. The transport
    # solver owns its own convergence threshold and is covered by later work.
    monkeypatch.setattr(
        sim,
        "_oxygen_shadow_transfer",
        lambda **_kwargs: {
            "status": "ok",
            "direction": "none:below_threshold",
            "transfer_o2_mol": 0.0,
            "interface_pO2_bar": float(finite_state["interface_pO2_bar"]),
            "tau_hr": 0.0,
        },
    )

    reservoir = sim._apply_oxygen_reservoir_exchange()
    assert reservoir.exchange_o2_mol == 0.0
    assert reservoir.interface_pO2_bar == reservoir.headspace_transport_pO2_bar

    calls = 0
    original_root = sim._oxygen_finite_interface_root

    def counted_root(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return original_root(*args, **kwargs)

    monkeypatch.setattr(sim, "_oxygen_finite_interface_root", counted_root)
    before = calls
    assert sim._interface_pO2_bar() == reservoir.headspace_transport_pO2_bar
    assert calls == before


def test_nonzero_exchange_vapour_reads_stored_endpoint_without_a_fresh_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=2.0, temperature_C=1400.0)
    _set_melt_iron_oxides(sim, n_feo_mol=1.0, n_fe2o3_mol=0.2)
    _finite_gas_film(sim)
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": 1.0},
        source="test finite-film transport pressure",
        material_origin="reagent",
    )

    reservoir = sim._apply_oxygen_reservoir_exchange()
    assert abs(reservoir.exchange_o2_mol) > OXYGEN_RESERVOIR_NOOP_MOL
    endpoint_pO2 = float(reservoir.shadow_oxygen_transfer["interface_pO2_bar"])
    assert reservoir.interface_pO2_bar == endpoint_pO2
    reservoir.headspace_transport_pO2_bar *= 2.0
    assert reservoir.interface_pO2_bar == endpoint_pO2

    calls = 0
    original_root = sim._oxygen_finite_interface_root

    def counted_root(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return original_root(*args, **kwargs)

    monkeypatch.setattr(sim, "_oxygen_finite_interface_root", counted_root)
    before = calls
    assert sim._interface_pO2_bar() == endpoint_pO2
    assert calls == before


def test_ferrous_free_interface_uses_saturated_drive_until_transfer_commits() -> None:
    """A zero commit publishes p_g while the lower bound supplies its 100 bar drive."""

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
    assert state["melt_intrinsic_pO2_bar"] == MELT_DISSOCIATION_PO2_MAX_BAR
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


def test_fe_saturation_bound_never_sets_sio_release_pressure() -> None:
    """M4 speciation uses its bound while SiO release uses the interface."""

    sim = _sim_with_oxides(
        feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1400.0
    )
    _finite_gas_film(sim)
    assert sim._melt_fO2_from_ledger() is None
    assert sim._last_redox_domain["basis"] == "fe_saturation_bound"
    bound_fO2_log = sim._last_redox_domain["fO2_log_lower_bound"]
    bound_pO2_bar = 10.0 ** bound_fO2_log
    reservoir = sim.melt.oxygen_reservoir
    reservoir.headspace_transport_pO2_bar = 1.0e-3
    reservoir.exchange_o2_mol = 0.0

    sio_data = sim.vapor_pressures["oxide_vapors"]["SiO"]
    p_ref_bar = float(sio_data["pO2_reference_bar"])
    reservoir.interface_pO2_bar = p_ref_bar
    reference = sim._internal_analytical_equilibrium()
    reference_sio = float(reference.vapor_pressures_Pa["SiO"])

    reservoir.interface_pO2_bar = 1.0e-3
    gas_state = sim._internal_analytical_equilibrium()
    gas_sio = float(gas_state.vapor_pressures_Pa["SiO"])
    expected_at_gas = reference_sio * math.sqrt(p_ref_bar / 1.0e-3)
    expected_at_bound = reference_sio * math.sqrt(
        p_ref_bar / max(p_ref_bar, bound_pO2_bar)
    )

    assert gas_state.diagnostics["interface_pO2_bar"] == pytest.approx(1.0e-3)
    assert reservoir.exchange_o2_mol == 0.0
    assert gas_sio == pytest.approx(expected_at_gas, rel=1.0e-12)
    assert gas_sio != pytest.approx(expected_at_bound, rel=1.0e-9)
    assert sim._current_melt_redox_fO2_log() is None


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


def test_ferrous_free_refinement_prediction_preserves_bound_consumers() -> None:
    """An uncertified ferrous-free hour commits a flagged bounded prediction."""
    sim = _fully_ferric_sim()
    expected = _ferrous_free_lower_bound_log10(
        sim,
        n_fe2o3_mol=2.0,
        temperature_C=1400.0,
    )
    assert sim._melt_fO2_from_ledger() is None
    assert sim._last_redox_domain["fO2_log_lower_bound"] == pytest.approx(
        expected, abs=1.0e-8
    )

    drift_before = sim.atom_ledger.element_atom_drift_report()
    sim.step()
    shadow = sim.melt.oxygen_reservoir.shadow_oxygen_transfer
    assert shadow['status'] == 'ok'
    assert shadow['bounded'] is True
    assert any(
        item['flag'] == 'oxygen_exchange_refinement_exhausted'
        for item in shadow['prediction_flags']
    )
    drift_after = sim.atom_ledger.element_atom_drift_report()
    for report_key in (
        'accepted_transition_residual_mol_atoms',
        'whole_run_boundary_residual_mol_atoms',
    ):
        for element, value in drift_after[report_key].items():
            assert value == pytest.approx(
                drift_before[report_key][element], abs=5.0e-12
            )

    absent = _fully_ferric_sim()
    assert absent._melt_fO2_from_ledger() is None
    from types import SimpleNamespace

    absent._dispatch_only = lambda *_args, **_kwargs: SimpleNamespace(
        status="ok",
        diagnostic={"solidus_T_C": 1000.0, "liquidus_T_C": 1600.0},
    )
    absent._project_cleaned_melt_from_atom_ledger()
    assert math.isfinite(float(absent._freeze_gate_curve()["liquidus_T_C"]))
    assert absent._freeze_gate_redox_key_fO2_log() == pytest.approx(
        absent._melt_redox_speciation_key()[0]
    )
    assert math.isfinite(
        absent._melt_redox_liquid_fraction_factor(1400.0 + 273.15)
    )
    assert absent._last_melt_redox_liquidus_gate_diagnostic["source"] == (
        "bound:ferrous_free_lower_bound"
    )

    absent_ledger_before = absent.atom_ledger.mol_by_account()
    predicted = absent._oxygen_shadow_transfer()
    assert predicted['status'] == 'ok'
    assert any(
        item['flag'] == 'oxygen_exchange_refinement_exhausted'
        for item in predicted['prediction_flags']
    )
    assert absent.atom_ledger.mol_by_account() == absent_ledger_before

    extent = absent._compute_native_fe_saturation_extent()
    assert extent["native_fe_saturation"] is False
    assert extent["native_fe_frac"] == 0.0

    absent._melt_redox_gate_authority_tick_hour = int(absent.melt.hour)
    absent._melt_redox_gate_authority_this_tick = None
    absent._record_phase_context_diagnostic("ferrous_free_probe")
    phase = absent._last_phase_context_diagnostic["ferrous_free_probe"]
    assert phase["source"] == "none:ferrous_free_lower_bound"
    assert phase["status"] == "unavailable"

    absent.campaign_mgr.o2_bubbler_controls = lambda _campaign: {
        "o2_bubbler_kg_per_hr": 1.0,
        "o2_bubbler_eta_absorb_default": 1.0,
        "o2_bubbler_target_fO2_log": -5.0,
    }
    bubbler = absent._apply_o2_bubbler()
    assert bubbler["reason"] == "ferrous_free_lower_bound"
    assert bubbler["injected_mol"] == 0.0

    source = absent._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:test": 1.0}
    )
    assert source.redox_source_skip_reason == "ferrous_free_lower_bound"
    assert _oxide_mol(absent, "FeO") == 0.0

    class _Result:
        def __init__(self, liquid_fraction):
            self.liquid_fraction = liquid_fraction
            self.warnings: list[str] = []

    absent._stage0_sulfur_input_ppm = lambda: 10.0
    absent._attach_post_equilibrium_sulfsat(_Result(None))
    sulfur_basis = absent._last_sulfur_saturation_result.melt_presence_basis
    assert sulfur_basis["melt_redox_speciation_key"]["authority"] == "bound"
    assert absent._last_sulfur_saturation_result.calibration_status == (
        "not_evaluated"
    )
    assert absent._last_sulfur_saturation_result.not_evaluated_reason == (
        "ferrous_free_lower_bound"
    )
    assert sulfur_basis["melt_redox_speciation_flag"]["code"] == (
        "melt_redox_speciation_from_bound"
    )
    pt0_key_fO2_log = absent._melt_redox_speciation_key()[0]
    assert _authoritative_melt_fO2_log(absent) == pytest.approx(
        pt0_key_fO2_log
    )

    fresh = _fully_ferric_sim()
    assert fresh._melt_fO2_from_ledger() is None
    calls: list[dict] = []

    def _equilibrate(**kwargs):
        calls.append(kwargs)
        from types import SimpleNamespace

        return SimpleNamespace(
            ledger_transition=None,
            liquid_fraction=None,
            vapor_pressures_Pa={},
            vapor_pressures_source={},
            activity_coefficients={},
            diagnostics={},
            warnings=[],
            status="ok",
        )

    fresh.backend.is_available = lambda: True
    fresh.backend.equilibrate = _equilibrate
    fresh_result = fresh._get_equilibrium()
    assert len(calls) == 1
    assert calls[0]["fO2_log"] == pytest.approx(
        fresh._melt_redox_speciation_key()[0]
    )
    assert fresh_result.diagnostics["melt_redox_speciation_flag"]["code"] == (
        "melt_redox_speciation_from_bound"
    )
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

def _m2_ideal_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> PyrolysisSimulator:
    """One mole FeO, one mole native Fe, and nine moles ideal SiO2 solvent."""

    feo_mass = float(MOLAR_MASS["FeO"])
    solvent_mass = 9.0 * float(MOLAR_MASS["SiO2"])
    total_mass = feo_mass + solvent_mass
    sim = _sim_with_oxides(
        feo_wt=100.0 * feo_mass / total_mass,
        fe2o3_wt=0.0,
        temperature_C=1726.85,
        mass_kg=total_mass / 1000.0,
    )
    _set_melt_iron_oxides(sim, n_feo_mol=1.0, n_fe2o3_mol=0.0)
    sim.atom_ledger.load_external_mol(
        "process.metal_phase",
        {"Fe": 1.0},
        source="M2 ideal finite-film fixture",
        material_origin="feedstock",
    )
    sim._project_cleaned_melt_from_atom_ledger()

    def ideal_activity(*, comp_wt: dict[str, float], **_kwargs: Any) -> dict[str, float]:
        feo_mol = float(comp_wt.get("FeO", 0.0)) / float(MOLAR_MASS["FeO"])
        solvent_mol = float(comp_wt.get("SiO2", 0.0)) / float(
            MOLAR_MASS["SiO2"]
        )
        total = feo_mol + solvent_mol
        return {
            "a_FeO_authoritative": feo_mol / total if total > 0.0 else 0.0
        }

    monkeypatch.setattr(
        core_module,
        "calphad_ferrous_feo_activity_diagnostic",
        ideal_activity,
    )
    monkeypatch.setattr(
        core_module,
        "feo_iw_log10_fO2_bar",
        lambda _T_K, *, a_feo: -5.0,
    )
    return sim


def _m2_ideal_buffer_pressure_pa(u_mol: float) -> float:
    activity = (2.0 * u_mol) / (2.0 * u_mol + 9.0)
    return activity * activity


def test_m2_finite_metal_film_fixture_and_endpoint_complementarity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.physical_constants import GAS_CONSTANT as physical_gas_constant

    sim = _m2_ideal_fixture(monkeypatch)
    k_g = 0.01
    k_m = 1.0e-9
    area = 1.0
    depth = 0.1
    gas_temperature = 2000.0
    alpha = k_g / (physical_gas_constant * gas_temperature)
    beta = k_m / (area * depth)

    # The ideal FeO activity gives a strictly decreasing F(u) on the feasible
    # surface inventory interval, with one sign change and therefore one root.
    bracket = [index / 100.0 for index in range(101)]
    residuals = [
        alpha * (0.02 - _m2_ideal_buffer_pressure_pa(u))
        - beta * (u - 0.5)
        for u in bracket
    ]
    assert residuals[0] > 0.0 > residuals[-1]
    assert all(left > right for left, right in zip(residuals, residuals[1:]))

    root = sim._oxygen_finite_interface_root(
        gas_pO2_bar=0.02 / 1.0e5,
        melt_pO2_bar=0.01 / 1.0e5,
        gas_temperature_K=gas_temperature,
        k_g=k_g,
        k_m=k_m,
        surface_area_m2=area,
        h_eff_m=depth,
        kress91_evaluator=None,
        n_feo_mol=1.0,
        n_fe2o3_mol=0.0,
        n_fe_metal_mol=1.0,
        capacity_mol_per_ln_fO2=0.0,
    )
    interface_pa = float(root["interface_pO2_bar"]) * 1.0e5
    assert interface_pa == pytest.approx(
        0.017068850876654978,
        rel=1.0e-9,
    )
    assert root["interface_root_clamped"] is False
    assert root["gas_flux_mol_m2_s"] == pytest.approx(
        root["melt_flux_mol_m2_s"], rel=1.0e-10, abs=2.0e-14
    )

    endpoint = sim._oxygen_finite_interface_root(
        gas_pO2_bar=0.1 / 1.0e5,
        melt_pO2_bar=0.01 / 1.0e5,
        gas_temperature_K=gas_temperature,
        k_g=k_g,
        k_m=k_m,
        surface_area_m2=area,
        h_eff_m=depth,
        kress91_evaluator=None,
        n_feo_mol=1.0,
        n_fe2o3_mol=0.0,
        n_fe_metal_mol=1.0,
        capacity_mol_per_ln_fO2=0.0,
    )
    assert endpoint["interface_root_clamped"] is True
    assert endpoint["surface_inventory_o2_equivalent_mol"] == pytest.approx(1.0)
    assert endpoint["surface_buffer_pO2_Pa"] == pytest.approx(
        _m2_ideal_buffer_pressure_pa(1.0)
    )
    assert endpoint["interface_pO2_bar"] * 1.0e5 > endpoint[
        "surface_buffer_pO2_Pa"
    ]
    assert endpoint["gas_flux_mol_m2_s"] == pytest.approx(
        endpoint["melt_flux_mol_m2_s"], rel=1.0e-10, abs=2.0e-14
    )


@pytest.mark.parametrize(
    ("gas_pressure_pa", "expected_sign"),
    [(0.02, -1), (0.001, 1)],
    ids=("oxidizing", "reducing"),
)
def test_m2_metal_exchange_stoichiometry_and_ledger_balance(
    monkeypatch: pytest.MonkeyPatch,
    gas_pressure_pa: float,
    expected_sign: int,
) -> None:
    sim = _m2_ideal_fixture(monkeypatch)
    _finite_gas_film(sim)
    sim._overhead_headspace_config["enabled"] = True
    sim.melt.melt_surface_area_m2 = 1.0
    sim.overhead.headspace_temperature_K = 2000.0
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": 1.0},
        source="M2 exchange gas boundary",
        material_origin="feedstock",
    )
    gas_pressure_bar = gas_pressure_pa / 1.0e5
    monkeypatch.setattr(
        sim,
        "_headspace_ledger_pO2_bar_from_o2_mol",
        lambda _n: gas_pressure_bar,
    )
    monkeypatch.setattr(
        sim,
        "_headspace_transport_pO2_bar_from_ledger",
        lambda *_args, **_kwargs: gas_pressure_bar,
    )
    monkeypatch.setattr(sim, "_headspace_floor_o2_mol", lambda: 0.0)
    _old_k_m, _source, melt_transport = sim._oxygen_exchange_k_m_s(
        sim.melt.temperature_C + 273.15
    )
    monkeypatch.setattr(
        sim,
        "_oxygen_exchange_k_m_s",
        lambda _T_K: (1.0e-9, "M2 fixture", melt_transport),
    )
    monkeypatch.setattr(
        sim,
        "_oxygen_interface_gas_side_k_m_s",
        lambda _T_K: (0.01, "M2 fixture"),
    )
    monkeypatch.setattr(sim, "_oxygen_exchange_effective_melt_depth_m", lambda: 0.1)
    monkeypatch.setattr(sim, "_melt_redox_exchange_is_liquid", lambda *_a, **_k: True)

    before_feo = _oxide_mol(sim, "FeO")
    before_fe2o3 = _oxide_mol(sim, "Fe2O3")
    before_metal = float(
        sim.atom_ledger.project_account_mol("process.metal_phase").get("Fe", 0.0)
    )
    before_head_o2 = float(
        sim.atom_ledger.mol_by_account("process.overhead_gas").get("O2", 0.0)
    )
    tracked_accounts = (
        "process.cleaned_melt",
        "process.metal_phase",
        "process.overhead_gas",
    )

    def tracked_atoms(element: str) -> float:
        total = 0.0
        for account in tracked_accounts:
            for species, amount in sim.atom_ledger.project_account_mol(account).items():
                formula = resolve_species_formula(species, sim.atom_ledger.registry)
                total += float(amount) * float(formula.elements.get(element, 0.0))
        return total

    before_fe_atoms = tracked_atoms("Fe")
    before_o_atoms = tracked_atoms("O")
    transitions_before = len(sim.atom_ledger.transitions)
    reservoir = sim._apply_oxygen_reservoir_exchange()
    shadow = reservoir.shadow_oxygen_transfer
    transfer = float(reservoir.exchange_o2_mol)
    assert shadow["status"] == "ok"
    assert transfer * expected_sign > 0.0
    assert shadow["fe2o3_mol_after"] == pytest.approx(before_fe2o3, abs=1.0e-12)
    assert shadow["fe_metal_mol_after"] - before_metal == pytest.approx(
        2.0 * transfer, abs=1.0e-12
    )
    assert shadow["fe_o_mol_after"] - before_feo == pytest.approx(
        -2.0 * transfer, abs=1.0e-12
    )
    if transfer < 0.0:
        assert -before_metal / 2.0 <= transfer < 0.0
    else:
        assert 0.0 < transfer <= before_feo / 2.0

    after_feo = _oxide_mol(sim, "FeO")
    after_fe2o3 = _oxide_mol(sim, "Fe2O3")
    after_metal = float(
        sim.atom_ledger.project_account_mol("process.metal_phase").get("Fe", 0.0)
    )
    after_head_o2 = float(
        sim.atom_ledger.mol_by_account("process.overhead_gas").get("O2", 0.0)
    )
    assert after_metal - before_metal == pytest.approx(2.0 * transfer, abs=1.0e-12)
    assert after_feo - before_feo == pytest.approx(-2.0 * transfer, abs=1.0e-12)
    assert after_fe2o3 == pytest.approx(before_fe2o3, abs=1.0e-12)
    assert after_head_o2 - before_head_o2 == pytest.approx(transfer, abs=1.0e-12)
    assert tracked_atoms("Fe") == pytest.approx(before_fe_atoms, abs=1.0e-12)
    assert tracked_atoms("O") == pytest.approx(before_o_atoms, abs=1.0e-12)
    transitions = sim.atom_ledger.transitions[transitions_before:]
    assert len(transitions) == 1
    assert transitions[0].name == "oxygen_reservoir_exchange"
    transitions[0].validate_conservation(sim.atom_ledger.registry)


@pytest.mark.parametrize("z", [0.01, 1.0, 100.0])
@pytest.mark.parametrize("equilibrium_mol", [0.25, -0.25], ids=("release", "uptake"))
def test_exponential_linear_relaxation_is_exact(
    monkeypatch: pytest.MonkeyPatch,
    z: float,
    equilibrium_mol: float,
) -> None:
    sim = _sim_with_oxides(
        feo_wt=10.0,
        fe2o3_wt=10.0,
        temperature_C=1400.0,
    )
    _set_melt_iron_oxides(sim, n_feo_mol=10.0, n_fe2o3_mol=10.0)
    _finite_gas_film(sim)
    sim._overhead_headspace_config["enabled"] = True
    sim.melt.melt_surface_area_m2 = 1.0
    sim.overhead.headspace_temperature_K = 2000.0
    sim._headspace_ledger_pO2_bar_from_o2_mol = lambda _n: 0.01
    sim._headspace_transport_pO2_bar_from_ledger = (
        lambda *_args, **_kwargs: 0.01
    )
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": 1.0},
        source="linear exponential relaxation fixture",
        material_origin="feedstock",
    )
    h_eff_m = sim._oxygen_exchange_effective_melt_depth_m()
    monkeypatch.setattr(
        sim,
        "_oxygen_exchange_k_m_s",
        lambda _T_K: (2.0 * z * h_eff_m, "linear fixture", {}),
    )
    n_feo0 = _oxide_mol(sim, "FeO")
    interface_calls = 0

    def linear_root(**kwargs: Any) -> dict[str, Any]:
        nonlocal interface_calls
        interface_calls += 1
        d_mol = (float(kwargs["n_feo_mol"]) - n_feo0) / 4.0
        vector_field = z * (equilibrium_mol - d_mol)
        return {
            "interface_pO2_bar": 0.01 * 10.0 ** (0.1 * d_mol),
            "interface_flux_mol_m2_s": -vector_field,
            "finite_melt_driving_force_mol": equilibrium_mol - d_mol,
            "interface_root_clamped": False,
            "interface_root_residual_mol_m2_s": 0.0,
            "interface_root_converged": True,
            "gas_conductance_mol_m2_s_per_ln": 1.0,
            "melt_conductance_mol_m2_s_per_ln": 1.0,
            "capacity_mol_per_ln_fO2": 1.0,
        }

    monkeypatch.setattr(sim, "_oxygen_finite_interface_root", linear_root)
    transfer = sim._oxygen_shadow_transfer(dt_s=1.0)
    assert transfer["interface_evaluations"] == interface_calls
    assert transfer["interface_evaluations"] <= 520
    assert transfer["amount_bisections"] == 0
    expected = equilibrium_mol * -math.expm1(-z)

    assert transfer["transfer_o2_mol"] == pytest.approx(
        expected,
        rel=2.0e-14,
        abs=2.0e-15,
    )
    assert transfer["bounded"] is True
    assert transfer["transfer_o2_mol"] * equilibrium_mol >= 0.0
    assert abs(transfer["transfer_o2_mol"]) <= abs(equilibrium_mol)


def _prepare_exponential_exchange_case(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
    *,
    law: str,
    direction: str,
) -> tuple[float, float, float, float, float, float, float, Any, float]:
    _finite_gas_film(sim)
    sim._overhead_headspace_config["enabled"] = True
    sim.melt.melt_surface_area_m2 = 1.0
    sim.overhead.headspace_temperature_K = 2000.0
    if law == "m2":
        gas_pressure_pa = 0.8 if direction == "release" else 1.2
        gas_pressure_bar = gas_pressure_pa / 1.0e5

        def ideal_surface_bound(**kwargs: Any) -> tuple[float, float]:
            composition = kwargs["comp"]
            feo_mol = float(composition.get("FeO", 0.0)) / MOLAR_MASS["FeO"]
            solvent_mol = float(composition.get("SiO2", 0.0)) / MOLAR_MASS["SiO2"]
            activity = feo_mol / (feo_mol + solvent_mol)
            return -5.0 + 2.0 * math.log10(activity), activity

        sim._fe_saturation_bound_fO2_log = ideal_surface_bound
    else:
        _set_melt_iron_oxides(sim, n_feo_mol=1.0, n_fe2o3_mol=0.2)
        intrinsic = sim._current_melt_redox_fO2_log()
        assert intrinsic is not None
        from engines.builtin.vapor_pressure import (
            physical_melt_dissociation_pO2_bar,
        )

        melt_pressure_bar = physical_melt_dissociation_pO2_bar(intrinsic)[0]
        gas_pressure_bar = melt_pressure_bar * (
            0.8 if direction == "release" else 1.2
        )
        gas_pressure_pa = gas_pressure_bar * 1.0e5

    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": 1.0},
        source=f"{law} exponential exchange fixture",
        material_origin="feedstock",
    )
    sim._headspace_ledger_pO2_bar_from_o2_mol = (
        lambda _n: gas_pressure_bar
    )
    sim._headspace_transport_pO2_bar_from_ledger = (
        lambda *_args, **_kwargs: gas_pressure_bar
    )
    sim._headspace_floor_o2_mol = lambda: 0.0
    h_eff_m = 0.1
    if law == "m2":
        # Keep z=100 on an interior equilibrium branch with a representable
        # flux-residual budget; the smaller rate makes its duration so large
        # that binary64 interface-pressure rounding dominates the root budget.
        k_m = 1.0e-7
        k_g = 10.0
    else:
        k_m = 1.0e-7
        k_g = 1.0
    melt_transport = sim._oxygen_exchange_k_m_s(
        float(sim.melt.temperature_C) + 273.15
    )[2]
    monkeypatch.setattr(
        sim,
        "_oxygen_exchange_k_m_s",
        lambda _T_K: (k_m, f"{law} reference fixture", melt_transport),
    )
    monkeypatch.setattr(
        sim,
        "_oxygen_exchange_effective_melt_depth_m",
        lambda: h_eff_m,
    )
    monkeypatch.setattr(
        sim,
        "_oxygen_interface_gas_side_k_m_s",
        lambda _T_K: (k_g, f"{law} reference fixture"),
    )
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = gas_pressure_bar
    n_feo = _oxide_mol(sim, "FeO")
    n_fe2o3 = _oxide_mol(sim, "Fe2O3")
    n_metal = float(
        sim.atom_ledger.project_account_mol("process.metal_phase").get(
            "Fe", 0.0
        )
    )
    comp = sim._melt_oxide_wt_pct()
    mol_fractions = melt_mol_fractions_for_kress91(comp)
    evaluator = (
        core_module._Kress91Evaluator(
            mol_fractions=mol_fractions,
            T_K=float(sim.melt.temperature_C) + 273.15,
            pressure_bar=_pressure_bar(sim),
        )
        if mol_fractions
        else None
    )
    intrinsic_log = sim._current_melt_redox_fO2_log()
    if intrinsic_log is None:
        melt_pressure_bar = sim._vacuum_floor_bar()
    else:
        from engines.builtin.vapor_pressure import (
            physical_melt_dissociation_pO2_bar,
        )

        melt_pressure_bar = physical_melt_dissociation_pO2_bar(intrinsic_log)[0]
    return (
        gas_pressure_bar,
        k_m,
        k_g,
        h_eff_m,
        n_feo,
        n_fe2o3,
        n_metal,
        evaluator,
        melt_pressure_bar,
    )


@pytest.mark.parametrize("z", [0.01, 1.0, 100.0])
@pytest.mark.parametrize("law", ["ferric", "m2"])
@pytest.mark.parametrize("direction", ["release", "uptake"])
def test_exponential_transfer_matches_converged_reference(
    monkeypatch: pytest.MonkeyPatch,
    law: str,
    direction: str,
    z: float,
) -> None:
    """DOP853 at rtol 1e-12 and 1e-13 supplies the independent ODE reference.

    The two references must agree to 1e-14 + 1e-5 relative moles and 1e-6
    dex before the candidate is checked against the r3 acceptance tolerances.
    """
    from scipy.integrate import solve_ivp

    if law == "m2":
        sim = _m2_ideal_fixture(monkeypatch)
    else:
        sim = _sim_with_oxides(
            feo_wt=10.0,
            fe2o3_wt=2.0,
            temperature_C=1400.0,
        )
    (
        gas_pressure_bar,
        k_m,
        k_g,
        h_eff_m,
        n_feo0,
        n_fe2o30,
        n_metal0,
        evaluator,
        melt_pressure_bar,
    ) = _prepare_exponential_exchange_case(
        sim,
        monkeypatch,
        law=law,
        direction=direction,
    )
    area = float(sim.melt.melt_surface_area_m2)
    gas_temperature_K = float(sim.overhead.headspace_temperature_K)
    initial = sim._oxygen_shadow_transfer(
        dt_s=1.0,
        transport_pO2_bar=gas_pressure_bar,
    )
    relaxation_rate = float(initial["initial_relaxation_rate_s"])
    assert math.isfinite(relaxation_rate) and relaxation_rate > 0.0
    duration_s = z / relaxation_rate

    def state_at(d_mol: float) -> tuple[float, dict[str, Any]]:
        if law == "m2":
            feo_mol = n_feo0 - 2.0 * d_mol
            ferric_mol = n_fe2o30
            metal_fe_mol = n_metal0 + 2.0 * d_mol
            melt_pressure = melt_pressure_bar
        else:
            feo_mol = n_feo0 + 4.0 * d_mol
            ferric_mol = n_fe2o30 - 2.0 * d_mol
            metal_fe_mol = n_metal0
            melt_pressure = sim._oxygen_melt_pO2_bar_for_inventory(
                n_feo_mol=feo_mol,
                n_fe2o3_mol=ferric_mol,
                kress91_evaluator=evaluator,
                fallback_pO2_bar=melt_pressure_bar,
            )
        root = sim._oxygen_finite_interface_root(
            gas_pO2_bar=gas_pressure_bar,
            melt_pO2_bar=melt_pressure,
            gas_temperature_K=gas_temperature_K,
            k_g=k_g,
            k_m=k_m,
            surface_area_m2=area,
            h_eff_m=h_eff_m,
            kress91_evaluator=evaluator,
            n_feo_mol=feo_mol,
            n_fe2o3_mol=ferric_mol,
            n_fe_metal_mol=metal_fe_mol,
            capacity_mol_per_ln_fO2=0.0,
            diagnostics=False,
        )
        return -area * float(root["interface_flux_mol_m2_s"]), root

    def reference(rtol: float) -> tuple[float, float]:
        result = solve_ivp(
            lambda _time, y: [state_at(float(y[0]))[0]],
            (0.0, duration_s),
            [0.0],
            method="DOP853",
            rtol=rtol,
            atol=1.0e-15,
        )
        assert result.success
        amount = float(result.y[0, -1])
        pressure = float(state_at(amount)[1]["interface_pO2_bar"])
        return amount, math.log10(pressure)

    ref_loose = reference(1.0e-12)
    ref_tight = reference(1.0e-13)
    amount_ref, log_pressure_ref = ref_tight
    assert abs(ref_loose[0] - amount_ref) <= (
        1.0e-14 + 1.0e-5 * abs(amount_ref)
    )
    assert abs(ref_loose[1] - log_pressure_ref) <= 1.0e-6
    initial_field = state_at(0.0)[0]
    final_field = state_at(amount_ref)[0]
    assert initial_field * amount_ref > 0.0
    assert initial_field * final_field >= -abs(initial_field) * 1.0e-8

    shadow = sim._oxygen_shadow_transfer(
        dt_s=duration_s,
        transport_pO2_bar=gas_pressure_bar,
    )
    assert abs(float(shadow["transfer_o2_mol"]) - amount_ref) <= (
        1.0e-12 + 1.0e-3 * abs(amount_ref)
    )
    candidate_root_pressure = float(
        state_at(float(shadow["transfer_o2_mol"]))[1]["interface_pO2_bar"]
    )
    assert float(shadow["interface_pO2_bar"]) == pytest.approx(
        candidate_root_pressure,
        rel=1.0e-10,
    )
    assert abs(
        math.log10(float(shadow["interface_pO2_bar"]))
        - log_pressure_ref
    ) <= 1.0e-4, (
        f"d={shadow['transfer_o2_mol']!r}, d_ref={amount_ref!r}, "
        f"d_delta={float(shadow['transfer_o2_mol']) - amount_ref!r}, "
        f"N={shadow['substeps']!r}"
    )
    assert shadow["bounded"] is True
    assert shadow["amount_bisections"] == 0
    assert shadow["interface_evaluations"] <= 520

    # Run the same passive interval through its publisher and verify that it
    # stores the endpoint root used by the accepted trajectory.
    original_root = sim._oxygen_finite_interface_root
    shadow_root_calls = 0
    inside_shadow = False

    def counted_root(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal shadow_root_calls
        if inside_shadow:
            shadow_root_calls += 1
        return original_root(*args, **kwargs)

    monkeypatch.setattr(sim, "_oxygen_finite_interface_root", counted_root)
    original_shadow = sim._oxygen_shadow_transfer

    def target_duration(**kwargs: Any) -> dict[str, Any]:
        nonlocal inside_shadow
        kwargs["dt_s"] = duration_s
        inside_shadow = True
        try:
            return original_shadow(**kwargs)
        finally:
            inside_shadow = False

    monkeypatch.setattr(sim, "_oxygen_shadow_transfer", target_duration)
    reservoir = sim._apply_oxygen_reservoir_exchange()
    published = reservoir.shadow_oxygen_transfer
    assert published["interface_pO2_bar"] == pytest.approx(
        reservoir.interface_pO2_bar,
        rel=1.0e-13,
    )
    assert shadow_root_calls == published["interface_evaluations"]
    assert shadow_root_calls <= 520
    assert published["amount_bisections"] == 0
    assert _oxide_mol(sim, "FeO") >= 0.0
    assert _oxide_mol(sim, "Fe2O3") >= 0.0


def test_exponential_binding_caps_stop_outward_and_publish_successor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = _sim_with_oxides(
        feo_wt=10.0,
        fe2o3_wt=0.1,
        temperature_C=1400.0,
    )
    _set_melt_iron_oxides(sim, n_feo_mol=10.0, n_fe2o3_mol=0.02)
    _finite_gas_film(sim)
    sim._overhead_headspace_config["enabled"] = True
    sim.melt.melt_surface_area_m2 = 1.0
    sim.overhead.headspace_temperature_K = 2000.0
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": 1.0},
        source="outward cap fixture",
        material_origin="feedstock",
    )
    h_eff_m = sim._oxygen_exchange_effective_melt_depth_m()
    monkeypatch.setattr(
        sim,
        "_oxygen_exchange_k_m_s",
        lambda _T_K: (200.0 * h_eff_m, "outward cap fixture", {}),
    )
    n_feo0 = _oxide_mol(sim, "FeO")
    root_calls = 0

    def outward_root(**kwargs: Any) -> dict[str, Any]:
        nonlocal root_calls
        root_calls += 1
        d_mol = (float(kwargs["n_feo_mol"]) - n_feo0) / 4.0
        vector_field = 100.0 * (1.0 - d_mol)
        return {
            "interface_pO2_bar": 0.01 * 10.0 ** (0.1 * d_mol),
            "interface_flux_mol_m2_s": -vector_field,
            "finite_melt_driving_force_mol": 1.0 - d_mol,
            "interface_root_clamped": False,
            "interface_root_residual_mol_m2_s": 0.0,
            "interface_root_converged": True,
            "gas_conductance_mol_m2_s_per_ln": 1.0,
            "melt_conductance_mol_m2_s_per_ln": 1.0,
            "capacity_mol_per_ln_fO2": 1.0,
        }

    monkeypatch.setattr(sim, "_oxygen_finite_interface_root", outward_root)
    transfer = sim._oxygen_shadow_transfer(dt_s=100.0)
    upper_cap = 0.02 / 2.0
    assert transfer["transfer_o2_mol"] == pytest.approx(upper_cap)
    assert transfer["fe2o3_mol_after"] == pytest.approx(0.0, abs=1.0e-15)
    assert transfer["interface_evaluations"] == root_calls
    assert transfer["interface_evaluations"] <= 529
    assert transfer["amount_bisections"] == 0
    assert transfer["interface_root_converged"] is True
    assert transfer["interface_flux_mol_m2_s"] < 0.0
    assert transfer["interface_pO2_bar"] == pytest.approx(
        0.01 * 10.0 ** (0.1 * upper_cap)
    )
    assert transfer["bounded"] is True


def _failure_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> PyrolysisSimulator:
    sim = _sim_with_oxides(
        feo_wt=10.0,
        fe2o3_wt=2.0,
        temperature_C=1400.0,
    )
    _set_melt_iron_oxides(sim, n_feo_mol=1.0, n_fe2o3_mol=0.2)
    _finite_gas_film(sim)
    sim._overhead_headspace_config["enabled"] = True
    sim.melt.melt_surface_area_m2 = 1.0
    sim.overhead.headspace_temperature_K = 2000.0
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": 1.0},
        source="numerical failure fixture",
        material_origin="feedstock",
    )
    gas_pressure_bar = 1.0e-4
    sim._headspace_ledger_pO2_bar_from_o2_mol = (
        lambda _n: gas_pressure_bar
    )
    sim._headspace_transport_pO2_bar_from_ledger = (
        lambda *_args, **_kwargs: gas_pressure_bar
    )
    sim._headspace_floor_o2_mol = lambda: 0.0
    melt_transport = sim._oxygen_exchange_k_m_s(
        float(sim.melt.temperature_C) + 273.15
    )[2]
    monkeypatch.setattr(
        sim,
        "_oxygen_exchange_k_m_s",
        lambda _T_K: (1.0e-9, "numerical failure fixture", melt_transport),
    )
    monkeypatch.setattr(
        sim,
        "_oxygen_exchange_effective_melt_depth_m",
        lambda: 0.1,
    )
    monkeypatch.setattr(
        sim,
        "_oxygen_interface_gas_side_k_m_s",
        lambda _T_K: (0.01, "numerical failure fixture"),
    )
    return sim


def _assert_shadow_prediction_keeps_ledger(
    sim: PyrolysisSimulator,
    *,
    flag: str,
) -> None:
    before = sim.atom_ledger.mol_by_account()
    transitions_before = len(sim.atom_ledger.transitions)
    result = sim._oxygen_shadow_transfer(dt_s=1.0)
    assert result['status'] == 'ok'
    assert result['bounded'] is True
    assert result['finite'] is True
    assert any(item['flag'] == flag for item in result['prediction_flags'])
    assert result['refinement_error_estimate_o2_mol'] >= 0.0
    assert sim.atom_ledger.mol_by_account() == before
    assert len(sim.atom_ledger.transitions) == transitions_before


def test_exponential_interface_root_miss_is_predicted_and_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = _failure_fixture(monkeypatch)
    original_root = sim._oxygen_finite_interface_root

    def root_without_convergence(**kwargs: Any) -> dict[str, Any]:
        result = original_root(**kwargs)
        result['interface_root_converged'] = False
        return result

    monkeypatch.setattr(
        sim,
        "_oxygen_finite_interface_root",
        root_without_convergence,
    )
    _assert_shadow_prediction_keeps_ledger(
        sim,
        flag='oxygen_exchange_root_tolerance_unmet',
    )


def test_exponential_m2_activity_failure_is_predicted_and_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = _m2_ideal_fixture(monkeypatch)
    _finite_gas_film(sim)
    sim._overhead_headspace_config["enabled"] = True
    sim.melt.melt_surface_area_m2 = 1.0
    sim.overhead.headspace_temperature_K = 2000.0
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": 1.0},
        source="M2 fixed-point failure fixture",
        material_origin="feedstock",
    )
    sim._headspace_ledger_pO2_bar_from_o2_mol = lambda _n: 0.02 / 1.0e5
    sim._headspace_transport_pO2_bar_from_ledger = (
        lambda *_args, **_kwargs: 0.02 / 1.0e5
    )
    sim._headspace_floor_o2_mol = lambda: 0.0
    monkeypatch.setattr(
        sim,
        "_fe_saturation_bound_fO2_log",
        lambda **_kwargs: None,
    )
    _assert_shadow_prediction_keeps_ledger(
        sim,
        flag='oxygen_exchange_activity_fixed_point_nonconverged',
    )


def test_m2_unavailable_surface_activity_completes_full_hour_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = _m2_ideal_fixture(monkeypatch)
    _finite_gas_film(sim)
    sim._overhead_headspace_config["enabled"] = True
    sim.melt.melt_surface_area_m2 = 1.0
    sim.overhead.headspace_temperature_K = 2000.0
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": 1.0},
        source="M2 full-hour unavailable-activity fixture",
        material_origin="feedstock",
    )
    gas_pressure_bar = 0.02 / 1.0e5
    sim._headspace_ledger_pO2_bar_from_o2_mol = lambda _n: gas_pressure_bar
    sim._headspace_transport_pO2_bar_from_ledger = (
        lambda *_args, **_kwargs: gas_pressure_bar
    )
    sim._headspace_floor_o2_mol = lambda: 0.0
    monkeypatch.setattr(
        sim,
        "_fe_saturation_bound_fO2_log",
        lambda **_kwargs: None,
    )
    exchange_ledger: dict[str, Any] = {}
    apply_exchange = sim._apply_oxygen_reservoir_exchange

    def observe_exchange(**kwargs: Any) -> Any:
        exchange_ledger["before"] = sim.atom_ledger.mol_by_account()
        result = apply_exchange(**kwargs)
        exchange_ledger["after"] = sim.atom_ledger.mol_by_account()
        return result

    monkeypatch.setattr(sim, "_apply_oxygen_reservoir_exchange", observe_exchange)
    drift_before = sim.atom_ledger.element_atom_drift_report()

    snapshot = sim._step_one_hour()

    assert snapshot is not None
    reservoir = sim.melt.oxygen_reservoir
    shadow = reservoir.shadow_oxygen_transfer
    assert shadow["status"] == "ok"
    assert shadow["transfer_o2_mol"] == 0.0
    assert reservoir.exchange_o2_mol == 0.0
    assert reservoir.interface_pO2_bar == pytest.approx(gas_pressure_bar)
    assert (
        reservoir.interface_pO2_limiting_regime
        == "surface_activity_unavailable"
    )
    assert any(
        flag["flag"] == "oxygen_exchange_activity_fixed_point_nonconverged"
        for flag in shadow["prediction_flags"]
    )
    assert any(
        flag["flag"] == "surface_activity_unavailable"
        for flag in shadow["prediction_flags"]
    )
    assert exchange_ledger["after"] == exchange_ledger["before"]
    drift_after = sim.atom_ledger.element_atom_drift_report()
    for report_key in (
        "accepted_transition_residual_mol_atoms",
        "whole_run_boundary_residual_mol_atoms",
    ):
        for element, value in drift_after[report_key].items():
            assert value == pytest.approx(
                drift_before[report_key][element], abs=5.0e-12
            )


def test_exponential_refinement_exhaustion_is_predicted_and_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = _failure_fixture(monkeypatch)
    original_root = sim._oxygen_finite_interface_root
    call_count = 0

    def alternating_pressure_root(**kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        root = original_root(**kwargs)
        factor = 10.0 if call_count % 3 == 1 else 0.1
        root["interface_pO2_bar"] *= factor
        return root

    monkeypatch.setattr(
        sim,
        "_oxygen_finite_interface_root",
        alternating_pressure_root,
    )
    drift_before = sim.atom_ledger.element_atom_drift_report()
    reservoir = sim._apply_oxygen_reservoir_exchange()
    shadow = reservoir.shadow_oxygen_transfer
    assert shadow['status'] == 'ok'
    assert shadow['bounded'] is True
    assert any(
        item['flag'] == 'oxygen_exchange_refinement_exhausted'
        for item in shadow['prediction_flags']
    )
    assert shadow['refinement_error_estimate_o2_mol'] > 0.0
    assert abs(reservoir.exchange_o2_mol) > OXYGEN_RESERVOIR_NOOP_MOL
    drift_after = sim.atom_ledger.element_atom_drift_report()
    for report_key in (
        'accepted_transition_residual_mol_atoms',
        'whole_run_boundary_residual_mol_atoms',
    ):
        for element, value in drift_after[report_key].items():
            assert value == pytest.approx(
                drift_before[report_key][element], abs=5.0e-12
            )


def test_exponential_e0_returns_without_interface_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = _failure_fixture(monkeypatch)
    monkeypatch.setattr(
        sim,
        "_oxygen_interface_gas_side_k_m_s",
        lambda _T_K: (math.inf, "hard vacuum fixture"),
    )

    def forbidden_root(**_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("E0 must return before the interface root")

    monkeypatch.setattr(sim, "_oxygen_finite_interface_root", forbidden_root)
    result = sim._oxygen_shadow_transfer(dt_s=3600.0)
    assert result["status"] == "hard_vacuum_no_passive_exchange"
    assert result["interface_evaluations"] == 0
    assert result["amount_bisections"] == 0
