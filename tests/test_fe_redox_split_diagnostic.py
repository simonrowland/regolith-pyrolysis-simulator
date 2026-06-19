from __future__ import annotations

import copy
import importlib
from pathlib import Path
from typing import Any

import pytest
import yaml

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
    assert split["fO2_log"] == pytest.approx(sim._compute_intrinsic_melt_fO2())

    default_summary = build_per_hour_summary(sim, snapshot)
    assert "fe_redox_split" not in default_summary

    diagnostic_summary = build_per_hour_summary(
        sim,
        snapshot,
        include_fe_redox_split=True,
    )
    assert diagnostic_summary["fe_redox_split"]["fe3_over_sigma_fe"] == pytest.approx(
        split["fe3_over_sigma_fe"],
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
