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
    PurePhaseUnknownSymbolError,
)
from simulator.melt_backend.pure_phase_janaf_score import (
    DEFAULT_PHASE_REQUESTS,
    DEFAULT_TEMPERATURES_K,
    ENGINE_PHASE_ACCESS,
    JANAF_TABLES,
    NO_JANAF_TABLE_FOR_POLYMORPH,
    NO_JUSTIFIED_ENGINE_ENDMEMBER,
    POLYMORPH_MISMATCH,
    PolymorphMismatchError,
    REACTION_ANDALUSITE,
    REACTION_CATALOGUE,
    REACTION_ENSTATITE,
    REACTION_FORSTERITE,
    REACTION_KYANITE,
    REACTION_SILLIMANITE,
    REACTION_SPINEL,
    REACTIONS,
    ELEMENT_REFERENCE_TABLES,
    JanafValues,
    PhaseScoreRequest,
    bands_for_table_id,
    build_phase_row,
    build_reaction_row,
    element_reference_g_kJ_mol,
    engine_phase_polymorph,
    enthalpy_increment_kJ_mol,
    formation_g_from_apparent_kJ_mol,
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
    'Al-096': -1361.437,  # Al2O3 alpha / corundum
    'Al-089': -1886.914,  # MgAl2O4
    'Al-102': -2097.155,  # andalusite
    'Al-103': -2090.0,    # kyanite
    'Al-104': -2096.351,  # sillimanite
}
# 298.15 K:
JANAF_DFG_298 = {
    'Mg-008': -568.945,
    'O-037': -856.443,
    'Mg-012': -1462.023,
    'Mg-028': -2057.879,
    'Al-096': -1582.275,
    'Al-089': -2176.621,
    'Al-102': -2444.482,
    'Al-103': -2443.937,
    'Al-104': -2442.394,
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


# --- W2 reaction catalogue (ported onto the printed-band rule) --------------


def test_reaction_catalogue_is_declarative_and_covers_targets():
    assert REACTIONS is REACTION_CATALOGUE
    assert [r.reaction_id for r in REACTION_CATALOGUE] == [
        '2MgO+SiO2->Mg2SiO4',
        'MgO+SiO2->MgSiO3',
        'MgO+Al2O3->MgAl2O4',
        'Al2O3+SiO2->Al2SiO5(andalusite)',
        'Al2O3+SiO2->Al2SiO5(kyanite)',
        'Al2O3+SiO2->Al2SiO5(sillimanite)',
    ]
    for reaction in REACTION_CATALOGUE:
        assert set(reaction.janaf_table_by_role) == {role for role, _ in reaction.terms}
        assert set(reaction.engine_phase_by_role) == {'magemin', 'thermoengine'}
        for table_id in reaction.janaf_table_by_role.values():
            assert table_id in JANAF_TABLES


def test_catalogue_omits_ca_silicates_and_stoichiometric_feo():
    """No CaSiO3 / Ca2SiO4 table, and Fe-001 is Fe0.947O not FeO."""
    blob = ' '.join(r.reaction_id for r in REACTION_CATALOGUE)
    assert 'CaSiO3' not in blob and 'Ca2SiO4' not in blob and 'FeO' not in blob
    assert JANAF_TABLES['Ca-027'].fallback_polymorph == 'lime'
    assert 'Fe-030' not in JANAF_TABLES
    assert all(
        'Ca-027' not in r.janaf_table_by_role.values()
        for r in REACTION_CATALOGUE
    )


def test_corundum_matches_printed_alpha_band_below_the_liquid():
    """ALPHA <--> LIQUID is corundum's alpha band, not a refuse-at-all-T."""
    info = bands_for_table_id('Al-096')
    assert info.title_polymorph == 'corundum'
    assert [band.polymorph for band in info.bands] == ['alpha']
    assert info.bands[0].t_max_K == 2327.0
    for temperature in DEFAULT_TEMPERATURES_K:
        band = require_polymorph_match(
            'corundum', JANAF_TABLES['Al-096'],
            temperature_K=temperature, context='t',
        )
        assert band.polymorph == 'alpha'
    with pytest.raises(PolymorphMismatchError):
        require_polymorph_match(
            'corundum', JANAF_TABLES['Al-096'],
            temperature_K=2327.0, context='t',
        )


def test_spinel_and_lime_fallbacks_close_at_the_printed_liquid():
    spinel = bands_for_table_id('Al-089')
    assert spinel.title_polymorph is None
    assert spinel.bands[0].polymorph == 'spinel'
    assert spinel.bands[0].t_max_K == 2408.0
    lime = bands_for_table_id('Ca-027')
    assert lime.bands[0].polymorph == 'lime'
    assert lime.bands[0].t_max_K == 3200.0
    matched = require_polymorph_match(
        'spinel', JANAF_TABLES['Al-089'], temperature_K=1000.0, context='t'
    )
    assert matched.polymorph == 'spinel'
    with pytest.raises(PolymorphMismatchError):
        require_polymorph_match(
            'spinel', JANAF_TABLES['Al-089'], temperature_K=2408.0, context='t'
        )


def test_reaction_sum_janaf_spinel_1000K_hand_computed():
    # -1886.914 - (-492.952) - (-1361.437) = -32.525
    assert reaction_sum([
        (1.0, JANAF_DFG_1000['Al-089']),
        (-1.0, JANAF_DFG_1000['Mg-008']),
        (-1.0, JANAF_DFG_1000['Al-096']),
    ]) == pytest.approx(-32.525, abs=1e-9)


def test_reaction_sum_janaf_andalusite_1000K_hand_computed():
    # -2097.155 - (-1361.437) - (-730.256) = -5.462
    assert reaction_sum([
        (1.0, JANAF_DFG_1000['Al-102']),
        (-1.0, JANAF_DFG_1000['Al-096']),
        (-1.0, JANAF_DFG_1000['O-037']),
    ]) == pytest.approx(-5.462, abs=1e-9)


def test_build_reaction_row_spinel_janaf_side_1000K():
    fixtures = {
        'Per': _props('thermoengine', 'Per', 'periclase', -650763.6656),
        'Crn': _props('thermoengine', 'Crn', 'corundum', -1670000.0),
        'Spl': _props('thermoengine', 'Spl', 'spinel', -2350000.0),
    }
    row = build_reaction_row(
        REACTION_SPINEL,
        'thermoengine',
        1000.0,
        engine_query=lambda phase_id, T: fixtures[phase_id],
        janaf_query=_janaf_stub,
    )
    assert row.drG_janaf_kJ_mol == pytest.approx(-32.525, abs=1e-9)
    assert row.sio2_polymorph is None
    assert row.drG_engine_no_quartz_adjustment_kJ_mol is None
    assert not any('quartz is metastable' in note for note in row.notes)


def test_preflight_magemin_spinel_refused_typed():
    """nsp is ordered normal spinel; it is not scored as JANAF Al-089."""
    for temperature in DEFAULT_TEMPERATURES_K:
        refusal = preflight_reaction(
            REACTION_SPINEL, 'magemin', temperature_K=temperature
        )
        assert refusal is not None
        assert refusal.reason == NO_JUSTIFIED_ENGINE_ENDMEMBER
        assert 'nsp' in refusal.detail
        assert "host 'spl'" in refusal.detail
    assert preflight_reaction(
        REACTION_SPINEL, 'thermoengine', temperature_K=1000.0
    ) is None
    assert 'sp' not in REACTION_SPINEL.engine_phase_by_role['magemin'].values()
    assert REACTION_SPINEL.engine_phase_by_role['thermoengine']['Al2O3'] == 'Crn'
    assert REACTION_SPINEL.engine_phase_by_role['thermoengine']['MgAl2O4'] == 'Spl'


def test_preflight_al2sio5_polymorph_mismatch_refused():
    assert preflight_reaction(
        REACTION_ANDALUSITE, 'thermoengine', temperature_K=1000.0
    ) is None
    assert preflight_reaction(
        REACTION_KYANITE, 'magemin', temperature_K=1000.0
    ) is None
    assert preflight_reaction(
        REACTION_SILLIMANITE, 'thermoengine', temperature_K=1500.0
    ) is None
    by_key = {
        (r.engine, r.phase_id, r.janaf_table_id): r
        for r in DEFAULT_PHASE_REQUESTS
    }
    refuse = preflight_phase_request(
        by_key[('thermoengine', 'And', 'Al-104')], temperature_K=1000.0
    )
    assert refuse is not None and refuse.reason == POLYMORPH_MISMATCH
    assert refuse.engine_polymorph == 'andalusite'
    assert refuse.janaf_polymorph == 'sillimanite'
    mm = preflight_phase_request(
        by_key[('magemin', 'and', 'Al-104')], temperature_K=1000.0
    )
    assert mm is not None and mm.reason == POLYMORPH_MISMATCH


def test_al2sio5_metastability_flagged_like_quartz():
    """Kyanite above ~493 K, and every Al2SiO5 row above the mullite bound."""
    kyanite_298 = build_reaction_row(
        REACTION_KYANITE, 'thermoengine', 298.15,
        engine_query=lambda phase_id, T: _props(
            'thermoengine', phase_id,
            {'Crn': 'corundum', 'Qz': 'quartz', 'Ky': 'kyanite'}[phase_id],
            -1.0e6, temperature_K=T,
        ),
        janaf_query=_janaf_stub,
    )
    assert not any('metastable' in note for note in kyanite_298.notes)
    kyanite_500 = build_reaction_row(
        REACTION_KYANITE, 'thermoengine', 500.0,
        engine_query=lambda phase_id, T: _props(
            'thermoengine', phase_id,
            {'Crn': 'corundum', 'Qz': 'quartz', 'Ky': 'kyanite'}[phase_id],
            -1.0e6, temperature_K=T,
        ),
        janaf_query=_janaf_stub,
    )
    assert any('kyanite is metastable' in note for note in kyanite_500.notes)
    andalusite_1000 = build_reaction_row(
        REACTION_ANDALUSITE, 'thermoengine', 1000.0,
        engine_query=lambda phase_id, T: _props(
            'thermoengine', phase_id,
            {'Crn': 'corundum', 'Qz': 'quartz', 'And': 'andalusite'}[phase_id],
            -1.0e6, temperature_K=T,
        ),
        janaf_query=_janaf_stub,
    )
    assert not any('metastable' in note for note in andalusite_1000.notes)
    andalusite_1500 = build_reaction_row(
        REACTION_ANDALUSITE, 'thermoengine', 1500.0,
        engine_query=lambda phase_id, T: _props(
            'thermoengine', phase_id,
            {'Crn': 'corundum', 'Qz': 'quartz', 'And': 'andalusite'}[phase_id],
            -1.0e6, temperature_K=T,
        ),
        janaf_query=_janaf_stub,
    )
    assert any('andalusite is metastable' in note for note in andalusite_1500.notes)
    assert any('mullite' in note for note in andalusite_1500.notes)
    # Quartz flag on the forsterite row is unchanged in wording.
    forsterite_1500 = build_reaction_row(
        REACTION_FORSTERITE, 'thermoengine', 1500.0,
        engine_query=lambda phase_id, T: _props(
            'thermoengine', phase_id,
            {'Per': 'periclase', 'Qz': 'quartz', 'Fo': 'forsterite'}[phase_id],
            TE_G_1000_J.get(phase_id, -1.0e6), temperature_K=T,
        ),
        janaf_query=_janaf_stub,
    )
    assert any(
        note.startswith('quartz is metastable above the q->trd transition')
        for note in forsterite_1500.notes
    )
    assert not any('Al2SiO5' in note or 'kyanite' in note for note in forsterite_1500.notes)
    sillimanite_1000 = build_reaction_row(
        REACTION_SILLIMANITE, 'thermoengine', 1000.0,
        engine_query=lambda phase_id, T: _props(
            'thermoengine', phase_id,
            {'Crn': 'corundum', 'Qz': 'quartz', 'Sil': 'sillimanite'}[phase_id],
            -1.0e6, temperature_K=T,
        ),
        janaf_query=_janaf_stub,
    )
    assert any('sillimanite is metastable' in note for note in sillimanite_1000.notes)
    assert not any('mullite' in note for note in sillimanite_1000.notes)


def test_janaf_values_at_real_tables_spinel_corundum_andalusite_1000K():
    spinel = janaf_values_at(load_janaf_table('Al-089'), 1000.0)
    corundum = janaf_values_at(load_janaf_table('Al-096'), 1000.0)
    andalusite = janaf_values_at(load_janaf_table('Al-102'), 1000.0)
    assert spinel is not None and spinel.formation_gibbs_kJ_mol == pytest.approx(-1886.914)
    assert corundum is not None and corundum.formation_gibbs_kJ_mol == pytest.approx(-1361.437)
    assert andalusite is not None and andalusite.formation_gibbs_kJ_mol == pytest.approx(-2097.155)


def test_build_all_rows_one_bad_phase_does_not_abort():
    """An unknown symbol on one row is a typed refusal; later rows still score."""
    import scripts.janaf_pure_phase_score as runner

    class _Querier:
        def __call__(self, engine, phase_id, temperature_K):
            if phase_id in ('Crn', 'cor', 'Spl', 'And', 'Ky', 'Sil', 'and', 'ky', 'sill'):
                raise PurePhaseUnknownSymbolError(f'no {phase_id}')
            polymorph = {
                'Per': 'periclase', 'Qz': 'quartz', 'Fo': 'forsterite',
                'per': 'periclase', 'q': 'quartz', 'fo': 'forsterite',
                'cEn': 'clinoenstatite', 'En': 'orthoenstatite',
                'en': 'orthoenstatite',
            }[phase_id]
            return _props(engine, phase_id, polymorph, -1.0e6, temperature_K=temperature_K)

    rows, refusals = runner.build_all_rows(
        _Querier(), runner.JanafQuerier(), ('magemin', 'thermoengine')
    )
    assert any(
        getattr(row, 'reaction_id', None) == '2MgO+SiO2->Mg2SiO4'
        for row in rows
    )
    access = [
        refusal for refusal in refusals
        if refusal.reason == ENGINE_PHASE_ACCESS
    ]
    assert access
    assert any(refusal.reaction_id == 'MgO+Al2O3->MgAl2O4' for refusal in access)
    assert any(
        refusal.reason == NO_JUSTIFIED_ENGINE_ENDMEMBER
        for refusal in refusals
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
    reason='ThermoEngine dylibs/package unavailable on this machine',
)


def _magemin_binary_available() -> bool:
    try:
        from simulator.engine_local_config import configured_magemin_binary_path
        from simulator.melt_backend.magemin import MAGEMinBackend

        if configured_magemin_binary_path() is not None:
            return True
        return MAGEMinBackend._locate_binary(None) is not None
    except Exception:
        return False


needs_magemin = pytest.mark.skipif(
    not _magemin_binary_available(),
    reason='no MAGEMin binary',
)


@needs_thermoengine
def test_catalogue_thermoengine_symbols_resolve_on_the_engine():
    """Phase ids are checked with Database.get_phase, not against the map."""
    from simulator.engine_local_config import setup_thermoengine_dylib_path

    setup_thermoengine_dylib_path()
    from thermoengine import model

    from engines.alphamelts.thermoengine import (
        _TE_PURE_PHASE_POLYMORPH,
        _thermoengine_phase_formula,
    )

    database = model.Database(database='Berman')
    catalogue_symbols = {
        phase_id
        for reaction in REACTION_CATALOGUE
        for phase_id in reaction.engine_phase_by_role['thermoengine'].values()
        if phase_id
    }
    # Lm is the lime fallback's Berman symbol; it is not a reaction term.
    catalogue_symbols.add('Lm')
    expected = {
        'Crn': ('Al2O3', 'Corundum'),
        'Spl': ('MgAl2O4', 'Spinel'),
        'And': ('Al2SiO5', 'Andalusite'),
        'Ky': ('Al2SiO5', 'Kyanite'),
        'Sil': ('Al2SiO5', 'Sillimanite'),
        'Lm': ('CaO', 'Lime'),
    }
    for symbol in sorted(catalogue_symbols):
        phase = database.get_phase(symbol)
        formula = _thermoengine_phase_formula(phase, symbol)
        name = getattr(phase, 'phase_name', '')
        if callable(name):
            name = name()
        if symbol in expected:
            want_formula, want_name = expected[symbol]
            assert formula == want_formula
            assert want_name in str(name)
        assert symbol in _TE_PURE_PHASE_POLYMORPH
    for bad in ('Co', 'Sp', 'a'):
        with pytest.raises(Exception):
            database.get_phase(bad)


@needs_magemin
def test_catalogue_magemin_endmembers_resolve_in_the_verb1_table():
    """Endmembers are read from a live Verb=1 table, not from the spec text."""
    from simulator.melt_backend.magemin import (
        MAGEMinBackend,
        _MAGEMIN_PURE_PHASES,
        _parse_magemin_gbase_tables,
    )

    backend = MAGEMinBackend()
    assert backend.initialize({'warm_worker': False})
    spec = _MAGEMIN_PURE_PHASES['q']
    stdout, _matlab, _notes = backend._run_pure_phase_probe(
        bulk_wt_ig=dict(spec.bulk_wt_pct),
        temperature_C=1000.0 - 273.15,
        pressure_kbar=0.001,
    )
    pure_phases, solutions = _parse_magemin_gbase_tables(stdout)
    assert 'spl' in solutions
    assert 'nsp' in solutions['spl']
    assert 'sp' not in solutions['spl']
    assert 'spn' not in solutions
    mapped_spinel = REACTION_SPINEL.engine_phase_by_role['magemin'].get('MgAl2O4')
    assert mapped_spinel not in solutions['spl']
    assert mapped_spinel not in pure_phases
    for reaction in REACTION_CATALOGUE:
        for phase_id in reaction.engine_phase_by_role['magemin'].values():
            if not phase_id:
                continue
            phase = _MAGEMIN_PURE_PHASES[phase_id]
            if phase.host_phase is None:
                assert phase.endmember in pure_phases
            else:
                assert phase.endmember in solutions[phase.host_phase]
    for endmember in ('cor', 'and', 'ky', 'sill'):
        assert endmember in pure_phases


# --- apparent G -> Delta_fG (element reference at T) -----------------------

# Element counts in the JANAF formation reaction. Oxygen is O2, not O.
_ELEMENT_COUNTS = {
    'MgO': {'Mg': 1.0, 'O2': 0.5},
    'SiO2': {'Si': 1.0, 'O2': 1.0},
    'Al2O3': {'Al': 2.0, 'O2': 1.5},
    'Mg2SiO4': {'Mg': 2.0, 'Si': 1.0, 'O2': 2.0},
    'MgAl2O4': {'Mg': 1.0, 'Al': 2.0, 'O2': 2.0},
    'Al2SiO5': {'Al': 2.0, 'Si': 1.0, 'O2': 2.5},
}


def _element_reference_g(temperature_K: float) -> dict:
    """g_ref from the JANAF ref tables at one printed temperature."""

    references = {}
    for element, table_id in ELEMENT_REFERENCE_TABLES.items():
        values = janaf_values_at(load_janaf_table(table_id), temperature_K)
        assert values is not None
        assert values.enthalpy_increment_kJ_mol is not None
        assert values.S_J_K_mol is not None
        references[element] = element_reference_g_kJ_mol(
            values.enthalpy_increment_kJ_mol,
            values.S_J_K_mol,
            temperature_K,
        )
    return references


def _apparent_g_from_janaf_phase(table_id: str, temperature_K: float) -> float:
    """G_a(T) = dfH(298.15) + [H(T)-H(298.15)] - T*S(T), from one phase table."""

    phase = janaf_values_at(load_janaf_table(table_id), temperature_K)
    reference = janaf_values_at(load_janaf_table(table_id), 298.15)
    assert phase is not None and reference is not None
    return (
        reference.formation_enthalpy_kJ_mol
        + phase.enthalpy_increment_kJ_mol
        - temperature_K * phase.S_J_K_mol / 1000.0
    )


def test_formation_g_at_298_reduces_to_the_enthalpy_entropy_identity():
    """dfG(298.15) = dfH298 - 298.15*(S_phase - sum n_el S_el)."""

    temperature_K = 298.15
    references = {
        element: janaf_values_at(load_janaf_table(table_id), temperature_K)
        for element, table_id in ELEMENT_REFERENCE_TABLES.items()
    }
    g_ref = _element_reference_g(temperature_K)
    for element in ('Mg', 'Al', 'Si', 'O2'):
        assert references[element].enthalpy_increment_kJ_mol == 0.0
    cases = (
        ('Mg-008', 'MgO'),
        ('Mg-028', 'Mg2SiO4'),
        ('Al-096', 'Al2O3'),
    )
    for table_id, formula in cases:
        phase = janaf_values_at(load_janaf_table(table_id), temperature_K)
        counts = _ELEMENT_COUNTS[formula]
        apparent = (
            phase.formation_enthalpy_kJ_mol
            - temperature_K * phase.S_J_K_mol / 1000.0
        )
        converted = formation_g_from_apparent_kJ_mol(apparent, counts, g_ref)
        element_entropy = sum(
            counts[element] * references[element].S_J_K_mol for element in counts
        )
        identity = phase.formation_enthalpy_kJ_mol - temperature_K * (
            phase.S_J_K_mol - element_entropy
        ) / 1000.0
        assert converted == pytest.approx(identity, abs=1e-9)
        assert converted == pytest.approx(phase.formation_gibbs_kJ_mol, abs=0.001)
    # Counting each oxygen atom as one O2 does not reproduce printed dfG.
    mgo = janaf_values_at(load_janaf_table('Mg-008'), temperature_K)
    wrong = formation_g_from_apparent_kJ_mol(
        mgo.formation_enthalpy_kJ_mol - temperature_K * mgo.S_J_K_mol / 1000.0,
        {'Mg': 1.0, 'O2': 1.0},
        g_ref,
    )
    assert abs(wrong - mgo.formation_gibbs_kJ_mol) > 10.0


def test_ref_tables_reproduce_printed_dfG_across_element_phase_changes():
    """1000 K is liquid Mg and Al; 1500 K is Mg gas. Separate liquid tables are not the reference."""

    cases = (
        ('Mg-008', 'MgO', 1000.0),
        ('Mg-008', 'MgO', 1500.0),
        ('Al-096', 'Al2O3', 1000.0),
        ('Al-096', 'Al2O3', 1500.0),
        ('Mg-028', 'Mg2SiO4', 1500.0),
        ('O-037', 'SiO2', 1500.0),
    )
    for table_id, formula, temperature_K in cases:
        phase = janaf_values_at(load_janaf_table(table_id), temperature_K)
        converted = formation_g_from_apparent_kJ_mol(
            _apparent_g_from_janaf_phase(table_id, temperature_K),
            _ELEMENT_COUNTS[formula],
            _element_reference_g(temperature_K),
        )
        assert converted == pytest.approx(phase.formation_gibbs_kJ_mol, abs=0.003)

    # Mg-003 liquid at 1500 K is not the reference: Mg-001 has already
    # switched to the ideal gas (boiling point 1366.104 K).
    liquid = janaf_values_at(load_janaf_table('Mg-003'), 1500.0)
    wrong_reference = _element_reference_g(1500.0)
    wrong_reference['Mg'] = element_reference_g_kJ_mol(
        liquid.enthalpy_increment_kJ_mol, liquid.S_J_K_mol, 1500.0
    )
    phase = janaf_values_at(load_janaf_table('Mg-008'), 1500.0)
    wrong = formation_g_from_apparent_kJ_mol(
        _apparent_g_from_janaf_phase('Mg-008', 1500.0),
        _ELEMENT_COUNTS['MgO'],
        wrong_reference,
    )
    assert abs(wrong - phase.formation_gibbs_kJ_mol) > 5.0


def test_formation_g_refuses_a_missing_element():
    with pytest.raises(KeyError):
        formation_g_from_apparent_kJ_mol(
            -600.0, {'Mg': 1.0, 'O2': 0.5}, {'Mg': -9.741}
        )


# Verbatim apparent G and printed Delta_fG from the scored reaction rows
# (kJ/mol). Order is product, then the two reactant oxides.
_REACTION_CONSISTENCY_ROWS = (
    # 2 MgO + SiO2 -> Mg2SiO4
    (
        'magemin', 298.15, (1.0, -2.0, -1.0),
        ('Mg2SiO4', 'MgO', 'SiO2'),
        (-2200.83007, -609.48298, -923.046355),
        (-2057.879, -568.945, -856.443),
        4.728244999999788,
    ),
    (
        'magemin', 1500.0, (1.0, -2.0, -1.0),
        ('Mg2SiO4', 'MgO', 'SiO2'),
        (-2498.17017, -696.63306, -1047.25787),
        (-1550.56, -422.752, -643.681),
        3.728819999999928,
    ),
    (
        'thermoengine', 298.15, (1.0, -2.0, -1.0),
        ('Mg2SiO4', 'MgO', 'SiO2'),
        (-2202.4490815, -609.53544065, -924.352460356055),
        (-2057.879, -568.945, -856.443),
        4.520260156054974,
    ),
    (
        'thermoengine', 1000.0, (1.0, -2.0, -1.0),
        ('Mg2SiO4', 'MgO', 'SiO2'),
        (-2341.132872947331, -650.7636656379934, -982.7312394215585),
        (-1778.598, -492.952, -730.256),
        5.563697750214146,
    ),
    (
        'thermoengine', 1500.0, (1.0, -2.0, -1.0),
        ('Mg2SiO4', 'MgO', 'SiO2'),
        (-2498.6244570788194, -697.4864835252936, -1048.4808618282902),
        (-1550.56, -422.752, -643.681),
        6.2043718000578565,
    ),
    # MgO + Al2O3 -> MgAl2O4. 1500 K uses Mg gas and Al liquid.
    (
        'thermoengine', 1500.0, (1.0, -1.0, -1.0),
        ('MgAl2O4', 'MgO', 'Al2O3'),
        (-2612.3056099264713, -697.4864835252936, -1881.631769182536),
        (-1657.458, -422.752, -1196.617),
        4.901642781358305,
    ),
    # Al2O3 + SiO2 -> andalusite. 1000 K uses Al liquid, Si crystal.
    (
        'magemin', 1000.0, (1.0, -1.0, -1.0),
        ('Al2SiO5', 'Al2O3', 'SiO2'),
        (-2760.925567, -1777.252553, -981.509065),
        (-2097.155, -1361.437, -730.256),
        3.298051000000555,
    ),
)


def test_converted_phase_residuals_sum_to_the_reaction_residual():
    """Stoichiometric sum of converted phase residuals equals the reaction residual.

    The unconverted apparent-G minus Delta_fG gap is the element offset
    (about 40 kJ for MgO at 298.15 K, hundreds of kJ once more atoms or
    a higher T are in the term). A converted phase residual on these
    rows is the assessment-scale gap.
    """

    for (
        _engine, temperature_K, nus, roles, engine_g, janaf_dfg, stored_residual
    ) in _REACTION_CONSISTENCY_ROWS:
        g_ref = _element_reference_g(temperature_K)
        phase_residuals = []
        for role, apparent_g, formation_g in zip(roles, engine_g, janaf_dfg):
            converted = formation_g_from_apparent_kJ_mol(
                apparent_g, _ELEMENT_COUNTS[role], g_ref
            )
            phase_residuals.append(converted - formation_g)
            # MgO at 298.15 is the smallest raw gap in this fixture (~40 kJ).
            # A skipped conversion leaves that gap; the converted residual
            # on these rows stays inside 15 kJ.
            assert abs(apparent_g - formation_g) > 30.0
            assert abs(phase_residuals[-1]) < 15.0
        reaction_residual = reaction_sum(zip(nus, engine_g)) - reaction_sum(
            zip(nus, janaf_dfg)
        )
        assert reaction_residual == pytest.approx(stored_residual, abs=1e-9)
        assert reaction_sum(zip(nus, phase_residuals)) == pytest.approx(
            reaction_residual, abs=1e-9
        )
