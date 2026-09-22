"""Tests for the JANAF pure-phase score consumer arithmetic (P4a-2).

No engine is touched: engine sides are fixed PurePhaseProperties fixtures
(verbatim probe values, noted per fixture) and the JANAF side is the
printed table row.  The anchor is a hand-computed reaction sum.  At
1000 K the forsterite reaction is scored.  The MgSiO3 sum at 1000 K is
still the printed arithmetic (Mg-012 form II), but it is not an engine
residual: Mg-012's bands are the printed tokens i/ii/iii, not
clinoenstatite, so a cEn row at 1000 K is refused.
"""

from __future__ import annotations

import pytest

from simulator.melt_backend.pure_phase import (
    GIBBS_CONVENTION_APPARENT_298,
    PHASE_NOT_STABLE_AT_TP,
    PropertyAbsence,
    PurePhaseProperties,
)
from simulator.melt_backend.pure_phase_janaf_score import (
    DEFAULT_PHASE_REQUESTS,
    DEFAULT_TEMPERATURES_K,
    JANAF_TABLES,
    NO_JANAF_TABLE_FOR_POLYMORPH,
    POLYMORPH_MISMATCH,
    PolymorphMismatchError,
    REACTION_ENSTATITE,
    REACTION_FORSTERITE,
    JanafValues,
    PhaseScoreRequest,
    bands_for_table_id,
    build_phase_row,
    build_reaction_row,
    engine_phase_polymorph,
    enthalpy_increment_kJ_mol,
    janaf_values_at,
    load_janaf_table,
    preflight_phase_request,
    preflight_reaction,
    reaction_sum,
    require_polymorph_match,
)

# --- printed JANAF rows (data/literature/compilations/janaf/tables) --------
# 1000 K formation_gibbs_energy, kJ/mol, as printed:
JANAF_DFG_1000 = {
    'Mg-008': -492.952,   # MgO(cr)
    'O-037': -730.256,    # SiO2 quartz
    'Mg-012': -1257.958,  # MgSiO3(cr) printed form II, not a clino label
    'Mg-028': -1778.598,  # Mg2SiO4(cr) forsterite
}
# 298.15 K:
JANAF_DFG_298 = {
    'Mg-008': -568.945,
    'O-037': -856.443,
    'Mg-012': -1462.023,
    'Mg-028': -2057.879,
}

# Engine probe fixtures (P4a scout probe_thermoengine.py, verbatim; J/mol):
TE_G_1000_J = {
    'Fo': -2341132.8729,
    'Per': -650763.6656,
    'Qz': -982731.2394,
    'cEn': -1663150.6062,
    'En': -1663433.3916,
}
# MAGEMin Verb=1 endmember gbase at 1000 K (kJ/mol; en is Mg2Si2O6 basis):
MM_G_1000_KJ = {'fo': -2340.23243, 'per': -650.29822, 'q': -981.511198}

TE_POLY = {
    'Fo': 'forsterite',
    'Per': 'periclase',
    'Qz': 'quartz',
    'cEn': 'clinoenstatite',
    'En': 'orthoenstatite',
}
MM_POLY = {'fo': 'forsterite', 'per': 'periclase', 'q': 'quartz'}


def _props(engine, phase_id, polymorph, G_J_mol, **kwargs) -> PurePhaseProperties:
    return PurePhaseProperties(
        engine=engine,
        phase_id=phase_id,
        host_phase=kwargs.pop('host_phase', None),
        polymorph=polymorph,
        formula='x',
        formula_basis='per 1 mol x',
        database='fixture',
        gibbs_convention=GIBBS_CONVENTION_APPARENT_298,
        temperature_K=kwargs.pop('temperature_K', 1000.0),
        pressure_bar=1.0,
        G_J_mol=G_J_mol,
        S_J_K_mol=kwargs.pop('S_J_K_mol', None),
        Cp_J_K_mol=kwargs.pop('Cp_J_K_mol', None),
        H_J_mol=kwargs.pop('H_J_mol', None),
        absences=kwargs.pop('absences', ()),
        warnings=kwargs.pop('warnings', ()),
    )


def _janaf_stub(table_id, temperature_K):
    values = JANAF_DFG_1000 if temperature_K == 1000.0 else JANAF_DFG_298
    return JanafValues(
        temperature_K=temperature_K,
        Cp_J_K_mol=174.607 if table_id == 'Mg-028' else None,
        S_J_K_mol=277.418 if table_id == 'Mg-028' else None,
        enthalpy_increment_kJ_mol=109.392 if table_id == 'Mg-028' else None,
        formation_gibbs_kJ_mol=values[table_id],
        formation_enthalpy_kJ_mol=None,
    )


# --- reaction-sum arithmetic (hand-computed anchors) ------------------------


def test_reaction_sum_janaf_forsterite_1000K_hand_computed():
    # -1778.598 - 2*(-492.952) - (-730.256) = -62.438 kJ/mol
    terms = [
        (1.0, JANAF_DFG_1000['Mg-028']),
        (-2.0, JANAF_DFG_1000['Mg-008']),
        (-1.0, JANAF_DFG_1000['O-037']),
    ]
    assert reaction_sum(terms) == pytest.approx(-62.438, abs=1e-9)


def test_reaction_sum_janaf_enstatite_1000K_hand_computed():
    # -1257.958 - (-492.952) - (-730.256) = -34.750 kJ/mol
    terms = [
        (1.0, JANAF_DFG_1000['Mg-012']),
        (-1.0, JANAF_DFG_1000['Mg-008']),
        (-1.0, JANAF_DFG_1000['O-037']),
    ]
    assert reaction_sum(terms) == pytest.approx(-34.750, abs=1e-9)


def test_reaction_sum_janaf_298_hand_computed():
    # forsterite: -2057.879 - 2*(-568.945) - (-856.443) = -63.546
    # enstatite:  -1462.023 - (-568.945) - (-856.443) = -36.635
    fo = reaction_sum(
        [(1.0, JANAF_DFG_298['Mg-028']), (-2.0, JANAF_DFG_298['Mg-008']),
         (-1.0, JANAF_DFG_298['O-037'])]
    )
    en = reaction_sum(
        [(1.0, JANAF_DFG_298['Mg-012']), (-1.0, JANAF_DFG_298['Mg-008']),
         (-1.0, JANAF_DFG_298['O-037'])]
    )
    assert fo == pytest.approx(-63.546, abs=1e-9)
    assert en == pytest.approx(-36.635, abs=1e-9)


def test_build_reaction_row_thermoengine_apparent_sum_1000K():
    """TE apparent-G sum through the real builder = hand-computed -56.874."""
    fixtures = {
        phase: _props('thermoengine', phase, TE_POLY[phase], G_J)
        for phase, G_J in TE_G_1000_J.items()
    }
    row = build_reaction_row(
        REACTION_FORSTERITE,
        'thermoengine',
        1000.0,
        engine_query=lambda phase_id, T: fixtures[phase_id],
        janaf_query=_janaf_stub,
    )
    # -2341132.8729 + 2*650763.6656 + 982731.2394 = -56874.3023 J -> -56.874 kJ
    assert row.drG_engine_kJ_mol == pytest.approx(-56.8743023, abs=1e-6)
    assert row.drG_janaf_kJ_mol == pytest.approx(-62.438, abs=1e-9)
    assert row.residual_kJ_mol == pytest.approx(5.5636977, abs=1e-6)
    assert row.sio2_polymorph == 'quartz'
    assert {t.role for t in row.terms} == {'MgO', 'SiO2', 'Mg2SiO4'}
    sio2 = next(t for t in row.terms if t.role == 'SiO2')
    # 1000 K is above O-037's printed I->II at 847 K, so the row is beta.
    assert sio2.janaf_polymorph == 'beta'
    assert any('sub-form beta' in note for note in row.notes)
    # MELTS QUARTZ_ADJUSTMENT variant: nu_Qz = -1, ADJ = -1.291 kJ, so the
    # no-adjustment sum is drG - nu*ADJ = -56.8743023 - 1.291 = -58.1653023.
    assert row.drG_engine_no_quartz_adjustment_kJ_mol == pytest.approx(
        -58.1653023, abs=1e-6
    )
    assert row.residual_no_quartz_adjustment_kJ_mol == pytest.approx(
        4.2726977, abs=1e-6
    )
    assert any('QUARTZ_ADJUSTMENT' in note for note in row.notes)


def test_build_reaction_row_enstatite_1000K_refused_not_scored():
    """Mg-012 at 1000 K is printed form ii, not clinoenstatite."""
    fixtures = {
        phase: _props('thermoengine', phase, TE_POLY[phase], G_J)
        for phase, G_J in TE_G_1000_J.items()
    }
    with pytest.raises(PolymorphMismatchError, match='clinoenstatite'):
        build_reaction_row(
            REACTION_ENSTATITE,
            'thermoengine',
            1000.0,
            engine_query=lambda phase_id, T: fixtures[phase_id],
            janaf_query=_janaf_stub,
        )


def test_build_reaction_row_magemin_apparent_sum_1000K():
    """MAGEMin gbase sum (kJ -> J conversion inside fixtures) = -58.125."""
    fixtures = {
        phase: _props('magemin', phase, MM_POLY[phase], G_kJ * 1000.0)
        for phase, G_kJ in MM_G_1000_KJ.items()
    }
    row = build_reaction_row(
        REACTION_FORSTERITE,
        'magemin',
        1000.0,
        engine_query=lambda phase_id, T: fixtures[phase_id],
        janaf_query=_janaf_stub,
    )
    # -2340.23243 + 2*650.29822 + 981.511198 = -58.124792 kJ
    assert row.drG_engine_kJ_mol == pytest.approx(-58.124792, abs=1e-6)
    assert row.residual_kJ_mol == pytest.approx(4.313208, abs=1e-6)
    # The MELTS quartz adjustment is ThermoEngine-only.
    assert row.drG_engine_no_quartz_adjustment_kJ_mol is None
    assert row.residual_no_quartz_adjustment_kJ_mol is None


# --- polymorph contract ------------------------------------------------------


def test_require_polymorph_match():
    quartz_1000 = require_polymorph_match(
        'quartz', JANAF_TABLES['O-037'], temperature_K=1000.0, context='t'
    )
    assert quartz_1000.polymorph == 'beta'
    quartz_500 = require_polymorph_match(
        'quartz', JANAF_TABLES['O-037'], temperature_K=500.0, context='t'
    )
    assert quartz_500.polymorph == 'alpha'
    with pytest.raises(PolymorphMismatchError):
        require_polymorph_match(
            'orthoenstatite', JANAF_TABLES['Mg-012'],
            temperature_K=500.0, context='t',
        )
    with pytest.raises(PolymorphMismatchError):
        require_polymorph_match(
            'cristobalite', JANAF_TABLES['O-037'],
            temperature_K=1000.0, context='t',
        )
    with pytest.raises(PolymorphMismatchError):
        require_polymorph_match(
            None, JANAF_TABLES['O-037'], temperature_K=1000.0, context='t'
        )
    high = require_polymorph_match(
        'cristobalite_high', JANAF_TABLES['O-035'],
        temperature_K=1000.0, context='t',
    )
    assert high.polymorph == 'cristobalite_high'
    low = require_polymorph_match(
        'cristobalite_low', JANAF_TABLES['O-036'],
        temperature_K=1000.0, context='t',
    )
    assert low.polymorph == 'cristobalite_low'
    with pytest.raises(PolymorphMismatchError):
        require_polymorph_match(
            'cristobalite', JANAF_TABLES['O-035'],
            temperature_K=1000.0, context='t',
        )
    with pytest.raises(PolymorphMismatchError):
        require_polymorph_match(
            'cristobalite_low', JANAF_TABLES['O-035'],
            temperature_K=1000.0, context='t',
        )


def test_engine_phase_polymorph_reads_engine_maps():
    assert engine_phase_polymorph('magemin', 'en') == 'orthoenstatite'
    assert engine_phase_polymorph('magemin', 'q') == 'quartz'
    assert engine_phase_polymorph('thermoengine', 'cEn') == 'clinoenstatite'
    assert engine_phase_polymorph('thermoengine', 'En') == 'orthoenstatite'
    assert engine_phase_polymorph('thermoengine', 'Nope') is None


def test_preflight_enstatite_reaction_refused_for_both_engines():
    """Neither ortho nor clino is a printed Mg-012 band."""
    for engine, polymorph in (
        ('magemin', 'orthoenstatite'),
        ('thermoengine', 'clinoenstatite'),
    ):
        refusal = preflight_reaction(
            REACTION_ENSTATITE, engine, temperature_K=298.15
        )
        assert refusal is not None
        assert refusal.reason == POLYMORPH_MISMATCH
        assert refusal.engine_polymorph == polymorph
        assert refusal.janaf_polymorph == 'i'
        assert refusal.phase_id in ('en', 'cEn')
        hot = preflight_reaction(
            REACTION_ENSTATITE, engine, temperature_K=1000.0
        )
        assert hot is not None and hot.janaf_polymorph == 'ii'
    assert preflight_reaction(
        REACTION_FORSTERITE, 'magemin', temperature_K=1000.0
    ) is None
    assert preflight_reaction(
        REACTION_FORSTERITE, 'thermoengine', temperature_K=1000.0
    ) is None


def test_preflight_phase_requests_refusals():
    by_phase = {r.phase_id: r for r in DEFAULT_PHASE_REQUESTS}
    en_te = preflight_phase_request(by_phase['En'], temperature_K=1000.0)
    assert en_te is not None and en_te.reason == POLYMORPH_MISMATCH
    en_mm = preflight_phase_request(by_phase['en'], temperature_K=1000.0)
    assert en_mm is not None and en_mm.reason == POLYMORPH_MISMATCH
    cen = preflight_phase_request(by_phase['cEn'], temperature_K=500.0)
    assert cen is not None and cen.reason == POLYMORPH_MISMATCH
    assert cen.janaf_polymorph == 'i'
    trd = preflight_phase_request(by_phase['trd'], temperature_K=1500.0)
    assert trd is not None
    assert trd.reason == NO_JANAF_TABLE_FOR_POLYMORPH
    assert trd.engine_polymorph == 'tridymite'
    assert preflight_phase_request(by_phase['q'], temperature_K=1000.0) is None


def test_build_reaction_row_live_polymorph_recheck_refuses():
    """A drifted engine map (live polymorph != static) refuses even when the
    static preflight passed: stub returns orthoenstatite for the SiO2 role."""
    fixtures = {
        'Per': _props('thermoengine', 'Per', 'periclase', TE_G_1000_J['Per']),
        'Qz': _props('thermoengine', 'Qz', 'orthoenstatite', TE_G_1000_J['Qz']),
        'Fo': _props('thermoengine', 'Fo', 'forsterite', TE_G_1000_J['Fo']),
    }
    with pytest.raises(PolymorphMismatchError):
        build_reaction_row(
            REACTION_FORSTERITE,
            'thermoengine',
            1000.0,
            engine_query=lambda phase_id, T: fixtures[phase_id],
            janaf_query=_janaf_stub,
        )


# --- phase-property arithmetic ----------------------------------------------


def _te_fo_query(phase_id, T):
    assert phase_id == 'Fo'
    if T == 298.15:
        return _props(
            'thermoengine', 'Fo', 'forsterite', -2202449.0815,
            temperature_K=298.15, S_J_K_mol=94.01, Cp_J_K_mol=118.3511,
            H_J_mol=-2174420.0,
        )
    return _props(
        'thermoengine', 'Fo', 'forsterite', TE_G_1000_J['Fo'],
        temperature_K=T, S_J_K_mol=276.1463, Cp_J_K_mol=175.2371,
        H_J_mol=-2064986.56,
    )


def test_build_phase_row_hand_computed_residuals_1000K():
    request = PhaseScoreRequest('thermoengine', 'Fo', 'Mg-028')
    row = build_phase_row(
        request, 1000.0, engine_query=_te_fo_query, janaf_query=_janaf_stub
    )
    props = {p.property: p for p in row.properties}
    # S: 276.1463 vs 277.418 -> -1.2717 (-0.4585 %)
    assert props['S'].residual == pytest.approx(-1.2717, abs=1e-4)
    assert props['S'].residual_pct == pytest.approx(-0.4585, abs=1e-3)
    # Cp: 175.2371 vs 174.607 -> +0.6301 (+0.3609 %)
    assert props['Cp'].residual == pytest.approx(0.6301, abs=1e-4)
    # H(1000)-H(298.15): (-2064986.56 + 2174420.0)/1000 = 109.43344 vs 109.392
    assert props['H_increment'].engine == pytest.approx(109.43344, abs=1e-6)
    assert props['H_increment'].residual == pytest.approx(0.04144, abs=1e-5)
    assert row.polymorph == 'forsterite' == row.janaf_polymorph


def test_enthalpy_increment_kJ_mol():
    assert enthalpy_increment_kJ_mol(-2064986.56, -2174420.0) == pytest.approx(
        109.43344, abs=1e-9
    )


def test_build_phase_row_typed_absence_never_zero():
    """MAGEMin-style absent S/Cp/H: residual None + typed reason, no zero."""

    def mm_query(phase_id, T):
        return _props(
            'magemin', 'q', 'quartz', -1047260.004 * 1000.0,
            temperature_K=T,
            absences=tuple(
                PropertyAbsence(p, PHASE_NOT_STABLE_AT_TP)
                for p in ('S', 'Cp', 'H')
            ),
        )

    request = PhaseScoreRequest('magemin', 'q', 'O-037')
    row = build_phase_row(
        request, 1500.0, engine_query=mm_query, janaf_query=_janaf_stub
    )
    for prop in row.properties:
        assert prop.engine is None
        assert prop.residual is None
        assert prop.absence_reason == PHASE_NOT_STABLE_AT_TP


def test_build_phase_row_magemin_passes_accessor_warnings_not_impurity():
    """S/Cp/H are derivatives of pure-endmember G.  Do not label them impure."""

    derived = 'S/Cp/H are central differences of the pure-endmember gbase'

    def mm_query(phase_id, T):
        assert phase_id == 'per'
        return _props(
            'magemin', 'per', 'periclase', -650.29822 * 1000.0,
            host_phase='fper',
            temperature_K=T,
            S_J_K_mol=81.495, Cp_J_K_mol=50.85,
            H_J_mol=-568900.0 if T != 298.15 else -601700.0,
            warnings=(derived,),
        )

    request = PhaseScoreRequest('magemin', 'per', 'Mg-008')
    row = build_phase_row(
        request, 1000.0, engine_query=mm_query, janaf_query=_janaf_stub
    )
    assert any(derived in note for note in row.notes)
    assert not any('slightly impure' in note for note in row.notes)


def test_default_temperatures_cover_brief_grid():
    assert DEFAULT_TEMPERATURES_K == (298.15, 500.0, 1000.0, 1500.0)


# --- JANAF table access against the real compilation -------------------------


def test_janaf_values_at_real_table_mgo_1000K():
    document = load_janaf_table('Mg-008')
    values = janaf_values_at(document, 1000.0)
    assert values is not None
    assert values.Cp_J_K_mol == pytest.approx(51.208)
    assert values.S_J_K_mol == pytest.approx(82.262)
    assert values.enthalpy_increment_kJ_mol == pytest.approx(33.001)
    assert values.formation_gibbs_kJ_mol == pytest.approx(-492.952)


def test_janaf_values_at_missing_temperature_returns_none():
    document = load_janaf_table('Mg-008')
    assert janaf_values_at(document, 999.0) is None


def _printed_solid_solid_temperatures(table_id: str) -> list[float]:
    document = load_janaf_table(table_id)
    found = []
    for item in document['table'].get('parse_ambiguities') or ():
        raw = str(item.get('raw_line') or '')
        fields = raw.split('\t')
        label = fields[-1].strip() if fields else ''
        if '<-->' not in label or 'LIQUID' in label.upper():
            continue
        found.append(float(fields[0].split()[0]))
    return found


def test_mg012_printed_bands_refuse_outside_the_matching_polymorph():
    """Bands come from Mg-012's own I->II and II->III markers.

    Form i is scored only inside its band.  clinoenstatite is not a
    printed band at any temperature, including below the first marker.
    """

    markers = _printed_solid_solid_temperatures('Mg-012')
    assert markers == [903.0, 1258.0]
    info = bands_for_table_id('Mg-012')
    assert info.title_polymorph is None
    assert [band.polymorph for band in info.bands] == ['i', 'ii', 'iii']
    assert info.bands[0].t_max_K == markers[0]
    assert info.bands[1].t_min_K == markers[0]
    assert info.bands[1].t_max_K == markers[1]
    assert info.bands[2].t_min_K == markers[1]
    assert all(band.polymorph != 'clinoenstatite' for band in info.bands)
    low = require_polymorph_match(
        'i', JANAF_TABLES['Mg-012'], temperature_K=500.0, context='t'
    )
    assert low.polymorph == 'i'
    with pytest.raises(PolymorphMismatchError):
        require_polymorph_match(
            'i', JANAF_TABLES['Mg-012'], temperature_K=1000.0, context='t'
        )
    with pytest.raises(PolymorphMismatchError):
        require_polymorph_match(
            'clinoenstatite', JANAF_TABLES['Mg-012'],
            temperature_K=298.15, context='t',
        )
    with pytest.raises(PolymorphMismatchError):
        require_polymorph_match(
            'clinoenstatite', JANAF_TABLES['Mg-012'],
            temperature_K=1500.0, context='t',
        )


def test_absence_token_beats_a_numeric_engine_value():
    """A 0.0 plus PropertyAbsence must not become a scored residual."""

    def mm_query(phase_id, T):
        return _props(
            'magemin', 'q', 'quartz', -1.0e6,
            temperature_K=T,
            S_J_K_mol=0.0,
            Cp_J_K_mol=0.0,
            H_J_mol=0.0,
            absences=tuple(
                PropertyAbsence(p, PHASE_NOT_STABLE_AT_TP)
                for p in ('S', 'Cp', 'H')
            ),
        )

    row = build_phase_row(
        PhaseScoreRequest('magemin', 'q', 'O-037'),
        1500.0,
        engine_query=mm_query,
        janaf_query=_janaf_stub,
    )
    for prop in row.properties:
        assert prop.engine is None
        assert prop.residual is None
        assert prop.absence_reason == PHASE_NOT_STABLE_AT_TP
    assert any('absence wins' in note for note in row.notes)
