"""C11: source receipts, independent disk/plate anchors and hardware causality."""
from dataclasses import replace
import json
import math
from pathlib import Path

import pytest

from engines.builtin.evaporation_flux import _series_resistance_evaporation_flux_kg_m2_s as source
from simulator.transport_regime import (
    EvaporationFluxConfigurationError, SurfaceFlow, carrier_dynamic_viscosity_Pa_s,
    surface_gas_film,
)
from tests.chemistry.test_builtin_evaporation_flux_provider import _w3_result_with_controls

PINS = json.loads((Path(__file__).parents[1] / 'fixtures/c11_legacy_series_pins.json').read_text())
# Actual archived designer area, not the historical 0.08/0.16 m film operands.
AREA = 0.41006003303200633
DIAMETER = 0.7225682229915182


def flow(u, geometry='planar'):
    return SurfaceFlow(geometry, u, 'C11 declared study hardware',
                       'pool free stream' if geometry == 'planar' else 'nozzle exit',
                       'uniform_free_stream', length_m=DIAMETER if geometry == 'planar' else DIAMETER/10,
                       width_m=AREA/DIAMETER, standoff_m=6*DIAMETER/10,
                       footprint_radius_m=DIAMETER/2, slot_width_m=DIAMETER/20,
                       averaging_half_span_m=DIAMETER/2)


def sample(**overrides):
    kwargs=dict(PINS[-1]['inputs'], melt_surface_area_m2=AREA)
    kwargs.update(overrides)
    return source(**kwargs)


@pytest.mark.parametrize('pin', PINS, ids=lambda p:f"{p['feedstock']}-{p['hour']}-{p['inputs']['species']}")
def test_source_preserves_archived_alpha_hk_diffusion_and_length_operands(pin):
    result=source(**pin['inputs'], melt_surface_area_m2=AREA)
    for key in ('alpha_intrinsic','k_hk_kg_s_m2_pa','r_interface'):
        assert getattr(result,key).hex()==pin['hex'][key]
    if pin['archived_flux'] > 0:
        assert result.d_ab_m2_s.hex()==pin['hex']['d_ab_m2_s']
    assert result.r_melt==0
    assert result.source_film_length_m==pin['inputs']['pipe_diameter_m']
    assert result.source_film_length_m in (0.08,0.16)


def test_disk_diffusion_anchor_and_stefan_series_reconstruction():
    result=sample(P_eq_pa=100, P_bulk_pa=0, overhead_pressure_pa=1000)
    d=result.as_diagnostic();f=d['surface_film']
    # Exact isolated circular disk diffusion result, Sh_d=8/pi.
    assert f['Sh']==pytest.approx(2.5464790894703255)
    assert f['length_m']==pytest.approx(0.7225682229915182, rel=1e-6)
    assert 'surface flow undeclared: still-gas floor' in f['domain_flags']
    assert f['stefan_model']=='binary_stagnant_carrier_logarithmic'
    assert f['stefan_conductance_multiplier'] > 1
    assert result.flux_kg_s_m2==pytest.approx(100/(result.r_interface+result.r_gas),rel=1e-12)
    assert sum(d[k] for k in ('R_interface_fraction','R_gas_fraction','R_melt_fraction'))==pytest.approx(1)
    assert result.alpha_effective<=result.alpha_intrinsic


def test_published_area_mean_plate_anchor_and_dimensional_groups():
    # Classical plate example Re_L=10,000, Sc=1 gives Sh_L=66.4.
    from simulator.physical_constants import GAS_CONSTANT
    from simulator.transport_constants import CARRIER_GAS_PROPERTIES
    mu=carrier_dynamic_viscosity_Pa_s('Ar',1500)
    rho=1000*CARRIER_GAS_PROPERTIES['Ar'].molar_mass_kg_mol/(GAS_CONSTANT*1500)
    hardware=replace(flow(1), length_m=0.5, width_m=2*AREA, velocity_m_s=20000*mu/rho)
    result=surface_gas_film(area_m2=AREA,flow=hardware,temperature_K=1500,
                            pressure_pa=1000,carrier='Ar',diffusivity_m2_s=mu/rho)
    assert result['Re']==pytest.approx(10000)
    assert result['Sc']==pytest.approx(1)
    assert result['Sh']==pytest.approx(66.4)
    assert result['Mach']>0.3
    assert any('Mach' in flag for flag in result['domain_flags'])


@pytest.mark.parametrize('geometry',['planar','round','slot'])
def test_declared_flow_is_independent_of_melt_stir_and_flags_actual_domain(geometry):
    results=[sample(surface_flow=flow(u,geometry)) for u in (1,3,10)]
    assert [r.flux_kg_s_m2 for r in results]==sorted(r.flux_kg_s_m2 for r in results)
    for r in results:
        f=r.as_diagnostic()['surface_film']
        assert f['Re']>0 and f['Sc']>0 and f['Sh']>0 and f['Kn_film']>0
        assert f['domain_flags']  # study's low-Re extrapolations, never certified.
    assert sample(surface_flow=flow(3,geometry),radial_stir_factor=10).flux_kg_s_m2==results[1].flux_kg_s_m2


def test_missing_area_or_hardware_refuses_without_pipe_fallback():
    with pytest.raises(EvaporationFluxConfigurationError,match='melt_surface_area'):
        source(**PINS[-1]['inputs'])
    for hardware in ({},replace(flow(3),length_m=None),replace(flow(3),provenance='')):
        with pytest.raises(EvaporationFluxConfigurationError,match='missing_input'):
            sample(surface_flow=hardware)


def test_zero_flow_is_declared_still_disk_and_no_pipe_or_stir_causality():
    still=sample()
    assert sample(surface_flow=flow(0)).flux_kg_s_m2==still.flux_kg_s_m2
    assert sample(pipe_diameter_m=0.001,radial_stir_factor=10).flux_kg_s_m2==still.flux_kg_s_m2
    assert sample(P_bulk_pa=1e6).flux_kg_s_m2==0
    hot=sample(P_eq_pa=1000,P_bulk_pa=0,overhead_pressure_pa=1000)
    assert any('high vapour fraction' in f for f in hot.surface_film['domain_flags'])
    assert hot.surface_film['stefan_model']=='dilute_passive_scalar'


def test_fixed_partial_source_is_insensitive_to_net_sweep():
    diagnostics=[_w3_result_with_controls(
        {'axial':6.,'radial':1.},overhead_pressure_pa=1000.,
        overhead_partials_Pa={'Na':1.},pipe_diameter_m=.16,sweep_mol_per_s=u,
    ).diagnostic['evaporation_series_resistance'] for u in (0.,1.,10.)]
    assert diagnostics[0]==diagnostics[1]==diagnostics[2]


@pytest.mark.parametrize(('feed','u','expected'),[
    ('lunar_mare_low_ti',None,{'SiO':.08666,'Na':.07735}),
    ('mars_basalt',None,{'SiO':.1625,'Na':.1543}),
    ('lunar_mare_low_ti',1,{'SiO':.1076,'Na':.1032}),
    ('lunar_mare_low_ti',3,{'SiO':.1846,'Na':.1787}),
    ('mars_basalt',1,{'SiO':.2079,'Na':.2107}),
    ('mars_basalt',3,{'SiO':.3568,'Na':.3648}),
])
def test_frozen_study_dilute_multipliers(feed,u,expected):
    sums={s:[0.,0.] for s in expected}
    for p in PINS:
        if p['feedstock']!=feed:continue
        r=source(**p['inputs'],melt_surface_area_m2=AREA,surface_flow=None if u is None else flow(u))
        sums[p['inputs']['species']][0]+=p['archived_flux']
        sums[p['inputs']['species']][1]+=(r.surface_film or {}).get('dilute_flux_kg_s_m2',r.flux_kg_s_m2)
    for species,want in expected.items():
        assert sums[species][1]/sums[species][0]==pytest.approx(want,rel=8e-4)


def test_saturation_proves_zero_without_transport_or_hardware():
    result=sample(species='unknown_transport',P_eq_pa=1,P_bulk_pa=2,
                  melt_surface_area_m2=None,surface_flow={})
    assert result.flux_kg_s_m2==0
    assert result.source_film_length_m==PINS[-1]['inputs']['pipe_diameter_m']


def test_diagnostic_wrapper_uses_actual_stefan_partial_pressures():
    from simulator.evaporation_classes import interface_resistance_share
    kwargs=dict(species='Na', alpha=1., T_K=1500.,molar_mass_kg_mol=.02298976928,
                overhead_pressure_pa=1000.,p_eq_pa=.7,p_bulk_pa=.6,melt_surface_area_m2=AREA)
    result=interface_resistance_share(**kwargs)
    expected=source(species='Na',alpha_i=1.,T_surface_K=1500.,molar_mass_kg_mol=.02298976928,
                    overhead_pressure_pa=1000.,P_eq_pa=.7,P_bulk_pa=.6,melt_surface_area_m2=AREA)
    assert result.r_gas==expected.r_gas
    assert result.flux_kg_s_m2==expected.flux_kg_s_m2
    assert sample(surface_flow=flow(3,'round')).surface_film['geometry_parameters']['H_over_d']==pytest.approx(6)


def test_provider_translates_typed_hardware_without_net_sweep_inference():
    results=[_w3_result_with_controls(
        {'axial':6.,'radial':1.},overhead_pressure_pa=1000.,
        pipe_diameter_m=.16, surface_flow=flow(u),
    ) for u in (1,3)]
    assert all(result.status=='ok' for result in results)
    assert results[1].diagnostic['evaporation_flux_kg_hr']['Na']>results[0].diagnostic['evaporation_flux_kg_hr']['Na']
    absent=_w3_result_with_controls({'axial':6.,'radial':1.},melt_surface_area_m2=None)
    assert absent.status=='refused'


def test_operator_metadata_exports_snapshot_resistance_and_groups():
    from types import SimpleNamespace
    from simulator.runner import build_per_hour_summary
    from simulator.state import HourSnapshot, CampaignPhase
    snapshot=HourSnapshot(hour=1,campaign=CampaignPhase.C2A)
    diagnostic=sample().as_diagnostic()
    snapshot.evap_plane_selectivity={'surface_transport_by_species':{'Na':diagnostic}}
    sim=SimpleNamespace(product_ledger=lambda:{}, campaign_mgr=SimpleNamespace(last_pO2_enforcement=None),
                        record=SimpleNamespace(snapshots=(snapshot,)),
                        _last_vapour_batch_report={'metadata':{'existing':'retained'}})
    result=build_per_hour_summary(sim,snapshot,include_fe_redox_split=False)
    metadata=result['vapour_batch_summary']['metadata']
    assert metadata['existing']=='retained'
    exported=metadata['surface_transport_by_species']['Na']
    assert exported['R_gas_fraction']>.9
    assert exported['surface_film']['Sh']==diagnostic['surface_film']['Sh']
    assert 'surface_transport_by_species' not in result


@pytest.mark.parametrize('gas_enabled',[False,True])
def test_subnormal_vapour_drive_does_not_divide_by_underflow(gas_enabled):
    result=sample(P_eq_pa=1e-320,P_bulk_pa=0,gas_resistance_enabled=gas_enabled)
    assert result.flux_kg_s_m2==0
    assert math.isfinite(result.alpha_effective)
    assert 0<=result.alpha_effective<=result.alpha_intrinsic
