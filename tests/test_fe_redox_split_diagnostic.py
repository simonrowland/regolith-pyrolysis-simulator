from __future__ import annotations

import copy
import importlib
from pathlib import Path
from typing import Any

import pytest
import yaml

from simulator.accounting import resolve_species_formula
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend
from simulator.runner import build_per_hour_summary
from simulator.state import CampaignPhase


ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / "data" / name).read_text())


def _make_sim(feedstock_id: str, *, temperature_C: float = 1600.0) -> PyrolysisSimulator:
    setpoints = _load_yaml("setpoints.yaml")
    setpoints.setdefault("chemistry_kernel", {})["allow_fallback_vapor"] = True
    sim = PyrolysisSimulator(
        StubBackend(),
        setpoints,
        _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
    )
    additives = {"C": 30.0} if feedstock_id == "mars_basalt" else {}
    sim.load_batch(feedstock_id, mass_kg=1000.0, additives_kg=additives)
    sim.melt.temperature_C = temperature_C
    sim.melt.fO2_log = sim._compute_intrinsic_melt_fO2()
    return sim


def test_fe_redox_split_snapshot_field_is_diagnostic_only() -> None:
    sim = _make_sim("lunar_mare_low_ti")
    before_inventory = copy.deepcopy(sim.inventory)
    before_ledger = sim.atom_ledger.mol_by_account()
    before_fO2 = sim.melt.fO2_log

    direct = sim._compute_fe_redox_split_diagnostic()

    assert sim.inventory == before_inventory
    assert sim.atom_ledger.mol_by_account() == before_ledger
    assert sim.melt.fO2_log == pytest.approx(before_fO2)
    assert direct["diagnostic_only"] is True

    sim.start_campaign(CampaignPhase.C0)
    snapshot = sim.step()
    split = snapshot.fe_redox_split

    assert split["status"] == "ok"
    assert split["source"] in {
        "PySulfSat.convert_fo2_to_fe_partition:Kress1991",
        "inline:Kress-Carmichael1991",
    }
    assert 0.0 <= split["fe3_over_sigma_fe"] <= 1.0
    assert split["ferric_frac"] == pytest.approx(split["fe3_over_sigma_fe"])
    assert (
        split["ferric_frac"]
        + split["ferrous_frac"]
        + split["native_fe_frac"]
    ) == pytest.approx(1.0, abs=1e-12)
    assert split["fO2_log"] == pytest.approx(
        sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    )

    default_summary = build_per_hour_summary(sim, snapshot)
    assert default_summary["fe_redox_split"]["fe3_over_sigma_fe"] == pytest.approx(
        split["fe3_over_sigma_fe"],
    )

    omitted_summary = build_per_hour_summary(
        sim,
        snapshot,
        include_fe_redox_split=False,
    )
    assert "fe_redox_split" not in omitted_summary

    diagnostic_summary = build_per_hour_summary(
        sim,
        snapshot,
        include_fe_redox_split=True,
    )
    assert diagnostic_summary["fe_redox_split"]["fe3_over_sigma_fe"] == pytest.approx(
        split["fe3_over_sigma_fe"],
    )


def test_fe_redox_split_reads_oxygen_reservoir_not_stale_mirror() -> None:
    sim = _make_sim("lunar_mare_low_ti")
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -5.25
    sim.melt.fO2_log = -11.0
    sim.melt.melt_fO2_log = -11.0

    split = sim._compute_fe_redox_split_diagnostic()

    assert split["fO2_log"] == pytest.approx(-5.25)
    assert split["status"] == "ok"


def test_native_fe_saturation_split_routes_fe_to_drain_tap() -> None:
    sim = _make_sim("lunar_mare_low_ti", temperature_C=1600.0)
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -10.0
    sim.melt.fO2_log = -10.0
    sim.melt.melt_fO2_log = -10.0

    before_feo_kg = sim.atom_ledger.kg_by_account("process.cleaned_melt")["FeO"]
    before_tap = dict(sim.atom_ledger.kg_by_account("terminal.drain_tap_material"))
    direct = sim._compute_fe_redox_split_diagnostic()

    assert direct["native_fe_saturation"] is True
    assert direct["native_fe_frac"] > 0.0
    assert before_tap == {}

    sim._apply_native_fe_saturation_split()

    after_melt = sim.atom_ledger.kg_by_account("process.cleaned_melt")
    tap_mol = sim.atom_ledger.mol_by_account("terminal.drain_tap_material")
    overhead_mol = sim.atom_ledger.mol_by_account("process.overhead_gas")
    train_mol = sim.atom_ledger.mol_by_account("process.condensation_train")
    wall01_mol = sim.atom_ledger.mol_by_account(
        "process.wall_deposit_segment_stage_0_to_stage_1"
    )
    tap = sim.atom_ledger.kg_by_account("terminal.drain_tap_material")
    overhead = sim.atom_ledger.kg_by_account("process.overhead_gas")
    fe_mm = resolve_species_formula(
        "Fe", sim.species_formula_registry).molar_mass_kg_per_mol()
    feo_mm = resolve_species_formula(
        "FeO", sim.species_formula_registry).molar_mass_kg_per_mol()
    o2_mm = resolve_species_formula(
        "O2", sim.species_formula_registry).molar_mass_kg_per_mol()
    routed_vapor_mol = (
        overhead_mol.get("Fe", 0.0)
        + train_mol.get("Fe", 0.0)
        + wall01_mol.get("Fe", 0.0)
    )
    split_fe_mol = tap_mol["Fe"] + routed_vapor_mol

    assert tap_mol["Fe"] > 0.0
    assert routed_vapor_mol > 0.0
    assert tap["Fe"] == pytest.approx(tap_mol["Fe"] * fe_mm)
    assert overhead.get("Fe", 0.0) == pytest.approx(
        overhead_mol.get("Fe", 0.0) * fe_mm
    )
    assert sim.train.stages[1].collected_kg.get("Fe", 0.0) > 0.0
    assert sim.atom_ledger.kg_by_account(
        "process.wall_deposit_segment_stage_0_to_stage_1"
    ).get("Fe", 0.0) > 0.0
    assert tap["Fe"] + routed_vapor_mol * fe_mm == pytest.approx(
        split_fe_mol * fe_mm,
    )
    assert sim.inventory.drain_tap_kg["Fe"] == pytest.approx(tap["Fe"])
    assert before_feo_kg - after_melt["FeO"] == pytest.approx(
        split_fe_mol * feo_mm,
    )
    assert overhead["O2"] == pytest.approx(0.5 * split_fe_mol * o2_mm)
    partition = sim._compute_fe_redox_split_diagnostic()["native_fe_partition"]
    assert partition["native_fe_pool_mol"] == pytest.approx(split_fe_mol)
    assert partition["native_fe_tap_mol"] == pytest.approx(tap_mol["Fe"])
    assert partition["native_fe_vapor_mol"] == pytest.approx(routed_vapor_mol)
    assert sim._make_snapshot().mass_balance_error_pct == pytest.approx(
        0.0,
        abs=5e-12,
    )


def _vaporock_chemistry_or_skip() -> Any:
    try:
        chemistry = importlib.import_module("vaporock.chemistry")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"VapoRock unavailable: {exc!r}")
    if not hasattr(chemistry, "reset_Fe_redox"):
        pytest.skip("VapoRock Fe-redox helper reset_Fe_redox unavailable")
    if not hasattr(chemistry, "OXIDE_MOLWT"):
        pytest.skip("VapoRock oxide molecular-weight table unavailable")
    return chemistry


def _fe3_over_total_fe_from_vaporock_split(
    chemistry: Any,
    comp_wt: dict[str, float],
    fe2o3_over_feo_molar: float,
) -> float:
    split = chemistry.reset_Fe_redox(comp_wt, fe2o3_over_feo_molar)
    molwt = chemistry.OXIDE_MOLWT
    feo_mol = float(split.get("FeO", 0.0)) / float(molwt["FeO"])
    fe2o3_mol = float(split.get("Fe2O3", 0.0)) / float(molwt["Fe2O3"])
    total_fe_mol = feo_mol + 2.0 * fe2o3_mol
    if total_fe_mol <= 0.0:
        return 0.0
    return (2.0 * fe2o3_mol) / total_fe_mol


def test_kress91_split_matches_vaporock_fe_redox_helper() -> None:
    chemistry = _vaporock_chemistry_or_skip()

    # VapoRock currently exposes the Fe-redox split helper, not a public
    # fO2->split API. Parity therefore checks that the Kress91 molar ratio
    # emitted by the diagnostic produces the same Fe3+/SigmaFe split through
    # VapoRock's own FeO/Fe2O3 partitioning helper.
    for feedstock_id in ("lunar_mare_low_ti", "mars_basalt"):
        sim = _make_sim(feedstock_id)
        diagnostic = sim._compute_fe_redox_split_diagnostic()
        vaporock_fe3 = _fe3_over_total_fe_from_vaporock_split(
            chemistry,
            sim._melt_oxide_wt_pct(),
            diagnostic["fe2o3_over_feo_molar"],
        )
        assert vaporock_fe3 == pytest.approx(
            diagnostic["fe3_over_sigma_fe"],
            abs=5e-4,
        ), feedstock_id


def test_kress91_extreme_reducing_fo2_does_not_underflow_crash() -> None:
    """BUG-159: at extreme-reducing fO2 the prior ``10.0 ** fO2_log`` underflowed
    to ``0.0`` and ``math.log(0.0)`` aborted the provider with a domain error.

    The ``a*ln(fO2)`` term is now computed as ``fO2_log * ln(10)`` (algebraically
    exact, the canonical Kress91 form), so an arbitrarily reducing ``fO2_log``
    returns a finite, physical ferric fraction instead of raising.
    """
    from simulator.fe_redox import kress91_fe3_over_sigma_fe

    mol_fractions = {
        "Al2O3": 0.08,
        "FeOt": 0.12,
        "CaO": 0.10,
        "Na2O": 0.02,
        "K2O": 0.005,
    }
    kwargs = dict(mol_fractions=mol_fractions, T_K=1873.0, pressure_bar=0.01)

    # Extreme reducing: pre-fix this raised ValueError("math domain error").
    extreme = kress91_fe3_over_sigma_fe(fO2_log=-350.0, **kwargs)
    assert isinstance(extreme, float)
    assert 0.0 <= extreme <= 1.0
    assert extreme == pytest.approx(0.0, abs=1e-6)  # ferric -> 0 at extreme reducing

    # Normal range stays finite/physical, and a more-oxidising fO2 gives a
    # strictly higher ferric fraction (monotonic sanity on the fixed term).
    mild = kress91_fe3_over_sigma_fe(fO2_log=-8.0, **kwargs)
    oxidising = kress91_fe3_over_sigma_fe(fO2_log=-2.0, **kwargs)
    assert 0.0 <= mild <= 1.0
    assert oxidising > mild
