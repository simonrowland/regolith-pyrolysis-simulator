import math

import pytest

from simulator import transport_regime as tr


DIAMETER_M = 0.010000
LENGTH_M = 0.100000
AREA_M2 = math.pi * DIAMETER_M ** 2 / 4.0
HOT_TEMPERATURE_K = 1873.15
ROOM_TEMPERATURE_K = 293.15
FREE_MOLECULAR_KN = 10.0


@pytest.mark.parametrize(
    (
        "species",
        "molar_mass_kg_mol",
        "pressure_delta_pa",
        "expected_cbar_m_s",
        "expected_conductance_l_s",
        "expected_throughput_pa_m3_s",
    ),
    [
        ("N2", 0.0280134, 1.0, 1189.845, 23.36256, 0.02336256),
        ("Ar", 0.039948, 1.0e-5, 996.3821, 19.56392, 1.956392e-7),
        ("O2", 0.031998, 1.0, 1113.299, 21.85958, 0.02185958),
    ],
)
def test_free_molecular_aperture_fixtures_a1_a2_a3(
    species,
    molar_mass_kg_mol,
    pressure_delta_pa,
    expected_cbar_m_s,
    expected_conductance_l_s,
    expected_throughput_pa_m3_s,
):
    assert (
        tr.mean_molecular_speed_m_s(HOT_TEMPERATURE_K, molar_mass_kg_mol)
        == pytest.approx(expected_cbar_m_s, abs=5e-4)
    )

    conductance_m3_s = tr.molecular_aperture_conductance_m3_s(
        AREA_M2,
        HOT_TEMPERATURE_K,
        molar_mass_kg_mol,
        knudsen_number=FREE_MOLECULAR_KN,
    )

    assert conductance_m3_s * 1000.0 == pytest.approx(
        expected_conductance_l_s,
        rel=1e-6,
    )
    assert tr.throughput_pa_m3_s(
        conductance_m3_s,
        pressure_delta_pa,
    ) == pytest.approx(expected_throughput_pa_m3_s, rel=1e-6)


@pytest.mark.parametrize(
    (
        "species",
        "temperature_K",
        "molar_mass_kg_mol",
        "pressure_delta_pa",
        "expected_conductance_l_s",
        "expected_throughput_pa_m3_s",
        "relative_tolerance",
    ),
    [
        (
            "N2",
            HOT_TEMPERATURE_K,
            0.0280134,
            1.0,
            3.115008,
            0.003115008,
            1e-6,
        ),
        (
            "Ar",
            HOT_TEMPERATURE_K,
            0.039948,
            1.0e-5,
            2.608522,
            2.608522e-8,
            1e-6,
        ),
        (
            "N2",
            ROOM_TEMPERATURE_K,
            0.0280134,
            None,
            1.2323038755117954,
            None,
            1e-9,
        ),
    ],
)
def test_free_molecular_tube_clausing_fixtures_t1_t2_t3(
    species,
    temperature_K,
    molar_mass_kg_mol,
    pressure_delta_pa,
    expected_conductance_l_s,
    expected_throughput_pa_m3_s,
    relative_tolerance,
):
    assert tr.long_tube_clausing_transmission(
        DIAMETER_M,
        LENGTH_M,
    ) == pytest.approx(0.1333333333)

    conductance_m3_s = tr.long_tube_molecular_conductance_m3_s(
        DIAMETER_M,
        LENGTH_M,
        temperature_K,
        molar_mass_kg_mol,
        knudsen_number=FREE_MOLECULAR_KN,
    )

    assert conductance_m3_s * 1000.0 == pytest.approx(
        expected_conductance_l_s,
        rel=relative_tolerance,
    )
    if expected_throughput_pa_m3_s is not None:
        assert tr.throughput_pa_m3_s(
            conductance_m3_s,
            pressure_delta_pa,
        ) == pytest.approx(expected_throughput_pa_m3_s, rel=1e-6)


def test_room_temperature_tube_shortcut_cross_check_t3():
    si_l_s = tr.long_tube_molecular_conductance_m3_s(
        DIAMETER_M,
        LENGTH_M,
        ROOM_TEMPERATURE_K,
        tr.MOLAR_MASSES_KG_PER_MOL["N2"],
        knudsen_number=FREE_MOLECULAR_KN,
    ) * 1000.0
    rounded_vacuum_engineering_shortcut_l_s = 12.1 * 1.0 ** 3 / 10.0

    assert si_l_s == pytest.approx(1.2323038755117954, rel=1e-9)
    assert rounded_vacuum_engineering_shortcut_l_s == pytest.approx(1.21)
    assert abs(si_l_s - rounded_vacuum_engineering_shortcut_l_s) / si_l_s == (
        pytest.approx(0.018099330818490134, rel=1e-9)
    )


@pytest.mark.parametrize(
    (
        "pressure_pa",
        "expected_lambda_m",
        "expected_knudsen",
        "expected_c_p_l_s",
        "expected_f_bk",
        "expected_c_bk_l_s",
        "allow_near_viscous_cross_check",
    ),
    [
        (
            1300.0,
            3.104114e-5,
            0.003104114,
            55.51208,
            1.013757,
            56.27578,
            True,
        ),
        (
            100.0,
            4.035349e-4,
            0.04035349,
            4.270160,
            1.192689,
            5.092972,
            False,
        ),
        (
            1.0,
            0.04035349,
            4.035349,
            0.04270160,
            25.29749,
            1.080243,
            False,
        ),
    ],
)
def test_beskok_karniadakis_civan_fixtures_b1_b2_b3(
    pressure_pa,
    expected_lambda_m,
    expected_knudsen,
    expected_c_p_l_s,
    expected_f_bk,
    expected_c_bk_l_s,
    allow_near_viscous_cross_check,
):
    eta_pa_s = tr.dynamic_viscosity_sutherland_pa_s(HOT_TEMPERATURE_K)
    assert eta_pa_s == pytest.approx(5.747722e-5, rel=1e-7)

    mfp = tr.single_species_mean_free_path(
        "N2",
        pressure_pa,
        HOT_TEMPERATURE_K,
        DIAMETER_M,
    )
    assert mfp.lambda_m == pytest.approx(expected_lambda_m, rel=1e-6)
    assert mfp.knudsen_number == pytest.approx(expected_knudsen, rel=1e-6)

    poiseuille = tr.poiseuille_conductance_m3_s(
        DIAMETER_M,
        LENGTH_M,
        pressure_pa,
        eta_pa_s,
    )
    assert poiseuille * 1000.0 == pytest.approx(expected_c_p_l_s, rel=1e-6)
    assert tr.beskok_karniadakis_rarefaction_factor(
        mfp.knudsen_number,
        allow_near_viscous_cross_check=allow_near_viscous_cross_check,
    ) == pytest.approx(expected_f_bk, rel=1e-6)
    assert tr.beskok_karniadakis_civan_conductance_m3_s(
        DIAMETER_M,
        LENGTH_M,
        pressure_pa,
        eta_pa_s,
        knudsen_number=mfp.knudsen_number,
        allow_near_viscous_cross_check=allow_near_viscous_cross_check,
    ) * 1000.0 == pytest.approx(expected_c_bk_l_s, rel=1e-6)


def test_single_carrier_mean_free_path_fixture_m1():
    result = tr.single_species_mean_free_path(
        "N2",
        1300.0,
        HOT_TEMPERATURE_K,
        DIAMETER_M,
    )

    assert result.lambda_m == pytest.approx(3.104114e-5, rel=1e-6)
    assert result.knudsen_number == pytest.approx(0.003104114, rel=1e-6)
    assert result.regime is tr.KnudsenRegime.VISCOUS
    assert result.formula_id == tr.FORMULA_SINGLE_SPECIES_MFP
    assert result.carriers[0].species == "N2"
    assert result.carriers[0].mole_fraction == pytest.approx(1.0)
    assert result.collision_diameter_source == tr.COLLISION_DIAMETER_SOURCE


def test_kr_carrier_mean_free_path_direction_vs_n2_and_ar():
    """Kr transport honesty, asserted directionally (t-395).

    Premise: lambda = k_B T / (sqrt(2) pi sigma^2 P); molar mass does
    NOT enter. Algebra: lambda ratios go as (sigma_b/sigma_a)^2, so with
    sigma(Ar)=3.542 A < sigma(Kr)=3.655 A < sigma(N2)=3.798 A: lambda_Kr
    = lambda_Ar*(3.542/3.655)^2 ~= 0.94*lambda_Ar (6% shorter) and
    lambda_Kr = lambda_N2*(3.798/3.655)^2 ~= 1.08*lambda_N2 (8% longer).
    Kr is transport-COMPARABLE to N2, better than Ar; the decisive Kr
    advantage is downstream cryo separation, not the sweep (writing this
    derivation caught the initial 'Kr superior sweep' overstatement).
    Units: J/K * K / (m^2 * Pa) = m. Sanity: the m1 fixture's N2 lambda
    at this operating point is ~3.1e-5 m; all three carriers land the
    same order of magnitude and the SAME viscous regime.
    """
    kr = tr.single_species_mean_free_path(
        "Kr", 1300.0, HOT_TEMPERATURE_K, DIAMETER_M,
    )
    n2 = tr.single_species_mean_free_path(
        "N2", 1300.0, HOT_TEMPERATURE_K, DIAMETER_M,
    )
    ar = tr.single_species_mean_free_path(
        "Ar", 1300.0, HOT_TEMPERATURE_K, DIAMETER_M,
    )

    assert kr.lambda_m < ar.lambda_m
    assert kr.knudsen_number < ar.knudsen_number
    assert kr.lambda_m == pytest.approx(n2.lambda_m, rel=0.09)
    assert kr.regime is tr.KnudsenRegime.VISCOUS
    assert kr.collision_diameter_source == tr.COLLISION_DIAMETER_SOURCE


def test_single_carrier_mean_free_path_fixture_m2():
    result = tr.single_species_mean_free_path(
        "Ar",
        1.0,
        HOT_TEMPERATURE_K,
        DIAMETER_M,
    )

    assert result.lambda_m == pytest.approx(0.04639742, rel=1e-6)
    assert result.knudsen_number == pytest.approx(4.639742, rel=1e-6)
    assert result.regime is tr.KnudsenRegime.TRANSITIONAL
    assert result.formula_id == tr.FORMULA_SINGLE_SPECIES_MFP
    assert result.carriers[0].species == "Ar"


def test_mixture_mean_free_path_fixture_m3():
    result = tr.carrier_mixture_mean_free_path(
        "N2",
        {"N2": 0.8, "Ar": 0.2},
        1300.0,
        HOT_TEMPERATURE_K,
        DIAMETER_M,
    )

    assert result.lambda_m == pytest.approx(3.192762e-5, rel=1e-6)
    assert result.knudsen_number == pytest.approx(0.003192762, rel=1e-6)
    assert result.regime is tr.KnudsenRegime.VISCOUS
    assert result.formula_id == tr.FORMULA_MIXTURE_MFP
    assert result.test_species == "N2"
    assert tuple(carrier.species for carrier in result.carriers) == ("N2", "Ar")
    assert result.carriers[0].collision_diameter_m == pytest.approx(3.798e-10)
    assert result.carriers[1].collision_diameter_m == pytest.approx(3.542e-10)


@pytest.mark.parametrize(
    ("species", "sigma_m", "molar_mass"),
    [
        ("He", 2.551e-10, 0.004002602),
        ("N2", 3.798e-10, 0.0280134),
        ("Ar", 3.542e-10, 0.039948),
        # Kr exact anchors (milestone review F2: the direction test's 9%
        # band alone would accept a neighbor-row copy-paste).
        ("Kr", 3.655e-10, 0.083798),
        ("CO2", 3.941e-10, 0.0440095),
    ],
)
def test_cover_gas_property_registry_matches_transport_anchors(
    species, sigma_m, molar_mass
):
    properties = tr.CARRIER_GAS_PROPERTIES[species]
    assert properties.collision_diameter_m == pytest.approx(sigma_m)
    assert properties.molar_mass_kg_mol == pytest.approx(molar_mass)


def test_helium_carrier_changes_mixture_transport():
    nitrogen = tr.carrier_mixture_mean_free_path(
        "N2", {"N2": 1.0}, 1300.0, HOT_TEMPERATURE_K, DIAMETER_M
    )
    helium = tr.carrier_mixture_mean_free_path(
        "N2", {"He": 1.0}, 1300.0, HOT_TEMPERATURE_K, DIAMETER_M
    )
    assert helium.lambda_m != pytest.approx(nitrogen.lambda_m)


@pytest.mark.parametrize(
    ("knudsen_number", "expected"),
    [
        (0.0, tr.KnudsenRegime.VISCOUS),
        (0.009999, tr.KnudsenRegime.VISCOUS),
        (0.01, tr.KnudsenRegime.TRANSITIONAL),
        (9.999999, tr.KnudsenRegime.TRANSITIONAL),
        (10.0, tr.KnudsenRegime.FREE_MOLECULAR),
        (math.inf, tr.KnudsenRegime.FREE_MOLECULAR),
    ],
)
def test_knudsen_handoff_boundaries_match_project_thresholds(
    knudsen_number,
    expected,
):
    assert tr.classify_knudsen_regime(knudsen_number) is expected


@pytest.mark.parametrize(
    ("knudsen_number", "expected"),
    [
        pytest.param(math.nan, None, id="nan"),
        pytest.param(-1.0, None, id="negative"),
        pytest.param(-math.inf, None, id="negative-infinity"),
        pytest.param(0.0, tr.KnudsenRegime.VISCOUS, id="zero"),
        pytest.param(
            math.inf,
            tr.KnudsenRegime.FREE_MOLECULAR,
            id="positive-infinity",
        ),
    ],
)
def test_knudsen_classifier_domain_contract(knudsen_number, expected):
    if expected is not None:
        assert tr.classify_knudsen_regime(knudsen_number) is expected
        return

    with pytest.raises(tr.TransportRegimeRefusal) as exc_info:
        tr.classify_knudsen_regime(knudsen_number)

    assert exc_info.value.category == "invalid_knudsen_number"
    assert exc_info.value.reason == "invalid_knudsen_number"


@pytest.mark.parametrize(
    ("call", "expected_category"),
    [
        (
            lambda: tr.single_species_mean_free_path(
                "SiO",
                1.0,
                HOT_TEMPERATURE_K,
                DIAMETER_M,
            ),
            "uncertified_collision_diameter",
        ),
        (
            lambda: tr.carrier_mixture_mean_free_path(
                "N2",
                {},
                1300.0,
                HOT_TEMPERATURE_K,
                DIAMETER_M,
            ),
            "missing_carrier_state",
        ),
        (
            lambda: tr.molecular_aperture_conductance_m3_s(
                AREA_M2,
                HOT_TEMPERATURE_K,
                tr.MOLAR_MASSES_KG_PER_MOL["N2"],
                knudsen_number=9.999999,
            ),
            "aperture_requires_free_molecular",
        ),
        (
            lambda: tr.long_tube_clausing_transmission(
                DIAMETER_M,
                DIAMETER_M,
            ),
            "clausing_long_tube_asymptote_out_of_range",
        ),
        (
            lambda: tr.long_tube_molecular_conductance_m3_s(
                DIAMETER_M,
                LENGTH_M,
                HOT_TEMPERATURE_K,
                tr.MOLAR_MASSES_KG_PER_MOL["N2"],
                knudsen_number=4.0,
            ),
            "tube_requires_free_molecular",
        ),
        (
            lambda: tr.beskok_karniadakis_civan_conductance_m3_s(
                DIAMETER_M,
                LENGTH_M,
                1300.0,
                tr.dynamic_viscosity_sutherland_pa_s(HOT_TEMPERATURE_K),
                knudsen_number=0.003104114,
            ),
            "transitional_correlation_out_of_range",
        ),
        (
            lambda: tr.beskok_karniadakis_civan_conductance_m3_s(
                DIAMETER_M,
                LENGTH_M,
                1.0,
                tr.dynamic_viscosity_sutherland_pa_s(HOT_TEMPERATURE_K),
                knudsen_number=10.0,
            ),
            "transitional_correlation_out_of_range",
        ),
    ],
)
def test_transport_formula_refusals_are_named(call, expected_category):
    with pytest.raises(tr.TransportRegimeRefusal) as exc_info:
        call()

    assert exc_info.value.category == expected_category
    assert exc_info.value.reason == expected_category
    assert exc_info.value.stage is None
    assert exc_info.value.asking_site is None


@pytest.mark.parametrize(
    ("campaign_name", "expected"),
    [
        ("C0", tr.ProcessRegime.STAGE0_BAKEOUT),
        ("C0B", tr.ProcessRegime.STAGE0_BAKEOUT),
        ("C0b_p_cleanup", tr.ProcessRegime.STAGE0_BAKEOUT),
        ("C2A", tr.ProcessRegime.PYROLYSIS_EXTRACTION),
        ("C2A_staged", tr.ProcessRegime.PYROLYSIS_EXTRACTION),
        ("C4", tr.ProcessRegime.PYROLYSIS_EXTRACTION),
        ("C7_CA_ALUMINOTHERMIC", tr.ProcessRegime.PYROLYSIS_EXTRACTION),
        ("C5", tr.ProcessRegime.ELECTROLYSIS),
        ("IDLE", tr.ProcessRegime.IDLE),
        (None, tr.ProcessRegime.UNKNOWN),
        ("", tr.ProcessRegime.UNKNOWN),
        ("not_a_campaign", tr.ProcessRegime.UNKNOWN),
    ],
)
def test_resolve_process_regime_maps_campaigns(campaign_name, expected):
    assert tr.resolve_process_regime(campaign_name) is expected


def test_continuum_transport_is_load_bearing_except_stage0_bakeout():
    assert tr.continuum_transport_is_load_bearing(
        tr.ProcessRegime.PYROLYSIS_EXTRACTION
    )
    assert tr.continuum_transport_is_load_bearing(tr.ProcessRegime.UNKNOWN)
    assert tr.continuum_transport_is_load_bearing(tr.ProcessRegime.ELECTROLYSIS)
    assert tr.continuum_transport_is_load_bearing(tr.ProcessRegime.IDLE)
    assert not tr.continuum_transport_is_load_bearing(
        tr.ProcessRegime.STAGE0_BAKEOUT
    )


def test_assess_continuum_validity_marks_bakeout_and_refuses_pyrolysis():
    bakeout = tr.assess_continuum_formula_validity(
        0.1,
        campaign_name="C0",
        asking_site="tests.bakeout",
    )
    pyrolysis = tr.assess_continuum_formula_validity(
        0.1,
        campaign_name="C4",
        asking_site="tests.pyrolysis",
    )
    missing = tr.assess_continuum_formula_validity(
        0.1,
        asking_site="tests.missing_stage",
    )
    viscous = tr.assess_continuum_formula_validity(
        0.001,
        campaign_name="C4",
        asking_site="tests.viscous",
    )

    assert bakeout.action is tr.ContinuumValidityAction.MARK
    assert bakeout.note["zero_because"] == "out_of_domain_marked"
    assert bakeout.note["doctrine_category"] == 2
    assert bakeout.stage == "C0"

    assert pyrolysis.action is tr.ContinuumValidityAction.REFUSE
    assert pyrolysis.note["doctrine_category"] == 1
    assert pyrolysis.stage == "C4"

    assert missing.action is tr.ContinuumValidityAction.REFUSE
    assert missing.process_regime is tr.ProcessRegime.UNKNOWN

    assert viscous.action is tr.ContinuumValidityAction.OK
    assert viscous.in_domain is True


def test_refuse_continuum_formula_if_load_bearing_names_stage():
    bakeout = tr.refuse_continuum_formula_if_load_bearing(
        0.1,
        campaign_name="C0",
        asking_site="tests.bakeout_guard",
    )
    assert bakeout.action is tr.ContinuumValidityAction.MARK

    with pytest.raises(tr.TransportRegimeRefusal) as exc_info:
        tr.refuse_continuum_formula_if_load_bearing(
            0.1,
            campaign_name="C4",
            asking_site="tests.pyrolysis_guard",
        )
    assert exc_info.value.category == "continuum_formula_out_of_domain"
    assert exc_info.value.stage == "C4"
    assert exc_info.value.asking_site == "tests.pyrolysis_guard"
    assert exc_info.value.process_regime == (
        tr.ProcessRegime.PYROLYSIS_EXTRACTION.value
    )
    assert "stage=C4" in str(exc_info.value)


def test_kr_epsilon_over_k_exact_anchor():
    """Milestone review F2: eps/k was asserted by no test for any carrier;
    pin Kr's (BSL Table E.1) so a silent edit cannot drift it."""
    assert tr.CARRIER_GAS_PROPERTIES[
        "Kr"
    ].lennard_jones_epsilon_over_k_K == pytest.approx(178.9)


def test_kr_carrier_key_normalizers_accept_kr_aliases():
    """Milestone review F3: the Kr normalizer branches shipped with zero
    coverage. Pin both acceptance sites and that Ar stays accepted."""
    from simulator.condensation import _canonical_carrier_gas_key
    from simulator.core import PyrolysisSimulator

    normalize = PyrolysisSimulator._normalize_condensation_carrier_gas
    for alias in ("Kr", "pKr", "kr", "p_kr", "P-KR"):
        assert normalize(alias) == "Kr", alias
        assert _canonical_carrier_gas_key(alias) == "Kr", alias
    assert normalize("pAr") == "Ar"
    assert _canonical_carrier_gas_key("pAr") == "Ar"


def test_core_carrier_normalizer_delegates_without_changing_unset_semantics(monkeypatch):
    import simulator.condensation as condensation
    from simulator.core import PyrolysisSimulator

    normalize = PyrolysisSimulator._normalize_condensation_carrier_gas
    assert normalize(None) == ""
    assert normalize(None, allow_unset=False) == "N2"
    assert normalize("   ") == ""
    with pytest.raises(ValueError, match="must be non-empty"):
        normalize("   ", allow_unset=False)

    calls = []

    def canonical(value, *, allow_unset=False):
        calls.append((value, allow_unset))
        return "Kr"

    monkeypatch.setattr(condensation, "_canonical_carrier_gas_key", canonical)

    raw_value = object()
    assert normalize(raw_value) == "Kr"
    assert calls == [(raw_value, True)]


@pytest.mark.parametrize(
    ("value", "expected", "error_message"),
    [
        (None, "N2", None),
        ("", None, "condensation carrier_gas must be non-empty when provided"),
        ("   ", None, "condensation carrier_gas must be non-empty when provided"),
        ("pKr", "Kr", None),
        ("pAr", "Ar", None),
        (
            "bogus",
            None,
            "Unsupported condensation carrier_gas 'bogus'; supported carrier "
            "gases: He/pHe, N2/pN2, Kr/pKr, Ar/pAr (legacy), O2/pO2, CO2/pCO2",
        ),
        (
            " bogus ",
            None,
            "Unsupported condensation carrier_gas ' bogus '; supported carrier "
            "gases: He/pHe, N2/pN2, Kr/pKr, Ar/pAr (legacy), O2/pO2, CO2/pCO2",
        ),
        (
            123,
            None,
            "Unsupported condensation carrier_gas 123; supported carrier gases: "
            "He/pHe, N2/pN2, Kr/pKr, Ar/pAr (legacy), O2/pO2, CO2/pCO2",
        ),
    ],
)
def test_core_carrier_normalizer_preserves_legacy_edge_behavior(
    value, expected, error_message
):
    from simulator.core import PyrolysisSimulator

    normalize = PyrolysisSimulator._normalize_condensation_carrier_gas
    if error_message is None:
        assert normalize(value, allow_unset=False) == expected
        return

    with pytest.raises(ValueError) as exc_info:
        normalize(value, allow_unset=False)
    assert type(exc_info.value) is ValueError
    assert str(exc_info.value) == error_message
