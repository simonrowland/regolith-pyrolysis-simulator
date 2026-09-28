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


def test_absent_fe3_follows_the_gas_not_the_floor_inverse() -> None:
    """R1: FeO remains, Fe3+ is absent, native metal has left."""

    sim = _sim_with_oxides(feo_wt=10.0, fe2o3_wt=0.0, temperature_C=1220.0)
    assert _oxide_mol(sim, "FeO") > 1.0e-6
    assert _oxide_mol(sim, "Fe2O3") == 0.0
    assert sim.atom_ledger.project_account_mol("process.metal_phase").get(
        "Fe", 0.0
    ) == 0.0

    fO2_log = sim._melt_fO2_from_ledger()
    domain = sim._last_redox_domain
    composition = melt_mol_fractions_for_kress91(sim._melt_oxide_wt_pct())
    floor_inverse = kress91_log_fO2_from_fe3_over_sigma_fe(
        fe3_over_sigma_fe=KRESS91_FERRIC_FRACTION_EPSILON,
        mol_fractions=composition,
        T_K=1220.0 + 273.15,
        pressure_bar=0.01,
    )

    assert domain["basis"] == "no_melt_redox_buffer"
    assert "kress91_inverse_not_evaluated" in domain["reason"]
    assert fO2_log == pytest.approx(math.log10(TRANSPORT_PO2_BAR))
    # The floor inverse sits far from the gas.  One dex is the bound; the
    # gap is the open-interval clamp, not a fitted residual.
    assert abs(fO2_log - floor_inverse) > 1.0


def test_depleted_feo_without_fe3_stays_on_the_gas() -> None:
    """R2: after FeO is reduced away, a missing Fe3+ inventory is still not Kress."""

    sim = _sim_with_oxides(feo_wt=1.0e-4, fe2o3_wt=0.0, temperature_C=1400.0)
    feo_mol = _oxide_mol(sim, "FeO")
    assert feo_mol > 0.0
    assert _oxide_mol(sim, "Fe2O3") == 0.0

    fO2_log = sim._melt_fO2_from_ledger()
    composition = melt_mol_fractions_for_kress91(sim._melt_oxide_wt_pct())
    floor_inverse = kress91_log_fO2_from_fe3_over_sigma_fe(
        fe3_over_sigma_fe=KRESS91_FERRIC_FRACTION_EPSILON,
        mol_fractions=composition,
        T_K=1400.0 + 273.15,
        pressure_bar=0.01,
    )

    assert sim._last_redox_domain["basis"] == "no_melt_redox_buffer"
    assert fO2_log == pytest.approx(math.log10(TRANSPORT_PO2_BAR))
    assert abs(fO2_log - floor_inverse) > 1.0


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
        pressure_bar=max(sim.melt.p_total_mbar / 1000.0, 1.0e-9),
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
        pressure_bar=0.01,
    )
    assert unclamped < KRESS91_FERRIC_FRACTION_EPSILON
    before = sim.atom_ledger.mol_by_account()

    diagnostic = sim._apply_fe_redox_respeciation(fO2_log_override=-40.0)

    assert diagnostic["respeciation_status"] == "endpoint_not_a_measurement"
    assert _oxide_mol(sim, "Fe2O3") == 0.0
    assert sim.atom_ledger.mol_by_account() == before
