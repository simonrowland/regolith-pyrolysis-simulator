"""Side-effect-free projected MAGEMin liquid-fraction diagnostic tests."""

from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from simulator.chemistry.kernel import ChemistryIntent
from simulator.reduced_real_determinism import canonical_json_bytes
from simulator.state import Atmosphere, CampaignPhase, OXIDE_SPECIES
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


def test_projection_counts_historical_metal_mass_toward_dropped_mass_cap():
    allowed = magemin_module.project_magemin_diagnostic_composition({
        "SiO2": 99.0,
        "Fe": 1.0,
    })
    assert allowed.dropped_species_mass_fractions == {"Fe": 0.01}

    with pytest.raises(
        magemin_module.MAGEMinDiagnosticProjectionRefusal,
        match="exceeds",
    ) as excessive:
        magemin_module.project_magemin_diagnostic_composition({
            "SiO2": 99.0,
            "Fe": 3.0,
        })
    assert excessive.value.total_dropped_fraction == pytest.approx(3 / 102)


class _GridBackend:
    instances = []
    calls = []
    fail = False

    def __init__(self):
        self.__class__.instances.append(self)

    def initialize(self, _config):
        return True

    def is_available(self):
        return True

    def find_liquidus_solidus(self, **kwargs):
        self.__class__.calls.append(kwargs)
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
    _GridBackend.calls = []
    _GridBackend.fail = False
    monkeypatch.setattr(magemin_module, "MAGEMinBackend", _GridBackend)

    first = magemin_module.projected_engine_liquid_fraction_diagnostic(
        composition_kg=_diagnostic_input(),
        temperature_C=700.0,
        fO2_log=-9.0,
        pressure_bar=0.2,
        backend_config={"warm_worker": False},
    )
    warmer = magemin_module.projected_engine_liquid_fraction_diagnostic(
        composition_kg=_diagnostic_input(),
        temperature_C=1300.0,
        fO2_log=-9.0,
        pressure_bar=0.2,
        backend_config={"warm_worker": False},
    )

    assert first["status"] == warmer["status"] == "ok"
    assert first["fraction"] == pytest.approx(0.3)
    assert warmer["fraction"] == pytest.approx(0.8)
    assert first["fraction"] != warmer["fraction"]
    assert first["pressure_bar"] == warmer["pressure_bar"] == 0.2
    assert len(_GridBackend.instances) == 1
    assert _GridBackend.calls[0]["pressure_bar"] == 0.2

    changed_pressure = magemin_module.projected_engine_liquid_fraction_diagnostic(
        composition_kg=_diagnostic_input(),
        temperature_C=1300.0,
        fO2_log=-9.0,
        pressure_bar=0.4,
        backend_config={"warm_worker": False},
    )
    assert changed_pressure["status"] == "ok"
    assert changed_pressure["pressure_bar"] == 0.4
    assert len(_GridBackend.instances) == 2
    assert _GridBackend.calls[1]["pressure_bar"] == 0.4

    changed_trace = _diagnostic_input()
    changed_trace["NiO"] += 1.0e-9
    changed = magemin_module.projected_engine_liquid_fraction_diagnostic(
        composition_kg=changed_trace,
        temperature_C=1300.0,
        fO2_log=-9.0,
        pressure_bar=0.2,
        backend_config={"warm_worker": False},
    )
    assert changed["status"] == "ok"
    assert len(_GridBackend.instances) == 3


def test_diagnostic_converts_solver_failure_to_typed_status(monkeypatch):
    magemin_module._projected_engine_liquid_fraction_grid.cache_clear()
    _GridBackend.instances = []
    _GridBackend.calls = []
    _GridBackend.fail = True
    monkeypatch.setattr(magemin_module, "MAGEMinBackend", _GridBackend)

    diagnostic = magemin_module.projected_engine_liquid_fraction_diagnostic(
        composition_kg=_diagnostic_input(),
        temperature_C=1100.0,
        fO2_log=-9.0,
        pressure_bar=0.2,
        backend_config={"warm_worker": False},
    )

    assert diagnostic["status"] == "solve_failed"
    assert diagnostic["fraction"] is None


def _run_real_ticks_with_diagnostic(
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
    if diagnostic_enabled:
        config["projected_engine_liquid_fraction_diagnostic"] = True
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
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.pO2_mbar = 1.5
    sim.melt.p_total_mbar = 1.5
    sim._overhead_headspace_config["enabled"] = True
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -8.0
    sim._sync_oxygen_reservoir_mirror()
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

    from simulator.runner import build_per_hour_summary

    def run_three_ticks():
        physics = []
        for _ in range(3):
            snapshot = sim.step()
            gate = snapshot.liquid_fraction_gate["evaporation"]
            assert gate["solidus_T_C"] < 1100.0 < gate["liquidus_T_C"]
            assert gate["liquid_fraction"] > 0.0
            assert sum(snapshot.evap_flux.species_kg_hr.values()) > 0.0
            assert snapshot.oxygen_reservoir["exchange_o2_mol"] != 0.0
            summary = build_per_hour_summary(sim, snapshot)
            physics.append({
                "ledger": canonical_json_bytes({
                    "accounts": sim.atom_ledger.mol_by_account(),
                    "transitions": repr(tuple(sim.atom_ledger.transitions)),
                }),
                "fO2_log": float(
                    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
                ).hex(),
                "oxygen_transfers": canonical_json_bytes(
                    snapshot.oxygen_reservoir
                ),
                "flux": canonical_json_bytes(snapshot.evap_flux.__dict__),
                "summary": summary,
            })
        return physics

    if diagnostic_enabled:
        # Keep the real physics backend instance, while making the separate
        # projected worker cheap enough for three consecutive cache misses.
        with monkeypatch.context() as patch:
            patch.setattr(magemin_module, "MAGEMinBackend", _GridBackend)
            physics = run_three_ticks()
    else:
        physics = run_three_ticks()
    backend.close()
    return physics


def test_diagnostic_is_neutral_across_three_real_exchange_ticks(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    magemin_module._projected_engine_liquid_fraction_grid.cache_clear()
    _GridBackend.instances = []
    _GridBackend.calls = []
    _GridBackend.fail = False
    diagnostic_on = _run_real_ticks_with_diagnostic(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        monkeypatch=monkeypatch,
        diagnostic_enabled=True,
    )
    diagnostic_off = _run_real_ticks_with_diagnostic(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        monkeypatch=monkeypatch,
        diagnostic_enabled=False,
    )

    assert len(diagnostic_on) == len(diagnostic_off) == 3
    assert len(_GridBackend.instances) == 3
    for on_tick, off_tick in zip(diagnostic_on, diagnostic_off):
        on_summary = deepcopy(on_tick["summary"])
        off_summary = deepcopy(off_tick["summary"])
        on_projected = on_summary["oxygen_reservoir"][
            "liquid_fraction_gate"
        ].pop("projected_engine_liquid_fraction")
        assert "projected_engine_liquid_fraction" not in off_summary[
            "oxygen_reservoir"]["liquid_fraction_gate"]
        assert on_projected["provider"] == "magemin"
        assert on_projected["pressure_bar"] == 0.0015
        assert on_tick["ledger"] == off_tick["ledger"]
        assert on_tick["fO2_log"] == off_tick["fO2_log"]
        assert on_tick["oxygen_transfers"] == off_tick["oxygen_transfers"]
        assert on_tick["flux"] == off_tick["flux"]
        assert json.dumps(on_summary, sort_keys=True) == json.dumps(
            off_summary, sort_keys=True
        )


def test_projected_diagnostic_is_default_off_and_keeps_runner_output_unchanged(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    config = deepcopy(setpoints_data)
    config["chemistry_kernel"] = {
        **config.get("chemistry_kernel", {}),
        "liquid_fraction_provider": "magemin",
    }
    config["freeze_gate"] = {**config.get("freeze_gate", {}), "enabled": True}
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
    sim._register_freeze_gate_liquid_fraction_providers()
    snapshot = sim.step()

    def fail_if_solved(**_kwargs):
        raise AssertionError("default-off diagnostic must not run")

    from simulator.runner import build_per_hour_summary

    monkeypatch.setattr(
        magemin_module,
        "projected_engine_liquid_fraction_diagnostic",
        fail_if_solved,
    )
    implicit_default = build_per_hour_summary(sim, snapshot)
    sim.setpoints["projected_engine_liquid_fraction_diagnostic"] = False
    explicit_default = build_per_hour_summary(sim, snapshot)
    assert "projected_engine_liquid_fraction" not in implicit_default[
        "oxygen_reservoir"]["liquid_fraction_gate"]
    assert canonical_json_bytes(implicit_default) == canonical_json_bytes(
        explicit_default
    )


def test_runner_projection_uses_complete_historical_melt_and_pressure(monkeypatch):
    from simulator.runner import _projected_engine_liquid_fraction_for_snapshot
    from simulator.state import HourSnapshot

    captured = []

    def project(**kwargs):
        captured.append(kwargs)
        return {
            "status": "ok",
            "fraction": 0.5,
        }

    class _AvailableBackend:
        _config = {"warm_worker": False}

        def is_available(self):
            return True

    backend = _AvailableBackend()
    provider = SimpleNamespace(_backend=backend)
    sim = SimpleNamespace(
        setpoints={"projected_engine_liquid_fraction_diagnostic": True},
        _chem_registry=SimpleNamespace(
            fallback_for=lambda _intent: provider,
        ),
    )
    monkeypatch.setattr(
        magemin_module,
        "projected_engine_liquid_fraction_diagnostic",
        project,
    )

    for metallic_iron_kg in (1.0, 3.0):
        snapshot = HourSnapshot(
            liquid_fraction_gate={"provider": "magemin"},
            melt_mass_kg=100.0,
            composition_wt_pct={"SiO2": 100.0},
        )
        snapshot._magemin_diagnostic_composition_kg = {
            "SiO2": 99.0,
            "Fe": metallic_iron_kg,
        }
        snapshot._magemin_diagnostic_pressure_bar = 0.75
        _projected_engine_liquid_fraction_for_snapshot(sim, snapshot)

    assert captured[0]["composition_kg"] == {"SiO2": 99.0, "Fe": 1.0}
    assert captured[0]["pressure_bar"] == 0.75
    with pytest.raises(
        magemin_module.MAGEMinDiagnosticProjectionRefusal,
        match="exceeds",
    ):
        magemin_module.project_magemin_diagnostic_composition(
            captured[1]["composition_kg"]
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
