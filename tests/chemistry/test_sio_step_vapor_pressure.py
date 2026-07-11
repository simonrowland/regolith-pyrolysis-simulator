"""SiO step isolation: VAPOR_PRESSURE.

Anchors:

* §25-bis convergence: SoF2018/MinerU Fig. 3 SiO at 1873 K,
  expected 2.820e-1 Pa, observed 3.824e-1 Pa, pass inside 1 dex.
* ``tests.chemistry.corpus_fixtures``: lunar mare basalt 12022 proxy
  SiO at 1900 K, expected 1.5490e-1 Pa.
* Post-refit guard: after the 2026-05-20 Antoine P_sat refit, the builtin
  SiO fallback was fitted to VapoRock, so the two now AGREE at 1873 K
  (within the fit residual band, max 0.113 dex on the grid). The historical
  ~1.4 dex divergence was the defect the refit corrected.
"""

from __future__ import annotations

import math
import warnings
from pathlib import Path

import pytest
import yaml

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.melt_backend.vaporock import VapoRockBackend
from tests.chemistry.corpus_fixtures import GRID_25_FEEDSTOCKS


REPO_ROOT = Path(__file__).resolve().parents[2]
ONE_DECADE = 10.0


def _load_vapor_pressure_data() -> dict:
    return yaml.safe_load((REPO_ROOT / "data" / "vapor_pressures.yaml").read_text())


def _build_lunar_12022_sim(vapor_pressure_data: dict) -> PyrolysisSimulator:
    feedstocks = {
        "lunar_mare_12022": GRID_25_FEEDSTOCKS[
            "lunar_mare_basalt_12022_proxy"
        ],
    }
    sim = PyrolysisSimulator(
        InternalAnalyticalBackend(), {"campaigns": {}}, feedstocks, vapor_pressure_data
    )
    sim.load_batch("lunar_mare_12022", mass_kg=1000.0)
    sim.melt.p_total_mbar = 1.0e-3
    sim.melt.pO2_mbar = 1.0e-6
    return sim


def _vaporock_backend_or_skip() -> VapoRockBackend:
    backend = VapoRockBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        available = backend.initialize({})
    if not available:
        pytest.skip("VapoRock optional dependency unavailable")
    return backend


def _vaporock_sio_pressure_pa(
    backend: VapoRockBackend,
    sim: PyrolysisSimulator,
    T_K: float,
) -> tuple[float, float]:
    sim.melt.temperature_C = T_K - 273.15
    fO2_log = sim._compute_intrinsic_melt_fO2(T_K)
    result = backend.equilibrate(
        sim.melt.temperature_C,
        composition_mol=sim._backend_composition_mol(),
        fO2_log=fO2_log,
        pressure_bar=sim.melt.p_total_mbar / 1000.0,
    )
    if not result.vapor_pressures_Pa:
        pytest.skip("VapoRock returned no vapor pressures")
    return float(result.vapor_pressures_Pa["SiO"]), fO2_log


@pytest.mark.parametrize(
    ("T_K", "expected_pa"),
    [
        (1873.0, 2.820e-1),
        (1900.0, 1.5490e-1),
    ],
)
def test_vaporock_sio_pressure_stays_inside_literature_envelope(
    T_K: float, expected_pa: float
):
    vapor_pressure_data = _load_vapor_pressure_data()
    sim = _build_lunar_12022_sim(vapor_pressure_data)
    backend = _vaporock_backend_or_skip()

    observed_pa, _ = _vaporock_sio_pressure_pa(backend, sim, T_K)

    assert expected_pa / ONE_DECADE <= observed_pa <= expected_pa * ONE_DECADE


def test_vaporock_vs_antoine_agree_after_psat_refit():
    vapor_pressure_data = _load_vapor_pressure_data()
    sim = _build_lunar_12022_sim(vapor_pressure_data)
    backend = _vaporock_backend_or_skip()

    vaporock_pa, fO2_log = _vaporock_sio_pressure_pa(backend, sim, 1873.0)
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": sim._backend_composition_mol()},
        species_formula_registry={},
    )
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=view,
        temperature_C=sim.melt.temperature_C,
        pressure_bar=1.0e-6,
        fO2_log=fO2_log,
        control_inputs={"pO2_bar": 10.0**fO2_log},
    )
    antoine_pa = BuiltinVaporPressureProvider(vapor_pressure_data).dispatch(
        request
    ).diagnostic["vapor_pressures_Pa"]["SiO"]

    # Post-refit the builtin SiO fallback agrees with VapoRock at 1873 K;
    # abs band covers the max 0.113 dex grid fit residual (observed ~-0.01 dex).
    assert math.log10(antoine_pa / vaporock_pa) == pytest.approx(0.0, abs=0.15)


def test_intrinsic_kress91_iw_regime_guards_against_vacuum_floor_conflation():
    vapor_pressure_data = _load_vapor_pressure_data()
    sim = _build_lunar_12022_sim(vapor_pressure_data)
    sim.melt.temperature_C = 1873.15 - 273.15
    sim.melt.pO2_mbar = 1.0e-6

    intrinsic_fO2_log = sim._compute_intrinsic_melt_fO2(1873.15)
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = intrinsic_fO2_log
    sim.melt.fO2_log = intrinsic_fO2_log
    sim.melt.melt_fO2_log = intrinsic_fO2_log
    equilibrium = sim._internal_analytical_equilibrium()

    assert -8.10 <= intrinsic_fO2_log <= -7.85
    assert equilibrium.fO2_log == pytest.approx(intrinsic_fO2_log, abs=0.05)
    assert abs(equilibrium.fO2_log - (-9.0)) > 0.75
