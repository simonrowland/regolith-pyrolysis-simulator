from __future__ import annotations

import ast
import inspect
import os
from types import SimpleNamespace

import pytest

import simulator.diagnostic_helpers.alphamelts_volatility as volatility_module
from engines.domain_reason import OutOfDomainReason
from simulator.alphamelts_reference_pressure import (
    alphamelts_condensed_phase_pressure_bar,
    annotate_alphamelts_reference_pressure,
)
from simulator.diagnostic_helpers.alphamelts_volatility import (
    alphamelts_activity_volatility_diagnostic,
)
from simulator.melt_backend.alphamelts import AlphaMELTSBackend


_SIO_VAPOR_DATA = {
    "metals": {},
    "oxide_vapors": {
        "SiO": {
            "formula": "SiO",
            "parent_oxide": "SiO2",
            "antoine": {"A": 2.0, "B": 0.0, "C": 0.0},
            "valid_range_K": [1000.0, 2500.0],
        },
    },
}


class StubActivitySource:
    def __init__(self, by_pressure: dict[float, dict[str, float]]):
        self.by_pressure = by_pressure
        self.calls: list[float] = []

    def __call__(
        self,
        *,
        temperature_C: float,
        pressure_bar: float,
        composition_wt_pct: dict[str, float],
        fO2_log: float,
    ) -> dict[str, object]:
        del temperature_C, composition_wt_pct, fO2_log
        self.calls.append(float(pressure_bar))
        sample = self.by_pressure[float(pressure_bar)]
        if any(
            key in sample
            for key in (
                "activity_coefficients",
                "diagnostic_oxide_activities",
                "diagnostics",
            )
        ):
            return {"status": "ok", **sample}
        return {
            "status": "ok",
            "activity_coefficients": sample,
        }


def _domain_composition() -> dict[str, float]:
    return {
        "SiO2": 50.0,
        "Al2O3": 15.0,
        "FeO": 10.0,
        "MgO": 10.0,
        "CaO": 10.0,
        "Na2O": 5.0,
    }


def test_default_activity_source_uses_explicit_reference_pressure(monkeypatch):
    seen: dict[str, object] = {}

    def fake_initialize(self, config):
        del config
        self._mode = "subprocess"
        return True

    monkeypatch.setattr(AlphaMELTSBackend, "initialize", fake_initialize)

    def fake_equilibrate(self, **kwargs):
        del self
        seen.update(kwargs)
        return SimpleNamespace(diagnostics={})

    monkeypatch.setattr(AlphaMELTSBackend, "equilibrate", fake_equilibrate)

    result = volatility_module.alphamelts_equilibrium_activity_source(
        temperature_C=1500.0,
        pressure_bar=0.1,
        composition_wt_pct=_domain_composition(),
        fO2_log=-9.0,
    )

    assert seen["pressure_bar"] == pytest.approx(1.0)
    assert result.diagnostics["physical_overhead_pressure_bar"] == pytest.approx(
        0.1
    )
    assert result.diagnostics[
        "condensed_phase_reference_pressure_bar"
    ] == pytest.approx(1.0)


def test_maps_alphamelt_activity_into_analytical_sio_vapor_pressure_grid():
    source = StubActivitySource(
        {
            1.0: {"SiO2": 0.25},
            10.0: {"SiO2": 0.25},
        }
    )

    diagnostic = alphamelts_activity_volatility_diagnostic(
        composition_wt_pct=_domain_composition(),
        pO2_grid_bar=[1e-9, 1e-7],
        temperature_C=1500.0,
        activity_source=source,
        vapor_pressure_data=_SIO_VAPOR_DATA,
    )

    assert source.calls == [1.0, 10.0]
    assert diagnostic["status"] == "ok"
    assert diagnostic["melt_oxide_activities"] == {"SiO2": 0.25}

    hard_vacuum = diagnostic["grid"][0]["species"]["SiO"]
    assert hard_vacuum["P_reference_Antoine_Pa"] == pytest.approx(100.0)
    assert hard_vacuum["melt_oxide_activity"] == pytest.approx(0.25)
    assert hard_vacuum["wt_fraction_activity"] == pytest.approx(0.50)
    assert hard_vacuum["P_eq_Pa"] == pytest.approx(25.0)
    assert hard_vacuum["P_eq_wt_fraction_Pa"] == pytest.approx(50.0)
    assert hard_vacuum["activity_ratio_vs_wt_fraction"] == pytest.approx(0.5)
    assert hard_vacuum["P_eq_ratio_vs_wt_fraction"] == pytest.approx(0.5)

    oxidized = diagnostic["grid"][1]["species"]["SiO"]
    # Builtin SiO branch applies sqrt(vacuum_floor / pO2) when pO2 exceeds
    # the 1e-9 bar floor: 100 Pa * a(SiO2)=0.25 * sqrt(1e-9 / 1e-7).
    assert oxidized["P_eq_Pa"] == pytest.approx(2.5)
    assert oxidized["P_eq_wt_fraction_Pa"] == pytest.approx(5.0)


def test_uses_diagnostic_oxide_activities_payload():
    source = StubActivitySource(
        {
            1.0: {
                "activity_coefficients": {"Na2SiO3": 0.8},
                "diagnostic_oxide_activities": {"SiO2": 0.25, "Na2O": 0.08},
            },
            10.0: {
                "activity_coefficients": {"Na2SiO3": 0.8},
                "diagnostic_oxide_activities": {"SiO2": 0.25, "Na2O": 0.08},
            },
        }
    )

    diagnostic = alphamelts_activity_volatility_diagnostic(
        composition_wt_pct=_domain_composition(),
        pO2_grid_bar=[1e-9],
        temperature_C=1500.0,
        activity_source=source,
        vapor_pressure_data=_SIO_VAPOR_DATA,
        primary_pressure_bar=1.0,
        comparison_pressure_bar=1.0,
    )

    assert diagnostic["status"] == "ok"
    assert diagnostic["melt_oxide_activities"] == {
        "SiO2": pytest.approx(0.25),
        "Na2O": pytest.approx(0.08),
    }


def test_diagnostic_helper_does_not_convert_endmember_labels_to_oxides():
    source = StubActivitySource(
        {
            1.0: {"SiO2_Liq": 0.25, "Na2SiO3": 0.08, "Na": 0.03},
            10.0: {"SiO2_Liq": 0.25, "Na2SiO3": 0.08, "Na": 0.03},
        }
    )

    diagnostic = alphamelts_activity_volatility_diagnostic(
        composition_wt_pct=_domain_composition(),
        pO2_grid_bar=[1e-9],
        temperature_C=1500.0,
        activity_source=source,
        vapor_pressure_data=_SIO_VAPOR_DATA,
        primary_pressure_bar=1.0,
        comparison_pressure_bar=1.0,
    )

    assert diagnostic["status"] == "ok"
    assert diagnostic["melt_oxide_activities"] == {"SiO2": pytest.approx(0.25)}
    assert "Na2O" not in diagnostic["melt_oxide_activities"]


def test_composition_domain_violation_reuses_alphamelt_reason_flag():
    source = StubActivitySource(
        {
            1.0: {"SiO2": 0.25},
            10.0: {"SiO2": 0.25},
        }
    )

    diagnostic = alphamelts_activity_volatility_diagnostic(
        composition_wt_pct={"SiO2": 10.0, "CaO": 90.0},
        pO2_grid_bar=[1e-9],
        temperature_C=1500.0,
        activity_source=source,
        vapor_pressure_data=_SIO_VAPOR_DATA,
    )

    domain = diagnostic["alphamelts_domain"]
    assert diagnostic["extrapolation_limited"] is True
    assert domain["status"] == "extrapolation_limited"
    assert domain["backend_status_reason"] == OutOfDomainReason.SILICATE_WINDOW.value
    assert domain["out_of_domain_crash_point"]["temperature_C"] == pytest.approx(1500.0)


def test_source_clamped_operating_point_marks_extrapolation_limited():
    def clamped_source(**kwargs):
        del kwargs
        return {
            "status": "out_of_domain",
            "backend_status_reason": "clamped_operating_point",
            "activity_coefficients": {"SiO2": 0.25},
            "diagnostics": {
                "operating_point_clamped": True,
                "authoritative_for_requested_conditions": False,
            },
        }

    diagnostic = alphamelts_activity_volatility_diagnostic(
        composition_wt_pct=_domain_composition(),
        pO2_grid_bar=[1e-9],
        temperature_C=650.0,
        activity_source=clamped_source,
        vapor_pressure_data=_SIO_VAPOR_DATA,
    )

    assert diagnostic["status"] == "ok"
    assert diagnostic["extrapolation_limited"] is True
    assert (
        diagnostic["activity_samples"]["primary"]["backend_status_reason"]
        == "clamped_operating_point"
    )


def test_source_vapor_pressure_facet_fallback_surfaces_diagnostic_only_limit():
    def fallback_source(**kwargs):
        del kwargs
        return {
            "status": "ok",
            "activity_coefficients": {"SiO2": 0.25},
            "diagnostics": {
                "vapor_pressure_backend_status": "fallback",
                "vapor_pressure_backend_status_reason": (
                    "vaporock_to_antoine_fallback"
                ),
                "vapor_pressure_fallback_source": (
                    "antoine_fallback_from_vaporock"
                ),
                "authoritative_for_requested_vapor_pressure": False,
            },
        }

    diagnostic = alphamelts_activity_volatility_diagnostic(
        composition_wt_pct=_domain_composition(),
        pO2_grid_bar=[1e-9],
        temperature_C=1500.0,
        activity_source=fallback_source,
        vapor_pressure_data=_SIO_VAPOR_DATA,
    )

    limits = diagnostic["activity_source_extrapolation_limits"]
    assert diagnostic["diagnostic_only"] is True
    assert diagnostic["extrapolation_limited"] is True
    assert "activity_sample_0" not in limits
    assert limits["vapor_pressure_sample_0"] == {
        "vapor_pressure_backend_status": "fallback",
        "vapor_pressure_backend_status_reason": (
            "vaporock_to_antoine_fallback"
        ),
        "vapor_pressure_fallback_source": "antoine_fallback_from_vaporock",
        "authority_status": "vapor_pressure_facet_degraded",
        "diagnostic_only": True,
        "diagnostics": diagnostic["activity_samples"]["primary"]["diagnostics"],
    }


def test_pressure_insensitivity_gate_passes_matched_and_flags_mismatch():
    matched = StubActivitySource(
        {
            1.0: {"SiO2": 0.25},
            10.0: {"SiO2": 0.2501},
        }
    )
    matched_result = alphamelts_activity_volatility_diagnostic(
        composition_wt_pct=_domain_composition(),
        pO2_grid_bar=[1e-9],
        temperature_C=1500.0,
        activity_source=matched,
        vapor_pressure_data=_SIO_VAPOR_DATA,
    )
    assert matched_result["activity_pressure_gate"]["status"] == "ok"

    mismatched = StubActivitySource(
        {
            1.0: {"SiO2": 0.25},
            10.0: {"SiO2": 0.35},
        }
    )
    mismatch_result = alphamelts_activity_volatility_diagnostic(
        composition_wt_pct=_domain_composition(),
        pO2_grid_bar=[1e-9],
        temperature_C=1500.0,
        activity_source=mismatched,
        vapor_pressure_data=_SIO_VAPOR_DATA,
    )
    assert mismatch_result["status"] == "falsification_flagged"
    assert mismatch_result["activity_pressure_gate"]["status"] == "falsified"
    assert "SiO2" in mismatch_result["activity_pressure_gate"]["mismatches"]
    assert "grid" not in mismatch_result


def test_diagnostic_helper_has_no_ledger_or_provider_authority_surface():
    source = inspect.getsource(volatility_module)
    tree = ast.parse(source)
    forbidden_names = {
        "AtomLedger",
        "LedgerTransition",
        "LedgerTransitionProposal",
        "ChemistryProvider",
        "ProviderRegistry",
        "ChemistryIntent",
    }
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in forbidden_names:
                    offenders.append(f"from {node.module} import {alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "simulator.accounting.ledger":
                    offenders.append(f"import {alias.name}")
    assert offenders == []
    assert not hasattr(volatility_module, "capability_profile")
    assert not hasattr(volatility_module, "dispatch")

    source_stub = StubActivitySource(
        {
            1.0: {"SiO2": 0.25},
            10.0: {"SiO2": 0.25},
        }
    )
    diagnostic = alphamelts_activity_volatility_diagnostic(
        composition_wt_pct=_domain_composition(),
        pO2_grid_bar=[1e-9],
        temperature_C=1500.0,
        activity_source=source_stub,
        vapor_pressure_data=_SIO_VAPOR_DATA,
    )
    assert "transition" not in diagnostic
    assert "ledger_transition" not in diagnostic


@pytest.mark.serial
def test_real_alphamelt_volatility_diagnostic_opt_in():
    if os.environ.get("REGOLITH_RUN_REAL_ALPHAMELTS_VOLATILITY") != "1":
        pytest.skip("real AlphaMELTS diagnostic smoke is env-gated")

    diagnostic = alphamelts_activity_volatility_diagnostic(
        composition_wt_pct=_domain_composition(),
        pO2_grid_bar=[1e-9, 1e-3],
        temperature_C=1500.0,
    )

    assert diagnostic["diagnostic_only"] is True
    assert diagnostic["activity_pressure_gate"]["status"] in {"ok", "falsified"}


@pytest.mark.serial
def test_live_alphamelt_benign_feedstock_returns_oxide_activities_when_available():
    backend = AlphaMELTSBackend()
    try:
        available = backend.initialize({"mode": "subprocess", "timeout_s": 25.0})
    except ImportError as exc:
        pytest.skip(f"AlphaMELTS subprocess transport unavailable: {exc}")
    if not available:
        pytest.skip("AlphaMELTS subprocess transport unavailable")

    backend_results = []

    def source(**kwargs):
        physical_pressure_bar = float(kwargs["pressure_bar"])
        evaluation_pressure_bar = alphamelts_condensed_phase_pressure_bar(
            physical_pressure_bar,
            transport="subprocess",
        )
        result = backend.equilibrate(
            temperature_C=float(kwargs["temperature_C"]),
            composition_kg=dict(kwargs["composition_wt_pct"]),
            fO2_log=float(kwargs["fO2_log"]),
            pressure_bar=evaluation_pressure_bar,
            subprocess_run_mode="isothermal",
        )
        result = annotate_alphamelts_reference_pressure(
            result,
            physical_pressure_bar=physical_pressure_bar,
            evaluation_pressure_bar=evaluation_pressure_bar,
        )
        backend_results.append(result)
        return result

    diagnostic = alphamelts_activity_volatility_diagnostic(
        composition_wt_pct=_domain_composition(),
        pO2_grid_bar=[1e-9],
        temperature_C=1500.0,
        activity_source=source,
        vapor_pressure_data=_SIO_VAPOR_DATA,
    )

    assert diagnostic["diagnostic_only"] is True
    assert diagnostic["status"] == "no_activities"
    assert diagnostic["activity_samples"]["primary"]["activity_coefficients"] == {}
    assert diagnostic["activity_samples"]["comparison"]["activity_coefficients"] == {}
    assert backend_results
    for result in backend_results:
        assert set(result.activity_coefficients) == {"H2O"}
        assert result.activity_coefficients["H2O"] == pytest.approx(0.0)
        diagnostics = result.diagnostics or {}
        assert diagnostics.get("diagnostic_oxide_activities") in (None, {})
        assert diagnostics.get("diagnostic_activity_label_map") in (None, {})
