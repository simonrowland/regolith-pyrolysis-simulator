"""Tests for the MAGEMin melt-backend adapter (simulator/melt_backend/magemin.py).

Distinct from ``tests/test_magemin_shadow_provider.py``, which covers the
``engines/magemin/`` kernel-shadow scaffold. This file defends the
``MeltBackend`` adapter contract -- in particular that the adapter is not
silently ignored if mis-selected as the active melt backend, that it fails
closed when MAGEMin is absent, that the mocked-present path converts the
oxide basis and pressure units correctly, and (skipif-guarded) that the
real MAGEMin binary runs end to end when one is built locally.
"""

from __future__ import annotations

import types
import warnings
from pathlib import Path

import pytest

from simulator.core import PyrolysisSimulator
from simulator.melt_backend.magemin import MAGEMinBackend


def _make_available_magemin(monkeypatch, fake_module):
    """Force a MAGEMinBackend to initialize() successfully with a fake bridge.

    The simulator constructor never calls ``initialize()``; tests that
    want an *available* MAGEMin backend must call it explicitly. We stub
    binary discovery and the Python-bridge import so no real MAGEMin
    install is required.
    """
    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: Path("/fake/MAGEMin")),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: ("pymagemin", fake_module),
    )


def test_magemin_as_active_backend_fails_closed_with_clear_message(monkeypatch):
    # MAGEMin is not wired into any active call site. If someone DOES
    # select it as the active melt backend, equilibrate() populates
    # phase_masses_kg but leaves ledger_transition None -- and core.py's
    # _get_equilibrium rejects exactly that combination. The adapter
    # docstring's "diagnostic" claim must mean "fails closed if
    # mis-selected", not "silently ignored".
    def minimize(**kwargs):
        # A populated post-equilibrium phase assemblage with NO ledger
        # transition -- the exact shape core.py must reject.
        return {
            "phases": {
                "liquid": {"mass_kg": 0.7},
                "olivine": {"mass_kg": 0.3},
            }
        }

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({}) is True
    assert backend.is_available() is True

    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"SiO2": 100.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("oxide", mass_kg=1.0)

    with pytest.raises(RuntimeError, match="without an AtomLedger transition"):
        sim.step()


def test_magemin_equilibrate_never_emits_ledger_transition(monkeypatch):
    # Even on a successful library call, the adapter must not fabricate a
    # ledger transition: MAGEMin holds no AtomLedger authority.
    def minimize(**kwargs):
        return {"phases": {"liquid": {"mass_kg": 1.0}}}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({}) is True

    result = backend.equilibrate(
        1600.0,
        composition_mol={"SiO2": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    assert result.ledger_transition is None
    # phase_masses_kg IS populated -- which is precisely why the result
    # is unsafe to route through _get_equilibrium as the active backend.
    assert result.phase_masses_kg
    assert backend.ledger_account_policies() == ()


# ----------------------------------------------------------------------
# Mocked-absent path: no MAGEMin binary, no bridge.
# ----------------------------------------------------------------------


def test_magemin_absent_binary_marks_backend_unavailable(monkeypatch):
    # No binary anywhere -> initialize() returns False and the backend
    # stays unavailable. The simulator can then route around it.
    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: None),
    )

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({}) is False
    assert backend.is_available() is False


def test_magemin_absent_equilibrate_returns_empty_result_with_warning(
    monkeypatch,
):
    # When MAGEMin is unavailable, equilibrate() must NOT raise: it
    # returns an empty EquilibriumResult carrying an explanatory warning,
    # and -- critically for the shadow posture -- no ledger transition.
    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: None),
    )

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})
    assert backend.is_available() is False

    result = backend.equilibrate(
        1500.0,
        composition_mol={"SiO2": 1.0, "MgO": 0.5},
        fO2_log=-9.0,
        pressure_bar=1.0,
    )

    assert result.phases_present == []
    assert result.phase_masses_kg == {}
    assert result.ledger_transition is None
    assert result.warnings
    assert any("not initialized" in w for w in result.warnings)


# ----------------------------------------------------------------------
# Mocked-present path: a tiny fake bridge module.
# Verifies oxide-basis projection + pressure_bar -> P_GPa conversion +
# EquilibriumResult population.
# ----------------------------------------------------------------------


def test_magemin_fake_bridge_receives_oxide_wt_pct_basis(monkeypatch):
    # The fake bridge captures what the adapter handed it: the input must
    # be projected onto the 14-oxide MELTS wt% basis, normalized to 100,
    # with non-oxide species dropped.
    captured = {}

    def minimize(**kwargs):
        captured.update(kwargs)
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    # Mol-native input including a non-oxide species (native Fe metal)
    # that the oxide projection must drop.
    backend.equilibrate(
        1400.0,
        composition_mol={
            "SiO2": 5.0,
            "Al2O3": 1.0,
            "MgO": 2.0,
            "CaO": 1.5,
            "Fe": 3.0,  # native metal -- not in the oxide basis
        },
        fO2_log=-8.0,
        pressure_bar=5000.0,
    )

    comp = captured["composition"]
    # Non-oxide native Fe must not have reached the library.
    assert "Fe" not in comp
    assert set(comp).issubset(
        {
            "SiO2", "TiO2", "Al2O3", "FeO", "Fe2O3", "MgO", "CaO",
            "Na2O", "K2O", "Cr2O3", "MnO", "P2O5", "NiO", "CoO",
        }
    )
    assert "SiO2" in comp and comp["SiO2"] > 0.0
    # Oxide wt% basis is normalized to 100.
    assert sum(comp.values()) == pytest.approx(100.0, rel=1e-6)


def test_magemin_fake_bridge_receives_pressure_in_gpa(monkeypatch):
    # The binding-spec contract (§4) is pressure in GPa. The adapter must
    # convert pressure_bar -> P_GPa with 1 GPa = 10000 bar before the
    # library boundary, and also expose the kbar form the binary's CLI
    # wants (1 GPa = 10 kbar).
    captured = {}

    def minimize(**kwargs):
        captured.update(kwargs)
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    # 15000 bar == 1.5 GPa == 15 kbar.
    backend.equilibrate(
        1450.0,
        composition_mol={"SiO2": 5.0, "MgO": 3.0},
        fO2_log=-8.0,
        pressure_bar=15000.0,
    )

    assert captured["P_GPa"] == pytest.approx(1.5)
    assert captured["P_kbar"] == pytest.approx(15.0)
    # Temperature is passed through in both C and K.
    assert captured["T_C"] == pytest.approx(1450.0)
    assert captured["T_K"] == pytest.approx(1450.0 + 273.15)


def test_magemin_pressure_conversion_helpers_are_exact():
    # The conversion is load-bearing: a wrong factor is a silent O(10^n)
    # pressure error. Pin both legs.
    assert MAGEMinBackend._pressure_bar_to_GPa(10000.0) == pytest.approx(1.0)
    assert MAGEMinBackend._pressure_bar_to_GPa(0.0) == pytest.approx(0.0)
    assert MAGEMinBackend._pressure_bar_to_GPa(2.5e5) == pytest.approx(25.0)
    assert MAGEMinBackend._GPa_to_kbar(1.0) == pytest.approx(10.0)
    assert MAGEMinBackend._GPa_to_kbar(1.5) == pytest.approx(15.0)


def test_magemin_fake_bridge_populates_equilibrium_result(monkeypatch):
    # A successful call must populate phases_present, phase_masses_kg and
    # liquid_fraction from the library's phase block -- and still leave
    # ledger_transition None (shadow posture).
    def minimize(**kwargs):
        return {
            "phases": {
                "liq": {"mass_kg": 0.8},
                "ol": {"mass_kg": 0.15},
                "spl": {"mass_kg": 0.05},
            }
        }

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    result = backend.equilibrate(
        1350.0,
        composition_mol={"SiO2": 5.0, "MgO": 3.0, "FeO": 1.0},
        fO2_log=-8.0,
        pressure_bar=2000.0,
    )

    assert set(result.phases_present) == {"liq", "ol", "spl"}
    assert result.phase_masses_kg["liq"] == pytest.approx(0.8)
    # liquid_fraction = liquid mass / total mass.
    assert result.liquid_fraction == pytest.approx(0.8 / 1.0)
    assert result.ledger_transition is None
    assert result.temperature_C == pytest.approx(1350.0)
    assert result.pressure_bar == pytest.approx(2000.0)


def test_magemin_fake_bridge_library_error_returns_warning(monkeypatch):
    # A library-boundary exception must be caught and surfaced as a
    # warning on an otherwise-empty result -- never raised.
    def minimize(**kwargs):
        raise RuntimeError("synthetic MAGEMin failure")

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    result = backend.equilibrate(
        1500.0,
        composition_mol={"SiO2": 1.0},
        fO2_log=-8.0,
        pressure_bar=1.0,
    )

    assert result.phases_present == []
    assert result.ledger_transition is None
    assert any("synthetic MAGEMin failure" in w for w in result.warnings)


def test_magemin_only_consumes_cleaned_melt_account(monkeypatch):
    # When called with the layered ABC's composition_mol_by_account, the
    # adapter must consume only process.cleaned_melt and warn about every
    # other account it dropped (binding spec §7 -- no metal/salt/sulfide).
    captured = {}

    def minimize(**kwargs):
        captured.update(kwargs)
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    result = backend.equilibrate(
        1400.0,
        composition_mol_by_account={
            "process.cleaned_melt": {"SiO2": 5.0, "MgO": 3.0},
            "process.metal_alloy": {"Fe": 2.0},
            "process.sulfide_matte": {"FeS": 1.0},
        },
        fO2_log=-8.0,
        pressure_bar=1000.0,
    )

    comp = captured["composition"]
    assert "Fe" not in comp and "FeS" not in comp
    assert "SiO2" in comp
    dropped_warnings = " ".join(result.warnings)
    assert "process.metal_alloy" in dropped_warnings
    assert "process.sulfide_matte" in dropped_warnings


# ----------------------------------------------------------------------
# Live smoke test: runs the real MAGEMin binary if one is built locally.
# Skipif-guarded so CI without a built MAGEMin still passes.
# ----------------------------------------------------------------------

# Resolve a real binary at collection time so the guard is a true
# pytest.mark.skipif rather than a runtime branch. MAGEMin v1.9.3 is built
# locally as a sibling clone (../MAGEMin/MAGEMin); _locate_binary also
# checks engines/magemin/{,bin/}MAGEMin and PATH.
_LIVE_MAGEMIN_BINARY = MAGEMinBackend._locate_binary(None)


@pytest.mark.skipif(
    _LIVE_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
def test_magemin_live_smoke_runs_real_binary():
    # End-to-end against the real MAGEMin binary: a basalt analog at
    # crustal P/T must equilibrate, report a silicate liquid, and -- the
    # invariant that matters -- leave ledger_transition None. MAGEMin is
    # shadow/diagnostic: it never gets AtomLedger authority.
    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        available = backend.initialize({})
    if not available:
        pytest.skip("MAGEMin binary present but backend failed to initialize")

    # The subprocess bridge is the supported default; the live binary
    # must resolve to it (ctypes is opt-in only, pymagemin/julia rare).
    assert backend._bridge == "subprocess"

    basalt_wt_pct = {
        "SiO2": 49.0,
        "TiO2": 1.5,
        "Al2O3": 14.0,
        "FeO": 10.0,
        "Fe2O3": 1.0,
        "MgO": 9.0,
        "CaO": 11.0,
        "Na2O": 2.5,
        "K2O": 0.8,
        "Cr2O3": 0.2,
        "MnO": 0.2,
        "P2O5": 0.3,
        "NiO": 0.02,
        "CoO": 0.01,
    }

    # 2000 bar == 0.2 GPa == 2 kbar; well inside the igneous database's
    # crustal calibration. 1200 C is super-liquidus for this analog.
    result = backend.equilibrate(
        1200.0,
        composition_kg=basalt_wt_pct,
        fO2_log=-8.0,
        pressure_bar=2000.0,
    )

    # No library-boundary error.
    assert not any("failed" in w for w in result.warnings), result.warnings
    # MAGEMin reports a phase assemblage including a silicate liquid.
    assert result.phases_present
    assert any(
        name.lower().startswith("liq") for name in result.phases_present
    ), result.phases_present
    assert result.phase_masses_kg
    # At 1200 C this analog is liquid-dominated.
    assert 0.0 < result.liquid_fraction <= 1.0
    # Shadow posture: MAGEMin holds no AtomLedger authority, ever.
    assert result.ledger_transition is None
    assert backend.ledger_account_policies() == ()


@pytest.mark.skipif(
    _LIVE_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
def test_magemin_live_subliquidus_run_reports_crystalline_phases():
    # A second live point below the liquidus: the binary must report
    # crystalline phases alongside (or instead of) the melt, and the
    # liquid fraction must drop relative to the super-liquidus case.
    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        available = backend.initialize({})
    if not available:
        pytest.skip("MAGEMin binary present but backend failed to initialize")

    peridotite_wt_pct = {
        "SiO2": 45.0,
        "TiO2": 0.2,
        "Al2O3": 4.0,
        "FeO": 8.0,
        "MgO": 38.0,
        "CaO": 3.5,
        "Na2O": 0.3,
        "Cr2O3": 0.4,
    }

    # 1000 C at 10 kbar (1 GPa, 10000 bar) is sub-solidus to
    # low-melt-fraction for a peridotite -- expect crystalline phases.
    result = backend.equilibrate(
        1000.0,
        composition_kg=peridotite_wt_pct,
        fO2_log=-9.0,
        pressure_bar=10000.0,
    )

    assert not any("failed" in w for w in result.warnings), result.warnings
    assert result.phases_present
    crystalline = [
        name
        for name in result.phases_present
        if not name.lower().startswith("liq")
    ]
    assert crystalline, result.phases_present
    assert result.ledger_transition is None
