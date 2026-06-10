import math

import pytest

from simulator.melt_backend.base import (
    EquilibriumResult,
    LiquidFractionInvalidError,
    MeltCompositionError,
    StubBackend,
)
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.melt_backend.magemin import MAGEMinBackend


@pytest.mark.parametrize('liquid_fraction', [math.nan, 1.5])
def test_ok_equilibrium_result_requires_finite_unit_liquid_fraction(
    liquid_fraction,
):
    with pytest.raises(LiquidFractionInvalidError):
        EquilibriumResult(status='ok', liquid_fraction=liquid_fraction)


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
