from pathlib import Path

import pytest
import yaml

from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend


DATA_PATH = Path(__file__).parent.parent / "data" / "feedstocks.yaml"


@pytest.fixture(scope="module")
def feedstocks():
    return yaml.safe_load(DATA_PATH.read_text())


def _sim(feedstocks):
    backend = StubBackend()
    backend.initialize({})
    return PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        feedstocks,
        {"metals": {}, "oxide_vapors": {}},
    )


@pytest.mark.parametrize(
    ("key", "organics", "organics_range", "carbonate", "carbonate_range"),
    [
        ("ci_carbonaceous_chondrite", 4.0, [3.0, 5.5], 2.5, [1.5, 4.0]),
        ("cm_carbonaceous_chondrite", 2.0, [1.5, 3.5], 1.2, [0.8, 2.5]),
    ],
)
def test_carbonaceous_chondrite_carbon_speciation_is_literature_grounded(
    feedstocks, key, organics, organics_range, carbonate, carbonate_range
):
    entry = feedstocks[key]
    composition = entry["composition_wt_pct"]
    ranges = entry["composition_ranges"]
    provenance = entry["carbon_speciation_provenance"]

    assert composition["carbonaceous_organic"] == pytest.approx(organics)
    assert composition["carbonate_salts"] == pytest.approx(carbonate)
    assert ranges["carbonaceous_organic"] == organics_range
    assert ranges["carbonate_salts"] == carbonate_range
    assert provenance["carbonaceous_organic"]["value_wt_pct"] == pytest.approx(
        organics
    )
    assert provenance["carbonaceous_organic"]["interval_wt_pct"] == organics_range
    assert provenance["carbonate_salts"]["value_wt_pct"] == pytest.approx(carbonate)
    assert provenance["carbonate_salts"]["interval_wt_pct"] == carbonate_range
    assert "Pearson" in provenance["carbonaceous_organic"]["source"]
    assert "Bland" in provenance["carbonate_salts"]["source"]


def test_ceres_carbon_catchall_is_split_without_moving_nh4_into_carbon(
    feedstocks,
):
    entry = feedstocks["ceres_regolith"]
    composition = entry["composition_wt_pct"]
    ranges = entry["composition_ranges"]
    provenance = entry["carbon_speciation_provenance"]
    carriers = entry["stage0_carrier_keys"]

    assert composition["carbonaceous_organic"] == pytest.approx(1.0)
    assert composition["carbonate_salts"] == pytest.approx(3.0)
    assert composition["NH3"] == pytest.approx(1.2)
    assert ranges["carbonaceous_organic_wt_pct"] == [0.5, 1.5]
    assert ranges["carbonate_salts"] == [2.0, 4.5]
    assert ranges["NH3"] == [0.8, 2.0]
    assert ranges["brine_salts_wt_pct"] == [0.5, 2.0]
    assert ranges["nh4_phyllosilicate_wt_pct"] == [5, 12]
    assert provenance["brine_salts"]["allocated_wt_pct"] == pytest.approx(1.0)
    assert provenance["nh4_phyllosilicate"]["phase_wt_pct"] == pytest.approx(8.0)
    assert "not a carbon carrier" in provenance["nh4_phyllosilicate"]["basis"]
    assert carriers["brine_salts"]["allocated_wt_pct"] == pytest.approx(1.0)
    assert carriers["nh4_phyllosilicate"]["allocated_wt_pct"] == pytest.approx(8.0)
    assert carriers["nh4_phyllosilicate"]["reaction_family"] == "declaration_only"


def test_comet_nucleus_uses_rosetta_refractory_organic_budget(feedstocks):
    entry = feedstocks["comet_nucleus"]
    composition = entry["composition_wt_pct"]
    ranges = entry["composition_ranges"]
    provenance = entry["carbon_speciation_provenance"]["organics"]

    assert composition["organics"] == pytest.approx(33.75)
    assert ranges["refractory_dust_organic_wt_pct"] == [40, 50]
    assert ranges["nucleus_bulk_refractory_organic_wt_pct"] == [32, 40]
    assert ranges["refractory_to_water_mass_ratio"] == [4, 8]
    assert provenance["refractory_dust_interval_wt_pct"] == [40, 50]
    assert provenance["nucleus_bulk_interval_wt_pct"] == [32, 40]
    assert "Bardyn" in provenance["source"]
    assert "Fulle" in provenance["source"]
    assert sum(composition.values()) == pytest.approx(entry["sum_check"])


@pytest.mark.parametrize(
    ("key", "value", "interval", "source_marker"),
    [
        ("mars_basalt", 3.0, [2, 5], "Bandfield"),
        ("mars_sulfate_rich", 2.0, [0, 3], "Niles"),
        ("mars_phyllosilicate_clay", 8.0, [3, 15], "Ehlmann"),
        ("mars_perchlorate_rich", 1.5, [0, 3], "Bandfield"),
    ],
)
def test_mars_feedstocks_declare_native_carbonate_carrier(
    feedstocks, key, value, interval, source_marker
):
    entry = feedstocks[key]
    composition = entry["composition_wt_pct"]
    ranges = entry["composition_ranges"]
    provenance = entry["native_carbonate_provenance"]["carbonate_salts"]
    carrier = entry["stage0_carrier_keys"]["carbonate"]

    assert composition["carbonate_salts"] == pytest.approx(value)
    assert ranges["carbonate_salts"] == interval
    assert provenance["value_wt_pct"] == pytest.approx(value)
    assert provenance["interval_wt_pct"] == [float(interval[0]), float(interval[1])]
    assert source_marker in provenance["source"]
    assert carrier["composition_key"] == "carbonate_salts"
    assert carrier["stage0_components_set"] == "STAGE0_CARBONATE_COMPONENTS"


def test_corrected_feedstock_sums_match_declared_sum_check(feedstocks):
    for key in (
        "ci_carbonaceous_chondrite",
        "cm_carbonaceous_chondrite",
        "ceres_regolith",
        "comet_nucleus",
        "mars_basalt",
        "mars_sulfate_rich",
        "mars_phyllosilicate_clay",
        "mars_perchlorate_rich",
    ):
        entry = feedstocks[key]
        assert sum(entry["composition_wt_pct"].values()) == pytest.approx(
            entry["sum_check"]
        )


@pytest.mark.parametrize(
    ("key", "additives_kg"),
    [
        ("ci_carbonaceous_chondrite", {}),
        ("cm_carbonaceous_chondrite", {}),
        ("ceres_regolith", {}),
        ("comet_nucleus", {}),
        ("mars_basalt", {"C": 30.0}),
        ("mars_sulfate_rich", {"C": 45.0}),
        ("mars_phyllosilicate_clay", {}),
        ("mars_perchlorate_rich", {}),
    ],
)
def test_corrected_feedstocks_load_and_close_stage0_balance(
    feedstocks, key, additives_kg
):
    sim = _sim(feedstocks)
    sim.load_batch(key, mass_kg=1000.0, additives_kg=additives_kg)
    sim.atom_ledger.assert_balanced()
    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0)
