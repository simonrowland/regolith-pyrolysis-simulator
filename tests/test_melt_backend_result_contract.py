import json
import math
from pathlib import Path

import pytest

from simulator.melt_backend.base import (
    EquilibriumResult,
    LiquidFractionInvalidError,
    MeltCompositionError,
    StubBackend,
    projection_diagnostics_for_melt_input,
    project_melt_to_oxide_projection,
)
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.melt_backend.magemin import MAGEMinBackend


@pytest.mark.parametrize('liquid_fraction', [math.nan, 1.5])
def test_ok_equilibrium_result_requires_finite_unit_liquid_fraction(
    liquid_fraction,
):
    with pytest.raises(LiquidFractionInvalidError):
        EquilibriumResult(status='ok', liquid_fraction=liquid_fraction)



def test_equilibrium_result_default_viscosity_is_unknown():
    result = EquilibriumResult(status='ok', liquid_fraction=1.0)

    assert result.liquid_viscosity_Pa_s is None


def test_stub_backend_reports_unavailable_with_no_liquid_fraction():
    result = StubBackend().equilibrate(temperature_C=1500.0)

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
    backend = AlphaMELTSBackend()

    result = backend.equilibrate(
        temperature_C=1325.0,
        fO2_log=-8.5,
        pressure_bar=1.0e-6,
        composition_mol_by_account={
            'process.cleaned_melt': {'SiO2': 1.0},
        },
    )

    assert result.status == 'out_of_domain'
    crash_point = result.diagnostics['out_of_domain_crash_point']
    assert crash_point['temperature_C'] == pytest.approx(1325.0)
    assert crash_point['pressure_bar'] == pytest.approx(1.0e-6)
    assert crash_point['fO2_log'] == pytest.approx(-8.5)
    assert crash_point['composition_mol']['SiO2'] == pytest.approx(1.0)
    assert crash_point['composition_wt_pct']['SiO2'] == pytest.approx(100.0)


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
