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

from simulator.accounting.formulas import resolve_species_formula
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


def _iron_atoms(melt: dict) -> float:
    molar_feo = resolve_species_formula("FeO").molar_mass_g_per_mol()
    molar_fe2o3 = resolve_species_formula("Fe2O3").molar_mass_g_per_mol()
    return (
        float(melt.get("FeO", 0.0)) / molar_feo
        + 2.0 * float(melt.get("Fe2O3", 0.0)) / molar_fe2o3
    )


def test_mars_load_iron_and_seed_stay_on_the_pre_prior_pin() -> None:
    pin = _LOAD_PIN["mars_basalt"]
    feedstocks = _feedstocks()
    carbon_kg = _required_stage0_carbon_kg(feedstocks["mars_basalt"], 1000.0)
    assert carbon_kg == pytest.approx(pin["carbon_kg"])
    sim = _sim()
    sim.load_batch(
        "mars_basalt", mass_kg=1000.0, additives_kg={"C": carbon_kg}
    )
    melt = sim.inventory.melt_oxide_kg
    composition = sim._melt_oxide_wt_pct()
    seeded = intrinsic_melt_fO2(composition, 1673.15)

    assert melt.get("FeO", 0.0) == pytest.approx(pin["feo_kg"])
    assert melt.get("Fe2O3", 0.0) == pytest.approx(pin["fe2o3_kg"])
    assert seeded == pytest.approx(pin["seed_1673_15"])
    assert seeded - feo_iw_log10_fO2_bar(1673.15) == pytest.approx(
        pin["alkali_dex"]
    )
    notice = sim.melt_fO2_seed_run_notice()
    assert notice is not None
    assert notice["authority"] == "lower_bound"
    assert notice["source_id"] is None
    assert "lower bound" in notice["message"].lower()


def test_lunar_prior_keeps_iron_atoms_and_seeds_below_the_old_iw() -> None:
    pin = _LOAD_PIN["lunar_mare_low_ti"]
    molar_feo = resolve_species_formula("FeO").molar_mass_g_per_mol()
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    melt = sim.inventory.melt_oxide_kg
    prior = sim._load_fe_redox.prior
    composition = sim._melt_oxide_wt_pct()

    assert _iron_atoms(melt) == pytest.approx(pin["feo_kg"] / molar_feo)
    assert float(melt.get("Fe2O3", 0.0)) > 0.0
    assert sim.inventory.stage0_external_inputs_kg[
        "feot_omitted_ferric_oxygen"
    ] > 0.0
    seeded = intrinsic_melt_fO2(
        composition, 1673.15, fe_redox_prior=prior
    )
    assert seeded == pytest.approx(feo_iw_log10_fO2_bar(1673.15) + prior.value)
    assert seeded < pin["seed_1673_15"]
    assert sim.melt.fO2_log == pytest.approx(
        feo_iw_log10_fO2_bar(25.0 + 273.15) + prior.value
    )
    notice = sim.melt_fO2_seed_run_notice()
    assert notice["authority"] == "prior"
    assert notice["source_id"] == prior.source_id
    assert notice["locator"] == prior.locator


def test_ci_prior_keeps_iron_atoms_and_inverts_the_measured_fraction() -> None:
    pin = _LOAD_PIN["ci_carbonaceous_chondrite"]
    molar_feo = resolve_species_formula("FeO").molar_mass_g_per_mol()
    sim = _sim()
    sim.load_batch("ci_carbonaceous_chondrite", mass_kg=1000.0)
    melt = sim.inventory.melt_oxide_kg
    prior = sim._load_fe_redox.prior

    assert _iron_atoms(melt) == pytest.approx(pin["feo_kg"] / molar_feo)
    assert float(melt.get("Fe2O3", 0.0)) > 0.0
    assert prior.kind == "measured_fe3_fraction"
    notice = sim.melt_fO2_seed_run_notice()
    assert notice["authority"] == "measured"
    assert notice["fe3_fraction"] == pytest.approx(prior.value)
    assert sim.melt.fO2_log == pytest.approx(
        intrinsic_melt_fO2(
            sim._melt_oxide_wt_pct(),
            25.0 + 273.15,
            fe_redox_prior=prior,
        )
    )


def test_lunar_resolver_does_not_invent_a_measured_ferric_zero() -> None:
    resolved = resolve_feedstock_composition(
        _feedstocks()["lunar_mare_low_ti"]
    )
    assert resolved.fe_redox_split_unknown is False
    assert resolved.canonical_wt_pct.get("Fe2O3", 0.0) == 0.0
    assert resolved.canonical_wt_pct["FeO"] == pytest.approx(16.5)
    assert resolved.fe_redox_prior is not None
    assert resolved.fe_redox_prior.kind == "delta_iw"
    assert resolved.total_fe == pytest.approx(16.5)
