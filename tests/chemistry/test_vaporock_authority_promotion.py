"""Acceptance tests for builtin vapor pressure plus VapoRock diagnostics.

VAPOR_PRESSURE ledger/runtime authority is builtin Antoine/Ellingham.
VapoRock is retained as a diagnostic shadow: it may produce full gas
speciation, fail to import, or return a non-authoritative empty pressure
surface without blocking the builtin pressure dict consumed by evaporation.
The filename is historical only; VapoRock is diagnostic-only.
"""

from __future__ import annotations

import pathlib
import types
from pathlib import Path
from typing import Any

import pytest
import yaml

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from engines.vaporock import VapoRockDiagnostics, VapoRockProvider
from simulator.chemistry.kernel import ChemistryIntent
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import EquilibriumResult, StubBackend


DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text())


@pytest.fixture(scope="module")
def vapor_pressure_data() -> dict:
    return _load_yaml("vapor_pressures.yaml")


@pytest.fixture(scope="module")
def feedstocks_data() -> dict:
    return _load_yaml("feedstocks.yaml")


@pytest.fixture(scope="module")
def setpoints_data() -> dict:
    return _load_yaml("setpoints.yaml")


def _build_sim(
    vapor_pressure_data: dict,
    feedstocks_data: dict,
    setpoints_data: dict,
    *,
    allow_fallback_vapor: bool = False,
) -> PyrolysisSimulator:
    backend = StubBackend()
    backend.initialize({})
    setpoints = dict(setpoints_data)
    kernel_cfg = dict(setpoints.get("chemistry_kernel", {}) or {})
    kernel_cfg["allow_fallback_vapor"] = bool(allow_fallback_vapor)
    setpoints["chemistry_kernel"] = kernel_cfg
    sim = PyrolysisSimulator(
        backend, setpoints, feedstocks_data, vapor_pressure_data
    )
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    return sim


def _vaporock_shadow(sim: PyrolysisSimulator) -> VapoRockProvider:
    shadows = sim._chem_registry.shadows_for(ChemistryIntent.VAPOR_PRESSURE)
    providers = [p for p in shadows if isinstance(p, VapoRockProvider)]
    assert len(providers) == 1
    return providers[0]


def _force_vaporock_unavailable(sim: PyrolysisSimulator) -> VapoRockProvider:
    provider = _vaporock_shadow(sim)
    cached = getattr(provider, "_backend", None)
    if cached is not None and hasattr(cached, "is_available"):
        cached.is_available = lambda: False  # type: ignore[assignment]
    provider._ensure_backend = lambda: cached  # type: ignore[method-assign]
    return provider


def _force_vaporock_available(sim: PyrolysisSimulator) -> VapoRockProvider:
    provider = _vaporock_shadow(sim)

    class _FakeBackend:
        def is_available(self) -> bool:
            return True

        def get_engine_version(self) -> str:
            return "fake-1.0"

        def equilibrate(self, **_: Any):
            result = EquilibriumResult(
                temperature_C=1500.0,
                pressure_bar=1e-6,
                fO2_log=-9.0,
                liquid_fraction=1.0,
                status="ok",
                vapor_pressures_Pa={
                    "Na": 1234.5,
                    "SiO": 0.0131,
                    "O2": 1e-4,
                    "Si2": 1e-7,
                    "SiO2_gas": 1e-9,
                },
            )
            setattr(
                result,
                "vaporock_full_speciation_Pa",
                dict(result.vapor_pressures_Pa),
            )
            return result

    fake = _FakeBackend()
    provider._backend = fake
    provider._backend_initialised = True
    provider._ensure_backend = lambda: fake  # type: ignore[method-assign]
    return provider


def _force_vaporock_non_authoritative_empty(
    sim: PyrolysisSimulator,
) -> VapoRockProvider:
    provider = _vaporock_shadow(sim)

    class _FakeBackend:
        def is_available(self) -> bool:
            return True

        def get_engine_version(self) -> str:
            return "fake-1.0"

        def equilibrate(self, **_: Any):
            result = EquilibriumResult(
                temperature_C=1500.0,
                pressure_bar=1e-6,
                fO2_log=-9.0,
                liquid_fraction=None,
                phase_assemblage_available=False,
                status="non_authoritative",
                warnings=["diagnostic pressure control is non-authoritative"],
                vapor_pressures_Pa={},
            )
            setattr(
                result,
                "vaporock_full_speciation_Pa",
                {"Na": 1000.0, "O2": 1e-4},
            )
            return result

    fake = _FakeBackend()
    provider._backend = fake
    provider._backend_initialised = True
    provider._ensure_backend = lambda: fake  # type: ignore[method-assign]
    return provider


def _dispatch_vapor(sim: PyrolysisSimulator):
    return sim._chem_kernel.dispatch(
        ChemistryIntent.VAPOR_PRESSURE,
        temperature_C=1500.0,
        pressure_bar=1e-6,
        control_inputs={"pO2_bar": 1e-9},
    )


def _shadow_result(sim: PyrolysisSimulator):
    trace = sim._chem_kernel.planner.shadow_trace
    dispatches = [
        record
        for record in trace
        if record.get("event") == "shadow_dispatch"
        and record.get("provider_id") == "vaporock"
    ]
    assert dispatches
    return dispatches[-1]["result"]


def test_builtin_authority_dispatches_even_when_vaporock_available(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(vapor_pressure_data, feedstocks_data, setpoints_data)
    _force_vaporock_available(sim)

    builtin_dispatches = 0
    original_builtin_dispatch = BuiltinVaporPressureProvider.dispatch

    def _spy(self, request):
        nonlocal builtin_dispatches
        builtin_dispatches += 1
        return original_builtin_dispatch(self, request)

    BuiltinVaporPressureProvider.dispatch = _spy
    try:
        result = _dispatch_vapor(sim)
    finally:
        BuiltinVaporPressureProvider.dispatch = original_builtin_dispatch

    assert builtin_dispatches == 1
    assert result.status == "ok"
    diagnostic = dict(result.diagnostic or {})
    assert diagnostic.get("vapor_pressures_Pa")
    assert "kernel_fallback_used" not in diagnostic
    sources = dict(diagnostic.get("vapor_pressures_source") or {})
    assert sources
    authoritative_species = {"Al", "Ca", "Cr", "Fe", "K", "Mg", "Mn", "Na", "Ti"}
    pseudo_vaporock_species = {"SiO"}
    assert authoritative_species.issubset(sources)
    assert pseudo_vaporock_species.issubset(sources)
    for species in authoritative_species:
        assert sources[species].startswith("builtin_authoritative:")
    for species in pseudo_vaporock_species:
        assert sources[species] == (
            "vaporock_backsolved_curve_fit:"
            "backsolved_vaporock_curve_fit"
        )

    shadow = _shadow_result(sim)
    assert shadow.status == "non_authoritative"
    shadow_diag = dict(shadow.diagnostic or {})
    assert shadow_diag.get("vapor_pressures_Pa") == {}
    full = dict(shadow_diag.get("vaporock_full_speciation_Pa") or {})
    assert full["Na"] == pytest.approx(1234.5)
    assert full["O2"] == pytest.approx(1e-4)
    assert full["Si2"] == pytest.approx(1e-7)


def test_vaporock_full_speciation_stays_out_of_evaporation_flux(
    monkeypatch, vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(vapor_pressure_data, feedstocks_data, setpoints_data)
    _force_vaporock_available(sim)
    sim.melt.temperature_C = 1500.0

    result = sim._get_equilibrium()
    assert result.vapor_pressures_Pa
    assert set(result.vapor_pressures_Pa) != {"Na", "SiO"}
    shadow = _shadow_result(sim)
    assert shadow.status == "non_authoritative"
    shadow_diag = dict(shadow.diagnostic or {})
    assert shadow_diag.get("vapor_pressures_Pa") == {}
    full = dict(shadow_diag.get("vaporock_full_speciation_Pa") or {})
    assert full["Na"] == pytest.approx(1234.5)
    assert full["O2"] == pytest.approx(1e-4)
    assert full["Si2"] == pytest.approx(1e-7)
    assert full["SiO2_gas"] == pytest.approx(1e-9)

    captured: dict[str, dict] = {}
    original_dispatch_only = sim._dispatch_only

    def _spy_dispatch_only(intent, *args, **kwargs):
        if intent == ChemistryIntent.EVAPORATION_FLUX:
            captured["vapor_pressures_Pa"] = dict(
                kwargs["control_inputs"]["vapor_pressures_Pa"]
            )
            return types.SimpleNamespace(
                status="ok",
                diagnostic={"evaporation_flux_kg_hr": {}},
            )
        return original_dispatch_only(intent, *args, **kwargs)

    monkeypatch.setattr(sim, "_dispatch_only", _spy_dispatch_only)
    sim._calculate_evaporation(result)

    assert captured["vapor_pressures_Pa"] == result.vapor_pressures_Pa
    assert "O2" not in captured["vapor_pressures_Pa"]
    assert "Si2" not in captured["vapor_pressures_Pa"]
    assert "SiO2_gas" not in captured["vapor_pressures_Pa"]


def test_vaporock_unavailable_no_flag_still_uses_builtin_authority(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(vapor_pressure_data, feedstocks_data, setpoints_data)
    _force_vaporock_unavailable(sim)

    result = _dispatch_vapor(sim)

    assert result.status == "ok"
    assert dict(result.diagnostic or {}).get("vapor_pressures_Pa")
    errors = [
        record
        for record in sim._chem_kernel.planner.shadow_trace
        if record.get("event") == "shadow_error"
        and record.get("provider_id") == "vaporock"
    ]
    assert errors


def test_allow_fallback_flag_does_not_change_builtin_authority(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        allow_fallback_vapor=True,
    )
    _force_vaporock_available(sim)

    result = _dispatch_vapor(sim)

    assert result.status == "ok"
    diagnostic = dict(result.diagnostic or {})
    assert diagnostic.get("vapor_pressures_Pa")
    assert "kernel_fallback_used" not in diagnostic
    shadow = _shadow_result(sim)
    assert shadow.status == "non_authoritative"
    shadow_diag = dict(shadow.diagnostic or {})
    assert shadow_diag.get("vapor_pressures_Pa") == {}
    assert shadow_diag.get("vaporock_full_speciation_Pa", {}).get(
        "Na"
    ) == pytest.approx(1234.5)


def test_non_authoritative_vaporock_empty_output_does_not_trip_core_guard(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(vapor_pressure_data, feedstocks_data, setpoints_data)
    _force_vaporock_non_authoritative_empty(sim)
    sim.melt.temperature_C = 1500.0

    result = sim._get_equilibrium()

    assert result.vapor_pressures_Pa
    sources = set(result.vapor_pressures_source.values())
    assert "vaporock" not in sources
    assert (
        "vaporock_backsolved_curve_fit:"
        "backsolved_vaporock_curve_fit"
    ) in sources
    assert any(source.startswith("builtin_authoritative") for source in sources)
    diagnostic = dict(sim._last_vapor_pressure_diagnostic or {})
    assert diagnostic.get("vapor_pressures_Pa")
    assert diagnostic.get("vapor_pressures_source")
    shadow = _shadow_result(sim)
    assert shadow.status == "non_authoritative"
    shadow_diag = dict(shadow.diagnostic or {})
    assert shadow_diag.get("vapor_pressures_Pa") == {}
    assert shadow_diag.get("vaporock_full_speciation_Pa", {}).get(
        "Na"
    ) == pytest.approx(1000.0)


def test_capability_summary_reports_builtin_authority_with_vaporock_shadow(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(vapor_pressure_data, feedstocks_data, setpoints_data)

    summary = sim._chem_registry.capability_summary()

    vapor_entry = summary.get(ChemistryIntent.VAPOR_PRESSURE.value)
    assert vapor_entry is not None
    assert vapor_entry["authoritative"] == "builtin-vapor-pressure"
    assert vapor_entry["fallback"] is None
    assert vapor_entry["shadows"] == ("vaporock",)

    expected_builtin_intents = {
        "evaporation_flux": "builtin-evaporation-flux",
        "evaporation_transition": "builtin-evaporation-transition",
        "condensation_route": "builtin-condensation-route",
        "electrolysis_step": "builtin-electrolysis-step",
        "metallothermic_step": "builtin-metallothermic-step",
        "stage0_pretreatment": "builtin-stage0-pretreatment",
        "overhead_gas_equilibrium": "builtin-overhead-gas-equilibrium",
        "overhead_bleed": "builtin-overhead-bleed",
    }
    for intent, expected_provider_id in expected_builtin_intents.items():
        entry = summary.get(intent)
        assert entry is not None
        assert entry["authoritative"] == expected_provider_id
        assert entry["fallback"] is None


def test_vaporock_provider_module_does_not_import_ledger_transition_proposal():
    import ast

    source_path = pathlib.Path("engines/vaporock/provider.py").resolve()
    if not source_path.exists():
        from engines.vaporock import provider as vp_module

        source_path = pathlib.Path(vp_module.__file__).resolve()
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "LedgerTransitionProposal":
                    bad.append(f"from {node.module} import {alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith("LedgerTransitionProposal"):
                    bad.append(f"import {alias.name}")

    assert not bad, (
        "VapoRockProvider must not import LedgerTransitionProposal "
        f"(diagnostic-only intent); found: {bad}"
    )


def test_vaporock_diagnostics_payload_round_trips():
    diag = VapoRockDiagnostics(
        vapor_pressures_Pa={"Na": 100.0},
        vaporock_full_speciation_Pa={"Na": 100.0},
        activities={},
        pO2_bar=1e-9,
        mode="fake",
        engine_version="1.0",
        backend_status="ok",
        backend_warnings=("hello",),
    )
    payload = diag.as_diagnostic()
    assert set(payload.keys()) == {
        "vapor_pressures_Pa",
        "vaporock_full_speciation_Pa",
        "activities",
        "pO2_bar",
        "mode",
        "engine_version",
        "backend_status",
        "backend_warnings",
    }
    assert payload["vapor_pressures_Pa"] == {}
    assert payload["vaporock_full_speciation_Pa"] == {"Na": 100.0}
    assert payload["backend_warnings"] == ("hello",)
