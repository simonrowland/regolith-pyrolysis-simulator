from __future__ import annotations

import math
from decimal import Decimal

import pytest

from simulator.diagnostic_helpers.binary_pot_battery import (
    EquilibrateCell,
    Po2Request,
)
from simulator.melt_backend.pure_phase_janaf_score import (
    ELEMENT_REFERENCE_TABLES,
    cao_activity_from_melts_exchange_mu,
    element_reference_g_kJ_mol,
    formation_g_from_apparent_kJ_mol,
    janaf_values_at,
    load_janaf_table,
    melts_reference_g0_cao_kJ_mol,
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
    )

    restored = EquilibrateCell.from_engine_cache_value(
        cell.to_engine_cache_value()
    )
    assert restored.mu_CaO_J_per_mol_melts_berman_apparent == 12345.0
    payload = EquilibrateCell.from_cache_payload(cell.to_cache_payload())
    assert payload.mu_CaO_J_per_mol_melts_berman_apparent == 12345.0


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


@pytest.mark.skipif(
    not thermoengine_package_available(),
    reason='ThermoEngine runtime is not installed',
)
def test_thermoengine_cao_activity_uses_the_printed_solid_reference(
    tmp_path,
    monkeypatch,
) -> None:
    from pathlib import Path

    from scripts.source_battery_report import ROOT, _isolated_source_root
    from simulator.battery.enums import Engine, Quantity
    from simulator.battery.identity import quantity_token
    from simulator.battery.migrate import migrate
    from simulator.battery.records import StandardState
    from simulator.battery.score import (
        ScoreContext,
        score_store,
        temperature_of,
    )

    monkeypatch.setenv('REGOLITH_ENGINE_CACHE', str(tmp_path / 'cache.sqlite'))
    monkeypatch.setenv('REGOLITH_ENGINE_CACHE_MODE', '')
    monkeypatch.setenv('REGOLITH_ENGINE_CACHE_BOX', 'rpp')
    source_id = 'stolyarova-1996-cao-alumina-silica-kems'
    migrated_root = tmp_path / 'migrated'
    _isolated_source_root(source_id, ROOT, migrated_root)
    migration = migrate(migrated_root, write=False)
    observation = next(
        row
        for row in migration.observations.values()
        if row.source_id == source_id
        and quantity_token(row.identity) is Quantity.ACTIVITY
        and row.identity.species is not None
        and row.identity.species.formula == 'CaO'
        and temperature_of(row.identity) == Decimal('1933')
        and 'table3_cao_activity_column_1' in row.observation_id
    )
    assert observation.identity.reference_state is not None
    assert observation.identity.reference_state.is_value
    printed_standard = observation.identity.reference_state.value
    assert isinstance(printed_standard, StandardState)
    assert printed_standard.endmember.phase.value.value == 'cr'

    context = ScoreContext(
        works=migration.works,
        experiments=migration.experiments,
        observations={observation.observation_id: observation},
        benches=migration.benches,
        root=Path(migrated_root),
    )
    residuals, predictions = score_store(
        context,
        engines=(Engine.THERMOENGINE,),
        work_id=source_id,
    )
    assert len(residuals) == 1
    residual = residuals[0]
    if residual.numeric is not None:
        candidate = predictions.get(residual.candidate)
        assert candidate is not None
        assert candidate.authority.value == 'bridge'
    else:
        assert residual.refusal is not None
        assert residual.refusal.detail.get('reason') != 'reference_state_mismatch'


@pytest.mark.skipif(
    not thermoengine_package_available(),
    reason='ThermoEngine runtime is not installed',
)
def test_pure_casio3_liquid_g0_anchors_to_janaf_wollastonite_formation() -> None:
    from simulator.melt_backend.thermoengine import ThermoEngineBackend

    temperature_K = 2000.0
    backend = ThermoEngineBackend()
    if not backend.initialize({}):
        pytest.skip(backend.unavailable_reason())
    try:
        result = backend.equilibrate(
            temperature_C=temperature_K - CELSIUS_TO_KELVIN_OFFSET,
            composition_kg={'CaO': 56.077, 'SiO2': 60.0843},
            fO2_log=None,
            pressure_bar=1.0,
        )
        assert result.status == 'ok', result.warnings
        components = result.chem_potentials['Liquid']['components']
        activity = float(result.activity_coefficients['CaSiO3'])
        assert activity > 0.0
        # The near-pure row's chemical potential is G0 + RT ln(a); remove
        # that small nonideal correction to compare the MELTS liquid G0.
        melts_g0_J_mol = (
            float(components['CaSiO3'])
            - GAS_CONSTANT * temperature_K * math.log(activity)
        )

        cao_solid = janaf_values_at(load_janaf_table('Ca-027'), temperature_K)
        silica_liquid = janaf_values_at(load_janaf_table('O-038'), temperature_K)
        assert cao_solid is not None and silica_liquid is not None
        assert cao_solid.formation_gibbs_kJ_mol is not None
        assert silica_liquid.formation_gibbs_kJ_mol is not None

        element_g: dict[str, float] = {}
        for element in ('Ca', 'Si', 'O2'):
            values = janaf_values_at(
                load_janaf_table(ELEMENT_REFERENCE_TABLES[element]),
                temperature_K,
            )
            assert values is not None
            assert values.enthalpy_increment_kJ_mol is not None
            assert values.S_J_K_mol is not None
            element_g[element] = element_reference_g_kJ_mol(
                values.enthalpy_increment_kJ_mol,
                values.S_J_K_mol,
                temperature_K,
            )

        # Shornikov 2024, Eq. (10), published p. 573: liquid wollastonite
        # formation from simple oxides has ΔH=-11.29 kJ/mol and
        # ΔS=17.53 J/(mol K). At 2000 K that ΔG is -46.35 kJ/mol. Use
        # crystalline CaO because 2000 K is below JANAF's 3200 K crossing.
        formation_from_oxides_kJ_mol = -11.29 - temperature_K * 17.53 / 1000.0
        delta_f_g_kJ_mol = (
            cao_solid.formation_gibbs_kJ_mol
            + silica_liquid.formation_gibbs_kJ_mol
            + formation_from_oxides_kJ_mol
        )
        # JANAF ΔfG is referenced to elements at T. Restore the Ca + Si +
        # 3/2 O2 reference terms to compare with MELTS's apparent Berman G0.
        janaf_apparent_g0_kJ_mol = (
            delta_f_g_kJ_mol
            + element_g['Ca']
            + element_g['Si']
            + 1.5 * element_g['O2']
        )
        # The independent Shornikov formation fit and Berman melt model use
        # different assessments; require agreement within 50 kJ/mol here.
        assert melts_g0_J_mol / 1000.0 == pytest.approx(
            janaf_apparent_g0_kJ_mol,
            abs=50.0,
        )
    finally:
        backend.close()
