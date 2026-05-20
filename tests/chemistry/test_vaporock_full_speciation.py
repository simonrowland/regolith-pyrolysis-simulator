from __future__ import annotations

import importlib
import json
import types
import warnings

import pytest

import simulator.melt_backend.vaporock as vaporock_module
from engines.vaporock import VapoRockDiagnostics, VapoRockProvider
from simulator.melt_backend.vaporock import VapoRockBackend


def _install_fake_vaporock(monkeypatch, fake_module) -> None:
    def fake_import_module(name):
        if name == "vaporock":
            return fake_module
        raise ImportError(name)

    monkeypatch.setattr(
        vaporock_module.importlib,
        "import_module",
        fake_import_module,
    )


def test_adapter_attaches_unfiltered_full_speciation(monkeypatch):
    class FakeSystem:
        def set_melt_comp(self, composition):
            self.composition = dict(composition)

        def eval_gas_abundances(self, temperature, log_fO2):
            return {
                "Na(g)": -2.0,
                "SiO(g)": -6.0,
                "O2(g)": -9.0,
                "Si2(g)": -12.0,
                "Al2O2(g)": -13.0,
                "SiO2(g)": -14.0,
            }

    _install_fake_vaporock(monkeypatch, types.SimpleNamespace(System=FakeSystem))

    backend = VapoRockBackend()
    assert backend.initialize({})
    result = backend.equilibrate(
        1600.0,
        composition_mol={"SiO2": 1.0, "Na2O": 0.1, "Al2O3": 0.1},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    full = getattr(result, "vaporock_full_speciation_Pa", {})
    assert full["O2"] == pytest.approx(1.0e-4)
    assert full["Si2"] == pytest.approx(1.0e-7)
    assert full["Al2O2"] == pytest.approx(1.0e-8)
    assert full["SiO2_gas"] == pytest.approx(1.0e-9)


def test_provider_keeps_evaporation_filter_byte_identical():
    filtered_before = {"Na": 100.0, "K": 10.0, "SiO": 0.25}
    full_speciation = {
        **filtered_before,
        "O2": 1.0e-4,
        "Si2": 1.0e-7,
        "SiO2_gas": 1.0e-9,
    }
    equilibrium = types.SimpleNamespace(
        vapor_pressures_Pa=full_speciation,
        vaporock_full_speciation_Pa=full_speciation,
        warnings=(),
        status="ok",
    )

    diagnostics = VapoRockProvider._project_equilibrium(
        equilibrium,
        pO2_bar=1e-9,
        mode="system_eval_gas_abundances",
        engine_version="test",
        allowed_species=frozenset(filtered_before),
    )

    encoded_before = json.dumps(
        filtered_before,
        sort_keys=True,
        separators=(",", ":"),
    )
    encoded_after = json.dumps(
        diagnostics.vapor_pressures_Pa,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert encoded_after == encoded_before

    full = diagnostics.vaporock_full_speciation_Pa
    assert full["O2"] == pytest.approx(1.0e-4)
    assert full["Si2"] == pytest.approx(1.0e-7)
    assert full["SiO2_gas"] == pytest.approx(1.0e-9)
    assert "O2" not in diagnostics.vapor_pressures_Pa


def test_vaporock_diagnostic_payload_round_trips_full_speciation():
    diagnostics = VapoRockDiagnostics(
        vapor_pressures_Pa={"Na": 100.0},
        vaporock_full_speciation_Pa={
            "Na": 100.0,
            "O2": 1.0e-4,
            "SiO2_gas": 1.0e-9,
        },
        activities={},
        pO2_bar=1e-9,
        mode="system_eval_gas_abundances",
        engine_version="test",
        backend_status="ok",
    )

    payload = diagnostics.as_diagnostic()
    assert payload["vapor_pressures_Pa"] == {"Na": 100.0}
    assert payload["vaporock_full_speciation_Pa"]["O2"] == pytest.approx(1.0e-4)
    assert payload["vaporock_full_speciation_Pa"]["SiO2_gas"] == pytest.approx(
        1.0e-9
    )


def test_installed_vaporock_full_speciation_has_structural_tail():
    if importlib.util.find_spec("vaporock") is None:
        pytest.skip("VapoRock optional dependency unavailable")

    backend = VapoRockBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        available = backend.initialize({})
    if not available:
        pytest.skip("VapoRock optional dependency unavailable")

    result = backend.equilibrate(
        1600.0,
        composition_kg={
            "SiO2": 49.0,
            "TiO2": 2.0,
            "Al2O3": 15.0,
            "FeO": 10.0,
            "MgO": 8.0,
            "CaO": 10.0,
            "Na2O": 3.0,
            "K2O": 1.0,
        },
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )
    if result.status != "ok":
        pytest.skip(f"VapoRock did not converge: {result.status}")

    full = getattr(result, "vaporock_full_speciation_Pa", {})
    assert len(full) >= 20
    assert "O2" in full
    assert "SiO2_gas" in full
    assert any(species in full for species in ("Si2", "Al2O2", "Na2", "K2"))

