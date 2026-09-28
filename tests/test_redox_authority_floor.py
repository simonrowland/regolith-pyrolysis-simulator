"""Melt fO2 authority when the ferric ratio is only the Kress floor."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
import yaml

from simulator.core import PyrolysisSimulator
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
    sim.load_batch("redox_authority_case", mass_kg=1.0)
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
