"""Tests for pure-phase standard-state access (P4a-1).

Covers the two new typed accessors:
  * ``MAGEMinBackend.pure_phase_properties`` (subprocess binary, ig database)
  * ``ThermoEngineBackend.pure_phase_properties`` (warm worker, Berman db)

Anchors are external ground truth (NIST-JANAF 4th printed values) with
tolerances that absorb dataset-assessment differences (Berman/HP vs JANAF
~1 kJ on Delta_fH, ~1 % on S/Cp) while still catching unit slips (x1000),
formula-basis slips (x2), polymorph swaps, and bulk swaps.  JANAF reference
values used here (cr tables, p = 1 bar):
  MgO:        S(298.15)=26.924, Cp(298.15)=37.106, Cp(1000)=51.208,
              S(1000)=82.262, H(1000)-H(298)=33.001 kJ/mol, dHf298=-601.241
  SiO2 quartz:S(298.15)=41.463, Cp(298.15)=44.589, S(1000)=116.018,
              Cp(1000)=68.952, S(1500, metastable ext.)=144.925,
              dHf298=-910.857
  Mg2SiO4:    S(298.15)=95.14, Cp(298.15)=118.688, Cp(1000)=174.607,
              H(1000)-H(298)=109.392 kJ/mol, dHf298=-2176.935
  MgSiO3 cr (clinoenstatite): Cp(1000)=120.34
"""

from __future__ import annotations

import os
import pickle
from pathlib import Path

import pytest

from simulator.melt_backend.magemin import (
    MAGEMinBackend,
    _assert_magemin_bulk_echo,
    _check_magemin_solver_status,
    _lambda_stencil_warning,
    _parse_magemin_gbase_tables,
    _parse_magemin_sys_oxide_row,
)
from simulator.melt_backend.pure_phase import (
    GIBBS_CONVENTION_APPARENT_298,
    PurePhaseAccessError,
    PurePhaseBulkMismatchError,
    PurePhaseProperties,
    PurePhaseUnknownSymbolError,
)

# --- availability guards (true skipif, resolved at collection) -------------

_LIVE_MAGEMIN_BINARY = os.environ.get("REGOLITH_MAGEMIN_BINARY") or (
    lambda p: str(p) if p is not None else None
)(MAGEMinBackend._locate_binary(None))

needs_magemin_binary = pytest.mark.skipif(
    _LIVE_MAGEMIN_BINARY is None,
    reason="no MAGEMin binary (set REGOLITH_MAGEMIN_BINARY or build engines/magemin)",
)


def _thermoengine_available() -> bool:
    try:
        from simulator.engine_local_config import setup_thermoengine_dylib_path

        setup_thermoengine_dylib_path()
        import thermoengine  # noqa: F401
        return True
    except Exception:
        return False


needs_thermoengine = pytest.mark.skipif(
    not _thermoengine_available(),
    reason="ThermoEngine dylibs/package unavailable on this machine",
)


def _magemin_backend() -> MAGEMinBackend:
    backend = MAGEMinBackend()
    assert backend.initialize(
        {"binary_path": _LIVE_MAGEMIN_BINARY, "warm_worker": False}
    )
    return backend


# --- MAGEMin: live access path ---------------------------------------------


@needs_magemin_binary
def test_magemin_forsterite_full_property_row_298():
    """fo at 298.15 K: full G/S/Cp/H against JANAF-anchored bands.

    S and Cp are central differences of the pure-endmember G.  A sign error
    on dG/dT lands near -95 J/K and fails the S band.
    """
    backend = _magemin_backend()
    try:
        r = backend.pure_phase_properties(
            "fo", temperature_K=298.15, pressure_bar=1.0
        )
    finally:
        backend.close()
    assert r.engine == "magemin"
    assert r.phase_id == "fo"
    assert r.host_phase == "ol"
    assert r.polymorph == "forsterite"
    assert r.formula == "Mg2SiO4"
    assert r.database.startswith("ig")
    assert r.gibbs_convention == GIBBS_CONVENTION_APPARENT_298
    assert r.absences == ()
    # Apparent G(298.15) from JANAF: dHf - T*S = -2176.935 - 298.15*0.09514
    # = -2205.30 kJ/mol; Berman/HP sit ~4 kJ higher (-2202.4/-2200.8).
    assert r.G_J_mol == pytest.approx(-2202.0e3, abs=4.0e3)
    assert r.S_J_K_mol == pytest.approx(95.14, rel=0.01)
    assert r.Cp_J_K_mol == pytest.approx(118.688, rel=0.01)
    # H(298.15) is dHf(298.15) in the apparent convention.
    assert r.H_J_mol == pytest.approx(-2176.9e3, abs=5.0e3)
    # S/Cp/H are the same endmember as G, so the Gibbs relation closes.
    assert r.G_J_mol - (r.H_J_mol - r.temperature_K * r.S_J_K_mol) == (
        pytest.approx(0.0, abs=1.0e-6)
    )
    assert any("pure-endmember gbase" in note for note in r.warnings)


@needs_magemin_binary
def test_magemin_periclase_1000K():
    backend = _magemin_backend()
    try:
        r1000 = backend.pure_phase_properties(
            "per", temperature_K=1000.0, pressure_bar=1.0
        )
        r298 = backend.pure_phase_properties(
            "per", temperature_K=298.15, pressure_bar=1.0
        )
    finally:
        backend.close()
    assert r1000.absences == ()
    assert r1000.G_J_mol == pytest.approx(-650.3e3, abs=2.0e3)
    assert r1000.S_J_K_mol == pytest.approx(82.262, rel=0.02)
    assert r1000.Cp_J_K_mol == pytest.approx(51.208, rel=0.02)
    # H increment H(1000)-H(298.15) = 33.001 kJ/mol (JANAF), convention-free.
    delta_h_kj = (r1000.H_J_mol - r298.H_J_mol) / 1000.0
    assert delta_h_kj == pytest.approx(33.001, rel=0.02)


@needs_magemin_binary
def test_magemin_silica_polymorph_ladder():
    """q and trd both answer, including metastable quartz at 1500 K.

    S/Cp/H are derivatives of the endmember G, so stability in the
    assemblage is not required.  Near-pure silica bulks return solver
    status -1 (PGE guard); that is a warning, not a refusal.
    """
    backend = _magemin_backend()
    try:
        q_1000 = backend.pure_phase_properties(
            "q", temperature_K=1000.0, pressure_bar=1.0
        )
        q_1500 = backend.pure_phase_properties(
            "q", temperature_K=1500.0, pressure_bar=1.0
        )
        trd_1500 = backend.pure_phase_properties(
            "trd", temperature_K=1500.0, pressure_bar=1.0
        )
    finally:
        backend.close()
    assert q_1000.polymorph == "quartz"
    assert q_1000.absences == ()
    assert q_1000.S_J_K_mol == pytest.approx(116.018, rel=0.01)
    assert q_1000.Cp_J_K_mol == pytest.approx(68.952, rel=0.01)
    assert any("status -1" in note for note in q_1000.warnings)
    # 1500 K: quartz is metastable, but its endmember G still has a
    # derivative.  JANAF's metastable extension is S(1500) = 144.925.
    assert q_1500.absences == ()
    assert q_1500.S_J_K_mol == pytest.approx(144.925, rel=0.03)
    assert q_1500.Cp_J_K_mol is not None and q_1500.H_J_mol is not None
    assert trd_1500.polymorph == "tridymite"
    assert trd_1500.absences == ()
    # JANAF prints no tridymite table; the quartz table's 1500 K metastable
    # extension (144.925) must bracket trd within a few %.
    assert trd_1500.S_J_K_mol == pytest.approx(144.925, rel=0.03)
    assert q_1000.S_J_K_mol < trd_1500.S_J_K_mol


@needs_magemin_binary
def test_magemin_cristobalite_derived_from_endmember_g():
    """crst is metastable at 1000 K; S/Cp still come from its own G(T).

    ds62 cristobalite (TC_endmembers.c "crst"): S0 = 50.86 J/K,
    Cp = a + b T + c T^-2 with a = 72.7 J/K, b = 1.304e-3 J/K^2,
    c = -4.129e6 J K.  At 1000 K that polynomial is 69.875 J/K.
    Integrating Cp/T from 298.15 K gives S(1000) = 118.59 J/K.
    """
    backend = _magemin_backend()
    try:
        r = backend.pure_phase_properties(
            "crst", temperature_K=1000.0, pressure_bar=1.0
        )
    finally:
        backend.close()
    assert r.polymorph == "cristobalite"
    assert r.absences == ()
    assert r.G_J_mol == pytest.approx(-981.0e3, abs=2.0e3)
    assert r.Cp_J_K_mol == pytest.approx(69.875, abs=1.0)
    assert r.S_J_K_mol == pytest.approx(118.59, abs=1.0)
    assert r.G_J_mol - (r.H_J_mol - r.temperature_K * r.S_J_K_mol) == (
        pytest.approx(0.0, abs=1.0e-6)
    )


@needs_magemin_binary
def test_magemin_enstatite_g_on_mgsio3_basis():
    """en is on the MgSiO3 basis (engine endmember is Mg2Si2O6 / 2).

    ds62 en S0 = 0.1325 kJ/K per Mg2Si2O6 = 66.25 J/K per MgSiO3
    (TC_endmembers.c "en").  A missed /2 would report ~132.5.
    """
    backend = _magemin_backend()
    try:
        r = backend.pure_phase_properties(
            "en", temperature_K=1000.0, pressure_bar=1.0
        )
        r298 = backend.pure_phase_properties(
            "en", temperature_K=298.15, pressure_bar=1.0
        )
    finally:
        backend.close()
    assert r.formula == "MgSiO3"
    assert "Mg2Si2O6" in r.formula_basis
    assert r.absences == ()
    assert r.G_J_mol == pytest.approx(-1662.6e3, abs=2.0e3)
    assert r298.S_J_K_mol == pytest.approx(66.25, abs=0.2)
    # 2x basis slip would land near 252 J/K, outside this band.
    assert r.Cp_J_K_mol == pytest.approx(126.0, abs=5.0)


@needs_magemin_binary
def test_magemin_unknown_symbol_raises():
    backend = _magemin_backend()
    try:
        with pytest.raises(PurePhaseUnknownSymbolError):
            backend.pure_phase_properties(
                "ky", temperature_K=298.15, pressure_bar=1.0
            )
    finally:
        backend.close()


def test_magemin_uninitialized_backend_refuses():
    backend = MAGEMinBackend()
    with pytest.raises(PurePhaseAccessError):
        backend.pure_phase_properties(
            "fo", temperature_K=298.15, pressure_bar=1.0
        )


# --- MAGEMin: KLB1 bulk-echo guard -----------------------------------------


def _matlab_text_with_sys_row(sys_values, oxides=None) -> str:
    oxides = oxides or [
        "SiO2", "Al2O3", "CaO", "MgO", "FeO", "K2O", "Na2O", "TiO2",
        "O", "Cr2O3", "H2O",
    ]
    header = "  " + "  ".join(f"{name:>8}" for name in oxides)
    row = "   SYS " + "  ".join(f"{v:8.5f}" for v in sys_values)
    return f"Oxide compositions [wt fr]:\n{header}\n{row}\n"


def test_bulk_echo_guard_accepts_matching_bulk():
    # 42.7059/57.2941 wt% Mg2SiO4 -> wt fractions 0.427/0.573.
    text = _matlab_text_with_sys_row(
        [0.42706, 0.0, 0.0, 0.57294, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    )
    _assert_magemin_bulk_echo({"SiO2": 42.7059, "MgO": 57.2941}, text)


def test_bulk_echo_guard_refuses_klb1_bulk():
    """KLB1's SYS row (SiO2 ~0.385, FeO ~0.059) must trip the guard."""
    text = _matlab_text_with_sys_row(
        [0.38451, 0.01774, 0.02821, 0.50510, 0.05879, 0.00010, 0.00250,
         0.00100, 0.00096, 0.00109, 0.0]
    )
    with pytest.raises(PurePhaseBulkMismatchError) as excinfo:
        _assert_magemin_bulk_echo({"SiO2": 100.0}, text)
    assert "different bulk" in str(excinfo.value)


def test_bulk_echo_guard_refuses_missing_sys_row():
    with pytest.raises(PurePhaseBulkMismatchError):
        _assert_magemin_bulk_echo({"SiO2": 100.0}, "no oxide block here\n")


@needs_magemin_binary
def test_klb1_fallback_refused_live():
    """Live proof the upstream trap is real and the guard catches it.

    A zero-first-component (SiO2=0) bulk makes upstream MAGEMin silently run
    the default KLB1 test bulk; the SYS echo then disagrees with the request
    and the guard must raise.
    """
    backend = _magemin_backend()
    try:
        _stdout, matlab_text, _w = backend._run_pure_phase_probe(
            bulk_wt_ig={"MgO": 100.0},
            temperature_C=25.0,
            pressure_kbar=1.0e-3,  # 1 bar; --Pres is kbar
        )
    finally:
        backend.close()
    sys_row = _parse_magemin_sys_oxide_row(matlab_text)
    assert sys_row is not None
    # The trap itself: the binary ran KLB1, not pure MgO.
    assert sys_row["SiO2"] > 0.1
    with pytest.raises(PurePhaseBulkMismatchError):
        _assert_magemin_bulk_echo({"MgO": 100.0}, matlab_text)


@needs_magemin_binary
def test_magemin_periclase_high_pressure_matches_volume_integral():
    """G(10 kbar) - G(1 bar) must match integral V dP, not a 10x-low pressure.

    Holland ds62 periclase, the volume this binary's EOS uses
    (TC_endmembers.c "per"): V = 1.125 kJ/kbar at the 298.15 K reference.
    Delta-P = 10 kbar - 0.001 kbar = 9.999 kbar.
    Incompressible integral V dP = 1.125 * 9.999 = 11.248875 kJ.
    Compressibility at 10 kbar moves that by only tens of joules.
    Passing bar * 1e-4 instead of bar * 1e-3 evaluates 1 kbar and yields
    ~1.12 kJ, outside the +/-1 kJ band.
    """
    integral_J = 1.125 * (10.0 - 0.001) * 1000.0
    backend = _magemin_backend()
    try:
        low = backend.pure_phase_properties(
            "per", temperature_K=298.15, pressure_bar=1.0
        )
        high = backend.pure_phase_properties(
            "per", temperature_K=298.15, pressure_bar=10_000.0
        )
    finally:
        backend.close()
    assert low.pressure_bar == 1.0
    assert high.pressure_bar == 10_000.0
    assert high.G_J_mol - low.G_J_mol == pytest.approx(integral_J, abs=1.0e3)


# --- MAGEMin: parser unit tests (no binary) --------------------------------


def test_gbase_tables_parse_pp_and_ss_endmembers():
    stdout = (
        "   ne:  -2292.229532  +0.333942\n"
        "   q:   -981.511198  +0.779197\n"
        "  trd:  -981.191364  +0.779197\n"
        "   ol:\n"
        "----\n"
        "         mont           fa           fo          cfm\n"
        "  -2435.70603  -1707.96412  -2340.23243  -2024.09828\n"
        "  fper:\n"
        "----\n"
        "          per           wu\n"
        "   -650.29822   -352.31396\n"
    )
    pp, ss = _parse_magemin_gbase_tables(stdout)
    assert pp["q"] == pytest.approx((-981.511198, 0.779197))
    assert pp["trd"][0] == pytest.approx(-981.191364)
    assert ss["ol"]["fo"] == pytest.approx(-2340.23243)
    assert ss["fper"]["per"] == pytest.approx(-650.29822)


def test_gbase_nonfinite_solution_endmember_refuses():
    """float() accepts nan/inf; a non-finite gbase must refuse, not pass."""
    stdout = (
        "   ol:\n"
        "----\n"
        "         mont           fa           fo          cfm\n"
        "  -2435.70603  nan  -2340.23243  -2024.09828\n"
    )
    with pytest.raises(PurePhaseAccessError, match="not finite"):
        _parse_magemin_gbase_tables(stdout)


def test_sys_oxide_row_nonfinite_refuses():
    """A NaN SYS value must not slip the bulk-echo comparison (NaN > tol is False)."""
    text = _matlab_text_with_sys_row(
        [float("nan"), 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    )
    assert _parse_magemin_sys_oxide_row(text) is None
    with pytest.raises(PurePhaseBulkMismatchError):
        _assert_magemin_bulk_echo({"MgO": 100.0}, text)


def test_solver_status_success_is_silent_and_failure_refuses():
    warnings: list[str] = []
    _check_magemin_solver_status(
        "Status             :            0 \t [success]\n", warnings
    )
    assert warnings == []
    _check_magemin_solver_status(
        "Status : 1 [success, under-relaxed]\n", warnings
    )
    assert warnings and "under-relaxed" in warnings[-1]
    guard: list[str] = []
    _check_magemin_solver_status("Status             :           -1\n", guard)
    assert guard and "status -1" in guard[0]
    for text in (
        "Status : 3 [failure, reached maximum iterations]\n",
        "Status : 4 [failure, terminated due to slow convergence or divergence]\n",
        "Status : 9\n",
        "no status here\n",
    ):
        with pytest.raises(PurePhaseAccessError):
            _check_magemin_solver_status(text, [])


def test_quartz_lambda_stencil_is_flagged_only_when_straddled():
    assert _lambda_stencil_warning("q", 847.0) is not None
    assert _lambda_stencil_warning("q", 1000.0) is None
    assert _lambda_stencil_warning("fo", 847.0) is None


# --- ThermoEngine: live access path ----------------------------------------


def _activity_passthrough(_mu, _mu0, _temperature_K):
    return 1.0


@pytest.fixture(scope="module")
def te_backend():
    from simulator.melt_backend.thermoengine import ThermoEngineBackend

    backend = ThermoEngineBackend()
    backend.initialize({})
    yield backend
    backend.close()


@needs_thermoengine
def test_thermoengine_forsterite_full_property_row_298(te_backend):
    r = te_backend.pure_phase_properties(
        "Fo", temperature_K=298.15, pressure_bar=1.0
    )
    assert r.engine == "thermoengine"
    assert r.phase_id == "Fo"
    assert r.polymorph == "forsterite"
    assert r.formula == "Mg2SiO4"
    assert "Berman" in r.database
    assert r.gibbs_convention == GIBBS_CONVENTION_APPARENT_298
    assert r.absences == ()
    assert r.G_J_mol == pytest.approx(-2202.0e3, abs=4.0e3)
    assert r.S_J_K_mol == pytest.approx(95.14, rel=0.02)
    assert r.Cp_J_K_mol == pytest.approx(118.688, rel=0.01)
    assert r.H_J_mol == pytest.approx(-2176.9e3, abs=5.0e3)
    # Internal consistency: the four numbers are one molar property set.
    assert r.G_J_mol - (r.H_J_mol - r.temperature_K * r.S_J_K_mol) == 0.0


@needs_thermoengine
def test_thermoengine_silica_polymorphs_discriminated(te_backend):
    """Qz vs Crs differ by ~2 J/K in S and ~3.7 kJ in G at 298.15 K; a
    polymorph swap fails these."""
    qz = te_backend.pure_phase_properties(
        "Qz", temperature_K=298.15, pressure_bar=1.0
    )
    crs = te_backend.pure_phase_properties(
        "Crs", temperature_K=298.15, pressure_bar=1.0
    )
    assert qz.polymorph == "quartz" and crs.polymorph == "cristobalite"
    assert qz.S_J_K_mol == pytest.approx(41.463, abs=0.5)
    assert crs.S_J_K_mol == pytest.approx(43.394, abs=0.5)
    assert qz.S_J_K_mol < crs.S_J_K_mol
    assert qz.G_J_mol < crs.G_J_mol  # quartz more stable at 298.15 K


@needs_thermoengine
def test_thermoengine_clinoenstatite_matches_janaf_mgsio3(te_backend):
    """JANAF MgSiO3(cr) is the clinoenstatite assessment: cEn Cp(1000)."""
    r = te_backend.pure_phase_properties(
        "cEn", temperature_K=1000.0, pressure_bar=1.0
    )
    assert r.polymorph == "clinoenstatite"
    assert r.formula == "MgSiO3"
    assert r.Cp_J_K_mol == pytest.approx(120.34, rel=0.02)
    assert r.G_J_mol == pytest.approx(-1663.2e3, abs=2.0e3)


def test_thermoengine_missing_formula_refuses():
    from engines.alphamelts.thermoengine import _thermoengine_phase_formula

    class _Bare:
        formula = ""

    with pytest.raises(PurePhaseAccessError, match="no formula"):
        _thermoengine_phase_formula(_Bare(), "Fo")

    class _Present:
        formula = "Mg2SiO4"

    assert _thermoengine_phase_formula(_Present(), "Fo") == "Mg2SiO4"

    class _Method:
        def formula(self):
            return "SiO2"

    assert _thermoengine_phase_formula(_Method(), "Qz") == "SiO2"


def test_quartz_provenance_records_adjustment_flag():
    from engines.alphamelts.thermoengine import _quartz_provenance

    on, on_note = _quartz_provenance("Berman 1988", applied=True)
    off, off_note = _quartz_provenance("Berman 1988", applied=False)
    unread, unread_note = _quartz_provenance("Berman 1988", applied=None)
    assert "QUARTZ_ADJUSTMENT=-1291 J applied" in on
    assert "isQuartzCorrectionUsed=True" in on
    assert "-1291" in on_note
    assert "not applied" in off and "isQuartzCorrectionUsed=False" in off
    assert "-1291" in off_note
    # Unread flag is not stamped as a value.
    assert unread == "Berman 1988"
    assert "could not be read" in unread_note


@needs_thermoengine
def test_thermoengine_quartz_records_melts_adjustment(te_backend):
    qz = te_backend.pure_phase_properties(
        "Qz", temperature_K=298.15, pressure_bar=1.0
    )
    fo = te_backend.pure_phase_properties(
        "Fo", temperature_K=298.15, pressure_bar=1.0
    )
    assert "QUARTZ_ADJUSTMENT=-1291 J applied" in qz.database
    assert "isQuartzCorrectionUsed=True" in qz.database
    assert any("-1291" in note for note in qz.warnings)
    assert "QUARTZ_ADJUSTMENT" not in fo.database
    assert qz.formula == "SiO2"


@needs_thermoengine
def test_thermoengine_unknown_symbol_raises(te_backend):
    with pytest.raises(PurePhaseUnknownSymbolError):
        te_backend.pure_phase_properties(
            "NotAPhase", temperature_K=298.15, pressure_bar=1.0
        )


def test_thermoengine_isolation_guard_without_worker():
    from engines.alphamelts.thermoengine import (
        ThermoEngineIsolationError,
        ThermoEngineTransport,
    )

    transport = ThermoEngineTransport(activity_converter=_activity_passthrough)
    with pytest.raises(ThermoEngineIsolationError):
        transport.pure_phase_properties(
            "Fo", temperature_K=298.15, pressure_bar=1.0
        )


def test_pure_phase_result_pickle_roundtrip():
    r = PurePhaseProperties(
        engine="magemin",
        phase_id="fo",
        host_phase="ol",
        polymorph="forsterite",
        formula="Mg2SiO4",
        formula_basis="per 1 mol Mg2SiO4",
        database="ig",
        gibbs_convention=GIBBS_CONVENTION_APPARENT_298,
        temperature_K=298.15,
        pressure_bar=1.0,
        G_J_mol=-2200833.99,
        S_J_K_mol=95.11,
        Cp_J_K_mol=118.64,
        H_J_mol=-2172384.7,
    )
    assert pickle.loads(pickle.dumps(r)) == r
