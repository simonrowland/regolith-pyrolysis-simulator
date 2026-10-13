import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator.melt_backend.base import (
    EquilibriumResult,
    LiquidFractionInvalidError,
    MeltCompositionError,
    InternalAnalyticalBackend,
    projection_diagnostics_for_melt_input,
    project_melt_to_oxide_projection,
)
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.melt_backend.magemin import MAGEMinBackend
from simulator.core import PyrolysisSimulator


@pytest.mark.parametrize('liquid_fraction', [math.nan, 1.5])
def test_ok_equilibrium_result_requires_finite_unit_liquid_fraction(
    liquid_fraction,
):
    with pytest.raises(LiquidFractionInvalidError):
        EquilibriumResult(status='ok', liquid_fraction=liquid_fraction)


@pytest.mark.parametrize(
    ('liquid_fraction', 'message'),
    [
        (None, 'liquid_fraction_missing'),
        (math.nan, 'liquid_fraction_invalid: nan'),
        (math.inf, 'liquid_fraction_invalid: inf'),
        (-0.1, 'liquid_fraction_invalid: -0.1'),
        (0.0, None),
        (0.5, None),
        (1.0, None),
        (1.2, 'liquid_fraction_invalid: 1.2'),
    ],
)
def test_equilibrium_result_liquid_fraction_validation_contract(
    liquid_fraction, message
):
    constructor = lambda: EquilibriumResult(
        status='ok', liquid_fraction=liquid_fraction
    )

    if message is None:
        result = constructor()
        assert result.liquid_fraction == liquid_fraction
    else:
        with pytest.raises(LiquidFractionInvalidError) as exc_info:
            constructor()
        assert str(exc_info.value) == message


@pytest.mark.parametrize(
    ('liquid_fraction', 'message'),
    [
        (None, None),
        (math.nan, 'Authoritative VAPOR_PRESSURE liquid_fraction_invalid: nan'),
        (math.inf, 'Authoritative VAPOR_PRESSURE liquid_fraction_invalid: inf'),
        (-0.1, 'Authoritative VAPOR_PRESSURE liquid_fraction_invalid: -0.1'),
        (0.0, None),
        (0.5, None),
        (1.0, None),
        (1.2, 'Authoritative VAPOR_PRESSURE liquid_fraction_invalid: 1.2'),
    ],
)
def test_authoritative_vapor_pressure_liquid_fraction_validation_contract(
    monkeypatch, liquid_fraction, message
):
    class ReachedVaporPressureDispatch(Exception):
        pass

    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = SimpleNamespace(temperature_C=1000.0)
    sim._last_high_t_melt_activity = {}
    result = SimpleNamespace(
        liquid_fraction=liquid_fraction,
        vapor_pressures_Pa={},
        vapor_pressures_source={},
    )

    def stop_after_validation():
        raise ReachedVaporPressureDispatch

    monkeypatch.setattr(
        sim, '_vapor_pressure_dispatch_pO2_bar', stop_after_validation
    )
    if message is None:
        if liquid_fraction == 0.0:
            sim._refresh_vapor_pressures_from_kernel(result)
            return
        with pytest.raises(ReachedVaporPressureDispatch):
            sim._refresh_vapor_pressures_from_kernel(result)
    else:
        with pytest.raises(RuntimeError) as exc_info:
            sim._refresh_vapor_pressures_from_kernel(result)
        assert type(exc_info.value) is RuntimeError
        assert str(exc_info.value) == message



def test_equilibrium_result_default_viscosity_is_unknown():
    result = EquilibriumResult(status='ok', liquid_fraction=1.0)

    assert result.liquid_viscosity_Pa_s is None


def test_internal_analytical_backend_reports_unavailable_with_no_liquid_fraction():
    result = InternalAnalyticalBackend().equilibrate(temperature_C=1500.0)

    assert result.status != 'ok'
    assert result.liquid_fraction is None


def test_vapor_only_ok_permits_missing_liquid_fraction():
    result = EquilibriumResult(
        status='ok',
        liquid_fraction=None,
        phase_assemblage_available=False,
    )

    assert result.liquid_fraction is None
    assert result.phase_assemblage_available is False


def test_projection_diagnostics_are_byte_identical_for_identical_inputs():
    projection = project_melt_to_oxide_projection(
        composition_kg={'SiO2': 0.6, 'MgO': 0.4, 'NaCl': 0.05},
        composition_mol=None,
        oxide_basis=('SiO2', 'MgO'),
    )
    kwargs = {
        'backend': 'shared',
        'projection': projection,
        'composition_kg': {'SiO2': 0.6, 'MgO': 0.4, 'NaCl': 0.05},
        'composition_mol': None,
        'oxide_basis': ('SiO2', 'MgO'),
        'species_formula_registry': None,
        'dropped_accounts': ['process.metal_alloy'],
        'dropped_account_species': {'process.metal_alloy': ('Fe',)},
    }

    first = projection_diagnostics_for_melt_input(**kwargs)
    second = projection_diagnostics_for_melt_input(**kwargs)

    assert json.dumps(first, sort_keys=True) == json.dumps(
        second,
        sort_keys=True,
    )
    projection_details = first['input_composition_projection']
    assert projection_details['status'] == 'projected'
    assert projection_details['backend'] == 'shared'
    assert projection_details['dropped_species'] == ['NaCl']
    assert projection_details['dropped_accounts'] == ['process.metal_alloy']
    assert projection_details['dropped_account_species'] == {
        'process.metal_alloy': ['Fe'],
    }
    assert projection_details['renormalization_delta'] == pytest.approx(0.05)


def test_projection_diagnostics_helper_is_not_redeclared_in_backends():
    root = Path(__file__).resolve().parents[1]
    for relative in (
        'simulator/melt_backend/magemin.py',
        'simulator/melt_backend/vaporock.py',
    ):
        source = (root / relative).read_text()
        assert 'def _projection_diagnostics' not in source
        assert 'projection_diagnostics_for_melt_input' in source


def test_alphamelts_out_of_domain_result_carries_crash_point_inputs():
    # 2026-09-13 ruling, applied by 997b9d685: only malformed input is vetoed;
    # a band-only miss proceeds to the uninitialized transport's unavailable result.
    backend = AlphaMELTSBackend()

    missing_silica = backend.equilibrate(
        temperature_C=1325.0,
        fO2_log=-8.5,
        pressure_bar=1.0e-6,
        composition_mol_by_account={
            'process.cleaned_melt': {'SiO2': 0.0, 'MgO': 1.0},
        },
    )

    assert missing_silica.status == 'out_of_domain'
    crash_point = missing_silica.diagnostics['out_of_domain_crash_point']
    assert crash_point['temperature_C'] == pytest.approx(1325.0)
    assert crash_point['pressure_bar'] == pytest.approx(1.0e-6)
    assert crash_point['fO2_log'] == pytest.approx(-8.5)
    assert crash_point['composition_mol']['MgO'] == pytest.approx(1.0)
    assert crash_point['composition_wt_pct']['MgO'] == pytest.approx(100.0)

    band_only = AlphaMELTSBackend().equilibrate(
        temperature_C=1325.0,
        fO2_log=-8.5,
        pressure_bar=1.0e-6,
        composition_mol_by_account={
            'process.cleaned_melt': {'SiO2': 1.0},
        },
    )
    from engines.alphamelts.domain import (
        CONSTRAINT_SILICATE_NETWORK_BAND,
        AlphaMELTSDomainGate,
    )

    assessment = AlphaMELTSDomainGate.assess({"SiO2": 100.0})
    assert assessment.valid is True
    assert CONSTRAINT_SILICATE_NETWORK_BAND in assessment.failed_constraints
    assert band_only.status == 'unavailable'
    assert 'out_of_domain_crash_point' not in band_only.diagnostics
    assert any('certified band' in warning for warning in band_only.warnings)


def test_melt_solving_ok_raises_without_liquid_fraction():
    with pytest.raises(LiquidFractionInvalidError):
        EquilibriumResult(status='ok', liquid_fraction=None)


def test_magemin_zero_total_phase_mass_raises():
    backend = MAGEMinBackend()
    result = EquilibriumResult(status='ok', liquid_fraction=0.0)

    with pytest.raises(MeltCompositionError, match='zero_total_phase_mass'):
        backend._populate_result(
            result,
            {'phases': {'liquid': {'mass_kg': 0.0}}},
        )
