"""Metal contact retains ferric iron at Kress/IW equilibrium (d163 steer F2)."""

import pytest

from simulator import fe_redox
from simulator.feedstock_composition import normalized_feedstock_component_masses_kg
from simulator.state import MOLAR_MASS
from tests.chemistry.conftest import _load_yaml


def test_lunar_contact_stops_at_equilibrium_not_ferric_exhaustion():
    feed = _load_yaml('feedstocks.yaml')['lunar_mare_low_ti']
    # Keep the historical 15020 proxy for the review's independent numeric
    # check; F1 changes the production family proxy to the cited median.
    feed['iron_speciation']['is_over_feo']['value'] = 70.
    prepared, _ = fe_redox.initialize_feedstock_iron(feed)
    mass = normalized_feedstock_component_masses_kg(prepared, 1000.)
    comp = {s: w for s, w in mass.items()
            if s in (*fe_redox.KRESS91_MOL_FRACTION_OXIDES, 'FeO', 'Fe2O3')}
    total = sum(comp.values())
    comp = {s: 100*w/total for s, w in comp.items()}
    metal = mass['Fe']*1000/MOLAR_MASS['Fe']
    ferric = mass['Fe2O3']*1000/MOLAR_MASS['Fe2O3']
    ferrous = mass['FeO']*1000/MOLAR_MASS['FeO']
    result = fe_redox.fe0_comproportionation_extent(
        metal, ferric, feo_mol=ferrous, comp_wt=comp,
        T_K=1673.15, pressure_bar=1e-9)
    n = result['extent_mol']
    # Independent review of the old 15020 proxy load: 3.498 mol, versus
    # the incorrect exhaustion extent 22.491 mol.
    assert n == pytest.approx(3.50, abs=.03)
    assert result['metal_remaining_mol'] > 0
    assert result['fe2o3_remaining_mol'] > 18.
    assert result['feo_credit_mol'] == 3*n
    assert result['equilibrium_gap_dex'] == pytest.approx(0., abs=1e-9)


@pytest.mark.parametrize('metal, ferric', [(0., 5.), (5., 0.), (.001, 5.), (20., 5.)])
def test_contact_stock_bounds_and_equilibrium(metal, ferric):
    result = fe_redox.fe0_comproportionation_extent(
        metal, ferric, feo_mol=10.,
        comp_wt={'SiO2': 80., 'FeO': 10*MOLAR_MASS['FeO'],
                 'Fe2O3': ferric*MOLAR_MASS['Fe2O3']},
        T_K=1673.15, pressure_bar=.01)
    n = result['extent_mol']
    assert 0 <= n <= min(metal, ferric)
    if metal == 0 or ferric == 0:
        assert n == 0
    elif n < metal:
        assert result['equilibrium_gap_dex'] == pytest.approx(0., abs=1e-9)
