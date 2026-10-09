"""Today's iron ledger and melt-seed numbers, before the b-756 prior.

Measured on f2b3602ad by loading 1000 kg through PyrolysisSimulator.
Mars basalt is supplied the Stage-0 carbon reductant that load_batch
requires (30 kg C). The seed column is intrinsic_melt_fO2 at the stated
kelvin temperature. With no prior seated, Fe2O3 stays absent and the
seed stays Holzheid IW plus the alkali offset. The notice contract is
the lower-bound wording owned by the load resolution.

C2A sio_evolved_kg (build_sio_yield_report, 24 h, engines.local.toml
present, allow_unmeasured_alpha_fallback=True) is recorded in
SIO_EVOLVED_KG_BEFORE once that measurement finishes. This file does not
re-run the 24 h campaign: the MAGEMin liquidus shadow exceeds the focused
test budget. Those kilograms are the before-column of the b-756 report.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.core import PyrolysisSimulator
from simulator.fe_redox import feo_iw_log10_fO2_bar, intrinsic_melt_fO2
from simulator.feedstock_composition import resolve_feedstock_composition
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.runner import _required_stage0_carbon_kg


DATA = Path(__file__).resolve().parents[1] / "data"

# Load of 1000 kg on f2b3602ad. Fe2O3 is 0 because no prior is seated.
# The lunar FeO kilogram is the normalized batch, not 0.165 * 1000.
_LOAD_PIN = {
    "lunar_mare_low_ti": {
        "carbon_kg": 0.0,
        "feo_kg": 169.4681999331701,
        "fe2o3_kg": 0.0,
        "seed_1673_15": -9.735607676062752,
        "alkali_dex": 0.005146680391148,
    },
    "mars_basalt": {
        "carbon_kg": 30.0,
        "feo_kg": 171.34697762970012,
        "fe2o3_kg": 0.0,
        "seed_1673_15": -9.697843211824154,
        "alkali_dex": 0.042911144629746545,
    },
    "ci_carbonaceous_chondrite": {
        "carbon_kg": 0.0,
        "feo_kg": 231.20493340292677,
        "fe2o3_kg": 0.0,
        "seed_1673_15": -9.733743897840405,
        "alkali_dex": 0.007010458613494919,
    },
}

# Engine-live C2A, 24 h, measured while this process still imported the
# pre-mechanism notice (code melt_fO2_seed_without_ferric_iron). Not
# re-executed here. A later prior must not replace the mars kilogram.
SIO_EVOLVED_KG_BEFORE = {
    "lunar_mare_low_ti": 4.1406638526e-05,
    "mars_basalt": 3.67446217974e-05,
    "ci_carbonaceous_chondrite": 3.4400139709e-05,
}


def _feedstocks() -> dict:
    return yaml.safe_load((DATA / "feedstocks.yaml").read_text()) or {}


def _sim() -> PyrolysisSimulator:
    return PyrolysisSimulator(
        InternalAnalyticalBackend(),
        yaml.safe_load((DATA / "setpoints.yaml").read_text()) or {},
        _feedstocks(),
        yaml.safe_load((DATA / "vapor_pressures.yaml").read_text()) or {},
    )


@pytest.mark.parametrize("feedstock_id", tuple(_LOAD_PIN))
def test_load_iron_and_seed_before_the_prior(feedstock_id: str) -> None:
    pin = _LOAD_PIN[feedstock_id]
    feedstocks = _feedstocks()
    carbon_kg = _required_stage0_carbon_kg(feedstocks[feedstock_id], 1000.0)
    assert carbon_kg == pytest.approx(pin["carbon_kg"])
    additives = {"C": carbon_kg} if carbon_kg > 0.0 else {}
    sim = _sim()
    sim.load_batch(feedstock_id, mass_kg=1000.0, additives_kg=additives)
    melt = sim.inventory.melt_oxide_kg
    composition = sim._melt_oxide_wt_pct()
    seeded = intrinsic_melt_fO2(composition, 1673.15)

    assert melt.get("FeO", 0.0) == pytest.approx(pin["feo_kg"])
    assert melt.get("Fe2O3", 0.0) == pytest.approx(pin["fe2o3_kg"])
    assert seeded == pytest.approx(pin["seed_1673_15"])
    assert seeded - feo_iw_log10_fO2_bar(1673.15) == pytest.approx(
        pin["alkali_dex"]
    )
    assert sim.melt.fO2_log == pytest.approx(
        intrinsic_melt_fO2(composition, 25.0 + 273.15)
    )


def test_lunar_resolver_does_not_call_absent_fe2o3_a_measured_zero() -> None:
    # The pre-prior pin recorded measured_fe2o3 == 0 and split_known True.
    # That reading treated a total-Fe analysis as a measurement of no Fe3+.
    # Absent Fe2O3 is now an unresolved split until a prior is seated.
    resolved = resolve_feedstock_composition(
        _feedstocks()["lunar_mare_low_ti"]
    )
    assert resolved.fe_redox_split_unknown is False
    assert resolved.split_known is False
    assert resolved.measured_feo is None
    assert resolved.measured_fe2o3 is None
    assert resolved.total_fe == pytest.approx(16.5)
    assert resolved.fe_redox_prior is None


def test_seed_notice_says_the_unresolved_split_is_a_lower_bound() -> None:
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    notice = sim.melt_fO2_seed_run_notice()
    assert notice is not None
    assert notice["code"] == "fe_redox_split"
    assert notice["authority"] == "lower_bound"
    assert notice["source_id"] is None
    assert notice["value"] is None
    assert "lower bound" in notice["message"].lower()
