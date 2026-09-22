"""Tests for the JANAF pure-phase score consumer arithmetic (P4a-2).

No engine is touched: engine sides are fixed PurePhaseProperties fixtures
(verbatim probe values, noted per fixture) and the JANAF side is the
printed table row.  The anchor is a hand-computed reaction sum: e.g. at
1000 K, MgO + SiO2(quartz) -> MgSiO3(clinoenstatite)

    d_rG = dfG(Mg-012) - dfG(Mg-008) - dfG(O-037)
         = -1257.958 - (-492.952) - (-730.256) = -34.750 kJ/mol

and the ThermoEngine apparent-G sum of the same reaction

    d_rG = G(cEn) - G(Per) - G(Qz)
         = (-1663150.6062 + 650763.6656 + 982731.2394) J / 1000
         = -29.656 kJ/mol   (residual +5.094 kJ/mol vs JANAF)

which is exactly the number the live engines must reproduce through the
consumer's reaction-sum path.
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
    JANAF_SOLID_SOLID_TRANSITION,
    JANAF_TABLES,
    MG012_CLINOENSTATITE_MAX_K,
    NO_JANAF_TABLE_FOR_POLYMORPH,
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
    JanafValues,
    PhaseScoreRequest,
    build_phase_row,
    build_reaction_row,
    engine_phase_polymorph,
    enthalpy_increment_kJ_mol,
    janaf_values_at,
    load_janaf_table,
    mg012_clino_temperature_refusal,
    preflight_phase_request,
    preflight_reaction,
    reaction_sum,
    reaction_uses_janaf_table,
    require_polymorph_match,
    sio2_polymorph_for_reaction,
)

# --- printed JANAF rows (data/literature/compilations/janaf/tables) --------
# 1000 K formation_gibbs_energy, kJ/mol, as printed:
JANAF_DFG_1000 = {
    'Mg-008': -492.952,   # MgO(cr)
    'O-037': -730.256,    # SiO2 quartz
    'Mg-012': -1257.958,  # MgSiO3(cr) clinoenstatite
    'Mg-028': -1778.598,  # Mg2SiO4(cr) forsterite
    'Al-096': -1361.437,  # Al2O3(cr, alpha)
    'Al-089': -1886.914,  # MgAl2O4(cr)
    'Al-102': -2097.155,  # Al2SiO5 andalusite
    'Al-103': -2090.0,    # Al2SiO5 kyanite
    'Al-104': -2096.351,  # Al2SiO5 sillimanite
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
    # MELTS QUARTZ_ADJUSTMENT variant: nu_Qz = -1, ADJ = -1.291 kJ, so the
    # no-adjustment sum is drG - nu*ADJ = -56.8743023 - 1.291 = -58.1653023.
    assert row.drG_engine_no_quartz_adjustment_kJ_mol == pytest.approx(
        -58.1653023, abs=1e-6
    )
    assert row.residual_no_quartz_adjustment_kJ_mol == pytest.approx(
        4.2726977, abs=1e-6
    )
    assert any('QUARTZ_ADJUSTMENT' in note for note in row.notes)


def test_build_reaction_row_thermoengine_clinoenstatite_1000K():
    fixtures = {
        phase: _props('thermoengine', phase, TE_POLY[phase], G_J)
        for phase, G_J in TE_G_1000_J.items()
    }
    row = build_reaction_row(
        REACTION_ENSTATITE,
        'thermoengine',
        1000.0,
        engine_query=lambda phase_id, T: fixtures[phase_id],
        janaf_query=_janaf_stub,
    )
    # -1663150.6062 + 650763.6656 + 982731.2394 = -29655.7012 J -> -29.656 kJ
    assert row.drG_engine_kJ_mol == pytest.approx(-29.6557012, abs=1e-6)
    assert row.residual_kJ_mol == pytest.approx(5.0942988, abs=1e-6)


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
    require_polymorph_match('quartz', JANAF_TABLES['O-037'], context='t')
    require_polymorph_match('clinoenstatite', JANAF_TABLES['Mg-012'], context='t')
    with pytest.raises(PolymorphMismatchError):
        require_polymorph_match(
            'orthoenstatite', JANAF_TABLES['Mg-012'], context='t'
        )
    with pytest.raises(PolymorphMismatchError):
        require_polymorph_match('cristobalite', JANAF_TABLES['O-037'], context='t')
    with pytest.raises(PolymorphMismatchError):
        require_polymorph_match(None, JANAF_TABLES['O-037'], context='t')


def test_engine_phase_polymorph_reads_engine_maps():
    assert engine_phase_polymorph('magemin', 'en') == 'orthoenstatite'
    assert engine_phase_polymorph('magemin', 'q') == 'quartz'
    assert engine_phase_polymorph('thermoengine', 'cEn') == 'clinoenstatite'
    assert engine_phase_polymorph('thermoengine', 'En') == 'orthoenstatite'
    assert engine_phase_polymorph('thermoengine', 'Nope') is None


def test_preflight_enstatite_reaction_refused_for_magemin_only():
    """MAGEMin 'en' is orthoenstatite vs JANAF clinoenstatite: refuse typed."""
    refusal = preflight_reaction(REACTION_ENSTATITE, 'magemin')
    assert refusal is not None
    assert refusal.reason == POLYMORPH_MISMATCH
    assert refusal.engine_polymorph == 'orthoenstatite'
    assert refusal.janaf_polymorph == 'clinoenstatite'
    assert refusal.phase_id == 'en'
    # forsterite reaction matches on both engines; enstatite matches for TE.
    assert preflight_reaction(REACTION_FORSTERITE, 'magemin') is None
    assert preflight_reaction(REACTION_FORSTERITE, 'thermoengine') is None
    assert preflight_reaction(REACTION_ENSTATITE, 'thermoengine') is None


def test_preflight_phase_requests_refusals():
    by_key = {(r.engine, r.phase_id, r.janaf_table_id): r for r in DEFAULT_PHASE_REQUESTS}
    en_te = preflight_phase_request(by_key[('thermoengine', 'En', 'Mg-012')])
    assert en_te is not None and en_te.reason == POLYMORPH_MISMATCH
    en_mm = preflight_phase_request(by_key[('magemin', 'en', 'Mg-012')])
    assert en_mm is not None and en_mm.reason == POLYMORPH_MISMATCH
    trd = preflight_phase_request(by_key[('magemin', 'trd', None)])
    assert trd is not None
    assert trd.reason == NO_JANAF_TABLE_FOR_POLYMORPH
    assert trd.engine_polymorph == 'tridymite'
    assert preflight_phase_request(by_key[('thermoengine', 'cEn', 'Mg-012')]) is None
    assert preflight_phase_request(by_key[('magemin', 'q', 'O-037')]) is None


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


def test_build_phase_row_magemin_host_phase_impurity_note():
    """MAGEMin S/Cp/H come from the slightly impure equilibrium SS instance
    while G is the pure endmember (controller review): rows are labelled."""

    def mm_query(phase_id, T):
        assert phase_id == 'per'
        return _props(
            'magemin', 'per', 'periclase', -650.29822 * 1000.0,
            host_phase='fper',
            temperature_K=T,
            S_J_K_mol=81.495, Cp_J_K_mol=50.85,
            H_J_mol=-568900.0 if T != 298.15 else -601700.0,
        )

    request = PhaseScoreRequest('magemin', 'per', 'Mg-008')
    row = build_phase_row(
        request, 1000.0, engine_query=mm_query, janaf_query=_janaf_stub
    )
    assert any('slightly impure' in note for note in row.notes)
    # ThermoEngine pure phases carry no such note.
    assert not any('slightly impure' in note for note in build_phase_row(
        PhaseScoreRequest('thermoengine', 'Fo', 'Mg-028'),
        1000.0, engine_query=_te_fo_query, janaf_query=_janaf_stub,
    ).notes)


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



# --- W2 reaction catalogue ---------------------------------------------------


def test_reaction_catalogue_is_declarative_and_covers_w2_targets():
    """Catalogue is data (ReactionSpec rows), not one function per reaction."""
    assert REACTIONS is REACTION_CATALOGUE
    ids = [r.reaction_id for r in REACTION_CATALOGUE]
    assert ids == [
        '2MgO+SiO2->Mg2SiO4',
        'MgO+SiO2->MgSiO3',
        'MgO+Al2O3->MgAl2O4',
        'Al2O3+SiO2->Al2SiO5(andalusite)',
        'Al2O3+SiO2->Al2SiO5(kyanite)',
        'Al2O3+SiO2->Al2SiO5(sillimanite)',
    ]
    # Every term has a JANAF table and both-engine phase map.
    for reaction in REACTION_CATALOGUE:
        assert set(reaction.janaf_table_by_role) == {role for role, _ in reaction.terms}
        assert set(reaction.engine_phase_by_role) == {'magemin', 'thermoengine'}
        for engine_map in reaction.engine_phase_by_role.values():
            assert set(engine_map) == set(reaction.janaf_table_by_role)
        for table_id in reaction.janaf_table_by_role.values():
            assert table_id in JANAF_TABLES


def test_catalogue_omits_ca_silicates_and_feo_without_janaf_phase():
    """CaSiO3/Ca2SiO4 absent from harvest; FeO is non-stoichiometric Fe0.947O."""
    blob = ' '.join(r.reaction_id for r in REACTION_CATALOGUE)
    assert 'CaSiO3' not in blob and 'Ca2SiO4' not in blob
    assert 'FeO' not in blob
    assert 'Ca-027' in JANAF_TABLES  # lime is tabulated for future use
    assert 'Fe-030' in JANAF_TABLES  # hematite tabulated; no FeO partner


def test_reaction_sum_janaf_spinel_1000K_hand_computed():
    # -1886.914 - (-492.952) - (-1361.437) = -32.525 kJ/mol
    terms = [
        (1.0, JANAF_DFG_1000['Al-089']),
        (-1.0, JANAF_DFG_1000['Mg-008']),
        (-1.0, JANAF_DFG_1000['Al-096']),
    ]
    assert reaction_sum(terms) == pytest.approx(-32.525, abs=1e-9)


def test_reaction_sum_janaf_andalusite_1000K_hand_computed():
    # -2097.155 - (-1361.437) - (-730.256) = -5.462 kJ/mol
    terms = [
        (1.0, JANAF_DFG_1000['Al-102']),
        (-1.0, JANAF_DFG_1000['Al-096']),
        (-1.0, JANAF_DFG_1000['O-037']),
    ]
    assert reaction_sum(terms) == pytest.approx(-5.462, abs=1e-9)


def test_build_reaction_row_spinel_janaf_side_1000K():
    """JANAF side of the spinel catalogue row through the real builder."""
    fixtures = {
        'Per': _props('thermoengine', 'Per', 'periclase', -650763.6656),
        'Co': _props('thermoengine', 'Co', 'corundum', -1670000.0),
        'Sp': _props('thermoengine', 'Sp', 'spinel', -2350000.0),
    }
    row = build_reaction_row(
        REACTION_SPINEL,
        'thermoengine',
        1000.0,
        engine_query=lambda phase_id, T: fixtures[phase_id],
        janaf_query=_janaf_stub,
    )
    assert row.reaction_id == 'MgO+Al2O3->MgAl2O4'
    assert row.drG_janaf_kJ_mol == pytest.approx(-32.525, abs=1e-9)
    assert row.sio2_polymorph == ''
    assert sio2_polymorph_for_reaction(REACTION_SPINEL) == ''
    assert sio2_polymorph_for_reaction(REACTION_ANDALUSITE) == 'quartz'
    # No quartz term -> no MELTS quartz-adjustment variant.
    assert row.drG_engine_no_quartz_adjustment_kJ_mol is None


def test_build_reaction_row_andalusite_janaf_side_1000K():
    fixtures = {
        'Co': _props('thermoengine', 'Co', 'corundum', -1670000.0),
        'Qz': _props('thermoengine', 'Qz', 'quartz', TE_G_1000_J['Qz']),
        'a': _props('thermoengine', 'a', 'andalusite', -2400000.0),
    }
    row = build_reaction_row(
        REACTION_ANDALUSITE,
        'thermoengine',
        1000.0,
        engine_query=lambda phase_id, T: fixtures[phase_id],
        janaf_query=_janaf_stub,
    )
    assert row.drG_janaf_kJ_mol == pytest.approx(-5.462, abs=1e-9)
    assert row.sio2_polymorph == 'quartz'


def test_preflight_catalogue_spinel_matches_both_engines():
    assert preflight_reaction(REACTION_SPINEL, 'magemin') is None
    assert preflight_reaction(REACTION_SPINEL, 'thermoengine') is None
    assert preflight_reaction(REACTION_ANDALUSITE, 'magemin') is None
    assert preflight_reaction(REACTION_ANDALUSITE, 'thermoengine') is None


def test_preflight_al2sio5_polymorph_mismatch_refused():
    """Andalusite engine vs kyanite/sillimanite JANAF product is refused."""
    # Swap: feed andalusite reaction's engine phases against kyanite spec by
    # using the kyanite catalogue row's TE map with a wrong live check is
    # covered elsewhere; here static preflight of ky/sill rows must pass
    # when maps match, and the deliberate DEFAULT_PHASE_REQUEST mismatch
    # (a vs Al-104) must refuse.
    assert preflight_reaction(REACTION_KYANITE, 'thermoengine') is None
    assert preflight_reaction(REACTION_SILLIMANITE, 'magemin') is None
    by_phase = {(r.engine, r.phase_id, r.janaf_table_id): r for r in DEFAULT_PHASE_REQUESTS}
    refuse = preflight_phase_request(by_phase[('thermoengine', 'a', 'Al-104')])
    assert refuse is not None and refuse.reason == POLYMORPH_MISMATCH
    assert refuse.engine_polymorph == 'andalusite'
    assert refuse.janaf_polymorph == 'sillimanite'


def test_engine_phase_polymorph_catalogue_phases():
    assert engine_phase_polymorph('thermoengine', 'Co') == 'corundum'
    assert engine_phase_polymorph('thermoengine', 'Sp') == 'spinel'
    assert engine_phase_polymorph('thermoengine', 'a') == 'andalusite'
    assert engine_phase_polymorph('magemin', 'cor') == 'corundum'
    assert engine_phase_polymorph('magemin', 'sp') == 'spinel'
    assert engine_phase_polymorph('magemin', 'and') == 'andalusite'
    assert engine_phase_polymorph('magemin', 'ky') == 'kyanite'
    assert engine_phase_polymorph('magemin', 'sill') == 'sillimanite'


def test_janaf_values_at_real_table_spinel_and_corundum_1000K():
    spinel = janaf_values_at(load_janaf_table('Al-089'), 1000.0)
    cor = janaf_values_at(load_janaf_table('Al-096'), 1000.0)
    andal = janaf_values_at(load_janaf_table('Al-102'), 1000.0)
    assert spinel is not None and spinel.formation_gibbs_kJ_mol == pytest.approx(-1886.914)
    assert cor is not None and cor.formation_gibbs_kJ_mol == pytest.approx(-1361.437)
    assert andal is not None and andal.formation_gibbs_kJ_mol == pytest.approx(-2097.155)


def test_mg012_clino_refused_at_and_above_I_II_transition():
    """R10: do not score clino-labelled Mg-012 past JANAF I<->II @ 903 K."""
    assert MG012_CLINOENSTATITE_MAX_K == 903.0
    assert mg012_clino_temperature_refusal(temperature_K=500.0) is None
    assert mg012_clino_temperature_refusal(temperature_K=902.999) is None
    hit = mg012_clino_temperature_refusal(
        temperature_K=1000.0, engine='thermoengine', phase_id='cEn'
    )
    assert hit is not None
    assert hit.reason == JANAF_SOLID_SOLID_TRANSITION
    assert hit.janaf_table_id == 'Mg-012'
    hit1500 = mg012_clino_temperature_refusal(temperature_K=1500.0)
    assert hit1500 is not None and 'II<->III' in hit1500.detail
    # Default cEn plan stays inside the clino window.
    cen = next(r for r in DEFAULT_PHASE_REQUESTS if r.phase_id == 'cEn')
    assert max(cen.temperatures_K) < MG012_CLINOENSTATITE_MAX_K
    assert reaction_uses_janaf_table(REACTION_ENSTATITE, 'Mg-012')
    assert not reaction_uses_janaf_table(REACTION_FORSTERITE, 'Mg-012')
    assert not reaction_uses_janaf_table(REACTION_SPINEL, 'Mg-012')
