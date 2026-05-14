"""Tests for the MAGEMin melt-backend adapter (simulator/melt_backend/magemin.py).

Distinct from ``tests/test_magemin_shadow_provider.py``, which covers the
``engines/magemin/`` kernel-shadow scaffold. This file defends the
``MeltBackend`` adapter contract -- in particular that the adapter is not
silently ignored if mis-selected as the active melt backend.
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
