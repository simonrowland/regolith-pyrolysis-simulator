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
    _parse_magemin_endmember_fractions,
    _parse_magemin_gbase_tables,
    _parse_magemin_matlab_assemblage,
    _parse_magemin_sys_oxide_row,
)
from simulator.melt_backend.pure_phase import (
    GIBBS_CONVENTION_APPARENT_298,
    PHASE_NOT_STABLE_AT_TP,
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

    The S assertion is the factor-correction catch: the raw matlab column is
    31.7 J/K (molar x factor 1/3); only the corrected value lands near 95.14.
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
    """q stable at 298.15/1000 K; at 1500 K q refuses typed and trd answers."""
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
    # 1500 K: quartz is metastable (tridymite stable) -> typed absence for
    # S/Cp/H, but the endmember-table G is still returned.
    assert q_1500.G_J_mol is not None
    assert q_1500.S_J_K_mol is None
    assert {a.property for a in q_1500.absences} == {"S", "Cp", "H"}
    assert all(a.reason == PHASE_NOT_STABLE_AT_TP for a in q_1500.absences)
    assert trd_1500.polymorph == "tridymite"
    assert trd_1500.absences == ()
    # JANAF prints no tridymite table; the quartz table's 1500 K metastable
    # extension (144.925) must bracket trd within a few %.
    assert trd_1500.S_J_K_mol == pytest.approx(144.925, rel=0.03)
    assert q_1000.S_J_K_mol < trd_1500.S_J_K_mol


@needs_magemin_binary
def test_magemin_cristobalite_g_only_typed_absence():
    backend = _magemin_backend()
    try:
        r = backend.pure_phase_properties(
            "crst", temperature_K=1000.0, pressure_bar=1.0
        )
    finally:
        backend.close()
    assert r.polymorph == "cristobalite"
    assert r.G_J_mol == pytest.approx(-981.0e3, abs=2.0e3)
    assert r.S_J_K_mol is None and r.Cp_J_K_mol is None and r.H_J_mol is None
    assert {a.property for a in r.absences} == {"S", "Cp", "H"}
    assert all(a.reason == PHASE_NOT_STABLE_AT_TP for a in r.absences)


@needs_magemin_binary
def test_magemin_enstatite_g_on_mgsio3_basis_typed_absence():
    """en G must be on the MgSiO3 basis (engine endmember is Mg2Si2O6)."""
    backend = _magemin_backend()
    try:
        r = backend.pure_phase_properties(
            "en", temperature_K=1000.0, pressure_bar=1.0
        )
    finally:
        backend.close()
    assert r.formula == "MgSiO3"
    assert "Mg2Si2O6" in r.formula_basis
    # Endmember-table gbase is -3325.230 kJ per Mg2Si2O6; /2 -> per MgSiO3.
    assert r.G_J_mol == pytest.approx(-1662.6e3, abs=2.0e3)
    assert r.S_J_K_mol is None and r.Cp_J_K_mol is None and r.H_J_mol is None
    assert all(a.reason == PHASE_NOT_STABLE_AT_TP for a in r.absences)


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
            pressure_kbar=1.0e-4,
        )
    finally:
        backend.close()
    sys_row = _parse_magemin_sys_oxide_row(matlab_text)
    assert sys_row is not None
    # The trap itself: the binary ran KLB1, not pure MgO.
    assert sys_row["SiO2"] > 0.1
    with pytest.raises(PurePhaseBulkMismatchError):
        _assert_magemin_bulk_echo({"MgO": 100.0}, matlab_text)


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


def test_endmember_fractions_positional_zip_with_padding():
    """Header '-' pads must not shift the name<->value alignment (two ol
    instances, dominant fo-rich first)."""
    text = (
        "End-members fractions[wt fr]\n"
        "            mont         fa         fo        cfm          -\n"
        "    ol   0.00000    0.00021    0.99979    0.00000          -\n"
        "            mont         fa         fo        cfm          -\n"
        "    ol   0.99982    0.00118    0.00000   -0.00100          -\n"
        "             per         wu          -\n"
        "  fper   0.99932    0.00068          -\n"
        "\n"
        "Site fractions:\n"
    )
    fracs = _parse_magemin_endmember_fractions(text)
    assert fracs["ol"][0]["fo"] == pytest.approx(0.99979)
    assert fracs["ol"][1]["mont"] == pytest.approx(0.99982)
    assert fracs["fper"][0]["per"] == pytest.approx(0.99932)


def test_matlab_assemblage_keeps_instance_order():
    text = (
        "Stable mineral assemblage:\n"
        " phase   fraction[wt]          G[J]  V_molar[cm3/mol]    V_partial[cm3]"
        "     Cp[kJ/K]   Rho[kg/m3]   Alpha[1/K] Entropy[J/K]  Enthalpy[J]"
        " BulkMod[GPa] ShearMod[GPa]     Vp[km/s]     Vs[km/s]\n"
        "    ol       +0.84348   -2317.86613         +44.98000         +12.89060"
        "     +0.17605  +3190.00351  +0.00000395    +0.093602      -680.4303"
        "      +111.04       +69.68        +8.00        +4.67\n"
        "    ol       +0.06921   -2413.18248         +52.73479          +1.12227"
        "     +0.17677  +3006.70722  +0.00000382    +0.099813      -706.0491"
        "       +98.64       +42.81        +7.20        +3.77\n"
        "   SYS                   -771.86636                            +0.00312\n"
    )
    rows = _parse_magemin_matlab_assemblage(text)
    assert len(rows["ol"]) == 2
    assert rows["ol"][0]["frac_wt"] == pytest.approx(0.84348)
    assert rows["ol"][0]["Cp_kJ_K"] == pytest.approx(0.17605)


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
