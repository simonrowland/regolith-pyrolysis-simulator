import math

import pytest

from simulator.melt_backend.base import (
    EquilibriumResult,
    LiquidFractionInvalidError,
    MeltCompositionError,
    StubBackend,
)
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
