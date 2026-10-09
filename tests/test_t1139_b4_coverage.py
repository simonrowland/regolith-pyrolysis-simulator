"""No silent carrier or feedstock-element omissions in the B4 audit."""
import pytest
import yaml

from simulator.config import load_config_bundle
from simulator.reference_data.janaf import feedstock_element_symbols
from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
from simulator.vapour_rail.channel_generator import (
    _all_feedstock_coverage, _assert_manifest_coverage, _manifest_coverage,
    load_demand_manifest,
)


@pytest.fixture(scope="module")
def coverage():
    bundle = load_config_bundle()
    payload = bundle.vapor_pressures.catalog_payload
    catalog = compile_vapour_rail_catalog(payload)
    return bundle, _manifest_coverage(catalog, payload), _all_feedstock_coverage(
        catalog, payload, bundle.feedstocks, temperature_K=1523.15,
    )


def test_every_manifest_pair_is_evaluated_or_a_typed_gap(coverage):
    _bundle, pairs, _feedstocks = coverage
    manifest = load_demand_manifest()
    assert len(pairs) == len(manifest["pairs"]) == 1874
    _assert_manifest_coverage(pairs, manifest)
    for carrier in ("Cu", "Cu2", "CuO"):
        row = next(p for p in pairs if p["element"] == "Cu" and p["carrier"] == carrier)
        assert row["path"] == "typed_gap"
        assert row["reasons"][0]["table"] == "Cu-020"
        assert row["reasons"][0]["temperature_K"] == 1600.0


def test_gate_rejects_a_silent_omission(coverage):
    with pytest.raises(ValueError, match="silent demand-manifest omission"):
        _assert_manifest_coverage(coverage[1][1:], load_demand_manifest())


def test_every_feedstock_declaration_has_an_element_path(coverage, tmp_path):
    bundle, _pairs, report = coverage
    assert report.keys() == bundle.feedstocks.keys()
    for feedstock_id, feedstock in bundle.feedstocks.items():
        declared = tmp_path / (feedstock_id + ".yaml")
        declared.write_text(yaml.safe_dump({feedstock_id: feedstock}))
        expected = set(feedstock_element_symbols(declared))
        actual = report[feedstock_id]["elements"]
        assert expected <= actual.keys(), (feedstock_id, expected - actual.keys())
        for element, entry in actual.items():
            assert entry["components"] and entry["paths"], (feedstock_id, element)
            assert all(path["path"] in {"evaluated_channel", "typed_gap", "stays_in_melt"}
                       for path in entry["paths"])


def test_metal_hosts_and_copper_are_visible_gaps(coverage):
    _bundle, _pairs, report = coverage
    nickel = report["m_type_metallic_phase"]["elements"]["Ni"]
    assert nickel["paths"][0]["kind"] == "siderophile_in_metal_scope_gap"
    copper = report["lunar_mare_low_ti"]["elements"]["Cu"]
    assert any(p.get("reasons", [{}])[0].get("table") == "Cu-020" for p in copper["paths"])
    oxygen = report["lunar_mare_low_ti"]["elements"]["O"]
    assert oxygen["paths"][0]["kind"] == "no_element_owner_demand"
