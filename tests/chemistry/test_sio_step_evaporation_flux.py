"""SiO step isolation: EVAPORATION_FLUX.

Anchors:

* ``data/vapor_pressures.yaml`` sets SiO Hertz-Knudsen alpha to 0.04.
* §25-bis convergence documents VapoRock SiO pressure near 3.824e-1 Pa
  at the 1873 K SoF2018 Fig. 3 anchor. With alpha=0.04, unit area, and
  unit stir factor, the corrected H-K-L mass flux is computed from the
  shared gas constant used by the series-resistance helper.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from simulator.condensation import GAS_CONSTANT_J_MOL_K
from simulator.evaporation import _load_evaporation_alpha_by_species


REPO_ROOT = Path(__file__).resolve().parents[2]
SIO_ANCHOR_PRESSURE_PA = 0.3824
SIO_ANCHOR_MOLAR_MASS_KG_MOL = 0.04408
SIO_ANCHOR_T_K = 1600.0 + 273.15
SIO_ANCHOR_FLUX_KG_HR = (
    0.04
    * SIO_ANCHOR_PRESSURE_PA
    * math.sqrt(
        SIO_ANCHOR_MOLAR_MASS_KG_MOL
        / (2.0 * math.pi * GAS_CONSTANT_J_MOL_K * SIO_ANCHOR_T_K)
    )
    * 3600.0
)


def _load_vapor_pressure_data() -> dict:
    return yaml.safe_load((REPO_ROOT / "data" / "vapor_pressures.yaml").read_text())


def _sio_flux(alpha: float) -> float:
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"SiO2": 1000.0}},
        species_formula_registry={},
    )
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=view,
        temperature_C=1600.0,
        pressure_bar=1.0e-6,
        control_inputs={
            "vapor_pressures_Pa": {"SiO": SIO_ANCHOR_PRESSURE_PA},
            "overhead_partials_Pa": {"SiO": 0.0},
            "gas_pO2_bar": 1.0e-9,
            "intrinsic_pO2_bar": 1.0e-9,
            "molar_mass_kg_mol": {"SiO": SIO_ANCHOR_MOLAR_MASS_KG_MOL},
            "stoich_by_species": {
                "SiO": {
                    "parent_oxide": "SiO2",
                    "oxide_per_product_kg": 1.3629764065335754,
                    "O2_per_product_kg": 0.3629764065335753,
                }
            },
            "available_oxide_kg": {"SiO": 1000.0},
            "melt_surface_area_m2": 1.0,
            "stir_factor": 1.0,
            "alpha": {"SiO": alpha},
            "evaporation_series_resistance": {
                "gas_resistance_enabled": False,
                "melt_resistance_enabled": False,
            },
        },
    )
    result = BuiltinEvaporationFluxProvider().dispatch(request)
    return float(result.diagnostic["evaporation_flux_kg_hr"]["SiO"])


def test_sio_evaporation_alpha_surface_is_live_from_yaml():
    alpha_by_species = _load_evaporation_alpha_by_species(
        _load_vapor_pressure_data()
    )

    assert alpha_by_species["SiO"] == pytest.approx(0.04)


def test_hertz_knudsen_flux_scales_linearly_with_alpha():
    full_alpha_flux = _sio_flux(0.04)
    half_alpha_flux = _sio_flux(0.02)

    assert half_alpha_flux / full_alpha_flux == pytest.approx(0.5)


def test_sio_flux_matches_25bis_pressure_anchor_with_alpha_surface():
    assert _sio_flux(0.04) == pytest.approx(SIO_ANCHOR_FLUX_KG_HR, rel=1e-12)
