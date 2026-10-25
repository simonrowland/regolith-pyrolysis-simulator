"""Signed oxygen closure when the physical excess is smaller than bulk precision."""
import math

import pytest

from simulator.chemistry.langmuir_knudsen import (
    _SurfaceOxygenEvaluation, _solve_surface_oxygen_balance,
)
from simulator.transport_regime import SurfaceFlow, series_evaporation_flux


def oxygen_transport(bulk, delta, *, film=False, stagnant=False):
    return series_evaporation_flux(
        'O2', (bulk + delta) * 1e5, bulk * 1e5, 1253.15, 0.031998, 1.0,
        pressure_excess_pa=delta * 1e5, signed=True,
        gas_resistance_enabled=film, overhead_pressure_pa=max(900.0, bulk * 1e5),
        melt_surface_area_m2=0.2,
        surface_flow=(SurfaceFlow('stagnant_disk', 0.0, 'test', 'pool', 'stagnant')
                      if stagnant else None),
        stir_diagnostic=dict(axial_stir_applied=0.0, radial_stir_applied=0.0,
            axial_stir_clamped=False, radial_stir_clamped=False,
            frozen_skull_stir_clamped=False, frozen_skull_stir_ceiling=1.0),
    )


def close_source(bulk, sources, *, film=False, stagnant=False):
    def evaluate(delta):
        transport = oxygen_transport(bulk, delta, film=film, stagnant=stagnant)
        return _SurfaceOxygenEvaluation(
            bulk + delta, bulk + transport.flux_kg_s_m2 * transport.r_gas / 1e5,
            transport.flux_kg_s_m2 / 0.031998, 0.0, sources, 0.0, 0.0, 0.0, math.fsum(sources.values()),
            payload=delta, bulk_pO2_bar=bulk,
        )
    return _solve_surface_oxygen_balance(evaluate, bulk_pO2_bar=bulk)


def test_captured_lunar_hour_19_retains_phosphorus_oxygen_source():
    # Captured before this fix: lunar C0, next step after hour 19, 980 C,
    # total/bulk oxygen 0.009 bar. Independent finite-settlement receipt;
    # all atomic, ferric, other and storage terms were individually zero.
    sources = {'PO': 1.820443327240158e-19, 'PO2': 2.6357184158084103e-14}
    solved = close_source(0.009, sources, film=True)
    assert solved.source_terms_mol_m2_s == sources
    assert solved.payload > 0.0
    assert solved.oxygen_flux_mol_m2_s == pytest.approx(math.fsum(sources.values()), rel=1e-8, abs=0.0)
    assert abs(solved.residual) <= math.sqrt(math.ulp(1.0)) * math.fsum(map(abs, solved.balance_terms))


@pytest.mark.parametrize('film,stagnant', ((False, False), (True, False), (True, True)))
@pytest.mark.parametrize('bulk', (1e-27, 1e-24, 1e-18, 1e-12, 1e-6, 0.009, 0.1, 1.0))
def test_signed_closure_property_across_bulk_and_source_scales(bulk, film, stagnant):
    # Fixed-source balances independently prescribe the exact signed flux.
    # Include a realizable consuming source, selected from the actual owner's
    # flux at a positive surface, rather than inventing a pressure floor.
    sink = oxygen_transport(bulk, -0.25 * bulk, film=film, stagnant=stagnant).flux_kg_s_m2 / 0.031998
    for source in (0.0, 1e-40, 1e-30, 2.6e-14, 1e-3, 1e3, sink):
        solved = close_source(bulk, {'source': source}, film=film, stagnant=stagnant)
        assert solved.reaction_pO2_bar >= 0.0
        if source == 0.0:
            assert solved.payload == solved.oxygen_flux_mol_m2_s == solved.residual == 0.0
            assert solved.reaction_pO2_bar == bulk
        else:
            assert math.copysign(1.0, solved.payload) == math.copysign(1.0, source)
            assert solved.oxygen_flux_mol_m2_s == pytest.approx(source, rel=1e-8, abs=0.0)
            assert abs(solved.residual) <= math.sqrt(math.ulp(1.0)) * math.fsum(map(abs, solved.balance_terms))
