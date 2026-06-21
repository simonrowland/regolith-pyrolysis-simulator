from __future__ import annotations

import importlib
import json
import types
import warnings

import pytest

import simulator.melt_backend.vaporock as vaporock_module
from engines.vaporock import VapoRockDiagnostics, VapoRockProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
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




def _vaporock_po2_request(pO2_bar):
    return IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"SiO2": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=1500.0,
        pressure_bar=1e-6,
        control_inputs={"pO2_bar": pO2_bar},
    )


@pytest.mark.parametrize("pO2_bar", [-1.0, 0.0, 1e-12])
def test_vaporock_provider_rejects_invalid_explicit_transport_po2(pO2_bar):
    request = _vaporock_po2_request(pO2_bar)

    with pytest.raises(ValueError, match="pO2_bar"):
        VapoRockProvider._resolve_pO2_bar(request)
    with pytest.raises(ValueError, match="pO2_bar"):
        VapoRockProvider._resolve_fO2_log(request)


def test_vaporock_provider_rejects_subfloor_request_level_fo2():
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"SiO2": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=1500.0,
        pressure_bar=1e-6,
        fO2_log=-20.0,
        control_inputs={},
    )

    with pytest.raises(ValueError, match="fO2_log=-20.*transport model floor"):
        VapoRockProvider._resolve_pO2_bar(request)
    with pytest.raises(ValueError, match="fO2_log=-20.*transport model floor"):
        VapoRockProvider._resolve_fO2_log(request)


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

    assert result.vapor_pressures_Pa == {}
    assert result.status == "non_authoritative"
    full = getattr(result, "vaporock_full_speciation_Pa", {})
    assert full["O2"] == pytest.approx(1.0e-4)
    assert full["Si2"] == pytest.approx(1.0e-7)
    assert full["Al2O2"] == pytest.approx(1.0e-8)
    assert full["SiO2_gas"] == pytest.approx(1.0e-9)


def test_provider_keeps_authoritative_pressure_dict_empty():
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

    assert json.dumps(diagnostics.vapor_pressures_Pa, sort_keys=True) == "{}"
    full = diagnostics.vaporock_full_speciation_Pa
    assert full["Na"] == pytest.approx(filtered_before["Na"])
    assert full["K"] == pytest.approx(filtered_before["K"])
    assert full["SiO"] == pytest.approx(filtered_before["SiO"])
    assert full["O2"] == pytest.approx(1.0e-4)
    assert full["Si2"] == pytest.approx(1.0e-7)
    assert full["SiO2_gas"] == pytest.approx(1.0e-9)


def test_provider_allowed_species_honors_inactive_consumer_status(
    vapor_pressure_data,
):
    assert (
        vapor_pressure_data["metals"]["Si"]["consumer_status"].lower()
        == "inactive"
    )

    allowed_species = VapoRockProvider._build_allowed_species(
        vapor_pressure_data
    )

    assert "Si" not in allowed_species
    assert {"SiO", "Na", "Fe"} <= allowed_species

    equilibrium = types.SimpleNamespace(
        vapor_pressures_Pa={
            "Si": 1000.0,
            "SiO": 100.0,
            "Na": 10.0,
            "Fe": 1.0,
        },
        vaporock_full_speciation_Pa={
            "Si": 1000.0,
            "SiO": 100.0,
            "Na": 10.0,
            "Fe": 1.0,
        },
        warnings=(),
        status="ok",
    )

    diagnostics = VapoRockProvider._project_equilibrium(
        equilibrium,
        pO2_bar=1e-9,
        mode="system_eval_gas_abundances",
        engine_version="test",
        allowed_species=allowed_species,
    )

    assert diagnostics.vapor_pressures_Pa == {}
    assert diagnostics.vaporock_full_speciation_Pa["Si"] == pytest.approx(
        1000.0
    )
    assert diagnostics.vaporock_full_speciation_Pa["SiO"] == pytest.approx(
        100.0
    )
    assert diagnostics.vaporock_full_speciation_Pa["Na"] == pytest.approx(10.0)
    assert diagnostics.vaporock_full_speciation_Pa["Fe"] == pytest.approx(1.0)


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
    assert payload["vapor_pressures_Pa"] == {}
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
