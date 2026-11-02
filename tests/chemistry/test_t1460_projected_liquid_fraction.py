"""Side-effect-free projected MAGEMin liquid-fraction diagnostic tests."""

from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from simulator.chemistry.kernel import ChemistryIntent
from simulator.reduced_real_determinism import canonical_json_bytes
from simulator.state import CampaignPhase, OXIDE_SPECIES
from tests.chemistry.conftest import _build_sim

import simulator.melt_backend.magemin as magemin_module


_IG_UNSUPPORTED_OXIDES = {"MnO", "P2O5", "NiO", "CoO"}


def _fixture_feed_masses_kg(feedstocks_data: dict) -> dict[str, float]:
    composition = feedstocks_data["lunar_mare_low_ti"]["composition_wt_pct"]
    total_wt_pct = sum(float(value) for value in composition.values())
    return {
        str(species): 1000.0 * float(wt_pct) / total_wt_pct
        for species, wt_pct in composition.items()
    }


def test_projection_reports_exact_fixture_dropped_mass_fractions(
    feedstocks_data,
):
    masses_kg = _fixture_feed_masses_kg(feedstocks_data)
    total_mass_kg = sum(masses_kg.values())
    expected_dropped = {
        species: mass_kg / total_mass_kg
        for species, mass_kg in sorted(masses_kg.items())
        if species not in OXIDE_SPECIES
        or species in _IG_UNSUPPORTED_OXIDES
    }

    projection = magemin_module.project_magemin_diagnostic_composition(
        dict(masses_kg)
    )

    assert projection.dropped_species_mass_fractions == expected_dropped
    assert projection.dropped_mass_fraction == sum(expected_dropped.values())
    assert projection.kept_oxide_kg == {
        species: mass_kg
        for species, mass_kg in masses_kg.items()
        if species in OXIDE_SPECIES
        and species not in _IG_UNSUPPORTED_OXIDES
    }


def test_projection_refuses_dropped_mass_above_cap_and_all_dropped_input():
    with pytest.raises(
        magemin_module.MAGEMinDiagnosticProjectionRefusal,
        match="exceeds",
    ) as excessive:
        magemin_module.project_magemin_diagnostic_composition({
            "SiO2": 97.0,
            "P2O5": 3.0,
        })
    assert excessive.value.total_dropped_fraction > 0.02

    with pytest.raises(
        magemin_module.MAGEMinDiagnosticProjectionRefusal,
        match="exceeds",
    ) as all_dropped:
        magemin_module.project_magemin_diagnostic_composition({"P2O5": 1.0})
    assert all_dropped.value.total_dropped_fraction == 1.0


class _GridBackend:
    instances = []
    fail = False

    def __init__(self):
        self.__class__.instances.append(self)

    def initialize(self, _config):
        return True

    def is_available(self):
        return True

    def find_liquidus_solidus(self, **_kwargs):
        if self.__class__.fail:
            raise RuntimeError("injected projected diagnostic solve failure")
        return SimpleNamespace(
            status="ok",
            samples=(
                SimpleNamespace(temperature_C=400.0, frac_M=0.0),
                SimpleNamespace(temperature_C=1000.0, frac_M=0.6),
                SimpleNamespace(temperature_C=1600.0, frac_M=1.0),
                SimpleNamespace(temperature_C=2200.0, frac_M=1.0),
            ),
        )

    def close(self):
        return None


def _diagnostic_input() -> dict[str, float]:
    return {"SiO2": 10.0, "FeO": 7.0, "NiO": 0.001}


def test_diagnostic_reuses_grid_but_interpolates_each_temperature_and_keys_trace(
    monkeypatch,
):
    magemin_module._projected_engine_liquid_fraction_grid.cache_clear()
    _GridBackend.instances = []
    _GridBackend.fail = False
    monkeypatch.setattr(magemin_module, "MAGEMinBackend", _GridBackend)

    first = magemin_module.projected_engine_liquid_fraction_diagnostic(
        composition_kg=_diagnostic_input(),
        temperature_C=700.0,
        fO2_log=-9.0,
        backend_config={"warm_worker": False},
    )
    warmer = magemin_module.projected_engine_liquid_fraction_diagnostic(
        composition_kg=_diagnostic_input(),
        temperature_C=1300.0,
        fO2_log=-9.0,
        backend_config={"warm_worker": False},
    )

    assert first["status"] == warmer["status"] == "ok"
    assert first["fraction"] == pytest.approx(0.3)
    assert warmer["fraction"] == pytest.approx(0.8)
    assert first["fraction"] != warmer["fraction"]
    assert len(_GridBackend.instances) == 1

    changed_trace = _diagnostic_input()
    changed_trace["NiO"] += 1.0e-9
    changed = magemin_module.projected_engine_liquid_fraction_diagnostic(
        composition_kg=changed_trace,
        temperature_C=1300.0,
        fO2_log=-9.0,
        backend_config={"warm_worker": False},
    )
    assert changed["status"] == "ok"
    assert len(_GridBackend.instances) == 2


def test_diagnostic_converts_solver_failure_to_typed_status(monkeypatch):
    magemin_module._projected_engine_liquid_fraction_grid.cache_clear()
    _GridBackend.instances = []
    _GridBackend.fail = True
    monkeypatch.setattr(magemin_module, "MAGEMinBackend", _GridBackend)

    diagnostic = magemin_module.projected_engine_liquid_fraction_diagnostic(
        composition_kg=_diagnostic_input(),
        temperature_C=1100.0,
        fO2_log=-9.0,
        backend_config={"warm_worker": False},
    )

    assert diagnostic["status"] == "solve_failed"
    assert diagnostic["fraction"] is None


def _run_real_tick_with_diagnostic(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    *,
    monkeypatch,
    diagnostic_enabled: bool,
):
    config = deepcopy(setpoints_data)
    config["chemistry_kernel"] = {
        **config.get("chemistry_kernel", {}),
        "liquid_fraction_provider": "magemin",
    }
    config["freeze_gate"] = {
        **config.get("freeze_gate", {}),
        "enabled": True,
    }
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        config,
    )
    sim.start_campaign(CampaignPhase.C2A)
    sim.melt.temperature_C = 1100.0
    sim.melt.target_temperature_C = 1100.0
    sim._update_temperature = lambda: None
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": 0.1},
        source="t1460 real-path exchange fixture",
        material_origin="feedstock",
    )

    sim._register_freeze_gate_liquid_fraction_providers()
    provider = sim._chem_registry.fallback_for(
        ChemistryIntent.GATE_LIQUID_FRACTION
    )
    backend = provider._ensure_backend()
    assert backend is not None and backend.is_available()

    if not diagnostic_enabled:
        monkeypatch.setattr(
            magemin_module,
            "projected_engine_liquid_fraction_diagnostic",
            lambda **_kwargs: None,
        )

    snapshot = sim.step()
    gate = snapshot.liquid_fraction_gate["evaporation"]
    assert gate["solidus_T_C"] < 1100.0 < gate["liquidus_T_C"]
    assert gate["liquid_fraction"] > 0.0
    assert sum(snapshot.evap_flux.species_kg_hr.values()) > 0.0
    assert snapshot.oxygen_reservoir["exchange_o2_mol"] != 0.0

    from simulator.runner import build_per_hour_summary

    summary = build_per_hour_summary(sim, snapshot)
    physics = {
        "ledger": canonical_json_bytes({
            "accounts": sim.atom_ledger.mol_by_account(),
            "transitions": repr(tuple(sim.atom_ledger.transitions)),
        }),
        "fO2_log": float(sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log).hex(),
        "oxygen_transfers": canonical_json_bytes(
            snapshot.oxygen_reservoir
        ),
        "flux": canonical_json_bytes(
            snapshot.evap_flux.__dict__
        ),
        "summary": summary,
    }
    backend.close()
    return physics


def test_diagnostic_is_neutral_on_real_exchange_and_flux_inside_melting_interval(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    magemin_module._projected_engine_liquid_fraction_grid.cache_clear()
    diagnostic_on = _run_real_tick_with_diagnostic(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        monkeypatch=monkeypatch,
        diagnostic_enabled=True,
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            magemin_module,
            "projected_engine_liquid_fraction_diagnostic",
            lambda **_kwargs: None,
        )
        diagnostic_off = _run_real_tick_with_diagnostic(
            vapor_pressure_data,
            feedstocks_data,
            setpoints_data,
            monkeypatch=patch,
            diagnostic_enabled=False,
        )

    on_summary = deepcopy(diagnostic_on["summary"])
    off_summary = deepcopy(diagnostic_off["summary"])
    on_projected = on_summary["oxygen_reservoir"]["liquid_fraction_gate"].pop(
        "projected_engine_liquid_fraction"
    )
    assert "projected_engine_liquid_fraction" not in off_summary[
        "oxygen_reservoir"]["liquid_fraction_gate"]
    assert on_projected["provider"] == "magemin"
    assert diagnostic_on["ledger"] == diagnostic_off["ledger"]
    assert diagnostic_on["fO2_log"] == diagnostic_off["fO2_log"]
    assert diagnostic_on["oxygen_transfers"] == diagnostic_off["oxygen_transfers"]
    assert diagnostic_on["flux"] == diagnostic_off["flux"]
    assert json.dumps(on_summary, sort_keys=True) == json.dumps(
        off_summary, sort_keys=True
    )


def test_freeze_gate_and_redox_consumers_do_not_read_diagnostic_key():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    key = "projected_engine_liquid_fraction"
    for path in (
        root / "simulator/core.py",
        root / "simulator/evaporation.py",
        root / "simulator/fe_redox.py",
    ):
        assert key not in path.read_text()
