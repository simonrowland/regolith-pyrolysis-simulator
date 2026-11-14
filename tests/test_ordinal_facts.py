from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from simulator.chemistry.melt_activity import single_cation_mole_fractions
from simulator.config import load_config_bundle
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.oxygen_buffers import qfm_log10_fO2_bar
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
COMPOSITION_EXTRACT = (
    DATA_DIR / "literature" / "extracts" / "smales-1971-lpsc-12022.yaml"
)


@pytest.mark.parametrize(
    ("source_id", "page", "quote", "sample", "temperature_K", "delta_fmq"),
    (
        pytest.param(
            "kems-041-sossi-fegley-2018",
            412,
            "alkali volatility is 10^4 times greater than the next most volatile components",
            "Apollo_12_lunar_basalt_12022",
            1400.0,
            0.5,
            id="F09-12022-1400K-FMQ-plus-0.5",
        ),
    ),
)
def test_f09_alkali_volatility_uses_the_printed_sample(
    source_id: str,
    page: int,
    quote: str,
    sample: str,
    temperature_K: float,
    delta_fmq: float,
) -> None:
    assert source_id == "kems-041-sossi-fegley-2018"
    assert page == 412
    assert len(quote.split()) <= 15
    assert sample == "Apollo_12_lunar_basalt_12022"
    assert 1396.0 <= temperature_K <= 1499.0
    assert delta_fmq == 0.5

    extract = yaml.safe_load(COMPOSITION_EXTRACT.read_text(encoding="utf-8"))
    assert extract["source_id"] == "smales-1971-lpsc-12022"
    observation = next(
        observation
        for observation in extract["species"]["SiO2"]["observations"]
        if observation["observation_id"]
        == "smales_1971_apollo_12022_bulk_composition_tables1_2_3"
    )
    assert observation["phase"] == sample
    assert observation["locator"]["tables"] == ["1", "2", "3"]
    assert observation["locator"]["pages"] == "1254–1255"
    printed_oxides = observation["values"]["major_oxide_measurements_wt_pct"]
    composition_wt_pct = {
        oxide: float(measurements[0]["value"])
        for oxide, measurements in printed_oxides.items()
    }

    bundle = load_config_bundle(DATA_DIR)
    feedstock_id = "apollo_12_12022_from_smales_1971"
    bundle.feedstocks[feedstock_id] = {
        "label": "Apollo 12 lunar basalt 12022 (Smales et al. 1971)",
        "source": "Smales et al. 1971, Tables 1–3, printed pp. 1254–1255",
        "composition_wt_pct": composition_wt_pct,
    }
    simulator = PyrolysisSimulator(
        InternalAnalyticalBackend(),
        bundle.setpoints,
        bundle.feedstocks,
        bundle.vapor_pressures,
        materials=bundle.materials,
    )
    simulator.load_batch(feedstock_id)

    simulator.melt.temperature_C = temperature_K - CELSIUS_TO_KELVIN_OFFSET
    intrinsic_fO2_log = qfm_log10_fO2_bar(temperature_K) + delta_fmq
    headspace_pO2_bar = 10.0**intrinsic_fO2_log
    simulator.melt.oxygen_reservoir.melt_intrinsic_fO2_log = intrinsic_fO2_log
    simulator.melt.oxygen_reservoir.headspace_transport_pO2_bar = headspace_pO2_bar
    simulator._sync_oxygen_reservoir_mirror()
    assert simulator.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
        intrinsic_fO2_log
    )
    assert simulator.melt.oxygen_reservoir.headspace_transport_pO2_bar == pytest.approx(
        headspace_pO2_bar
    )

    equilibrium = simulator._internal_analytical_equilibrium()
    assert equilibrium.status == "ok"
    pressures = equilibrium.vapor_pressures_Pa
    assert {"Na", "K", "Fe", "Mn"} <= pressures.keys()
    initial_condensed = simulator.atom_ledger.project_account_mol(
        "process.cleaned_melt"
    )
    initial_fractions = single_cation_mole_fractions(initial_condensed)
    normalized = {
        "Na": pressures["Na"] / initial_fractions["Na2O"],
        "K": pressures["K"] / initial_fractions["K2O"],
        "Fe": pressures["Fe"] / initial_fractions["FeO"],
        "Mn": pressures["Mn"] / initial_fractions["MnO"],
    }
    assert min(normalized["Na"], normalized["K"]) > max(
        normalized["Fe"], normalized["Mn"]
    ), normalized
