from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from engines.alphamelts.domain import (
    MELTS_CASIO3_LIQUID_G0_FIELD,
    MELTS_CAO_EXCHANGE_MU_FIELD,
    MELTS_PWO_G0_FIELD,
)
from simulator.diagnostic_helpers.binary_pot_battery import (
    EquilibrateCell,
    Po2Request,
)
from simulator.engine_pool import EngineWorkerDied
from simulator.melt_backend.pure_phase import PurePhaseAccessError
from simulator.melt_backend.pure_phase_janaf_score import (
    ELEMENT_REFERENCE_TABLES,
    MELTS_CASIO3_HHR_SOLID_RESIDUAL_AT_1400_J_MOL,
    casio3_fusion_delta_g_kJ_mol,
    casio3_fusion_model_systematic_dex,
    cao_activity_from_melts_exchange_mu,
    element_reference_g_kJ_mol,
    formation_g_from_apparent_kJ_mol,
    janaf_values_at,
    load_janaf_table,
    melts_reference_g0_cao_kJ_mol,
    _janaf_column_at,
)
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET, GAS_CONSTANT
from tests.engine_availability import thermoengine_package_available


def test_cao_activity_conversion_uses_requested_janaf_reference() -> None:
    assert ELEMENT_REFERENCE_TABLES['Ca'] == 'Ca-001'
    reference_temperature_K = 1900.0
    cao_row = janaf_values_at(
        load_janaf_table('Ca-027'), reference_temperature_K,
    )
    assert cao_row is not None and cao_row.formation_gibbs_kJ_mol is not None
    element_g: dict[str, float] = {}
    for element in ('Ca', 'O2'):
        values = janaf_values_at(
            load_janaf_table(ELEMENT_REFERENCE_TABLES[element]),
            reference_temperature_K,
        )
        assert values is not None
        assert values.enthalpy_increment_kJ_mol is not None
        assert values.S_J_K_mol is not None
        element_g[element] = element_reference_g_kJ_mol(
            values.enthalpy_increment_kJ_mol,
            values.S_J_K_mol,
            reference_temperature_K,
        )
    cao_g0_kJ_mol, _ = melts_reference_g0_cao_kJ_mol(
        reference_temperature_K, 'cr',
    )
    assert formation_g_from_apparent_kJ_mol(
        cao_g0_kJ_mol,
        {'Ca': 1.0, 'O2': 0.5},
        element_g,
    ) == pytest.approx(cao_row.formation_gibbs_kJ_mol, abs=1e-9)

    temperature_K = 1933.0
    g0_solid_kJ_mol, solid_extrapolated = melts_reference_g0_cao_kJ_mol(
        temperature_K, 'cr',
    )
    activity, extrapolated = cao_activity_from_melts_exchange_mu(
        g0_solid_kJ_mol * 1000.0,
        temperature_K,
        phase='cr',
    )
    assert activity == pytest.approx(1.0, abs=1e-12)
    assert not solid_extrapolated
    assert not extrapolated

    g0_liquid_kJ_mol, liquid_extrapolated = melts_reference_g0_cao_kJ_mol(
        temperature_K, 'l',
    )
    liquid_activity, extrapolated = cao_activity_from_melts_exchange_mu(
        g0_liquid_kJ_mol * 1000.0,
        temperature_K,
        phase='l',
    )
    assert liquid_activity == pytest.approx(1.0, abs=1e-12)
    assert liquid_extrapolated
    assert extrapolated


def test_cao_exchange_mu_survives_the_compact_cell_cache() -> None:
    from simulator.melt_backend.base import EquilibriumResult

    cell = EquilibrateCell(
        pot_id='cao-peer',
        engine='thermoengine',
        temperature_K=1933.0,
        po2=Po2Request(mode='engine_default', po2_bar=None),
        status='ok',
        refusal_reason=None,
        engine_status='ok',
        engine_reason=None,
        melt_activities={'CaSiO3': 0.4, 'SiO2': 0.2},
        gas_partial_pressures_Pa={},
        liquid_fraction=1.0,
        wall_s=0.0,
        cpu_s=0.0,
        hostname='test',
        mu_CaO_J_per_mol_melts_berman_apparent=12345.0,
        g0_CaSiO3_liquid_J_per_mol_melts_berman_apparent=-900000.0,
        g0_pWo_J_per_mol_melts_berman_apparent=-800000.0,
    )

    restored = EquilibrateCell.from_engine_cache_value(
        cell.to_engine_cache_value()
    )
    assert restored.mu_CaO_J_per_mol_melts_berman_apparent == 12345.0
    assert (
        restored.g0_CaSiO3_liquid_J_per_mol_melts_berman_apparent
        == -900000.0
    )
    assert restored.g0_pWo_J_per_mol_melts_berman_apparent == -800000.0
    payload = EquilibrateCell.from_cache_payload(cell.to_cache_payload())
    assert payload.mu_CaO_J_per_mol_melts_berman_apparent == 12345.0
    assert (
        payload.g0_CaSiO3_liquid_J_per_mol_melts_berman_apparent
        == -900000.0
    )
    assert payload.g0_pWo_J_per_mol_melts_berman_apparent == -800000.0

    equilibrium = EquilibriumResult(
        liquid_fraction=1.0,
        diagnostics={
            MELTS_CAO_EXCHANGE_MU_FIELD: 12345.0,
            MELTS_CASIO3_LIQUID_G0_FIELD: -900000.0,
            MELTS_PWO_G0_FIELD: -800000.0,
        },
    )
    restored_equilibrium = EquilibriumResult.from_engine_cache_value(
        equilibrium.to_engine_cache_value(engine_id='alphamelts'),
        engine_id='alphamelts',
    )
    assert restored_equilibrium.diagnostics == equilibrium.diagnostics


def test_thermoengine_exchange_mu_requires_calcium_in_the_composition() -> None:
    from engines.alphamelts.thermoengine import (
        ThermoEngineTransport,
    )
    from simulator.melt_backend.alphamelts import activity_from_chem_potential

    class LiquidPhase:
        endmember_names = ('CaSiO3', 'SiO2')

        def chem_potential(self, _temperature_K, _pressure_bar, *, mol):
            assert len(mol[0]) == 2
            return [[-100000.0, -120000.0]]

        def gibbs_energy(self, _temperature_K, _pressure_bar, *, mol):
            return [-100000.0 if mol[0][0] == 1.0 else -120000.0]

    transport = ThermoEngineTransport(
        model_name='MELTSv1.0.2',
        activity_converter=activity_from_chem_potential,
    )
    transport._liq_phase = LiquidPhase()
    ca_free_exchange: dict[str, float] = {}
    ca_free_activities = transport._activities_from_chemical_potentials(
        temperature_C=1700.0,
        pressure_bar=1.0,
        component_mole_fraction={'SiO2': 1.0},
        comp_wt={'SiO2': 100.0, 'CaO': 0.0},
        cao_exchange_out=ca_free_exchange,
    )
    assert ca_free_exchange == {}
    assert ca_free_activities == {'CaSiO3': 1.0, 'SiO2': 1.0}

    ca_bearing_exchange: dict[str, float] = {}
    ca_bearing_activities = transport._activities_from_chemical_potentials(
        temperature_C=1700.0,
        pressure_bar=1.0,
        component_mole_fraction={'CaSiO3': 0.5, 'SiO2': 0.5},
        comp_wt={'CaO': 50.0, 'SiO2': 50.0},
        cao_exchange_out=ca_bearing_exchange,
    )
    assert ca_bearing_activities == ca_free_activities
    assert ca_bearing_exchange[MELTS_CAO_EXCHANGE_MU_FIELD] == 20000.0


def test_alphamelts_exchange_mu_requires_positive_component_activities() -> None:
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend

    backend = AlphaMELTSBackend()
    provider = {
        'mu': {'CaSiO3': -100000.0, 'SiO2': -120000.0},
        'mu0': {'CaSiO3': -101000.0, 'SiO2': -121000.0},
    }
    exchange_mu = backend._cao_exchange_mu_from_chemical_potentials(
        provider,
        temperature_C=1700.0,
    )
    assert exchange_mu == 20000.0
    assert backend._cao_exchange_mu_from_chemical_potentials(
        {'activities': {'CaSiO3': 1.0, 'SiO2': 1.0}},
        temperature_C=1700.0,
    ) is None

    nonpositive = {
        'mu': {'CaSiO3': -1.0e12, 'SiO2': -120000.0},
        'mu0': {'CaSiO3': 0.0, 'SiO2': -121000.0},
    }
    assert backend._cao_exchange_mu_from_chemical_potentials(
        nonpositive,
        temperature_C=1700.0,
    ) is None


def test_alphamelts_cell_exposes_the_fusion_reference_energies(monkeypatch) -> None:
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend

    backend = AlphaMELTSBackend()
    monkeypatch.setattr(
        backend,
        '_pwo_g0_for_cao_fusion',
        lambda **_kwargs: -800000.0,
    )
    result = backend._parse_petthermotools_result(
        {
            'Conditions': {'mass': 1.0, 'P_bar': 1.0, 'fO2_log': -10.0},
            'Liquid': {'CaO_Liq': 50.0, 'SiO2_Liq': 50.0},
            'Liquid_prop': {'mass': 1.0},
            'mu': {'CaSiO3': -90000.0, 'SiO2': -100000.0},
            'mu0': {'CaSiO3': -100000.0, 'SiO2': -120000.0},
        },
        temperature_C=1700.0,
        pressure_bar=1.0,
        fO2_log=-10.0,
        comp_wt={'CaO': 50.0, 'SiO2': 50.0},
    )

    assert result.diagnostics[MELTS_CAO_EXCHANGE_MU_FIELD] == 10000.0
    assert result.diagnostics[MELTS_CASIO3_LIQUID_G0_FIELD] == -100000.0
    assert result.diagnostics[MELTS_PWO_G0_FIELD] == -800000.0


def _apparent_element_g_kJ_mol(temperature_K: float) -> dict[str, float]:
    terms: dict[str, float] = {}
    for element in ('Ca', 'Si', 'O2'):
        table_id = ELEMENT_REFERENCE_TABLES[element]
        enthalpy = _janaf_column_at(
            table_id, 'enthalpy_increment', temperature_K,
        )
        entropy = _janaf_column_at(table_id, 'entropy', temperature_K)
        terms[element] = element_reference_g_kJ_mol(
            enthalpy,
            entropy,
            temperature_K,
        )
    return terms


def _hhr_casio3_source() -> dict:
    path = (
        Path(__file__).resolve().parents[1]
        / 'data/literature/compilations/hemingway-haas-robinson-1982-usgs-b1544'
        / 'records/usgs-b1544-casio3-reference.json'
    )
    return json.loads(path.read_text(encoding='utf-8'))


def _hhr_casio3_row(source: dict, temperature_K: float) -> dict:
    return next(
        row for row in source['rows']
        if row['temperature']['value'] is not None
        and float(row['temperature']['value']) == temperature_K
        and row['formation']['from_the_elements']['gibbs_energy']['value']
        is not None
    )


def _thermoengine_casio3_binary(temperature_K: float):
    from simulator.melt_backend.thermoengine import ThermoEngineBackend

    backend = ThermoEngineBackend()
    if not backend.initialize({}):
        pytest.skip(backend.unavailable_reason())
    result = backend.equilibrate(
        temperature_C=temperature_K - CELSIUS_TO_KELVIN_OFFSET,
        composition_kg={'CaO': 56.077, 'SiO2': 60.0843},
        fO2_log=-10.0,
        pressure_bar=1.0,
    )
    assert result.status == 'ok', result.warnings
    return backend, result


@pytest.mark.skipif(
    not thermoengine_package_available(),
    reason='ThermoEngine runtime is not installed',
)
def test_thermoengine_sio2_liquid_g0_uses_janaf_o038_element_basis() -> None:
    temperature_K = 1933.0
    backend, result = _thermoengine_casio3_binary(temperature_K)
    try:
        components = result.chem_potentials['Liquid']['components']
        activity = float(result.activity_coefficients['SiO2'])
        assert activity > 0.0
        # Premise: μ(SiO2)=G0(SiO2,l)+RT ln(a). Algebra removes RT ln(a),
        # then JANAF ΔfG(O-038) gains Si-001 + one O2-029 molecule. Units are
        # kJ/mol after the element terms; the J/mol residual converts to dex
        # by RT ln(10). Sanity: the same activity is positive and finite.
        melts_g0_kJ_mol = (
            float(components['SiO2'])
            - GAS_CONSTANT * temperature_K * math.log(activity)
        ) / 1000.0
        silica_delta_f_g_kJ_mol = _janaf_column_at(
            'O-038', 'formation_gibbs_energy', temperature_K,
        )
        element_g = _apparent_element_g_kJ_mol(temperature_K)
        janaf_g0_kJ_mol = (
            silica_delta_f_g_kJ_mol + element_g['Si'] + element_g['O2']
        )
        residual_kJ_mol = melts_g0_kJ_mol - janaf_g0_kJ_mol
        residual_dex = residual_kJ_mol * 1000.0 / (
            GAS_CONSTANT * temperature_K * math.log(10.0)
        )
        # O-038 and each element table print these Gibbs/enthalpy tokens to
        # 0.001 kJ and entropy to 0.001 J/K. A conservative worst-case sum of
        # token rounding contributes <0.004 kJ/mol here; no formal uncertainty
        # is published. Pin the measured residual within that source precision.
        assert residual_kJ_mol == pytest.approx(-0.084, abs=0.004)
        assert math.isfinite(residual_dex)
    finally:
        backend.close()


@pytest.mark.skipif(
    not thermoengine_package_available(),
    reason='ThermoEngine runtime is not installed',
)
def test_thermoengine_named_solid_casio3_hhr_residual_is_disclosed_not_applied() -> None:
    from simulator.melt_backend.thermoengine import ThermoEngineBackend

    source = _hhr_casio3_source()
    assert source['compilation_role']['engine_reference_input'] is True
    assert source['compilation_role']['scoring_eligible'] is False
    assert 'Wollastonite crystals 298.15 to 1398 K' in source['phase_as_published']
    assert 'Cyclowollastonite is the stable phase above 1398 K' in source[
        'phase_as_published'
    ]
    source_uncertainty_kJ_mol = float(
        source['uncertainty']['from_the_elements']['formation_gibbs_energy']['value']
    )
    backend = ThermoEngineBackend()
    if not backend.initialize({}):
        pytest.skip(backend.unavailable_reason())
    try:
        # Premise: at 1400 K HHR names stable cyclowollastonite; at 1000 K it
        # names wollastonite. Add identical Ca-001 + Si-001 + 3/2 O-029 terms
        # to the phase-named ΔfG values before comparing to Berman. HHR is an
        # engine_reference_input with scoring_eligible:false; Berman (1988)
        # used Haas/Hemingway data, so these +3.15/+2.95 kJ/mol results are a
        # consistency disclosure, never independent validation or a correction.
        # Robie & Hemingway (1995), USGS B2131, is also in the corpus. J/kJ are
        # converted explicitly; HHR's published ±0.608 kJ/mol uncertainty is
        # the only tolerance used for these consistency comparisons.
        row_1400 = _hhr_casio3_row(source, 1400.0)
        terms_1400 = _apparent_element_g_kJ_mol(1400.0)
        janaf_1400 = (
            float(row_1400['formation']['from_the_elements']['gibbs_energy']['value'])
            + terms_1400['Ca'] + terms_1400['Si'] + 1.5 * terms_1400['O2']
        )
        berman_pwo = backend.pure_phase_properties(
            'pWo', temperature_K=1400.0, pressure_bar=1.0,
        )
        assert berman_pwo.G_J_mol is not None
        residual_1400_kJ_mol = berman_pwo.G_J_mol / 1000.0 - janaf_1400
        assert residual_1400_kJ_mol == pytest.approx(
            3.151, abs=source_uncertainty_kJ_mol,
        ), 'HHR (1982), pp. 49–50: ΔfG uncertainty is ±0.608 kJ/mol'

        row_1000 = _hhr_casio3_row(source, 1000.0)
        terms_1000 = _apparent_element_g_kJ_mol(1000.0)
        janaf_1000 = (
            float(row_1000['formation']['from_the_elements']['gibbs_energy']['value'])
            + terms_1000['Ca'] + terms_1000['Si'] + 1.5 * terms_1000['O2']
        )
        berman_wo = backend.pure_phase_properties(
            'Wo', temperature_K=1000.0, pressure_bar=1.0,
        )
        assert berman_wo.G_J_mol is not None
        residual_1000_kJ_mol = berman_wo.G_J_mol / 1000.0 - janaf_1000
        assert residual_1000_kJ_mol == pytest.approx(
            2.947, abs=source_uncertainty_kJ_mol,
        ), (
            'HHR (1982), pp. 49–50: ΔfG uncertainty is ±0.608 kJ/mol'
        )
        row_temperature_K = 1933.0
        residual_band_dex = (
            MELTS_CASIO3_HHR_SOLID_RESIDUAL_AT_1400_J_MOL
            / (GAS_CONSTANT * row_temperature_K * math.log(10.0))
        )
        assert residual_band_dex == pytest.approx(0.085, abs=0.001)
    finally:
        backend.close()


@pytest.mark.skipif(
    not thermoengine_package_available(),
    reason='ThermoEngine runtime is not installed',
)
def test_thermoengine_casio3_liquid_fusion_residual_is_a_dex_systematic() -> None:
    import yaml

    temperature_K = 1933.0
    source_path = (
        Path(__file__).resolve().parents[1]
        / 'data/literature/extracts/adamkovicova-1980-casio3-heat-of-fusion.yaml'
    )
    source = yaml.safe_load(source_path.read_text(encoding='utf-8'))
    assert source['source_id'] == 'adamkovicova-1980-casio3-heat-of-fusion'
    fusion = next(
        row for row in source['species']['CaSiO3']['observations']
        if row['observation_id'] == 'adamkovicova_1980_heat_of_fusion_at_1817K'
    )
    assert fusion['locator']['published_page'] == 197
    assert fusion['locator']['equation'] == '8'
    solid_cp = next(
        row for row in source['species']['CaSiO3']['observations']
        if row['observation_id'] == 'adamkovicova_1980_solid_heat_capacity_eq4'
    )
    assert solid_cp['locator']['published_page'] == 196
    assert solid_cp['locator']['equation'] == '4'
    liquid_hrel = next(
        row for row in source['species']['CaSiO3']['observations']
        if row['observation_id'] == 'adamkovicova_1980_melt_relative_enthalpy_fit_eq7'
    )
    assert liquid_hrel['locator']['published_page'] == 197
    assert liquid_hrel['locator']['equation'] == '7'
    assert fusion['values']['delta_H_fus_kJ_mol-1'] == 57.3
    assert fusion['values']['uncertainty_kJ_mol-1'] == 2.9
    assert fusion['values']['T_fus_K'] == 1817
    backend, result = _thermoengine_casio3_binary(temperature_K)
    try:
        components = result.chem_potentials['Liquid']['components']
        activity = float(result.activity_coefficients['CaSiO3'])
        assert activity > 0.0
        # The source reports “ΔH_fus(1817 K) = 57.3 ± 2.9 kJ mol−1”
        # (Adamkovičová et al., 1980, p. 197, Eq. 8); Eq. (4), p. 196, gives
        # Cp(s), while the slope of the liquid Hrel fit Eq. (7), p. 197, gives
        # Cp(l). Premise: both
        # G0 values are Berman apparent energies for one mol
        # CaSiO3. Ca-001 + Si-001 + 3/2 O-029 cancel in the liquid-solid
        # difference. Algebra removes RT ln(a) from μ(CaSiO3), then compares
        # G0(liquid)−G0(pWo) with ΔH_fus(1−T/Tfus)+∫ΔCp dT−T∫ΔCp/T dT.
        # Units are J/mol before conversion to dex by RT ln(10). Sanity: the
        # signed residual remains a model systematic; this check never applies
        # it to the CaO chemical potential.
        g0_liquid_J_mol = (
            float(components['CaSiO3'])
            - GAS_CONSTANT * temperature_K * math.log(activity)
        )
        pWo = backend.pure_phase_properties(
            'pWo', temperature_K=temperature_K, pressure_bar=1.0,
        )
        assert pWo.G_J_mol is not None
        melts_delta_g_J_mol = g0_liquid_J_mol - pWo.G_J_mol
        source_delta_g_kJ_mol = casio3_fusion_delta_g_kJ_mol(temperature_K)
        residual_kJ_mol = melts_delta_g_J_mol / 1000.0 - source_delta_g_kJ_mol
        residual_dex = casio3_fusion_model_systematic_dex(
            melts_delta_g_J_mol, temperature_K,
        )
        source_tolerance_kJ_mol = (
            abs(1.0 - temperature_K / 1817.0) * 2.9 + 0.001
        )
        assert residual_kJ_mol == pytest.approx(
            -0.233, abs=source_tolerance_kJ_mol,
        ), '±2.9 kJ/mol ΔH_fus uncertainty propagated to 1933 K, plus Cp token rounding'
        assert math.isfinite(residual_dex)
        caveat = next(
            notice
            for notice in result.diagnostics['imcc_notices']
            if 'CaSiO3(l)-pWo fusion residual' in notice['reason']
        )
        assert caveat['kind'] == 'engine_model_caveat'
        assert caveat['band'] == (
            f'{residual_dex:+.8g} dex at {temperature_K:g} K'
        )
    finally:
        backend.close()


@pytest.mark.skipif(
    not thermoengine_package_available(),
    reason='ThermoEngine runtime is not installed',
)
def test_thermoengine_cao_activity_uses_the_printed_solid_reference(
    tmp_path,
    monkeypatch,
) -> None:
    from scripts.source_battery_report import ROOT, _isolated_source_root
    from simulator.battery.enums import Engine, NoticeKind, Quantity
    from simulator.battery.identity import quantity_token
    from simulator.battery.migrate import migrate
    from simulator.battery.records import StandardState
    from simulator.battery.score import (
        ScoreContext,
        score_store,
        temperature_of,
    )
    from simulator.diagnostic_helpers import binary_pot_battery

    monkeypatch.setenv('REGOLITH_ENGINE_CACHE', str(tmp_path / 'cache.sqlite'))
    monkeypatch.setenv('REGOLITH_ENGINE_CACHE_MODE', '')
    monkeypatch.setenv('REGOLITH_ENGINE_CACHE_BOX', 'rpp')
    source_id = 'stolyarova-1996-cao-alumina-silica-kems'
    migrated_root = tmp_path / 'migrated'
    _isolated_source_root(source_id, ROOT, migrated_root)
    migration = migrate(migrated_root, write=False)
    engine = Engine.THERMOENGINE
    observations = [
        row
        for row in migration.observations.values()
        if row.source_id == source_id
        and quantity_token(row.identity) is Quantity.ACTIVITY
        and row.identity.species is not None
        and row.identity.species.formula == 'CaO'
        and temperature_of(row.identity) == Decimal('1933')
        and row.identity.reference_state is not None
        and row.identity.reference_state.is_value
    ]
    assert observations
    for observation in observations:
        assert observation.identity.reference_state is not None
        assert observation.identity.reference_state.is_value
        printed_standard = observation.identity.reference_state.value
        assert isinstance(printed_standard, StandardState)
        assert printed_standard.endmember.phase.value.value == 'cr'

    context = ScoreContext(
        works=migration.works,
        experiments=migration.experiments,
        observations={row.observation_id: row for row in observations},
        benches=migration.benches,
        root=Path(migrated_root),
    )
    raw_cell_mu_by_pot: dict[str, float] = {}
    equilibrate_cell = binary_pot_battery.equilibrate_cell

    def capture_raw_cell_mu(handle, pot, **kwargs):
        cell = equilibrate_cell(handle, pot, **kwargs)
        if cell.mu_CaO_J_per_mol_melts_berman_apparent is not None:
            raw_cell_mu_by_pot[pot.pot_id] = (
                cell.mu_CaO_J_per_mol_melts_berman_apparent
            )
        return cell

    monkeypatch.setattr(
        binary_pot_battery, 'equilibrate_cell', capture_raw_cell_mu,
    )
    residuals, predictions = score_store(
        context,
        engines=(engine,),
        work_id=source_id,
    )
    numeric = [row for row in residuals if row.numeric is not None]
    assert numeric, [
        row.refusal.detail for row in residuals if row.refusal is not None
    ]
    residual = numeric[0]
    candidate = predictions.get(residual.candidate)
    assert candidate is not None and candidate.value.point is not None
    assert candidate.authority.value == 'bridge'
    raw_mu = raw_cell_mu_by_pot[f'obs:{residual.reference}']
    expected_activity, _ = cao_activity_from_melts_exchange_mu(
        raw_mu, 1933.0, phase='cr',
    )
    assert candidate.value.point == Decimal(str(expected_activity))
    model_flags = [
        notice for notice in candidate.notices
        if notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
        and (
            'CaSiO3(l)-pWo fusion residual' in notice.reason
            or 'Berman-vs-HHR CaSiO3(cr) reassessment' in notice.reason
        )
    ]
    assert len(model_flags) == (
        2 if engine is Engine.THERMOENGINE else 1
    )
    hhr_flag = next(
        notice for notice in model_flags
        if 'Berman-vs-HHR CaSiO3(cr) reassessment' in notice.reason
    )
    assert hhr_flag.affected_quantities == (Quantity.ACTIVITY,)
    assert hhr_flag.band is not None and 'dex at 1933 K' in hhr_flag.band
    assert 'cyclowollastonite' in hhr_flag.reason
    assert 'wollastonite' in hhr_flag.reason
    assert 'HHR does not extend to the row temperature' in hhr_flag.reason
    hhr_residual_dex = MELTS_CASIO3_HHR_SOLID_RESIDUAL_AT_1400_J_MOL / (
        GAS_CONSTANT * 1933.0 * math.log(10.0)
    )
    assert hhr_flag.band == (
        f'{hhr_residual_dex:+.8g} '
        'dex at 1933 K from the measured +3.151 kJ/mol residual at 1400 K'
    )
    fusion_flag = next(
        notice for notice in model_flags
        if 'CaSiO3(l)-pWo fusion residual' in notice.reason
    )
    assert fusion_flag.affected_quantities == (Quantity.ACTIVITY,)
    assert fusion_flag.band is not None and 'dex at 1933 K' in fusion_flag.band


@pytest.mark.skipif(
    not thermoengine_package_available(),
    reason='ThermoEngine runtime is not installed',
)
def test_thermoengine_ca_free_liquid_omits_fusion_disclosure() -> None:
    from simulator.melt_backend.thermoengine import ThermoEngineBackend

    backend = ThermoEngineBackend()
    if not backend.initialize({}):
        pytest.skip(backend.unavailable_reason())
    try:
        result = backend.equilibrate(
            temperature_C=1700.0 - CELSIUS_TO_KELVIN_OFFSET,
            composition_kg={'FeO': 40.0, 'SiO2': 60.0},
            fO2_log=-8.0,
            pressure_bar=1.0,
        )
        assert result.status == 'ok', result.warnings
        components = result.chem_potentials['Liquid']['components']
        assert 'CaSiO3' not in components
        raw_mu = result.diagnostics.get(
            MELTS_CAO_EXCHANGE_MU_FIELD
        )
        assert raw_mu is None
        notices = result.diagnostics.get('imcc_notices') or ()
        assert not any(
            'CaSiO3(l)-pWo fusion residual' in notice.get('reason', '')
            for notice in notices
        )
    finally:
        backend.close()


@pytest.mark.skipif(
    not thermoengine_package_available(),
    reason='ThermoEngine runtime is not installed',
)
@pytest.mark.parametrize(
    'fusion_failure',
    [
        pytest.param(
            PurePhaseAccessError('forced pWo fusion query failure'),
            id='pure-phase-access',
        ),
        pytest.param(
            EngineWorkerDied('call', 'forced pWo worker death'),
            id='worker-died',
        ),
    ],
)
def test_thermoengine_fusion_failure_is_disclosed_on_cao_activity(
    monkeypatch, fusion_failure,
) -> None:
    from simulator.battery.enums import Engine, Quantity
    from simulator.battery.score import _cell_notices_scoped_to_cao_activity
    from simulator.melt_backend.thermoengine import ThermoEngineBackend

    temperature_K = 1933.0
    backend = ThermoEngineBackend()
    if not backend.initialize({}):
        pytest.skip(backend.unavailable_reason())
    try:
        transport_equilibrate = backend.transport.equilibrate
        cached_payload = []

        def equilibrate_once(**kwargs):
            if not cached_payload:
                cached_payload.append(transport_equilibrate(**kwargs))
            return cached_payload[0]

        monkeypatch.setattr(
            backend.transport, 'equilibrate', equilibrate_once,
        )

        def fail_pwo_fusion_query(symbol, *, temperature_K, pressure_bar):
            if symbol == 'pWo':
                raise fusion_failure
            return backend.transport.pure_phase_properties(
                symbol,
                temperature_K=temperature_K,
                pressure_bar=pressure_bar,
            )

        monkeypatch.setattr(
            backend, 'pure_phase_properties', fail_pwo_fusion_query,
        )
        result = backend.equilibrate(
            temperature_C=temperature_K - CELSIUS_TO_KELVIN_OFFSET,
            composition_kg={'CaO': 56.077, 'SiO2': 60.0843},
            fO2_log=-10.0,
            pressure_bar=1.0,
        )
        assert result.status == 'ok', result.warnings
        assert 'CaSiO3' in result.chem_potentials['Liquid']['components']
        raw_mu = result.diagnostics['mu_CaO_J_per_mol_melts_berman_apparent']
        activity, _ = cao_activity_from_melts_exchange_mu(
            float(raw_mu), temperature_K, phase='cr',
        )
        assert math.isfinite(activity) and activity > 0.0

        cell = SimpleNamespace(notices=result.diagnostics['imcc_notices'])
        activity_notices = _cell_notices_scoped_to_cao_activity(
            Quantity.ACTIVITY, Engine.THERMOENGINE, cell, 'CaO',
        )
        gap = next(
            notice for notice in activity_notices
            if 'CaSiO3(l)-pWo fusion residual band could not be computed'
            in notice.reason
        )
        assert gap.affected_quantities == (Quantity.ACTIVITY,)
        assert gap.band is None
        assert (
            f'{type(fusion_failure).__name__}: {fusion_failure}'
            in gap.reason
        )

        def fail_with_name_error(symbol, *, temperature_K, pressure_bar):
            raise NameError('forced fusion implementation error')

        monkeypatch.setattr(
            backend, 'pure_phase_properties', fail_with_name_error,
        )
        with pytest.raises(
            RuntimeError,
            # The outer equilibrium handler re-raises as RuntimeError with
            # 'ThermoEngine equilibrium failed: {exc}' (no class prefix); the
            # original NameError is asserted via __cause__ below.
            match='ThermoEngine equilibrium failed: forced fusion implementation error',
        ) as error:
            backend.equilibrate(
                temperature_C=temperature_K - CELSIUS_TO_KELVIN_OFFSET,
                composition_kg={'CaO': 56.077, 'SiO2': 60.0843},
                fO2_log=-10.0,
                pressure_bar=1.0,
            )
        assert isinstance(error.value.__cause__, NameError)
    finally:
        backend.close()


@pytest.mark.parametrize(
    ('engine_name', 'cell_mode'),
    (
        ('alphamelts', 'ca-free'),
        ('alphamelts', 'ca-bearing-gap'),
        ('alphamelts', 'ca-bearing-band'),
        ('thermoengine', 'ca-free'),
    ),
)
def test_alphamelts_cao_scoring_uses_raw_cell_exchange_and_scopes_model_caveats(
    tmp_path,
    monkeypatch,
    engine_name,
    cell_mode,
) -> None:
    from scripts.source_battery_report import ROOT, _isolated_source_root
    from simulator.battery.enums import (
        Engine,
        NoticeKind,
        Quantity,
        RefusalReason,
    )
    from simulator.battery.identity import quantity_token
    from simulator.battery.migrate import migrate
    from simulator.battery.score import ScoreContext, score_store, temperature_of
    from simulator.diagnostic_helpers import binary_pot_battery
    from simulator.diagnostic_helpers.binary_pot_battery import (
        EquilibrateCell,
        Po2Request,
    )

    source_id = 'stolyarova-1996-cao-alumina-silica-kems'
    migrated_root = tmp_path / 'migrated'
    _isolated_source_root(source_id, ROOT, migrated_root)
    migration = migrate(migrated_root, write=False)
    activity_rows = [
        row
        for row in migration.observations.values()
        if row.source_id == source_id
        and quantity_token(row.identity) is Quantity.ACTIVITY
        and row.identity.species is not None
        and temperature_of(row.identity) == Decimal('1933')
    ]
    cao = next(row for row in activity_rows if row.identity.species.formula == 'CaO')
    silica = next(
        row for row in activity_rows
        if row.identity.species.formula == 'SiO2'
    )
    temperature_K = 1933.0
    engine = Engine(engine_name)
    g0_solid_kJ_mol, _ = melts_reference_g0_cao_kJ_mol(
        temperature_K, 'cr',
    )
    raw_mu = (
        g0_solid_kJ_mol * 1000.0
        + GAS_CONSTANT * temperature_K * math.log(0.4)
    )
    ca_free = cell_mode == 'ca-free'
    g0_casio3_liquid = -900000.0
    g0_pwo = -800000.0
    cell = EquilibrateCell(
        pot_id='same-cell',
        engine=engine.value,
        temperature_K=temperature_K,
        po2=Po2Request(mode='engine_default', po2_bar=None),
        status='ok',
        refusal_reason=None,
        engine_status='ok',
        engine_reason=None,
        melt_activities={'CaSiO3': 0.5, 'SiO2': 0.25},
        gas_partial_pressures_Pa={},
        liquid_fraction=1.0,
        wall_s=0.0,
        cpu_s=0.0,
        hostname='test',
        liquid_composition_wt_pct=(
            {'CaO': 0.0, 'SiO2': 100.0}
            if ca_free
            else {'CaO': 48.0, 'SiO2': 52.0}
        ),
        mu_CaO_J_per_mol_melts_berman_apparent=(
            None if ca_free else raw_mu
        ),
        g0_CaSiO3_liquid_J_per_mol_melts_berman_apparent=(
            g0_casio3_liquid if cell_mode == 'ca-bearing-band' else None
        ),
        g0_pWo_J_per_mol_melts_berman_apparent=(
            g0_pwo if cell_mode == 'ca-bearing-band' else None
        ),
    )
    handle = SimpleNamespace(
        name=engine.value,
        available=True,
        unavailable_reason='',
        backend=None,
    )
    from simulator.battery import score as score_module

    monkeypatch.setattr(
        score_module, '_open_cached_battery_handle',
        lambda *_args, **_kwargs: (handle, False),
    )
    monkeypatch.setattr(
        binary_pot_battery, 'equilibrate_cell',
        lambda *_args, **_kwargs: cell,
    )
    context = ScoreContext(
        works=migration.works,
        experiments=migration.experiments,
        observations={cao.observation_id: cao, silica.observation_id: silica},
        benches=migration.benches,
        root=Path(migrated_root),
    )
    _residuals, predictions = score_store(
        context,
        engines=(engine,),
        work_id=source_id,
    )
    cao_candidate = next(
        candidate for candidate in predictions.values()
        if candidate.identity.species.formula == 'CaO'
    )
    cao_refusal = next(
        residual for residual in _residuals
        if residual.candidate == cao_candidate.observation_id
    )
    non_cao_candidate = next(
        candidate for candidate in predictions.values()
        if candidate.identity.species.formula == 'SiO2'
    )
    fusion_markers = (
        'CaSiO3(l)-pWo fusion residual',
        'Berman-vs-HHR CaSiO3(cr) reassessment',
        'AlphaMELTS CaSiO3 fusion residual band is not computed',
    )
    if ca_free:
        assert cao_candidate.value.point is None
        assert cao_candidate.value.unavailable_reason == (
            'species_unmatched_in_engine_output'
        )
        assert cao_refusal.refusal is not None
        assert cao_refusal.refusal.reason is RefusalReason.UNSUPPORTED
        assert cao_refusal.refusal.detail['reason'] == (
            'species_unmatched_in_engine_output'
        )
        assert cao_refusal.refusal.detail['converter'].startswith(
            'typed-refusal:melts_endmember_not_parent_oxide'
        )
        assert not any(
            marker in notice.reason
            for notice in cao_candidate.notices
            for marker in fusion_markers
        )
    else:
        expected, _ = cao_activity_from_melts_exchange_mu(
            raw_mu, temperature_K, phase='cr',
        )
        assert cao_candidate.value.point == Decimal(str(expected))
        hhr_flag = next(
            notice for notice in cao_candidate.notices
            if notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
            and 'Berman-vs-HHR CaSiO3(cr) reassessment' in notice.reason
        )
        assert hhr_flag.affected_quantities == (Quantity.ACTIVITY,)
        assert hhr_flag.band is not None and 'dex at 1933 K' in hhr_flag.band
        if cell_mode == 'ca-bearing-band':
            fusion_flag = next(
                notice for notice in cao_candidate.notices
                if notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
                and 'CaSiO3(l)-pWo fusion residual' in notice.reason
            )
            expected_fusion_band = casio3_fusion_model_systematic_dex(
                g0_casio3_liquid - g0_pwo,
                temperature_K,
            )
            assert fusion_flag.affected_quantities == (Quantity.ACTIVITY,)
            assert fusion_flag.band == (
                f'{expected_fusion_band:+.8g} dex at {temperature_K:g} K'
            )
            assert not any(
                marker in notice.reason
                for notice in cao_candidate.notices
                for marker in fusion_markers[2:]
            )
        else:
            fusion_gap = next(
                notice for notice in cao_candidate.notices
                if notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
                and fusion_markers[2] in notice.reason
            )
            assert fusion_gap.affected_quantities == (Quantity.ACTIVITY,)
            assert fusion_gap.band is None
            assert 'liquid/pWo pure-phase G0' in fusion_gap.reason
            assert 'shared MELTS database' in fusion_gap.reason
            assert 'ThermoEngine CaO fusion band at the same T is the reference' in (
                fusion_gap.reason
            )
    assert not any(
        notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
        and any(marker in notice.reason for marker in fusion_markers)
        for notice in non_cao_candidate.notices
    )


def test_melts_cao_cell_model_caveats_do_not_leak_to_other_activity_species() -> None:
    from simulator.battery.enums import Engine, NoticeKind, Quantity
    from simulator.battery.score import _cell_notices_scoped_to_cao_activity

    cell = SimpleNamespace(notices=[
        {
            'kind': 'engine_model_caveat',
            'reason': 'ThermoEngine CaSiO3(l)-pWo fusion residual',
            'band': '-0.006 dex at 1933 K',
        },
        {
            'kind': 'engine_model_caveat',
            'reason': 'Berman-vs-HHR CaSiO3(cr) reassessment',
            'band': '+0.085 dex at 1933 K',
        },
    ])
    cao_notices = _cell_notices_scoped_to_cao_activity(
        Quantity.ACTIVITY, Engine.THERMOENGINE, cell, 'CaO',
    )
    silica_notices = _cell_notices_scoped_to_cao_activity(
        Quantity.ACTIVITY, Engine.THERMOENGINE, cell, 'SiO2',
    )
    non_activity_notices = _cell_notices_scoped_to_cao_activity(
        Quantity.P_SAT, Engine.THERMOENGINE, cell, 'CaO',
    )
    assert len(cao_notices) == 2
    assert all(
        notice.affected_quantities == (Quantity.ACTIVITY,)
        and notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
        for notice in cao_notices
    )
    assert silica_notices == ()
    assert non_activity_notices == ()
