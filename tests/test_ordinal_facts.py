from __future__ import annotations

from pathlib import Path

import pytest

from simulator.config import load_config_bundle
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_F09 = (
    "kems-041-sossi-fegley-2018",
    412,
    "alkali volatility is 104 times greater than the next most volatile components",
    "lunar_mare_low_ti",
)


@pytest.mark.parametrize(
    (
        "source_id",
        "page",
        "quote",
        "feedstock_id",
        "condition_note",
        "temperature_K",
        "total_pressure_mbar",
        "pO2_mbar",
    ),
    (
        pytest.param(
            *_F09,
            "declared condition, within printed range",
            1250.0,
            0.0,
            0.0,
            id="F09-1250K",
        ),
        pytest.param(
            *_F09,
            "declared condition, within printed range",
            1500.0,
            0.0,
            0.0,
            id="F09-1500K",
        ),
        pytest.param(
            *_F09,
            "declared condition, within printed range",
            1750.0,
            0.0,
            0.0,
            marks=pytest.mark.xfail(
                strict=True,
                reason=(
                    "FINDING F09: engine gives pK=0.256 Pa vs pFe=0.659 Pa; "
                    "printed ordering is alkali > Fe/Mn"
                ),
            ),
            id="F09-1750K",
        ),
    ),
)
def test_f09_alkali_vapour_pressures_exceed_fe_and_mn(
    source_id: str,
    page: int,
    quote: str,
    feedstock_id: str,
    condition_note: str,
    temperature_K: float,
    total_pressure_mbar: float,
    pO2_mbar: float,
) -> None:
    assert source_id == "kems-041-sossi-fegley-2018"
    assert page == 412
    assert len(quote.split()) <= 15
    assert condition_note == "declared condition, within printed range"

    bundle = load_config_bundle(DATA_DIR)
    simulator = PyrolysisSimulator(
        InternalAnalyticalBackend(),
        bundle.setpoints,
        bundle.feedstocks,
        bundle.vapor_pressures,
        materials=bundle.materials,
    )
    simulator.load_batch(feedstock_id)
    simulator.melt.temperature_C = temperature_K - 273.15
    simulator.melt.p_total_mbar = total_pressure_mbar
    simulator.melt.pO2_mbar = pO2_mbar

    equilibrium = simulator._internal_analytical_equilibrium()
    assert equilibrium.status == "ok"
    pressures = equilibrium.vapor_pressures_Pa
    assert {"Na", "K", "Fe", "Mn"} <= pressures.keys()
    assert min(pressures["Na"], pressures["K"]) > max(
        pressures["Fe"], pressures["Mn"]
    )
