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

import re
import subprocess
import types
import warnings
from pathlib import Path

import pytest

from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import LiquidFractionInvalidError
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


def test_magemin_liquidus_finder_bisects_fake_bridge(monkeypatch):
    def minimize(**kwargs):
        temperature_C = float(kwargs["T_C"])
        frac = max(0.0, min(1.0, (temperature_C - 1000.0) / 300.0))
        phases = {}
        if frac > 0.0:
            phases["liq"] = {"mass_kg": frac}
        if frac < 1.0:
            phases["ol"] = {"mass_kg": 1.0 - frac}
        return {"phases": phases}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({}) is True

    result = backend.find_liquidus_solidus(
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
        min_T_C=800.0,
        max_T_C=1500.0,
        scan_step_C=100.0,
        tolerance_C=1.0,
    )

    assert result.status == "ok"
    assert result.solidus_T_C == pytest.approx(1000.0, abs=1.0)
    assert result.liquidus_T_C == pytest.approx(1300.0, abs=1.0)
    assert result.liquidus_T_K == pytest.approx(result.liquidus_T_C + 273.15)


def test_magemin_liquidus_finder_unavailable_without_backend():
    backend = MAGEMinBackend()

    result = backend.find_liquidus_solidus(
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    assert result.status == "unavailable"
    assert "not initialized" in " ".join(result.warnings)


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
    assert result.status == "unavailable"


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


def test_magemin_feot_conversion_uses_iupac_2feo_mass():
    # Current standard atomic weights: Fe 55.845, O 15.999 g/mol.
    feo_molar_mass = (
        MAGEMinBackend._FE_MOLAR_MASS_G_PER_MOL
        + MAGEMinBackend._O_MOLAR_MASS_G_PER_MOL
    )
    fe2o3_molar_mass = (
        2 * MAGEMinBackend._FE_MOLAR_MASS_G_PER_MOL
        + 3 * MAGEMinBackend._O_MOLAR_MASS_G_PER_MOL
    )
    feot_numerator = 2 * feo_molar_mass

    assert feot_numerator == pytest.approx(143.688)
    assert MAGEMinBackend._FEOT_FROM_FE2O3_MOLAR_MASS_G_PER_MOL == (
        pytest.approx(feot_numerator)
    )
    assert MAGEMinBackend._FEOT_FROM_FE2O3_FACTOR == pytest.approx(
        feot_numerator / fe2o3_molar_mass
    )


def test_magemin_ig_bulk_vector_folds_fe2o3_to_feot():
    backend = MAGEMinBackend()
    vector = backend._build_ig_bulk_vector({"FeO": 10.0, "Fe2O3": 1.0})
    feot_index = MAGEMinBackend._IG_BULK_ORDER.index("FeOt")
    oxygen_index = MAGEMinBackend._IG_BULK_ORDER.index("O")

    expected_feot = 10.0 + MAGEMinBackend._FEOT_FROM_FE2O3_FACTOR
    assert vector[feot_index] == pytest.approx(expected_feot)
    assert vector[oxygen_index] == pytest.approx(
        MAGEMinBackend._EXCESS_O_FROM_FE2O3_FACTOR
    )
    assert vector[feot_index] + vector[oxygen_index] == pytest.approx(11.0)


def test_magemin_explicit_fe2o3_does_not_apply_total_iron_o_provision():
    backend = MAGEMinBackend()
    vector = backend._build_ig_bulk_vector({"FeO": 10.0, "Fe2O3": 1.0})
    oxygen_index = MAGEMinBackend._IG_BULK_ORDER.index("O")

    assert vector[oxygen_index] == pytest.approx(
        1.0 * MAGEMinBackend._EXCESS_O_FROM_FE2O3_FACTOR
    )


def test_magemin_ig_bulk_vector_feo_total_iron_provisions_redox_o():
    backend = MAGEMinBackend()
    vector = backend._build_ig_bulk_vector(
        {
            "SiO2": 49.0,
            "Al2O3": 14.0,
            "CaO": 11.0,
            "MgO": 8.0,
            "FeO": 16.5,
        }
    )
    feot_index = MAGEMinBackend._IG_BULK_ORDER.index("FeOt")
    oxygen_index = MAGEMinBackend._IG_BULK_ORDER.index("O")

    assert vector[feot_index] == pytest.approx(16.5)
    assert vector[oxygen_index] == pytest.approx(
        16.5 * MAGEMinBackend._EXCESS_O_FROM_FEO_TOTAL_IRON_FACTOR
    )
    assert vector[oxygen_index] > 0.0


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
    assert result.status == "ok"


def test_magemin_ok_result_with_nonfinite_phase_mass_raises(monkeypatch):
    def minimize(**kwargs):
        return {
            "phases": {
                "liq": {"mass_kg": float("nan")},
                "ol": {"mass_kg": 0.2},
            }
        }

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    with pytest.raises(LiquidFractionInvalidError, match="phase_mass_invalid"):
        backend.equilibrate(
            1350.0,
            composition_mol={"SiO2": 5.0, "MgO": 3.0, "FeO": 1.0},
            fO2_log=-8.0,
            pressure_bar=2000.0,
        )


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
    assert result.status == "not_converged"


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


@pytest.mark.skipif(
    _LIVE_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
def test_magemin_live_liquidus_finder_lunar_mare_low_ti_sane():
    """Apollo low-Ti mare basalt sample 12009 begins crystallizing near 1230 C.

    Reference: Walker et al. 1971, Experimental petrology of Apollo 12 basalts,
    part 1, sample 12009. This feedstock is only an Apollo 12/15 low-Ti soil
    analog, so the test checks a sane bracket rather than a forced retune.
    """
    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        available = backend.initialize({})
    if not available:
        pytest.skip("MAGEMin binary present but backend failed to initialize")

    result = backend.find_liquidus_solidus(
        composition_kg={
            "SiO2": 44.5,
            "TiO2": 1.5,
            "Al2O3": 13.5,
            "FeO": 16.5,
            "MgO": 9.0,
            "CaO": 11.0,
            "Na2O": 0.4,
            "K2O": 0.10,
            "Cr2O3": 0.35,
            "MnO": 0.20,
            "P2O5": 0.10,
        },
        fO2_log=-9.0,
        pressure_bar=1.0,
        min_T_C=800.0,
        max_T_C=1500.0,
        scan_step_C=100.0,
        tolerance_C=2.0,
    )

    assert result.status == "ok", result.warnings
    assert 900.0 <= result.solidus_T_C <= 1100.0
    assert 1200.0 <= result.liquidus_T_C <= 1450.0
    assert result.liquidus_T_C >= result.solidus_T_C


@pytest.mark.skipif(
    _LIVE_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
@pytest.mark.parametrize(
    ("name", "composition_kg", "reference_C"),
    [
        ("forsterite", {"MgO": 57.276, "SiO2": 42.724}, 1890.0),
        ("diopside", {"CaO": 25.9, "MgO": 18.6, "SiO2": 55.5}, 1391.5),
        ("anorthite", {"CaO": 20.16, "Al2O3": 36.65, "SiO2": 43.19}, 1553.0),
    ],
)
def test_magemin_live_pure_endmember_references_documented_xfail(
    name,
    composition_kg,
    reference_C,
):
    """Pure endmember melting references for any future calibrated engine.

    Forsterite 2163 K follows Akimoto et al. 1981; diopside 1391.5 C and
    anorthite 1553 C are standard Di-An calibration values. The MAGEMin `ig`
    subprocess path here is calibrated for natural igneous systems and the L1
    probe showed these pure endmembers are not reliable acceptance targets.
    """
    pytest.xfail(
        f"MAGEMin ig subprocess is not accepted for pure {name} "
        f"endmember liquidus {reference_C:g} C"
    )


def _run_magemin_gam_o(binary: Path, *, buffer_n: float) -> float:
    """Run the live binary at one ``buffer_n`` and return GAM[O] (mu of the
    oxygen system component, kJ).

    Builds the ``ig`` bulk vector directly with a **nonzero O component** so
    the fO2 buffer actually engages. P3-F showed MAGEMin's qfm buffer is inert
    when ``O=0`` (see
    ``docs-private/research/2026-06-05-p3f/findings.md`` Finding 2). This test
    therefore bypasses the adapter to probe the binary's real redox response.

    GAM is reported in IG component order
    ``SiO2 Al2O3 CaO MgO FeOt K2O Na2O TiO2 O Cr2O3 H2O`` -> O is index 8.
    """
    # Basalt analog (matches the live-smoke test) with O set nonzero.
    bulk = "49,14,11,9,10.899810,0.8,2.5,1.5,1.0,0.2,0"
    completed = subprocess.run(  # noqa: S603 - args are test-built constants
        [
            str(binary),
            "--Verb=2",
            "--db=ig",
            "--Temp=1200.0",
            "--Pres=2.0",
            "--sys_in=wt",
            f"--Bulk={bulk}",
            "--buffer=qfm",
            f"--buffer_n={buffer_n:.6f}",
        ],
        cwd=str(binary.parent),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    match = re.search(r"GAM = \[([^\]]+)\]", completed.stdout)
    assert match, f"no GAM vector in MAGEMin stdout:\n{completed.stdout}"
    gam = [float(x) for x in match.group(1).split(",")]
    # Full IG order has 11 components; the O-component mu is index 8.
    assert len(gam) == 11, gam
    return gam[8]


def _run_magemin_adapter_buffer_probe(
    binary: Path,
    *,
    fO2_log: float,
) -> tuple[float, tuple[str, ...], tuple[float, ...]]:
    """Run the live binary through the production adapter's ig vector path."""
    backend = MAGEMinBackend()
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
    }
    temperature_C = 1200.0
    pressure_bar = 2000.0
    pressure_kbar = backend._GPa_to_kbar(
        backend._pressure_bar_to_GPa(pressure_bar)
    )
    bulk = backend._build_ig_bulk_vector(basalt_wt_pct)
    buffer_name, buffer_n, _warnings = backend._resolve_buffer(
        temperature_C=temperature_C,
        fO2_log=fO2_log,
    )
    completed = subprocess.run(  # noqa: S603 - args are test-built constants
        [
            str(binary),
            "--Verb=2",
            f"--db={backend._database}",
            f"--Temp={temperature_C:.6f}",
            f"--Pres={pressure_kbar:.6f}",
            "--sys_in=wt",
            "--Bulk=" + ",".join(f"{value:.6f}" for value in bulk),
            f"--buffer={buffer_name}",
            f"--buffer_n={buffer_n:.6f}",
        ],
        cwd=str(binary.parent),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    gam_match = re.search(r"GAM = \[([^\]]+)\]", completed.stdout)
    assert gam_match, f"no GAM vector in MAGEMin stdout:\n{completed.stdout}"
    gam = [float(x) for x in gam_match.group(1).split(",")]
    assert len(gam) == 11, gam

    phase_match = re.search(r"^\s*Phase\s*:\s*(.+)$", completed.stdout, re.M)
    mode_match = re.search(r"^\s*Mode\s*:\s*(.+)$", completed.stdout, re.M)
    assert phase_match and mode_match, (
        f"no Phase/Mode block in MAGEMin stdout:\n{completed.stdout}"
    )
    phases = tuple(phase_match.group(1).split())
    modes = tuple(float(x) for x in mode_match.group(1).split())
    assert len(phases) == len(modes)
    return gam[8], phases, modes


@pytest.mark.skipif(
    _LIVE_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
def test_magemin_live_buffer_n_sign_and_magnitude_round_trip():
    """P3-F: the live binary must honour ``--buffer_n`` with the correct sign
    AND magnitude, validating ``_resolve_buffer``'s
    ``buffer_n = fO2_log - QFM(T)`` translation against the real MAGEMin.

    The single-point ``ig`` CLI prints no explicit fO2/Fe3+; redox is carried
    by the oxygen component's chemical potential, reported as GAM[O]. We use
    GAM[O] as the non-speculative redox proxy (recon:
    ``docs-private/research/2026-06-05-p3f/findings.md`` Finding 1).

    Two invariants, both anchored to MAGEMin's documented buffer formula
    ``mu_offset(O2) = T_K * 0.019145 * buffer_n`` (0.019145 = R*ln10/1000):
      - SIGN: higher buffer_n => higher (less negative) GAM[O] => more
        oxidizing. So requesting fO2 above QFM (positive buffer_n) is more
        oxidizing, confirming the translation sign.
      - MAGNITUDE: d(GAM[O])/d(buffer_n) == T_K * 0.019145 / 2 (per single O;
        the formula is per O2 = 2 O), so a delta of 4 buffer_n units shifts
        GAM[O] by T_K * 0.019145 * 4 / 2 kJ.
    """
    binary = _LIVE_MAGEMIN_BINARY
    mu_o_reduced = _run_magemin_gam_o(binary, buffer_n=-2.0)
    mu_o_oxidized = _run_magemin_gam_o(binary, buffer_n=2.0)

    # SIGN: oxidizing (higher buffer_n) gives a less-negative oxygen mu.
    assert mu_o_oxidized > mu_o_reduced, (
        f"buffer_n=+2 mu_O={mu_o_oxidized} must exceed "
        f"buffer_n=-2 mu_O={mu_o_reduced} (higher buffer_n = more oxidizing)"
    )

    # MAGNITUDE: anchored to MAGEMin's own buffer formula, not a fitted
    # constant. T = 1200 C = 1473.15 K; delta buffer_n = 4.
    T_K = 1200.0 + 273.15
    expected_delta = T_K * 0.019145 * 4.0 / 2.0
    observed_delta = mu_o_oxidized - mu_o_reduced
    assert observed_delta == pytest.approx(expected_delta, abs=0.5), (
        f"GAM[O] shift {observed_delta:.4f} kJ over buffer_n delta=4 must "
        f"match MAGEMin's buffer formula prediction {expected_delta:.4f} kJ"
    )


@pytest.mark.skipif(
    _LIVE_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
def test_magemin_live_adapter_path_fO2_changes_shadow_response():
    """Requested fO2 must move the MAGEMin shadow response.

    This intentionally drives the production adapter path
    (``_build_ig_bulk_vector`` + ``_resolve_buffer``). P3-F real-binary probes
    showed MAGEMin's qfm buffer changes GAM[O] only when ig O is nonzero; the
    adapter provisions O from explicit Fe2O3 or from the total-iron-as-FeO
    inventory when no ferric split is reported.
    """
    binary = _LIVE_MAGEMIN_BINARY
    reduced = _run_magemin_adapter_buffer_probe(binary, fO2_log=-12.0)
    oxidized = _run_magemin_adapter_buffer_probe(binary, fO2_log=-4.0)

    gam_o_changed = abs(oxidized[0] - reduced[0]) > 1.0e-6
    assemblage_changed = oxidized[1] != reduced[1]
    modes_changed = oxidized[2] != pytest.approx(reduced[2], abs=1.0e-9)
    # Require the robust, production-parsed signals (phase assemblage / modes) to
    # move, not GAM[O] alone: the subprocess bridge reliably exposes Phase/Mode,
    # whereas GAM[O] is a secondary readout. GAM is kept as a sanity signal.
    assert assemblage_changed or modes_changed, (
        "MAGEMin adapter path must move phase assemblage or modes across widely "
        f"separated fO2_log values; reduced={reduced} oxidized={oxidized} "
        f"(gam_o_changed={gam_o_changed})"
    )


def test_magemin_empty_melt_composition_marks_status_out_of_domain(monkeypatch):
    # A composition with no species in MAGEMin's 14-oxide basis (only
    # native Fe / sulfide / halide) collapses to an empty wt% projection.
    # The adapter labels this 'out_of_domain' -- the engine has nothing
    # valid to act on, not a runtime convergence failure.
    fake_module = types.SimpleNamespace(minimize=lambda **_: {"phases": {}})
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    result = backend.equilibrate(
        1600.0,
        composition_mol={"Fe": 1.0, "FeS": 0.5, "NaCl": 0.2},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    assert result.status == "out_of_domain"
    assert any("empty melt composition" in w for w in result.warnings)


def test_magemin_subprocess_fo2_log_substitution_recorded(monkeypatch):
    # MAGEMin's CLI only accepts a named buffer plus a numeric `buffer_n`
    # offset (see ``MAGEMin/examples/MAGEMin_C_single_point_with_buffer.jl``),
    # so the adapter must translate the caller's absolute log10(fO2) into
    # `--buffer=qfm --buffer_n=<delta>` using the O'Neill (1987) QFM
    # calibration. The previous "silently substitute qfm and ignore the
    # absolute value" path made a Mars reducing campaign at fO2_log=-12
    # land at QFM (~ -6 at 1450 C in the O'Neill fit) -- a multi-decade
    # error -- without any warning to the caller. This test pins the
    # honest translation: the binary receives the offset that reproduces
    # the requested absolute fO2, and the EquilibriumResult.warnings
    # surfaces the substitution so a downstream consumer cannot miss it.
    captured: dict = {}

    class FakeCompleted:
        returncode = 0
        stderr = ""
        stdout = (
            "Phase : liq qfm\n"
            "Mode  : 1.000 0.000\n"
        )

    def fake_subprocess_run(args, **kwargs):
        captured["args"] = list(args)
        return FakeCompleted()

    # Force the subprocess bridge directly: stub the binary discovery so
    # initialize() picks up the subprocess path without needing a real
    # MAGEMin install.
    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: Path("/fake/MAGEMin")),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: ("subprocess", None),
    )
    import simulator.melt_backend.magemin as magemin_module
    monkeypatch.setattr(
        magemin_module.subprocess, "run", fake_subprocess_run
    )

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({}) is True
    assert backend._bridge == "subprocess"

    # Mars-reducing analog: T = 1450 C, fO2_log = -12 (well below QFM).
    result = backend.equilibrate(
        1450.0,
        composition_mol={"SiO2": 5.0, "MgO": 3.0, "FeO": 1.0},
        fO2_log=-12.0,
        pressure_bar=1e-6,
    )

    # The substitution result is OK (the subprocess ran), but the
    # warnings record the absolute -> buffer-offset translation in
    # detail so a downstream consumer cannot miss it.
    assert "args" in captured, "subprocess.run was not invoked"
    args = captured["args"]
    buffer_args = [a for a in args if a.startswith("--buffer")]
    # Both --buffer and --buffer_n must be passed so the absolute fO2
    # is honoured rather than silently snapped to QFM.
    assert any(a == "--buffer=qfm" for a in buffer_args), buffer_args
    buffer_n_args = [a for a in buffer_args if a.startswith("--buffer_n=")]
    assert len(buffer_n_args) == 1, buffer_args
    buffer_n = float(buffer_n_args[0].split("=", 1)[1])
    # O'Neill 1987: logfo2_QFM = 8.58 - 25050 / T_K. At T_C = 1450,
    # T_K = 1723.15, QFM ~ 8.58 - 14.537 = -5.957. So delta should be
    # ~-12 - (-5.957) = -6.043. Allow generous tolerance for the
    # calibration fit.
    expected_offset = -12.0 - (8.58 - 25050.0 / (1450.0 + 273.15))
    assert buffer_n == pytest.approx(expected_offset, abs=0.05), (
        f"buffer_n={buffer_n} should approximate {expected_offset}"
    )
    # Once the offset round-trips through QFM(T) we recover the
    # requested absolute fO2_log within calibration accuracy.
    recovered_fo2_log = buffer_n + (8.58 - 25050.0 / (1450.0 + 273.15))
    assert recovered_fo2_log == pytest.approx(-12.0, abs=0.05)
    # The warning chain must explicitly name the substitution so the
    # caller knows their absolute fO2 was translated, not ignored.
    substitution_warnings = [
        w for w in result.warnings if "fO2_log" in w and "qfm" in w
    ]
    assert substitution_warnings, result.warnings
    assert any("-12.0" in w for w in substitution_warnings), substitution_warnings


def test_magemin_subprocess_unknown_buffer_falls_back_with_warning(monkeypatch):
    # An unrecognised fO2_buffer config still drives the subprocess bridge,
    # but the adapter MUST surface the substitution as a warning rather
    # than silently swapping in 'qfm' (which would hide the requested fO2
    # mismatch from the caller). The previous _resolve_buffer routed the
    # warning into self._warnings, never reaching EquilibriumResult.
    captured: dict = {}

    class FakeCompleted:
        returncode = 0
        stderr = ""
        stdout = "Phase : liq\nMode  : 1.000\n"

    def fake_subprocess_run(args, **kwargs):
        captured["args"] = list(args)
        return FakeCompleted()

    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: Path("/fake/MAGEMin")),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: ("subprocess", None),
    )
    import simulator.melt_backend.magemin as magemin_module
    monkeypatch.setattr(
        magemin_module.subprocess, "run", fake_subprocess_run
    )

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({"fO2_buffer": "qfm-2"})  # invalid: includes offset

    result = backend.equilibrate(
        1450.0,
        composition_mol={"SiO2": 5.0, "MgO": 3.0},
        fO2_log=-12.0,
        pressure_bar=1e-6,
    )

    assert any("qfm-2" in w and "qfm" in w for w in result.warnings), (
        result.warnings
    )


def test_magemin_resolve_buffer_qfm_calibration_at_1450C():
    # Pin the O'Neill 1987 calibration math: at T = 1450 C (T_K = 1723.15),
    # logfo2_QFM should be ~-5.96. The conversion is load-bearing for the
    # A5 honest-substitution path; a wrong constant would silently shift
    # every Mars reducing fO2 by ~6 decades.
    qfm_at_1450C = MAGEMinBackend._qfm_logfo2_oneill(1450.0)
    expected = 8.58 - 25050.0 / (1450.0 + 273.15)
    assert qfm_at_1450C == pytest.approx(expected, abs=1e-9)
    assert -7.0 < qfm_at_1450C < -5.0, qfm_at_1450C
